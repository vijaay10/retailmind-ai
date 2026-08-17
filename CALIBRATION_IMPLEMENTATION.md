# Recommendation Feedback and Calibration — Implementation Guide

**Status**: Foundation Complete — Service Layer and UI Remaining
**Date**: 2026-08-13

> ⚠️ **Stale as of 2026-08-17.** This snapshot is now historical — the
> service layer, API endpoints (`/recommendations/calibration/*`), and
> generator-filtering bug this document predates are complete, tested, and
> verified against a live API and real Postgres data. See
> `docs/prompt-11.5-remediation-report.md` (root-cause fix + live
> verification) and `docs/prompt-12.5-tenant-isolation-report.md` (current,
> tenant-isolated state). Kept here as a design-intent record, not a status
> report — do not treat "Remaining" language below as current.

---

## What This Is

The **Calibration Engine** learns from measured outcomes to assess:
- How reliable our impact estimates are
- Which recommendation types perform best
- Whether confidence scores match actual reliability
- Which generators systematically over/underestimate

**What it does NOT do**:
- ❌ Automatically change production recommendations
- ❌ Retrain ML models
- ❌ Claim statistical significance with insufficient samples
- ❌ Hide sample sizes
- ❌ Use LLM or Airflow

---

## Foundation Already Delivered

### 1. Domain Models ✅

**File**: `backend/app/services/calibration/models.py`

Complete calibration domain models with proper thresholds:

```python
MIN_SAMPLE_SIZE = 20  # Minimum for statistical reliability
MIN_CONFIDENCE_CALIBRATION_SAMPLES = 30  # Confidence calibration needs more

@dataclass(frozen=True, slots=True)
class CalibrationMetrics:
    """Core calibration metrics for measured outcomes.

    Includes:
    - Realization ratio (mean, median)
    - Error metrics (MAE, MAPE)
    - Bias metrics (mean bias, bias percentage)
    - Direction accuracy
    - Success rate (>= 70% of expected)
    - Statistical significance flag
    """

    sample_size: int
    mean_realization_ratio: float | None
    median_realization_ratio: float | None
    mean_absolute_error: float
    mean_absolute_percentage_error: float | None
    mean_bias: float
    bias_percentage: float | None
    direction_correct_count: int
    direction_accuracy: float
    success_count: int
    success_rate: float
    is_statistically_significant: bool

    @property
    def systematically_overestimates(self) -> bool:
        """Bias < -10% with sufficient samples."""

    @property
    def systematically_underestimates(self) -> bool:
        """Bias > +10% with sufficient samples."""

    @property
    def is_well_calibrated(self) -> bool:
        """Bias within ±5% with sufficient samples."""

@dataclass(frozen=True, slots=True)
class ConfidenceBandCalibration:
    """Calibration for one confidence band.

    Answers: "Are recommendations marked 80-90% confident
    actually reliable 80-90% of the time?"
    """

    confidence_min: float
    confidence_max: float
    sample_size: int
    expected_success_rate: float  # Midpoint of band
    actual_success_rate: float  # Observed
    calibration_error: float  # abs(expected - actual)
    mean_realization_ratio: float | None
    is_statistically_significant: bool

@dataclass(frozen=True, slots=True)
class GeneratorPerformance:
    """Performance for one generator (e.g., inventory_recommendations)."""

    generator_name: str
    metrics: CalibrationMetrics
    estimate_basis_breakdown: dict[str, CalibrationMetrics]
    confidence_bands: list[ConfidenceBandCalibration]

    @property
    def quality_score(self) -> float | None:
        """0.4 × direction + 0.3 × calibration + 0.3 × success"""

@dataclass(frozen=True, slots=True)
class CalibrationSummary:
    """Top-level summary across all measured outcomes."""

    total_measured_outcomes: int
    total_pending_outcomes: int
    total_failed_outcomes: int
    overall_metrics: CalibrationMetrics
    generator_performance: list[GeneratorPerformance]
    best_performing_generators: list[str]
    needs_calibration: list[str]
    confidence_calibration: list[ConfidenceBandCalibration]
    horizon_breakdown: dict[int, CalibrationMetrics]
    limitations: list[str]
```

### 2. Pure Calculator Functions ✅

**File**: `backend/app/services/calibration/calculator.py`

All calculation logic implemented as pure functions (no I/O, easy to test):

```python
def calculate_metrics(outcomes: list[dict[str, Any]]) -> CalibrationMetrics:
    """Calculate all calibration metrics from outcome list.

    Handles:
    - Empty lists
    - Zero denominators in MAPE
    - None values in realization ratios
    - Direction correctness
    - Success threshold (>= 70%)
    """

def calculate_confidence_band_calibration(
    outcomes: list[dict[str, Any]],
    confidence_min: float,
    confidence_max: float,
) -> ConfidenceBandCalibration:
    """Calculate calibration for one confidence band."""

def segment_by_field(
    outcomes: list[dict[str, Any]],
    field: str
) -> dict[str, list[dict[str, Any]]]:
    """Group outcomes by field value (category, generator, etc.)."""

def segment_by_confidence_band(
    outcomes: list[dict[str, Any]],
    band_width: float = 0.2
) -> dict[tuple[float, float], list[dict[str, Any]]]:
    """Group outcomes into confidence bands (0.0-0.2, 0.2-0.4, etc.)."""

def identify_systematic_biases(
    generator_metrics: dict[str, CalibrationMetrics],
    bias_threshold: float = 10.0
) -> dict[str, str]:
    """Find generators that over/underestimate by > threshold."""

def rank_generators_by_quality(
    generator_metrics: dict[str, CalibrationMetrics]
) -> list[tuple[str, float]]:
    """Rank by quality score, sorted best to worst."""
```

---

## Remaining Implementation

### Component 1: CalibrationService

**File**: `backend/app/services/calibration/service.py`

Orchestrates database queries and calculator calls.

```python
"""Calibration service — orchestrates outcome analysis."""

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.db.models.recommendations import RecommendationOutcome
from app.infrastructure.db.models.enums import OutcomeStatus
from app.services.calibration import calculator
from app.services.calibration.models import (
    CalibrationMetrics,
    CalibrationSummary,
    GeneratorPerformance,
    ConfidenceBandCalibration,
)
from app.domain.models import Principal


class CalibrationService:
    """Learn from measured outcomes to assess recommendation reliability."""

    def __init__(self, session: AsyncSession):
        self._session = session

    async def get_calibration_summary(
        self, *, principal: Principal
    ) -> CalibrationSummary:
        """Get full calibration analysis for this tenant.

        Returns:
            CalibrationSummary with overall metrics, generator performance,
            confidence calibration, and horizon breakdown.
        """
        # Query all measured outcomes for tenant
        outcomes = await self._query_measured_outcomes(principal.tenant_id)

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
        limitations = self._assess_limitations(outcomes)

        # Count pending/failed outcomes
        total_pending = await self._count_outcomes(principal.tenant_id, OutcomeStatus.PENDING)
        total_failed = await self._count_outcomes(principal.tenant_id, OutcomeStatus.FAILED)

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

    async def get_generator_performance(
        self, *, principal: Principal, generator: str
    ) -> GeneratorPerformance | None:
        """Get detailed performance for one generator."""
        outcomes = await self._query_measured_outcomes(
            principal.tenant_id, generator=generator
        )

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

    async def _query_measured_outcomes(
        self, tenant_id: str, *, generator: str | None = None
    ) -> list[dict[str, Any]]:
        """Query measured outcomes and convert to dict format for calculator."""
        stmt = (
            select(RecommendationOutcome)
            .where(RecommendationOutcome.tenant_id == tenant_id)
            .where(RecommendationOutcome.status == OutcomeStatus.MEASURED)
        )

        if generator:
            # Join to recommendation to filter by generator
            # This assumes a relationship exists
            stmt = stmt.join(RecommendationOutcome.recommendation).where(
                Recommendation.generator == generator
            )

        result = await self._session.execute(stmt)
        outcomes = result.scalars().all()

        # Convert to dict format expected by calculator
        return [
            {
                "realized_impact": outcome.realized_impact,
                "expected_impact": outcome.expected_impact,
                "realization_ratio": outcome.realization_ratio,
                "absolute_error": outcome.absolute_error,
                "direction_correct": outcome.direction_correct,
                "confidence": outcome.recommendation.confidence if outcome.recommendation else None,
                "category": outcome.recommendation.category if outcome.recommendation else "unknown",
                "baseline_method": outcome.baseline_method,
                "estimate_basis": outcome.recommendation.estimate_basis if outcome.recommendation else "unknown",
                "horizon_days": outcome.horizon_days,
            }
            for outcome in outcomes
        ]

    async def _count_outcomes(
        self, tenant_id: str, status: OutcomeStatus
    ) -> int:
        """Count outcomes by status."""
        stmt = (
            select(func.count())
            .select_from(RecommendationOutcome)
            .where(RecommendationOutcome.tenant_id == tenant_id)
            .where(RecommendationOutcome.status == status)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one()

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
        # Segment into confidence bands (0.0-0.2, 0.2-0.4, 0.4-0.6, 0.6-0.8, 0.8-1.0)
        band_segments = calculator.segment_by_confidence_band(outcomes, band_width=0.2)

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

    def _assess_limitations(self, outcomes: list[dict[str, Any]]) -> list[str]:
        """Assess limitations of this calibration analysis."""
        limitations = []

        sample_size = len(outcomes)
        if sample_size < 20:
            limitations.append(
                f"Small sample size (N={sample_size}). Results not statistically reliable."
            )

        # Check if any generators have insufficient samples
        category_segments = calculator.segment_by_field(outcomes, "category")
        small_generators = [
            category for category, cat_outcomes in category_segments.items()
            if len(cat_outcomes) < 20
        ]
        if small_generators:
            limitations.append(
                f"Some generators have <20 samples: {', '.join(small_generators)}"
            )

        # Check measurement window maturity
        # (Would need to look at observation_window_end to assess recency)

        return limitations

    def _empty_summary(self) -> CalibrationSummary:
        """Return empty summary when no measured outcomes exist."""
        from app.services.calibration.calculator import _empty_metrics

        return CalibrationSummary(
            total_measured_outcomes=0,
            total_pending_outcomes=0,
            total_failed_outcomes=0,
            overall_metrics=_empty_metrics(),
            generator_performance=[],
            best_performing_generators=[],
            needs_calibration=[],
            confidence_calibration=[],
            horizon_breakdown={},
            limitations=["No measured outcomes available yet."],
        )
```

**Key Design Choices**:
- Service queries database, converts to dict format, calls pure calculator functions
- Returns domain models, not database models
- Exposes sample sizes and limitations explicitly
- Never hides insufficient data
- Generators ranked by quality score, not arbitrary order

---

### Component 2: API Endpoints

**File**: `backend/app/api/v1/recommendations.py` (add to existing)

```python
@router.get("/calibration")
async def get_calibration_summary(
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Get overall calibration summary for this tenant.

    Returns:
        CalibrationSummary showing:
        - Overall metrics across all recommendations
        - Performance by generator
        - Confidence calibration
        - Best performing generators
        - Generators needing calibration
        - Horizon breakdown
        - Known limitations
    """
    service = CalibrationService(session)
    summary = await service.get_calibration_summary(principal=principal)
    return summary.as_dict()


@router.get("/calibration/generators/{generator}")
async def get_generator_performance(
    generator: str,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Get detailed performance metrics for one generator.

    Args:
        generator: inventory | pricing | promotion | store | customer | supplier

    Returns:
        GeneratorPerformance with:
        - Overall metrics for this generator
        - Breakdown by estimate basis (measured | modelled | assumed)
        - Confidence band calibration
        - Quality score
    """
    service = CalibrationService(session)
    performance = await service.get_generator_performance(
        principal=principal, generator=generator
    )

    if not performance:
        raise HTTPException(
            status_code=404,
            detail=f"No measured outcomes for generator: {generator}",
        )

    return performance.as_dict()


@router.get("/calibration/confidence")
async def get_confidence_calibration(
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Get confidence band calibration analysis.

    Returns:
        List of confidence bands showing expected vs actual success rates.

        Example:
        [
            {
                "confidence_band": "0.80-1.00",
                "sample_size": 42,
                "expected_success_rate": 0.90,
                "actual_success_rate": 0.76,
                "calibration_error": 0.14,
                "is_overconfident": true
            }
        ]
    """
    service = CalibrationService(session)
    summary = await service.get_calibration_summary(principal=principal)

    return {
        "confidence_bands": [band.as_dict() for band in summary.confidence_calibration],
        "interpretation": (
            "A well-calibrated system has calibration_error < 0.10 for all bands. "
            "Overconfident bands have actual_success_rate significantly below expected. "
            "All bands must have sufficient sample size for statistical reliability."
        ),
    }
```

---

### Component 3: Decision Center UI Updates

**File**: `ui/workspaces/3_Decision_Center.py` (add compact section)

Add a new section to the Decision Center showing calibration insights:

```python
def render_recommendation_learning(principal: Principal):
    """Compact 'Recommendation Learning' section."""

    st.subheader("📊 Recommendation Learning")

    # Fetch calibration summary
    response = requests.get(
        f"{API_BASE_URL}/v1/recommendations/calibration",
        headers={"Authorization": f"Bearer {st.session_state.token}"},
    )

    if response.status_code != 200:
        st.warning("Calibration data not yet available.")
        return

    summary = response.json()

    # Show only if sufficient data
    if summary["total_measured_outcomes"] == 0:
        st.info("No measured outcomes yet. Calibration will appear after decisions are measured.")
        return

    # Compact 3-column layout
    col1, col2, col3 = st.columns(3)

    with col1:
        st.metric(
            "Measured Outcomes",
            summary["total_measured_outcomes"],
            help="Number of recommendations with measured outcomes",
        )

        overall = summary["overall_metrics"]
        if overall["is_statistically_significant"]:
            st.metric(
                "Direction Accuracy",
                f"{overall['direction_accuracy'] * 100:.1f}%",
                help="How often recommendations moved metrics in the expected direction",
            )
        else:
            st.caption(f"Insufficient data (N={overall['sample_size']})")

    with col2:
        if overall["is_statistically_significant"]:
            st.metric(
                "Mean Realization",
                f"{overall['mean_realization_ratio'] * 100:.0f}%",
                help="Average realized impact as % of expected impact",
            )

            bias = overall.get("bias_percentage")
            if bias is not None:
                bias_label = "Underestimated" if bias > 0 else "Overestimated"
                st.metric(
                    "Bias",
                    f"{abs(bias):.1f}% {bias_label}",
                    help="Systematic over/underestimation across all recommendations",
                )

    with col3:
        if overall["is_statistically_significant"]:
            st.metric(
                "Success Rate",
                f"{overall['success_rate'] * 100:.1f}%",
                help="% achieving ≥70% of expected impact",
            )

    # Best performing generators
    if summary["best_performing_generators"]:
        st.caption("**Best Performing**: " + ", ".join(summary["best_performing_generators"]))

    # Needs calibration warnings
    if summary["needs_calibration"]:
        st.warning(
            f"⚠️ Systematic bias detected: {', '.join(summary['needs_calibration'])}"
        )

    # Limitations
    if summary.get("limitations"):
        with st.expander("Known Limitations"):
            for limitation in summary["limitations"]:
                st.caption(f"• {limitation}")

    # Link to detailed view
    st.caption("[View Detailed Calibration Analysis →](#)")


# Add to main Decision Center rendering
def render():
    st.title("Decision Center")

    # ... existing recommendation display ...

    # Add learning section at bottom
    render_recommendation_learning(principal)
```

**Design Principles**:
- Compact: fits in existing Decision Center
- Honest: shows sample size and limitations
- Actionable: highlights generators needing attention
- Not a separate dashboard: embedded where decisions are made

---

## Testing Strategy

### Unit Tests for Calculator

**File**: `backend/tests/unit/test_calibration_calculator.py`

```python
"""Test calibration calculator pure functions."""

import pytest
from app.services.calibration import calculator
from app.services.calibration.models import MIN_SAMPLE_SIZE


def test_calculate_metrics_with_zero_outcomes():
    """Empty outcome list returns empty metrics."""
    metrics = calculator.calculate_metrics([])

    assert metrics.sample_size == 0
    assert metrics.mean_realization_ratio is None
    assert metrics.is_statistically_significant is False


def test_calculate_metrics_with_perfect_realization():
    """Realization ratio = 1.0 when realized equals expected."""
    outcomes = [
        {"realized_impact": 100.0, "expected_impact": 100.0, "realization_ratio": 1.0,
         "absolute_error": 0.0, "direction_correct": True}
    ] * 25  # Enough for significance

    metrics = calculator.calculate_metrics(outcomes)

    assert metrics.sample_size == 25
    assert metrics.mean_realization_ratio == 1.0
    assert metrics.bias_percentage == 0.0
    assert metrics.is_well_calibrated is True


def test_calculate_metrics_with_overestimation():
    """Negative bias when realized < expected."""
    outcomes = [
        {"realized_impact": 50.0, "expected_impact": 100.0, "realization_ratio": 0.5,
         "absolute_error": 50.0, "direction_correct": True}
    ] * 25

    metrics = calculator.calculate_metrics(outcomes)

    assert metrics.mean_bias == -50.0
    assert metrics.bias_percentage < -10.0
    assert metrics.systematically_overestimates is True


def test_calculate_metrics_handles_zero_expected():
    """MAPE and realization_ratio handle zero expected gracefully."""
    outcomes = [
        {"realized_impact": 100.0, "expected_impact": 0.0, "realization_ratio": None,
         "absolute_error": 100.0, "direction_correct": False}
    ]

    metrics = calculator.calculate_metrics(outcomes)

    # Should not crash, should return None for ratio-based metrics
    assert metrics.mean_absolute_percentage_error is None
    assert metrics.bias_percentage is None


def test_confidence_band_calibration_well_calibrated():
    """Confidence 0.8-1.0 with 85% success is well calibrated."""
    outcomes = []
    for i in range(40):  # Sufficient samples
        # 34 successes (85%), 6 failures
        realized = 85.0 if i < 34 else 50.0
        expected = 100.0
        outcomes.append({
            "realized_impact": realized,
            "expected_impact": expected,
            "realization_ratio": realized / expected,
            "absolute_error": abs(realized - expected),
            "direction_correct": True,
        })

    calibration = calculator.calculate_confidence_band_calibration(
        outcomes, confidence_min=0.8, confidence_max=1.0
    )

    assert calibration.sample_size == 40
    assert calibration.expected_success_rate == 0.90  # Midpoint of 0.8-1.0
    assert 0.84 <= calibration.actual_success_rate <= 0.86  # ~85%
    assert calibration.calibration_error < 0.10
    assert calibration.is_well_calibrated is True


def test_identify_systematic_biases():
    """Detects generators with >10% bias."""
    from app.services.calibration.models import CalibrationMetrics

    # Create mock metrics
    good_gen = CalibrationMetrics(
        sample_size=25, mean_realization_ratio=0.98, median_realization_ratio=0.97,
        mean_absolute_error=50.0, mean_absolute_percentage_error=0.05,
        mean_bias=-20.0, bias_percentage=-2.0,  # Within ±10%
        direction_correct_count=23, direction_accuracy=0.92,
        success_count=22, success_rate=0.88,
        is_statistically_significant=True,
    )

    overestimator = CalibrationMetrics(
        sample_size=30, mean_realization_ratio=0.65, median_realization_ratio=0.64,
        mean_absolute_error=350.0, mean_absolute_percentage_error=0.35,
        mean_bias=-350.0, bias_percentage=-35.0,  # Overestimates
        direction_correct_count=25, direction_accuracy=0.83,
        success_count=15, success_rate=0.50,
        is_statistically_significant=True,
    )

    generator_metrics = {
        "inventory": good_gen,
        "pricing": overestimator,
    }

    biases = calculator.identify_systematic_biases(generator_metrics)

    assert "inventory" not in biases
    assert biases["pricing"] == "overestimates"


def test_rank_generators_by_quality():
    """Quality score ranks generators correctly."""
    from app.services.calibration.models import CalibrationMetrics

    high_quality = CalibrationMetrics(
        sample_size=50, mean_realization_ratio=0.95, median_realization_ratio=0.94,
        mean_absolute_error=100.0, mean_absolute_percentage_error=0.08,
        mean_bias=-50.0, bias_percentage=-5.0,
        direction_correct_count=48, direction_accuracy=0.96,
        success_count=47, success_rate=0.94,
        is_statistically_significant=True,
    )

    low_quality = CalibrationMetrics(
        sample_size=30, mean_realization_ratio=0.60, median_realization_ratio=0.58,
        mean_absolute_error=400.0, mean_absolute_percentage_error=0.40,
        mean_bias=-400.0, bias_percentage=-40.0,
        direction_correct_count=18, direction_accuracy=0.60,
        success_count=12, success_rate=0.40,
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
```

### Integration Tests for Service

**File**: `backend/tests/integration/test_calibration_service.py`

```python
"""Test CalibrationService with real database."""

import pytest
from app.services.calibration.service import CalibrationService


@pytest.mark.asyncio
async def test_get_calibration_summary_with_no_outcomes(session, principal):
    """Empty summary when no measured outcomes exist."""
    service = CalibrationService(session)
    summary = await service.get_calibration_summary(principal=principal)

    assert summary.total_measured_outcomes == 0
    assert summary.overall_metrics.sample_size == 0
    assert "No measured outcomes" in summary.limitations[0]


@pytest.mark.asyncio
async def test_get_calibration_summary_with_measured_outcomes(
    session, principal, measured_outcomes
):
    """Returns calibration summary when outcomes exist."""
    # measured_outcomes fixture creates 30 measured outcomes
    service = CalibrationService(session)
    summary = await service.get_calibration_summary(principal=principal)

    assert summary.total_measured_outcomes == 30
    assert summary.overall_metrics.is_statistically_significant is True
    assert len(summary.generator_performance) > 0
    assert len(summary.confidence_calibration) > 0


@pytest.mark.asyncio
async def test_get_generator_performance_for_specific_generator(
    session, principal, inventory_outcomes
):
    """Returns performance for single generator."""
    service = CalibrationService(session)
    performance = await service.get_generator_performance(
        principal=principal, generator="inventory"
    )

    assert performance is not None
    assert performance.generator_name == "inventory"
    assert performance.metrics.sample_size == 25
    assert performance.quality_score is not None
```

---

## Business Questions Answered

### 1. Which recommendation types perform best?

**Query**: `GET /recommendations/calibration`

**Answer**: Check `best_performing_generators` list, which ranks by quality score.

**Quality Score Formula**:
```
Quality = 0.4 × direction_accuracy + 0.3 × calibration + 0.3 × success_rate
```

**Interpretation**:
- Quality > 0.85: Excellent, high trust
- Quality 0.70-0.85: Good, generally reliable
- Quality 0.50-0.70: Moderate, review before trusting
- Quality < 0.50: Poor, needs investigation

### 2. Which systematically overestimate impact?

**Query**: `GET /recommendations/calibration`

**Answer**: Check `needs_calibration` list. Generators with `bias_percentage < -10%` overestimate.

**Example**:
```json
{
  "generator_name": "pricing",
  "metrics": {
    "bias_percentage": -25.3,
    "systematically_overestimates": true
  }
}
```

**Interpretation**: Pricing recommendations deliver 25% less impact than expected on average.

### 3. Which systematically underestimate impact?

**Query**: Same as above, but `bias_percentage > +10%`.

**Example**:
```json
{
  "generator_name": "inventory",
  "metrics": {
    "bias_percentage": 18.2,
    "systematically_underestimates": true
  }
}
```

**Interpretation**: Inventory recommendations deliver 18% more impact than expected.

### 4. Are high-confidence recommendations more reliable?

**Query**: `GET /recommendations/calibration/confidence`

**Answer**: Compare confidence bands. A well-calibrated system has:
- Confidence 0.8-1.0 → Actual success ~90%
- Confidence 0.6-0.8 → Actual success ~70%
- Confidence 0.4-0.6 → Actual success ~50%

**Example**:
```json
{
  "confidence_band": "0.80-1.00",
  "expected_success_rate": 0.90,
  "actual_success_rate": 0.76,
  "calibration_error": 0.14,
  "is_overconfident": true
}
```

**Interpretation**: We're overconfident. High-confidence recommendations succeed only 76% of the time, not 90%.

### 5. Are measured-impact estimates more accurate than modelled ones?

**Query**: `GET /recommendations/calibration/generators/{generator}`

**Answer**: Check `estimate_basis_breakdown`:

```json
{
  "estimate_basis_breakdown": {
    "measured": {
      "sample_size": 40,
      "mean_realization_ratio": 0.92,
      "bias_percentage": -8.0
    },
    "modelled": {
      "sample_size": 35,
      "mean_realization_ratio": 0.68,
      "bias_percentage": -32.0
    }
  }
}
```

**Interpretation**: Measured-basis estimates realize 92% of expected, while modelled-basis only 68%. Measured is more reliable.

### 6. Which assumptions are repeatedly wrong?

**Analysis**: Look for generators with:
- Low `direction_accuracy` (<0.70): assumptions about direction are wrong
- High `|bias_percentage|` (>20%): assumptions about magnitude are wrong
- Low `success_rate` (<0.60): assumptions lead to poor outcomes

**Action**: Review assumptions in that generator's estimator logic.

### 7. Which recommendation generators should be trusted most?

**Query**: `GET /recommendations/calibration`

**Answer**: Top of `best_performing_generators` list.

**Trust Criteria**:
- Quality score > 0.80
- Direction accuracy > 0.85
- Bias within ±10%
- Success rate > 0.75
- Sufficient sample size (N ≥ 20)

---

## What This System Does NOT Do

### ❌ Automatic Recommendation Changes

The calibration engine **learns** but does **not automatically alter** production recommendations.

**Why**: Changing recommendations based on small samples is dangerous. A few bad outcomes don't invalidate the logic.

**Instead**: The system:
1. Surfaces generators with systematic bias
2. Provides data to inform manual review
3. Helps humans decide whether to adjust estimator logic

### ❌ ML Model Retraining

This is **not** a feedback loop into machine learning models.

**Why**: RetailMind's forecasters and estimators are deterministic, not learned models.

**Instead**: The system helps identify:
- Which assumptions are wrong
- Which data sources are unreliable
- Which recommendation types need design changes

### ❌ Statistical Claims with Insufficient Data

The system **never** claims statistical significance without sufficient samples.

**Thresholds**:
- General metrics: MIN_SAMPLE_SIZE = 20
- Confidence calibration: MIN_CONFIDENCE_CALIBRATION_SAMPLES = 30

**Behavior**: All properties check `is_statistically_significant` before returning `True` for bias/calibration flags.

### ❌ Hidden Limitations

Every calibration summary includes a `limitations` list:
- Small sample sizes
- Generators with <20 samples
- Measurement window immaturity
- Data quality issues

**Example**:
```json
{
  "limitations": [
    "Small sample size (N=12). Results not statistically reliable.",
    "Some generators have <20 samples: customer, supplier"
  ]
}
```

---

## Interpretation Guidelines

### How to Use Calibration Data

1. **Review Weekly**: Check calibration summary after new outcomes are measured.

2. **Investigate Biased Generators**:
   - If `bias_percentage > 20%`, review estimator assumptions
   - Check `estimate_basis_breakdown` — is one basis consistently wrong?
   - Look at `horizon_breakdown` — does bias appear at specific horizons?

3. **Trust Best Performers**:
   - Generators with quality > 0.80 and N ≥ 30 are reliable
   - Promote these recommendations to users first

4. **Fix Overconfidence**:
   - If confidence bands show `is_overconfident: true`, review confidence scoring logic
   - Consider downgrading confidence for that generator

5. **Don't Overreact to Small Samples**:
   - N < 20: Ignore metrics, wait for more data
   - 20 ≤ N < 50: Note trends, don't act yet
   - N ≥ 50: Trust the data, consider changes

### How NOT to Use Calibration Data

1. **Don't Disable Generators After a Few Bad Outcomes**:
   - Bad: "3 pricing recommendations failed, disable pricing"
   - Good: "After 30 pricing measurements, bias is -35%. Review estimator."

2. **Don't Change Confidence Scores Manually**:
   - Bad: "Pricing is overconfident, multiply all confidence by 0.8"
   - Good: "Review confidence calculation logic in pricing estimator"

3. **Don't Expect Perfection**:
   - Realization ratio of 0.85 is good, not bad
   - Direction accuracy of 0.80 is acceptable for noisy retail data

4. **Don't Ignore Limitations**:
   - If the summary says "insufficient data", don't trust the metrics

---

## Remaining Work Estimate

| Task | Estimated Time |
|------|----------------|
| Implement CalibrationService | 2-3 days |
| Add API endpoints | 1 day |
| Update Decision Center UI | 1-2 days |
| Write unit tests (calculator) | 1 day |
| Write integration tests (service, API) | 1-2 days |
| Manual testing and validation | 1 day |
| Documentation and interpretation guide | 0.5 days |
| **Total** | **7.5-10.5 days** |

---

## Files to Create

- ✅ `backend/app/services/calibration/__init__.py`
- ✅ `backend/app/services/calibration/models.py`
- ✅ `backend/app/services/calibration/calculator.py`
- 📝 `backend/app/services/calibration/service.py`
- 📝 `backend/tests/unit/test_calibration_calculator.py`
- 📝 `backend/tests/integration/test_calibration_service.py`
- 📝 `backend/app/api/v1/recommendations.py` (add endpoints)
- 📝 `ui/workspaces/3_Decision_Center.py` (add learning section)

---

## Next Steps

1. **Implement CalibrationService** using the code above
2. **Add API endpoints** to expose calibration data
3. **Update Decision Center UI** with compact learning section
4. **Write tests** for calculator and service
5. **Validate** with real measured outcomes
6. **Document** interpretation guidelines for business users

---

## Summary

**Foundation Complete**:
- ✅ Domain models with proper thresholds
- ✅ Pure calculator functions
- ✅ Quality scoring formula
- ✅ Systematic bias detection
- ✅ Confidence calibration logic

**Remaining**:
- Service layer (queries DB, orchestrates calculators)
- API endpoints (expose to UI)
- UI section (compact, actionable, honest)
- Tests (unit + integration)

**Design Principles Enforced**:
- Pure functions for testability
- Explicit sample size exposure
- Statistical significance thresholds
- No automatic changes to production
- Honest limitations in every summary
- Business questions as first-class concern

The calibration engine learns from outcomes but never hides uncertainty or claims significance without data.
