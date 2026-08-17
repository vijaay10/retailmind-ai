"""Calibration metric calculator — pure functions over outcome data.

Every function here takes raw outcome data and returns calibration metrics.
No I/O, no database queries, just arithmetic. That makes them fast to test
and easy to reason about.
"""

import statistics
from typing import Any

from app.services.calibration.models import (
    MIN_CONFIDENCE_CALIBRATION_SAMPLES,
    MIN_SAMPLE_SIZE,
    CalibrationMetrics,
    ConfidenceBandCalibration,
)

#: Success threshold: outcome is "successful" if realization >= this ratio
SUCCESS_THRESHOLD = 0.70


def calculate_metrics(outcomes: list[dict[str, Any]]) -> CalibrationMetrics:
    """Calculate calibration metrics from a list of measured outcomes.

    Args:
        outcomes: List of outcome dicts with keys:
            - realized_impact: float
            - expected_impact: float
            - realization_ratio: float | None
            - absolute_error: float
            - direction_correct: bool

    Returns:
        CalibrationMetrics with all computed values.
    """
    sample_size = len(outcomes)

    if sample_size == 0:
        return _empty_metrics()

    # Extract values
    realized = [float(o.get("realized_impact") or 0.0) for o in outcomes]
    expected = [float(o.get("expected_impact") or 0.0) for o in outcomes]
    realization_ratios = [
        float(o["realization_ratio"]) for o in outcomes if o.get("realization_ratio") is not None
    ]
    absolute_errors = [float(o.get("absolute_error") or 0.0) for o in outcomes]
    direction_correct = [bool(o.get("direction_correct", False)) for o in outcomes]

    # Realization metrics
    mean_realization = statistics.mean(realization_ratios) if realization_ratios else None
    median_realization = statistics.median(realization_ratios) if realization_ratios else None

    # Error metrics
    mean_abs_error = statistics.mean(absolute_errors)

    # MAPE: handle zero expected values
    mape_values = []
    for r, e in zip(realized, expected, strict=True):
        if abs(e) > 0.01:  # Avoid division by near-zero
            mape_values.append(abs((r - e) / e))
    mean_absolute_percentage_error = statistics.mean(mape_values) if mape_values else None

    # Bias metrics
    biases = [r - e for r, e in zip(realized, expected, strict=True)]
    mean_bias = statistics.mean(biases)

    total_expected = sum(expected)
    bias_percentage = (sum(biases) / total_expected * 100.0) if abs(total_expected) > 0.01 else None

    # Direction accuracy
    direction_correct_count = sum(direction_correct)
    direction_accuracy = direction_correct_count / sample_size

    # Success rate (>= 70% of expected)
    successes = [
        r >= (e * SUCCESS_THRESHOLD)
        for r, e in zip(realized, expected, strict=True)
        if abs(e) > 0.01
    ]
    success_count = sum(successes)
    success_rate = success_count / sample_size if sample_size > 0 else 0.0

    # Statistical significance
    is_significant = sample_size >= MIN_SAMPLE_SIZE

    return CalibrationMetrics(
        sample_size=sample_size,
        mean_realization_ratio=mean_realization,
        median_realization_ratio=median_realization,
        mean_absolute_error=mean_abs_error,
        mean_absolute_percentage_error=mean_absolute_percentage_error,
        mean_bias=mean_bias,
        bias_percentage=bias_percentage,
        direction_correct_count=direction_correct_count,
        direction_accuracy=direction_accuracy,
        success_count=success_count,
        success_rate=success_rate,
        is_statistically_significant=is_significant,
    )


def calculate_confidence_band_calibration(
    outcomes: list[dict[str, Any]],
    confidence_min: float,
    confidence_max: float,
) -> ConfidenceBandCalibration:
    """Calculate calibration for one confidence band.

    Args:
        outcomes: Outcomes with 'confidence' in [confidence_min, confidence_max]
        confidence_min: Lower bound of confidence band
        confidence_max: Upper bound of confidence band

    Returns:
        ConfidenceBandCalibration showing expected vs actual success rate.
    """
    sample_size = len(outcomes)

    if sample_size == 0:
        expected_rate = (confidence_min + confidence_max) / 2.0
        return ConfidenceBandCalibration(
            confidence_min=confidence_min,
            confidence_max=confidence_max,
            sample_size=0,
            expected_success_rate=expected_rate,
            actual_success_rate=0.0,
            calibration_error=expected_rate,
            mean_realization_ratio=None,
            is_statistically_significant=False,
        )

    # Expected success rate = midpoint of confidence band
    expected_rate = (confidence_min + confidence_max) / 2.0

    # Actual success rate
    realized = [float(o.get("realized_impact") or 0.0) for o in outcomes]
    expected = [float(o.get("expected_impact") or 0.0) for o in outcomes]
    successes = [
        r >= (e * SUCCESS_THRESHOLD)
        for r, e in zip(realized, expected, strict=True)
        if abs(e) > 0.01
    ]
    actual_rate = sum(successes) / sample_size if sample_size > 0 else 0.0

    # Calibration error
    calibration_error = abs(expected_rate - actual_rate)

    # Mean realization ratio
    realization_ratios = [
        float(o["realization_ratio"]) for o in outcomes if o.get("realization_ratio") is not None
    ]
    mean_realization = statistics.mean(realization_ratios) if realization_ratios else None

    # Statistical significance
    is_significant = sample_size >= MIN_CONFIDENCE_CALIBRATION_SAMPLES

    return ConfidenceBandCalibration(
        confidence_min=confidence_min,
        confidence_max=confidence_max,
        sample_size=sample_size,
        expected_success_rate=expected_rate,
        actual_success_rate=actual_rate,
        calibration_error=calibration_error,
        mean_realization_ratio=mean_realization,
        is_statistically_significant=is_significant,
    )


def segment_by_field(outcomes: list[dict[str, Any]], field: str) -> dict[str, list[dict[str, Any]]]:
    """Group outcomes by a field value.

    Args:
        outcomes: List of outcome dicts
        field: Field name to group by (e.g., 'category', 'baseline_method')

    Returns:
        Dict mapping field value to list of outcomes with that value.
    """
    segments: dict[str, list[dict[str, Any]]] = {}

    for outcome in outcomes:
        value = outcome.get(field, "unknown")
        if value not in segments:
            segments[value] = []
        segments[value].append(outcome)

    return segments


def segment_by_confidence_band(
    outcomes: list[dict[str, Any]], band_width: float = 0.2
) -> dict[tuple[float, float], list[dict[str, Any]]]:
    """Group outcomes by confidence band.

    Args:
        outcomes: List of outcome dicts with 'confidence' field
        band_width: Width of each band (default 0.2 for 0.0-0.2, 0.2-0.4, etc.)

    Returns:
        Dict mapping (min, max) tuples to list of outcomes in that band.
    """
    segments: dict[tuple[float, float], list[dict[str, Any]]] = {}

    for outcome in outcomes:
        confidence = outcome.get("confidence")
        if confidence is None:
            continue

        # Determine which band this outcome falls into
        band_index = int(confidence / band_width)
        band_min = band_index * band_width
        band_max = min(1.0, band_min + band_width)
        band_key = (band_min, band_max)

        if band_key not in segments:
            segments[band_key] = []
        segments[band_key].append(outcome)

    return segments


def identify_systematic_biases(
    generator_metrics: dict[str, CalibrationMetrics], bias_threshold: float = 10.0
) -> dict[str, str]:
    """Identify generators with systematic over/underestimation.

    Args:
        generator_metrics: Dict mapping generator name to its CalibrationMetrics
        bias_threshold: Bias % threshold to flag as systematic (default 10%)

    Returns:
        Dict mapping generator name to bias type:
        - "overestimates": bias_percentage < -10%
        - "underestimates": bias_percentage > +10%
    """
    biases: dict[str, str] = {}

    for generator, metrics in generator_metrics.items():
        if not metrics.is_statistically_significant:
            continue

        if metrics.bias_percentage is None:
            continue

        if metrics.bias_percentage < -bias_threshold:
            biases[generator] = "overestimates"
        elif metrics.bias_percentage > bias_threshold:
            biases[generator] = "underestimates"

    return biases


def rank_generators_by_quality(
    generator_metrics: dict[str, CalibrationMetrics],
) -> list[tuple[str, float]]:
    """Rank generators by overall quality score.

    Quality = 0.4 * direction_accuracy + 0.3 * calibration + 0.3 * success_rate

    Args:
        generator_metrics: Dict mapping generator name to its CalibrationMetrics

    Returns:
        List of (generator_name, quality_score) tuples, sorted best to worst.
        Only includes generators with sufficient sample size.
    """
    scores: list[tuple[str, float]] = []

    for generator, metrics in generator_metrics.items():
        if not metrics.is_statistically_significant:
            continue

        direction_score = metrics.direction_accuracy
        calibration_score = 1.0 - min(1.0, abs(metrics.bias_percentage or 0.0) / 100.0)
        success_score = metrics.success_rate

        quality = 0.4 * direction_score + 0.3 * calibration_score + 0.3 * success_score
        scores.append((generator, quality))

    # Sort by quality descending
    scores.sort(key=lambda x: x[1], reverse=True)
    return scores


def _empty_metrics() -> CalibrationMetrics:
    """Return empty metrics for zero-outcome case."""
    return CalibrationMetrics(
        sample_size=0,
        mean_realization_ratio=None,
        median_realization_ratio=None,
        mean_absolute_error=0.0,
        mean_absolute_percentage_error=None,
        mean_bias=0.0,
        bias_percentage=None,
        direction_correct_count=0,
        direction_accuracy=0.0,
        success_count=0,
        success_rate=0.0,
        is_statistically_significant=False,
    )
