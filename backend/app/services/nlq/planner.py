"""Turning a question into a plan.

The deterministic planner runs first and handles the shapes people actually
ask in — "show top customers", "compare stores", "revenue by region last
month", "why did sales drop". It is fast, free, reproducible, and has no
prompt-injection surface at all, because it never sends the question anywhere;
it matches words against a closed vocabulary and discards everything else.

A language model is a *fallback* for questions this cannot parse, not the
front door. That ordering is a design decision rather than an optimisation:
the common path stays auditable and identical every time it runs, and the
model is only consulted where determinism has already failed. When it is
consulted, its output goes through exactly the same validation — the model is
untrusted input, not a trusted component.

**Nothing in this module builds SQL.** The most a planner can do is choose
registry keys. A question containing a semicolon and a DROP statement produces
a plan whose metric list is empty, and that plan is rejected for being empty —
the same way a question about the weather on Mars is rejected.
"""

import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Protocol

import structlog

from app.services.nlq.contracts import Intent, QueryPlan, UnsupportedQuestionError
from app.services.nlq.vocabulary import Vocabulary

log = structlog.get_logger(__name__)

#: Words that make a question a *why*, not a *what*. Routed to root cause
#: analysis, because no SELECT statement answers "why" — and one that appears
#: to is a table the reader will over-read.
DIAGNOSIS_MARKERS = (
    "why",
    "reason",
    "cause",
    "caused",
    "explain the drop",
    "what happened",
    "driving",
    "behind the",
)

FORECAST_MARKERS = (
    "forecast",
    "predict",
    "prediction",
    "projected",
    "will sell",
    "next week",
    "next month",
    "expect",
    "expected to",
    "going to",
)

RECOMMENDATION_MARKERS = (
    "recommend",
    "should i",
    "should we",
    "what to do",
    "what should",
    "suggest",
    "advice",
    "action",
    "how do i fix",
    "how do we fix",
)

#: Words implying a decline, used to sharpen a diagnosis interpretation.
DECLINE_MARKERS = ("decrease", "decline", "drop", "fell", "fall", "down", "lower", "worse")

#: "top" / "best" / "worst" — ranking, with the direction that implies.
ASCENDING_MARKERS = ("worst", "lowest", "bottom", "least", "weakest")

#: Relative periods a question may name, in days.
PERIOD_PATTERNS: tuple[tuple[str, int], ...] = (
    ("last 7 days", 7),
    ("last week", 7),
    ("past week", 7),
    ("last 14 days", 14),
    ("last fortnight", 14),
    ("last 30 days", 30),
    ("last month", 30),
    ("past month", 30),
    ("last 90 days", 90),
    ("last quarter", 90),
    ("last year", 365),
)

DEFAULT_PERIOD_DAYS = 30
DEFAULT_LIMIT = 20
MAX_LIMIT = 200

#: Questions longer than this are almost certainly not questions. Bounding the
#: input keeps a planner from being handed a payload to chew through, and a
#: genuine question has never needed this many characters.
MAX_QUESTION_LENGTH = 500

_WORD = re.compile(r"[a-z][a-z_]*")
_TOP_N = re.compile(r"\b(?:top|bottom|first|best|worst)\s+(\d{1,3})\b")


class Planner(Protocol):
    """Anything that turns a question into a plan.

    The contract is narrow on purpose: a plan, or an exception. There is no
    variant that returns SQL, and no implementation could add one without
    changing this signature — which is the kind of change a reviewer notices.
    """

    def plan(self, question: str, *, as_of: date) -> QueryPlan: ...


@dataclass(frozen=True, slots=True)
class DeterministicPlanner:
    """Rule-based planning over the registry vocabulary."""

    vocabulary: Vocabulary

    def plan(self, question: str, *, as_of: date) -> QueryPlan:
        text = _normalise(question)
        intent = self._intent(text)

        if intent is not Intent.METRIC_QUERY:
            return QueryPlan(
                intent=intent,
                start_date=as_of - timedelta(days=DEFAULT_PERIOD_DAYS - 1),
                end_date=as_of,
                interpretation=self._routed_interpretation(intent, text),
                confidence=0.8,
            )

        return self._metric_plan(text, as_of=as_of)

    # ── Intent ───────────────────────────────────────────────────────

    def _intent(self, text: str) -> Intent:
        # Order matters. "What should I do about the forecast" is a request for
        # advice, not a forecast, so recommendation markers are checked first.
        if any(marker in text for marker in RECOMMENDATION_MARKERS):
            return Intent.RECOMMENDATION
        if any(marker in text for marker in DIAGNOSIS_MARKERS):
            return Intent.DIAGNOSIS
        if any(marker in text for marker in FORECAST_MARKERS):
            return Intent.FORECAST
        return Intent.METRIC_QUERY

    def _routed_interpretation(self, intent: Intent, text: str) -> str:
        if intent is Intent.DIAGNOSIS:
            direction = "decline" if any(m in text for m in DECLINE_MARKERS) else "movement"
            return (
                f"Read as a question about the cause of a {direction}. Routed to "
                "root cause analysis, which decomposes the change and grades "
                "candidate explanations — a query cannot answer 'why'."
            )
        if intent is Intent.FORECAST:
            return (
                "Read as a question about what happens next. Routed to the "
                "published forecasts, which travel with the accuracy record of "
                "the model that produced them."
            )
        return (
            "Read as a request for action. Routed to the recommendation "
            "engine, which returns actions with their impact, confidence, and "
            "downside."
        )

    # ── Metric queries ───────────────────────────────────────────────

    def _metric_plan(self, text: str, *, as_of: date) -> QueryPlan:
        # A period phrase is consumed before anything else looks at the text.
        # Otherwise "last week" both sets the window *and* matches the date
        # dimension, so "revenue by region last week" silently becomes revenue
        # by region *and day* — twenty rows of daily detail in answer to a
        # question about five regions.
        start, end, remainder = self._take_period(text, as_of)
        text = remainder
        words = _WORD.findall(text)
        if not words:
            raise UnsupportedQuestionError(
                "no recognisable terms in the question",
                hint="Try naming a subject, for example 'revenue by region'.",
            )

        domain_key, domain_term = self._pick_domain(words)
        if domain_key is None:
            raise UnsupportedQuestionError(
                "could not tell what the question is about",
                hint=(
                    "Name a subject this platform knows: "
                    f"{', '.join(self.vocabulary.domain_keys[:8])}…"
                ),
                unresolved=tuple(words[:8]),
            )

        metrics = self._pick_metrics(words, domain_key)
        dimensions = self._pick_dimensions(words, domain_key, text)
        limit, descending = self._pick_ranking(text)

        # Anything that resolved to nothing is reported. A question mentioning
        # "margin by courier" should not quietly answer about margin alone.
        unresolved = tuple(
            word
            for word in dict.fromkeys(words)
            if word != domain_term
            and not self.vocabulary.resolve_metric(word, domain_key=domain_key).resolved
            and not self.vocabulary.resolve_dimension(word, domain_key=domain_key).resolved
            and word not in _STOPWORDS
        )

        defaulted = not any(
            self.vocabulary.resolve_metric(word, domain_key=domain_key).resolved for word in words
        )
        interpretation = (
            f"Reading this as {', '.join(metrics)} from {domain_key}"
            + (f", grouped by {', '.join(dimensions)}" if dimensions else "")
            + (f", {start} to {end}" if start else "")
            + (
                "; no measure was named, so the domain's headline metrics were used"
                if defaulted
                else ""
            )
            + "."
        )

        return QueryPlan(
            intent=Intent.METRIC_QUERY,
            domain=domain_key,
            metrics=metrics,
            dimensions=dimensions,
            start_date=start,
            end_date=end,
            sort_by=metrics[0] if metrics else None,
            descending=descending,
            limit=limit,
            confidence=0.85 if not defaulted else 0.6,
            interpretation=interpretation,
            unresolved=unresolved[:8],
        )

    def _pick_domain(self, words: list[str]) -> tuple[str | None, str]:
        for word in words:
            resolution = self.vocabulary.resolve_domain(word)
            if resolution.resolved:
                return resolution.key, word
        return None, ""

    def _pick_metrics(self, words: list[str], domain_key: str) -> tuple[str, ...]:
        found: list[str] = []
        for word in words:
            resolution = self.vocabulary.resolve_metric(word, domain_key=domain_key)
            if resolution.resolved and resolution.key not in found:
                found.append(resolution.key)  # type: ignore[arg-type]
        return tuple(found[:4]) or self.vocabulary.default_metrics(domain_key)

    def _pick_dimensions(self, words: list[str], domain_key: str, text: str) -> tuple[str, ...]:
        found: list[str] = []
        for word in words:
            resolution = self.vocabulary.resolve_dimension(word, domain_key=domain_key)
            if resolution.resolved and resolution.key not in found:
                found.append(resolution.key)  # type: ignore[arg-type]

        if found:
            return tuple(found[:2])

        # "Compare X" and "top X" both want the things listed out, so fall back
        # to the domain's natural cut rather than returning a single total.
        if any(marker in text for marker in ("compare", "top", "best", "worst", "by ", "each")):
            default = self.vocabulary.default_dimension(domain_key)
            if default:
                return (default,)
        return ()

    def _take_period(self, text: str, as_of: date) -> tuple[date, date, str]:
        """Resolve a period phrase and remove it from the question.

        Longest phrase first, so "last 7 days" is not matched as "last" plus
        stray words, and the matched span is stripped so it cannot be read a
        second time as a grouping.
        """
        for phrase, days in sorted(PERIOD_PATTERNS, key=lambda item: -len(item[0])):
            if phrase in text:
                return (
                    as_of - timedelta(days=days - 1),
                    as_of,
                    text.replace(phrase, " "),
                )
        return as_of - timedelta(days=DEFAULT_PERIOD_DAYS - 1), as_of, text

    def _pick_ranking(self, text: str) -> tuple[int, bool]:
        limit = DEFAULT_LIMIT
        match = _TOP_N.search(text)
        if match:
            limit = max(1, min(MAX_LIMIT, int(match.group(1))))
        descending = not any(marker in text for marker in ASCENDING_MARKERS)
        return limit, descending


def normalise_question(question: str) -> str:
    """Validate and normalise raw input before any planner sees it.

    Bounds the length and strips control characters. Neither is an injection
    defence — the registry is — but an unbounded string is an easy denial of
    service and a control character in a logged question is an easy way to
    forge a log line.
    """
    if not question or not question.strip():
        raise UnsupportedQuestionError("the question is empty")
    if len(question) > MAX_QUESTION_LENGTH:
        raise UnsupportedQuestionError(
            f"the question is longer than {MAX_QUESTION_LENGTH} characters",
            hint="Ask something shorter and more specific.",
        )
    return "".join(char for char in question if char.isprintable() or char == " ").strip()


def _normalise(question: str) -> str:
    return normalise_question(question).lower()


#: Words that carry no meaning for planning. Filtered out of the unresolved
#: report so it names the terms a user might actually want to fix.
_STOPWORDS = frozenset(
    {
        "show",
        "me",
        "the",
        "a",
        "an",
        "of",
        "for",
        "in",
        "by",
        "and",
        "or",
        "to",
        "top",
        "bottom",
        "best",
        "worst",
        "list",
        "give",
        "get",
        "what",
        "which",
        "how",
        "many",
        "much",
        "is",
        "are",
        "was",
        "were",
        "do",
        "does",
        "did",
        "compare",
        "comparison",
        "across",
        "between",
        "over",
        "last",
        "past",
        "this",
        "that",
        "with",
        "from",
        "all",
        "each",
        "per",
        "my",
        "our",
        "us",
        "please",
        "can",
        "you",
        "week",
        "month",
        "year",
        "day",
        "days",
        "quarter",
    }
)
