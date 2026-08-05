"""What a root-cause finding is, and what it is allowed to claim.

This module exists because "root cause analysis" is a promise the underlying
data cannot keep. Everything here is observational: sales, stock, shipments,
and weather that happened alongside each other. Observational data supports
statements about *contribution* and *coincidence*. It does not support
statements about causation, and an engine that says "cause" while meaning
"correlate" will eventually send a team to fix something that was not broken.

So the vocabulary is deliberately graded, and the grade is enforced rather
than advisory:

``ARITHMETIC``
    The finding is a decomposition. "The Northeast accounts for 62% of the
    decline" is not an inference — it is subtraction, and it is exactly true
    by construction. It still does not say *why* the Northeast fell.

``MECHANICAL``
    A mechanism exists that necessarily reduces the metric. A stockout
    prevents a sale; there is no version of the world where a shelf is empty
    and the sale happens anyway. Strong, but still bounded: the mechanism
    explains the sales it prevented, not necessarily the whole drop.

``STATISTICAL``
    The factor moved significantly and a mechanism is plausible but
    unverified here. Late deliveries plausibly suppress repeat ecommerce
    orders; this platform has not measured that they do.

``ASSOCIATIVE``
    The factor moved with the metric and no mechanism is established in this
    data. Weather is the archetype: strongly predictive of footfall, entirely
    outside anyone's control, and impossible to confirm from sales alone.

Each tier carries a **confidence ceiling**. This is the point of the whole
design: correlation strength cannot promote a finding into a stronger tier.
A weather signal with an overwhelming z-score is still weather, and still
capped, because the limit is what the evidence *type* can support rather than
how loud the number is.
"""

from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from typing import Any


class EvidenceTier(StrEnum):
    ARITHMETIC = "arithmetic"
    MECHANICAL = "mechanical"
    STATISTICAL = "statistical"
    ASSOCIATIVE = "associative"


#: The most confidence each tier of evidence may ever express.
#:
#: Nothing reaches 1.0. A decomposition is exact arithmetic, and even then the
#: claim it licenses is "this slice accounts for the change", never "this slice
#: caused it" — the slice could be entirely a victim of something upstream.
#: Reserving the top of the scale for a certainty this data cannot deliver
#: keeps the scale honest.
TIER_CEILING: dict[EvidenceTier, float] = {
    EvidenceTier.ARITHMETIC: 0.95,
    EvidenceTier.MECHANICAL: 0.85,
    EvidenceTier.STATISTICAL: 0.65,
    EvidenceTier.ASSOCIATIVE: 0.45,
}


class ClaimType(StrEnum):
    """What a finding actually asserts. Rendered in the API verbatim."""

    ACCOUNTS_FOR = "accounts_for"
    """A share of the measured change is attributable to this slice, exactly."""

    MECHANISM = "mechanism"
    """A known mechanism operated that necessarily suppresses the metric."""

    COINCIDES_WITH = "coincides_with"
    """The factor moved alongside the metric. Nothing more is claimed."""


class Dimension(StrEnum):
    """The nine investigated dimensions."""

    REGION = "region"
    STORE = "store"
    SEGMENT = "segment"
    PRODUCT = "product"
    PROMOTION = "promotion"
    INVENTORY = "inventory"
    RETURNS = "returns"
    SHIPPING = "shipping"
    WEATHER = "weather"


@dataclass(frozen=True, slots=True)
class Evidence:
    """One number a reader can check, with what it was compared against."""

    label: str
    value: float
    baseline: float | None = None
    unit: str = ""
    note: str = ""

    @property
    def change(self) -> float | None:
        if self.baseline is None:
            return None
        return self.value - self.baseline

    @property
    def relative_change(self) -> float | None:
        if not self.baseline:
            return None
        return (self.value - self.baseline) / abs(self.baseline)

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "value": round(self.value, 4),
            "baseline": round(self.baseline, 4) if self.baseline is not None else None,
            "change": round(self.change, 4) if self.change is not None else None,
            "relative_change": (
                round(self.relative_change, 4) if self.relative_change is not None else None
            ),
            "unit": self.unit,
            "note": self.note,
        }


@dataclass(frozen=True, slots=True)
class Recommendation:
    """An action, with the assumption it rests on stated alongside it."""

    action: str
    rationale: str
    assumes: str
    """What must be true for this to help. A recommendation whose assumption
    is false is worse than none, and the reader is the one who can check."""

    owner: str = ""
    urgency: str = "normal"

    def as_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "rationale": self.rationale,
            "assumes": self.assumes,
            "owner": self.owner,
            "urgency": self.urgency,
        }


@dataclass(frozen=True, slots=True)
class Finding:
    """One candidate explanation, with its evidence and its limits."""

    dimension: Dimension
    subject: str
    """What the finding is about — "Northeast", "NORTHBOUND", "Champions"."""

    headline: str
    claim_type: ClaimType
    tier: EvidenceTier
    confidence: float

    impact_amount: float = 0.0
    """Estimated effect on the metric, in the metric's units. Signed: negative
    means this subject pushed the metric down."""

    impact_share: float = 0.0
    """Share of the total measured change this subject accounts for."""

    evidence: tuple[Evidence, ...] = ()
    recommendations: tuple[Recommendation, ...] = ()
    does_not_establish: str = ""
    """The honest limit of this specific finding."""

    def __post_init__(self) -> None:
        ceiling = TIER_CEILING[self.tier]
        if self.confidence > ceiling + 1e-9:
            raise ValueError(
                f"{self.subject}: confidence {self.confidence:.3f} exceeds the "
                f"{self.tier} ceiling of {ceiling}. The ceiling is the point — "
                "a strong correlation is still a correlation."
            )
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"{self.subject}: confidence {self.confidence} outside [0, 1]")

    @property
    def rank_score(self) -> float:
        """Ordering key: confidence weighted by how much is actually at stake.

        Confidence alone would put a certain but trivial finding above an
        uncertain but expensive one, and a reader with an hour should spend it
        on the second.
        """
        return self.confidence * abs(self.impact_share)

    def as_dict(self) -> dict[str, Any]:
        return {
            "dimension": self.dimension.value,
            "subject": self.subject,
            "headline": self.headline,
            "claim_type": self.claim_type.value,
            "evidence_tier": self.tier.value,
            "confidence": round(self.confidence, 4),
            "confidence_ceiling": TIER_CEILING[self.tier],
            "impact_amount": round(self.impact_amount, 2),
            "impact_share": round(self.impact_share, 4),
            "evidence": [item.as_dict() for item in self.evidence],
            "recommendations": [item.as_dict() for item in self.recommendations],
            "does_not_establish": self.does_not_establish,
        }


@dataclass(frozen=True, slots=True)
class Window:
    """A period under investigation."""

    start: date
    end: date

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    def as_dict(self) -> dict[str, Any]:
        return {"start": self.start.isoformat(), "end": self.end.isoformat(), "days": self.days}


@dataclass(frozen=True, slots=True)
class Investigation:
    """The complete answer to "why did this move?"."""

    metric: str
    current: Window
    baseline: Window
    current_value: float
    baseline_value: float
    findings: tuple[Finding, ...]
    dimensions_investigated: tuple[Dimension, ...]
    dimensions_unavailable: dict[str, str] = field(default_factory=dict)
    caveats: tuple[str, ...] = ()
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def change(self) -> float:
        return self.current_value - self.baseline_value

    @property
    def relative_change(self) -> float | None:
        if not self.baseline_value:
            return None
        return self.change / abs(self.baseline_value)

    @property
    def coverage_by_dimension(self) -> dict[str, float]:
        """How much of the change each *cut* accounts for, cut by cut."""
        coverage: dict[str, float] = {}
        for finding in self.findings:
            if finding.tier is not EvidenceTier.ARITHMETIC:
                continue
            key = finding.dimension.value
            coverage[key] = coverage.get(key, 0.0) + abs(finding.impact_share)
        return coverage

    @property
    def explained_share(self) -> float:
        """Best single cut's coverage of the change.

        **Cuts do not add.** Region, store, product, and segment are four
        alternative decompositions of the *same* pounds, so summing their
        shares counts every pound four times and produces figures like 298%
        while each individual number stays correct. The honest headline is how
        much the best single cut explains.

        Mechanical and associative findings are excluded for the same reason
        one layer down: a stockout is an explanation *of* a regional shortfall
        already counted, not an additional shortfall.

        This can still exceed 1.0 legitimately — when some slices fell while
        others grew, the fallers must over-explain a smaller net change — and
        that is a real signal rather than an error.
        """
        coverage = self.coverage_by_dimension
        return max(coverage.values(), default=0.0)
