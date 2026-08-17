# Business Anomaly Detection System

## Overview

RetailMind's anomaly detection system proactively monitors key business metrics and alerts users when values deviate significantly from expected patterns. The system uses five complementary detection methods, each optimized for different types of anomalies.

**Core Principle**: Use the simplest defensible method for each type of anomaly. Complex models are harder to explain and maintain. Simple statistical methods with clear thresholds build trust and are easier to debug.

## Detection Methods

### 1. Rolling Baseline Anomaly Detection

**Purpose**: Detect deviations from recent historical average.

**Method**: Compares current value to 14-day rolling average (simple moving average).

**Use Cases**:
- Detecting sudden revenue drops or spikes
- Identifying breaks in recent trends
- Catching operational disruptions

**Thresholds**:
| Deviation | Severity | Reasoning |
|-----------|----------|-----------|
| < 15% | No alert | Normal business variation |
| 15-25% | MEDIUM | Notable deviation, investigate |
| 25-40% | HIGH | Significant deviation, urgent review |
| > 40% | CRITICAL | Severe deviation, immediate action |

**Minimum Data**: 14 historical data points

**Evidence Provided**:
- Current value
- Rolling baseline (14-day average)
- Deviation percentage
- Number of days analyzed

**Implementation**: `app/services/notifications/detectors.py::rolling_baseline_anomaly()`

### 2. Seasonal Baseline Anomaly Detection

**Purpose**: Detect deviations from comparable prior period (year-over-year).

**Method**: Compares current period to same period last year.

**Use Cases**:
- Year-over-year revenue tracking
- Holiday period comparisons
- Seasonal pattern validation

**Thresholds**:
| Deviation | Severity | Reasoning |
|-----------|----------|-----------|
| < 20% | No alert | Normal year-over-year variation |
| 20-35% | MEDIUM | Material YoY change |
| 35-50% | HIGH | Severe YoY decline/growth |
| > 50% | CRITICAL | Fundamental business change |

**Minimum Baseline**: 100 units (avoids false positives on tiny numbers)

**Evidence Provided**:
- Current value
- Seasonal baseline value
- Baseline period label
- Deviation percentage
- Direction (up/down)

**Implementation**: `app/services/notifications/detectors.py::seasonal_baseline_anomaly()`

### 3. Forecast Residual Anomaly Detection

**Purpose**: Detect when actuals deviate significantly from forecasts.

**Method**: Compares actual revenue to forecast prediction, using prediction intervals when available.

**Use Cases**:
- Plan variance detection
- Forecast accuracy monitoring
- Budget vs actual tracking

**Thresholds**:
| Error | Severity | Reasoning |
|-------|----------|-----------|
| < 20% | No alert | Within acceptable forecast error |
| 20-35% | MEDIUM | Material forecast miss |
| 35-50% | HIGH | Severe forecast miss, plans at risk |
| > 50% | CRITICAL | Forecast fundamentally wrong |

**Special Cases**:
- If actual is outside 80% prediction interval AND error >= 35%, escalate to CRITICAL
- Alerts if outside prediction interval even if error < 20%

**Confidence Level**: 80% prediction intervals

**Evidence Provided**:
- Actual value
- Forecast value
- Error percentage
- Prediction interval bounds (if available)
- Whether outside prediction interval

**Implementation**: `app/services/notifications/detectors.py::forecast_residual_anomaly()`

### 4. Statistical Process Control (Control Limits)

**Purpose**: Detect when metric goes out of statistical control (3-sigma rule).

**Method**: Calculates mean and standard deviation from 20+ historical points, alerts when current value exceeds mean ± 3σ.

**Use Cases**:
- Detecting special cause variation
- Quality control for business processes
- Identifying process instability

**Thresholds**:
| Sigma Distance | Severity | Reasoning |
|---------------|----------|-----------|
| < 3.0σ | No alert | Within control limits |
| 3.0-3.5σ | MEDIUM | Early warning, verify special cause |
| 3.5-4.0σ | HIGH | High probability of special cause |
| > 4.0σ | CRITICAL | Almost certainly special cause |

**Minimum Data**: 20 historical points

**Zero Variance Handling**: Gracefully handles cases where all historical values are identical

**Evidence Provided**:
- Current value
- Process mean
- Standard deviation
- Lower control limit (LCL)
- Upper control limit (UCL)
- Sigma distance
- Baseline points used

**Implementation**: `app/services/notifications/detectors.py::control_limits_anomaly()`

### 5. Rate-of-Change Anomaly Detection

**Purpose**: Detect accelerating or decelerating trends.

**Method**: Compares recent rate of change (last 2 periods) to longer-term rate (all prior periods). Alerts when acceleration exceeds threshold.

**Use Cases**:
- Detecting accelerating revenue declines
- Catching sudden momentum shifts
- Early warning for trend reversals

**Thresholds**:
| Acceleration | Severity | Reasoning |
|--------------|----------|-----------|
| < 30% | No alert | Normal trend variation |
| 30-50% | MEDIUM | Notable acceleration |
| 50-75% | HIGH | Rapid acceleration |
| > 75% | CRITICAL | Extreme acceleration |

**Minimum Data**: 5 periods (need 3+ changes to compare rates)

**Near-Zero Handling**: Skips alert if longer-term rate < 0.01 to avoid division issues

**Evidence Provided**:
- Recent rate of change
- Longer-term rate
- Acceleration percentage
- Direction description
- Periods analyzed

**Implementation**: `app/services/notifications/detectors.py::rate_of_change_anomaly()`

## Severity Levels

The system uses a 5-level severity scale:

| Severity | Meaning | User Action |
|----------|---------|-------------|
| INFO | Informational only | Review when convenient |
| LOW | Minor deviation | Monitor trend |
| MEDIUM | Notable deviation | Investigate within 24h |
| HIGH | Significant deviation | Investigate today |
| CRITICAL | Severe deviation | Immediate action required |

**Backwards Compatibility**: Legacy WARN severity maps to MEDIUM.

## Alert Spam Prevention

Each detector includes multiple safeguards against false positives:

1. **Minimum Absolute Thresholds**:
   - Rolling baseline: 15% deviation minimum
   - Seasonal baseline: 20% minimum + 100 unit minimum baseline
   - Forecast residual: 20% error minimum
   - Control limits: 3.0 sigma minimum
   - Rate-of-change: 30% acceleration minimum

2. **Minimum Data Requirements**:
   - Rolling baseline: 14 points
   - Seasonal baseline: Non-zero baseline > 100 units
   - Forecast residual: Non-zero forecast
   - Control limits: 20 points
   - Rate-of-change: 5 points

3. **Fingerprinting & Suppression**:
   - Each alert has a fingerprint: hash(kind, subject, severity)
   - Same fingerprint won't re-alert within suppression window
   - Severity escalation creates new fingerprint (allows re-notification)

4. **Graceful Degradation**:
   - Zero values handled (no division by zero)
   - Insufficient data returns empty list (no alert)
   - Detectors fail independently (one failure doesn't stop others)

## Integration with Notification Service

Detectors are called by `NotificationService.sweep()` which:

1. Runs all 11 detectors in parallel (6 existing + 5 new)
2. Catches exceptions per detector (failures don't stop sweep)
3. Applies suppression to avoid duplicates
4. Fans out to configured channels (in-app, email, slack)
5. Honors permission checks (users only see alerts they can act on)

**Detection Schedule**: Sweeps run hourly via scheduled task.

**Service Integration**: `app/services/notifications/service.py`
- `_rolling_baseline_anomaly()`: Fetches 28 days of revenue
- `_seasonal_baseline_anomaly()`: Compares current 7 days vs year-ago 7 days
- `_forecast_residual_anomaly()`: Compares today's actual vs forecast
- `_control_limits_anomaly()`: Fetches 30 days for baseline
- `_rate_of_change_anomaly()`: Fetches 14 days for trend analysis

## Evidence Structure

Every alert includes structured evidence for audit and debugging:

```python
{
    "method": "rolling_baseline",  # Detection method used
    "current": 850.0,              # Current observed value
    "baseline": 1000.0,            # Expected baseline
    "deviation": -0.15,            # Deviation (as decimal)
    "window_days": 14,             # Analysis window
    # ... method-specific fields
}
```

**Audit Trail**: Evidence allows tracing back why an alert fired and with what data.

## Testing Strategy

Each detector has comprehensive tests (`tests/unit/test_anomaly_detectors.py`):

1. **Normal Variation**: Verify no alert for acceptable variation
2. **True Anomalies**: Verify alert triggers at correct threshold
3. **Severity Escalation**: Test MEDIUM → HIGH → CRITICAL thresholds
4. **Edge Cases**: Insufficient data, zero values, missing data
5. **Cross-Detector Consistency**: All return lists, all include evidence

**Test Coverage**: 29 tests, all passing.

## Decision Loop Integration

Anomaly detection is the entry point to the decision loop:

```
Alert Detected
    ↓
User Clicks Alert
    ↓
INVESTIGATE (RCA Engine)
    ↓
Root Cause Identified
    ↓
FORECAST (What happens if we do nothing?)
    ↓
RECOMMEND (Proposed actions with profit/risk)
    ↓
User Accepts/Dismisses
    ↓
Outcome Measurement (Did it work?)
```

**Key Insight**: Detectors don't recommend actions. They detect conditions and provide evidence. The RCA engine explains *why*, the recommendation engine proposes *what to do*.

## Performance Considerations

**Query Efficiency**:
- All detectors fetch limited date ranges (14-30 days)
- Use indexed date filters
- Aggregate at database (not Python)
- Limit to network-level or top 100 entities

**Expected Latency** (per detector):
- Rolling baseline: ~50ms
- Seasonal baseline: ~100ms (2 queries)
- Forecast residual: ~80ms
- Control limits: ~60ms
- Rate-of-change: ~50ms

**Total Sweep Time**: ~2-3 seconds for all 11 detectors in parallel.

## Maintenance & Tuning

**Threshold Calibration**:
- Monitor alert volume: target 2-5 alerts per day
- If too many alerts: increase minimum thresholds
- If too few: decrease thresholds or add new metrics
- Track false positive rate via user dismissals

**Expanding to New Metrics**:

Currently all detectors monitor `net_revenue`. To add new metrics:

1. Update service methods to query different metrics
2. Use same detector functions (they're metric-agnostic)
3. Add permission checks for new metric types
4. Consider metric-specific thresholds (inventory vs revenue)

**Example**: Monitor `inventory_on_hand`:
```python
async def _rolling_baseline_inventory(self, principal: Principal, as_of: date):
    # Fetch inventory data
    answer = await self._analytics.query(...)
    # Call detector with inventory thresholds
    return detectors.rolling_baseline_anomaly(
        metric="inventory_on_hand",
        current_value=current,
        historical_values=historical,
        as_of=as_of,
        # Could override thresholds here
    )
```

## Files Modified

**Core Implementation**:
- `app/infrastructure/db/models/enums.py`: Added LOW, MEDIUM, HIGH to Severity
- `app/services/notifications/contracts.py`: Updated Severity enum with backwards compat
- `app/services/notifications/detectors.py`: Added 5 new detector functions
- `app/services/notifications/service.py`: Integrated 5 new detectors into sweep

**Testing**:
- `tests/unit/test_anomaly_detectors.py`: 29 new tests for all detection methods

**Documentation**:
- `docs/anomaly_detection.md`: This file

## Future Enhancements

**Potential Additions** (not yet implemented):
1. **Multivariate Detection**: Alert when multiple metrics deviate together
2. **Segment-Level Detection**: Per-store, per-SKU anomalies (currently network-level)
3. **Time-of-Day Patterns**: Detect unusual hourly patterns
4. **Correlation Alerts**: Revenue down + Inventory up = investigate supplier
5. **Anomaly Scoring**: Composite score from multiple detectors

**When to Add**:
- Only if current detectors miss important anomalies
- Only if false positive rate is acceptable (< 30%)
- Only if explainable to non-technical users

## References

**Detection Theory**:
- Rolling baseline: Simple moving average (SMA)
- Seasonal baseline: Year-over-year comparison
- Forecast residual: MAPE (Mean Absolute Percentage Error)
- Control limits: Shewhart control charts (Western Electric rules)
- Rate-of-change: First-order difference acceleration

**Implementation Details**:
- All detectors use `AlertCandidate` contract
- All include `AlertKind` for categorization
- All derive severity from magnitude (not detector preference)
- All handle missing/zero data gracefully

**Related Systems**:
- RCA Engine: `app/services/analyst/`
- Forecasting: `ml/forecasting/`
- Recommendations: `app/services/recommendations/`
- Outcome Measurement: `OUTCOME_MEASUREMENT_IMPLEMENTATION.md`
