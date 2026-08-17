# Analytics & Semantic Layer Guide

RetailMind AI semantic layer - 23 analytics domains, metric registry, governed vocabulary, and caching strategy.

**Last Updated**: 2026-08-15
**Version**: 0.9.0
**Code**: `backend/app/services/analytics/` + `backend/app/infrastructure/semantic/`

---

## Table of Contents

- [Overview](#overview)
- [Semantic Layer Architecture](#semantic-layer-architecture)
- [Analytics Domains](#analytics-domains)
- [Metric Registry](#metric-registry)
- [Query Patterns](#query-patterns)
- [Caching Strategy](#caching-strategy)
- [Security & Authorization](#security--authorization)
- [API Usage](#api-usage)
- [Performance Optimization](#performance-optimization)

---

## Overview

The analytics service provides a **governed, secure, and performant** interface to the data warehouse through a semantic layer.

### Key Principles

1. **Single Source of Truth** - All metrics defined once in registry
2. **Security Boundary** - Callers send metric names, never SQL expressions
3. **Governed Vocabulary** - Only registered metrics can be queried
4. **No Raw SQL Injection** - API validates against registry before query construction
5. **Correctness Guarantees** - Ratios recomputed at requested grain (never averaged)

### Architecture Summary

```
API Request (domain=revenue, metrics=[net_revenue], dimensions=[category])
  ↓
AnalyticsService.query()
  ↓
Authorization Check (Permission.ANALYTICS_REVENUE_READ)
  ↓
Metric Registry Validation (metric "net_revenue" exists in "revenue" domain?)
  ↓
Query Builder (construct SQL from metric expressions)
  ↓
Cache Check (Redis: analytics:revenue:summary:30d)
  ↓ Cache Miss
DuckDB Warehouse Query (SELECT SUM(net_revenue) FROM v_mart_sales_daily WHERE...)
  ↓
Cache Write (TTL: 15s for summaries, 5min for breakdowns)
  ↓
AnalyticsAnswer (rows, metadata, cache status)
```

---

## Semantic Layer Architecture

### Purpose

The semantic layer translates **business questions** into **correct SQL** while enforcing:
- **Security** (RBAC per domain)
- **Correctness** (ratio recomputation, additivity rules)
- **Performance** (caching, query optimization)

### Components

| Component | Purpose | File |
|-----------|---------|------|
| **Metric Registry** | Declares all metrics with SQL expressions | `services/analytics/registry.py` |
| **AnalyticsService** | Orchestrates authorization + query execution | `services/analytics/service.py` |
| **AnalyticsRepository** | Constructs SQL, executes, caches results | `infrastructure/semantic/repository.py` |
| **DuckDB Connector** | Warehouse connection pool | `infrastructure/semantic/client.py` |

### Metric Declaration

**File**: `backend/app/services/analytics/registry.py`

```python
@dataclass(frozen=True, slots=True)
class Metric:
    key: str                          # "net_revenue"
    label: str                        # "Net Revenue"
    expression: str                   # "sum(net_revenue)" ← SQL aggregate
    additivity: Additivity            # FULL, SEMI, NON
    unit: str                         # "currency", "units", "rate"
    description: str
    ratio_of: tuple[str, str] | None  # ("numerator", "denominator")
```

**Example**:

```python
"aov": Metric(
    key="aov",
    label="Average Order Value",
    expression="sum(net_revenue) / nullif(sum(orders), 0)",
    additivity=Additivity.NON,  # Cannot be summed
    unit="currency",
    description="Recomputed at the requested grain, never averaged.",
    ratio_of=("net_revenue", "orders"),  # Components for recomputation
)
```

### Additivity Rules

**File**: `backend/app/services/analytics/registry.py`

```python
class Additivity(StrEnum):
    FULL = "full"   # Sums correctly across every dimension
    SEMI = "semi"   # Sums across dimensions but NOT across time (inventory)
    NON = "non"     # Never summed (ratios, distinct counts, rates)
```

**Why This Matters**:

```python
# WRONG: Averaging an average
SELECT AVG(aov) FROM daily_metrics  # ❌ Incorrect AOV

# CORRECT: Recomputing from components
SELECT SUM(revenue) / NULLIF(SUM(orders), 0) FROM daily_metrics  # ✅ Correct AOV
```

The registry's `ratio_of` field ensures the API always uses the correct approach.

---

## Analytics Domains

### Overview

**23 domains** organized by business function.

### Domain Catalog

| Domain | Relation | Metrics | Dimensions | Permission |
|--------|----------|---------|------------|------------|
| **revenue** | `v_mart_sales_daily` | 8 | 7 | ANALYTICS_REVENUE_READ |
| **store** | `v_mart_store_daily` | 12 | 6 | ANALYTICS_STORE_READ |
| **customer** | `v_mart_customer_daily` | 10 | 5 | ANALYTICS_CUSTOMER_READ |
| **rfm_grid** | `v_mart_customer_rfm_grid` | 6 | 3 | ANALYTICS_CUSTOMER_READ |
| **cohorts** | `v_mart_customer_cohorts` | 7 | 4 | ANALYTICS_CUSTOMER_READ |
| **lifecycle** | `v_mart_customer_lifecycle` | 8 | 3 | ANALYTICS_CUSTOMER_READ |
| **churn** | `v_mart_customer_churn_risk` | 5 | 2 | ANALYTICS_CUSTOMER_READ |
| **vip** | `v_mart_customer_vip` | 6 | 3 | ANALYTICS_CUSTOMER_READ |
| **inventory** | `v_mart_inventory_daily` | 9 | 6 | ANALYTICS_INVENTORY_READ |
| **marketing** | `v_mart_promo_daily` | 8 | 5 | ANALYTICS_MARKETING_READ |
| **profitability** | `v_mart_sales_daily` | 7 | 7 | ANALYTICS_PROFITABILITY_READ |
| **product** | `v_mart_sales_daily` | 6 | 4 | ANALYTICS_REVENUE_READ |
| **product_abc** | `v_mart_product_abc` | 5 | 3 | ANALYTICS_INVENTORY_READ |
| **inventory_health** | `v_mart_inventory_health` | 8 | 4 | ANALYTICS_INVENTORY_READ |
| **reorder** | `v_mart_reorder_suggestions` | 6 | 5 | ANALYTICS_INVENTORY_READ |
| **supplier** | `v_mart_supplier_performance` | 9 | 4 | ANALYTICS_INVENTORY_READ |
| **warehouse_health** | `v_mart_warehouse_health` | 7 | 3 | ANALYTICS_INVENTORY_READ |
| **forecast** | `v_mart_forecast` | 5 | 6 | FORECASTS_READ |
| **forecast_accuracy** | `v_mart_forecast_accuracy` | 6 | 4 | FORECASTS_READ |
| **forecast_explanation** | `v_mart_forecast_accuracy` | 4 | 3 | FORECASTS_READ |
| **rca_slice** | `v_mart_rca_slice_daily` | 5 | 8 | RCA_RUN |
| **rca_factor** | `v_mart_rca_factor_daily` | 4 | 5 | RCA_RUN |
| **rca_weather** | `v_mart_rca_weather_effect` | 6 | 4 | RCA_RUN |

### Domain Details

#### 1. Revenue (ANALYTICS_REVENUE_READ)

**Relation**: `v_mart_sales_daily`

**Metrics**:
- `net_revenue` - Gross revenue less discounts, with returns netted (FULL)
- `gross_revenue` - Revenue before discounts (FULL)
- `discount_amount` - Total discount given (FULL)
- `units_sold` - Net units, returns included as negatives (FULL)
- `orders` - Distinct orders (NON - pre-aggregated)
- `aov` - Average order value (NON - ratio, recomputed)
- `asp` - Average selling price (NON - ratio, recomputed)
- `discount_rate` - Discount as % of gross (NON - ratio, recomputed)

**Dimensions**:
- `category` - Product category
- `department` - Department
- `region` - Geographic region
- `channel` - Sales channel (online, store)
- `channel_group` - Channel grouping
- `store_cluster` - Store cluster
- `business_date` - Date

**Example Query**:

```json
{
  "domain": "revenue",
  "metrics": ["net_revenue", "aov"],
  "dimensions": ["category"],
  "start_date": "2026-07-16",
  "end_date": "2026-08-15"
}
```

**SQL Generated**:

```sql
SELECT
  category,
  SUM(net_revenue) AS net_revenue,
  SUM(net_revenue) / NULLIF(SUM(orders), 0) AS aov  -- Recomputed, not averaged
FROM v_mart_sales_daily
WHERE business_date BETWEEN '2026-07-16' AND '2026-08-15'
GROUP BY category
ORDER BY net_revenue DESC
LIMIT 100
```

#### 2. Store (ANALYTICS_STORE_READ)

**Relation**: `v_mart_store_daily`

**Metrics**:
- `net_revenue` - Store net revenue (FULL)
- `margin_amount` - Gross margin dollars (FULL)
- `margin_rate` - Gross margin % (NON - ratio)
- `foot_traffic` - Customer visits (FULL)
- `conversion_rate` - Visits → transactions (NON - ratio)
- `sales_per_sqft` - Revenue per square foot (NON - ratio)
- `labor_cost` - Store labor cost (FULL)
- `operating_expense` - Store OpEx (FULL)
- `ebitda` - Earnings before interest, taxes, depreciation, amortization (FULL)
- `headcount` - Employee count (SEMI - not additive across time)
- `utilization_rate` - Store capacity utilization (NON - ratio)
- `basket_size` - Units per transaction (NON - ratio)

**Dimensions**:
- `store_id` - Store identifier
- `store_name` - Store name
- `region` - Geographic region
- `store_cluster` - Store cluster (A/B/C)
- `format` - Store format (flagship, mall, outlet)
- `business_date` - Date

#### 3. Customer (ANALYTICS_CUSTOMER_READ)

**Relation**: `v_mart_customer_daily`

**Metrics**:
- `customers` - Active customers (NON - distinct count)
- `new_customers` - First-time buyers (NON - distinct count)
- `returning_customers` - Repeat buyers (NON - distinct count)
- `revenue` - Customer revenue (FULL)
- `orders` - Customer orders (NON - distinct)
- `aov` - Average order value (NON - ratio)
- `frequency` - Orders per customer (NON - ratio)
- `ltv` - Lifetime value (SEMI - not additive across time)
- `churn_rate` - Customer churn % (NON - ratio)
- `retention_rate` - Customer retention % (NON - ratio)

**Dimensions**:
- `segment` - Customer segment (VIP, Loyal, At-Risk, etc.)
- `cohort` - Acquisition cohort (2026-Q1, etc.)
- `channel` - Acquisition channel
- `region` - Customer region
- `business_date` - Date

#### 4. Inventory (ANALYTICS_INVENTORY_READ)

**Relation**: `v_mart_inventory_daily`

**Metrics**:
- `stock_qty` - Units in stock (SEMI - not additive across time)
- `stock_value` - Inventory value at cost (SEMI)
- `days_on_hand` - Days of supply (NON - ratio)
- `turnover` - Inventory turns (NON - ratio)
- `stockout_rate` - % of days out of stock (NON - ratio)
- `overstock_rate` - % of days overstocked (NON - ratio)
- `shrinkage` - Inventory shrinkage (FULL)
- `receiving_qty` - Units received (FULL)
- `allocation_efficiency` - Allocation efficiency score (NON - ratio)

**Dimensions**:
- `product_id` - Product SKU
- `product_name` - Product name
- `category` - Product category
- `store_id` - Store ID
- `warehouse_id` - Warehouse ID
- `business_date` - Date

#### 5. Forecasting (FORECASTS_READ)

**Domains**: `forecast`, `forecast_accuracy`, `forecast_explanation`

**Relation**: `v_mart_forecast`, `v_mart_forecast_accuracy`

**Metrics** (forecast):
- `forecast_qty` - Forecasted units (FULL)
- `forecast_revenue` - Forecasted revenue (FULL)
- `confidence_lower` - Lower bound (FULL)
- `confidence_upper` - Upper bound (FULL)
- `model_id` - Model identifier (metadata)

**Metrics** (forecast_accuracy):
- `mase` - Mean Absolute Scaled Error (NON)
- `wape` - Weighted Absolute Percentage Error (NON - ratio)
- `bias` - Forecast bias (FULL)
- `rmse` - Root Mean Squared Error (NON)
- `beats_naive` - Beats naive baseline (NON - boolean)
- `coverage` - Prediction interval coverage (NON - ratio)

**Dimensions**:
- `product_id` - Product SKU
- `store_id` - Store ID
- `forecast_date` - Date being forecasted
- `as_of_date` - Forecast creation date
- `horizon_days` - Forecast horizon
- `model_version` - Model version

#### 6. RCA (RCA_RUN)

**Domains**: `rca_slice`, `rca_factor`, `rca_weather`

**Relation**: `v_mart_rca_slice_daily`, `v_mart_rca_factor_daily`, `v_mart_rca_weather_effect`

**Metrics** (rca_slice):
- `contribution` - Contribution to variance (FULL)
- `variance` - Absolute variance (FULL)
- `variance_pct` - Variance as % (NON - ratio)
- `expected` - Expected value (baseline) (FULL)
- `actual` - Actual value (FULL)

**Dimensions**:
- `dimension` - Dimension name (store, product, category, etc.)
- `dimension_value` - Dimension value
- `metric` - Metric analyzed
- `period` - Period analyzed
- `comparison_period` - Comparison period
- `business_date` - Date
- `tier` - Evidence tier (MECHANICAL, STATISTICAL, etc.)
- `confidence` - Confidence score (0.0-1.0)

---

## Metric Registry

### Registry Structure

**File**: `backend/app/services/analytics/registry.py`

**67KB file** defining all metrics across 23 domains.

### Metric Properties

#### 1. Key & Label

```python
key: str        # "net_revenue" (used in API)
label: str      # "Net Revenue" (used in UI)
```

#### 2. SQL Expression

```python
expression: str  # SQL aggregate expression
```

**Examples**:

```python
# Simple aggregation
"sum(net_revenue)"

# Ratio computation
"sum(net_revenue) / nullif(sum(orders), 0)"

# Complex calculation
"(sum(net_revenue) - sum(cogs)) / nullif(sum(net_revenue), 0)"
```

**Security**: Expressions are **predefined and validated**. Users never supply SQL.

#### 3. Additivity

```python
class Additivity(StrEnum):
    FULL = "full"   # Sums correctly across every dimension
    SEMI = "semi"   # Sums across dimensions but NOT time
    NON = "non"     # Never summed (ratios, distinct counts)
```

**Why It Matters**:

| Metric | Additivity | Reason |
|--------|------------|--------|
| `net_revenue` | FULL | Revenue from Store A + Store B = Total Revenue ✅ |
| `stock_qty` | SEMI | Stock on Day 1 + Day 2 ≠ Total Stock ❌ (point-in-time) |
| `aov` | NON | AVG(AOV) ≠ Correct AOV ❌ (must recompute from revenue/orders) |

#### 4. Ratio Handling

```python
ratio_of: tuple[str, str] | None  # ("numerator", "denominator")
```

**Ensures correct computation**:

```python
# Metric definition
"aov": Metric(
    expression="sum(net_revenue) / nullif(sum(orders), 0)",
    ratio_of=("net_revenue", "orders"),
)
```

**Query behavior**:

```python
# User asks for AOV by category
SELECT
  category,
  SUM(net_revenue) / NULLIF(SUM(orders), 0) AS aov  -- ✅ Recomputed
FROM v_mart_sales_daily
GROUP BY category

# NOT this (common mistake):
SELECT
  category,
  AVG(aov) AS aov  -- ❌ Averaging pre-computed averages = wrong
FROM v_mart_sales_daily
GROUP BY category
```

---

## Query Patterns

### 1. Summary (No Dimensions)

**Request**:

```json
{
  "domain": "revenue",
  "metrics": ["net_revenue", "orders"],
  "start_date": "2026-07-16",
  "end_date": "2026-08-15"
}
```

**SQL**:

```sql
SELECT
  SUM(net_revenue) AS net_revenue,
  SUM(orders) AS orders
FROM v_mart_sales_daily
WHERE business_date BETWEEN '2026-07-16' AND '2026-08-15'
```

**Cache Key**: `analytics:revenue:summary:20260716-20260815`
**TTL**: 15 seconds

### 2. Breakdown (1-2 Dimensions)

**Request**:

```json
{
  "domain": "revenue",
  "metrics": ["net_revenue"],
  "dimensions": ["category", "region"],
  "start_date": "2026-08-01",
  "end_date": "2026-08-15"
}
```

**SQL**:

```sql
SELECT
  category,
  region,
  SUM(net_revenue) AS net_revenue
FROM v_mart_sales_daily
WHERE business_date BETWEEN '2026-08-01' AND '2026-08-15'
GROUP BY category, region
ORDER BY net_revenue DESC
LIMIT 100
```

**Cache Key**: `analytics:revenue:breakdown:category,region:20260801-20260815`
**TTL**: 5 minutes

### 3. Trend (Date Dimension)

**Request**:

```json
{
  "domain": "revenue",
  "metrics": ["net_revenue"],
  "dimensions": ["business_date"],
  "start_date": "2026-08-01",
  "end_date": "2026-08-15"
}
```

**SQL**:

```sql
SELECT
  business_date,
  SUM(net_revenue) AS net_revenue
FROM v_mart_sales_daily
WHERE business_date BETWEEN '2026-08-01' AND '2026-08-15'
GROUP BY business_date
ORDER BY business_date ASC
LIMIT 100
```

**Cache Key**: `analytics:revenue:trend:20260801-20260815`
**TTL**: 1 minute

### 4. Filtered Queries

**Request**:

```json
{
  "domain": "revenue",
  "metrics": ["net_revenue"],
  "dimensions": ["category"],
  "filters": {
    "region": "Northeast",
    "channel": "online"
  },
  "start_date": "2026-08-01",
  "end_date": "2026-08-15"
}
```

**SQL**:

```sql
SELECT
  category,
  SUM(net_revenue) AS net_revenue
FROM v_mart_sales_daily
WHERE business_date BETWEEN '2026-08-01' AND '2026-08-15'
  AND region = 'Northeast'
  AND channel = 'online'
GROUP BY category
ORDER BY net_revenue DESC
LIMIT 100
```

**Cache Key**: `analytics:revenue:breakdown:category:region=Northeast,channel=online:20260801-20260815`

---

## Caching Strategy

### Redis Cache Layer

**File**: `backend/app/infrastructure/semantic/repository.py`

```python
# Cache key format
f"analytics:{domain}:{pattern}:{dimensions}:{filters_hash}:{period}"

# Examples
"analytics:revenue:summary:20260801-20260815"
"analytics:revenue:breakdown:category:20260801-20260815"
"analytics:store:trend:20260801-20260815"
```

### TTL by Query Pattern

| Pattern | TTL | Reason |
|---------|-----|--------|
| **Summary** | 15 seconds | Frequently requested, short cache |
| **Breakdown** | 5 minutes | More specific, longer cache |
| **Trend** | 1 minute | Time-series, moderate cache |

### Cache Invalidation

**Strategy**: Time-based expiry only (no manual invalidation)

**Rationale**:
- Data warehouse updates are **batch**, not real-time
- dbt runs once daily (nightly ETL)
- Short TTLs provide "good enough" freshness
- Manual invalidation adds complexity with little benefit

### Cache Performance

**Hit Rate**: ~85% (typical production workload)

**Metrics** (Prometheus):

```python
cache_requests_total{domain="revenue", status="hit"} 8543
cache_requests_total{domain="revenue", status="miss"} 1457

# Hit rate = 8543 / (8543 + 1457) = 85.4%
```

---

## Security & Authorization

### Permission Model

**File**: `backend/app/services/analytics/service.py`

Every domain requires specific permission:

```python
DOMAIN_PERMISSIONS = {
    "revenue": Permission.ANALYTICS_REVENUE_READ,
    "store": Permission.ANALYTICS_STORE_READ,
    "customer": Permission.ANALYTICS_CUSTOMER_READ,
    "inventory": Permission.ANALYTICS_INVENTORY_READ,
    "marketing": Permission.ANALYTICS_MARKETING_READ,
    "profitability": Permission.ANALYTICS_PROFITABILITY_READ,
    "forecast": Permission.FORECASTS_READ,
    "rca_slice": Permission.RCA_RUN,
    # ... 23 total
}
```

### RBAC Matrix

| Role | Revenue | Store | Customer | Inventory | Marketing | Profitability | Forecasts | RCA |
|------|---------|-------|----------|-----------|-----------|---------------|-----------|-----|
| **CEO** | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| **CFO** | ✅ | ✅ | ❌ | ✅ | ❌ | ✅ | ✅ | ✅ |
| **CMO** | ✅ | ❌ | ✅ | ❌ | ✅ | ❌ | ❌ | ✅ |
| **Store Manager** | ✅ | ✅ | ❌ | ✅ | ❌ | ❌ | ❌ | ❌ |
| **Inventory Planner** | ❌ | ❌ | ❌ | ✅ | ❌ | ❌ | ✅ | ❌ |
| **Analyst** | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |

**Enforcement**: Checked at service layer before query execution.

### Row-Level Security

**Not implemented at database level**. Tenant isolation enforced by:

1. **Warehouse files** - Each tenant has separate DuckDB file
2. **Application layer** - AnalyticsRepository filters by `tenant_id`

```python
async def run(self, request: AnalyticsRequest, tenant_id: str) -> QueryResult:
    # Application enforces tenant isolation
    warehouse_path = f"/data/warehouse/{tenant_id}/retailmind.duckdb"
    conn = duckdb.connect(warehouse_path, read_only=True)
    # ...
```

---

## API Usage

### REST Endpoint

**Route**: `POST /api/v1/analytics/{domain}/{verb}`

**Verbs**: `summary`, `breakdown`, `trend`

### Example Requests

#### 1. Revenue Summary (Last 30 Days)

```bash
curl -X POST https://api.retailmind.example.com/api/v1/analytics/revenue/summary \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "metrics": ["net_revenue", "gross_revenue", "aov"],
    "start_date": "2026-07-16",
    "end_date": "2026-08-15"
  }'
```

**Response**:

```json
{
  "domain": "revenue",
  "metrics": ["net_revenue", "gross_revenue", "aov"],
  "dimensions": [],
  "rows": [
    {
      "net_revenue": 1250000.00,
      "gross_revenue": 1350000.00,
      "aov": 125.50
    }
  ],
  "row_count": 1,
  "cache": "miss",
  "elapsed_ms": 45.3
}
```

#### 2. Revenue by Category

```bash
curl -X POST https://api.retailmind.example.com/api/v1/analytics/revenue/breakdown \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "metrics": ["net_revenue"],
    "dimensions": ["category"],
    "start_date": "2026-08-01",
    "end_date": "2026-08-15",
    "sort_by": "net_revenue",
    "descending": true,
    "limit": 10
  }'
```

**Response**:

```json
{
  "domain": "revenue",
  "metrics": ["net_revenue"],
  "dimensions": ["category"],
  "rows": [
    {"category": "Electronics", "net_revenue": 450000.00},
    {"category": "Apparel", "net_revenue": 380000.00},
    {"category": "Home & Garden", "net_revenue": 250000.00},
    ...
  ],
  "row_count": 10,
  "cache": "hit",
  "elapsed_ms": 2.1
}
```

#### 3. Daily Revenue Trend

```bash
curl -X POST https://api.retailmind.example.com/api/v1/analytics/revenue/trend \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "metrics": ["net_revenue", "orders"],
    "dimensions": ["business_date"],
    "start_date": "2026-08-01",
    "end_date": "2026-08-15"
  }'
```

**Response**:

```json
{
  "domain": "revenue",
  "metrics": ["net_revenue", "orders"],
  "dimensions": ["business_date"],
  "rows": [
    {"business_date": "2026-08-01", "net_revenue": 85000.00, "orders": 678},
    {"business_date": "2026-08-02", "net_revenue": 92000.00, "orders": 734},
    ...
  ],
  "row_count": 15,
  "cache": "miss",
  "elapsed_ms": 32.7
}
```

---

## Performance Optimization

### Query Performance

**Typical Latency** (p95):
- Summary queries: <100ms
- Breakdown queries (1-2 dimensions): 100-500ms
- Trend queries (15-30 days): 200-800ms

**With Cache**:
- Summary: <10ms
- Breakdown: <15ms
- Trend: <20ms

### DuckDB Optimization

**Columnar Storage**: Parquet files optimized for analytical queries

**Query Plan**:

```sql
EXPLAIN SELECT category, SUM(net_revenue)
FROM v_mart_sales_daily
WHERE business_date BETWEEN '2026-08-01' AND '2026-08-15'
GROUP BY category;

-- Output:
-- ┌─────────────────────────────────────┐
-- │         QUERY PLAN                  │
-- ├─────────────────────────────────────┤
-- │ PROJECTION [category, sum(net_revenue)] │
-- │   AGGREGATE [category] SUM(net_revenue) │
-- │     FILTER [business_date >= 2026-08-01 AND business_date <= 2026-08-15] │
-- │       PARQUET_SCAN [v_mart_sales_daily] │
-- │         Filters: business_date>=2026-08-01 AND business_date<=2026-08-15 │
-- │         Columns: [category, net_revenue, business_date] │
-- └─────────────────────────────────────┘
```

**Optimizations**:
- **Predicate pushdown** - Date filters applied during Parquet scan
- **Projection pushdown** - Only necessary columns read
- **Columnar processing** - SIMD vectorization for aggregates

### Scaling Considerations

**Current Limits**:
- Single DuckDB instance per tenant
- File-based, no distributed queries
- Suitable for <100GB per tenant

**Future Scaling** (if needed):
- MotherDuck (DuckDB cloud, distributed)
- Clickhouse (columnar OLAP database)
- BigQuery / Snowflake (cloud data warehouse)

---

## Appendix

### File Reference

| File | Purpose |
|------|---------|
| `backend/app/services/analytics/registry.py` | Metric registry (23 domains, ~300 metrics) |
| `backend/app/services/analytics/service.py` | Analytics service (authorization, query orchestration) |
| `backend/app/infrastructure/semantic/repository.py` | Query builder, cache, DuckDB execution |
| `backend/app/infrastructure/semantic/client.py` | DuckDB connection pool |
| `backend/app/api/v1/analytics.py` | REST API endpoints |

### Metric Count by Domain

```bash
# Count metrics per domain
grep -E "\".*\": Metric\(" backend/app/services/analytics/registry.py | wc -l
# Output: ~300 metrics across 23 domains
```

### Domain-Permission Mapping

```python
# Complete mapping
DOMAIN_PERMISSIONS = {
    "revenue": Permission.ANALYTICS_REVENUE_READ,
    "store": Permission.ANALYTICS_STORE_READ,
    "customer": Permission.ANALYTICS_CUSTOMER_READ,
    "rfm_grid": Permission.ANALYTICS_CUSTOMER_READ,
    "cohorts": Permission.ANALYTICS_CUSTOMER_READ,
    "lifecycle": Permission.ANALYTICS_CUSTOMER_READ,
    "churn": Permission.ANALYTICS_CUSTOMER_READ,
    "vip": Permission.ANALYTICS_CUSTOMER_READ,
    "inventory": Permission.ANALYTICS_INVENTORY_READ,
    "marketing": Permission.ANALYTICS_MARKETING_READ,
    "profitability": Permission.ANALYTICS_PROFITABILITY_READ,
    "product": Permission.ANALYTICS_REVENUE_READ,
    "product_abc": Permission.ANALYTICS_INVENTORY_READ,
    "inventory_health": Permission.ANALYTICS_INVENTORY_READ,
    "reorder": Permission.ANALYTICS_INVENTORY_READ,
    "supplier": Permission.ANALYTICS_INVENTORY_READ,
    "warehouse_health": Permission.ANALYTICS_INVENTORY_READ,
    "forecast": Permission.FORECASTS_READ,
    "forecast_accuracy": Permission.FORECASTS_READ,
    "forecast_explanation": Permission.FORECASTS_READ,
    "rca_slice": Permission.RCA_RUN,
    "rca_factor": Permission.RCA_RUN,
    "rca_weather": Permission.RCA_RUN,
}
```

---

**Maintained by**: RetailMind AI Contributors
**License**: MIT
**Last Reviewed**: 2026-08-15
