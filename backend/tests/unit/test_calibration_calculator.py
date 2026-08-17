"""Test calibration calculator pure functions."""

import pytest

from app.services.calibration import calculator
from app.services.calibration.models import CalibrationMetrics


def test_calculate_metrics_with_zero_outcomes():
    """Empty outcome list returns empty metrics."""
    metrics = calculator.calculate_metrics([])

    assert metrics.sample_size == 0
    assert metrics.mean_realization_ratio is None
    assert metrics.is_statistically_significant is False
    assert metrics.systematically_overestimates is False
    assert metrics.systematically_underestimates is False


def test_calculate_metrics_with_perfect_realization():
    """Realization ratio = 1.0 when realized equals expected."""
    outcomes = [
        {
            "realized_impact": 100.0,
            "expected_impact": 100.0,
            "realization_ratio": 1.0,
            "absolute_error": 0.0,
            "direction_correct": True,
        }
    ] * 25  # Enough for significance

    metrics = calculator.calculate_metrics(outcomes)

    assert metrics.sample_size == 25
    assert metrics.mean_realization_ratio == 1.0
    assert metrics.median_realization_ratio == 1.0
    assert metrics.mean_absolute_error == 0.0
    assert metrics.mean_bias == 0.0
    assert metrics.bias_percentage == 0.0
    assert metrics.direction_accuracy == 1.0
    assert metrics.success_rate == 1.0
    assert metrics.is_statistically_significant is True
    assert metrics.is_well_calibrated is True


def test_calculate_metrics_with_overestimation():
    """Negative bias when realized < expected."""
    outcomes = [
        {
            "realized_impact": 50.0,
            "expected_impact": 100.0,
            "realization_ratio": 0.5,
            "absolute_error": 50.0,
            "direction_correct": True,
        }
    ] * 25

    metrics = calculator.calculate_metrics(outcomes)

    assert metrics.sample_size == 25
    assert metrics.mean_realization_ratio == 0.5
    assert metrics.mean_bias == -50.0
    assert metrics.bias_percentage == pytest.approx(-50.0, abs=0.1)
    assert metrics.systematically_overestimates is True
    assert metrics.systematically_underestimates is False
    assert metrics.is_well_calibrated is False


def test_calculate_metrics_with_underestimation():
    """Positive bias when realized > expected."""
    outcomes = [
        {
            "realized_impact": 150.0,
            "expected_impact": 100.0,
            "realization_ratio": 1.5,
            "absolute_error": 50.0,
            "direction_correct": True,
        }
    ] * 25

    metrics = calculator.calculate_metrics(outcomes)

    assert metrics.mean_bias == 50.0
    assert metrics.bias_percentage == pytest.approx(50.0, abs=0.1)
    assert metrics.systematically_overestimates is False
    assert metrics.systematically_underestimates is True


def test_calculate_metrics_handles_zero_expected():
    """MAPE and realization_ratio handle zero expected gracefully."""
    outcomes = [
        {
            "realized_impact": 100.0,
            "expected_impact": 0.0,
            "realization_ratio": None,
            "absolute_error": 100.0,
            "direction_correct": False,
        }
    ]

    metrics = calculator.calculate_metrics(outcomes)

    # Should not crash, should return None for ratio-based metrics
    assert metrics.mean_absolute_percentage_error is None
    assert metrics.bias_percentage is None
    assert metrics.mean_absolute_error == 100.0


def test_calculate_metrics_direction_accuracy():
    """Direction accuracy calculates correctly."""
    outcomes = [
        {
            "realized_impact": 10,
            "expected_impact": 10,
            "realization_ratio": 1.0,
            "absolute_error": 0,
            "direction_correct": True,
        },
        {
            "realized_impact": -5,
            "expected_impact": -5,
            "realization_ratio": 1.0,
            "absolute_error": 0,
            "direction_correct": True,
        },
        {
            "realized_impact": 5,
            "expected_impact": -5,
            "realization_ratio": -1.0,
            "absolute_error": 10,
            "direction_correct": False,
        },
    ] * 10  # 30 outcomes: 20 correct, 10 incorrect

    metrics = calculator.calculate_metrics(outcomes)

    assert metrics.sample_size == 30
    assert metrics.direction_correct_count == 20
    assert metrics.direction_accuracy == pytest.approx(0.6667, abs=0.01)


def test_calculate_metrics_success_rate():
    """Success rate uses 70% threshold."""
    outcomes = [
        {
            "realized_impact": 80,
            "expected_impact": 100,
            "realization_ratio": 0.8,
            "absolute_error": 20,
            "direction_correct": True,
        },  # Success: 80% >= 70%
        {
            "realized_impact": 60,
            "expected_impact": 100,
            "realization_ratio": 0.6,
            "absolute_error": 40,
            "direction_correct": True,
        },  # Failure: 60% < 70%
    ] * 15  # 30 outcomes: 15 successes, 15 failures

    metrics = calculator.calculate_metrics(outcomes)

    assert metrics.sample_size == 30
    assert metrics.success_count == 15
    assert metrics.success_rate == 0.5


def test_confidence_band_calibration_well_calibrated():
    """Confidence 0.8-1.0 with 85% success is reasonably calibrated."""
    outcomes = []
    for i in range(40):  # Sufficient samples
        # 34 successes (85%), 6 failures
        realized = 85.0 if i < 34 else 50.0
        expected = 100.0
        outcomes.append(
            {
                "realized_impact": realized,
                "expected_impact": expected,
                "realization_ratio": realized / expected,
                "absolute_error": abs(realized - expected),
                "direction_correct": True,
            }
        )

    calibration = calculator.calculate_confidence_band_calibration(
        outcomes, confidence_min=0.8, confidence_max=1.0
    )

    assert calibration.sample_size == 40
    assert calibration.expected_success_rate == 0.90  # Midpoint of 0.8-1.0
    assert 0.84 <= calibration.actual_success_rate <= 0.86  # ~85%
    assert calibration.calibration_error < 0.10
    assert calibration.is_statistically_significant is True
    assert calibration.is_well_calibrated is True
    assert calibration.is_overconfident is False


def test_confidence_band_calibration_overconfident():
    """Overconfident when actual << expected."""
    outcomes = []
    for i in range(40):
        # Only 60% success, but confidence band is 0.8-1.0 (expects 90%)
        realized = 85.0 if i < 24 else 50.0  # 24/40 = 60%
        expected = 100.0
        outcomes.append(
            {
                "realized_impact": realized,
                "expected_impact": expected,
                "realization_ratio": realized / expected,
                "absolute_error": abs(realized - expected),
                "direction_correct": True,
            }
        )

    calibration = calculator.calculate_confidence_band_calibration(
        outcomes, confidence_min=0.8, confidence_max=1.0
    )

    assert calibration.expected_success_rate == pytest.approx(0.90, abs=0.001)
    assert calibration.actual_success_rate == pytest.approx(0.60, abs=0.001)
    assert calibration.calibration_error == pytest.approx(0.30, abs=0.001)
    assert calibration.is_overconfident is True


def test_identify_systematic_biases():
    """Detects generators with >10% bias."""
    good_gen = CalibrationMetrics(
        sample_size=25,
        mean_realization_ratio=0.98,
        median_realization_ratio=0.97,
        mean_absolute_error=50.0,
        mean_absolute_percentage_error=0.05,
        mean_bias=-20.0,
        bias_percentage=-2.0,  # Within ±10%
        direction_correct_count=23,
        direction_accuracy=0.92,
        success_count=22,
        success_rate=0.88,
        is_statistically_significant=True,
    )

    overestimator = CalibrationMetrics(
        sample_size=30,
        mean_realization_ratio=0.65,
        median_realization_ratio=0.64,
        mean_absolute_error=350.0,
        mean_absolute_percentage_error=0.35,
        mean_bias=-350.0,
        bias_percentage=-35.0,  # Overestimates
        direction_correct_count=25,
        direction_accuracy=0.83,
        success_count=15,
        success_rate=0.50,
        is_statistically_significant=True,
    )

    underestimator = CalibrationMetrics(
        sample_size=28,
        mean_realization_ratio=1.25,
        median_realization_ratio=1.24,
        mean_absolute_error=250.0,
        mean_absolute_percentage_error=0.25,
        mean_bias=250.0,
        bias_percentage=25.0,  # Underestimates
        direction_correct_count=26,
        direction_accuracy=0.93,
        success_count=27,
        success_rate=0.96,
        is_statistically_significant=True,
    )

    generator_metrics = {
        "inventory": good_gen,
        "pricing": overestimator,
        "promotion": underestimator,
    }

    biases = calculator.identify_systematic_biases(generator_metrics)

    assert "inventory" not in biases
    assert biases["pricing"] == "overestimates"
    assert biases["promotion"] == "underestimates"


def test_rank_generators_by_quality():
    """Quality score ranks generators correctly."""
    high_quality = CalibrationMetrics(
        sample_size=50,
        mean_realization_ratio=0.95,
        median_realization_ratio=0.94,
        mean_absolute_error=100.0,
        mean_absolute_percentage_error=0.08,
        mean_bias=-50.0,
        bias_percentage=-5.0,
        direction_correct_count=48,
        direction_accuracy=0.96,
        success_count=47,
        success_rate=0.94,
        is_statistically_significant=True,
    )

    low_quality = CalibrationMetrics(
        sample_size=30,
        mean_realization_ratio=0.60,
        median_realization_ratio=0.58,
        mean_absolute_error=400.0,
        mean_absolute_percentage_error=0.40,
        mean_bias=-400.0,
        bias_percentage=-40.0,
        direction_correct_count=18,
        direction_accuracy=0.60,
        success_count=12,
        success_rate=0.40,
        is_statistically_significant=True,
    )

    generator_metrics = {
        "inventory": high_quality,
        "pricing": low_quality,
    }

    ranked = calculator.rank_generators_by_quality(generator_metrics)

    assert len(ranked) == 2
    assert ranked[0][0] == "inventory"  # Best first
    assert ranked[1][0] == "pricing"
    assert ranked[0][1] > ranked[1][1]  # Quality scores descending


def test_segment_by_field():
    """Groups outcomes by field value."""
    outcomes = [
        {"category": "inventory", "value": 100},
        {"category": "pricing", "value": 200},
        {"category": "inventory", "value": 150},
    ]

    segments = calculator.segment_by_field(outcomes, "category")

    assert len(segments) == 2
    assert len(segments["inventory"]) == 2
    assert len(segments["pricing"]) == 1


def test_segment_by_confidence_band():
    """Groups outcomes into confidence bands."""
    outcomes = [
        {"confidence": "low", "value": 1},  # Should not be included (string confidence)
        {"confidence": 0.15, "value": 2},  # Band 0.0-0.2
        {"confidence": 0.55, "value": 3},  # Band 0.4-0.6
        {"confidence": 0.95, "value": 4},  # Band 0.8-1.0
    ]

    # Note: segment_by_confidence_band expects numeric confidence values
    # Filter out non-numeric values for this test
    numeric_outcomes = [o for o in outcomes if isinstance(o.get("confidence"), (int, float))]

    segments = calculator.segment_by_confidence_band(numeric_outcomes, band_width=0.2)

    # Check that we have 3 bands (allowing for floating point imprecision in keys)
    assert len(segments) == 3

    # Find bands by checking min values approximately
    low_band = [k for k in segments if abs(k[0] - 0.0) < 0.01]
    mid_band = [k for k in segments if abs(k[0] - 0.4) < 0.01]
    high_band = [k for k in segments if abs(k[0] - 0.8) < 0.01]

    assert len(low_band) == 1
    assert len(mid_band) == 1
    assert len(high_band) == 1

    assert len(segments[low_band[0]]) == 1
    assert len(segments[mid_band[0]]) == 1
    assert len(segments[high_band[0]]) == 1


def test_empty_metrics():
    """Empty metrics have expected structure."""
    metrics = calculator._empty_metrics()

    assert metrics.sample_size == 0
    assert metrics.mean_realization_ratio is None
    assert metrics.mean_absolute_error == 0.0
    assert metrics.is_statistically_significant is False
