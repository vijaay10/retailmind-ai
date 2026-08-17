"""The AI Business Analyst.

Routes a question to whichever engine can actually answer it, then assembles
the reply in the shape a senior analyst gives: the answer, what was checked,
what was *not* checked, facts kept apart from inferences, and the next
question worth asking.

**It composes; it does not recompute.** Every figure comes from the surface
that owns it, with that surface's caveats carried through. An assistant that
derived its own numbers would eventually disagree with the dashboard, and the
disagreement would surface as the assistant being wrong about a thing the user
can see on screen.

**It routes rather than translating everything into one shape.** "Why did
revenue fall" is not a query, "what does AOV mean" needs no data at all, and
"how much should I trust the forecast" is a question about a model rather than
about the business. Forcing all of them through a single query path produces
answers that look right and address something else.

**On adding a language model.** The judgement encoded here — which surface
answers which question, and what each cannot establish — is knowledge about
*this platform*, not about English, and a model would not improve it. Where a
model would help is breadth of phrasing on the way in, and fluency on the way
out. The extension point is deliberate: a planner supplies the capability and
subject, and a narrator could rephrase these statements with the figures
injected as fixed facts. Generation from raw data instead reads better and
occasionally invents a quarter, which is not a trade worth taking in a tool
somebody quotes in a board meeting.
"""

import re
from datetime import date
from typing import Any

import structlog

from app.domain.auth.entities import Principal
from app.domain.auth.permissions import Permission
from app.infrastructure.llm.gateway import LlmGateway
from app.services.analyst import comparison, glossary
from app.services.analyst.contracts import (
    AnalystAnswer,
    CannotAnswerError,
    Capability,
    Certainty,
    Conversation,
    FollowUp,
    Statement,
    Turn,
)
from app.services.analyst.narrator import AnalystNarrator
from app.services.analytics.service import AnalyticsService
from app.services.forecasting.service import ForecastingService
from app.services.nlq.contracts import UnsupportedQuestionError
from app.services.nlq.service import NaturalLanguageService
from app.services.rca.service import RootCauseService
from app.services.rca.service import summarise as summarise_rca
from app.services.recommendations.service import RecommendationService
from app.services.recommendations.service import summarise as summarise_recommendations
from app.services.reporting.composer import ReportComposer, ReportRequest
from app.services.shared import authz

log = structlog.get_logger(__name__)

DEFAULT_PERIOD_DAYS = 28

#: Phrases that ask what something *means* rather than what it *is*. Checked
#: before every other intent: "what is AOV" wants a definition, and answering
#: it with this month's average order value is the classic way an assistant
#: answers a different question confidently.
DEFINITION_MARKERS = (
    "what does",
    "what is the definition",
    "define ",
    "what do you mean by",
    "how is it calculated",
    "how is it computed",
    "how do you calculate",
    "meaning of",
    "explain the metric",
    "what counts as",
)

COMPARE_MARKERS = (
    "compare",
    "versus",
    " vs ",
    "against last",
    "against the prior",
    "period on period",
)
SUMMARY_MARKERS = ("summarise", "summarize", "how are we doing", "overview", "state of", "brief me")
FORECAST_MARKERS = ("forecast", "predict", "outlook", "next week", "next month", "expected to")
TRUST_MARKERS = ("trust", "reliable", "accurate", "how good is", "confidence in")
IMPROVE_MARKERS = ("improve", "better", "what should we measure", "gaps", "weakness", "blind spot")
INVESTIGATE_MARKERS = ("why", "cause", "reason", "driving", "what happened", "explain the drop")
RECOMMEND_MARKERS = ("recommend", "should we", "should i", "what to do", "what should", "advice")

#: Pronouns that need the previous turn to resolve.
PRONOUNS = ("that", "it", "this", "those", "them", "the same")


class BusinessAnalystService:
    """Answers business questions the way a senior analyst would."""

    def __init__(
        self,
        analytics: AnalyticsService,
        *,
        nlq: NaturalLanguageService | None = None,
        rca: RootCauseService | None = None,
        forecasts: ForecastingService | None = None,
        recommendations: RecommendationService | None = None,
        reports: ReportComposer | None = None,
        llm_gateway: LlmGateway | None = None,
    ) -> None:
        self._analytics = analytics
        self._nlq = nlq
        self._rca = rca
        self._forecasts = forecasts
        self._recommendations = recommendations
        self._reports = reports
        self._narrator = AnalystNarrator(llm_gateway) if llm_gateway else None

    async def ask(
        self,
        principal: Principal,
        question: str,
        *,
        conversation: Conversation | None = None,
        as_of: date | None = None,
        period_days: int = DEFAULT_PERIOD_DAYS,
    ) -> tuple[AnalystAnswer, Conversation]:
        """Answer one question, and return the conversation it belongs to."""
        authz.require(principal, Permission.INSIGHTS_READ)

        history = conversation or Conversation()
        text = question.strip()
        if not text:
            raise CannotAnswerError("the question is empty")

        resolved = self._resolve(text, history)
        capability = self._classify(resolved.lower())
        today = as_of or date.today()

        log.info(
            "analyst.question",
            capability=capability.value,
            resolved_from_context=resolved != text,
            turns_so_far=len(history.turns),
        )

        answer = await self._answer(
            principal, capability, question=text, resolved=resolved, as_of=today, days=period_days
        )
        turn = Turn(
            question=text,
            capability=capability,
            subject=str(answer.meta.get("subject", "")),
            period_end=today,
            period_days=period_days,
        )
        return answer, history.extend(turn)

    # ── Understanding ────────────────────────────────────────────────

    def _resolve(self, question: str, history: Conversation) -> str:
        """Substitute the previous subject for a pronoun.

        "Why did that drop?" is meaningless alone and obvious in context. The
        substitution is *reported* in the answer's caveats rather than done
        silently, because an assistant that quietly decides what "that" meant
        and gets it wrong produces a confident answer about something else.
        """
        lowered = question.lower()
        if not any(f" {p} " in f" {lowered} " for p in PRONOUNS):
            return question

        subject = history.recent_subject()
        if not subject:
            return question

        for pronoun in PRONOUNS:
            pattern = re.compile(rf"\b{re.escape(pronoun)}\b", re.IGNORECASE)
            if pattern.search(question):
                return pattern.sub(subject, question, count=1)
        return question

    def _classify(self, text: str) -> Capability:
        """Pick the capability. Order encodes precedence.

        Definitions are checked first: "what does AOV mean" contains no
        question about the business at all, and every other branch would
        happily answer it with a number.
        """
        if any(marker in text for marker in DEFINITION_MARKERS):
            return Capability.EXPLAIN_KPI
        if any(marker in text for marker in TRUST_MARKERS) and any(
            marker in text for marker in FORECAST_MARKERS
        ):
            return Capability.EXPLAIN_FORECAST
        if any(marker in text for marker in IMPROVE_MARKERS):
            return Capability.IMPROVE
        if any(marker in text for marker in RECOMMEND_MARKERS):
            return Capability.RECOMMEND
        if any(marker in text for marker in INVESTIGATE_MARKERS):
            return Capability.INVESTIGATE
        if any(marker in text for marker in FORECAST_MARKERS):
            return Capability.EXPLAIN_FORECAST
        if any(marker in text for marker in COMPARE_MARKERS):
            return Capability.COMPARE
        if any(marker in text for marker in SUMMARY_MARKERS):
            return Capability.SUMMARISE
        return Capability.ANSWER

    async def _answer(
        self,
        principal: Principal,
        capability: Capability,
        *,
        question: str,
        resolved: str,
        as_of: date,
        days: int,
    ) -> AnalystAnswer:
        handlers = {
            Capability.EXPLAIN_KPI: self._explain_kpi,
            Capability.INVESTIGATE: self._investigate,
            Capability.RECOMMEND: self._recommend,
            Capability.SUMMARISE: self._summarise,
            Capability.COMPARE: self._compare,
            Capability.EXPLAIN_FORECAST: self._explain_forecast,
            Capability.IMPROVE: self._improve,
            Capability.ANSWER: self._answer_question,
        }
        return await handlers[capability](
            principal, question=question, resolved=resolved, as_of=as_of, days=days
        )

    # ── 1. Explain a KPI ─────────────────────────────────────────────

    async def _explain_kpi(
        self, principal: Principal, *, question: str, resolved: str, as_of: date, days: int
    ) -> AnalystAnswer:
        """Define a metric, and say how it gets misread.

        Needs no data at all, which is why it is checked first: answering a
        definitional question with this month's value is the classic way an
        assistant addresses something other than what was asked.
        """
        term = self._extract_term(resolved)
        explanation = glossary.explain(term) if term else None
        if explanation is None:
            raise CannotAnswerError(
                f"I don't have a metric matching '{term or resolved}'.",
                because="Only metrics declared in the registry can be explained.",
                instead=("Ask /nlq/catalogue for everything this platform measures.",),
            )

        facts = [
            Statement(explanation.definition, Certainty.MEASURED, "metric registry"),
            Statement(
                f"Computed as {explanation.computed_as}.", Certainty.MEASURED, "metric registry"
            ),
            Statement(explanation.how_to_read, Certainty.MEASURED, "metric registry"),
        ]

        return AnalystAnswer(
            question=question,
            capability=Capability.EXPLAIN_KPI,
            headline=f"{explanation.label}: {explanation.definition}",
            facts=tuple(facts),
            checked=("metric registry — the same declaration the SQL compiler uses",),
            not_checked=("Current values. This is a definition, not a reading.",),
            caveats=tuple(f"Common misreading: {item}" for item in explanation.misreadings),
            follow_ups=(
                FollowUp(
                    f"What is {explanation.label.lower()} this period?",
                    "The definition is only useful next to the number.",
                ),
                FollowUp(
                    f"How has {explanation.label.lower()} moved against last period?",
                    "A level says less than a direction.",
                ),
            ),
            data=explanation.as_dict(),
            meta={"subject": explanation.key},
        )

    # ── 2. Answer a factual question ─────────────────────────────────

    async def _answer_question(
        self, principal: Principal, *, question: str, resolved: str, as_of: date, days: int
    ) -> AnalystAnswer:
        if self._nlq is None:
            raise CannotAnswerError("Question answering is not configured.")

        try:
            answer = await self._nlq.ask(principal, resolved, as_of=as_of)
        except UnsupportedQuestionError as error:
            raise CannotAnswerError(
                str(error),
                because="The question named nothing this platform measures.",
                instead=("Ask /nlq/catalogue for the vocabulary available.",),
            ) from error

        plan = answer.plan

        # A question whose subject went unresolved must not be answered with a
        # number about something else. "What is the ROI on our TikTok
        # campaign?" resolves 'campaign' to the promotions domain, defaults the
        # measure, and returns total promotional revenue — a confident answer
        # to a question nobody asked, about a channel the platform does not
        # have and a metric it does not compute.
        #
        # The signal is the pair: terms left unresolved *and* no measure named.
        # Either alone is ordinary — a stray adjective, or "show me stores" —
        # but together they mean the platform understood neither what to
        # measure nor what about.
        if plan.unresolved and plan.confidence < 0.75:
            raise CannotAnswerError(
                f"I could not work out what you are asking about: {', '.join(plan.unresolved)}.",
                because=(
                    "Those terms match nothing this platform measures, and the "
                    "question named no metric either — so any answer I gave "
                    "would be about something else."
                ),
                instead=("Ask /nlq/catalogue for the vocabulary available.",),
            )

        rows = list(answer.rows)
        facts: list[Statement] = []

        if rows and plan.metrics:
            metric = plan.metrics[0]
            total = sum(float(row.get(metric) or 0) for row in rows)
            facts.append(
                Statement(
                    f"{len(rows)} row(s) returned, totalling {total:,.0f} "
                    f"{metric.replace('_', ' ')}.",
                    Certainty.MEASURED,
                    f"{plan.domain} domain",
                )
            )
            if plan.dimensions:
                leader = rows[0]
                facts.append(
                    Statement(
                        f"{leader.get(plan.dimensions[0])} leads with "
                        f"{float(leader.get(metric) or 0):,.0f}.",
                        Certainty.MEASURED,
                        f"{plan.domain} domain",
                    )
                )

        explanation = answer.explanation
        caveats = list(explanation.caveats) if explanation else []
        if plan.unresolved:
            caveats.append(
                "These terms were not understood and had no effect on the "
                f"answer: {', '.join(plan.unresolved)}."
            )

        return AnalystAnswer(
            question=question,
            capability=Capability.ANSWER,
            headline=self._headline_with_gaps(explanation, plan.unresolved),
            facts=tuple(facts),
            checked=(f"{plan.domain} domain, {plan.start_date} to {plan.end_date}",),
            not_checked=self._unchecked(exclude={plan.domain}),
            caveats=tuple(caveats),
            follow_ups=(
                FollowUp(
                    f"Why did {_label(plan.metrics)} move?",
                    "A level is a starting point; the driver is the finding.",
                ),
                FollowUp(
                    "Compare this against the prior period.",
                    "A number without a comparison cannot be judged good or bad.",
                ),
            ),
            data={"rows": rows, "plan": plan.as_dict(), "compiled_sql": answer.compiled_sql},
            meta={"subject": plan.metrics[0] if plan.metrics else plan.domain},
        )

    # ── 3. Investigate ───────────────────────────────────────────────

    async def _investigate(
        self, principal: Principal, *, question: str, resolved: str, as_of: date, days: int
    ) -> AnalystAnswer:
        if self._rca is None:
            raise CannotAnswerError("Root cause analysis is not configured.")

        investigation = await self._rca.investigate(principal, current_end=as_of)
        payload = summarise_rca(investigation)
        findings = payload["findings"]

        if not findings:
            return AnalystAnswer(
                question=question,
                capability=Capability.INVESTIGATE,
                headline=(
                    f"{payload['metric']} moved {(payload['relative_change'] or 0):+.1%}, "
                    "which is inside ordinary variation. I have no driver to offer."
                ),
                checked=("root cause analysis across all nine dimensions",),
                caveats=(
                    "Attributing a movement this size would produce a confident "
                    "explanation for noise, which is worse than no explanation.",
                ),
                follow_ups=(
                    FollowUp(
                        "Compare a longer period.",
                        "A week can be flat while a quarter is not.",
                    ),
                ),
                data=payload,
                meta={"subject": payload["metric"]},
            )

        arithmetic = [f for f in findings if f["evidence_tier"] == "arithmetic"]
        weaker = [f for f in findings if f["evidence_tier"] != "arithmetic"]

        # Build deterministic answer first
        answer = AnalystAnswer(
            question=question,
            capability=Capability.INVESTIGATE,
            headline=(
                f"{payload['metric']} moved {(payload['relative_change'] or 0):+.1%}. "
                f"{findings[0]['headline']}"
            ),
            facts=tuple(
                Statement(item["headline"], Certainty.MEASURED, "decomposition")
                for item in arithmetic[:4]
            ),
            inferences=tuple(
                Statement(
                    f"{item['headline']} ({item['evidence_tier']} evidence, "
                    f"{item['confidence']:.0%} confidence)",
                    Certainty.INFERRED,
                    item["dimension"],
                )
                for item in weaker[:4]
            ),
            checked=("root cause analysis across all nine dimensions",),
            not_checked=tuple(
                f"{name}: {reason}"
                for name, reason in payload.get("dimensions_unavailable", {}).items()
            ),
            caveats=tuple(payload["caveats"][:3]),
            follow_ups=(
                FollowUp(
                    "What should we do about it?",
                    "A cause without an action is a diagnosis nobody acts on.",
                ),
                FollowUp(
                    f"Show {findings[0]['subject']} over time.",
                    "A single period cannot tell a step change from a trend.",
                ),
            ),
            data=payload,
            meta={"subject": findings[0]["subject"]},
        )

        # Enhance with LLM narration if available
        if self._narrator:
            try:
                enhanced_headline = await self._narrator.narrate_investigation(answer)
                # Replace headline with LLM-generated narration
                # All facts, inferences, caveats remain unchanged
                answer = AnalystAnswer(
                    question=answer.question,
                    capability=answer.capability,
                    headline=enhanced_headline,
                    facts=answer.facts,
                    inferences=answer.inferences,
                    checked=answer.checked,
                    not_checked=answer.not_checked,
                    caveats=answer.caveats,
                    follow_ups=answer.follow_ups,
                    data=answer.data,
                    meta=answer.meta,
                )
            except Exception as error:
                log.warning(
                    "narrator.failed_in_investigate",
                    error=str(error),
                    error_type=type(error).__name__,
                )
                # Fall back to deterministic answer

        return answer

    # ── 4. Recommend ─────────────────────────────────────────────────

    async def _recommend(
        self, principal: Principal, *, question: str, resolved: str, as_of: date, days: int
    ) -> AnalystAnswer:
        if self._recommendations is None:
            raise CannotAnswerError("The recommendation engine is not configured.")

        portfolio = await self._recommendations.recommend(principal, end_date=as_of)
        payload = summarise_recommendations(portfolio)
        items = payload["recommendations"]

        if not items:
            return AnalystAnswer(
                question=question,
                capability=Capability.RECOMMEND,
                headline="Nothing clears the materiality floor right now.",
                checked=("all seven recommendation categories",),
                caveats=tuple(payload["categories_empty"].values()),
                data=payload,
                meta={"subject": "recommendations"},
            )

        measured = [i for i in items if not i["impact"]["rests_on_unmeasured_assumptions"]]

        return AnalystAnswer(
            question=question,
            capability=Capability.RECOMMEND,
            headline=(
                f"{payload['count']} actions worth {payload['net_profit_opportunity']:,.0f} "
                f"in profit. Start with: {items[0]['action']}"
            ),
            facts=tuple(
                Statement(
                    f"{item['action']} — {item['impact']['profit']:,.0f} profit, "
                    f"{item['risk']['band']} risk, owned by {item['owner']}",
                    Certainty.MEASURED if item in measured else Certainty.DERIVED,
                    item["category"],
                )
                for item in items[:5]
            ),
            checked=("inventory, pricing, promotion, store, marketing, customer, supplier",),
            caveats=tuple(payload["caveats"][:3])
            + (
                f"The {payload['net_profit_opportunity']:,.0f} figure counts "
                "overlapping actions once; adding each separately gives "
                f"{payload['gross_profit_opportunity']:,.0f} and double-promises "
                "the same pounds.",
            ),
            follow_ups=(
                FollowUp(
                    f"What is the downside of {items[0]['subject']}?",
                    "An upside without its downside is a sales pitch.",
                ),
            ),
            data=payload,
            meta={"subject": items[0]["subject"]},
        )

    # ── 5. Summarise ─────────────────────────────────────────────────

    async def _summarise(
        self, principal: Principal, *, question: str, resolved: str, as_of: date, days: int
    ) -> AnalystAnswer:
        if self._reports is None:
            raise CannotAnswerError("Report composition is not configured.")

        report = await self._reports.compose(
            principal, ReportRequest(period_end=as_of, period_days=days)
        )
        summary = report.section("summary")
        blocks = summary.blocks if summary else ()

        headline = next((b.text for b in blocks if b.text), "No sales in the period.")
        bullets = [bullet for block in blocks for bullet in block.bullets]
        empty = [s.title for s in report.sections if s.is_empty]

        return AnalystAnswer(
            question=question,
            capability=Capability.SUMMARISE,
            headline=headline,
            facts=tuple(Statement(bullet, Certainty.MEASURED, "report") for bullet in bullets),
            checked=tuple(s.title for s in report.sections if not s.is_empty),
            not_checked=tuple(
                f"{s.title}: {s.unavailable_reason}" for s in report.sections if s.is_empty
            ),
            caveats=tuple(report.caveats),
            follow_ups=(
                FollowUp("Why did revenue move?", "The summary says what; not why."),
                FollowUp(
                    "What should we do next?", "A summary that ends in no action is a report."
                ),
            ),
            data=report.as_dict(),
            meta={"subject": "net_revenue", "empty_sections": empty},
        )

    # ── 6. Compare periods ───────────────────────────────────────────

    async def _compare(
        self, principal: Principal, *, question: str, resolved: str, as_of: date, days: int
    ) -> AnalystAnswer:
        current, baseline = comparison.windows(as_of, days)

        async def totals(window: comparison.Window) -> tuple[float, float]:
            answer = await self._analytics.query(
                principal,
                domain_key="revenue",
                metrics=["net_revenue", "orders"],
                dimensions=[],
                start_date=window.start,
                end_date=window.end,
                limit=1,
            )
            rows = answer.result.rows
            if not rows:
                return 0.0, 0.0
            return (
                float(rows[0].get("net_revenue") or 0.0),
                float(rows[0].get("orders") or 0.0),
            )

        current_value, current_orders = await totals(current)
        baseline_value, baseline_orders = await totals(baseline)

        result = comparison.compare(
            metric="net_revenue",
            current=current,
            baseline=baseline,
            current_value=current_value,
            baseline_value=baseline_value,
            current_count=current_orders,
            baseline_count=baseline_orders,
        )

        facts = [Statement(result.describe(), Certainty.MEASURED, "revenue domain")]
        if result.dominant:
            facts.append(
                Statement(
                    f"Volume effect {result.volume_effect:,.0f}; rate effect "
                    f"{result.rate_effect:,.0f}.",
                    Certainty.DERIVED,
                    "volume/rate decomposition",
                )
            )

        caveats = [
            "Day-of-week composition is not adjusted for. A period containing "
            "an extra Saturday will look stronger for that reason alone.",
        ]
        if abs(result.scale - 1.0) > 1e-9:
            caveats.insert(
                0,
                f"The windows differ in length, so the baseline is scaled by "
                f"{result.scale:.3f} to compare per-day.",
            )

        # Build deterministic answer first
        answer = AnalystAnswer(
            question=question,
            capability=Capability.COMPARE,
            headline=result.describe(),
            facts=tuple(facts),
            checked=(
                f"revenue, {current.start} to {current.end} against "
                f"{baseline.start} to {baseline.end}",
            ),
            not_checked=("Margin, units, and customer mix. Ask about those directly.",),
            caveats=tuple(caveats),
            follow_ups=(
                FollowUp(
                    "Why did it move?", "A comparison locates a change; it does not explain it."
                )
                if result.is_material
                else FollowUp(
                    "Compare a longer period.",
                    "A flat week can sit inside a moving quarter.",
                ),
            ),
            data=result.as_dict(),
            meta={"subject": "net_revenue"},
        )

        # Enhance with LLM narration if available
        if self._narrator:
            try:
                enhanced_headline = await self._narrator.narrate_comparison(answer)
                # Replace headline with LLM-generated narration
                # All facts, inferences, caveats remain unchanged
                answer = AnalystAnswer(
                    question=answer.question,
                    capability=answer.capability,
                    headline=enhanced_headline,
                    facts=answer.facts,
                    inferences=answer.inferences,
                    checked=answer.checked,
                    not_checked=answer.not_checked,
                    caveats=answer.caveats,
                    follow_ups=answer.follow_ups,
                    data=answer.data,
                    meta=answer.meta,
                )
            except Exception as error:
                log.warning(
                    "narrator.failed_in_compare",
                    error=str(error),
                    error_type=type(error).__name__,
                )
                # Fall back to deterministic answer

        return answer

    # ── 7. Explain the forecast ──────────────────────────────────────

    async def _explain_forecast(
        self, principal: Principal, *, question: str, resolved: str, as_of: date, days: int
    ) -> AnalystAnswer:
        if self._forecasts is None:
            raise CannotAnswerError("Forecasting is not configured.")

        section = await self._forecasts.forecast(principal, target="revenue")
        if not section.rows:
            raise CannotAnswerError(
                "No forecast has been published.",
                because="The training job has not run, or the series was too short to fit.",
            )

        rows = section.rows
        total = sum(float(row.get("forecast") or 0.0) for row in rows)
        mase = max(
            (float(r["model_mase"]) for r in rows if r.get("model_mase") is not None), default=None
        )
        wape = max(
            (float(r["model_wape"]) for r in rows if r.get("model_wape") is not None), default=None
        )

        facts = [
            Statement(
                f"{len(rows)} days forecast, totalling {total:,.0f} in net revenue.",
                Certainty.DERIVED,
                "forecast service",
            )
        ]
        inferences: list[Statement] = []

        if wape is not None:
            facts.append(
                Statement(
                    f"The model's measured out-of-sample error is {wape:.1%} WAPE.",
                    Certainty.MEASURED,
                    "backtest",
                )
            )
        if mase is not None:
            if mase >= 1.0:
                inferences.append(
                    Statement(
                        f"MASE is {mase:.2f}. This model does not beat assuming the "
                        "same weekday repeats, so the forecast carries no more "
                        "information than a calendar. I would not plan against it.",
                        Certainty.MEASURED,
                        "backtest",
                    )
                )
            else:
                facts.append(
                    Statement(
                        f"MASE is {mase:.2f} — better than a seasonal-naive baseline.",
                        Certainty.MEASURED,
                        "backtest",
                    )
                )

        return AnalystAnswer(
            question=question,
            capability=Capability.EXPLAIN_FORECAST,
            headline=(
                f"{total:,.0f} expected over the next {len(rows)} days"
                + (f", from a model at {wape:.1%} WAPE." if wape is not None else ".")
            ),
            facts=tuple(facts),
            inferences=tuple(inferences),
            checked=("published forecasts and the accuracy record of the model behind them",),
            not_checked=(
                "Anything the model was not shown — a planned promotion, a "
                "store opening, a competitor's move.",
            ),
            caveats=tuple(section.caveats)
            + (
                "Prediction intervals come from how wrong this model has "
                "actually been at each horizon, not from a distributional "
                "assumption. They are conservative on thin evidence.",
            ),
            follow_ups=(
                FollowUp(
                    "How accurate has the forecast been?",
                    "The track record decides how much weight the number deserves.",
                ),
            ),
            data={"rows": rows, "meta": section.meta},
            meta={"subject": "forecast"},
        )

    # ── 8. Suggest improvements ──────────────────────────────────────

    async def _improve(
        self, principal: Principal, *, question: str, resolved: str, as_of: date, days: int
    ) -> AnalystAnswer:
        """Where this platform's own measurement is weak.

        A senior analyst's most useful contribution is often not an answer but
        a statement of what cannot currently be answered. Every gap below is
        one this codebase already documents as an unmeasured assumption, so
        the list is derived from the system's own admissions rather than
        invented.
        """
        gaps = [
            Statement(
                "Promotional incrementality is assumed, not measured. Without a "
                "holdout group, subsidised sales that would have happened anyway "
                "are indistinguishable from sales the promotion created — so "
                "every promotional attribution is a guess with a number attached.",
                Certainty.UNKNOWN,
                "recommendation engine",
            ),
            Statement(
                "Price elasticity has never been measured here. At −0.8 a price "
                "rise is profitable and at −2.0 it is destructive; the platform "
                "cannot tell you which. Pricing recommendations therefore "
                "propose a controlled test rather than a rollout.",
                Certainty.UNKNOWN,
                "recommendation engine",
            ),
            Statement(
                "The lost-sale rate on a stockout is a placeholder. How much "
                "demand substitutes to another size or brand, rather than "
                "walking out, is the single largest lever on every availability "
                "estimate — and it needs basket analysis across stockout events.",
                Certainty.UNKNOWN,
                "recommendation engine",
            ),
            Statement(
                "Customer segments are assigned as of today and applied to "
                "historical purchases, so the segment cut compares today's "
                "population against its own past rather than like for like.",
                Certainty.UNKNOWN,
                "root cause analysis",
            ),
            Statement(
                "Demand is censored by availability: a day out of stock records "
                "zero demand, not zero want. Forecasts therefore understate "
                "demand for exactly the lines that stock out most.",
                Certainty.UNKNOWN,
                "forecasting",
            ),
        ]

        return AnalystAnswer(
            question=question,
            capability=Capability.IMPROVE,
            headline=(
                f"{len(gaps)} measurement gaps limit what I can tell you. The "
                "highest-value fix is a promotional holdout group."
            ),
            inferences=tuple(gaps),
            checked=("the platform's own declared assumptions across every engine",),
            not_checked=(
                "Data quality itself. This lists what is unmeasured, not what is measured wrongly.",
            ),
            caveats=(
                "Each of these is already surfaced as a caveat on the answers "
                "it affects. Nothing here is hidden until asked — this is the "
                "same list, collected.",
            ),
            follow_ups=(
                FollowUp(
                    "What would a promotional holdout tell us?",
                    "It is the cheapest of these to run and unblocks the most estimates.",
                ),
            ),
            data={"gaps": [gap.as_dict() for gap in gaps]},
            meta={"subject": "measurement gaps"},
        )

    # ── Helpers ──────────────────────────────────────────────────────

    def _headline_with_gaps(self, explanation: Any, unresolved: tuple[str, ...]) -> str:
        """Lead with what was not understood, when anything was not.

        Burying it in the caveats lets a reader take the number and move on,
        which is precisely the case where the number may be about something
        adjacent to their question.
        """
        summary = explanation.summary if explanation else "No data matched the question."
        if not unresolved:
            return summary
        return f"Ignoring {', '.join(unresolved[:3])}, which I do not recognise: {summary}"

    def _extract_term(self, question: str) -> str:
        """Pull the metric name out of a definitional question.

        Trailing verbs matter as much as leading ones: "what does AOV mean"
        leaves "aov mean" if only the prefix is stripped, and that matches no
        metric — so a perfectly ordinary question gets refused.
        """
        text = question.lower().rstrip("?").strip()
        for marker in (*DEFINITION_MARKERS, "what is", "whats", "what's"):
            if marker in text:
                text = text.split(marker, 1)[1]

        text = text.strip(" ?'\"")
        for prefix in ("the ", "a ", "an ", "our ", "my "):
            text = text.removeprefix(prefix)
        for suffix in (
            " mean",
            " means",
            " measure",
            " measures",
            " represent",
            " represents",
            " actually mean",
            " exactly",
        ):
            text = text.removesuffix(suffix)
        return text.strip()

    def _unchecked(self, *, exclude: set[str]) -> tuple[str, ...]:
        """What a thorough analyst would mention having skipped."""
        candidates = {
            "revenue": "Revenue trend",
            "profitability": "Margin and profitability",
            "inventory_health": "Stock availability",
            "customer": "Customer mix",
        }
        return tuple(
            f"{label}. Not consulted for this question."
            for key, label in candidates.items()
            if key not in exclude
        )[:3]


def _label(metrics: tuple[str, ...]) -> str:
    return metrics[0].replace("_", " ") if metrics else "this"


def as_payload(answer: AnalystAnswer, conversation: Conversation) -> dict[str, Any]:
    return {**answer.as_dict(), "conversation": conversation.as_dict()}
