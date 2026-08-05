"""The nine investigators.

Two families, and the split is the architecture.

**Dimensional investigators** — region, store, product, segment — decompose the
change across a cut of the business. They are exact and they all share one
implementation, because the maths does not care which cut it is given. What
they produce is *where*: which parts of the estate moved, and which moved
differently from the rest.

**Factor investigators** — promotion, inventory, returns, shipping, weather —
line an operational signal up against the movement and ask whether it
coincides. What they produce is a candidate *why*, graded by whether a
mechanism exists (an empty shelf prevents a sale) or merely a correlation
(it rained).

Neither family answers the question alone, and the engine says so. A
decomposition without a factor tells you the Northeast collapsed and not why;
a factor without a decomposition tells you it snowed somewhere and not what it
cost. Ranked together, with the arithmetic and the mechanism kept visibly
apart, they are a briefing rather than a dashboard.
"""

from collections.abc import Sequence
from typing import Any

from app.services.rca.confidence import compose, is_material
from app.services.rca.contracts import (
    ClaimType,
    Dimension,
    Evidence,
    EvidenceTier,
    Finding,
    Recommendation,
)
from app.services.rca.decomposition import (
    Contribution,
    SliceChange,
    contributions,
    normalise_significance,
    volume_rate_split,
)

#: Findings reported per dimensional cut. A decomposition over 40 stores has
#: 40 answers and a reader has patience for a handful; the rest are available
#: by asking about that cut directly.
TOP_N = 3

#: Relative deterioration in an operational factor before it is worth naming.
#: Below this, the factor moved about as much as things move.
FACTOR_SENSITIVITY = 0.10

#: Percentage points of on-time delivery lost before shipping is named.
#: Absolute rather than relative — see investigate_shipping.
ON_TIME_SENSITIVITY_POINTS = 0.03


# ── Dimensional ──────────────────────────────────────────────────────


def investigate_dimension(
    dimension: Dimension,
    *,
    current: dict[str, dict[str, float]],
    baseline: dict[str, dict[str, float]],
    network_current: float,
    network_baseline: float,
    metric: str = "net_revenue",
) -> list[Finding]:
    """Decompose the change across one cut and report the slices that moved.

    Ranked by **excess** share, not gross share. Ranking by gross contribution
    ranks by size: the largest region contributes most of every decline the
    estate ever has, and an engine that reports it is describing the estate's
    shape rather than the incident. Excess asks the useful question — did this
    slice move differently from everything else? — and a slice that fell
    exactly as much as the network appears nowhere, which is correct, because
    it is not the story.
    """
    labels = set(current) | set(baseline)
    changes = [
        SliceChange(
            label=label,
            current=current.get(label, {}).get(metric, 0.0),
            baseline=baseline.get(label, {}).get(metric, 0.0),
            current_orders=current.get(label, {}).get("orders", 0.0),
            baseline_orders=baseline.get(label, {}).get("orders", 0.0),
        )
        for label in labels
    ]
    if not changes:
        return []

    decomposed = contributions(
        changes, network_current=network_current, network_baseline=network_baseline
    )

    # Keep only slices that moved *with* the overall change. A region that
    # grew during a decline is a real finding, but it answers a different
    # question ("what held up?") and mixing it into a list titled "likely
    # causes" sends a reader off to investigate good news.
    #
    # No direction multiplier is needed and applying one inverts the test:
    # excess_share is already excess_change divided by total_change, so a
    # slice falling during a decline divides a negative by a negative and is
    # *positive*. Multiplying by the sign of the change flipped that, and the
    # engine ranked exactly the slices that had held up while dropping the
    # ones that collapsed.
    relevant = [item for item in decomposed if item.excess_share > 0]
    relevant.sort(key=lambda item: abs(item.excess_share), reverse=True)

    findings: list[Finding] = []
    for item in relevant[:TOP_N]:
        if not is_material(item.excess_share):
            continue
        findings.append(_dimensional_finding(dimension, item, changes, metric))

    return findings


def _dimensional_finding(
    dimension: Dimension,
    item: Contribution,
    changes: Sequence[SliceChange],
    metric: str,
) -> Finding:
    source = next((change for change in changes if change.label == item.label), None)
    split = None
    if source is not None and source.baseline_orders:
        split = volume_rate_split(
            current_total=source.current,
            baseline_total=source.baseline,
            current_count=source.current_orders,
            baseline_count=source.baseline_orders,
        )

    evidence = [
        Evidence(
            label=f"{metric} in period",
            value=item.current,
            baseline=item.baseline,
            unit="currency",
        ),
        Evidence(
            label="Share of total change (gross)",
            value=item.share,
            unit="ratio",
            note="Includes the part explained by the estate moving as a whole.",
        ),
        Evidence(
            label="Share of total change (excess)",
            value=item.excess_share,
            unit="ratio",
            note=(
                "The part that is this slice behaving differently from the "
                "network. This is what the ranking uses."
            ),
        ),
    ]
    if item.peer_z is not None:
        evidence.append(
            Evidence(
                label="Deviation from peers",
                value=item.peer_z,
                unit="z",
                note="Standard deviations from the average movement of comparable slices.",
            )
        )
    if split is not None:
        evidence.append(
            Evidence(
                label="Volume effect",
                value=split.volume_effect,
                unit="currency",
                note="Change from doing more or fewer transactions.",
            )
        )
        evidence.append(
            Evidence(
                label="Rate effect",
                value=split.rate_effect,
                unit="currency",
                note="Change from each transaction being worth more or less.",
            )
        )

    confidence = compose(
        EvidenceTier.ARITHMETIC,
        impact=abs(item.excess_share),
        significance=normalise_significance(item.peer_z),
    )

    driver = f" — driven by {split.dominant}" if split is not None else ""
    headline = (
        f"{item.label} accounts for {item.excess_share:+.0%} of the change "
        f"beyond the estate-wide move{driver}"
    )

    return Finding(
        dimension=dimension,
        subject=item.label,
        headline=headline,
        claim_type=ClaimType.ACCOUNTS_FOR,
        tier=EvidenceTier.ARITHMETIC,
        confidence=confidence,
        impact_amount=item.excess_change,
        impact_share=item.excess_share,
        evidence=tuple(evidence),
        recommendations=_dimensional_recommendations(dimension, item, split),
        does_not_establish=(
            "Where the change landed, not why. This slice may be the origin of "
            "the problem or simply the place an upstream problem surfaced — the "
            "decomposition cannot tell them apart."
        ),
    )


#: What a volume shortfall means in each cut, and who owns it.
#:
#: "Fewer transactions" is one arithmetic fact with four different meanings.
#: In a region it is footfall; in a product it is people no longer choosing
#: that item; in a segment it is a cohort shopping less often. Telling a
#: marketer to "investigate footfall in Loyal" is a category error, and it is
#: the tell that a recommendation was generated from a template rather than
#: from the finding.
_VOLUME_ADVICE: dict[Dimension, tuple[str, str, str]] = {
    Dimension.REGION: (
        "Check footfall, opening hours, and availability across {label}",
        "Fewer transactions in a region points at whether customers could "
        "reach the stores and find stock when they did.",
        "operations",
    ),
    Dimension.STORE: (
        "Check staffing, opening hours, and local disruption at {label}",
        "A single store losing transactions while its peers hold is usually "
        "something local: a closure, a road, a staffing gap.",
        "operations",
    ),
    Dimension.PRODUCT: (
        "Check availability and shelf presence for {label}",
        "Fewer transactions containing a product usually means customers "
        "could not find it rather than that they stopped wanting it.",
        "merchandising",
    ),
    Dimension.SEGMENT: (
        "Check purchase frequency and lapse rate within {label}",
        "A segment buying less often is a retention signal, not a footfall "
        "one — the customers still exist and have stopped coming back.",
        "marketing",
    ),
}

#: The same, for a basket-value shortfall.
_RATE_ADVICE: dict[Dimension, tuple[str, str, str]] = {
    Dimension.REGION: (
        "Review discount depth and product mix in {label}",
        "Transactions held up but each was worth less, which is a pricing or "
        "mix question rather than a traffic one.",
        "merchandising",
    ),
    Dimension.STORE: (
        "Review markdown activity and basket composition at {label}",
        "Customers kept coming and spent less, which points at what was on "
        "promotion rather than at whether the store was reachable.",
        "merchandising",
    ),
    Dimension.PRODUCT: (
        "Review price realisation and discount depth on {label}",
        "The product still sold in the same baskets at a lower realised price.",
        "merchandising",
    ),
    Dimension.SEGMENT: (
        "Review basket composition and offer targeting for {label}",
        "This segment shopped as often and spent less per visit, which is a "
        "trade-down rather than a lapse.",
        "marketing",
    ),
}


def _dimensional_recommendations(
    dimension: Dimension, item: Contribution, split: Any
) -> tuple[Recommendation, ...]:
    volume_driven = split is not None and split.dominant == "volume"
    table = _VOLUME_ADVICE if volume_driven else _RATE_ADVICE
    action, rationale, owner = table.get(
        dimension,
        (
            "Investigate {label} directly",
            "The decomposition localises the change without explaining it.",
            "analytics",
        ),
    )

    diagnosis = Recommendation(
        action=action.format(label=item.label),
        rationale=rationale,
        assumes=(
            "Order counts are a usable proxy for visits in this cut."
            if volume_driven
            else "Product mix within the slice is comparable across the two periods."
        ),
        owner=owner,
        urgency="high" if abs(item.excess_share) > 0.3 else "normal",
    )

    return (
        diagnosis,
        Recommendation(
            action=f"Compare {item.label} against its own history before acting",
            rationale=(
                "This finding is relative to peers in the same period. A slice "
                "that is always volatile can look like an outlier in any week."
            ),
            assumes="Comparable history exists for this slice.",
            owner="analytics",
            urgency="low",
        ),
    )


# ── Factors ──────────────────────────────────────────────────────────


def investigate_inventory(
    *,
    current: dict[str, float],
    baseline: dict[str, float],
    region: str,
    revenue_at_stake: float,
    total_change: float,
) -> list[Finding]:
    """Availability: the one factor with an unarguable mechanism.

    A stockout does not correlate with a lost sale, it produces one. There is
    no version of events in which the shelf is empty and the customer buys
    anyway, which is why this is the strongest tier any factor reaches here.

    The impact estimate is still an estimate. It assumes a stocked-out
    position would have sold at the rate its stocked peers did, which
    overstates substitutable products (the customer buys the other brand) and
    understates destination ones (the customer leaves without their basket).
    """
    current_rate = current.get("stockout_rate") or 0.0
    baseline_rate = baseline.get("stockout_rate") or 0.0
    if baseline_rate <= 0 and current_rate <= 0:
        return []

    deterioration = current_rate - baseline_rate
    if deterioration <= 0 or deterioration < FACTOR_SENSITIVITY * max(baseline_rate, 0.01):
        return []

    # Revenue foregone if the additional stocked-out positions would have sold
    # at the same rate as the ones that stayed available.
    estimated_impact = -abs(revenue_at_stake) * (deterioration / max(1.0 - baseline_rate, 0.01))
    estimated_impact = max(estimated_impact, -abs(total_change))
    share = estimated_impact / total_change if total_change else 0.0

    if not is_material(share):
        return []

    confidence = compose(
        EvidenceTier.MECHANICAL,
        impact=abs(share),
        significance=normalise_significance(deterioration / max(baseline_rate, 0.01)),
    )

    return [
        Finding(
            dimension=Dimension.INVENTORY,
            subject=region,
            headline=(
                f"Stockout rate in {region} rose from {baseline_rate:.1%} to "
                f"{current_rate:.1%} — unavailable stock cannot sell"
            ),
            claim_type=ClaimType.MECHANISM,
            tier=EvidenceTier.MECHANICAL,
            confidence=confidence,
            impact_amount=estimated_impact,
            impact_share=share,
            evidence=(
                Evidence("Stockout rate", current_rate, baseline_rate, "rate"),
                Evidence(
                    "Positions at zero",
                    current.get("stockout_positions") or 0.0,
                    baseline.get("stockout_positions") or 0.0,
                    "count",
                ),
                Evidence(
                    "Estimated revenue foregone",
                    estimated_impact,
                    unit="currency",
                    note=(
                        "Assumes an unavailable position would have sold at the "
                        "rate its available peers did."
                    ),
                ),
            ),
            recommendations=(
                Recommendation(
                    action=f"Expedite replenishment for stocked-out lines in {region}",
                    rationale="Availability is the binding constraint on these sales.",
                    assumes=(
                        "Supply exists to expedite; if the shortage is "
                        "upstream, transfer between stores instead."
                    ),
                    owner="inventory",
                    urgency="high",
                ),
                Recommendation(
                    action="Check whether the stockouts are concentrated in A-class lines",
                    rationale=(
                        "The same stockout rate costs far more when it lands on "
                        "the products that carry the revenue."
                    ),
                    assumes="ABC classification is current.",
                    owner="inventory",
                ),
            ),
            does_not_establish=(
                "How much of the lost sale was recovered by substitution. A "
                "customer who bought a different size is not a lost sale, and "
                "this estimate counts them as one."
            ),
        )
    ]


def investigate_returns(
    *,
    current: dict[str, float],
    baseline: dict[str, float],
    subject: str,
    total_change: float,
) -> list[Finding]:
    """Returns reduce net revenue directly — the mechanism is definitional.

    Graded MECHANICAL rather than ARITHMETIC despite the exactness, because
    returns are a *different cut of the same pounds* already counted by the
    regional and product decompositions. Marking them arithmetic would let the
    explained share exceed 100% while every individual number stayed correct.
    """
    current_returns = current.get("return_amount") or 0.0
    baseline_returns = baseline.get("return_amount") or 0.0
    increase = current_returns - baseline_returns
    if increase <= 0:
        return []

    share = -increase / total_change if total_change else 0.0
    if not is_material(share):
        return []

    current_rate = current.get("return_rate") or 0.0
    baseline_rate = baseline.get("return_rate") or 0.0
    relative = (current_rate - baseline_rate) / baseline_rate if baseline_rate else 0.0

    confidence = compose(
        EvidenceTier.MECHANICAL,
        impact=abs(share),
        significance=normalise_significance(relative * 3.0),
    )

    return [
        Finding(
            dimension=Dimension.RETURNS,
            subject=subject,
            headline=(
                f"Returns rose {increase:,.0f} ({baseline_rate:.1%} → "
                f"{current_rate:.1%} of sales), reducing net revenue directly"
            ),
            claim_type=ClaimType.MECHANISM,
            tier=EvidenceTier.MECHANICAL,
            confidence=confidence,
            impact_amount=-increase,
            impact_share=share,
            evidence=(
                Evidence("Returned value", current_returns, baseline_returns, "currency"),
                Evidence("Return rate", current_rate, baseline_rate, "rate"),
                Evidence(
                    "Returned units",
                    current.get("return_units") or 0.0,
                    baseline.get("return_units") or 0.0,
                    "units",
                ),
            ),
            recommendations=(
                Recommendation(
                    action="Identify the products and reasons behind the increase",
                    rationale=(
                        "A return spike is usually concentrated: a quality "
                        "problem, a sizing change, or a delivery failure."
                    ),
                    assumes="Return reason codes are captured at the till or in the portal.",
                    owner="merchandising",
                    urgency="high" if relative > 0.5 else "normal",
                ),
                Recommendation(
                    action="Check whether the rise tracks the late-delivery window",
                    rationale=(
                        "Deliveries that miss their promise are a common cause "
                        "of returns, and that would make this a symptom rather "
                        "than a cause."
                    ),
                    assumes="Shipping data covers the same period.",
                    owner="operations",
                ),
            ),
            does_not_establish=(
                "Why goods came back. The value is exact; the reason is not in this data."
            ),
        )
    ]


def investigate_shipping(
    *,
    current: dict[str, float],
    baseline: dict[str, float],
    subject: str,
    total_change: float,
    revenue_at_stake: float,
) -> list[Finding]:
    """Late deliveries: a plausible mechanism this platform has not verified.

    Poor delivery performance is well established to suppress repeat purchase.
    That evidence comes from the literature and from other retailers — not
    from this dataset, which contains no experiment and no control. So the
    finding is graded STATISTICAL: the deterioration is measured, the link to
    revenue is asserted from outside.
    """
    current_rate = current.get("on_time_rate")
    baseline_rate = baseline.get("on_time_rate")
    if current_rate is None or baseline_rate is None or baseline_rate <= 0:
        return []

    # Judged in percentage points, not relatively. On-time rates live near
    # the top of their range, so a fall from 92% to 86% is a six-point
    # operational failure and only a 7% relative move — a relative threshold
    # calibrated for stockouts silently ignores every delivery incident.
    deterioration = baseline_rate - current_rate
    if deterioration < ON_TIME_SENSITIVITY_POINTS:
        return []

    # Deliberately conservative: attributes only a fraction of the shortfall,
    # because shipping affects the shipped share of orders and repeat
    # behaviour rather than the whole estate's revenue in-period.
    estimated_impact = -abs(revenue_at_stake) * deterioration * 0.5
    estimated_impact = max(estimated_impact, -abs(total_change))
    share = estimated_impact / total_change if total_change else 0.0
    if not is_material(share):
        return []

    confidence = compose(
        EvidenceTier.STATISTICAL,
        impact=abs(share),
        significance=normalise_significance(deterioration / max(baseline_rate, 0.01) * 3.0),
    )

    return [
        Finding(
            dimension=Dimension.SHIPPING,
            subject=subject,
            headline=(
                f"On-time delivery in {subject} fell from {baseline_rate:.0%} to {current_rate:.0%}"
            ),
            claim_type=ClaimType.COINCIDES_WITH,
            tier=EvidenceTier.STATISTICAL,
            confidence=confidence,
            impact_amount=estimated_impact,
            impact_share=share,
            evidence=(
                Evidence("On-time rate", current_rate, baseline_rate, "rate"),
                Evidence(
                    "Average days late",
                    current.get("avg_days_late") or 0.0,
                    baseline.get("avg_days_late") or 0.0,
                    "days",
                ),
                Evidence(
                    "Carriers missing promise",
                    current.get("carriers_missing_promise") or 0.0,
                    baseline.get("carriers_missing_promise") or 0.0,
                    "count",
                ),
            ),
            recommendations=(
                Recommendation(
                    action=f"Review carrier performance for {subject} and reallocate volume",
                    rationale=(
                        "The deterioration is measured and attributable to specific carriers."
                    ),
                    assumes="Alternative carrier capacity exists on these lanes.",
                    owner="operations",
                    urgency="high",
                ),
                Recommendation(
                    action="Check the return rate over the same window",
                    rationale=(
                        "Late deliveries and returns usually move together, and "
                        "the pair is stronger evidence than either alone."
                    ),
                    assumes="Returns are attributable to delivery date.",
                    owner="analytics",
                ),
            ),
            does_not_establish=(
                "That late deliveries reduced revenue in this period. The link "
                "is plausible and taken from outside this dataset; no "
                "experiment here measured it."
            ),
        )
    ]


def investigate_promotion(
    *, current: dict[str, float], baseline: dict[str, float], total_change: float
) -> list[Finding]:
    """A promotion ending removes its uplift — but the uplift was never measured.

    The trap is treating all promotional revenue as incremental. Much of it is
    not: some customers would have bought anyway and simply bought cheaper.
    Without a holdout group the incremental share is unknown, so this
    attributes a deliberately conservative fraction and says why.
    """
    current_revenue = current.get("promo_revenue") or 0.0
    baseline_revenue = baseline.get("promo_revenue") or 0.0
    if baseline_revenue <= 0:
        return []

    decline = baseline_revenue - current_revenue
    if decline <= 0 or decline / baseline_revenue < FACTOR_SENSITIVITY:
        return []

    # Half of promotional revenue treated as incremental. A placeholder for a
    # measurement nobody has taken, chosen to be visibly a placeholder.
    incremental_share = 0.5
    estimated_impact = -decline * incremental_share
    estimated_impact = max(estimated_impact, -abs(total_change))
    share = estimated_impact / total_change if total_change else 0.0
    if not is_material(share):
        return []

    confidence = compose(
        EvidenceTier.STATISTICAL,
        impact=abs(share),
        significance=normalise_significance(decline / baseline_revenue * 3.0),
    )

    return [
        Finding(
            dimension=Dimension.PROMOTION,
            subject="Promotional activity",
            headline=(
                f"Promotional revenue fell {decline:,.0f} "
                f"({baseline.get('active_promotions', 0):.0f} → "
                f"{current.get('active_promotions', 0):.0f} active campaigns)"
            ),
            claim_type=ClaimType.COINCIDES_WITH,
            tier=EvidenceTier.STATISTICAL,
            confidence=confidence,
            impact_amount=estimated_impact,
            impact_share=share,
            evidence=(
                Evidence("Promotional revenue", current_revenue, baseline_revenue, "currency"),
                Evidence(
                    "Active campaigns",
                    current.get("active_promotions") or 0.0,
                    baseline.get("active_promotions") or 0.0,
                    "count",
                ),
                Evidence(
                    "Average depth",
                    current.get("avg_promo_depth") or 0.0,
                    baseline.get("avg_promo_depth") or 0.0,
                    "rate",
                ),
                Evidence(
                    "Assumed incremental share",
                    incremental_share,
                    unit="ratio",
                    note=(
                        "A placeholder, not a measurement. Establishing the real "
                        "figure needs a holdout group."
                    ),
                ),
            ),
            recommendations=(
                Recommendation(
                    action="Compare the promotional calendar across both periods",
                    rationale=(
                        "A campaign that ended is the simplest explanation to confirm or dismiss."
                    ),
                    assumes="The calendar is recorded with accurate start and end dates.",
                    owner="marketing",
                ),
                Recommendation(
                    action="Run the next campaign with a holdout group",
                    rationale=(
                        "Incremental uplift is currently assumed rather than "
                        "measured, which makes every promotional attribution "
                        "here a guess with a number attached."
                    ),
                    assumes="A holdout is commercially acceptable.",
                    owner="marketing",
                    urgency="low",
                ),
            ),
            does_not_establish=(
                "How much promotional revenue was incremental. Without a "
                "holdout, subsidised sales that would have happened anyway are "
                "indistinguishable from sales the promotion created."
            ),
        )
    ]


def investigate_weather(
    *,
    severe_days: int,
    window_days: int,
    baseline_severe_days: int,
    observed_daily_gap: float,
    region: str,
    total_change: float,
    detail: dict[str, float],
) -> list[Finding]:
    """Weather: strongly predictive, entirely outside anyone's control.

    The impact estimate is a real comparison rather than a hand-wave — average
    daily revenue on severe days against non-severe days in the same region,
    multiplied by the number of severe days in the window. It is still capped
    at ASSOCIATIVE, because the comparison is observational: severe days
    differ from ordinary days in more ways than the weather, and nothing here
    controls for any of them.

    Weather never generates an action to fix it. Its value in a root-cause
    briefing is subtractive — it rules a region-week *out* as an operational
    failure, which stops a team investigating a store that did nothing wrong.
    """
    if severe_days <= baseline_severe_days or observed_daily_gap >= 0:
        return []

    estimated_impact = observed_daily_gap * severe_days
    estimated_impact = max(estimated_impact, -abs(total_change))
    share = estimated_impact / total_change if total_change else 0.0
    if not is_material(share):
        return []

    consistency = severe_days / max(window_days, 1)
    confidence = compose(
        EvidenceTier.ASSOCIATIVE,
        impact=abs(share),
        significance=normalise_significance(detail.get("precipitation_z")),
        consistency=consistency,
    )

    return [
        Finding(
            dimension=Dimension.WEATHER,
            subject=region,
            headline=(
                f"{severe_days} severe-weather day(s) in {region} "
                f"(vs {baseline_severe_days} in the comparison period)"
            ),
            claim_type=ClaimType.COINCIDES_WITH,
            tier=EvidenceTier.ASSOCIATIVE,
            confidence=confidence,
            impact_amount=estimated_impact,
            impact_share=share,
            evidence=(
                Evidence(
                    "Severe days in window", float(severe_days), float(baseline_severe_days), "days"
                ),
                Evidence(
                    "Average daily revenue gap on severe days",
                    observed_daily_gap,
                    unit="currency",
                    note="Severe days against ordinary days in the same region.",
                ),
                Evidence(
                    "Rainfall vs regional norm",
                    detail.get("precipitation_z") or 0.0,
                    unit="z",
                    note="Standardised against this region's own distribution.",
                ),
                Evidence(
                    "Peak wind",
                    detail.get("wind_kph_max") or 0.0,
                    unit="kph",
                ),
            ),
            recommendations=(
                Recommendation(
                    action=f"Exclude the severe-weather days when reviewing {region}'s performance",
                    rationale=(
                        "Judging stores on days customers could not reach them "
                        "measures the weather, not the stores."
                    ),
                    assumes="The severe-weather flag is accurate for these dates.",
                    owner="operations",
                ),
                Recommendation(
                    action="Check whether demand recovered afterwards or was lost",
                    rationale=(
                        "Weather usually defers purchases rather than destroying "
                        "them, and deferred demand needs stock to land in, not a "
                        "recovery plan."
                    ),
                    assumes="Enough days have passed since the event to see a rebound.",
                    owner="analytics",
                ),
            ),
            does_not_establish=(
                "That weather caused the shortfall. Severe days differ from "
                "ordinary days in more ways than the weather, and nothing here "
                "controls for any of them. Weather is never the thing to fix — "
                "it is the thing that excuses a region from investigation."
            ),
        )
    ]
