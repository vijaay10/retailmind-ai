# Recommendation Engine Guide

RetailMind AI recommendation system - 7 generators, decision tracking, outcome measurement, and calibration loop.

**Last Updated**: 2026-08-15
**Version**: 0.9.0
**Code**: `backend/app/services/recommendations/`

---

## Overview

The recommendation engine generates actionable suggestions with impact estimates, tracks user decisions, and measures actual outcomes for continuous improvement.

### Key Features

- **7 generators** (reorder, markdown, price_adjust, promotion, retention, supplier, allocation)
- **Impact estimation** (profit, revenue, risk)
- **Decision tracking** (accept/reject/defer with rationale)
- **Outcome measurement** (actual vs. projected)
- **Calibration loop** (generator weight adjustment)

### Decision Loop

```
Signal → Investigate → Recommend → Decide → Execute → Measure → Calibrate
```

---

## Generators

### 1. Reorder (Inventory)

**Trigger**: Stock below reorder point

**Recommendation**:

```json
{
  "type": "reorder",
  "subject": {
    "sku": "SKU-12345",
    "store_id": "STORE-042",
    "current_stock": 50,
    "reorder_point": 120,
    "suggested_qty": 500,
    "order_by_date": "2026-08-20"
  },
  "expected_impact": {
    "metric": "revenue",
    "value_usd": 45000,
    "method": "forecast_driven",
    "confidence": "high"
  },
  "rationale": "Stock at 50 units (58% below reorder point). 7-day forecast shows 480 units demand."
}
```

### 2. Markdown (Pricing)

**Trigger**: Excess inventory, slow turnover

**Recommendation**:

```json
{
  "type": "markdown",
  "subject": {
    "sku": "SKU-99999",
    "current_price": 99.99,
    "suggested_price": 79.99,
    "markdown_pct": 0.20,
    "duration_days": 14
  },
  "expected_impact": {
    "metric": "profit",
    "value_usd": 12000,
    "method": "elasticity_model",
    "confidence": "medium"
  }
}
```

### 3-7. Other Generators

- **Price Adjust** - Competitive pricing optimization
- **Promotion** - Promotional campaign suggestions
- **Retention** - Customer retention actions
- **Supplier** - Supplier switch recommendations
- **Allocation** - Inventory allocation across stores

---

## Impact Estimation

**Honesty Rule**: Method MUST be declared (no "magic" predictions).

**Methods**:
- `forecast_driven` - Based on forecast model
- `elasticity_model` - Price elasticity curve
- `historical_avg` - Historical average impact
- `rule_of_thumb` - Industry benchmark
- `assumed` - Hypothesis (not verified)

**Example**:

```python
expected_impact = {
    "metric": "profit",
    "value_usd": 12000,
    "method": "elasticity_model",  # REQUIRED
    "confidence": "medium",
    "basis": "Last 3 markdown events averaged +$11K profit"
}
```

---

## Decision Tracking

**Table**: `recommendation_decision`

```sql
CREATE TABLE recommendation_decision (
  id UUID PRIMARY KEY,
  recommendation_id UUID,
  user_id UUID,
  decision TEXT,  -- 'accept', 'reject', 'defer'
  rationale TEXT,
  decided_at TIMESTAMP
);
```

**Example Decision**:

```bash
POST /api/v1/recommendations/{id}/decide
{
  "decision": "accept",
  "rationale": "Aligns with Q3 inventory strategy"
}
```

---

## Outcome Measurement

**Table**: `recommendation_outcome`

```sql
CREATE TABLE recommendation_outcome (
  id UUID PRIMARY KEY,
  recommendation_id UUID,
  measured_at TIMESTAMP,
  projected_revenue DECIMAL,
  actual_revenue DECIMAL,
  variance_revenue DECIMAL,
  projected_profit DECIMAL,
  actual_profit DECIMAL,
  variance_profit DECIMAL
);
```

**Measurement Period**: 7-30 days after execution

**Example Outcome**:

```json
{
  "projected_profit": 12000,
  "actual_profit": 13500,
  "variance_profit": +1500,  // 12.5% better than projected
  "status": "confirmed"
}
```

---

## Calibration Loop

**Generator weights** adjust based on historical accuracy.

```python
def calibrate_generator(generator_id: str):
    """Adjust generator weight based on outcomes."""
    outcomes = get_outcomes(generator_id)

    # Compute average variance
    avg_variance_pct = mean([
        (o.actual_profit - o.projected_profit) / o.projected_profit
        for o in outcomes
    ])

    # Adjust weight
    if abs(avg_variance_pct) > 0.30:  # >30% error
        generator.weight *= 0.9  # Reduce influence
    else:
        generator.weight *= 1.05  # Increase trust

    log.info(f"Calibrated {generator_id}: weight={generator.weight}")
```

---

**Maintained by**: RetailMind AI Contributors
**License**: MIT
**Last Reviewed**: 2026-08-15
