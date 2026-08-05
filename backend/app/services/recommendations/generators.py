"""The seven recommendation generators.

Each takes rows already fetched through the governed registry and turns them
into proposed actions. They share three rules.

**Nothing is recommended below a materiality floor.** A reorder worth eleven
pounds costs more in a buyer's attention than it returns. An engine that
surfaces everything it can compute produces a queue nobody works through, and
the queue being ignored is indistinguishable from the engine being wrong.

**Every recommendation names its disqualifier.** ``do_not_act_if`` is the
condition under which the advice is wrong, and it exists because the reader is
almost always the only person who can check it — the platform cannot see that
a store is closing next month or that a supplier is already in dispute.

**Risk is sized from reversibility, not from the size of the prize.** A large
upside does not make a permanent markdown safe. Ordering too much leaves stock
that eventually sells; marking down the range gives away margin that never
comes back, and the two must not carry the same badge.
"""

from typing import Any

from app.services.recommendations import estimators
from app.services.recommendations.contracts import (
    BASIS_CEILING,
    Category,
    Evidence,
    ImpactEstimate,
    Recommendation,
    Reversibility,
    RiskProfile,
)

#: Minimum profit effect worth a person's attention.
MIN_PROFIT_IMPACT = 250.0

#: Recommendations produced per category. A ranked list of forty is a report.
TOP_N = 4

#: OTIF below which a supplier is worth acting on rather than watching.
SUPPLIER_ACTION_THRESHOLD = 0.85

#: Days of cover beyond which stock is a liquidation candidate rather than a
#: replenishment one.
DEAD_STOCK_COVER_DAYS = 180.0

#: Churn bands that are not worth a retention contact. "none" and "low" are
#: customers behaving normally, and "unknown" is customers the model could not
#: place — contacting either spends budget to fix a problem that is not there.
NON_ACTIONABLE_RISK_BANDS = frozenset({"none", "low", "unknown", ""})


def _confidence(basis_ceiling: float, *, evidence_strength: float) -> float:
    """Scale the basis ceiling by how strong the supporting evidence is."""
    return round(basis_ceiling * min(1.0, max(0.2, evidence_strength)), 4)


def _f(row: dict[str, Any], key: str, default: float = 0.0) -> float:
    value = row.get(key)
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


# ── 1. Inventory ─────────────────────────────────────────────────────


def inventory_recommendations(
    rows: list[dict[str, Any]], *, margin_rate: float = 0.35
) -> list[Recommendation]:
    """Replenish lines whose unavailability is costing sales.

    Ranked by what the shortage is costing rather than by how far below the
    reorder point a line sits. A staple 10% short loses more than a slow mover
    at zero, and a buyer with an hour should spend it on the first.
    """
    results: list[Recommendation] = []

    for row in rows:
        order_qty = _f(row, "suggested_order_qty")
        demand = _f(row, "daily_demand")
        if order_qty <= 0 or demand <= 0:
            continue

        sku = str(row.get("sku") or row.get("slice_value") or "unknown")
        store = str(row.get("store_id") or "")
        days_short = _f(row, "soonest_stockout_days", 0.0)
        revenue_at_risk = _f(row, "revenue_at_risk")
        if revenue_at_risk <= 0:
            continue

        impact = estimators.availability_recovery(
            revenue_at_risk=revenue_at_risk, margin_rate=margin_rate
        )
        if impact.profit < MIN_PROFIT_IMPACT:
            continue

        results.append(
            Recommendation(
                category=Category.INVENTORY,
                subject=f"{sku} @ {store}" if store else sku,
                action=f"Raise a replenishment order for {order_qty:,.0f} units of {sku}",
                rationale=(
                    f"Inventory position is below the reorder point with "
                    f"{days_short:.0f} days of cover left against demand of "
                    f"{demand:.1f} units a day."
                ),
                impact=impact,
                risk=RiskProfile(
                    reversibility=Reversibility.REVERSIBLE,
                    # Ordering stock that turns out unnecessary costs its
                    # carrying charge, not its value: the units still sell.
                    downside_profit=-(
                        revenue_at_risk
                        * estimators.CARRYING_COST_RATE
                        * (estimators.DEFAULT_HORIZON_DAYS / 365.0)
                    ),
                    blast_radius=f"one SKU at {store or 'one store'}",
                    principal_risk=(
                        "If demand was overstated the units still sell, just "
                        "later — the cost is carrying charge, not write-off."
                    ),
                ),
                confidence=_confidence(
                    BASIS_CEILING[impact.basis],
                    evidence_strength=min(1.0, demand / 5.0),
                ),
                scope=frozenset({f"sku:{sku}", f"store:{store}"} - {"store:"}),
                evidence=(
                    Evidence("Suggested order", order_qty, "units"),
                    Evidence("Daily demand", demand, "units/day"),
                    Evidence("Days of cover left", days_short, "days"),
                    Evidence("Revenue at risk", _f(row, "revenue_at_risk"), "currency"),
                ),
                owner="inventory",
                urgency="high" if days_short <= 3 else "normal",
                effort="low",
                do_not_act_if=(
                    "The line is being discontinued, or a delivery is already "
                    "in transit that the position feed has not yet recorded."
                ),
            )
        )

    return sorted(results, key=lambda r: r.risk_adjusted_profit, reverse=True)[:TOP_N]


# ── 2. Pricing ───────────────────────────────────────────────────────


def pricing_recommendations(rows: list[dict[str, Any]]) -> list[Recommendation]:
    """Clear stock that will not sell through, and stop margin erosion.

    Two different actions with opposite risk profiles. A markdown is
    irreversible — the margin is gone the moment it is taken — while a
    discount-discipline change can be undone next week. They are generated
    together because they are the same lever pulled in opposite directions,
    and a merchant should see both.
    """
    results: list[Recommendation] = []

    for row in rows:
        excess_units = _f(row, "excess_units")
        excess_value = _f(row, "excess_value")
        cover = _f(row, "cover_days")
        if excess_units <= 0 or excess_value <= 0:
            continue

        sku = str(row.get("sku") or "unknown")
        store = str(row.get("store_id") or "")
        unit_cost = excess_value / max(excess_units, 1.0)

        # Depth scales with how hopeless the position is. Stock with three
        # years of cover does not clear at 20% off.
        depth = 0.5 if cover >= DEAD_STOCK_COVER_DAYS else 0.3
        impact = estimators.liquidation(
            excess_units=excess_units, unit_cost=unit_cost, markdown_depth=depth
        )
        if impact.capital_freed < MIN_PROFIT_IMPACT:
            continue

        results.append(
            Recommendation(
                category=Category.PRICING,
                subject=f"{sku} @ {store}" if store else sku,
                action=f"Mark {excess_units:,.0f} excess units of {sku} down by {depth:.0%}",
                rationale=(
                    f"{cover:,.0f} days of cover against current demand. The "
                    "stock is capital and shelf space, and it is not turning."
                ),
                impact=impact,
                risk=RiskProfile(
                    reversibility=Reversibility.IRREVERSIBLE,
                    downside_profit=-(excess_units * unit_cost * depth),
                    blast_radius=f"one SKU at {store or 'one store'}",
                    principal_risk=(
                        "The margin is given away the moment the markdown is "
                        "taken. If the stock would have cleared at full price — "
                        "on a seasonal upturn, say — the discount is pure loss."
                    ),
                ),
                confidence=_confidence(
                    BASIS_CEILING[impact.basis],
                    evidence_strength=min(1.0, cover / DEAD_STOCK_COVER_DAYS),
                ),
                scope=frozenset({f"sku:{sku}", f"store:{store}"} - {"store:"}),
                evidence=(
                    Evidence("Excess units", excess_units, "units"),
                    Evidence("Capital tied up", excess_value, "currency"),
                    Evidence("Days of cover", cover, "days"),
                ),
                owner="merchandising",
                urgency="normal",
                effort="medium",
                do_not_act_if=(
                    "The line is seasonal and its season is approaching, or the "
                    "excess is a deliberate forward buy."
                ),
            )
        )

    return sorted(results, key=lambda r: r.impact.capital_freed, reverse=True)[:TOP_N]


def margin_recommendations(rows: list[dict[str, Any]]) -> list[Recommendation]:
    """Categories where discounting has outrun the margin it was meant to buy."""
    results: list[Recommendation] = []

    for row in rows:
        revenue = _f(row, "net_revenue")
        margin_rate = _f(row, "margin_rate")
        discount_rate = _f(row, "discount_rate")
        if revenue <= 0 or discount_rate < 0.12:
            continue

        category = str(row.get("category") or row.get("slice_value") or "unknown")
        # A modest, testable move rather than a heroic one.
        impact = estimators.price_change(
            current_revenue=revenue, margin_rate=margin_rate, price_delta=0.02
        )
        if impact.profit < MIN_PROFIT_IMPACT:
            continue

        results.append(
            Recommendation(
                category=Category.PRICING,
                subject=category,
                action=f"Test a 2% price increase on {category} against a control group",
                rationale=(
                    f"Discount depth is running at {discount_rate:.1%} while "
                    f"margin sits at {margin_rate:.1%}. The category may be "
                    "buying volume it would have had anyway."
                ),
                impact=impact,
                risk=RiskProfile(
                    reversibility=Reversibility.REVERSIBLE,
                    downside_profit=impact.pessimistic_profit or 0.0,
                    blast_radius=f"the {category} category, estate-wide",
                    principal_risk=(
                        "Elasticity is assumed, not measured. At the pessimistic "
                        "end of the range this move loses money, which is why "
                        "the action is a test rather than a rollout."
                    ),
                ),
                confidence=_confidence(BASIS_CEILING[impact.basis], evidence_strength=0.6),
                scope=frozenset({f"category:{category}"}),
                evidence=(
                    Evidence("Discount rate", discount_rate, "rate"),
                    Evidence("Margin rate", margin_rate, "rate"),
                    Evidence("Category revenue", revenue, "currency"),
                ),
                owner="merchandising",
                urgency="low",
                effort="high",
                do_not_act_if=(
                    "The discounting is contractual, or the category is a "
                    "known traffic driver whose margin is made elsewhere."
                ),
            )
        )

    return sorted(results, key=lambda r: r.risk_adjusted_profit, reverse=True)[:TOP_N]


# ── 3. Promotion ─────────────────────────────────────────────────────


def promotion_recommendations(rows: list[dict[str, Any]]) -> list[Recommendation]:
    """End campaigns whose subsidy outruns the margin they generate."""
    results: list[Recommendation] = []

    for row in rows:
        promo_revenue = _f(row, "promo_revenue")
        promo_margin = _f(row, "promo_margin")
        subsidy = _f(row, "subsidy_amount")
        if promo_revenue <= 0 or subsidy <= 0:
            continue
        if subsidy <= promo_margin:
            continue  # the campaign pays for itself

        code = str(row.get("promo_code") or "unknown")
        impact = estimators.promotion_withdrawal(
            promo_revenue=promo_revenue, promo_margin=promo_margin, subsidy=subsidy
        )
        if impact.profit < MIN_PROFIT_IMPACT:
            continue

        results.append(
            Recommendation(
                category=Category.PROMOTION,
                subject=code,
                action=f"End or reprice campaign {code}",
                rationale=(
                    f"Subsidy of {subsidy:,.0f} against margin of "
                    f"{promo_margin:,.0f}: the campaign costs more than the "
                    "margin it produces."
                ),
                impact=impact,
                risk=RiskProfile(
                    reversibility=Reversibility.REVERSIBLE,
                    downside_profit=impact.pessimistic_profit or 0.0,
                    blast_radius="one campaign, estate-wide",
                    principal_risk=(
                        "Incrementality is assumed. If most promoted sales "
                        "would not have happened otherwise, ending the campaign "
                        "removes revenue the subsidy was legitimately buying."
                    ),
                ),
                confidence=_confidence(BASIS_CEILING[impact.basis], evidence_strength=0.7),
                scope=frozenset({f"promo:{code}"}),
                evidence=(
                    Evidence("Promotional revenue", promo_revenue, "currency"),
                    Evidence("Promotional margin", promo_margin, "currency"),
                    Evidence("Subsidy", subsidy, "currency"),
                    Evidence("Effective depth", _f(row, "effective_depth"), "rate"),
                ),
                owner="marketing",
                urgency="normal",
                effort="low",
                do_not_act_if=(
                    "The campaign is a contractual supplier-funded activity, or "
                    "it exists to defend share rather than to make margin."
                ),
            )
        )

    return sorted(results, key=lambda r: r.risk_adjusted_profit, reverse=True)[:TOP_N]


# ── 4. Store ─────────────────────────────────────────────────────────


def store_recommendations(
    rows: list[dict[str, Any]], *, peer_median_revenue: float
) -> list[Recommendation]:
    """Stores trading materially below their peers."""
    results: list[Recommendation] = []

    for row in rows:
        revenue = _f(row, "net_revenue")
        if revenue <= 0 or peer_median_revenue <= 0:
            continue

        shortfall = peer_median_revenue - revenue
        if shortfall <= 0 or shortfall / peer_median_revenue < 0.15:
            continue

        store = str(row.get("store_id") or row.get("slice_value") or "unknown")
        impact = estimators.store_recovery(
            shortfall=shortfall, margin_rate=_f(row, "margin_rate", 0.35)
        )
        if impact.profit < MIN_PROFIT_IMPACT:
            continue

        results.append(
            Recommendation(
                category=Category.STORE,
                subject=store,
                action=f"Review trading conditions and execution at {store}",
                rationale=(
                    f"Trading {shortfall / peer_median_revenue:.0%} below the "
                    "median comparable store over the same period."
                ),
                impact=impact,
                risk=RiskProfile(
                    reversibility=Reversibility.REVERSIBLE,
                    downside_profit=0.0,
                    blast_radius="one store",
                    principal_risk=(
                        "Some of a store's gap is its catchment, and no amount "
                        "of operational attention makes a smaller town bigger. "
                        "The recoverable share is assumed, not measured."
                    ),
                ),
                confidence=_confidence(
                    BASIS_CEILING[impact.basis],
                    evidence_strength=min(1.0, shortfall / peer_median_revenue * 3),
                ),
                scope=frozenset({f"store:{store}"}),
                evidence=(
                    Evidence("Store revenue", revenue, "currency"),
                    Evidence("Peer median", peer_median_revenue, "currency"),
                    Evidence("Shortfall", shortfall, "currency"),
                ),
                owner="operations",
                urgency="normal",
                effort="high",
                do_not_act_if=(
                    "The store is newly opened, under refit, or serves a "
                    "catchment not comparable to the peer set."
                ),
            )
        )

    return sorted(results, key=lambda r: r.risk_adjusted_profit, reverse=True)[:TOP_N]


# ── 5 & 6. Marketing and customer targeting ──────────────────────────


def customer_recommendations(rows: list[dict[str, Any]]) -> list[Recommendation]:
    """Retention against measured value at risk.

    The value is real and measured. The save rate is not, and cannot be
    without a holdout — which is why the second recommendation this engine
    would make about any campaign is to run one.
    """
    results: list[Recommendation] = []

    for row in rows:
        value_at_risk = _f(row, "value_at_risk")
        customers = int(_f(row, "customers"))
        if value_at_risk <= 0 or customers <= 0:
            continue

        # The registry dimension is `risk_band`; the column it resolves to is
        # `churn_risk_band`. Reading the dimension key returns None, and a
        # default here silently turned "no churn risk" into "at risk" — the
        # engine's top recommendation became a campaign to the customers with
        # nothing wrong with them.
        band = str(row.get("churn_risk_band") or row.get("risk_band") or "").lower()
        if band in NON_ACTIONABLE_RISK_BANDS:
            continue

        vip_value = _f(row, "vip_value_at_risk")
        impact = estimators.retention_campaign(value_at_risk=value_at_risk, customers=customers)
        if impact.profit < MIN_PROFIT_IMPACT:
            continue

        is_vip_heavy = vip_value > value_at_risk * 0.3
        category = Category.CUSTOMER if is_vip_heavy else Category.MARKETING

        results.append(
            Recommendation(
                category=category,
                subject=f"{band} churn risk",
                action=(
                    f"Run a targeted retention contact to {customers:,} "
                    f"customers at {band} churn risk"
                ),
                rationale=(
                    f"{value_at_risk:,.0f} of lifetime value sits in this band"
                    + (
                        f", of which {vip_value:,.0f} belongs to VIPs — "
                        "expensive to replace and still reachable."
                        if is_vip_heavy
                        else "."
                    )
                ),
                impact=impact,
                risk=RiskProfile(
                    reversibility=Reversibility.COSTLY,
                    downside_profit=-(customers * 4.0),
                    blast_radius=f"{customers:,} customers",
                    principal_risk=(
                        "The save rate is a placeholder. The contact cost is "
                        "spent whether or not anyone is retained, so the "
                        "downside is the campaign budget in full."
                    ),
                ),
                confidence=_confidence(
                    BASIS_CEILING[impact.basis],
                    evidence_strength=min(1.0, customers / 500.0),
                ),
                scope=frozenset({f"segment:{band}"}),
                evidence=(
                    Evidence("Value at risk", value_at_risk, "currency"),
                    Evidence("Customers", float(customers), "count"),
                    Evidence("VIP value at risk", vip_value, "currency"),
                    Evidence("Contact cost", customers * 4.0, "currency"),
                ),
                owner="marketing",
                urgency="high" if is_vip_heavy else "normal",
                effort="medium",
                do_not_act_if=(
                    "These customers were contacted recently, or the band is "
                    "dominated by one-time buyers who were never going to return."
                ),
            )
        )

    return sorted(results, key=lambda r: r.risk_adjusted_profit, reverse=True)[:TOP_N]


# ── 7. Supplier ──────────────────────────────────────────────────────


def supplier_recommendations(rows: list[dict[str, Any]]) -> list[Recommendation]:
    """Suppliers whose unreliability is being paid for in safety stock.

    The prize is working capital, not margin. An erratic supplier forces a
    buffer sized against its worst behaviour, and that buffer is cash sitting
    in a warehouse — which is why the estimate is expressed as capital freed
    rather than as profit earned.
    """
    results: list[Recommendation] = []

    for row in rows:
        otif = _f(row, "otif_rate")
        closed = _f(row, "closed_lines")
        lead_time = _f(row, "avg_lead_time_days")
        if closed < 20 or otif >= SUPPLIER_ACTION_THRESHOLD or lead_time <= 0:
            continue

        supplier = str(row.get("supplier_name") or row.get("supplier_id") or "unknown")
        ordered_value = _f(row, "ordered_value")
        # A supplier meeting its contract would deliver at the contracted time.
        contracted = _f(row, "contract_lead_time_days", lead_time * 0.8) or lead_time * 0.8

        impact = estimators.safety_stock_release(
            current_safety_stock=ordered_value / max(lead_time, 1.0),
            lead_time_days=lead_time,
            improved_lead_time_days=min(contracted, lead_time),
            unit_cost=1.0,
        )
        if impact.capital_freed < MIN_PROFIT_IMPACT:
            continue

        results.append(
            Recommendation(
                category=Category.SUPPLIER,
                subject=supplier,
                action=(
                    f"Put {supplier} on a performance plan or reallocate volume "
                    "to a more reliable carrier of the same lines"
                ),
                rationale=(
                    f"OTIF of {otif:.0%} across {closed:,.0f} received lines, "
                    f"averaging {lead_time:.1f} days against a contracted "
                    f"{contracted:.1f}. The gap is being funded with safety stock."
                ),
                impact=impact,
                risk=RiskProfile(
                    reversibility=Reversibility.COSTLY,
                    downside_profit=-(ordered_value * 0.02),
                    blast_radius=f"{ordered_value:,.0f} of committed spend",
                    principal_risk=(
                        "Moving volume mid-season risks a worse disruption than "
                        "the one being fixed. A performance plan is the "
                        "reversible half of this recommendation and should come "
                        "first."
                    ),
                ),
                confidence=_confidence(
                    BASIS_CEILING[impact.basis],
                    evidence_strength=min(1.0, closed / 200.0),
                ),
                scope=frozenset({f"supplier:{supplier}"}),
                evidence=(
                    Evidence("OTIF", otif, "rate"),
                    Evidence("Received lines", closed, "count"),
                    Evidence("Average lead time", lead_time, "days"),
                    Evidence("Committed spend", ordered_value, "currency"),
                ),
                owner="inventory",
                urgency="normal",
                effort="high",
                do_not_act_if=(
                    "This supplier is the sole source for the lines concerned, "
                    "or the lateness is a known one-off rather than a pattern."
                ),
            )
        )

    return sorted(results, key=lambda r: r.impact.capital_freed, reverse=True)[:TOP_N]


def has_material_impact(impact: ImpactEstimate) -> bool:
    return impact.profit >= MIN_PROFIT_IMPACT or impact.capital_freed >= MIN_PROFIT_IMPACT
