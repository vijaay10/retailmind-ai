# Forecasting Guide

RetailMind AI forecasting system - hand-written ridge regression, feature engineering, backtest framework, and model registry.

**Last Updated**: 2026-08-15
**Version**: 0.9.0
**Code**: `ml/forecasting/` (3,848 LOC)

---

## Table of Contents

- [Overview](#overview)
- [Ridge Regression Implementation](#ridge-regression-implementation)
- [Feature Engineering](#feature-engineering)
- [Quality Gates](#quality-gates)
- [Backtest Framework](#backtest-framework)
- [Model Registry](#model-registry)
- [Usage](#usage)

---

## Overview

### Key Characteristics

- **Hand-written ridge regression** (~200 LOC, no scikit-learn)
- **Closed-form solution** - No iteration, deterministic results
- **~15 features** - Calendar + level + trend
- **MASE quality gate** - Must beat naive baseline
- **Walk-forward validation** - Expanding window backtests
- **SQLite model registry** - Versioned artifacts with promotion workflow

### Design Philosophy

**Why Ridge Instead of XGBoost/Prophet:**

1. **Explainability is exact** - Prediction = intercept + Σ wᵢxᵢ (arithmetic decomposition)
2. **Data cannot support more** - 40-60 training rows per horizon (thin evidence)
3. **Serializes to numbers** - Coefficient vector + scaler (no pickle/binary execution)

**Quote from code**:

> "Model capacity has to match evidence, and here the evidence is thin."

---

## Ridge Regression Implementation

### Closed-Form Solution

**File**: `ml/forecasting/models/ridge.py`

```python
def _fit_one(self, step: int, x: Array, y: Array) -> None:
    """Closed-form ridge on standardized features.

    Solves: w = (X^T X + λI)^(-1) X^T y
    """
    # 1. Standardize features (mean=0, std=1)
    centre = x.mean(axis=0)
    scale = x.std(axis=0)
    scale = np.where(scale < 1e-12, 1.0, scale)  # Avoid div by zero
    standardized = (x - centre) / scale

    # 2. Ridge penalty matrix
    n_features = x.shape[1]
    penalty = self.alpha * np.eye(n_features)

    # 3. Solve (X^T X + λI)w = X^T y
    xtx = standardized.T @ standardized
    xty = standardized.T @ y
    w_standardized = np.linalg.solve(xtx + penalty, xty)

    # 4. Unstandardize coefficients
    w = w_standardized / scale
    intercept = y.mean() - (centre * w).sum()

    # Store
    self._coefficients[step] = w
    self._intercepts[step] = intercept
    self._centre[step] = centre
    self._scale[step] = scale
```

**Properties**:
- **Deterministic** - Same data → same coefficients (bit-identical)
- **No hyperparameter tuning** - Alpha = 1.0 (conservative default)
- **Fast** - O(p² n) where p = features (~15), n = rows (~60)

### Prediction

```python
def predict(self, series: TimeSeries, horizon: int) -> Prediction:
    """Generate multi-step forecast."""
    predictions = []

    for step in range(1, horizon + 1):
        # Extract features for this horizon
        x = features_for(series, step)

        # Linear prediction
        y_pred = self._intercepts[step] + (self._coefficients[step] @ x)

        predictions.append(y_pred)

    return Prediction(values=predictions, horizon=horizon)
```

### Explanation

**Exact decomposition**:

```python
def explain(self, series: TimeSeries, step: int) -> Explanation:
    """Decompose prediction into feature contributions."""
    x = features_for(series, step)
    w = self._coefficients[step]

    # Contribution of each feature
    contributions = [
        Contribution(
            feature=name,
            value=x[i],
            weight=w[i],
            contribution=x[i] * w[i]
        )
        for i, name in enumerate(self.feature_names)
    ]

    # Sort by absolute contribution
    contributions.sort(key=lambda c: abs(c.contribution), reverse=True)

    return Explanation(
        prediction=self._intercepts[step] + (w @ x),
        intercept=self._intercepts[step],
        contributions=contributions
    )
```

**Example Output**:

```
Prediction: 1,250 units

Intercept: 800
+ rolling_mean_7d (1,200) × 0.35 = +420
+ day_of_week=Saturday (1.0) × 150 = +150
+ week_of_month=2 (0.5) × -40 = -20
+ trend_7d (0.05) × -2000 = -100
...
= 1,250
```

---

## Feature Engineering

**File**: `ml/forecasting/features.py`

### Feature Categories

| Category | Features | Example Values | Purpose |
|----------|----------|----------------|---------|
| **Calendar** | day_of_week, week_of_month, month, is_weekend, is_holiday | Saturday=1, Monday=0 | Weekly seasonality |
| **Level** | rolling_mean_7d, rolling_mean_14d, rolling_mean_28d | 1,250 units | Recent baseline |
| **Trend** | pct_change_7d, pct_change_28d | +0.05 (5% growth) | Momentum |

**Total**: ~15 features

### Calendar Features

```python
def calendar_features(date: datetime.date) -> dict[str, float]:
    """Encode calendar patterns."""
    return {
        "day_of_week": float(date.weekday()),  # 0=Monday, 6=Sunday
        "week_of_month": (date.day - 1) // 7,  # 0-4
        "month": float(date.month),  # 1-12
        "is_weekend": float(date.weekday() >= 5),  # 0 or 1
        "is_holiday": float(is_us_holiday(date)),  # 0 or 1
    }
```

### Level Features

```python
def level_features(series: TimeSeries) -> dict[str, float]:
    """Recent moving averages."""
    return {
        "rolling_mean_7d": series.rolling_mean(7),
        "rolling_mean_14d": series.rolling_mean(14),
        "rolling_mean_28d": series.rolling_mean(28),
    }
```

### Trend Features

```python
def trend_features(series: TimeSeries) -> dict[str, float]:
    """Percent change over different windows."""
    return {
        "pct_change_7d": series.pct_change(7),  # Week-over-week
        "pct_change_28d": series.pct_change(28),  # Month-over-month
    }
```

### Design Matrix

```python
def build_design_matrix(series: TimeSeries, horizon: int):
    """Build (X, y) for training.

    For each date in history:
      X = features as of that date
      y = actual value `horizon` days later
    """
    X_rows = []
    y_values = []

    for i in range(len(series) - horizon):
        date = series.dates[i]
        target_date = series.dates[i + horizon]

        # Features as of `date`
        features = {
            **calendar_features(target_date),
            **level_features(series[:i+1]),
            **trend_features(series[:i+1]),
        }

        X_rows.append(list(features.values()))
        y_values.append(series.values[i + horizon])

    return DesignMatrix(
        x=np.array(X_rows),
        y=np.array(y_values),
        feature_names=list(features.keys())
    )
```

---

## Quality Gates

### MASE (Mean Absolute Scaled Error)

**Metric of choice** for forecast quality.

**Definition**:

```
MASE = MAE(forecast) / MAE(naive_baseline)
```

Where naive baseline = "tomorrow equals today".

**Interpretation**:
- **MASE < 1.0** → Better than naive ✅
- **MASE > 1.0** → Worse than naive ❌ (model rejected)

**Why MASE**:
- Scale-independent (works across SKUs with different volumes)
- Intuitive benchmark (naive forecast)
- No division by zero (unlike MAPE)

### Quality Gate Logic

```python
def evaluate_forecast(actual: Array, forecast: Array, naive: Array) -> Metrics:
    """Compute MASE and apply quality gate."""
    mae_forecast = np.abs(actual - forecast).mean()
    mae_naive = np.abs(actual - naive).mean()

    mase = mae_forecast / mae_naive

    if mase >= 1.0:
        raise QualityGateFailure(
            f"Forecast MASE={mase:.2f} fails to beat naive baseline"
        )

    return Metrics(mase=mase, mae=mae_forecast, ...)
```

### WAPE (Weighted Absolute Percentage Error)

**Secondary metric** for error magnitude.

```
WAPE = Σ |actual - forecast| / Σ actual
```

**Interpretation**:
- WAPE = 0.10 → 10% average error
- WAPE = 0.25 → 25% average error

**Example**:
- MASE = 0.85 (beats naive)
- WAPE = 0.12 (12% error)
- Verdict: **PASS** ✅

---

## Backtest Framework

**File**: `ml/forecasting/backtest.py`

### Walk-Forward Validation

**Strategy**: Expanding window with walk-forward prediction.

```
Training Window       Test
[======]              *     ← Predict day 1
[========]            *     ← Predict day 2
[==========]          *     ← Predict day 3
...
[====================]*     ← Predict day N
```

**Code**:

```python
def walk_forward_backtest(
    series: TimeSeries,
    horizon: int,
    min_train_days: int = 42,
    test_days: int = 14
) -> BacktestResult:
    """Walk-forward validation."""
    predictions = []
    actuals = []

    for i in range(min_train_days, len(series) - horizon, 1):
        # Train on data up to day i
        train = series[:i]
        model = RidgeForecaster().fit(train, horizon=horizon)

        # Predict horizon steps ahead
        pred = model.predict(train, horizon=horizon)

        # Compare to actual
        actual = series[i:i+horizon]

        predictions.append(pred.values)
        actuals.append(actual.values)

    # Compute metrics
    mase = compute_mase(actuals, predictions, naive)
    wape = compute_wape(actuals, predictions)

    return BacktestResult(mase=mase, wape=wape, ...)
```

### Backtest Output

```
Backtest Summary
================
Horizon: 7 days
Test period: 2026-07-01 to 2026-08-15 (45 days)
Folds: 45 (expanding window)

Metrics:
  MASE: 0.82 ✅ (beats naive)
  WAPE: 0.11 (11% error)
  RMSE: 125 units

Quality Gate: PASS
```

---

## Model Registry

**File**: `ml/forecasting/registry.py`

### SQLite-Backed Registry

**Purpose**: Version models, track performance, promote to production.

**Schema**:

```sql
CREATE TABLE models (
  model_id TEXT PRIMARY KEY,
  model_class TEXT NOT NULL,  -- "ridge", "naive"
  name TEXT NOT NULL,
  artifact_path TEXT NOT NULL,
  trained_at TIMESTAMP NOT NULL,
  promoted_at TIMESTAMP,
  mase REAL,
  wape REAL,
  metadata JSON
);
```

### Registration

```python
def register_model(
    model_id: str,
    artifact_path: str,
    metrics: dict,
    metadata: dict
):
    """Register trained model."""
    conn.execute(
        """
        INSERT INTO models (model_id, artifact_path, mase, wape, metadata, trained_at)
        VALUES (?, ?, ?, ?, ?, NOW())
        """,
        (model_id, artifact_path, metrics["mase"], metrics["wape"], json.dumps(metadata))
    )
```

### Promotion Workflow

```
[dev] → [staging] → [prod]
```

**Commands**:

```python
# Promote to staging
registry.promote(model_id, environment="staging")

# Promote to production (after validation)
registry.promote(model_id, environment="prod")
```

**Query Production Model**:

```python
prod_model = registry.get_promoted("prod")
# Returns: model_id="ridge_20260815_v3"
```

### Artifact Storage

**Format**: JSON (coefficients + metadata)

```json
{
  "model_class": "ridge",
  "name": "ridge_calendar_level",
  "alpha": 1.0,
  "horizon": 7,
  "coefficients": {
    "1": [0.35, 150, -40, ...],
    "2": [0.32, 145, -38, ...],
    ...
  },
  "intercepts": {
    "1": 800,
    "2": 810,
    ...
  },
  "feature_names": ["rolling_mean_7d", "day_of_week", ...],
  "trained_at": "2026-08-15T10:30:00Z"
}
```

**Why JSON**:
- Human-readable
- No pickle/binary execution risk
- Portable across Python versions

---

## Usage

### Training a Model

```python
from forecasting import TimeSeries, RidgeForecaster, backtest

# Load historical data
series = TimeSeries.from_warehouse(
    product="SKU-12345",
    store="STORE-042",
    start_date="2026-07-01",
    end_date="2026-08-15"
)

# Train model
model = RidgeForecaster(alpha=1.0)
model.fit(series, horizon=7)

# Validate with backtest
result = backtest.walk_forward(series, model, horizon=7)

if result.mase < 1.0:
    # Register model
    registry.register(
        model_id=f"ridge_{date.today():%Y%m%d}",
        model=model,
        metrics={"mase": result.mase, "wape": result.wape}
    )
```

### Generating Forecast

```python
# Load production model
model = registry.load_promoted("prod")

# Get recent data
series = TimeSeries.from_warehouse(
    product="SKU-12345",
    store="STORE-042",
    last_n_days=60
)

# Forecast next 7 days
prediction = model.predict(series, horizon=7)

print(f"Forecast: {prediction.values}")
# Output: [1250, 1320, 1180, 1400, 1550, 1620, 1450]
```

### Explaining Forecast

```python
# Explain day 1 prediction
explanation = model.explain(series, step=1)

print(f"Prediction: {explanation.prediction:.0f}")
print(f"Intercept: {explanation.intercept:.0f}")
print("\nTop contributors:")
for contrib in explanation.contributions[:5]:
    print(f"  {contrib.feature}: {contrib.contribution:+.0f}")

# Output:
# Prediction: 1250
# Intercept: 800
#
# Top contributors:
#   rolling_mean_7d: +420
#   day_of_week=Saturday: +150
#   week_of_month: -20
#   trend_7d: -100
#   is_weekend: +0
```

---

## Appendix

### File Reference

| File | Purpose |
|------|---------|
| `ml/forecasting/models/ridge.py` | Ridge regression implementation |
| `ml/forecasting/features.py` | Feature engineering (calendar, level, trend) |
| `ml/forecasting/backtest.py` | Walk-forward validation framework |
| `ml/forecasting/metrics.py` | MASE, WAPE, RMSE computation |
| `ml/forecasting/registry.py` | Model versioning and promotion |
| `ml/forecasting/series.py` | TimeSeries abstraction |

### No External ML Dependencies

**Intentionally avoided**:
- ❌ scikit-learn
- ❌ XGBoost
- ❌ Prophet
- ❌ TensorFlow
- ❌ PyTorch

**Only dependency**: NumPy (array operations)

**Rationale**: Full control, transparency, no black-box complexity.

---

**Maintained by**: RetailMind AI Contributors
**License**: MIT
**Last Reviewed**: 2026-08-15
