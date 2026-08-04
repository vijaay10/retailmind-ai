"""The five forecast targets, and which of them are actually modelled.

Only three are. The other two are **derived**, and that is a modelling
decision rather than a shortcut.

**Revenue, units, and demand are fitted.** They are observed time series with
their own dynamics, and a model is the right instrument.

**Inventory is projected, not fitted.** Closing stock is an accounting
identity — ``closing = opening − demand + receipts`` — and fitting a time
series to on-hand throws that structure away. A fitted stock model will
happily forecast negative inventory, or forecast stock rising on a day with no
delivery scheduled, because nothing in a regression knows that stock only
arrives when a purchase order lands. Projecting through the identity, driven
by the demand forecast and the *known* open-order schedule, cannot produce
either, and it inherits the demand model's uncertainty honestly.

**Profit is decomposed, not fitted.** Margin rate is far more stable than
margin amount: it moves with mix and markdown, in a band of a few points,
while the amount inherits all of revenue's volatility. Forecasting the stable
component and multiplying gives a materially lower-variance estimate than
fitting the noisy product directly.

The caveat is real and stated in the output: ``E[revenue × rate]`` equals
``E[revenue] × E[rate]`` only when the two are independent, and they are not
— heavy discounting drives volume up and rate down. The decomposition is still
the better estimator here, but it carries a small negative bias in promotional
periods, and a reader deciding on a markdown should know that.
"""

from dataclasses import dataclass
from enum import StrEnum

from forecasting.series import GapPolicy


class TargetKind(StrEnum):
    FITTED = "fitted"
    """A model is trained on the observed series."""

    DERIVED = "derived"
    """Computed from other forecasts through a known relationship, so it
    cannot contradict them or violate an identity they must satisfy."""


@dataclass(frozen=True, slots=True)
class TargetSpec:
    """How one forecast target is produced."""

    key: str
    label: str
    kind: TargetKind
    unit: str
    description: str
    gap_policy: GapPolicy = GapPolicy.ERROR
    source_column: str = ""
    derived_from: tuple[str, ...] = ()
    caveat: str = ""


REVENUE = TargetSpec(
    key="revenue",
    label="Net Revenue",
    kind=TargetKind.FITTED,
    unit="currency",
    source_column="net_revenue",
    gap_policy=GapPolicy.ERROR,
    description=(
        "Daily net revenue for the network. Read from the same governed KPI "
        "view the dashboard reports, so the forecast targets the metric the "
        "business actually watches rather than a parallel definition that "
        "drifts from it."
    ),
)

SALES = TargetSpec(
    key="sales",
    label="Units Sold",
    kind=TargetKind.FITTED,
    unit="units",
    source_column="units_sold",
    gap_policy=GapPolicy.ERROR,
    description=(
        "Daily unit volume. Forecast separately from revenue rather than "
        "divided out of it: price and mix move independently of volume, and a "
        "revenue forecast divided by an assumed price hides which one changed."
    ),
)

DEMAND = TargetSpec(
    key="demand",
    label="SKU Demand",
    kind=TargetKind.FITTED,
    unit="units",
    source_column="units_sold",
    gap_policy=GapPolicy.ZERO,
    description=(
        "Units per SKU × store per day — the series replenishment actually "
        "consumes. A day with no sales line means nobody bought it, so gaps "
        "fill with zero; dropping them would make an item selling twice a "
        "month look like a daily seller."
    ),
    caveat=(
        "Demand is censored by availability: a day that was out of stock "
        "records zero demand, not zero want. The forecast therefore "
        "under-states true demand for lines that stock out often, which is "
        "precisely the population replenishment cares most about."
    ),
)

MARGIN_RATE = TargetSpec(
    key="margin_rate",
    label="Margin Rate",
    kind=TargetKind.FITTED,
    unit="rate",
    source_column="margin_rate",
    gap_policy=GapPolicy.ERROR,
    description=(
        "Daily gross margin as a share of revenue. Fitted in its own right "
        "because it is the stable half of the profit decomposition — it moves "
        "a few points with mix and markdown while revenue swings by tens of "
        "percent, so it is a far easier series to forecast well."
    ),
)

INVENTORY = TargetSpec(
    key="inventory",
    label="Projected On Hand",
    kind=TargetKind.DERIVED,
    unit="units",
    derived_from=("demand",),
    description=(
        "Projected closing stock, rolled forward through the inventory "
        "identity: closing = opening − forecast demand + scheduled receipts. "
        "Not fitted. A regression on on-hand can forecast negative stock, or "
        "stock rising on a day with no delivery due, because nothing in it "
        "knows that inventory only arrives when a purchase order lands."
    ),
)

PROFIT = TargetSpec(
    key="profit",
    label="Gross Margin",
    kind=TargetKind.DERIVED,
    unit="currency",
    derived_from=("revenue",),
    description=(
        "Forecast revenue multiplied by forecast margin rate. Rate is far "
        "more stable than amount — it moves a few points with mix and "
        "markdown, while the amount inherits all of revenue's volatility — so "
        "forecasting the stable component and multiplying beats fitting the "
        "noisy product."
    ),
    caveat=(
        "E[revenue × rate] equals E[revenue] × E[rate] only under "
        "independence, and heavy discounting drives volume up while pushing "
        "rate down. The estimate carries a small negative bias in promotional "
        "periods."
    ),
)

TARGETS: dict[str, TargetSpec] = {
    spec.key: spec for spec in (REVENUE, SALES, DEMAND, MARGIN_RATE, INVENTORY, PROFIT)
}

FITTED_TARGETS: tuple[str, ...] = tuple(
    key for key, spec in TARGETS.items() if spec.kind is TargetKind.FITTED
)


def get_target(key: str) -> TargetSpec:
    spec = TARGETS.get(key)
    if spec is None:
        raise KeyError(f"unknown forecast target '{key}'. Available: {', '.join(sorted(TARGETS))}")
    return spec
