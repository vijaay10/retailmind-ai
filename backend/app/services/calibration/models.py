"""Calibration domain models and metrics.

The calibration engine learns from measured outcomes to assess:
- How reliable our impact estimates are
- Which recommendation types perform best
- Whether confidence scores match actual reliability
- Which generators systematically over/underestimate

All metrics expose sample sizes. No statistical claims are made when N is
insufficient. A calibration metric from 3 outcomes is reported honestly as
"3 samples, insufficient for statistical inference" rather than hidden or
dressed as a trend.
"""

from dataclasses import dataclass
from typing import Any

#: Minimum samples required for statistical reliability.
#: Below this threshold, metrics are marked as "insufficient sample size".
MIN_SAMPLE_SIZE = 20

#: Minimum samples for confidence band calibration.
#: Confidence calibration requires larger samples to be meaningful.
MIN_CONFIDENCE_CALIBRATION_SAMPLES = 30


@dataclass(frozen=True, slots=True)
class CalibrationMetrics:
    """Core calibration metrics for a set of measured outcomes.

    All metrics handle edge cases (zero denominators, negative values, empty sets)
    and expose sample size for statistical validity assessment.
    """

    sample_size: int
    """Number of measured outcomes in this segment."""

    # Realization metrics
    mean_realization_ratio: float | None
    """Average of (realized / expected). 1.0 = perfect, >1.0 = underestimated."""

    median_realization_ratio: float | None
    """Median realization ratio — more robust to outliers than mean."""

    # Error metrics
    mean_absolute_error: float
    """Average abs(realized - expected) in currency."""

    mean_absolute_percentage_error: float | None
    """MAPE: average abs((realized - expected) / expected). None if any expected=0."""

    # Bias metrics
    mean_bias: float
    """Average (realized - expected). Positive = underestimated, negative = overestimated."""

    bias_percentage: float | None
    """Mean bias as % of expected. None if expected sum is zero."""

    # Direction accuracy
    direction_correct_count: int
    """How many moved in the expected direction (positive vs negative)."""

    direction_accuracy: float
    """Ratio of direction_correct_count / sample_size."""

    # Success rate (where success = realization >= threshold)
    success_count: int
    """How many achieved >= 70% of expected impact."""

    success_rate: float
    """Ratio of success_count / sample_size."""

    # Statistical validity
    is_statistically_significant: bool
    """Whether sample_size >= MIN_SAMPLE_SIZE."""

    @property
    def systematically_overestimates(self) -> bool:
        """Consistently delivers less than expected (mean bias < -10%)."""
        if not self.is_statistically_significant or self.bias_percentage is None:
            return False
        return self.bias_percentage < -10.0

    @property
    def systematically_underestimates(self) -> bool:
        """Consistently delivers more than expected (mean bias > +10%)."""
        if not self.is_statistically_significant or self.bias_percentage is None:
            return False
        return self.bias_percentage > 10.0

    @property
    def is_well_calibrated(self) -> bool:
        """Bias within ±5% and sample size sufficient."""
        if not self.is_statistically_significant or self.bias_percentage is None:
            return False
        return abs(self.bias_percentage) <= 5.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "sample_size": self.sample_size,
            "is_statistically_significant": self.is_statistically_significant,
            "mean_realization_ratio": (
                round(self.mean_realization_ratio, 4)
                if self.mean_realization_ratio is not None
                else None
            ),
            "median_realization_ratio": (
                round(self.median_realization_ratio, 4)
                if self.median_realization_ratio is not None
                else None
            ),
            "mean_absolute_error": round(self.mean_absolute_error, 2),
            "mean_absolute_percentage_error": (
                round(self.mean_absolute_percentage_error, 4)
                if self.mean_absolute_percentage_error is not None
                else None
            ),
            "mean_bias": round(self.mean_bias, 2),
            "bias_percentage": (
                round(self.bias_percentage, 2) if self.bias_percentage is not None else None
            ),
            "direction_correct_count": self.direction_correct_count,
            "direction_accuracy": round(self.direction_accuracy, 4),
            "success_count": self.success_count,
            "success_rate": round(self.success_rate, 4),
            "systematically_overestimates": self.systematically_overestimates,
            "systematically_underestimates": self.systematically_underestimates,
            "is_well_calibrated": self.is_well_calibrated,
        }


@dataclass(frozen=True, slots=True)
class ConfidenceBandCalibration:
    """Calibration for one confidence band (e.g., 0.8-0.9).

    Answers: "Are recommendations we marked as 80-90% confident actually reliable
    80-90% of the time?"

    Confidence score should match success rate. A band with confidence=0.85 but
    success_rate=0.60 is miscalibrated by 25 percentage points.
    """

    confidence_min: float
    confidence_max: float
    sample_size: int

    expected_success_rate: float
    """Midpoint of the confidence band, e.g., 0.85 for 0.8-0.9."""

    actual_success_rate: float
    """Observed success rate (>= 70% of expected impact achieved)."""

    calibration_error: float
    """abs(expected - actual) in percentage points."""

    mean_realization_ratio: float | None
    """Average realization within this band."""

    is_statistically_significant: bool
    """Whether sample_size >= MIN_CONFIDENCE_CALIBRATION_SAMPLES."""

    @property
    def is_well_calibrated(self) -> bool:
        """Calibration error <= 10 percentage points and sufficient samples."""
        if not self.is_statistically_significant:
            return False
        return self.calibration_error <= 0.10

    @property
    def is_overconfident(self) -> bool:
        """Expected success rate exceeds actual by >10pp."""
        if not self.is_statistically_significant:
            return False
        return (self.expected_success_rate - self.actual_success_rate) > 0.10

    @property
    def is_underconfident(self) -> bool:
        """Actual success rate exceeds expected by >10pp."""
        if not self.is_statistically_significant:
            return False
        return (self.actual_success_rate - self.expected_success_rate) > 0.10

    def as_dict(self) -> dict[str, Any]:
        return {
            "confidence_band": f"{self.confidence_min:.2f}-{self.confidence_max:.2f}",
            "sample_size": self.sample_size,
            "is_statistically_significant": self.is_statistically_significant,
            "expected_success_rate": round(self.expected_success_rate, 4),
            "actual_success_rate": round(self.actual_success_rate, 4),
            "calibration_error": round(self.calibration_error, 4),
            "mean_realization_ratio": (
                round(self.mean_realization_ratio, 4)
                if self.mean_realization_ratio is not None
                else None
            ),
            "is_well_calibrated": self.is_well_calibrated,
            "is_overconfident": self.is_overconfident,
            "is_underconfident": self.is_underconfident,
        }


@dataclass(frozen=True, slots=True)
class GeneratorPerformance:
    """Performance metrics for one recommendation generator (e.g., inventory_recommendations).

    Answers business questions:
    - Which generators should be trusted most?
    - Which systematically overestimate?
    - Which have the best direction accuracy?
    """

    generator_name: str
    """inventory | pricing | promotion | store | customer | supplier"""

    metrics: CalibrationMetrics
    """Core calibration metrics for this generator."""

    estimate_basis_breakdown: dict[str, CalibrationMetrics]
    """Metrics segmented by basis (measured | modelled | assumed)."""

    confidence_bands: list[ConfidenceBandCalibration]
    """Confidence calibration for this generator."""

    @property
    def quality_score(self) -> float | None:
        """Overall quality score (0.0-1.0) combining multiple factors.

        Factors:
        - Direction accuracy (40%)
        - Calibration (30%)
        - Success rate (30%)

        Returns None if insufficient samples.
        """
        if not self.metrics.is_statistically_significant:
            return None

        direction_score = self.metrics.direction_accuracy
        calibration_score = 1.0 - min(1.0, abs(self.metrics.bias_percentage or 0.0) / 100.0)
        success_score = self.metrics.success_rate

        return 0.4 * direction_score + 0.3 * calibration_score + 0.3 * success_score

    @property
    def needs_calibration(self) -> bool:
        """Whether this generator shows systematic bias or poor calibration."""
        if not self.metrics.is_statistically_significant:
            return False

        return (
            self.metrics.systematically_overestimates
            or self.metrics.systematically_underestimates
            or self.metrics.direction_accuracy < 0.70
            or self.metrics.success_rate < 0.60
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "generator_name": self.generator_name,
            "metrics": self.metrics.as_dict(),
            "estimate_basis_breakdown": {
                basis: metrics.as_dict() for basis, metrics in self.estimate_basis_breakdown.items()
            },
            "confidence_bands": [band.as_dict() for band in self.confidence_bands],
            "quality_score": (
                round(self.quality_score, 4) if self.quality_score is not None else None
            ),
            "needs_calibration": self.needs_calibration,
        }


@dataclass(frozen=True, slots=True)
class SegmentPerformance:
    """Performance for an arbitrary segment (e.g., all inventory recs at store S2016).

    Used to answer: "Are recommendations for this store/category/basis reliable?"
    """

    segment_name: str
    segment_type: str
    """category | store | product_category | horizon | basis | confidence | risk"""

    metrics: CalibrationMetrics

    def as_dict(self) -> dict[str, Any]:
        return {
            "segment_name": self.segment_name,
            "segment_type": self.segment_type,
            "metrics": self.metrics.as_dict(),
        }


@dataclass(frozen=True, slots=True)
class CalibrationSummary:
    """Top-level calibration summary across all measured outcomes."""

    total_measured_outcomes: int
    total_pending_outcomes: int
    total_failed_outcomes: int

    overall_metrics: CalibrationMetrics
    """Metrics across all generators."""

    generator_performance: list[GeneratorPerformance]
    """Performance by generator type."""

    best_performing_generators: list[str]
    """Generators with highest quality scores."""

    needs_calibration: list[str]
    """Generators showing systematic bias."""

    confidence_calibration: list[ConfidenceBandCalibration]
    """Overall confidence band calibration."""

    horizon_breakdown: dict[int, CalibrationMetrics]
    """Metrics by measurement horizon (1, 7, 14, 30 days)."""

    limitations: list[str]
    """Known limitations of this calibration analysis."""

    def as_dict(self) -> dict[str, Any]:
        return {
            "total_measured_outcomes": self.total_measured_outcomes,
            "total_pending_outcomes": self.total_pending_outcomes,
            "total_failed_outcomes": self.total_failed_outcomes,
            "overall_metrics": self.overall_metrics.as_dict(),
            "generator_performance": [gp.as_dict() for gp in self.generator_performance],
            "best_performing_generators": self.best_performing_generators,
            "needs_calibration": self.needs_calibration,
            "confidence_calibration": [cb.as_dict() for cb in self.confidence_calibration],
            "horizon_breakdown": {
                horizon: metrics.as_dict() for horizon, metrics in self.horizon_breakdown.items()
            },
            "limitations": self.limitations,
        }
