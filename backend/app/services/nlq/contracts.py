"""Natural-language query contracts, and the security model behind them.

**No component here generates SQL, and no model output is ever executed.**
That is the entire injection defence, and it is worth stating plainly because
the usual approach — have a model write SQL, then sanitise it — cannot be made
safe. Sanitising assumes you can tell a malicious query from a legitimate one
by inspection, and you cannot: `SELECT * FROM users` is syntactically perfect,
parameterised, and catastrophic. Parameter binding does not help either, since
binding protects *values* and here the whole statement is attacker-influenced.

So the pipeline never produces a string that could be a statement:

    question (untrusted text)
        → QueryPlan   — structured, every field a registry name
        → validation  — anything not in the registry is rejected here
        → the existing semantic compiler
        → read-only connection

A planner's only power is to *choose from a closed vocabulary*. It cannot name
a table, cannot write a predicate, cannot express a join. "Show top customers;
DROP TABLE users" resolves to a metric name that does not exist, and fails at
validation with the same message as a typo — because to this system there is
no difference between the two.

That property holds identically whether the plan came from the deterministic
planner or from a language model, which is the point: the model is treated as
untrusted input, not as a trusted component.
"""

from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from typing import Any


class Intent(StrEnum):
    """What the user is actually asking for.

    Routing on intent matters because these questions are not all queries.
    "Why did sales decrease?" cannot be answered by any SELECT statement — the
    answer is a decomposition across dimensions with graded evidence, which is
    a different engine. Translating it into SQL anyway produces a table that
    looks like an answer and is not one.
    """

    METRIC_QUERY = "metric_query"
    """An aggregate over the warehouse: show, list, compare, rank."""

    DIAGNOSIS = "diagnosis"
    """Why did something move — routed to root cause analysis."""

    FORECAST = "forecast"
    """What happens next — routed to the published forecasts."""

    RECOMMENDATION = "recommendation"
    """What should be done — routed to the recommendation engine."""

    UNSUPPORTED = "unsupported"
    """Understood well enough to know it cannot be answered here."""


class ChartType(StrEnum):
    LINE = "line"
    BAR = "bar"
    HORIZONTAL_BAR = "horizontal_bar"
    BIG_NUMBER = "big_number"
    TABLE = "table"


@dataclass(frozen=True, slots=True)
class QueryPlan:
    """A resolved question, expressed only in registry vocabulary.

    Every string field is a key that must exist in the metric registry. This
    is deliberately *not* a place where free text can survive: there is no
    ``where_clause``, no ``sql``, no ``expression``. A planner that wants to
    filter may only name a dimension the registry declares and supply a value,
    which the compiler binds as a parameter.
    """

    intent: Intent
    domain: str = ""
    metrics: tuple[str, ...] = ()
    dimensions: tuple[str, ...] = ()
    filters: dict[str, str] = field(default_factory=dict)
    start_date: date | None = None
    end_date: date | None = None
    sort_by: str | None = None
    descending: bool = True
    limit: int = 20

    confidence: float = 1.0
    """How sure the planner is that this is what was asked. A low value is
    surfaced to the user rather than hidden — answering the wrong question
    confidently is worse than admitting the question was ambiguous."""

    interpretation: str = ""
    """What the planner understood, in words, so the user can correct it."""

    unresolved: tuple[str, ...] = ()
    """Parts of the question that could not be mapped to anything the registry
    knows. Reported rather than silently dropped: a question half-understood
    and answered anyway is how a user comes to trust a wrong number."""

    def as_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent.value,
            "domain": self.domain,
            "metrics": list(self.metrics),
            "dimensions": list(self.dimensions),
            "filters": dict(self.filters),
            "start_date": self.start_date.isoformat() if self.start_date else None,
            "end_date": self.end_date.isoformat() if self.end_date else None,
            "sort_by": self.sort_by,
            "descending": self.descending,
            "limit": self.limit,
            "confidence": round(self.confidence, 4),
            "interpretation": self.interpretation,
            "unresolved": list(self.unresolved),
        }


@dataclass(frozen=True, slots=True)
class ChartSpec:
    """A renderer-agnostic chart description.

    A specification rather than markup, so the same answer can be drawn by a
    web client, an export, or a notebook without this service knowing which.
    """

    type: ChartType
    x: str = ""
    y: tuple[str, ...] = ()
    title: str = ""
    rationale: str = ""
    """Why this chart shape was chosen. Charts mislead when the shape implies
    a relationship the data does not have — a line across categories implies
    continuity between them — so the choice is explained, not just made."""

    def as_dict(self) -> dict[str, Any]:
        return {
            "type": self.type.value,
            "x": self.x,
            "y": list(self.y),
            "title": self.title,
            "rationale": self.rationale,
        }


@dataclass(frozen=True, slots=True)
class Explanation:
    """The business reading of a result set."""

    summary: str
    details: tuple[str, ...] = ()
    caveats: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary,
            "details": list(self.details),
            "caveats": list(self.caveats),
        }


@dataclass(frozen=True, slots=True)
class Answer:
    """Everything returned for one question."""

    question: str
    plan: QueryPlan
    columns: tuple[str, ...] = ()
    rows: tuple[dict[str, Any], ...] = ()
    chart: ChartSpec | None = None
    explanation: Explanation | None = None
    compiled_sql: str = ""
    """The SQL that ran, shown for auditability.

    Displayed, never accepted. A user cannot send SQL to this service and
    there is no endpoint that would execute it if they did — this field exists
    so that an analyst can check the question was understood correctly, which
    is the one thing a natural-language interface most needs to prove.
    """

    routed_to: str = ""
    """Which engine answered, when the question was not a plain query."""

    payload: dict[str, Any] = field(default_factory=dict)
    """The routed engine's own response, passed through unchanged."""

    meta: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "plan": self.plan.as_dict(),
            "columns": list(self.columns),
            "rows": [dict(row) for row in self.rows],
            "row_count": len(self.rows),
            "chart": self.chart.as_dict() if self.chart else None,
            "explanation": self.explanation.as_dict() if self.explanation else None,
            "compiled_sql": self.compiled_sql,
            "routed_to": self.routed_to,
            "payload": self.payload,
            "meta": self.meta,
        }


class UnsupportedQuestionError(Exception):
    """Raised when a question cannot be mapped onto the registry.

    Deliberately an error rather than a best-effort answer. A system that
    always returns *something* teaches users that every response is a guess,
    and the guesses are indistinguishable from the correct answers.
    """

    def __init__(self, message: str, *, hint: str = "", unresolved: tuple[str, ...] = ()) -> None:
        super().__init__(message)
        self.hint = hint
        self.unresolved = unresolved
