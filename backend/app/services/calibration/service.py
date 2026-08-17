"""Calibration service — orchestrates outcome analysis for learning.

Reads measured recommendation outcomes and computes calibration metrics to
answer business questions about recommendation reliability, systematic bias,
and confidence calibration.

This service is observational only — it learns from outcomes but does NOT
automatically change production recommendations, confidence scores, or
estimator logic.
"""

from typing import Any

from app.infrastructure.db.models.enums import OutcomeStatus
from app.infrastructure.db.repositories.outcomes import OutcomeRepository
from app.services.calibration import calculator
from app.services.calibration.models import (
    CalibrationMetrics,
    CalibrationSummary,
    ConfidenceBandCalibration,
    GeneratorPerformance,
    SegmentPerformance,
)


class CalibrationService:
    """Learn from measured outcomes to assess recommendation reliability.

    Provides calibration analysis across generators, confidence bands, horizons,
    and estimate bases. All metrics expose sample sizes and limitations — no
    statistical claims without sufficient data.
    """

    def __init__(self, repository: OutcomeRepository) -> None:
        self._repository = repository

    async def get_summary(self) -> CalibrationSummary:
        """Get full calibration analysis across all measured outcomes.

        Returns:
            CalibrationSummary with overall metrics, generator performance,
            confidence calibration, horizon breakdown, and limitations.
        """
        # Query all measured outcomes
        outcomes = await self._repository.find_measured()

        if not outcomes:
            return self._empty_summary()

        # Overall metrics
        overall_metrics = calculator.calculate_metrics(outcomes)

        # Generator performance
        generator_performance = await self._calculate_generator_performance(outcomes)

        # Confidence calibration
        confidence_calibration = self._calculate_confidence_calibration(outcomes)

        # Horizon breakdown
        horizon_breakdown = self._calculate_horizon_breakdown(outcomes)

        # Best performing generators
        generator_metrics = {gp.generator_name: gp.metrics for gp in generator_performance}
        ranked = calculator.rank_generators_by_quality(generator_metrics)
        best_performing = [name for name, _ in ranked[:3]]  # Top 3

        # Needs calibration
        biased = calculator.identify_systematic_biases(generator_metrics)
        needs_calibration = list(biased.keys())

        # Limitations
        limitations = self._assess_limitations(outcomes, generator_performance)

        # Count pending/failed outcomes
        total_pending = await self._repository.count_by_status(OutcomeStatus.PENDING)
        total_failed = await self._repository.count_by_status(OutcomeStatus.FAILED)

        return CalibrationSummary(
            total_measured_outcomes=len(outcomes),
            total_pending_outcomes=total_pending,
            total_failed_outcomes=total_failed,
            overall_metrics=overall_metrics,
            generator_performance=generator_performance,
            best_performing_generators=best_performing,
            needs_calibration=needs_calibration,
            confidence_calibration=confidence_calibration,
            horizon_breakdown=horizon_breakdown,
            limitations=limitations,
        )

    async def get_generator_performance(self, generator: str) -> GeneratorPerformance | None:
        """Get detailed performance for one generator.

        Args:
            generator: inventory | pricing | promotion | store | customer | supplier

        Returns:
            GeneratorPerformance or None if no measured outcomes exist for this generator.
        """
        outcomes = await self._repository.find_measured(generator=generator)

        if not outcomes:
            return None

        # Overall metrics for this generator
        metrics = calculator.calculate_metrics(outcomes)

        # Breakdown by estimate basis
        basis_segments = calculator.segment_by_field(outcomes, "estimate_basis")
        estimate_basis_breakdown = {
            basis: calculator.calculate_metrics(basis_outcomes)
            for basis, basis_outcomes in basis_segments.items()
        }

        # Confidence band calibration
        confidence_bands = self._calculate_confidence_calibration(outcomes)

        return GeneratorPerformance(
            generator_name=generator,
            metrics=metrics,
            estimate_basis_breakdown=estimate_basis_breakdown,
            confidence_bands=confidence_bands,
        )

    async def get_segment_performance(
        self, segment_type: str, segment_name: str
    ) -> SegmentPerformance | None:
        """Get calibration metrics for an arbitrary segment.

        Args:
            segment_type: category | horizon | basis | confidence | risk
            segment_name: The specific value (e.g., "inventory", "7", "measured")

        Returns:
            SegmentPerformance or None if no outcomes match the segment.
        """
        # Query all outcomes and segment them
        outcomes = await self._repository.find_measured()

        if not outcomes:
            return None

        # Segment by the requested field
        segments = calculator.segment_by_field(outcomes, segment_type)
        segment_outcomes = segments.get(segment_name, [])

        if not segment_outcomes:
            return None

        metrics = calculator.calculate_metrics(segment_outcomes)

        return SegmentPerformance(
            segment_name=segment_name,
            segment_type=segment_type,
            metrics=metrics,
        )

    async def get_confidence_calibration(self) -> list[ConfidenceBandCalibration]:
        """Get confidence band calibration analysis.

        Returns:
            List of ConfidenceBandCalibration showing expected vs actual success
            rates for each confidence band (0.0-0.2, 0.2-0.4, etc.).
        """
        outcomes = await self._repository.find_measured()

        if not outcomes:
            return []

        return self._calculate_confidence_calibration(outcomes)

    async def get_systematic_biases(self) -> dict[str, str]:
        """Identify generators with systematic over/underestimation.

        Returns:
            Dict mapping generator name to "overestimates" or "underestimates"
            for generators with bias > ±10% and sufficient samples.
        """
        outcomes = await self._repository.find_measured()

        if not outcomes:
            return {}

        # Segment by category
        category_segments = calculator.segment_by_field(outcomes, "category")
        generator_metrics = {
            category: calculator.calculate_metrics(cat_outcomes)
            for category, cat_outcomes in category_segments.items()
        }

        return calculator.identify_systematic_biases(generator_metrics)

    async def _calculate_generator_performance(
        self, outcomes: list[dict[str, Any]]
    ) -> list[GeneratorPerformance]:
        """Calculate performance for each generator."""
        # Segment by category (which maps to generator)
        category_segments = calculator.segment_by_field(outcomes, "category")

        performances = []
        for category, category_outcomes in category_segments.items():
            # Overall metrics
            metrics = calculator.calculate_metrics(category_outcomes)

            # Breakdown by estimate basis
            basis_segments = calculator.segment_by_field(category_outcomes, "estimate_basis")
            estimate_basis_breakdown = {
                basis: calculator.calculate_metrics(basis_outcomes)
                for basis, basis_outcomes in basis_segments.items()
            }

            # Confidence calibration
            confidence_bands = self._calculate_confidence_calibration(category_outcomes)

            performances.append(
                GeneratorPerformance(
                    generator_name=category,
                    metrics=metrics,
                    estimate_basis_breakdown=estimate_basis_breakdown,
                    confidence_bands=confidence_bands,
                )
            )

        return performances

    def _calculate_confidence_calibration(
        self, outcomes: list[dict[str, Any]]
    ) -> list[ConfidenceBandCalibration]:
        """Calculate confidence band calibration."""
        # `segment_by_confidence_band` expects a numeric 0.0-1.0 confidence —
        # documented and tested in test_calibration_calculator.py's own
        # `test_segment_by_confidence_band`. `outcome["confidence"]` here
        # comes from `Recommendation.confidence`, a categorical rubric
        # (high/medium/low), never numeric — passing it through unfiltered
        # raised TypeError on every call with real data (Prompt 11.5). Until
        # a numeric confidence score is actually persisted somewhere, this
        # correctly (safely) returns no confidence-band calibration rather
        # than crashing the whole calibration endpoint over one sub-metric.
        numeric_outcomes = [o for o in outcomes if isinstance(o.get("confidence"), int | float)]

        # Segment into confidence bands (0.0-0.2, 0.2-0.4, 0.4-0.6, 0.6-0.8, 0.8-1.0)
        band_segments = calculator.segment_by_confidence_band(numeric_outcomes, band_width=0.2)

        calibrations = []
        for (band_min, band_max), band_outcomes in band_segments.items():
            calibration = calculator.calculate_confidence_band_calibration(
                band_outcomes, band_min, band_max
            )
            calibrations.append(calibration)

        # Sort by confidence ascending
        calibrations.sort(key=lambda c: c.confidence_min)
        return calibrations

    def _calculate_horizon_breakdown(
        self, outcomes: list[dict[str, Any]]
    ) -> dict[int, CalibrationMetrics]:
        """Calculate metrics by measurement horizon."""
        horizon_segments = calculator.segment_by_field(outcomes, "horizon_days")

        return {
            int(horizon): calculator.calculate_metrics(horizon_outcomes)
            for horizon, horizon_outcomes in horizon_segments.items()
        }

    def _assess_limitations(
        self, outcomes: list[dict[str, Any]], generator_performance: list[GeneratorPerformance]
    ) -> list[str]:
        """Assess limitations of this calibration analysis."""
        limitations = []

        sample_size = len(outcomes)
        if sample_size == 0:
            limitations.append("No measured outcomes available yet.")
            return limitations

        if sample_size < 20:
            limitations.append(
                f"Small sample size (N={sample_size}). Results not statistically reliable."
            )

        # Check if any generators have insufficient samples
        small_generators = [
            gp.generator_name for gp in generator_performance if gp.metrics.sample_size < 20
        ]
        if small_generators:
            generators_str = ", ".join(small_generators)
            limitations.append(f"Some generators have <20 samples: {generators_str}")

        # Check if all outcomes are from a single horizon
        horizons = set(o.get("horizon_days") for o in outcomes if o.get("horizon_days"))
        if len(horizons) == 1:
            limitations.append(
                f"All outcomes from single horizon (H+{horizons.pop()}). "
                "Metrics may not generalize to other horizons."
            )

        return limitations

    def _empty_summary(self) -> CalibrationSummary:
        """Return empty summary when no measured outcomes exist."""
        return CalibrationSummary(
            total_measured_outcomes=0,
            total_pending_outcomes=0,
            total_failed_outcomes=0,
            overall_metrics=calculator._empty_metrics(),
            generator_performance=[],
            best_performing_generators=[],
            needs_calibration=[],
            confidence_calibration=[],
            horizon_breakdown={},
            limitations=["No measured outcomes available yet."],
        )
