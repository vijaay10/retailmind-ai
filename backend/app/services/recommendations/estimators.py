"""Impact arithmetic. Pure functions, no I/O, one job each.

Every estimator returns an :class:`ImpactEstimate` carrying the basis it was
computed on and the assumptions it rests on. The separation matters: two
estimates of "+£40,000" are not comparable when one is the carrying value of
stock sitting in a warehouse and the other is a guess about how customers
respond to a price rise.

Where a behavioural parameter is needed and none has been measured, the value
used is declared as a **placeholder** rather than dressed up as a default, and
a sensitivity range travels with the point estimate. A merchant who disagrees
with the assumption can then see immediately whether the disagreement changes
the decision — which is usually the only question that matters.
"""

from app.services.recommendations.contracts import (
    Assumption,
    EstimateBasis,
    ImpactEstimate,
)

#: Days of trading a recommendation's impact is estimated over. Long enough to
#: matter, short enough that the underlying demand estimate still holds — a
#: quarter-long projection from a fortnight's demand history is arithmetic
#: applied to a guess.
DEFAULT_HORIZON_DAYS = 30

#: Share of demand that walks out unfulfilled when an item is unavailable.
#: The rest substitutes to another size, colour, or brand. Genuinely unmeasured
#: here — measuring it needs basket analysis across stockout events — so it is
#: a placeholder, and it is the single largest lever on every availability
#: estimate in this module.
LOST_SALE_RATE = 0.55

#: Share of at-risk customer value a retention campaign recovers. Unmeasured,
#: and unmeasurable without a holdout group.
CAMPAIGN_SAVE_RATE = 0.15

#: Years over which a customer's observed lifetime value is assumed to have
#: accrued. Used to convert historic value into a forward *rate*, which is the
#: only part a retention campaign can actually recover.
ASSUMED_TENURE_YEARS = 1.0

#: Share of promotional revenue that would not have happened without the
#: promotion. Unmeasured; the same placeholder the RCA engine uses, kept
#: identical on purpose so the two never disagree about the same campaign.
PROMOTIONAL_INCREMENTALITY = 0.5

#: Price elasticity of demand. A single estate-wide figure is a crude stand-in
#: for something that varies by category, brand, and season — which is exactly
#: why pricing estimates ship with a sensitivity range rather than a number.
ASSUMED_ELASTICITY = -1.4

#: Annual cost of holding stock: capital, space, shrink, obsolescence.
#: An industry convention rather than a measurement of this business.
CARRYING_COST_RATE = 0.25


def availability_recovery(
    *,
    revenue_at_risk: float,
    margin_rate: float,
    horizon_days: int = DEFAULT_HORIZON_DAYS,
) -> ImpactEstimate:
    """Sales recovered by making an out-of-stock line available again.

    Takes the warehouse's own ``revenue_at_risk`` rather than rebuilding it
    from price and units here. That figure is already the governed answer to
    "what will this shortage cost", computed from forecast demand and the
    replenishment lead time, and recomputing it in the API would create a
    second definition that agrees today and drifts the first time either input
    changes — surfacing as a recommendation that contradicts the inventory
    dashboard it came from.

    Only a share of unmet demand is a genuinely lost sale. The rest
    substitutes: the customer buys the other size, the other colour, or the
    competitor's. Counting all of it as recoverable is how availability
    programmes get approved on numbers they cannot deliver.
    """
    recoverable = revenue_at_risk * LOST_SALE_RATE
    profit = recoverable * margin_rate

    def at(rate: float) -> float:
        return revenue_at_risk * rate * margin_rate

    return ImpactEstimate(
        revenue=recoverable,
        profit=profit,
        basis=EstimateBasis.MODELLED,
        horizon_days=horizon_days,
        method=("warehouse revenue-at-risk × lost-sale rate, valued at the category margin"),
        assumptions=(
            Assumption("lost_sale_rate", LOST_SALE_RATE, "placeholder", "ratio"),
            Assumption("revenue_at_risk", revenue_at_risk, "measured", "currency"),
            Assumption("margin_rate", margin_rate, "measured", "ratio"),
        ),
        # The lost-sale rate is the only unmeasured input and it scales the
        # answer proportionally, so it is what the range is drawn over.
        pessimistic_profit=at(0.25),
        optimistic_profit=at(0.85),
    )


def liquidation(
    *,
    excess_units: float,
    unit_cost: float,
    markdown_depth: float,
    horizon_days: int = DEFAULT_HORIZON_DAYS,
) -> ImpactEstimate:
    """Clearing stock that is not going to sell at full price.

    The honest framing is that this is a **working-capital** action, not a
    profit one, and the return says so by keeping the two apart. Selling dead
    stock at 40% off cost brings in cash and books a loss; a system that nets
    them into one positive number makes liquidation look like a profit centre
    and every markdown look justified.

    Carrying cost avoided is the genuine profit argument: stock nobody buys
    keeps costing money to hold, so clearing it stops a bleed even as it
    crystallises a loss.
    """
    recovered = excess_units * unit_cost * (1.0 - markdown_depth)
    write_down = -(excess_units * unit_cost * markdown_depth)
    carrying_avoided = excess_units * unit_cost * CARRYING_COST_RATE * (horizon_days / 365.0)

    return ImpactEstimate(
        revenue=recovered,
        profit=write_down + carrying_avoided,
        capital_freed=recovered,
        basis=EstimateBasis.MEASURED,
        horizon_days=horizon_days,
        method=(
            "units × cost × (1 − markdown) recovered as cash; the markdown "
            "itself booked as a write-down, offset by carrying cost avoided"
        ),
        assumptions=(
            Assumption("markdown_depth", markdown_depth, "placeholder", "ratio"),
            Assumption("carrying_cost_rate", CARRYING_COST_RATE, "industry default", "annual"),
            Assumption("unit_cost", unit_cost, "measured", "currency"),
        ),
        # If the stock does not clear even at this depth, the write-down
        # happens and the cash does not.
        pessimistic_profit=write_down,
        optimistic_profit=write_down + carrying_avoided * 2,
    )


def price_change(
    *,
    current_revenue: float,
    margin_rate: float,
    price_delta: float,
    elasticity: float = ASSUMED_ELASTICITY,
    horizon_days: int = DEFAULT_HORIZON_DAYS,
) -> ImpactEstimate:
    """Effect of moving price, given an elasticity nobody here has measured.

    The arithmetic is standard: a price move of ``d`` changes volume by
    ``elasticity × d``, so revenue moves by roughly ``d + elasticity × d`` and
    margin moves by more, because the price change lands entirely on the
    margin while the volume change costs only its contribution.

    **The elasticity is the whole estimate.** At −0.8 a price rise is
    profitable; at −2.0 it is destructive; the platform has measured neither.
    So this returns a wide sensitivity range and names the parameter, and the
    recommendation built on it is capped at the ``ASSUMED`` ceiling. The
    correct next action is almost always to test the price on a subset rather
    than to trust this number.
    """
    volume_delta = elasticity * price_delta
    revenue_delta = current_revenue * (price_delta + volume_delta)
    # Contribution margin moves with volume; the price move is pure margin.
    profit_delta = current_revenue * (price_delta + volume_delta * margin_rate)

    def at(assumed: float) -> float:
        return current_revenue * (price_delta + assumed * price_delta * margin_rate)

    return ImpactEstimate(
        revenue=revenue_delta,
        profit=profit_delta,
        basis=EstimateBasis.ASSUMED,
        horizon_days=horizon_days,
        method=(
            "price delta applied to current revenue, with volume responding at "
            "the assumed elasticity; the price move accrues to margin in full"
        ),
        assumptions=(
            Assumption("price_elasticity", elasticity, "placeholder", "ratio"),
            Assumption("price_delta", price_delta, "measured", "ratio"),
            Assumption("margin_rate", margin_rate, "measured", "ratio"),
        ),
        pessimistic_profit=at(-2.2),
        optimistic_profit=at(-0.6),
    )


def promotion_withdrawal(
    *,
    promo_revenue: float,
    promo_margin: float,
    subsidy: float,
    horizon_days: int = DEFAULT_HORIZON_DAYS,
) -> ImpactEstimate:
    """Ending a promotion that costs more in subsidy than it earns.

    Revenue falls — the incremental share of promoted sales goes away — and
    margin improves, because the subsidy stops. Whether the trade is worth it
    turns entirely on how much of the promotional revenue was incremental, and
    that is unmeasured without a holdout group.

    The same placeholder as the RCA engine uses, deliberately: two parts of one
    platform disagreeing about the same campaign's incrementality is worse than
    either being wrong alone.
    """
    lost_revenue = -promo_revenue * PROMOTIONAL_INCREMENTALITY
    lost_margin = -promo_margin * PROMOTIONAL_INCREMENTALITY
    profit = subsidy + lost_margin

    def at(incrementality: float) -> float:
        return subsidy - promo_margin * incrementality

    return ImpactEstimate(
        revenue=lost_revenue,
        profit=profit,
        basis=EstimateBasis.ASSUMED,
        horizon_days=horizon_days,
        method=(
            "subsidy recovered in full, less the margin on the share of "
            "promotional sales assumed to be incremental"
        ),
        assumptions=(
            Assumption(
                "promotional_incrementality", PROMOTIONAL_INCREMENTALITY, "placeholder", "ratio"
            ),
            Assumption("subsidy", subsidy, "measured", "currency"),
        ),
        pessimistic_profit=at(0.9),
        optimistic_profit=at(0.15),
    )


def retention_campaign(
    *,
    value_at_risk: float,
    customers: int,
    contact_cost: float = 4.0,
    horizon_days: int = 90,
) -> ImpactEstimate:
    """Recovering some of the future spend of customers about to leave.

    **The prize is forward spend, not lifetime value.** This is the mistake
    that makes retention business cases indefensible: lifetime value is money
    the customer has *already spent*, and no campaign recovers it. What is at
    stake is the spend that would have happened next, so the observed value is
    converted to an annual rate and prorated to the campaign's horizon before
    any save rate is applied. Skipping that step overstates the prize by
    roughly the ratio of tenure to horizon — here about fourfold, and far more
    for a long-tenured base.

    Two unmeasured parameters remain even after that correction: how much of
    the historic rate would have repeated, and how much of it a campaign
    saves. Neither is knowable without a holdout group, which is why the
    sensitivity range is wide and the basis is ASSUMED.

    Ninety days rather than thirty, because retention only becomes visible
    over a customer's own purchase cycle.
    """
    forward_value = value_at_risk * (horizon_days / 365.0) / ASSUMED_TENURE_YEARS
    recovered = forward_value * CAMPAIGN_SAVE_RATE
    cost = customers * contact_cost

    def at(save_rate: float) -> float:
        return forward_value * save_rate - cost

    return ImpactEstimate(
        revenue=recovered,
        profit=recovered - cost,
        basis=EstimateBasis.ASSUMED,
        horizon_days=horizon_days,
        method=(
            "observed lifetime value converted to an annual rate, prorated to "
            "the campaign horizon, × assumed save rate, less contact cost"
        ),
        assumptions=(
            Assumption("campaign_save_rate", CAMPAIGN_SAVE_RATE, "placeholder", "ratio"),
            Assumption("assumed_tenure_years", ASSUMED_TENURE_YEARS, "placeholder", "years"),
            Assumption("contact_cost", contact_cost, "industry default", "currency/customer"),
            Assumption("value_at_risk", value_at_risk, "measured", "currency"),
        ),
        pessimistic_profit=at(0.03),
        optimistic_profit=at(0.30),
    )


def safety_stock_release(
    *,
    current_safety_stock: float,
    lead_time_days: float,
    improved_lead_time_days: float,
    unit_cost: float,
    horizon_days: int = DEFAULT_HORIZON_DAYS,
) -> ImpactEstimate:
    """Working capital freed by a shorter or steadier supplier lead time.

    Safety stock scales with the square root of lead time, so halving lead
    time releases about 30% of the buffer rather than half of it. Using a
    linear rule here would overstate the prize by a wide margin, and the
    square-root relationship is exactly the part a spreadsheet estimate
    usually gets wrong.

    Modelled rather than measured: the newsvendor relationship is documented
    and the inputs are observed, but the supplier actually delivering the
    improved lead time is an assumption about a negotiation.
    """
    if lead_time_days <= 0 or improved_lead_time_days <= 0:
        released_units = 0.0
    else:
        ratio = (improved_lead_time_days / lead_time_days) ** 0.5
        released_units = max(0.0, current_safety_stock * (1.0 - ratio))

    capital = released_units * unit_cost
    carrying_avoided = capital * CARRYING_COST_RATE * (horizon_days / 365.0)

    return ImpactEstimate(
        revenue=0.0,
        profit=carrying_avoided,
        capital_freed=capital,
        basis=EstimateBasis.MODELLED,
        horizon_days=horizon_days,
        method=(
            "safety stock scales with √lead time; the released buffer is valued "
            "at cost and its carrying cost taken as the profit effect"
        ),
        assumptions=(
            Assumption("improved_lead_time_days", improved_lead_time_days, "placeholder", "days"),
            Assumption("carrying_cost_rate", CARRYING_COST_RATE, "industry default", "annual"),
            Assumption("current_safety_stock", current_safety_stock, "measured", "units"),
        ),
        pessimistic_profit=0.0,
        optimistic_profit=carrying_avoided * 1.5,
    )


def store_recovery(
    *,
    shortfall: float,
    margin_rate: float,
    recoverable_share: float = 0.4,
    horizon_days: int = DEFAULT_HORIZON_DAYS,
) -> ImpactEstimate:
    """Closing part of a store's gap against its peers.

    The shortfall is measured — it comes from the same decomposition the RCA
    engine uses — but how much of it is *recoverable* is not. Some of a store's
    underperformance is its catchment, and no amount of operational attention
    fixes a smaller town.
    """
    recovered = shortfall * recoverable_share

    return ImpactEstimate(
        revenue=recovered,
        profit=recovered * margin_rate,
        basis=EstimateBasis.ASSUMED,
        horizon_days=horizon_days,
        method="measured shortfall against peers × assumed recoverable share",
        assumptions=(
            Assumption("recoverable_share", recoverable_share, "placeholder", "ratio"),
            Assumption("peer_shortfall", shortfall, "measured", "currency"),
        ),
        pessimistic_profit=recovered * margin_rate * 0.25,
        optimistic_profit=recovered * margin_rate * 1.75,
    )
