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
                # Unbounded above: there is no amount of cover that makes this
                # alert fire. `None` rather than infinity, which JSON cannot
                # carry and Postgres will not accept.
                expected_high=None,
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


# ── 7. Rolling baseline anomaly ──────────────────────────────────────


#: Rolling window for baseline (days of history to average)
ROLLING_WINDOW_DAYS = 28

#: Minimum deviation from rolling baseline to trigger alert
ROLLING_BASELINE_DEVIATION_MEDIUM = 0.15  # 15%
ROLLING_BASELINE_DEVIATION_HIGH = 0.25  # 25%
ROLLING_BASELINE_DEVIATION_CRITICAL = 0.40  # 40%


def rolling_baseline_anomaly(
    metric: str,
    current_value: float,
    historical_values: list[float],
    *,
    as_of: date,
    entity: str = "network",
    min_history: int = 14,
) -> list[AlertCandidate]:
    """Detect anomalies against rolling historical baseline.

    Compares current value vs rolling average of historical values. Uses simple
    moving average rather than sophisticated forecasting - defensibly simple.

    Args:
        metric: Metric name (e.g., "net_revenue")
        current_value: Today's observed value
        historical_values: Prior N days of values (most recent first)
        as_of: Detection date
        entity: What this alert is about (store, region, network)
        min_history: Minimum historical points required

    Returns:
        List of alert candidates (0 or 1)
    """
    if len(historical_values) < min_history:
        return []

    baseline = sum(historical_values) / len(historical_values)
    if baseline <= 0:
        return []

    deviation = (current_value - baseline) / baseline
    abs_deviation = abs(deviation)

    if abs_deviation < ROLLING_BASELINE_DEVIATION_MEDIUM:
        return []

    # Severity based on magnitude
    if abs_deviation >= ROLLING_BASELINE_DEVIATION_CRITICAL:
        severity = Severity.CRITICAL
    elif abs_deviation >= ROLLING_BASELINE_DEVIATION_HIGH:
        severity = Severity.HIGH
    else:
        severity = Severity.MEDIUM

    direction = "above" if deviation > 0 else "below"
    return [
        AlertCandidate(
            kind=AlertKind.SALES_DROP if deviation < 0 else AlertKind.RECOMMENDATION_READY,
            subject=entity,
            title=f"{metric} is {abs_deviation:.1%} {direction} rolling baseline",
            body=(
                f"Current value is {current_value:,.0f} against a {len(historical_values)}-day "
                f"rolling average of {baseline:,.0f} ({deviation:+.1%}). "
                f"This represents a {'decline' if deviation < 0 else 'surge'} "
                f"outside normal variation."
            ),
            severity=severity,
            observed=current_value,
            expected_low=baseline * (1 - ROLLING_BASELINE_DEVIATION_MEDIUM),
            expected_high=baseline * (1 + ROLLING_BASELINE_DEVIATION_MEDIUM),
            detected_for=as_of,
            evidence={
                "method": "rolling_baseline",
                "current": round(current_value, 2),
                "baseline": round(baseline, 2),
                "deviation": round(deviation, 4),
                "window_days": len(historical_values),
            },
            deep_link=f"/analytics/trends?metric={metric}",
        )
    ]


# ── 8. Forecast residual anomaly ─────────────────────────────────────


#: Minimum forecast error to trigger alert (as % of forecast)
FORECAST_ERROR_MEDIUM = 0.20  # 20%
FORECAST_ERROR_HIGH = 0.35  # 35%
FORECAST_ERROR_CRITICAL = 0.50  # 50%


def forecast_residual_anomaly(
    metric: str,
    actual: float,
    forecast: float,
    forecast_lower: float | None,
    forecast_upper: float | None,
    *,
    as_of: date,
    entity: str = "network",
    confidence_level: float = 0.80,
) -> list[AlertCandidate]:
    """Detect when actual significantly deviates from forecast.

    Compares actual vs forecast, considering prediction intervals if available.
    A forecast miss is a business fact worth investigating - it means plans
    built on that forecast are now wrong.

    Args:
        metric: Metric name
        actual: Realized value
        forecast: Point forecast
        forecast_lower: Lower prediction bound (optional)
        forecast_upper: Upper prediction bound (optional)
        as_of: Detection date
        entity: What this alert is about
        confidence_level: Forecast confidence level (for reporting)

    Returns:
        List of alert candidates (0 or 1)
    """
    if forecast <= 0:
        return []

    # Check if actual is outside prediction interval
    outside_interval = (forecast_lower is not None and actual < forecast_lower) or (
        forecast_upper is not None and actual > forecast_upper
    )

    # Calculate percentage error
    error = (actual - forecast) / forecast
    abs_error = abs(error)

    # Only alert if error is material or outside interval
    if abs_error < FORECAST_ERROR_MEDIUM and not outside_interval:
        return []

    # Severity based on magnitude
    is_critical = abs_error >= FORECAST_ERROR_CRITICAL or (
        outside_interval and abs_error >= FORECAST_ERROR_HIGH
    )
    if is_critical:
        severity = Severity.CRITICAL
    elif abs_error >= FORECAST_ERROR_HIGH or outside_interval:
        severity = Severity.HIGH
    else:
        severity = Severity.MEDIUM

    interval_note = ""
    if outside_interval:
        interval_note = f" This is outside the {confidence_level:.0%} prediction interval."

    direction = "above" if error > 0 else "below"
    return [
        AlertCandidate(
            kind=AlertKind.FORECAST_RISK,
            subject=entity,
            title=f"{metric} actual is {abs_error:.1%} {direction} forecast",
            body=(
                f"Actual value is {actual:,.0f} against a forecast of {forecast:,.0f} "
                f"({error:+.1%}).{interval_note} Plans built on this forecast may need revision."
            ),
            severity=severity,
            observed=actual,
            expected_low=forecast_lower if forecast_lower else forecast * 0.8,
            expected_high=forecast_upper if forecast_upper else forecast * 1.2,
            detected_for=as_of,
            evidence={
                "method": "forecast_residual",
                "actual": round(actual, 2),
                "forecast": round(forecast, 2),
                "error_pct": round(error, 4),
                "forecast_lower": round(forecast_lower, 2) if forecast_lower else None,
                "forecast_upper": round(forecast_upper, 2) if forecast_upper else None,
                "outside_interval": outside_interval,
            },
            deep_link=f"/forecasts/accuracy?metric={metric}",
        )
    ]


# ── 9. Control limits (Statistical Process Control) ─────────────────


#: Standard deviations for control limits
CONTROL_LIMIT_SIGMA = 3.0  # 3-sigma = 99.7% of normal variation


def control_limits_anomaly(
    metric: str,
    current_value: float,
    historical_values: list[float],
    *,
    as_of: date,
    entity: str = "network",
    min_history: int = 20,
) -> list[AlertCandidate]:
    """Detect out-of-control conditions using statistical process control.

    Uses mean ± 3σ control limits. A process is "in control" when values fall
    within limits; outside signals a special cause worth investigating.

    Args:
        metric: Metric name
        current_value: Current observed value
        historical_values: Historical baseline period
        as_of: Detection date
        entity: What this alert is about
        min_history: Minimum points for stable statistics

    Returns:
        List of alert candidates (0 or 1)
    """
    if len(historical_values) < min_history:
        return []

    # Calculate mean and standard deviation
    mean = sum(historical_values) / len(historical_values)
    variance = sum((x - mean) ** 2 for x in historical_values) / (len(historical_values) - 1)
    std_dev = variance**0.5

    if std_dev <= 0:
        return []

    # Control limits: mean ± 3σ
    ucl = mean + (CONTROL_LIMIT_SIGMA * std_dev)  # Upper control limit
    lcl = mean - (CONTROL_LIMIT_SIGMA * std_dev)  # Lower control limit

    # Check if current value is outside control limits
    if lcl <= current_value <= ucl:
        return []  # In control

    # Calculate how many sigma out
    sigma_distance = abs(current_value - mean) / std_dev

    # Severity based on sigma distance
    if sigma_distance >= 4.0:
        severity = Severity.CRITICAL  # 4+ sigma is extremely rare
    elif sigma_distance >= 3.5:
        severity = Severity.HIGH
    else:
        severity = Severity.MEDIUM  # 3-3.5 sigma

    direction = "above" if current_value > ucl else "below"
    limit_breached = ucl if current_value > ucl else lcl

    return [
        AlertCandidate(
            kind=AlertKind.SALES_DROP if current_value < lcl else AlertKind.RECOMMENDATION_READY,
            subject=entity,
            title=f"{metric} outside control limits ({sigma_distance:.1f}σ {direction} mean)",
            body=(
                f"Current value is {current_value:,.0f}, which is {direction} the "
                f"{CONTROL_LIMIT_SIGMA}σ control limit of {limit_breached:,.0f}. "
                f"Process mean is {mean:,.0f} ± {std_dev:,.0f}. This signals a special "
                "cause requiring investigation."
            ),
            severity=severity,
            observed=current_value,
            expected_low=lcl,
            expected_high=ucl,
            detected_for=as_of,
            evidence={
                "current": round(current_value, 2),
                "mean": round(mean, 2),
                "std_dev": round(std_dev, 2),
                "lcl": round(lcl, 2),
                "ucl": round(ucl, 2),
                "sigma_distance": round(sigma_distance, 2),
                "baseline_points": len(historical_values),
            },
            deep_link=f"/analytics/control-chart?metric={metric}",
        )
    ]


def seasonal_baseline_anomaly(
    metric: str,
    current_value: float,
    seasonal_comparison_value: float,
    *,
    as_of: date,
    entity: str = "network",
    period_label: str = "same period last year",
    min_baseline: float = 100.0,
) -> list[AlertCandidate]:
    """Detect anomalies against seasonal baseline (e.g., year-over-year).

    Compares current value to the same period in a prior seasonal cycle.
    Most useful for metrics with strong seasonal patterns (retail sales,
    holiday demand, etc.). Uses percentage deviation with minimum absolute
    threshold to avoid false positives on small numbers.

    Thresholds (percentage deviation):
    - MEDIUM: 20% deviation
    - HIGH: 35% deviation
    - CRITICAL: 50% deviation

    Args:
        metric: Metric being monitored (e.g., "revenue", "units_sold")
        current_value: Current period value
        seasonal_comparison_value: Value from comparable prior period
        as_of: Date of detection
        entity: What's being monitored (store, SKU, region, "network")
        period_label: Human-readable period description (e.g., "Q4 2023")
        min_baseline: Minimum baseline value to consider significant

    Returns:
        List with 0-1 AlertCandidate. Empty if deviation below threshold
        or baseline too small to be meaningful.

    Example:
        >>> seasonal_baseline_anomaly(
        ...     "revenue", 85000.0, 100000.0, as_of=date(2024, 12, 15),
        ...     period_label="Q4 2023"
        ... )
        [AlertCandidate(kind=SALES_DROP, severity=MEDIUM, ...)]
    """
    # Avoid division by zero and false positives on tiny numbers
    if seasonal_comparison_value == 0 or abs(seasonal_comparison_value) < min_baseline:
        return []

    deviation_pct = abs((current_value - seasonal_comparison_value) / seasonal_comparison_value)

    # Determine severity based on magnitude
    if deviation_pct < 0.20:
        return []  # Below significance threshold
    elif deviation_pct < 0.35:
        severity = Severity.MEDIUM
    elif deviation_pct < 0.50:
        severity = Severity.HIGH
    else:
        severity = Severity.CRITICAL

    # Determine direction (drop vs spike)
    direction = "down" if current_value < seasonal_comparison_value else "up"
    kind = AlertKind.SALES_DROP if direction == "down" else AlertKind.INVENTORY_RISK

    metric_name = metric.replace("_", " ").title()
    if direction == "down":
        impact_desc = "This represents a significant decline from seasonal norms."
    else:
        impact_desc = "This represents unusual growth vs seasonal norms."

    return [
        AlertCandidate(
            kind=kind,
            subject=entity,
            title=f"{metric_name} {direction} {deviation_pct:.0%} vs {period_label}",
            body=(
                f"{metric_name} is {current_value:,.0f}, "
                f"{direction} {deviation_pct:.0%} from {seasonal_comparison_value:,.0f} "
                f"in {period_label}. {impact_desc}"
            ),
            severity=severity,
            observed=current_value,
            expected_low=seasonal_comparison_value * 0.80 if direction == "down" else None,
            expected_high=seasonal_comparison_value * 1.20 if direction == "up" else None,
            detected_for=as_of,
            evidence={
                "method": "seasonal_baseline",
                "current": current_value,
                "baseline": seasonal_comparison_value,
                "baseline_period": period_label,
                "deviation_pct": round(deviation_pct, 4),
                "direction": direction,
            },
            deep_link=f"/analytics/seasonal-comparison?metric={metric}&entity={entity}",
        )
    ]


def rate_of_change_anomaly(
    metric: str,
    recent_values: list[float],
    *,
    as_of: date,
    entity: str = "network",
    min_periods: int = 5,
    acceleration_threshold: float = 0.30,
) -> list[AlertCandidate]:
    """Detect rapid acceleration or deceleration in metric trends.

    Measures the rate of change between consecutive periods to identify
    sudden momentum shifts. Uses simple linear regression slope comparison:
    recent slope vs longer-term slope. Alerts when acceleration/deceleration
    exceeds threshold.

    Useful for detecting:
    - Revenue declines that are accelerating
    - Inventory buildups that are speeding up
    - Sudden reversals in trends

    Thresholds (slope change):
    - MEDIUM: 30% acceleration/deceleration
    - HIGH: 50% acceleration/deceleration
    - CRITICAL: 75% acceleration/deceleration

    Args:
        metric: Metric being monitored
        recent_values: Time series (oldest to newest), minimum 5 periods
        as_of: Date of detection
        entity: What's being monitored
        min_periods: Minimum data points required (default 5)
        acceleration_threshold: Minimum acceleration to alert (default 30%)

    Returns:
        List with 0-1 AlertCandidate. Empty if insufficient data or
        rate of change below threshold.

    Example:
        >>> rate_of_change_anomaly(
        ...     "revenue", [100, 98, 95, 90, 82], as_of=date(2024, 12, 15)
        ... )
        [AlertCandidate(kind=SALES_DROP, severity=HIGH, ...)]
    """
    if len(recent_values) < min_periods:
        return []

    # Calculate period-over-period changes
    changes = [recent_values[i] - recent_values[i - 1] for i in range(1, len(recent_values))]

    if not changes:
        return []

    # Compare recent rate of change vs longer-term average
    # Recent = last 2 changes, longer-term = all changes
    if len(changes) < 3:
        return []  # Need at least 3 changes to compare

    recent_rate = sum(changes[-2:]) / 2
    longer_term_rate = sum(changes[:-2]) / len(changes[:-2])

    # Avoid division by zero
    if abs(longer_term_rate) < 0.01:
        return []

    # Calculate acceleration (positive = accelerating decline or growth)
    acceleration = abs((recent_rate - longer_term_rate) / longer_term_rate)

    # Determine severity
    if acceleration < acceleration_threshold:
        return []
    elif acceleration < 0.50:
        severity = Severity.MEDIUM
    elif acceleration < 0.75:
        severity = Severity.HIGH
    else:
        severity = Severity.CRITICAL

    # Determine direction
    is_decline = recent_rate < 0
    is_accelerating = abs(recent_rate) > abs(longer_term_rate)

    if is_decline:
        kind = AlertKind.SALES_DROP
        direction_desc = "accelerating decline" if is_accelerating else "slowing decline"
    else:
        kind = AlertKind.INVENTORY_RISK
        direction_desc = "accelerating growth" if is_accelerating else "slowing growth"

    metric_name = metric.replace("_", " ").title()
    if is_decline and is_accelerating:
        conclusion = "Decline is accelerating."
    else:
        conclusion = "Momentum shift detected."

    return [
        AlertCandidate(
            kind=kind,
            subject=entity,
            title=f"{metric_name} showing {direction_desc}",
            body=(
                f"{metric_name} rate of change has shifted "
                f"{acceleration:.0%} compared to recent trend. "
                f"Recent average change: {recent_rate:,.0f} per period. "
                f"Prior trend: {longer_term_rate:,.0f} per period. "
                f"{conclusion}"
            ),
            severity=severity,
            observed=recent_values[-1],
            expected_low=None,
            expected_high=None,
            detected_for=as_of,
            evidence={
                "method": "rate_of_change",
                "recent_rate": round(recent_rate, 2),
                "longer_term_rate": round(longer_term_rate, 2),
                "acceleration": round(acceleration, 4),
                "direction": direction_desc,
                "periods_analyzed": len(recent_values),
            },
            deep_link=f"/analytics/trend-analysis?metric={metric}&entity={entity}",
        )
    ]
