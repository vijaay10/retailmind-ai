# Recommendation Outcome Measurement — Implementation Summary

**Date**: 2026-08-13
**Status**: Foundation Complete — Ready for Implementation
**Estimated Remaining Work**: 12-18 developer-days

> ⚠️ **Stale as of 2026-08-17.** The "12-18 developer-days remaining"
> estimate is obsolete — the work completed across Prompts 10.5–12.5
> (real root-cause fixes verified against live data, not estimated). See
> `docs/prompt-11.5-remediation-report.md` onward. Kept as a design-intent
> record, not a status report.

---

## What Was Delivered

### 1. Complete Database Schema ✅

**Migration**: `backend/app/infrastructure/db/migrations/versions/202608131600_outcome_measurement.py`

Enhanced `recommendation_outcome` table with 18 new columns:

- **Lifecycle**: `status`, `measurement_attempts`, `last_attempt_at`, `error_message`
- **Windows**: `baseline_window_start/end`, `observation_window_start/end`
- **Baseline**: `baseline_method`, `baseline_value`
- **Impact**: `observed_value`, `realized_impact`, `expected_impact`, `absolute_error`, `realization_ratio`, `direction_correct`
- **Quality**: `measurement_confidence`, `limitations`

**Migration is production-ready**:
- ✅ Backward-compatible (all columns nullable)
- ✅ Handles existing tables gracefully
- ✅ Includes proper indexes
- ✅ Enum check constraints
- ✅ Safe to run multiple times

### 2. Enhanced Domain Models ✅

**Enums** (`backend/app/infrastructure/db/models/enums.py`):
- `OutcomeStatus`: PENDING → MEASURING → MEASURED/FAILED/INSUFFICIENT_DATA
- `BaselineMethod`: COMPARABLE_PERIOD, PRE_DECISION, PEER_BASELINE, FORECAST_BASELINE

**Database Model** (`backend/app/infrastructure/db/models/recommendations.py`):
- Updated `RecommendationOutcome` with all new fields
- Proper relationships and constraints
- Full inline documentation

**Domain Models** (`backend/app/services/outcomes/models.py`):
- `MeasurementWindow`: Time window calculations
- `BaselineCalculation`: How baseline was calculated
- `ObservationResult`: What was observed
- `ImpactMeasurement`: Realized vs. expected
- `MeasurementResult`: Complete measurement record
- `OutcomeRecord`: Flattened for persistence

All models include:
- Proper type hints
- Validation logic
- `as_dict()` serialization
- Comprehensive docstrings

### 3. Complete Implementation Guide ✅

**File**: `OUTCOME_MEASUREMENT_IMPLEMENTATION.md`

Includes:
- Detailed architecture overview
- Step-by-step implementation instructions for each component
- Code snippets for all major methods
- Testing strategies
- Example workflow
- Known limitations

---

## Architecture Design

### Lifecycle Flow

```
Decision Made (User accepts recommendation)
    ↓
Create Pending Outcomes (H+1, H+7, H+14, H+30)
    ↓
Wait for Observation Window to Mature
    ↓
Daily Background Job Runs
    ↓
For Each Mature Outcome:
    1. Calculate Baseline (comparable period/pre-decision/peer/forecast)
    2. Query Observation (semantic layer)
    3. Calculate Impact (realized vs. expected)
    4. Assess Confidence (baseline + data quality)
    5. Persist Result
    ↓
Status: MEASURED (or FAILED/INSUFFICIENT_DATA)
    ↓
User Views Results in Decision Center
```

### Component Structure

```
app/services/outcomes/
├── __init__.py                    ✅ Created
├── models.py                      ✅ Created (domain models)
├── baseline.py                    📝 Needs implementation
├── observation.py                 📝 Needs implementation
├── impact.py                      📝 Needs implementation
└── measurement.py                 📝 Needs implementation

app/infrastructure/db/repositories/
└── outcomes.py                    📝 Needs implementation

app/workers/tasks/
└── outcomes.py                    📝 Needs implementation

app/api/v1/
└── recommendations.py             📝 Needs endpoint additions

ui/workspaces/
└── 3_Decision_Center.py           📝 Needs UI updates
```

---

## Baseline Calculation Strategy

### Method Selection by Category

| Recommendation Type | Primary Method | Fallback |
|---------------------|----------------|----------|
| Inventory (reorder) | `forecast_baseline` | `comparable_period` |
| Pricing | `pre_decision` | `comparable_period` |
| Promotion | `comparable_period` | `pre_decision` |
| Store improvement | `peer_baseline` | `pre_decision` |
| Customer retention | `pre_decision` | `forecast_baseline` |

### Baseline Windows

- **H+1 (1 day)**: 7-day baseline period
- **H+7 (1 week)**: 7-day baseline period
- **H+14 (2 weeks)**: 14-day baseline period
- **H+30 (1 month)**: 30-day baseline period

Baseline ends the day before the decision date.

---

## Measurement Confidence Assessment

### High Confidence
- ✅ Baseline from measured data (not assumptions)
- ✅ >90% data completeness
- ✅ No confounding events detected
- ✅ Clear direction match

### Medium Confidence
- ⚠ Baseline from forecast or peer comparison
- ⚠ 70-90% data completeness
- ⚠ Minor confounding events (e.g., holiday in window)
- ⚠ Large error but correct direction

### Low Confidence
- ❌ Baseline from placeholder assumptions
- ❌ <70% data completeness
- ❌ Major confounding events (promotion, stockout, store closure)
- ❌ Direction mismatch

**All measurements document their confidence and limitations explicitly.**

---

## Example Outcome

```json
{
  "decision_key": "a3f891c2...",
  "horizon_days": 7,
  "status": "measured",

  "baseline": {
    "method": "comparable_period",
    "value": 15420.00,
    "confidence": "high",
    "limitations": [],
    "metadata": {
      "lookback_start": "2025-07-01",
      "lookback_end": "2025-07-07",
      "data_days": 7
    }
  },

  "observation": {
    "value": 17890.00,
    "data_completeness": 1.0,
    "confounding_events": []
  },

  "impact": {
    "baseline_value": 15420.00,
    "observed_value": 17890.00,
    "realized_impact": 2470.00,
    "expected_impact": 3200.00,
    "absolute_error": 730.00,
    "realization_ratio": 0.7719,
    "direction_correct": true
  },

  "measurement_confidence": "high",
  "limitations": [],
  "measured_at": "2026-08-13T04:00:00Z"
}
```

**Honest interpretation**:
- "Revenue increased during the 7-day measurement window"
- "The observed result was 77% of expected impact"
- **NOT**: "This recommendation caused revenue to increase by £2,470"

---

## Remaining Implementation Work

### Critical Path (Must Complete)

1. **Baseline Calculator** (2-3 days)
   - `comparable_period`: Query same calendar period last year
   - `pre_decision`: Average of N days before decision
   - `peer_baseline`: Average of similar stores/SKUs
   - `forecast_baseline`: Use existing forecast if available
   - Method selection logic

2. **Observation Service** (1-2 days)
   - Parse recommendation subject to filters
   - Query semantic layer for metric during observation window
   - Calculate data completeness
   - Detect confounding events

3. **Impact Calculator** (1 day)
   - Safe division (handle zero denominators)
   - Direction checking
   - Confidence assessment
   - Limitation aggregation

4. **Measurement Service** (1-2 days)
   - Orchestrate baseline → observation → impact
   - Window calculation
   - Error handling
   - Return `MeasurementResult`

5. **Outcome Repository** (1 day)
   - `create_pending`: On decision acceptance
   - `find_pending_measurements`: Query mature outcomes
   - `record_measurement`: Persist results
   - `update_status`: Handle failures

6. **Background Job** (1 day)
   - Celery task definition
   - Runtime function
   - Beat schedule (daily at 4 AM)
   - Retry logic

7. **Unit Tests** (2-3 days)
   - Baseline calculation methods
   - Observation querying
   - Impact calculation
   - Window calculation
   - Edge cases (zero denominators, missing data)

### Nice to Have (Can Defer)

8. **API Endpoints** (1 day)
   - `GET /recommendations/{decision_key}/outcomes`
   - `GET /recommendations/calibration`
   - `GET /recommendations/performance`

9. **UI Updates** (1 day)
   - Decision Center outcome display
   - Calibration dashboard
   - Performance metrics

10. **Integration Tests** (1-2 days)
    - End-to-end outcome measurement
    - Job execution
    - API endpoints

---

## How to Start

### Step 1: Run the Migration

```bash
cd backend
uv run alembic upgrade head
```

Verify:
```sql
\d+ recommendation_outcome
```

You should see all 18 new columns.

### Step 2: Implement Baseline Calculator

Start with `backend/app/services/outcomes/baseline.py`:

```python
from app.services.outcomes.models import BaselineCalculation

class BaselineCalculator:
    def __init__(self, analytics: AnalyticsService):
        self._analytics = analytics

    async def calculate_comparable_period(
        self, *, category: str, subject: str, ...
    ) -> BaselineCalculation:
        # TODO: Implement
        # 1. Determine lookback period (365 days for inventory, 28 for pricing)
        # 2. Parse subject to get filters (sku, store_id, region, etc.)
        # 3. Query semantic layer for metric during lookback
        # 4. Calculate average/sum
        # 5. Assess confidence based on data availability
        # 6. Return BaselineCalculation
        pass
```

Test as you go:
```bash
uv run pytest backend/tests/unit/test_outcome_baseline.py -v
```

### Step 3: Implement Observation Service

Create `backend/app/services/outcomes/observation.py`.

### Step 4: Wire Up the Rest

Follow the implementation guide step by step.

---

## Testing Strategy

### Unit Tests

Each component has pure-function logic that can be tested independently:

```python
# Test baseline calculation
def test_comparable_period_calculates_last_year_average()
def test_pre_decision_uses_recent_trend()
def test_insufficient_data_returns_low_confidence()

# Test observation
def test_observation_queries_correct_metric()
def test_data_completeness_calculation()
def test_confounding_event_detection()

# Test impact
def test_impact_calculation_with_positive_realized()
def test_impact_handles_zero_expected()
def test_direction_correctness_check()

# Test measurement
def test_window_calculation()
def test_full_measurement_orchestration()
```

### Integration Tests

```python
# Test end-to-end flow
async def test_measure_inventory_recommendation_outcome()
async def test_measurement_job_finds_and_processes_pending()
async def test_api_returns_measured_outcomes()
```

### Manual Testing

1. Accept a recommendation in UI
2. Verify pending outcomes created: `SELECT * FROM recommendation_outcome WHERE status='pending';`
3. Fast-forward time (or backdate decision): `UPDATE recommendation_outcome SET observation_window_end = '2026-08-12';`
4. Run measurement job: `celery -A app.workers.celery_app call outcomes.measure_matured`
5. Verify measured: `SELECT * FROM recommendation_outcome WHERE status='measured';`
6. View in UI

---

## Files Changed

### Created
- ✅ `backend/app/infrastructure/db/migrations/versions/202608131600_outcome_measurement.py`
- ✅ `backend/app/services/outcomes/__init__.py`
- ✅ `backend/app/services/outcomes/models.py`
- ✅ `OUTCOME_MEASUREMENT_IMPLEMENTATION.md`
- ✅ `OUTCOME_MEASUREMENT_SUMMARY.md` (this file)

### Modified
- ✅ `backend/app/infrastructure/db/models/enums.py` (added OutcomeStatus, BaselineMethod)
- ✅ `backend/app/infrastructure/db/models/recommendations.py` (enhanced RecommendationOutcome)

### To Create
- 📝 `backend/app/services/outcomes/baseline.py`
- 📝 `backend/app/services/outcomes/observation.py`
- 📝 `backend/app/services/outcomes/impact.py`
- 📝 `backend/app/services/outcomes/measurement.py`
- 📝 `backend/app/infrastructure/db/repositories/outcomes.py`
- 📝 `backend/app/workers/tasks/outcomes.py`
- 📝 `backend/tests/unit/test_outcome_*.py`
- 📝 `backend/tests/integration/test_outcomes_api.py`

### To Update
- 📝 `backend/app/workers/celery_app.py` (add beat schedule)
- 📝 `backend/app/workers/runtime.py` (add run_outcome_measurement)
- 📝 `backend/app/api/v1/recommendations.py` (add endpoints)
- 📝 `ui/workspaces/3_Decision_Center.py` (add outcome display)

---

## Next Steps

1. **Review this summary** and the detailed implementation guide
2. **Run the migration** in a dev environment
3. **Start with baseline calculator** — it's the most complex component
4. **Test each component** as you build it
5. **Wire them together** in the measurement service
6. **Add the background job** once measurement service works
7. **Add API endpoints** for UI consumption
8. **Update UI** to display results
9. **Write integration tests** to verify end-to-end flow

---

## Questions or Issues?

The implementation guide (`OUTCOME_MEASUREMENT_IMPLEMENTATION.md`) contains:
- Detailed code snippets for every component
- Edge case handling strategies
- Testing approaches
- Known limitations

All code delivered is:
- ✅ Lint-clean (ruff passed)
- ✅ Type-safe (mypy ready)
- ✅ Documented (inline + docstrings)
- ✅ Production-ready (migration is backward-compatible)

**The foundation is complete. The remaining work is well-defined and straightforward to implement.**
