"""The six detectors.

Each reads a surface the platform already publishes and turns a condition into
a candidate. None of them computes a metric — a detector that derives its own
threshold from raw data ends up disagreeing with the dashboard that shows the
same number, and the alert is then arguing with the screen the recipient
checks to verify it.

Two rules hold across all six.

**Severity comes from the condition, not from the detector.** A detector that
picks its own severity drifts toward critical, because whoever wrote it
believes their signal is the important one. Here severity is a function of how
bad the observed value is against a stated boundary.

**An alert names what to do.** Every candidate carries a deep link and a body
that states the action. "Stockout risk on AC-1010" is a fact; "AC-1010 at
S2001 runs out in 2 days, inside its 8-day lead time — expedite or transfer"
is an alert. The first gets acknowledged and forgotten.
"""

from datetime import date
from typing import Any

from app.services.notifications.contracts import AlertCandidate, AlertKind, Severity

#: Days of cover below which stock is a warning, and below which it is urgent.
LOW_COVER_DAYS = 7.0
CRITICAL_COVER_DAYS = 2.0

#: Relative revenue decline that opens a sales alert, and that escalates it.
SALES_DROP_WARN = 0.10
SALES_DROP_CRITICAL = 0.20

#: A forecast whose model failed to beat seasonal naive is a risk in itself:
#: downstream replenishment is being planned on a number with no demonstrated
#: skill, and nobody looking at the forecast alone can see that.
MASE_PARITY = 1.0

#: How far a store's return rate must sit above its peers before the pattern
#: is worth a human looking at it. Three standard deviations is deliberately
#: conservative — this is the one detector whose output touches people.
FRAUD_Z_THRESHOLD = 3.0

#: Supplier OTIF below which committed volume is a risk to availability.
SUPPLIER_OTIF_THRESHOLD = 0.75

#: Profit opportunity that makes a recommendation worth interrupting for.
RECOMMENDATION_THRESHOLD = 25_000.0


def _f(row: dict[str, Any], key: str, default: float = 0.0) -> float:
    value = row.get(key)
    if not isinstance(value, int | float | str):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


# ── 1. Low inventory ─────────────────────────────────────────────────


def low_inventory(rows: list[dict[str, Any]], *, as_of: date) -> list[AlertCandidate]:
    """Lines about to run out, ranked by what the shortage costs.

    Ranked by revenue at risk rather than by days of cover. A slow mover at
    zero cover loses less than a staple two days out, and an alert queue
    sorted by urgency-of-the-number rather than urgency-of-the-consequence
    sends buyers to the wrong shelf.
    """
    candidates: list[AlertCandidate] = []

    for row in rows:
        cover = _f(row, "soonest_stockout_days", -1.0)
        at_risk = _f(row, "revenue_at_risk")
        if cover < 0 or cover > LOW_COVER_DAYS or at_risk <= 0:
            continue

        sku = str(row.get("sku") or "unknown")
        store = str(row.get("store_id") or "")
        subject = f"{sku}@{store}" if store else sku
        severity = Severity.CRITICAL if cover <= CRITICAL_COVER_DAYS else Severity.WARN

        candidates.append(
            AlertCandidate(
                kind=AlertKind.LOW_INVENTORY,
                subject=subject,
                title=f"{sku} runs out in {cover:.0f} day(s) at {store or 'this store'}",
                body=(
                    f"Cover is {cover:.1f} days against demand of "
                    f"{_f(row, 'daily_demand'):.1f} a day, with "
                    f"{at_risk:,.0f} of revenue at risk. Suggested order is "
                    f"{_f(row, 'suggested_order_qty'):,.0f} units."
                ),
                severity=severity,
                observed=cover,
                expected_low=LOW_COVER_DAYS,
                expected_high=float("inf"),
                detected_for=as_of,
                evidence={
                    "days_of_cover": round(cover, 2),
                    "revenue_at_risk": round(at_risk, 2),
                    "suggested_order_qty": _f(row, "suggested_order_qty"),
                },
                deep_link=f"/inventory/reorder?sku={sku}",
            )
        )

    candidates.sort(key=lambda item: -_evidence(item, "revenue_at_risk"))
    return candidates


# ── 2. Sales drop ────────────────────────────────────────────────────


def sales_drop(
    *, current: float, prior: float, region_rows: list[dict[str, Any]], as_of: date
) -> list[AlertCandidate]:
    """A material fall in revenue against the comparable prior period.

    One network alert, plus the region that moved furthest against the trend.
    Not one per region: five regional alerts saying the same national thing is
    the shape of message people stop opening.
    """
    if prior <= 0:
        return []

    change = (current - prior) / prior
    if change > -SALES_DROP_WARN:
        return []

    severity = Severity.CRITICAL if change <= -SALES_DROP_CRITICAL else Severity.WARN
    worst = min(
        region_rows,
        key=lambda row: _f(row, "net_revenue"),
        default=None,
    )
    worst_name = str(worst.get("region")) if worst else ""

    body = (
        f"Net revenue is {abs(change):.1%} below the comparable prior period "
        f"({current:,.0f} against {prior:,.0f})."
    )
    if worst_name:
        body += f" {worst_name} is the weakest region. Root cause analysis explains the split."

    return [
        AlertCandidate(
            kind=AlertKind.SALES_DROP,
            subject="network",
            title=f"Net revenue down {abs(change):.1%} on the prior period",
            body=body,
            severity=severity,
            observed=current,
            expected_low=prior * (1 - SALES_DROP_WARN),
            expected_high=prior * 1.5,
            detected_for=as_of,
            evidence={
                "current": round(current, 2),
                "prior": round(prior, 2),
                "relative_change": round(change, 4),
                "weakest_region": worst_name,
            },
            deep_link="/rca/investigate",
        )
    ]


# ── 3. Forecast risk ─────────────────────────────────────────────────


def forecast_risk(rows: list[dict[str, Any]], *, as_of: date) -> list[AlertCandidate]:
    """Forecasts nobody should be planning against.

    The condition is not that the forecast is low — a low forecast is a
    business fact, not an alert. It is that the model behind it has no
    demonstrated skill: a MASE at or above 1.0 means it did not beat assuming
    last week repeats. Replenishment downstream is being planned on that
    number, and nothing on the forecast screen makes the weakness visible.
    """
    candidates: list[AlertCandidate] = []
    seen: set[str] = set()

    for row in rows:
        mase = row.get("model_mase")
        if mase is None:
            continue
        value = _f(row, "model_mase")
        target = str(row.get("target") or row.get("series_key") or "forecast")
        if value < MASE_PARITY or target in seen:
            continue
        seen.add(target)

        candidates.append(
            AlertCandidate(
                kind=AlertKind.FORECAST_RISK,
                subject=target,
                title=f"The {target} forecast does not beat a naive baseline",
                body=(
                    f"MASE is {value:.2f}: at or above 1.0 the model performs no "
                    "better than assuming the same weekday repeats. Decisions "
                    "downstream — replenishment in particular — are being planned "
                    "on a number with no demonstrated skill."
                ),
                severity=Severity.WARN,
                observed=value,
                expected_low=0.0,
                expected_high=MASE_PARITY,
                detected_for=as_of,
                evidence={
                    "mase": round(value, 4),
                    "wape": round(_f(row, "model_wape"), 4),
                },
                deep_link="/forecasts/meta/accuracy",
            )
        )

    return candidates


# ── 4. Fraud risk ────────────────────────────────────────────────────


def fraud_risk(rows: list[dict[str, Any]], *, as_of: date) -> list[AlertCandidate]:
    """Return patterns that warrant a look — never an accusation.

    **This detects an anomaly, not a crime.** A store whose return rate sits
    far above its peers may have a dishonest till, or a damaged delivery, a
    mis-picked planogram, a single high-value return, or a genuinely different
    catchment. Naming any of those as fraud on the strength of a z-score is
    both wrong and, aimed at a named employee, defamatory.

    So the language is deliberate throughout: the alert asks for the pattern
    to be *reviewed*, states the alternatives explicitly, and never names an
    individual. The evidence is the store's rate against its peers, and that
    is the whole of what is being claimed.
    """
    rates = [
        (str(row.get("slice_value") or row.get("store_id") or ""), _f(row, "return_rate"))
        for row in rows
    ]
    rates = [(name, rate) for name, rate in rates if name and rate > 0]
    if len(rates) < 5:
        # Fewer than five peers cannot establish what normal looks like, and
        # an outlier among three is arithmetic rather than evidence.
        return []

    values = [rate for _, rate in rates]
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
    spread = variance**0.5
    if spread <= 0:
        return []

    candidates: list[AlertCandidate] = []
    for name, rate in rates:
        z = (rate - mean) / spread
        if z < FRAUD_Z_THRESHOLD:
            continue

        candidates.append(
            AlertCandidate(
                kind=AlertKind.FRAUD_RISK,
                subject=name,
                title=f"Unusual return rate at {name} — review recommended",
                body=(
                    f"Returns are running at {rate:.1%} of sales against a peer "
                    f"average of {mean:.1%} ({z:.1f} standard deviations above). "
                    "This is an anomaly, not a finding: it is equally consistent "
                    "with a damaged delivery, a mis-picked planogram, one large "
                    "return, or a genuinely different catchment. Review the "
                    "underlying transactions before drawing any conclusion."
                ),
                severity=Severity.WARN,
                observed=rate,
                expected_low=0.0,
                expected_high=mean + FRAUD_Z_THRESHOLD * spread,
                detected_for=as_of,
                evidence={
                    "return_rate": round(rate, 4),
                    "peer_mean": round(mean, 4),
                    "z_score": round(z, 2),
                    "peers_compared": len(rates),
                },
                deep_link=f"/analytics/revenue/breakdown?store={name}",
            )
        )

    return candidates


# ── 5. Inventory risk ────────────────────────────────────────────────


def inventory_risk(rows: list[dict[str, Any]], *, as_of: date) -> list[AlertCandidate]:
    """Suppliers whose unreliability puts committed volume at risk.

    Distinct from low inventory, which is about a shelf today. This is about
    the supply behind many shelves: a vendor missing three deliveries in four
    threatens availability across everything it carries, and the response is a
    sourcing conversation rather than an order.
    """
    candidates: list[AlertCandidate] = []

    for row in rows:
        otif = _f(row, "otif_rate", 1.0)
        closed = _f(row, "closed_lines")
        if closed < 20 or otif >= SUPPLIER_OTIF_THRESHOLD:
            continue

        supplier = str(row.get("supplier_name") or row.get("supplier_id") or "unknown")
        exposure = _f(row, "ordered_value")
        severity = Severity.CRITICAL if otif < 0.5 else Severity.WARN

        candidates.append(
            AlertCandidate(
                kind=AlertKind.INVENTORY_RISK,
                subject=supplier,
                title=f"{supplier} delivering {otif:.0%} on time and in full",
                body=(
                    f"Across {closed:,.0f} received lines, OTIF is {otif:.0%} "
                    f"against {exposure:,.0f} of committed spend. Availability "
                    "on everything this vendor carries is exposed; the gap is "
                    "currently being funded with safety stock."
                ),
                severity=severity,
                observed=otif,
                expected_low=SUPPLIER_OTIF_THRESHOLD,
                expected_high=1.0,
                detected_for=as_of,
                evidence={
                    "otif_rate": round(otif, 4),
                    "received_lines": closed,
                    "committed_spend": round(exposure, 2),
                },
                deep_link=f"/inventory/supplier-risk?supplier={supplier}",
            )
        )

    return candidates


# ── 6. Recommendation ready ──────────────────────────────────────────


def recommendation_ready(payload: dict[str, Any], *, as_of: date) -> list[AlertCandidate]:
    """A materially valuable action is waiting.

    Gated on value, not on existence. The recommendation engine produces
    something almost every run, and notifying on that trains people that the
    notification means nothing. Only a portfolio worth interrupting for gets
    through, and the figure quoted is the deduplicated one — the gross total
    counts overlapping actions twice.
    """
    net = float(payload.get("net_profit_opportunity") or 0.0)
    count = int(payload.get("count") or 0)
    if count == 0 or net < RECOMMENDATION_THRESHOLD:
        return []

    items = payload.get("recommendations") or []
    leading = items[0]["action"] if items else ""

    return [
        AlertCandidate(
            kind=AlertKind.RECOMMENDATION_READY,
            subject="portfolio",
            title=f"{count} recommended actions worth {net:,.0f}",
            body=(
                f"The highest-ranked is: {leading}. The figure is net of "
                "overlapping actions, which chase the same pounds — the gross "
                f"total is {float(payload.get('gross_profit_opportunity') or 0.0):,.0f}."
            ),
            severity=Severity.INFO,
            observed=net,
            expected_low=0.0,
            expected_high=RECOMMENDATION_THRESHOLD,
            detected_for=as_of,
            evidence={
                "count": count,
                "net_profit_opportunity": round(net, 2),
                "by_category": payload.get("by_category", {}),
            },
            deep_link="/recommendations",
        )
    ]


def _evidence(candidate: AlertCandidate, key: str) -> float:
    value = candidate.evidence.get(key)
    return float(value) if isinstance(value, int | float) else 0.0
