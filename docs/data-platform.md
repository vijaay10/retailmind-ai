# Data Platform Guide

RetailMind AI data platform - Bronze/Silver/Gold medallion architecture, ingestion pipeline, dbt transformation, and DuckDB warehouse.

**Last Updated**: 2026-08-15
**Version**: 0.9.0
**Code**: `data_platform/` (8,116 LOC)

---

## Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Bronze Layer](#bronze-layer)
- [Silver Layer](#silver-layer)
- [Gold Layer](#gold-layer)
- [Ingestion Pipeline](#ingestion-pipeline)
- [dbt Transformation](#dbt-transformation)
- [Quality Gates](#quality-gates)
- [Data Generators](#data-generators)
- [CLI Usage](#cli-usage)
- [Warehouse Administration](#warehouse-administration)

---

## Overview

The data platform implements a **medallion architecture** with three layers:

```
CSV Files → Bronze (Parquet) → Silver (Dimensional) → Gold (Metrics)
```

### Key Characteristics

- **67 dbt models** organized by layer and purpose
- **DuckDB warehouse** for in-process analytical queries
- **7 data generators** for synthetic demo data
- **Parquet storage** in Bronze layer (columnar, compressed)
- **Quality gates** powered by Great Expectations
- **Audit ledger** tracking every file ingested
- **No orchestration** (manual via CLI, no Airflow/Dagster in production)

### Design Principles

1. **Windows as Unit of Work** - Backfills use same code path as daily runs
2. **One Transaction Per Load** - Atomic commits preserve consistency
3. **Quarantine Protection** - Bad partitions don't block other sources
4. **Schema Validation** - YAML-defined contracts with drift detection
5. **Idempotent Loads** - Re-running same window is safe

---

## Architecture

### Directory Structure

```
data_platform/
├── ingestion/           # Bronze layer ingestion
│   ├── cli/            # Command-line interface
│   ├── connectors/     # CSV file connector
│   ├── generators/     # Synthetic data generators
│   ├── landing/        # Bronze writer (Parquet)
│   ├── loading/        # Warehouse loader (DuckDB)
│   ├── schemas/        # Source schema definitions (YAML)
│   ├── transform/      # SQL conformance, currency conversion
│   ├── audit/          # Audit ledger
│   ├── core/           # Config, logging, retry
│   └── pipeline.py     # Orchestration logic
│
├── dbt/                # Silver + Gold transformation
│   ├── models/
│   │   ├── staging/    # Bronze → Silver (5 models)
│   │   └── marts/
│   │       ├── core/   # Dimensional models (11 models)
│   │       ├── metrics/# Metric marts (21 models)
│   │       └── semantic/# Exposed views (30 models)
│   ├── macros/         # dbt macros
│   ├── tests/          # dbt tests
│   └── dbt_project.yml
│
├── quality/            # Quality gates (Great Expectations)
│   ├── expectations/   # Expectation suites
│   └── gate.py         # Gate verdict logic
│
├── warehouse_admin/    # Warehouse management
│   ├── compute/        # Query optimization
│   ├── maintenance/    # Vacuuming, stats
│   └── security/       # Access control
│
└── orchestration/      # Orchestration (NOT IN USE)
    ├── dagster/        # Dagster assets (empty)
    └── dags/           # Airflow DAGs (empty)
```

**Note**: `orchestration/` directory exists but is **NOT USED**. Ingestion is manual via CLI.

### Data Flow

```
┌─────────────────────────────────────────────────────────────┐
│  LANDING ZONE                                               │
│  /data/landing/pos_sales/sales_2026-08-15_store_001.csv    │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       │ discover() - Find CSV files
                       │
┌──────────────────────▼──────────────────────────────────────┐
│  INGESTION PIPELINE                                         │
│  discover → conform → validate → land → load → reconcile    │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       │ BronzeWriter
                       │
┌──────────────────────▼──────────────────────────────────────┐
│  BRONZE LAYER (Parquet)                                     │
│  /data/warehouse/bronze/pos_sales/2026-08-15/part_001.pqt  │
│  + audit_ledger entry (file checksum, row count)           │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       │ dbt run
                       │
┌──────────────────────▼──────────────────────────────────────┐
│  SILVER LAYER (Dimensional)                                 │
│  dim_product, dim_store, fct_sales, fct_inventory          │
│  (SCD Type 2 for slowly changing dimensions)               │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       │ dbt run
                       │
┌──────────────────────▼──────────────────────────────────────┐
│  GOLD LAYER (Metrics)                                       │
│  mart_sales_daily, mart_customer_rfm, mart_inventory_daily  │
│  (Pre-aggregated for performance)                          │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       │ dbt run
                       │
┌──────────────────────▼──────────────────────────────────────┐
│  SEMANTIC LAYER (Views)                                     │
│  v_revenue_summary, v_customer_segments, v_inventory_health │
│  (Exposed to Analytics Service via DuckDB connector)       │
└─────────────────────────────────────────────────────────────┘
```

---

## Bronze Layer

### Purpose

Raw, immutable data storage in columnar format.

**Format**: Parquet (snappy compression)
**Schema**: Validated against YAML contracts
**Partitioning**: By business date

### Schema Definitions

**File**: `data_platform/ingestion/schemas/`

Example schema (POS sales):

```yaml
# data_platform/ingestion/schemas/pos/sales.yml
source: pos
table: sales
description: Point-of-sale transaction line items

columns:
  - name: transaction_id
    type: VARCHAR
    nullable: false
    description: Unique transaction identifier

  - name: store_id
    type: VARCHAR
    nullable: false
    description: Store identifier

  - name: product_id
    type: VARCHAR
    nullable: false
    description: Product SKU

  - name: quantity
    type: INTEGER
    nullable: false
    constraints:
      - type: range
        min: 1
        max: 999

  - name: line_total
    type: DECIMAL(15,2)
    nullable: false
    semantic_type: CURRENCY

  - name: transaction_timestamp
    type: TIMESTAMP
    nullable: false
    semantic_type: EVENT_TIME

metadata:
  expected_units: 120  # Number of stores
  completeness_threshold: 0.95  # 95% of stores must report
```

**Semantic Types**:
- `CURRENCY` - Monetary amounts (requires currency_code column)
- `EVENT_TIME` - Timestamps for event ordering
- `DIMENSION` - Foreign key to dimension table
- `MEASURE` - Numeric metric

### Ingestion Pipeline Stages

**File**: `data_platform/ingestion/pipeline.py`

#### 1. Discover

```python
def discover(window: Window) -> list[ExtractionPlan]:
    """Find CSV files matching pattern in landing zone."""
    # Example: /data/landing/pos_sales/sales_2026-08-15_store_*.csv
    # Returns one ExtractionPlan per partition
```

**Partition**: Typically one file per store per day.

#### 2. Conform

```python
def conform(plan: ExtractionPlan) -> duckdb.DuckDBPyRelation:
    """Apply schema validation and type coercion.

    - Parse CSV with DuckDB read_csv
    - Cast columns to schema types
    - Add metadata columns (ingested_at, partition_key)
    - Currency conversion (if multi-currency)
    """
```

**SQL Template**:

```sql
SELECT
  -- Business columns (from CSV)
  transaction_id::VARCHAR AS transaction_id,
  store_id::VARCHAR AS store_id,
  product_id::VARCHAR AS product_id,
  quantity::INTEGER AS quantity,
  line_total::DECIMAL(15,2) AS line_total,
  transaction_timestamp::TIMESTAMP AS transaction_timestamp,

  -- Metadata columns (added by pipeline)
  '{partition}' AS partition_key,
  now() AS ingested_at,
  '{source_file}' AS source_file

FROM read_csv('{file_path}', header=true, delim=',')
```

#### 3. Validate

```python
def validate(data: duckdb.DuckDBPyRelation) -> GateVerdict:
    """Run quality checks via Great Expectations.

    Checks:
    - Column completeness (nulls within threshold)
    - Range constraints (min/max)
    - Duplicate detection
    - Volume anomaly detection
    """
```

**File**: `data_platform/quality/gate.py`

**Verdict**: `pass` | `warn` | `fail`

- `pass` → Load normally
- `warn` → Load with warning logged
- `fail` → Quarantine partition

#### 4. Land

```python
def land(data: duckdb.DuckDBPyRelation, partition: str) -> PartitionManifest:
    """Write to Bronze as Parquet.

    Path: /data/warehouse/bronze/{source}/{table}/{date}/part_{partition}.parquet
    """
```

**Manifest**:

```python
@dataclass
class PartitionManifest:
    partition: str
    rows_read: int          # From CSV
    rows_rejected: int      # Failed validation
    rows_written: int       # To Parquet
    file_size_bytes: int
    checksum: str           # SHA-256 of Parquet file
```

#### 5. Load

```python
def load(partition_path: str, table: str) -> LoadResult:
    """Load Parquet into DuckDB warehouse.

    CREATE TABLE IF NOT EXISTS bronze.{source}_{table} AS
    SELECT * FROM read_parquet('{partition_path}')
    """
```

**Idempotent**: Re-running same partition replaces existing data (upsert by partition_key).

#### 6. Reconcile

```python
def reconcile(manifest: PartitionManifest, load: LoadResult):
    """Verify row counts match.

    - manifest.rows_written == load.rows_inserted
    - If mismatch: raise ReconciliationError
    """
```

#### 7. Record

```python
def record(manifest: PartitionManifest, verdict: GateVerdict):
    """Write to audit ledger.

    INSERT INTO audit_ledger (
      tenant_id, connector_id, partition, rows_read, rows_loaded,
      checksum, verdict, ingested_at
    ) VALUES (...)
    """
```

**File**: `data_platform/ingestion/audit/ledger.py`

**Table**: `audit_ledger` (PostgreSQL application database)

### Quarantine System

When a partition fails quality gates:

1. **Quarantine Directory**: `/data/warehouse/quarantine/{source}/{table}/{date}/`
2. **Write Failed CSV**: Original file preserved for investigation
3. **Log Verdict**: Audit ledger records failed rules
4. **Alert**: Notification sent (if configured)
5. **Continue**: Other partitions proceed normally

**Recovery**: Fix source data, re-run ingestion for that date.

---

## Silver Layer

### Purpose

Conformed, dimensional models for analytical queries.

**Technology**: dbt SQL models
**Schema**: Star schema (facts + dimensions)
**SCD**: Type 2 for slowly changing dimensions (product, store)

### Staging Models (5)

**Path**: `data_platform/dbt/models/staging/`

Transform Bronze → typed relations.

| Model | Source | Purpose |
|-------|--------|---------|
| `stg_pos__sales` | bronze.pos_sales | POS transactions |
| `stg_inventory__positions` | bronze.inventory_positions | Inventory snapshots |
| `stg_purchasing__orders` | bronze.purchase_orders | Purchase orders |
| `stg_fulfilment__deliveries` | bronze.fulfilment_deliveries | Delivery events |
| `stg_weather__observations` | bronze.weather_observations | Weather data |

**Example**:

```sql
-- stg_pos__sales.sql
WITH source AS (
  SELECT * FROM {{ source('bronze', 'pos_sales') }}
),

typed AS (
  SELECT
    transaction_id,
    store_id,
    product_id,
    quantity::INTEGER AS quantity,
    line_total::DECIMAL(15,2) AS line_total,
    transaction_timestamp::TIMESTAMP AS transaction_timestamp,
    partition_key,
    ingested_at
  FROM source
)

SELECT * FROM typed
```

### Core Models (11)

**Path**: `data_platform/dbt/models/marts/core/`

Dimensional and fact tables.

#### Dimensions (6)

| Model | Type | Key Columns | SCD Type |
|-------|------|-------------|----------|
| `dim_product` | Product master | product_id, product_key | Type 2 |
| `dim_store` | Store master | store_id, store_key | Type 2 |
| `dim_supplier` | Supplier master | supplier_id | Type 1 |
| `dim_customer` | Customer master | customer_id | Type 1 |
| `dim_date` | Date dimension | date_day | Static |
| `dim_calendar` | Calendar with holidays | date_day | Static |

**SCD Type 2 Example** (dim_product):

```sql
-- dim_product.sql
{{ config(materialized='table') }}

WITH source AS (
  SELECT DISTINCT
    product_id,
    product_name,
    category,
    subcategory,
    brand,
    unit_cost,
    ingested_at
  FROM {{ ref('stg_pos__sales') }}
),

changes AS (
  SELECT
    product_id,
    product_name,
    category,
    subcategory,
    brand,
    unit_cost,
    ingested_at AS valid_from,
    LEAD(ingested_at) OVER (PARTITION BY product_id ORDER BY ingested_at) AS valid_to,
    CASE
      WHEN LEAD(ingested_at) OVER (PARTITION BY product_id ORDER BY ingested_at) IS NULL
      THEN TRUE
      ELSE FALSE
    END AS is_current
  FROM source
)

SELECT
  {{ dbt_utils.generate_surrogate_key(['product_id', 'valid_from']) }} AS product_key,
  product_id,
  product_name,
  category,
  subcategory,
  brand,
  unit_cost,
  valid_from,
  COALESCE(valid_to, '9999-12-31'::DATE) AS valid_to,
  is_current
FROM changes
```

**Surrogate Key**: Hashed combination of natural key + valid_from timestamp.

#### Facts (5)

| Model | Grain | Key Metrics | Dimensions |
|-------|-------|-------------|------------|
| `fct_sales` | Transaction line item | revenue, quantity | product, store, date, customer |
| `fct_inventory_daily` | Product-store-day | stock_qty, stock_value | product, store, date |
| `fct_purchase_orders` | PO line item | order_qty, order_value | product, supplier, date |
| `fct_deliveries` | Delivery event | delivered_qty | product, store, date |
| `fct_forecast` | Product-store-day | forecast_qty, confidence | product, store, date |

**Example**:

```sql
-- fct_sales.sql
{{ config(materialized='incremental', unique_key='transaction_line_key') }}

SELECT
  {{ dbt_utils.generate_surrogate_key(['transaction_id', 'line_number']) }} AS transaction_line_key,
  transaction_id,
  line_number,

  -- Foreign keys to dimensions
  p.product_key,
  s.store_key,
  c.customer_key,
  d.date_key,

  -- Degenerate dimensions
  payment_method,
  channel,

  -- Measures
  quantity,
  unit_price,
  line_total AS revenue,
  unit_cost,
  (line_total - (unit_cost * quantity)) AS profit,

  -- Metadata
  transaction_timestamp,
  ingested_at

FROM {{ ref('stg_pos__sales') }} sales
LEFT JOIN {{ ref('dim_product') }} p
  ON sales.product_id = p.product_id
  AND sales.transaction_timestamp >= p.valid_from
  AND sales.transaction_timestamp < p.valid_to
LEFT JOIN {{ ref('dim_store') }} s
  ON sales.store_id = s.store_id
LEFT JOIN {{ ref('dim_customer') }} c
  ON sales.customer_id = c.customer_id
LEFT JOIN {{ ref('dim_date') }} d
  ON DATE(sales.transaction_timestamp) = d.date_day

{% if is_incremental() %}
WHERE sales.ingested_at > (SELECT MAX(ingested_at) FROM {{ this }})
{% endif %}
```

**Incremental Model**: Only process new rows since last run.

---

## Gold Layer

### Purpose

Pre-aggregated metric marts for performance.

**Path**: `data_platform/dbt/models/marts/metrics/`

**Count**: 21 models

### Metric Marts

| Mart | Grain | Metrics | Purpose |
|------|-------|---------|---------|
| `mart_sales_daily` | Date-store-category | revenue, transactions, units | Daily sales rollup |
| `mart_customer_rfm` | Customer | recency, frequency, monetary | RFM segmentation |
| `mart_customer_cohorts` | Cohort-date | retention_rate, ltv | Cohort analysis |
| `mart_customer_lifecycle` | Customer-date | days_since_first, total_orders | Lifecycle tracking |
| `mart_customer_churn_risk` | Customer | churn_score, last_order_days | Churn prediction |
| `mart_inventory_daily` | Product-store-date | stock_qty, days_on_hand, turnover | Inventory metrics |
| `mart_inventory_health` | Product-store | stock_status, weeks_of_supply | Inventory health |
| `mart_reorder_suggestions` | Product-store | suggested_qty, order_by_date | Reorder planning |
| `mart_forecast_accuracy` | Product-date | mase, wape, beats_naive | Forecast quality |
| `mart_kpi_daily` | Date | 20+ KPIs | Executive dashboard |
| `mart_store_daily` | Store-date | revenue, foot_traffic, conversion | Store performance |
| `mart_product_abc` | Product | abc_class, revenue_contribution | ABC analysis |
| `mart_supplier_performance` | Supplier-month | on_time_pct, quality_score | Supplier scorecard |
| `mart_promo_daily` | Promotion-date | promo_revenue, lift | Promotion effectiveness |
| `mart_shipping_daily` | Date | deliveries, on_time_pct | Shipping metrics |
| `mart_warehouse_health` | Warehouse-date | utilization_pct, accuracy | Warehouse KPIs |
| `mart_rca_slice_daily` | Date-dimension-value | contribution, variance | RCA pre-compute |
| `mart_rca_factor_daily` | Date-factor | factor_value, correlation | RCA factors |
| `mart_rca_weather_effect` | Date-store | temp, precip, sales_impact | Weather correlation |
| `mart_customer_rfm_grid` | RFM_segment | customer_count, avg_ltv | RFM grid |
| `mart_customer_vip` | Customer | vip_tier, lifetime_revenue | VIP segmentation |

**Example**:

```sql
-- mart_sales_daily.sql
{{ config(materialized='table') }}

SELECT
  d.date_day,
  s.store_id,
  s.store_name,
  s.region,
  p.category,
  p.subcategory,

  -- Aggregated metrics
  SUM(f.revenue) AS revenue,
  SUM(f.profit) AS profit,
  SUM(f.quantity) AS units_sold,
  COUNT(DISTINCT f.transaction_id) AS transactions,
  COUNT(DISTINCT f.customer_key) AS unique_customers,
  AVG(f.revenue) AS avg_transaction_value,
  SUM(f.revenue) / NULLIF(SUM(f.quantity), 0) AS avg_unit_price

FROM {{ ref('fct_sales') }} f
JOIN {{ ref('dim_date') }} d ON f.date_key = d.date_key
JOIN {{ ref('dim_store') }} s ON f.store_key = s.store_key
JOIN {{ ref('dim_product') }} p ON f.product_key = p.product_key

GROUP BY 1, 2, 3, 4, 5, 6
```

### Semantic Views (30)

**Path**: `data_platform/dbt/models/marts/semantic/`

Views exposed to Analytics Service for querying.

**Naming Convention**: `v_{domain}_{purpose}`

| View | Base Mart | Exposed Columns | API Endpoint |
|------|-----------|----------------|--------------|
| `v_revenue_summary` | mart_sales_daily | date, revenue, profit | /api/v1/analytics/revenue/summary |
| `v_revenue_by_store` | mart_sales_daily | store_id, store_name, revenue | /api/v1/analytics/revenue/breakdown |
| `v_revenue_trend` | mart_sales_daily | date, revenue | /api/v1/analytics/revenue/trend |
| `v_customer_segments` | mart_customer_rfm | segment, customer_count, avg_ltv | /api/v1/analytics/customer/segments |
| `v_inventory_positions` | mart_inventory_daily | product, store, stock_qty | /api/v1/analytics/inventory/positions |
| ... | ... | ... | ... |

**Example**:

```sql
-- v_revenue_summary.sql
{{ config(materialized='view') }}

SELECT
  date_day AS date,
  SUM(revenue) AS total_revenue,
  SUM(profit) AS total_profit,
  SUM(transactions) AS total_transactions,
  AVG(avg_transaction_value) AS avg_order_value,
  SUM(units_sold) AS total_units

FROM {{ ref('mart_sales_daily') }}

GROUP BY date_day
ORDER BY date_day DESC
```

**Access Pattern**: Analytics Service queries these views via DuckDB connector.

---

## Ingestion Pipeline

### CLI Commands

**File**: `data_platform/ingestion/cli/main.py`

#### Generate Synthetic Data

```bash
# Generate 7 days of POS data for 120 stores
uv run -m ingestion.cli generate \
  --day 2026-08-15 \
  --days 7 \
  --stores 120 \
  --seed 42
```

**Output**: CSV files in `/data/landing/pos_sales/`

#### Run Ingestion

```bash
# Ingest POS sales for yesterday
uv run -m ingestion.cli run \
  --source pos \
  --table sales \
  --day yesterday

# Ingest specific date
uv run -m ingestion.cli run \
  --source pos \
  --table sales \
  --day 2026-08-15
```

#### Backfill Historical Data

```bash
# Backfill last 30 days
uv run -m ingestion.cli backfill \
  --source pos \
  --table sales \
  --start 2026-07-16 \
  --end 2026-08-15
```

**Note**: Backfill uses same code path as daily run (same quality gates).

#### Inspect Warehouse

```bash
# Show table stats
uv run -m ingestion.cli inspect \
  --source pos \
  --table sales

# Output:
# pos.sales
#   Partitions: 30
#   Rows: 1,234,567
#   Size: 45 MB (Parquet compressed)
#   Date range: 2026-07-16 to 2026-08-15
```

### Configuration

**File**: `data_platform/ingestion/core/config.py`

```python
class EtlSettings(BaseSettings):
    # Paths
    warehouse_path: Path = Path("/data/warehouse/retailmind.duckdb")
    landing_path: Path = Path("/data/landing")
    bronze_path: Path = Path("/data/warehouse/bronze")
    quarantine_path: Path = Path("/data/warehouse/quarantine")

    # Quality gates
    reject_rate_threshold: float = 0.05  # Max 5% row rejection
    volume_band_tolerance: float = 0.30  # ±30% volume variance

    # Retry behavior
    max_retries: int = 3
    retry_base_seconds: float = 2.0  # Exponential backoff
```

**Environment Variables**:

```bash
RM_WAREHOUSE_PATH=/data/warehouse/retailmind.duckdb
RM_LANDING_PATH=/data/landing
RM_REJECT_RATE_THRESHOLD=0.05
```

---

## dbt Transformation

### Running dbt

```bash
# Install dbt dependencies
cd data_platform/dbt
uv run dbt deps

# Run all models
uv run dbt run

# Run specific model
uv run dbt run --models mart_sales_daily

# Run tests
uv run dbt test

# Generate documentation
uv run dbt docs generate
uv run dbt docs serve  # View at http://localhost:8080
```

### dbt Project Configuration

**File**: `data_platform/dbt/dbt_project.yml`

```yaml
name: retailmind_warehouse
version: 1.0.0
config-version: 2

profile: retailmind

model-paths: ["models"]
test-paths: ["tests"]
macro-paths: ["macros"]
seed-paths: ["seeds"]

models:
  retailmind_warehouse:
    staging:
      +materialized: view
      +schema: staging

    marts:
      core:
        +materialized: table
        +schema: core

      metrics:
        +materialized: table
        +schema: metrics

      semantic:
        +materialized: view
        +schema: semantic
```

### dbt Tests

**File**: `data_platform/dbt/tests/`

```yaml
# tests/schema.yml
version: 2

models:
  - name: fct_sales
    description: Sales transaction fact table
    columns:
      - name: transaction_line_key
        description: Surrogate key
        tests:
          - unique
          - not_null

      - name: product_key
        description: FK to dim_product
        tests:
          - relationships:
              to: ref('dim_product')
              field: product_key

      - name: revenue
        description: Line total revenue
        tests:
          - not_null
          - dbt_utils.accepted_range:
              min_value: 0
              max_value: 1000000
```

**Run Tests**:

```bash
uv run dbt test  # Run all tests
uv run dbt test --models fct_sales  # Test specific model
```

---

## Quality Gates

### Great Expectations Integration

**File**: `data_platform/quality/gate.py`

```python
class QualityGate:
    """Runs Great Expectations validation suites."""

    def validate(self, data: duckdb.DuckDBPyRelation) -> GateVerdict:
        """Run validation suite.

        Returns:
          GateVerdict(status='pass|warn|fail', failed_rules=[...])
        """
        # 1. Column completeness checks
        # 2. Range validation
        # 3. Duplicate detection
        # 4. Volume anomaly detection
        # 5. Schema drift detection
```

### Expectation Suites

**File**: `data_platform/quality/expectations/pos_sales.json`

```json
{
  "expectation_suite_name": "pos_sales",
  "expectations": [
    {
      "expectation_type": "expect_column_values_to_not_be_null",
      "kwargs": {
        "column": "transaction_id",
        "mostly": 1.0
      }
    },
    {
      "expectation_type": "expect_column_values_to_be_between",
      "kwargs": {
        "column": "quantity",
        "min_value": 1,
        "max_value": 999,
        "mostly": 0.95
      }
    },
    {
      "expectation_type": "expect_table_row_count_to_be_between",
      "kwargs": {
        "min_value": 10000,
        "max_value": 100000
      }
    }
  ]
}
```

---

## Data Generators

### Purpose

Generate synthetic, realistic demo data for development and testing.

**Path**: `data_platform/ingestion/generators/`

### Available Generators

| Generator | File | Output | Characteristics |
|-----------|------|--------|----------------|
| **POS Sales** | `pos_files.py` | CSV (one per store) | Realistic transaction patterns, seasonal trends |
| **Inventory** | `inventory_files.py` | CSV (daily snapshots) | Stock levels, turnover |
| **Purchase Orders** | `purchase_orders.py` | CSV (supplier orders) | Lead times, order patterns |
| **Fulfilment** | `fulfilment.py` | CSV (delivery events) | On-time delivery, delays |
| **Weather** | `weather.py` | CSV (daily observations) | Temperature, precipitation |
| **Customers** | `customers.py` | CSV (customer master) | Demographics, segments |
| **Shocks** | `shocks.py` | Anomaly injection | Concert, weather event, supply disruption |

### Generation Example

```python
# data_platform/ingestion/generators/pos_files.py
def generate_day(day: date, stores: int, seed: int) -> list[Path]:
    """Generate one day of POS sales for N stores.

    Returns:
      List of CSV file paths
    """
    np.random.seed(seed + day.toordinal())

    files = []
    for store_id in range(1, stores + 1):
        transactions = []

        # Base volume with day-of-week pattern
        base_volume = 100  # transactions per store per day
        dow_factor = [0.8, 0.9, 1.0, 1.1, 1.3, 1.5, 1.2][day.weekday()]

        # Seasonal trend
        month_factor = seasonal_trend(day.month)

        num_transactions = int(base_volume * dow_factor * month_factor)

        for txn in range(num_transactions):
            transaction_id = f"TXN-{day:%Y%m%d}-{store_id:03d}-{txn:05d}"
            num_items = np.random.choice([1, 2, 3, 4, 5], p=[0.4, 0.3, 0.15, 0.10, 0.05])

            for item in range(num_items):
                transactions.append({
                    "transaction_id": transaction_id,
                    "store_id": f"STORE-{store_id:03d}",
                    "product_id": select_product(),  # Weighted by popularity
                    "quantity": np.random.randint(1, 5),
                    "line_total": round(np.random.uniform(5.0, 200.0), 2),
                    "transaction_timestamp": generate_timestamp(day),
                })

        # Write CSV
        file_path = Path(f"/data/landing/pos_sales/sales_{day:%Y-%m-%d}_store_{store_id:03d}.csv")
        write_csv(transactions, file_path)
        files.append(file_path)

    return files
```

### Deterministic Generation

**Seed-based**: Same seed produces identical data (useful for testing).

```bash
# Generate same data every time
uv run -m ingestion.cli generate --seed 42

# Different seed = different data
uv run -m ingestion.cli generate --seed 43
```

### Shock Injection

**File**: `data_platform/ingestion/generators/shocks.py`

Inject realistic anomalies:

```python
SHOCKS = [
    {
        "date": "2026-08-10",
        "type": "concert",
        "affected_stores": ["STORE-042"],
        "revenue_lift": 3.5,  # 3.5x normal
        "duration_days": 1,
    },
    {
        "date": "2026-08-12",
        "type": "weather_event",
        "affected_region": "Northeast",
        "revenue_impact": 0.4,  # 60% drop
        "duration_days": 2,
    },
]
```

**Purpose**: Test RCA engine's ability to detect and explain anomalies.

---

## CLI Usage

### Common Workflows

#### Daily Production Run

```bash
#!/bin/bash
# Nightly ETL job (cron: 0 2 * * *)

# 1. Ingest all sources
for source in pos inventory purchasing fulfilment weather; do
  uv run -m ingestion.cli run \
    --source $source \
    --table sales \
    --day yesterday
done

# 2. Transform with dbt
cd /app/data_platform/dbt
uv run dbt run

# 3. Run tests
uv run dbt test

# 4. Update stats
uv run python -m warehouse_admin.maintenance.update_stats
```

#### Backfill After Source Issue

```bash
# Data source was down for 5 days, now recovered

# 1. Backfill missing days
uv run -m ingestion.cli backfill \
  --source pos \
  --table sales \
  --start 2026-08-10 \
  --end 2026-08-14

# 2. Re-run dbt for affected dates
cd /app/data_platform/dbt
uv run dbt run --vars '{"start_date": "2026-08-10", "end_date": "2026-08-14"}'
```

#### Replay After Bug Fix

```bash
# Bug in dbt model fixed, need to recompute

# 1. Drop affected mart
uv run duckdb /data/warehouse/retailmind.duckdb \
  "DROP TABLE IF EXISTS metrics.mart_customer_rfm"

# 2. Re-run model
cd /app/data_platform/dbt
uv run dbt run --models mart_customer_rfm+

# 3. Verify
uv run dbt test --models mart_customer_rfm
```

---

## Warehouse Administration

### Compute Optimization

**File**: `data_platform/warehouse_admin/compute/optimize.py`

```python
# Analyze query performance
def analyze_slow_queries(min_duration_ms: int = 1000):
    """Identify queries taking >1 second."""
    # Parse DuckDB query logs
    # Report slow queries with EXPLAIN plans

# Recommend indexes
def recommend_indexes():
    """Analyze query patterns and suggest indexes."""
    # Not applicable to DuckDB (columnar storage)
```

### Maintenance

**File**: `data_platform/warehouse_admin/maintenance/vacuum.py`

```python
# Vacuum deleted rows
def vacuum_warehouse():
    """Reclaim space from deleted rows."""
    conn.execute("VACUUM")

# Update statistics
def update_statistics():
    """Refresh table statistics for query optimizer."""
    conn.execute("ANALYZE")
```

**Schedule**: Weekly vacuum, daily statistics update.

### Security

**File**: `data_platform/warehouse_admin/security/access.py`

```python
# Row-level security (not implemented in DuckDB)
# Access control handled at application layer (Analytics Service)
```

**Note**: DuckDB is file-based, no native RBAC. Security enforced by:
1. File permissions (warehouse file readable only by app)
2. Application layer (Analytics Service filters by tenant_id)

---

## Appendix

### File Reference

| File | Purpose |
|------|---------|
| `data_platform/ingestion/pipeline.py` | Ingestion orchestrator |
| `data_platform/ingestion/cli/main.py` | CLI commands (run, backfill, generate) |
| `data_platform/ingestion/connectors/csv_files.py` | CSV file connector |
| `data_platform/ingestion/generators/*.py` | 7 synthetic data generators |
| `data_platform/ingestion/landing/writer.py` | Bronze Parquet writer |
| `data_platform/ingestion/loading/warehouse.py` | DuckDB loader |
| `data_platform/ingestion/schemas/*/*.yml` | Source schema definitions |
| `data_platform/quality/gate.py` | Quality gate logic |
| `data_platform/dbt/models/staging/*.sql` | 5 staging models |
| `data_platform/dbt/models/marts/core/*.sql` | 11 core models |
| `data_platform/dbt/models/marts/metrics/*.sql` | 21 metric marts |
| `data_platform/dbt/models/marts/semantic/*.sql` | 30 semantic views |

### Model Counts

```bash
# Verify model count
find data_platform/dbt/models -name "*.sql" -type f | wc -l
# Output: 67
```

### Storage Estimates

| Layer | Typical Size | Compression | Notes |
|-------|-------------|-------------|-------|
| **Bronze** (30 days) | 2-5 GB | Parquet snappy (~10x vs CSV) | Raw data, all partitions |
| **Silver** (1 year) | 10-20 GB | Columnar | Dimensional models |
| **Gold** (1 year) | 1-5 GB | Pre-aggregated | Metric marts |
| **Total** | 15-30 GB | - | Per tenant |

---

**Maintained by**: RetailMind AI Contributors
**License**: MIT
**Last Reviewed**: 2026-08-15
