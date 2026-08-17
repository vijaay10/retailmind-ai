# Recommendation Outcome Measurement — Implementation Guide

> ⚠️ **Stale as of 2026-08-17.** The database schema/migration this document
> describes is real and unchanged; the outcome-measurement service and the
> calibration API that reads it are now complete and verified against live
> data (see `docs/prompt-11.5-remediation-report.md`,
> `docs/prompt-12.5-tenant-isolation-report.md`). Kept as a design-intent
> record — do not treat "In Progress" as current status.

## Status: Foundation Complete, Implementation In Progress

This guide documents the recommendation outcome measurement system that completes the decision-intelligence feedback loop.

---

## What Has Been Completed

### 1. Database Schema & Migration ✅

**File**: `backend/app/infrastructure/db/migrations/versions/202608131600_outcome_measurement.py`

The migration enhances the `recommendation_outcome` table with:

- **Lifecycle tracking**: `status` (pending → measuring → measured/failed/insufficient_data)
- **Baseline calculation**: `baseline_method`, `baseline_window_start/end`
- **Observation tracking**: `observation_window_start/end`
- **Realized impact**: `baseline_value`, `observed_value`, `realized_impact`
- **Calibration metrics**: `expected_impact`, `absolute_error`, `realization_ratio`, `direction_correct`
- **Quality tracking**: `measurement_confidence`, `limitations`
- **Error handling**: `error_message`, `measurement_attempts`, `last_attempt_at`

**Run migration**:
```bash
cd backend
uv run alembic upgrade head
```

### 2. Enums ✅

**File**: `backend/app/infrastructure/db/models/enums.py`

Added:
- `OutcomeStatus`: PENDING, MEASURING, MEASURED, FAILED, INSUFFICIENT_DATA
- `BaselineMethod`: COMPARABLE_PERIOD, PRE_DECISION, PEER_BASELINE, FORECAST_BASELINE

### 3. Enhanced RecommendationOutcome Model ✅

**File**: `backend/app/infrastructure/db/models/recommendations.py`

The `RecommendationOutcome` model now includes all new fields with:
- Proper enum checks
- Indexed for pending measurements
- Backward-compatible (existing fields nullable)
- Full documentation

### 4. Outcome Measurement Domain Models ✅

**File**: `backend/app/services/outcomes/models.py`

Created domain models:
- `MeasurementWindow`: Time windows for baseline and observation
- `BaselineCalculation`: How the counterfactual was calculated
- `ObservationResult`: What was actually observed
- `ImpactMeasurement`: Realized vs. expected impact
- `MeasurementResult`: Complete measurement for one horizon
- `OutcomeRecord`: Flattened model for database persistence

---

## What Remains To Be Implemented

### 5. Baseline Calculation Service

**File to create**: `backend/app/services/outcomes/baseline.py`

**Purpose**: Calculate defensible counterfactual baselines for different recommendation types.

**Methods needed**:

```python
class BaselineCalculator:
    """Calculates counterfactual baselines for outcome measurement."""

    def __init__(self, analytics: AnalyticsService):
        self._analytics = analytics

    async def calculate_comparable_period(
        self,
        *,
        category: str,
        subject: str,
        decision_date: date,
        horizon_days: int,
        principal: Principal,
    ) -> BaselineCalculation:
        """Same period last year/month/week.

        For inventory: last year's sales for this SKU+store in the same calendar period
        For pricing: last month's revenue before price change
        For promotion: comparable non-promotional period
        """
        # Determine lookback period (365 days for inventory, 28 for pricing, etc.)
        # Query semantic layer for historical metric
        # Calculate baseline value
        # Assess confidence based on data availability
        # Return BaselineCalculation

    async def calculate_pre_decision(
        self,
        *,
        category: str,
        subject: str,
        decision_date: date,
        horizon_days: int,
        principal: Principal,
    ) -> BaselineCalculation:
        """Period immediately before decision.

        Use the N days before decision_date as baseline.
        Good for: store performance, customer retention, margin trends.
        """
        # Query pre-decision period (e.g., 7-30 days before)
        # Calculate average/trend
        # Extrapolate to measurement horizon
        # Return BaselineCalculation

    async def calculate_peer_baseline(
        self,
        *,
        category: str,
        subject: str,
        decision_date: date,
        horizon_days: int,
        principal: Principal,
    ) -> BaselineCalculation:
        """Peer stores/SKUs without the intervention.

        For store recommendations: average of similar stores that DIDN'T get the recommendation
        For SKU recommendations: similar SKUs in same category
        """
        # Identify peer group (similar stores, similar SKUs)
        # Query their performance during observation window
        # Calculate peer average
        # Return BaselineCalculation

    async def calculate_forecast_baseline(
        self,
        *,
        category: str,
        subject: str,
        decision_date: date,
        horizon_days: int,
        principal: Principal,
    ) -> BaselineCalculation:
        """What the forecast predicted.

        Use the platform's own forecast as baseline.
        Only valid if forecast was generated BEFORE the decision.
        """
        # Query forecast registry for prediction made before decision_date
        # Extract forecast for the observation period
        # Return BaselineCalculation

    async def select_best_method(
        self,
        *,
        category: str,
        subject: str,
        decision_date: date,
        horizon_days: int,
        principal: Principal,
    ) -> BaselineCalculation:
        """Choose the most appropriate baseline method for this recommendation."""
        # Try methods in priority order based on category:
        # - Inventory: forecast_baseline > comparable_period
        # - Pricing: pre_decision > comparable_period
        # - Store: peer_baseline > pre_decision
        # - Promotion: comparable_period
        # Return first method that has sufficient data
```

**Testing**:
```python
# backend/tests/unit/test_outcome_baseline.py
def test_comparable_period_calculates_last_year_revenue()
def test_pre_decision_uses_recent_trend()
def test_peer_baseline_averages_similar_stores()
def test_forecast_baseline_uses_existing_prediction()
def test_insufficient_data_returns_low_confidence()
def test_selects_best_method_by_category()
```

### 6. Observation Query Service

**File to create**: `backend/app/services/outcomes/observation.py`

**Purpose**: Query the semantic layer for observed results during the measurement window.

```python
class ObservationService:
    """Queries warehouse for observed outcomes."""

    def __init__(self, analytics: AnalyticsService):
        self._analytics = analytics

    async def query_observation(
        self,
        *,
        category: str,
        subject: str,
        window_start: date,
        window_end: date,
        principal: Principal,
    ) -> ObservationResult:
        """Query observed metric value during the measurement window."""
        # Parse subject to extract filters (e.g., "SKU-1@S2016" -> sku=SKU-1, store=S2016)
        # Determine metric based on category (net_revenue, units_sold, etc.)
        # Query semantic layer with filters and date range
        # Calculate data completeness (days with data / expected days)
        # Detect confounding events (promotions, stockouts, store closures)
        # Return ObservationResult

    def _parse_subject(self, category: str, subject: str) -> dict[str, str]:
        """Extract filters from subject string."""
        # "SKU-1@S2016" -> {"sku": "SKU-1", "store_id": "S2016"}
        # "S2016" -> {"store_id": "S2016"}
        # "Northeast" -> {"region": "Northeast"}

    def _detect_confounding_events(
        self,
        category: str,
        filters: dict[str, str],
        window_start: date,
        window_end: date,
    ) -> list[str]:
        """Detect events that may have influenced the outcome."""
        # Check for:
        # - Promotions during observation window
        # - Stock outages
        # - Store closures
        # - Major holidays
        # - Category-wide trends (e.g., all stores up 20%)
```

### 7. Impact Calculation Service

**File to create**: `backend/app/services/outcomes/impact.py`

```python
class ImpactCalculator:
    """Calculates realized vs. expected impact."""

    @staticmethod
    def calculate(
        *,
        baseline_value: float,
        observed_value: float,
        expected_impact: float,
    ) -> ImpactMeasurement:
        """Calculate impact metrics with safe handling of edge cases."""
        realized_impact = observed_value - baseline_value

        absolute_error = abs(realized_impact - expected_impact)

        # Safe division
        if abs(expected_impact) < 0.01:
            realization_ratio = None
        else:
            realization_ratio = realized_impact / expected_impact

        # Direction check
        expected_positive = expected_impact > 0
        realized_positive = realized_impact > 0
        direction_correct = expected_positive == realized_positive

        return ImpactMeasurement(
            baseline_value=baseline_value,
            observed_value=observed_value,
            realized_impact=realized_impact,
            expected_impact=expected_impact,
            absolute_error=absolute_error,
            realization_ratio=realization_ratio,
            direction_correct=direction_correct,
        )

    @staticmethod
    def assess_measurement_confidence(
        baseline: BaselineCalculation,
        observation: ObservationResult,
        impact: ImpactMeasurement,
    ) -> str:
        """Determine overall confidence: low | medium | high."""
        # Start with baseline confidence
        confidence = baseline.confidence

        # Downgrade if data incomplete
        if observation.data_completeness < 0.7:
            confidence = "low"
        elif observation.data_completeness < 0.9 and confidence == "high":
            confidence = "medium"

        # Downgrade if confounding events detected
        if observation.confounding_events:
            if confidence == "high":
                confidence = "medium"
            elif confidence == "medium":
                confidence = "low"

        return confidence
```

### 8. Outcome Measurement Service (Main Orchestrator)

**File to create**: `backend/app/services/outcomes/measurement.py`

```python
class OutcomeMeasurementService:
    """Orchestrates the full outcome measurement process."""

    def __init__(
        self,
        analytics: AnalyticsService,
        baseline_calculator: BaselineCalculator,
        observation_service: ObservationService,
    ):
        self._analytics = analytics
        self._baseline = baseline_calculator
        self._observation = observation_service

    async def measure_outcome(
        self,
        *,
        decision_key: str,
        category: str,
        subject: str,
        expected_impact: float,
        decision_date: date,
        horizon_days: int,
        principal: Principal,
    ) -> MeasurementResult:
        """Measure outcome for one decision at one horizon."""

        # 1. Calculate measurement windows
        window = self._calculate_window(decision_date, horizon_days)

        # 2. Check if mature
        if not window.is_mature:
            raise ValueError(f"Outcome not yet mature (need data through {window.observation_end})")

        # 3. Calculate baseline
        baseline = await self._baseline.select_best_method(
            category=category,
            subject=subject,
            decision_date=decision_date,
            horizon_days=horizon_days,
            principal=principal,
        )

        # 4. Query observation
        observation = await self._observation.query_observation(
            category=category,
            subject=subject,
            window_start=window.observation_start,
            window_end=window.observation_end,
            principal=principal,
        )

        # 5. Calculate impact
        impact = ImpactCalculator.calculate(
            baseline_value=baseline.value,
            observed_value=observation.value,
            expected_impact=expected_impact,
        )

        # 6. Assess overall confidence
        measurement_confidence = ImpactCalculator.assess_measurement_confidence(
            baseline, observation, impact
        )

        # 7. Aggregate limitations
        limitations = baseline.limitations + observation.confounding_events

        return MeasurementResult(
            decision_key=decision_key,
            horizon_days=horizon_days,
            window=window,
            baseline=baseline,
            observation=observation,
            impact=impact,
            measurement_confidence=measurement_confidence,
            limitations=limitations,
            measured_at=datetime.now(tz=UTC),
        )

    def _calculate_window(
        self,
        decision_date: date,
        horizon_days: int,
    ) -> MeasurementWindow:
        """Calculate baseline and observation windows."""
        # Baseline: same length as observation, ending day before decision
        baseline_end = decision_date - timedelta(days=1)
        baseline_start = baseline_end - timedelta(days=horizon_days - 1)

        # Observation: starts at decision, runs for horizon_days
        observation_start = decision_date
        observation_end = decision_date + timedelta(days=horizon_days - 1)

        return MeasurementWindow(
            decision_date=decision_date,
            horizon_days=horizon_days,
            baseline_start=baseline_start,
            baseline_end=baseline_end,
            observation_start=observation_start,
            observation_end=observation_end,
        )
```

### 9. Outcome Repository

**File to create**: `backend/app/infrastructure/db/repositories/outcomes.py`

```python
class OutcomeRepository:
    """Persists and retrieves recommendation outcomes."""

    def __init__(self, session: AsyncSession):
        self._session = session

    async def create_pending(
        self,
        *,
        recommendation_id: uuid.UUID | str,
        decision_key: str,
        horizon_days: int,
        decision_date: date,
        expected_impact: float,
    ) -> uuid.UUID:
        """Create a pending outcome to be measured later."""
        window = ... # Calculate window
        outcome = RecommendationOutcome(
            recommendation_id=uuid.UUID(recommendation_id) if isinstance(recommendation_id, str) else recommendation_id,
            status="pending",
            window_days=horizon_days,
            observation_window_start=window.observation_start,
            observation_window_end=window.observation_end,
            expected_impact=expected_impact,
        )
        self._session.add(outcome)
        await self._session.flush()
        return outcome.id

    async def record_measurement(
        self,
        outcome_id: uuid.UUID,
        result: MeasurementResult,
    ) -> None:
        """Update outcome with measurement results."""
        outcome = await self._session.get(RecommendationOutcome, outcome_id)
        if not outcome:
            raise ValueError(f"Outcome {outcome_id} not found")

        outcome.status = "measured"
        outcome.baseline_method = result.baseline.method
        outcome.baseline_window_start = result.window.baseline_start
        outcome.baseline_window_end = result.window.baseline_end
        outcome.baseline_value = result.baseline.value
        outcome.observed_value = result.observation.value
        outcome.realized_impact = result.impact.realized_impact
        outcome.absolute_error = result.impact.absolute_error
        outcome.realization_ratio = result.impact.realization_ratio
        outcome.direction_correct = result.impact.direction_correct
        outcome.measurement_confidence = result.measurement_confidence
        outcome.limitations = "; ".join(result.limitations)
        outcome.measured_at = result.measured_at

        await self._session.flush()

    async def find_pending_measurements(
        self,
        *,
        as_of: date | None = None,
    ) -> list[tuple[uuid.UUID, str, int, date, float]]:
        """Find outcomes ready to be measured."""
        cutoff = as_of or date.today()
        statement = (
            select(
                RecommendationOutcome.id,
                RecommendationDecision.decision_key,
                RecommendationOutcome.window_days,
                RecommendationDecision.decided_at,
                RecommendationOutcome.expected_impact,
            )
            .join(RecommendationDecision, ...)  # Join logic
            .where(
                RecommendationOutcome.status == "pending",
                RecommendationOutcome.observation_window_end <= cutoff,
                RecommendationOutcome.measurement_attempts < 3,  # Max retries
            )
        )
        rows = await self._session.execute(statement)
        return rows.all()
```

### 10. Background Job

**File to create**: `backend/app/workers/tasks/outcomes.py`

```python
@celery_app.task(name="outcomes.measure_matured", bind=True, max_retries=2)
def measure_matured_outcomes(self: Any, tenant_id: str | None = None) -> dict[str, Any]:
    """Measure outcomes for decisions whose observation window has matured."""
    from app.workers.runtime import run_outcome_measurement

    try:
        result = asyncio.run(run_outcome_measurement(tenant_id))
    except Exception as error:
        log.error("outcomes.measurement_failed", error=str(error))
        raise self.retry(exc=error, countdown=RETRY_BACKOFF) from error

    log.info("outcomes.measurement_task", **result)
    return result
```

**File to update**: `backend/app/workers/celery_app.py`

Add to `beat_schedule`:
```python
"outcome-measurement-daily": {
    "task": "outcomes.measure_matured",
    "schedule": crontab(hour=4, minute=0),  # Daily at 4 AM
    "options": {"expires": 3600},
},
```

**File to create**: `backend/app/workers/runtime.py` (add function)

```python
async def run_outcome_measurement(tenant_id: str | None = None) -> dict[str, Any]:
    """Measure matured outcomes."""
    # Build services
    # Find pending measurements
    # For each pending:
    #   Try to measure
    #   Handle insufficient data
    #   Handle errors
    #   Update status
    # Return summary
```

### 11. API Endpoints

**File to update**: `backend/app/api/v1/recommendations.py`

```python
@router.get("/{decision_key}/outcomes")
async def get_outcomes(
    decision_key: str,
    principal: PrincipalDep,
    session: SessionDep,
) -> list[dict[str, Any]]:
    """Get all measured outcomes for a decision (H+1, H+7, H+14, H+30)."""
    # Query outcomes for this decision_key
    # Return list of outcomes with measurement details

@router.get("/calibration")
async def get_calibration_summary(
    principal: PrincipalDep,
    session: SessionDep,
    category: str | None = None,
) -> dict[str, Any]:
    """Get calibration metrics across all measured outcomes."""
    # Calculate:
    # - Average realization ratio
    # - Direction accuracy %
    # - MAPE (mean absolute percentage error)
    # - By category, by estimate basis
    # Return summary

@router.get("/performance")
async def get_recommendation_performance(
    principal: PrincipalDep,
    session: SessionDep,
) -> dict[str, Any]:
    """Get recommendation acceptance and realization rates."""
    # Calculate:
    # - Total recommendations generated
    # - Acceptance rate
    # - Measurement rate (how many have outcomes)
    # - Average realization by category
    # Return dashboard data
```

### 12. UI Updates

**File to update**: `ui/workspaces/3_Decision_Center.py`

Add outcome display to decision cards:

```python
# After showing expected impact, add:
outcomes = client.get(f"/api/v1/recommendations/{decision_key}/outcomes")
if outcomes:
    latest = outcomes[0]  # Most recent horizon
    st.metric(
        "Realized Impact",
        f"£{latest['realized_impact']:,.0f}",
        delta=f"{latest['realization_ratio']:.1%} of expected"
    )

    if latest['direction_correct']:
        st.success("✓ Moved in expected direction")
    else:
        st.warning("⚠ Direction mismatch")

    if latest['limitations']:
        with st.expander("Measurement Limitations"):
            for limitation in latest['limitations']:
                st.caption(f"• {limitation}")
```

Add calibration dashboard:

```python
# New section in Decision Center
st.header("Recommendation Performance")

calibration = client.get("/api/v1/recommendations/calibration")

col1, col2, col3 = st.columns(3)
with col1:
    st.metric("Avg Realization", f"{calibration['avg_realization']:.1%}")
with col2:
    st.metric("Direction Accuracy", f"{calibration['direction_accuracy']:.1%}")
with col3:
    st.metric("Measured Decisions", calibration['measured_count'])

# By category table
st.subheader("By Category")
df = pd.DataFrame(calibration['by_category'])
st.dataframe(df)
```

### 13. Tests

**Unit tests needed**:

```python
# backend/tests/unit/test_outcome_baseline.py
- Test each baseline calculation method
- Test method selection logic
- Test edge cases (no data, insufficient data)

# backend/tests/unit/test_outcome_observation.py
- Test observation querying
- Test subject parsing
- Test confounding event detection

# backend/tests/unit/test_outcome_impact.py
- Test impact calculation
- Test zero denominator handling
- Test confidence assessment

# backend/tests/unit/test_outcome_measurement.py
- Test window calculation
- Test full measurement orchestration
- Test error handling
```

**Integration tests needed**:

```python
# backend/tests/integration/test_outcomes_api.py
def test_create_pending_outcome_on_decision()
def test_measure_outcome_when_mature()
def test_get_outcomes_for_decision()
def test_calibration_summary()
def test_outcome_measurement_job()
```

---

## Example Workflow

### 1. Decision Made
```python
# When user accepts a recommendation in UI:
decision = await decision_repo.record(
    decision_key=rec.decision_key,
    action="accepted",
    expected_profit=rec.impact.profit,
    estimate_basis=rec.impact.basis,
    ...
)

# Create pending outcomes for each horizon
for horizon in [1, 7, 14, 30]:
    await outcome_repo.create_pending(
        decision_key=rec.decision_key,
        horizon_days=horizon,
        decision_date=date.today(),
        expected_impact=rec.impact.profit,
    )
```

### 2. Daily Measurement Job Runs
```bash
# Celery beat triggers at 4 AM
# Job finds all pending outcomes where observation_window_end <= today()
# For each mature outcome:
#   - Calculate baseline
#   - Query observation
#   - Calculate impact
#   - Persist result
#   - Update status to 'measured'
```

### 3. User Views Results
```
GET /api/v1/recommendations/{decision_key}/outcomes
→ Returns all horizons with measurement details

UI shows:
Expected Impact: £42,000
Realized Impact: £36,800 (87.6% of expected)
Status: Measured
Baseline: comparable_period (high confidence)
Limitations: None
```

---

## Remaining Work Summary

| Component | Status | Effort | Priority |
|-----------|--------|--------|----------|
| Baseline calculator | Not started | 2-3 days | High |
| Observation service | Not started | 1-2 days | High |
| Impact calculator | Not started | 1 day | High |
| Measurement service | Not started | 1-2 days | High |
| Outcome repository | Not started | 1 day | High |
| Background job | Not started | 1 day | High |
| API endpoints | Not started | 1 day | Medium |
| UI updates | Not started | 1 day | Medium |
| Unit tests | Not started | 2-3 days | High |
| Integration tests | Not started | 1-2 days | Medium |

**Total estimated effort**: 12-18 days for one developer

---

## Testing the Implementation

### Run Migration
```bash
cd backend
uv run alembic upgrade head
```

### Verify Schema
```sql
SELECT column_name, data_type
FROM information_schema.columns
WHERE table_name = 'recommendation_outcome'
ORDER BY ordinal_position;
```

### Run Tests
```bash
make test  # Unit tests
make test-integration  # Integration tests
```

---

## Known Limitations

1. **No causal inference**: Measurements show correlation, not causation
2. **Confounding events**: Detection is heuristic, not statistical
3. **Baseline selection**: Automated but may not always choose optimally
4. **Data completeness**: Requires warehouse to have full data coverage
5. **Peer baselines**: Require sufficient peer population

All limitations are explicitly documented in measurement results.
