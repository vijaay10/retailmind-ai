"""What a recommendation is, and what its numbers are allowed to mean.

The hard problem in this module is not generating advice. It is putting a
pound figure on advice, because an expected revenue impact is a *causal*
claim — "if you do this, that will happen" — and most of the interventions
here have no measured causal model behind them anywhere in this platform.

Raising a price by 5% moves profit by an amount that depends on price
elasticity. Nobody here has measured elasticity. Running a campaign recovers
some share of at-risk customer value; nobody has measured the save rate. A
system that prints "+£142,000" for either of those has invented a number and
dressed it as arithmetic, and the first time a merchant checks it the whole
platform loses its credibility.

So every estimate declares its **basis**, and the basis caps the confidence:

``MEASURED``
    Arithmetic over observed data with no behavioural assumption. Liquidating
    dead stock frees exactly its carrying value; that is subtraction, not a
    forecast.

``MODELLED``
    Uses a forecast or a documented model, but no unmeasured human response.
    Sales recovered by fixing a stockout are the forecast demand for the days
    the shelf would otherwise be empty.

``ASSUMED``
    Requires a behavioural parameter this platform has never measured —
    elasticity, promotional incrementality, campaign response. The estimate is
    only as good as the assumption, so the assumption is named in the
    response, and a sensitivity range travels beside the point estimate.

The second thing every recommendation carries is a **downside**. An engine
that reports only upside is a sales pitch. Actions differ enormously in what
happens when they are wrong: a reorder that turns out unnecessary leaves stock
that eventually sells, while a 40% markdown destroys margin that cannot be
recovered. Reversibility is therefore a first-class field, not a footnote.
"""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class Category(StrEnum):
    """The seven kinds of action this engine proposes."""

    INVENTORY = "inventory"
    PRICING = "pricing"
    PROMOTION = "promotion"
    STORE = "store"
    MARKETING = "marketing"
    CUSTOMER = "customer"
    SUPPLIER = "supplier"


class EstimateBasis(StrEnum):
    MEASURED = "measured"
    MODELLED = "modelled"
    ASSUMED = "assumed"


#: The most confidence an estimate may express, given how it was derived.
#:
#: Nothing reaches 1.0, including MEASURED. Even exact arithmetic about the
#: capital tied up in dead stock assumes the stock can actually be sold at the
#: assumed price, and that the recommendation is executed at all.
BASIS_CEILING: dict[EstimateBasis, float] = {
    EstimateBasis.MEASURED: 0.90,
    EstimateBasis.MODELLED: 0.70,
    EstimateBasis.ASSUMED: 0.45,
}


class Reversibility(StrEnum):
    """How expensive it is to undo, if the recommendation turns out wrong."""

    REVERSIBLE = "reversible"
    """Undone at little cost. An order can be cancelled; a campaign stopped."""

    COSTLY = "costly_to_reverse"
    """Undoable, but the money spent getting there is gone."""

    IRREVERSIBLE = "irreversible"
    """Cannot be undone. A markdown taken is margin permanently given away."""


@dataclass(frozen=True, slots=True)
class Assumption:
    """A parameter the estimate rests on, and where the value came from."""

    name: str
    value: float
    source: str
    """``measured`` | ``industry default`` | ``placeholder``. A placeholder is
    a number chosen so the arithmetic runs, and saying so is the difference
    between an estimate and a fabrication."""

    unit: str = ""

    @property
    def is_evidenced(self) -> bool:
        return self.source == "measured"

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "value": round(self.value, 6),
            "source": self.source,
            "unit": self.unit,
            "is_evidenced": self.is_evidenced,
        }


@dataclass(frozen=True, slots=True)
class ImpactEstimate:
    """What this action is expected to be worth, and how that was worked out."""

    revenue: float
    profit: float
    basis: EstimateBasis
    horizon_days: int
    method: str
    """How the figure was derived, in a sentence a merchant can argue with."""

    assumptions: tuple[Assumption, ...] = ()
    pessimistic_profit: float | None = None
    optimistic_profit: float | None = None
    capital_freed: float = 0.0
    """Working capital released. Kept apart from profit deliberately: freeing
    cash from dead stock is valuable and is *not* margin, and adding the two
    would let a liquidation programme look profitable."""

    @property
    def rests_on_unmeasured_assumptions(self) -> bool:
        return any(not assumption.is_evidenced for assumption in self.assumptions)

    @property
    def spread(self) -> float | None:
        """How far the estimate moves across its sensitivity range."""
        if self.pessimistic_profit is None or self.optimistic_profit is None:
            return None
        return self.optimistic_profit - self.pessimistic_profit

    def as_dict(self) -> dict[str, Any]:
        return {
            "revenue": round(self.revenue, 2),
            "profit": round(self.profit, 2),
            "capital_freed": round(self.capital_freed, 2),
            "basis": self.basis.value,
            "horizon_days": self.horizon_days,
            "method": self.method,
            "assumptions": [item.as_dict() for item in self.assumptions],
            "pessimistic_profit": (
                round(self.pessimistic_profit, 2) if self.pessimistic_profit is not None else None
            ),
            "optimistic_profit": (
                round(self.optimistic_profit, 2) if self.optimistic_profit is not None else None
            ),
            "rests_on_unmeasured_assumptions": self.rests_on_unmeasured_assumptions,
        }


@dataclass(frozen=True, slots=True)
class RiskProfile:
    """What happens if this is wrong."""

    reversibility: Reversibility
    downside_profit: float
    """Profit lost if the action is taken and the reasoning does not hold.
    Signed negative."""

    blast_radius: str
    """What is exposed — one SKU at one store, or the whole estate."""

    principal_risk: str
    """The single sentence a reader needs before approving."""

    @property
    def band(self) -> str:
        """Low, medium, or high.

        Irreversibility dominates. A large upside does not make a permanent
        margin give-away safe, and the asymmetry between "we ordered too much"
        and "we marked down the whole range" is the thing a band has to
        capture.
        """
        if self.reversibility is Reversibility.IRREVERSIBLE:
            return "high" if abs(self.downside_profit) > 0 else "medium"
        if self.reversibility is Reversibility.COSTLY:
            return "medium"
        return "low"

    def as_dict(self) -> dict[str, Any]:
        return {
            "band": self.band,
            "reversibility": self.reversibility.value,
            "downside_profit": round(self.downside_profit, 2),
            "blast_radius": self.blast_radius,
            "principal_risk": self.principal_risk,
        }


@dataclass(frozen=True, slots=True)
class Evidence:
    """An observed number supporting the recommendation."""

    label: str
    value: float
    unit: str = ""
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "value": round(self.value, 4),
            "unit": self.unit,
            "note": self.note,
        }


@dataclass(frozen=True, slots=True)
class Recommendation:
    """One proposed action, with its value, its risk, and its disqualifier."""

    category: Category
    subject: str
    action: str
    rationale: str
    impact: ImpactEstimate
    risk: RiskProfile
    confidence: float

    scope: frozenset[str] = frozenset()
    """Keys this action operates on — ``sku:AC-1010``, ``region:Northeast``.
    Used to detect recommendations whose value overlaps, so a portfolio total
    does not count the same pounds twice."""

    evidence: tuple[Evidence, ...] = ()
    owner: str = ""
    urgency: str = "normal"
    effort: str = "medium"
    do_not_act_if: str = ""
    """The condition that disqualifies this recommendation. Stated because the
    reader is usually the only one who can check it."""

    def __post_init__(self) -> None:
        ceiling = BASIS_CEILING[self.impact.basis]
        if self.confidence > ceiling + 1e-9:
            raise ValueError(
                f"{self.subject}: confidence {self.confidence:.3f} exceeds the "
                f"{self.impact.basis} ceiling of {ceiling}. An estimate cannot "
                "be more certain than the weakest input it rests on."
            )

    @property
    def risk_adjusted_profit(self) -> float:
        """Expected profit net of what is lost when the reasoning fails.

        A plain expected value: the upside weighted by confidence, less the
        downside weighted by the chance the recommendation is wrong. Ranking on
        raw upside would put an irreversible, assumption-heavy markdown above a
        certain, reversible reorder worth almost as much — which is exactly the
        trade a recommendation engine exists to get right.
        """
        return self.impact.profit * self.confidence + self.risk.downside_profit * (
            1.0 - self.confidence
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "category": self.category.value,
            "subject": self.subject,
            "action": self.action,
            "rationale": self.rationale,
            "confidence": round(self.confidence, 4),
            "confidence_ceiling": BASIS_CEILING[self.impact.basis],
            "impact": self.impact.as_dict(),
            "risk": self.risk.as_dict(),
            "risk_adjusted_profit": round(self.risk_adjusted_profit, 2),
            "evidence": [item.as_dict() for item in self.evidence],
            "scope": sorted(self.scope),
            "owner": self.owner,
            "urgency": self.urgency,
            "effort": self.effort,
            "do_not_act_if": self.do_not_act_if,
        }


@dataclass(frozen=True, slots=True)
class Portfolio:
    """A ranked set of recommendations, with honest totals."""

    recommendations: tuple[Recommendation, ...]
    categories_requested: tuple[Category, ...]
    categories_empty: dict[str, str] = field(default_factory=dict)
    caveats: tuple[str, ...] = ()
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def gross_profit_opportunity(self) -> float:
        """Naive sum. Reported *only* alongside the deduplicated figure."""
        return sum(item.impact.profit for item in self.recommendations)

    @property
    def net_profit_opportunity(self) -> float:
        """Sum with overlapping recommendations counted once.

        Two actions touching the same SKU-store often chase the same pounds:
        reordering a line and fixing the supplier that made it late both
        recover the sales the stockout is preventing. Adding them promises the
        money twice, and a merchant who acts on both and banks the sum will
        find the shortfall at quarter end.

        Where scopes overlap, only the largest estimate counts. Conservative
        rather than exact — the true joint effect needs a model of how the
        actions interact, which nobody has — and conservative is the right
        direction for a number someone will budget against.
        """
        claimed: set[str] = set()
        total = 0.0
        for item in sorted(self.recommendations, key=lambda r: r.impact.profit, reverse=True):
            if item.scope and item.scope & claimed:
                continue
            claimed |= item.scope
            total += item.impact.profit
        return total

    @property
    def capital_freed(self) -> float:
        return sum(item.impact.capital_freed for item in self.recommendations)
