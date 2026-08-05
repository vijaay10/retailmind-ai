"""Natural-language query orchestration (Analytics §11, ARCH ADR-9).

Plans a question, validates the plan against the registry, and either runs it
through the governed compiler or routes it to the engine that can actually
answer it.

**The security posture, stated once and enforced everywhere below.** A user's
question is untrusted text and is never treated as anything else. It is never
concatenated into a statement, never passed to a database driver, and never
logged verbatim into an audit trail that something else parses. The only thing
derived from it is a :class:`QueryPlan` whose every field must resolve to a
registry key, and the registry is a closed set that no request can extend.

There are four independent layers, and the feature would still be safe if any
three failed:

1. **No SQL is generated from text.** The planner emits registry keys.
2. **Validation rejects anything unknown.** ``AnalyticsRequest.validate``
   refuses a metric, dimension, or filter the domain does not declare.
3. **The compiler owns the statement.** Identifiers come from the registry,
   values are bound as parameters, operators come from a fixed map.
4. **The connection is read-only.** Even a compiler bug cannot mutate data.

Routing matters as much as safety. "Why did sales decrease?" is not a query,
and answering it with a table of numbers produces something that looks like an
answer while omitting the entire question. It goes to root cause analysis;
"what will sell next week" goes to the forecasts; "what should I do" goes to
the recommendation engine. Each already returns its own graded, caveated
answer, and this service passes those through rather than restating them.
"""

from dataclasses import dataclass
from datetime import date
from typing import Any

import structlog

from app.domain.auth.entities import Principal
from app.domain.auth.permissions import Permission
from app.services.analytics.service import AnalyticsService
from app.services.forecasting.service import ForecastingService
from app.services.nlq import charts, explain
from app.services.nlq.contracts import (
    Answer,
    Explanation,
    Intent,
    QueryPlan,
    UnsupportedQuestionError,
)
from app.services.nlq.planner import DeterministicPlanner, Planner, normalise_question
from app.services.nlq.vocabulary import Vocabulary
from app.services.rca.service import RootCauseService
from app.services.rca.service import summarise as summarise_rca
from app.services.recommendations.service import RecommendationService
from app.services.recommendations.service import summarise as summarise_recommendations
from app.services.shared import authz

log = structlog.get_logger(__name__)

#: Rows a natural-language answer may return. Lower than the analytics API's
#: own cap: a conversational answer is read, not exported, and a thousand rows
#: in a chat window is a denial of service against the reader.
MAX_ROWS = 200


@dataclass(frozen=True, slots=True)
class _Routed:
    payload: dict[str, Any]
    summary: str
    details: tuple[str, ...]
    caveats: tuple[str, ...]


class NaturalLanguageService:
    """Answers questions asked in English, through the governed layer."""

    def __init__(
        self,
        analytics: AnalyticsService,
        *,
        rca: RootCauseService | None = None,
        forecasts: ForecastingService | None = None,
        recommendations: RecommendationService | None = None,
        planner: Planner | None = None,
        vocabulary: Vocabulary | None = None,
    ) -> None:
        self._analytics = analytics
        self._rca = rca
        self._forecasts = forecasts
        self._recommendations = recommendations
        self._vocabulary = vocabulary or Vocabulary()
        self._planner = planner or DeterministicPlanner(self._vocabulary)

    async def ask(
        self, principal: Principal, question: str, *, as_of: date | None = None
    ) -> Answer:
        """Plan, validate, and answer one question."""
        authz.require(principal, Permission.NLQ_ASK)

        cleaned = normalise_question(question)
        today = as_of or date.today()
        plan = self._planner.plan(cleaned, as_of=today)

        log.info(
            "nlq.planned",
            # The plan is logged, not the question. A structured plan cannot
            # forge a log line, and it is what an auditor actually needs:
            # which relation was read, with which metrics.
            intent=plan.intent.value,
            domain=plan.domain,
            metrics=list(plan.metrics),
            dimensions=list(plan.dimensions),
            question_length=len(cleaned),
        )

        if plan.intent is Intent.METRIC_QUERY:
            return await self._answer_query(principal, cleaned, plan)
        return await self._answer_routed(principal, cleaned, plan, today)

    # ── Metric queries ───────────────────────────────────────────────

    async def _answer_query(self, principal: Principal, question: str, plan: QueryPlan) -> Answer:
        if not plan.metrics:
            raise UnsupportedQuestionError(
                "the question named nothing measurable",
                hint="Name a measure, for example 'revenue' or 'units'.",
                unresolved=plan.unresolved,
            )

        # Every name below is checked again inside the analytics service. The
        # duplication is intentional: this layer must not be the only thing
        # standing between a planner and the compiler.
        answer = await self._analytics.query(
            principal,
            domain_key=plan.domain,
            metrics=list(plan.metrics),
            dimensions=list(plan.dimensions),
            start_date=plan.start_date,
            end_date=plan.end_date,
            filters=dict(plan.filters) or None,
            sort_by=plan.sort_by,
            descending=plan.descending,
            limit=min(plan.limit, MAX_ROWS),
        )

        rows = answer.result.rows
        chart = charts.choose(dimensions=plan.dimensions, metrics=plan.metrics, rows=rows)
        explanation = explain.build(plan, rows)

        return Answer(
            question=question,
            plan=plan,
            columns=tuple(answer.result.columns),
            rows=tuple(rows),
            chart=chart,
            explanation=explanation,
            compiled_sql=answer.result.compiled_sql,
            meta=answer.result.meta,
        )

    # ── Routed questions ─────────────────────────────────────────────

    async def _answer_routed(
        self, principal: Principal, question: str, plan: QueryPlan, as_of: date
    ) -> Answer:
        routed = await self._route(principal, plan, as_of)
        return Answer(
            question=question,
            plan=plan,
            routed_to=plan.intent.value,
            payload=routed.payload,
            explanation=Explanation(
                summary=routed.summary,
                details=routed.details,
                caveats=(plan.interpretation, *routed.caveats),
            ),
        )

    async def _route(self, principal: Principal, plan: QueryPlan, as_of: date) -> _Routed:
        if plan.intent is Intent.DIAGNOSIS:
            if self._rca is None:
                raise UnsupportedQuestionError("root cause analysis is not available")
            investigation = await self._rca.investigate(principal, current_end=as_of)
            payload = summarise_rca(investigation)
            findings = payload["findings"]
            headline = (
                findings[0]["headline"]
                if findings
                else "No cause cleared the materiality floor for this period."
            )
            return _Routed(
                payload=payload,
                summary=(
                    f"{payload['metric']} moved "
                    f"{(payload['relative_change'] or 0):+.1%}. {headline}"
                ),
                details=tuple(item["headline"] for item in findings[:4]),
                caveats=tuple(payload["caveats"]),
            )

        if plan.intent is Intent.FORECAST:
            if self._forecasts is None:
                raise UnsupportedQuestionError("forecasts are not available")
            section = await self._forecasts.forecast(principal, target="revenue")
            total = sum(float(row.get("forecast") or 0.0) for row in section.rows)
            return _Routed(
                payload={"data": section.rows, "meta": section.meta},
                summary=(
                    f"{len(section.rows)} day(s) forecast, totalling {total:,.0f} "
                    "in expected net revenue."
                ),
                details=(),
                caveats=tuple(section.caveats),
            )

        if plan.intent is Intent.RECOMMENDATION:
            if self._recommendations is None:
                raise UnsupportedQuestionError("recommendations are not available")
            portfolio = await self._recommendations.recommend(principal, end_date=as_of)
            payload = summarise_recommendations(portfolio)
            return _Routed(
                payload=payload,
                summary=(
                    f"{payload['count']} recommendation(s), worth "
                    f"{payload['net_profit_opportunity']:,.0f} in profit after "
                    "removing overlapping actions."
                ),
                details=tuple(item["action"] for item in payload["recommendations"][:4]),
                caveats=tuple(payload["caveats"]),
            )

        raise UnsupportedQuestionError(
            "the question was understood but cannot be answered here",
            hint="Try asking about a metric, a cause, a forecast, or an action.",
        )

    # ── Introspection ────────────────────────────────────────────────

    def catalogue(self, principal: Principal) -> list[dict[str, Any]]:
        """What can be asked about.

        Published deliberately. A natural-language interface whose scope is
        invisible trains users to guess, and a guess that returns an answer is
        indistinguishable from one that returns the *right* answer.
        """
        authz.require(principal, Permission.NLQ_ASK)
        return self._vocabulary.catalogue()
