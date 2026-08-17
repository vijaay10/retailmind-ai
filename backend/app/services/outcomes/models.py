"""Outcome measurement domain models and contracts.

These are the working objects the measurement service uses, distinct from the
database models. A measurement happens in several steps (baseline calculation,
observation query, impact calculation, confidence assessment), and each step
produces values that need to travel together before they land in a row.
"""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class MeasurementWindow:
    """The time windows used for outcome measurement."""

    decision_date: date
    """When the decision was made."""

    horizon_days: int
    """How far forward to measure (1, 7, 14, 30)."""

    baseline_start: date
    """Start of the baseline observation period."""

    baseline_end: date
    """End of the baseline period (usually decision_date - 1)."""

    observation_start: date
    """Start of post-decision measurement (usually decision_date)."""

    observation_end: date
    """End of measurement window (decision_date + horizon_days)."""

    @property
    def is_mature(self) -> bool:
        """Has enough time passed to measure this outcome?"""
        return date.today() >= self.observation_end

    @property
    def baseline_days(self) -> int:
        return (self.baseline_end - self.baseline_start).days + 1

    @property
    def observation_days(self) -> int:
        return (self.observation_end - self.observation_start).days + 1


@dataclass(frozen=True, slots=True)
class BaselineCalculation:
    """Result of calculating the counterfactual baseline."""

    method: str
    """comparable_period | pre_decision | peer_baseline | forecast_baseline"""

    value: float
    """The baseline metric value (what would have happened)."""

    confidence: str
    """low | medium | high"""

    limitations: list[str]
    """Known issues, e.g., ['short baseline window', 'partial data']"""

    metadata: dict[str, Any]
    """Method-specific details for audit trail."""

    def as_dict(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "value": round(self.value, 2),
            "confidence": self.confidence,
            "limitations": self.limitations,
            "metadata": self.metadata,
        }


@dataclass(frozen=True, slots=True)
class ObservationResult:
    """The observed metric value during the measurement window."""

    value: float
    """Actual observed value."""

    data_completeness: float
    """Ratio of days with data to expected days (0.0-1.0)."""

    confounding_events: list[str]
    """Detected events that may have influenced the outcome."""

    def as_dict(self) -> dict[str, Any]:
        return {
            "value": round(self.value, 2),
            "data_completeness": round(self.data_completeness, 4),
            "confounding_events": self.confounding_events,
        }


@dataclass(frozen=True, slots=True)
class ImpactMeasurement:
    """Calculated impact: observed vs. baseline vs. expected."""

    baseline_value: float
    observed_value: float
    realized_impact: float
    """observed - baseline"""

    expected_impact: float
    """From the recommendation's impact estimate."""

    absolute_error: float
    """abs(realized - expected)"""

    realization_ratio: float | None
    """realized / expected (None if expected is zero)"""

    direction_correct: bool
    """Did it move in the expected direction?"""

    def as_dict(self) -> dict[str, Any]:
        return {
            "baseline_value": round(self.baseline_value, 2),
            "observed_value": round(self.observed_value, 2),
            "realized_impact": round(self.realized_impact, 2),
            "expected_impact": round(self.expected_impact, 2),
            "absolute_error": round(self.absolute_error, 2),
            "realization_ratio": (
                round(self.realization_ratio, 4) if self.realization_ratio is not None else None
            ),
            "direction_correct": self.direction_correct,
        }


@dataclass(frozen=True, slots=True)
class MeasurementResult:
    """Complete outcome measurement for one decision at one horizon."""

    decision_key: str
    horizon_days: int
    window: MeasurementWindow
    baseline: BaselineCalculation
    observation: ObservationResult
    impact: ImpactMeasurement
    measurement_confidence: str
    """low | medium | high - overall confidence in this measurement"""

    limitations: list[str]
    """All limitations from baseline + observation + impact"""

    measured_at: datetime

    def as_dict(self) -> dict[str, Any]:
        return {
            "decision_key": self.decision_key,
            "horizon_days": self.horizon_days,
            "window": {
                "decision_date": self.window.decision_date.isoformat(),
                "baseline_start": self.window.baseline_start.isoformat(),
                "baseline_end": self.window.baseline_end.isoformat(),
                "observation_start": self.window.observation_start.isoformat(),
                "observation_end": self.window.observation_end.isoformat(),
            },
            "baseline": self.baseline.as_dict(),
            "observation": self.observation.as_dict(),
            "impact": self.impact.as_dict(),
            "measurement_confidence": self.measurement_confidence,
            "limitations": self.limitations,
            "measured_at": self.measured_at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class OutcomeRecord:
    """Flattened outcome for persistence to RecommendationOutcome table."""

    recommendation_id: str
    decision_key: str
    horizon_days: int

    status: str
    baseline_method: str

    baseline_window_start: date
    baseline_window_end: date
    observation_window_start: date
    observation_window_end: date

    baseline_value: float
    observed_value: float
    realized_impact: float
    expected_impact: float
    absolute_error: float
    realization_ratio: float | None
    direction_correct: bool

    measurement_confidence: str
    limitations: str  # Joined list
    measured_at: datetime

    # Legacy compatibility
    outcome_metrics: dict[str, Any]
    method: str
    vs_expected_ratio: float | None

    @classmethod
    def from_measurement(
        cls,
        recommendation_id: str,
        decision_key: str,
        result: MeasurementResult,
    ) -> "OutcomeRecord":
        """Convert a MeasurementResult into a persistable OutcomeRecord."""
        return cls(
            recommendation_id=recommendation_id,
            decision_key=decision_key,
            horizon_days=result.horizon_days,
            status="measured",
            baseline_method=result.baseline.method,
            baseline_window_start=result.window.baseline_start,
            baseline_window_end=result.window.baseline_end,
            observation_window_start=result.window.observation_start,
            observation_window_end=result.window.observation_end,
            baseline_value=result.baseline.value,
            observed_value=result.observation.value,
            realized_impact=result.impact.realized_impact,
            expected_impact=result.impact.expected_impact,
            absolute_error=result.impact.absolute_error,
            realization_ratio=result.impact.realization_ratio,
            direction_correct=result.impact.direction_correct,
            measurement_confidence=result.measurement_confidence,
            limitations="; ".join(result.limitations),
            measured_at=result.measured_at,
            # Legacy compatibility
            outcome_metrics={
                "baseline": result.baseline.as_dict(),
                "observation": result.observation.as_dict(),
                "impact": result.impact.as_dict(),
            },
            method=(
                f"{result.baseline.method} baseline vs "
                f"{result.window.observation_days}d observation"
            ),
            vs_expected_ratio=result.impact.realization_ratio,
        )
