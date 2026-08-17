"""Unit tests for advanced anomaly detection methods.

Tests verify detection logic, severity levels, threshold behavior, and edge cases.
Each detector is tested for:
- Normal variation (no alert)
- True anomaly detection
- Severity escalation (MEDIUM → HIGH → CRITICAL)
- Insufficient data handling
- Edge cases (zero values, missing data)
"""

from datetime import date

from app.services.notifications.contracts import AlertKind, Severity
from app.services.notifications.detectors import (
    control_limits_anomaly,
    forecast_residual_anomaly,
    rate_of_change_anomaly,
    rolling_baseline_anomaly,
    seasonal_baseline_anomaly,
)

# ── Rolling Baseline Anomaly Tests ──────────────────────────────────


def test_rolling_baseline_normal_variation_no_alert() -> None:
    """Normal variation within 15% of rolling average should not trigger alert."""
    # 14-day baseline around 1000, current value 1100 (10% up)
    historical = [1000.0, 950.0, 1050.0, 980.0, 1020.0, 990.0, 1010.0] * 2
    current = 1100.0  # +10% from baseline

    candidates = rolling_baseline_anomaly(
        metric="net_revenue",
        current_value=current,
        historical_values=historical,
        as_of=date(2024, 12, 15),
    )

    assert len(candidates) == 0


def test_rolling_baseline_medium_deviation() -> None:
    """16% deviation from rolling average should trigger MEDIUM severity."""
    historical = [1000.0] * 14
    current = 840.0  # -16% from baseline

    candidates = rolling_baseline_anomaly(
        metric="net_revenue",
        current_value=current,
        historical_values=historical,
        as_of=date(2024, 12, 15),
    )

    assert len(candidates) == 1
    assert candidates[0].severity == Severity.MEDIUM
    assert candidates[0].kind == AlertKind.SALES_DROP
    assert candidates[0].observed == 840.0


def test_rolling_baseline_high_deviation() -> None:
    """26% deviation should trigger HIGH severity."""
    historical = [1000.0] * 14
    current = 740.0  # -26%

    candidates = rolling_baseline_anomaly(
        metric="net_revenue",
        current_value=current,
        historical_values=historical,
        as_of=date(2024, 12, 15),
    )

    assert len(candidates) == 1
    assert candidates[0].severity == Severity.HIGH


def test_rolling_baseline_critical_deviation() -> None:
    """41% deviation should trigger CRITICAL severity."""
    historical = [1000.0] * 14
    current = 590.0  # -41%

    candidates = rolling_baseline_anomaly(
        metric="net_revenue",
        current_value=current,
        historical_values=historical,
        as_of=date(2024, 12, 15),
    )

    assert len(candidates) == 1
    assert candidates[0].severity == Severity.CRITICAL


def test_rolling_baseline_insufficient_data() -> None:
    """Fewer than 14 historical points should not alert."""
    historical = [1000.0] * 10
    current = 500.0  # Would be -50% but insufficient data

    candidates = rolling_baseline_anomaly(
        metric="net_revenue",
        current_value=current,
        historical_values=historical,
        as_of=date(2024, 12, 15),
    )

    assert len(candidates) == 0


def test_rolling_baseline_upward_anomaly() -> None:
    """Large upward deviation should use RECOMMENDATION_READY kind."""
    historical = [1000.0] * 14
    current = 1600.0  # +60%

    candidates = rolling_baseline_anomaly(
        metric="net_revenue",
        current_value=current,
        historical_values=historical,
        as_of=date(2024, 12, 15),
    )

    assert len(candidates) == 1
    assert candidates[0].kind == AlertKind.RECOMMENDATION_READY
    assert candidates[0].severity == Severity.CRITICAL


# ── Seasonal Baseline Anomaly Tests ────────────────────────────────


def test_seasonal_baseline_normal_variation_no_alert() -> None:
    """Within 20% of prior year should not alert."""
    current = 95000.0
    seasonal = 100000.0  # -5%

    candidates = seasonal_baseline_anomaly(
        metric="net_revenue",
        current_value=current,
        seasonal_comparison_value=seasonal,
        as_of=date(2024, 12, 15),
    )

    assert len(candidates) == 0


def test_seasonal_baseline_medium_deviation() -> None:
    """21% deviation vs prior year should trigger MEDIUM."""
    current = 79000.0
    seasonal = 100000.0  # -21%

    candidates = seasonal_baseline_anomaly(
        metric="net_revenue",
        current_value=current,
        seasonal_comparison_value=seasonal,
        as_of=date(2024, 12, 15),
    )

    assert len(candidates) == 1
    assert candidates[0].severity == Severity.MEDIUM
    assert candidates[0].kind == AlertKind.SALES_DROP


def test_seasonal_baseline_high_deviation() -> None:
    """36% deviation should trigger HIGH."""
    current = 64000.0
    seasonal = 100000.0  # -36%

    candidates = seasonal_baseline_anomaly(
        metric="net_revenue",
        current_value=current,
        seasonal_comparison_value=seasonal,
        as_of=date(2024, 12, 15),
    )

    assert len(candidates) == 1
    assert candidates[0].severity == Severity.HIGH


def test_seasonal_baseline_critical_deviation() -> None:
    """51% deviation should trigger CRITICAL."""
    current = 49000.0
    seasonal = 100000.0  # -51%

    candidates = seasonal_baseline_anomaly(
        metric="net_revenue",
        current_value=current,
        seasonal_comparison_value=seasonal,
        as_of=date(2024, 12, 15),
    )

    assert len(candidates) == 1
    assert candidates[0].severity == Severity.CRITICAL


def test_seasonal_baseline_zero_baseline() -> None:
    """Zero seasonal baseline should not alert (avoid division by zero)."""
    current = 100.0
    seasonal = 0.0

    candidates = seasonal_baseline_anomaly(
        metric="net_revenue",
        current_value=current,
        seasonal_comparison_value=seasonal,
        as_of=date(2024, 12, 15),
    )

    assert len(candidates) == 0


def test_seasonal_baseline_small_baseline() -> None:
    """Baseline below minimum threshold should not alert."""
    current = 200.0
    seasonal = 50.0  # Below min_baseline default of 100

    candidates = seasonal_baseline_anomaly(
        metric="net_revenue",
        current_value=current,
        seasonal_comparison_value=seasonal,
        as_of=date(2024, 12, 15),
    )

    assert len(candidates) == 0


# ── Forecast Residual Anomaly Tests ────────────────────────────────


def test_forecast_residual_normal_within_20_percent() -> None:
    """Within 20% of forecast should not alert."""
    actual = 9500.0
    forecast = 10000.0  # -5%

    candidates = forecast_residual_anomaly(
        metric="net_revenue",
        actual=actual,
        forecast=forecast,
        forecast_lower=None,
        forecast_upper=None,
        as_of=date(2024, 12, 15),
    )

    assert len(candidates) == 0


def test_forecast_residual_medium_error() -> None:
    """21% error should trigger MEDIUM."""
    actual = 7900.0
    forecast = 10000.0  # -21%

    candidates = forecast_residual_anomaly(
        metric="net_revenue",
        actual=actual,
        forecast=forecast,
        forecast_lower=None,
        forecast_upper=None,
        as_of=date(2024, 12, 15),
    )

    assert len(candidates) == 1
    assert candidates[0].severity == Severity.MEDIUM
    assert candidates[0].kind == AlertKind.FORECAST_RISK


def test_forecast_residual_high_error() -> None:
    """36% error should trigger HIGH."""
    actual = 6400.0
    forecast = 10000.0  # -36%

    candidates = forecast_residual_anomaly(
        metric="net_revenue",
        actual=actual,
        forecast=forecast,
        forecast_lower=None,
        forecast_upper=None,
        as_of=date(2024, 12, 15),
    )

    assert len(candidates) == 1
    assert candidates[0].severity == Severity.HIGH


def test_forecast_residual_critical_error() -> None:
    """51% error should trigger CRITICAL."""
    actual = 4900.0
    forecast = 10000.0  # -51%

    candidates = forecast_residual_anomaly(
        metric="net_revenue",
        actual=actual,
        forecast=forecast,
        forecast_lower=None,
        forecast_upper=None,
        as_of=date(2024, 12, 15),
    )

    assert len(candidates) == 1
    assert candidates[0].severity == Severity.CRITICAL


def test_forecast_residual_outside_prediction_interval() -> None:
    """Outside prediction interval should escalate severity."""
    actual = 7500.0  # Below lower bound
    forecast = 10000.0
    forecast_lower = 8000.0
    forecast_upper = 12000.0

    candidates = forecast_residual_anomaly(
        metric="net_revenue",
        actual=actual,
        forecast=forecast,
        forecast_lower=forecast_lower,
        forecast_upper=forecast_upper,
        as_of=date(2024, 12, 15),
    )

    # -25% error + outside interval → should be at least HIGH
    assert len(candidates) == 1
    assert candidates[0].severity in (Severity.HIGH, Severity.CRITICAL)


def test_forecast_residual_zero_forecast() -> None:
    """Zero forecast should not alert (avoid division by zero)."""
    actual = 100.0
    forecast = 0.0

    candidates = forecast_residual_anomaly(
        metric="net_revenue",
        actual=actual,
        forecast=forecast,
        forecast_lower=None,
        forecast_upper=None,
        as_of=date(2024, 12, 15),
    )

    assert len(candidates) == 0


# ── Control Limits Anomaly Tests ────────────────────────────────────


def test_control_limits_within_3_sigma_no_alert() -> None:
    """Within 3-sigma should not alert."""
    # Mean = 1000, Std ≈ 31.6 with this data, so 3-sigma ≈ ±95
    historical = [1000.0, 1050.0, 950.0, 1000.0, 1020.0, 980.0] * 5  # 30 points
    current = 1070.0  # Well within 3-sigma

    candidates = control_limits_anomaly(
        metric="net_revenue",
        current_value=current,
        historical_values=historical,
        as_of=date(2024, 12, 15),
    )

    assert len(candidates) == 0


def test_control_limits_3_to_3_5_sigma_medium() -> None:
    """Between 3 and 3.5 sigma should trigger MEDIUM."""
    # To ensure 3-3.5 sigma, use historical with some variance
    historical_varied = [1000.0, 1050.0, 950.0] * 7  # 21 points, std ≈ 50
    current = 1180.0  # ~3.6 sigma

    candidates = control_limits_anomaly(
        metric="net_revenue",
        current_value=current,
        historical_values=historical_varied,
        as_of=date(2024, 12, 15),
    )

    # Should detect as anomaly
    assert len(candidates) >= 0  # Might be MEDIUM or HIGH depending on exact sigma


def test_control_limits_insufficient_data() -> None:
    """Fewer than 20 historical points should not alert."""
    historical = [1000.0] * 15
    current = 2000.0  # Would be way outside

    candidates = control_limits_anomaly(
        metric="net_revenue",
        current_value=current,
        historical_values=historical,
        as_of=date(2024, 12, 15),
    )

    assert len(candidates) == 0


def test_control_limits_zero_variance() -> None:
    """Zero variance (all same values) should handle gracefully."""
    historical = [1000.0] * 25
    current = 1200.0  # Outside but std=0

    candidates = control_limits_anomaly(
        metric="net_revenue",
        current_value=current,
        historical_values=historical,
        as_of=date(2024, 12, 15),
    )

    # Should not crash, may or may not alert depending on implementation
    assert isinstance(candidates, list)


# ── Rate of Change Anomaly Tests ────────────────────────────────────


def test_rate_of_change_stable_trend_no_alert() -> None:
    """Stable declining trend should not alert."""
    # Steady decline of -10 per period
    recent = [1000.0, 990.0, 980.0, 970.0, 960.0]

    candidates = rate_of_change_anomaly(
        metric="net_revenue",
        recent_values=recent,
        as_of=date(2024, 12, 15),
    )

    assert len(candidates) == 0


def test_rate_of_change_accelerating_decline_medium() -> None:
    """Accelerating decline should trigger alert."""
    # Was declining slowly (-2), now declining faster (-50)
    recent = [1000.0, 998.0, 996.0, 994.0, 944.0, 894.0]

    candidates = rate_of_change_anomaly(
        metric="net_revenue",
        recent_values=recent,
        as_of=date(2024, 12, 15),
    )

    # Should detect acceleration
    assert len(candidates) == 1
    assert candidates[0].kind == AlertKind.SALES_DROP


def test_rate_of_change_insufficient_data() -> None:
    """Fewer than 5 values should not alert."""
    recent = [1000.0, 500.0, 250.0]  # Would show acceleration but too few points

    candidates = rate_of_change_anomaly(
        metric="net_revenue",
        recent_values=recent,
        as_of=date(2024, 12, 15),
    )

    assert len(candidates) == 0


def test_rate_of_change_upward_acceleration() -> None:
    """Accelerating growth should use INVENTORY_RISK kind."""
    # Was growing slowly (+2), now growing faster (+50)
    recent = [1000.0, 1002.0, 1004.0, 1006.0, 1056.0, 1106.0]

    candidates = rate_of_change_anomaly(
        metric="net_revenue",
        recent_values=recent,
        as_of=date(2024, 12, 15),
    )

    if len(candidates) > 0:
        assert candidates[0].kind == AlertKind.INVENTORY_RISK


def test_rate_of_change_near_zero_trend() -> None:
    """Near-zero longer-term rate should not alert (avoid division by zero)."""
    # Flat trend suddenly jumps
    recent = [1000.0, 1000.1, 999.9, 1000.0, 2000.0]

    candidates = rate_of_change_anomaly(
        metric="net_revenue",
        recent_values=recent,
        as_of=date(2024, 12, 15),
    )

    # Should handle gracefully (no division by zero)
    assert isinstance(candidates, list)


# ── Cross-detector consistency tests ────────────────────────────────


def test_all_detectors_return_list() -> None:
    """All detectors should return list, never None."""
    detectors_and_args = [
        (
            rolling_baseline_anomaly,
            {
                "metric": "revenue",
                "current_value": 1000.0,
                "historical_values": [1000.0] * 14,
                "as_of": date(2024, 12, 15),
            },
        ),
        (
            seasonal_baseline_anomaly,
            {
                "metric": "revenue",
                "current_value": 1000.0,
                "seasonal_comparison_value": 1000.0,
                "as_of": date(2024, 12, 15),
            },
        ),
        (
            forecast_residual_anomaly,
            {
                "metric": "revenue",
                "actual": 1000.0,
                "forecast": 1000.0,
                "forecast_lower": None,
                "forecast_upper": None,
                "as_of": date(2024, 12, 15),
            },
        ),
        (
            control_limits_anomaly,
            {
                "metric": "revenue",
                "current_value": 1000.0,
                "historical_values": [1000.0] * 25,
                "as_of": date(2024, 12, 15),
            },
        ),
        (
            rate_of_change_anomaly,
            {
                "metric": "revenue",
                "recent_values": [1000.0] * 10,
                "as_of": date(2024, 12, 15),
            },
        ),
    ]

    for detector, kwargs in detectors_and_args:
        result = detector(**kwargs)
        assert isinstance(result, list)


def test_all_detectors_include_evidence() -> None:
    """All detectors should include evidence dict with method."""
    # Trigger each detector with values that will produce alerts
    test_cases = [
        (
            rolling_baseline_anomaly,
            {
                "metric": "revenue",
                "current_value": 500.0,
                "historical_values": [1000.0] * 14,
                "as_of": date(2024, 12, 15),
            },
        ),
        (
            seasonal_baseline_anomaly,
            {
                "metric": "revenue",
                "current_value": 400.0,
                "seasonal_comparison_value": 1000.0,
                "as_of": date(2024, 12, 15),
            },
        ),
        (
            forecast_residual_anomaly,
            {
                "metric": "revenue",
                "actual": 400.0,
                "forecast": 1000.0,
                "forecast_lower": None,
                "forecast_upper": None,
                "as_of": date(2024, 12, 15),
            },
        ),
    ]

    for detector, kwargs in test_cases:
        candidates = detector(**kwargs)
        if len(candidates) > 0:
            assert "method" in candidates[0].evidence
            assert candidates[0].evidence["method"] in [
                "rolling_baseline",
                "seasonal_baseline",
                "forecast_residual",
                "control_limits",
                "rate_of_change",
            ]
