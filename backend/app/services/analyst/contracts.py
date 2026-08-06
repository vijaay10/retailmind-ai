"""What the analyst says, and what makes it senior rather than junior.

The difference between a junior and a senior analyst is not that the senior
knows more. It is that the senior tells you what they *did not* check, says
"that isn't measurable here" without embarrassment, distinguishes what the
data shows from what they infer, and volunteers the next question worth
asking. A junior answers exactly what was asked, confidently, and leaves you
to discover the caveat yourself.

That distinction is encoded structurally here rather than left to phrasing.
Every answer must carry:

``checked``
    The surfaces consulted. A reader can tell whether the answer rests on the
    thing they care about.

``not_checked``
    What was left out, and why. The most valuable sentence a senior analyst
    says is usually "I haven't looked at returns yet" — and an assistant that
    never says it is one whose silences are unreadable.

``facts`` versus ``inferences``
    Kept apart. A decomposition is arithmetic; an explanation for it is a
    hypothesis. Presenting them in one list is how a reader comes to treat a
    guess as a measurement.

``follow_ups``
    The next question. An analyst who answers exactly what was asked and stops
    is a search box; the value is in knowing what to ask next.

None of this needs a language model. The judgement lives in which surface
answers which question and what each one cannot establish, and that is
knowledge about *this platform* rather than about English.
"""

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from typing import Any


class Capability(StrEnum):
    """The eight things this assistant does."""

    ANSWER = "answer"
    """A factual question about the numbers."""

    EXPLAIN_KPI = "explain_kpi"
    """What a metric means, how it is computed, and how it is misread."""

    INVESTIGATE = "investigate"
    """Why something moved."""

    RECOMMEND = "recommend"
    """What to do about it."""

    SUMMARISE = "summarise"
    """The state of the business, briefly."""

    COMPARE = "compare"
    """This period against another."""

    EXPLAIN_FORECAST = "explain_forecast"
    """What the forecast says and how much to trust it."""

    IMPROVE = "improve"
    """Where the platform's own measurement is weak."""


class Certainty(StrEnum):
    """How firmly a statement is being made."""

    MEASURED = "measured"
    """Read from the warehouse. Arithmetic, reproducible."""

    DERIVED = "derived"
    """Computed from measured values through a stated relationship."""

    INFERRED = "inferred"
    """A hypothesis consistent with the data. Not established by it."""

    UNKNOWN = "unknown"
    """The platform cannot answer this. Said plainly rather than approximated."""


@dataclass(frozen=True, slots=True)
class Statement:
    """One thing the analyst asserts, with how firmly."""

    text: str
    certainty: Certainty = Certainty.MEASURED
    source: str = ""
    """Which surface it came from, so a reader can go and check."""

    def as_dict(self) -> dict[str, Any]:
        return {"text": self.text, "certainty": self.certainty.value, "source": self.source}


@dataclass(frozen=True, slots=True)
class FollowUp:
    """A question worth asking next, and why it is worth asking."""

    question: str
    because: str

    def as_dict(self) -> dict[str, Any]:
        return {"question": self.question, "because": self.because}


@dataclass(frozen=True, slots=True)
class AnalystAnswer:
    """One reply, in the shape a senior analyst actually gives."""

    question: str
    capability: Capability
    headline: str
    """The answer in one sentence. A reader who stops here should not be
    misled by having stopped."""

    facts: tuple[Statement, ...] = ()
    inferences: tuple[Statement, ...] = ()
    checked: tuple[str, ...] = ()
    not_checked: tuple[str, ...] = ()
    caveats: tuple[str, ...] = ()
    follow_ups: tuple[FollowUp, ...] = ()
    data: dict[str, Any] = field(default_factory=dict)
    """The structured payload behind the prose, so a client can chart it."""

    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def is_answerable(self) -> bool:
        return self.capability is not Capability.ANSWER or bool(self.facts)

    def as_dict(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "capability": self.capability.value,
            "headline": self.headline,
            "facts": [item.as_dict() for item in self.facts],
            "inferences": [item.as_dict() for item in self.inferences],
            "checked": list(self.checked),
            "not_checked": list(self.not_checked),
            "caveats": list(self.caveats),
            "follow_ups": [item.as_dict() for item in self.follow_ups],
            "data": self.data,
            "meta": self.meta,
        }


@dataclass(frozen=True, slots=True)
class Turn:
    """One exchange, kept so the next question can refer back to it."""

    question: str
    capability: Capability
    subject: str = ""
    """What the turn was about — a metric, a region, a store. This is what
    "why did *that* happen?" resolves against."""

    period_end: date | None = None
    period_days: int = 0
    asked_at: datetime | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "capability": self.capability.value,
            "subject": self.subject,
            "period_end": self.period_end.isoformat() if self.period_end else None,
            "period_days": self.period_days,
        }


@dataclass(frozen=True, slots=True)
class Conversation:
    """The turns so far.

    Deliberately small and explicit rather than a free-form transcript. What a
    follow-up needs is the *subject* and *period* of the last answer, not its
    prose — and carrying prose forward is how an assistant starts answering
    questions about its own earlier phrasing.
    """

    turns: tuple[Turn, ...] = ()

    #: How far back a pronoun may reach. Beyond a couple of turns, "that" is
    #: ambiguous to the human asking as well, and guessing produces an answer
    #: about the wrong thing delivered with full confidence.
    MAX_LOOKBACK = 3

    @property
    def last(self) -> Turn | None:
        return self.turns[-1] if self.turns else None

    def recent_subject(self) -> str:
        """The most recent thing worth resolving a pronoun against."""
        for turn in reversed(self.turns[-self.MAX_LOOKBACK :]):
            if turn.subject:
                return turn.subject
        return ""

    def extend(self, turn: Turn) -> "Conversation":
        return Conversation(turns=(*self.turns, turn)[-20:])

    def as_dict(self) -> dict[str, Any]:
        return {"turns": [turn.as_dict() for turn in self.turns]}


class CannotAnswerError(Exception):
    """Raised when the platform genuinely cannot answer.

    A distinct exception rather than a low-confidence answer. An assistant that
    always produces *something* teaches its users that every reply is a guess,
    and the guesses are indistinguishable from the correct answers. Saying "I
    can't tell you that, and here is why" is the more senior response.
    """

    def __init__(self, message: str, *, because: str = "", instead: tuple[str, ...] = ()) -> None:
        super().__init__(message)
        self.because = because
        self.instead = instead
