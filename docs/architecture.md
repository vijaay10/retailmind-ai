# System Architecture

RetailMind AI System Architecture Documentation

**Last Updated**: 2026-08-15
**Version**: 0.9.0

---

## Table of Contents

- [Overview](#overview)
- [System Diagram](#system-diagram)
- [Architecture Principles](#architecture-principles)
- [Component Architecture](#component-architecture)
- [Data Flow](#data-flow)
- [Service Boundaries](#service-boundaries)
- [Semantic Layer](#semantic-layer)
- [AI Architecture](#ai-architecture)
- [Decision Loop](#decision-loop)
- [Technology Stack](#technology-stack)
- [Deployment Architecture](#deployment-architecture)
- [Security Architecture](#security-architecture)

---

## Overview

RetailMind AI is a production-grade retail intelligence platform built around three core engines:

1. **Analytics Engine** - Semantic layer over warehouse, 23 business domains
2. **Investigation Engine** - Root cause analysis with 9 dimensional investigators
3. **Decision Engine** - Recommendation generation, tracking, and outcome measurement

The platform follows **Clean Architecture** principles with strict layer separation and **Medallion Architecture** for data transformation.

### Key Characteristics

- **51,309 lines of Python code** across 4 workspace members
- **1,099 passing tests** (794 unit + 305 integration)
- **67 dbt models** for data transformation
- **Zero external ML dependencies** (hand-written ridge regression)
- **Evidence-based AI** (LLM explains verified facts, never generates business numbers)

---

## System Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                    STREAMLIT UI (9,413 LOC)                     │
│  ┌──────────────┬──────────────┬──────────────┬──────────────┐  │
│  │  Command     │ AI           │  Decision    │   Sales      │  │
│  │  Center      │ Investigation│  Center      │   Intel      │  │
│  └──────────────┴──────────────┴──────────────┴──────────────┘  │
│        │                                                         │
│        │ HTTP/REST (FastAPI endpoints)                          │
│        ▼                                                         │
└─────────────────────────────────────────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────────┐
│                    FASTAPI BACKEND (29,932 LOC)                 │
│                                                                  │
│  ┌───────────────────── API LAYER ───────────────────────┐      │
│  │  13 endpoint modules (auth, analytics, rca, etc.)     │      │
│  │  Authentication, rate limiting, idempotency           │      │
│  └───────────────────────┬──────────────────────────────┘      │
│                          │                                      │
│  ┌───────────────────── SERVICE LAYER ──────────────────┐      │
│  │  ┌─────────────┬─────────────┬─────────────┐         │      │
│  │  │  Analytics  │     RCA     │ Forecasting │         │      │
│  │  │   Service   │   Service   │   Service   │         │      │
│  │  └─────────────┴─────────────┴─────────────┘         │      │
│  │  ┌─────────────┬─────────────┬─────────────┐         │      │
│  │  │Recommend    │   Analyst   │    NLQ      │         │      │
│  │  │  Service    │   Service   │   Service   │         │      │
│  │  └─────────────┴─────────────┴─────────────┘         │      │
│  └───────────────────────┬──────────────────────────────┘      │
│                          │                                      │
│  ┌───────────────────── DOMAIN LAYER ──────────────────┐       │
│  │  Business logic, entities, value objects             │       │
│  │  Metric definitions, evidence tiers, forecast models │       │
│  └──────────────────────────────────────────────────────┘       │
│                          │                                      │
│  ┌────────────────── INFRASTRUCTURE LAYER ─────────────┐       │
│  │  ┌──────────┬──────────┬──────────┬──────────┐      │       │
│  │  │PostgreSQL│  Redis   │  LLM     │ Warehouse│      │       │
│  │  │Repository│  Cache   │ Gateway  │ Connector│      │       │
│  │  └──────────┴──────────┴──────────┴──────────┘      │       │
│  └──────────────────────┬──────────────────────────────┘       │
└─────────────────────────┼───────────────────────────────────────┘
                          │
        ┌─────────────────┴─────────────────┐
        │                                   │
┌───────▼────────────────┐      ┌──────────▼──────────────┐
│ DATA PLATFORM          │      │ ML MODULE               │
│ (8,116 LOC)            │      │ (3,848 LOC)             │
│                        │      │                         │
│ ┌────────────────────┐ │      │ ┌────────────────────┐  │
│ │ Ingestion Pipeline │ │      │ │ Ridge Forecaster   │  │
│ │ CSV → Bronze       │ │      │ │ Feature Engineering│  │
│ └────────┬───────────┘ │      │ │ Backtest Framework │  │
│          │             │      │ │ Model Registry     │  │
│ ┌────────▼───────────┐ │      │ └────────────────────┘  │
│ │ Bronze Layer       │ │      │                         │
│ │ (Parquet files)    │ │      └─────────────────────────┘
│ └────────┬───────────┘ │
│          │             │
│ ┌────────▼───────────┐ │
│ │ dbt Transformation │ │
│ │ (67 models)        │ │
│ │ Silver → Gold      │ │
│ └────────┬───────────┘ │
│          │             │
└──────────┼─────────────┘
           │
┌──────────▼──────────────────────────────────────────────┐
│            DUCKDB WAREHOUSE (Analytical Database)       │
│                                                          │
│  ┌─────────────┬─────────────┬─────────────┐            │
│  │   BRONZE    │   SILVER    │    GOLD     │            │
│  │  Raw Files  │ Dimensional │  Metrics    │            │
│  │  (Parquet)  │  Models     │  Marts      │            │
│  └─────────────┴─────────────┴─────────────┘            │
│                                                          │
│  Exposed via semantic layer to Analytics Service        │
└──────────────────────────────────────────────────────────┘
```

---

## Architecture Principles

### 1. Clean Architecture

Four strict layers with dependency inversion:

```
api → services → domain → infrastructure
 ↓       ↓        ↓            ↓
HTTP   Business  Entities   Adapters
Layer   Logic    & Rules    (DB, Cache, LLM)
```

**Enforced by**: `import-linter` in CI (see `.importlinter`)

**Rules**:
- API layer depends on services
- Services depend on domain
- Domain depends on nothing (pure business logic)
- Infrastructure implements domain interfaces (ports & adapters)

**File**: `backend/app/`
- `api/` - FastAPI routers, request validation
- `services/` - Orchestration, business workflows
- `domain/` - Business entities, value objects, interfaces
- `infrastructure/` - PostgreSQL, Redis, LLM, warehouse adapters

### 2. Medallion Architecture

Data transformation pipeline with quality gates:

```
Bronze (Raw) → Silver (Conformed) → Gold (Aggregated)
```

- **Bronze**: Parquet files, schema validation, quarantine on failure
- **Silver**: Dimensional models (SCD Type 2), fact tables, dbt-transformed
- **Gold**: Metric marts, semantic views, optimized for queries

**File**: `data_platform/`

### 3. Evidence-Based AI

**Critical Principle**: Claude explains verified facts, never generates them.

```
Analytical Engine → Evidence Package → LLM → Natural Language
(SOURCE OF TRUTH)   (Structured Facts)        (Explanation Only)
```

All business numbers come from:
- Warehouse queries
- RCA calculations
- Forecast models
- Recommendation generators

The LLM receives only EvidencePackage objects with verified facts and explains them in natural language.

**File**: `backend/app/infrastructure/llm/models.py` (EvidencePackage, EvidenceTier)

### 4. Fail-Safe Design

- **LLM defaults to mock mode** - No external API calls unless explicitly configured
- **Graceful degradation** - Falls back to deterministic templates if LLM fails
- **Idempotent mutations** - Retry-safe via Redis cache (24h TTL)
- **Rate limiting** - Redis sliding window (100 req/min per IP)

---

## Component Architecture

### Backend (FastAPI) - 29,932 LOC

#### API Layer (13 endpoint modules)

**File**: `backend/app/api/v1/`

| Endpoint | Purpose | Key Routes |
|----------|---------|------------|
| `auth.py` | JWT authentication, refresh tokens | `/login`, `/refresh`, `/logout` |
| `analytics.py` | Semantic layer queries | `/analytics/{domain}/{verb}` |
| `rca.py` | Root cause analysis | `/rca/investigate`, `/rca/compare` |
| `analyst.py` | AI analyst conversation | `/analyst/` |
| `recommendations.py` | Decision intelligence | `/recommendations/`, `/recommendations/{id}/decide` |
| `forecasts.py` | Forecast management | `/forecasts/`, `/forecasts/train` |
| `nlq.py` | Natural language queries | `/nlq/` |
| `customers.py` | Customer analytics | `/customers/` |
| `inventory.py` | Inventory analytics | `/inventory/` |
| `dashboards.py` | Dashboard composition | `/dashboard/` |
| `reports.py` | Report generation | `/reports/` |
| `notifications.py` | Alert management | `/notifications/` |
| `admin.py` | System administration | `/admin/users`, `/admin/permissions` |

#### Service Layer (12 services)

**File**: `backend/app/services/`

| Service | Responsibilities | Key Methods |
|---------|------------------|-------------|
| **AnalyticsService** | Semantic layer queries, 23 domains | `summary()`, `breakdown()`, `trend()` |
| **RcaService** | Root cause analysis, 9 investigators | `investigate()`, `compare()` |
| **ForecastService** | Forecast generation, backtesting | `train()`, `backtest()`, `promote()` |
| **RecommendationService** | 7 generators, decision tracking | `generate()`, `decide()`, `measure_outcome()` |
| **BusinessAnalystService** | Conversational interface, 8 capabilities | `ask()`, `_investigate()`, `_explain_kpi()` |
| **NlqService** | Natural language query parsing | `plan()`, `execute()` |
| **ReportService** | Executive briefings, narratives | `compose()`, `schedule()` |
| **CustomerService** | Customer analytics | `segments()`, `cohorts()`, `lifetime_value()` |
| **InventoryService** | Stock analytics | `positions()`, `turnover()`, `allocation()` |
| **DashboardService** | Dashboard composition | `render()`, `configure()` |
| **NotificationService** | Alert routing | `send()`, `subscribe()` |
| **AuthService** | Authentication, RBAC | `login()`, `verify_token()`, `check_permission()` |

#### Domain Layer

**File**: `backend/app/domain/`

Pure business logic with no dependencies:

- **Entities**: `Metric`, `Finding`, `Recommendation`, `Forecast`
- **Value Objects**: `Period`, `Dimension`, `EvidenceTier`, `ConfidenceScore`
- **Domain Services**: Evidence tier assignment, confidence scoring
- **Interfaces**: Repository ports, provider ports

#### Infrastructure Layer

**File**: `backend/app/infrastructure/`

Adapters implementing domain interfaces:

| Component | Technology | Purpose |
|-----------|------------|---------|
| **db/** | SQLAlchemy 2.0 + PostgreSQL | Application state, auth, metadata |
| **cache/** | Redis (volatile) | Analytics cache, rate limiting |
| **state/** | Redis (durable, AOF) | Idempotency, session state |
| **llm/** | Anthropic SDK | LLM gateway, prompt registry, PII scrubbing |
| **warehouse/** | DuckDB connector | Analytical queries via semantic layer |

### Data Platform - 8,116 LOC

**File**: `data_platform/`

#### Ingestion Pipeline

```python
# data_platform/ingestion/pipeline.py
discover() → conform() → validate() → land() → load() → reconcile()
```

**Phases**:
1. **Discover**: Find CSV files in landing zone
2. **Conform**: Parse, type coercion, schema validation
3. **Validate**: Great Expectations quality gates
4. **Land**: Write to Bronze (Parquet + audit ledger)
5. **Load**: Trigger dbt transformation
6. **Reconcile**: Row counts, checksums, quarantine failures

**File**: `data_platform/ingestion/`

#### Bronze Layer

- **Format**: Parquet files (snappy compression)
- **Schema**: YAML-defined with semantic types
- **Validation**: Schema conformance, null checks, range checks
- **Audit**: Every file tracked in `audit_ledger` table

**File**: `data_platform/bronze/`

#### dbt Transformation (67 models)

**File**: `data_platform/warehouse/models/`

| Layer | Model Count | Purpose |
|-------|-------------|---------|
| **Staging** | 5 | Raw → typed (stg_pos_sales, stg_inventory, etc.) |
| **Core** | 11 | Dimensional + fact (dim_product, fct_sales, etc.) |
| **Metrics** | 21 | Business metrics (mart_revenue, mart_profitability) |
| **Semantic** | 30 | Exposed views (v_revenue_summary, v_store_performance) |

**Key Models**:
- `dim_product` - SCD Type 2, product master
- `dim_store` - SCD Type 2, store master
- `fct_sales` - Grain: transaction line item
- `mart_revenue` - Revenue metrics by all dimensions
- `v_revenue_summary` - Semantic layer exposure

#### Silver Layer (Dimensional)

dbt models create conformed dimensions and facts:

```sql
-- Example: dim_product (SCD Type 2)
CREATE TABLE silver.dim_product AS
SELECT
  product_key,      -- Surrogate key
  product_id,       -- Natural key
  product_name,
  category,
  valid_from,       -- SCD tracking
  valid_to,
  is_current
FROM {{ ref('stg_product_master') }}
```

#### Gold Layer (Metrics)

Pre-aggregated metric marts:

```sql
-- Example: mart_revenue
CREATE TABLE gold.mart_revenue AS
SELECT
  date_day,
  store_id,
  category,
  SUM(revenue) AS revenue,
  SUM(quantity) AS units,
  COUNT(DISTINCT transaction_id) AS transactions
FROM {{ ref('fct_sales') }}
GROUP BY 1, 2, 3
```

### ML Module - 3,848 LOC

**File**: `ml/`

#### Ridge Forecaster

**Hand-written implementation** (no scikit-learn):

```python
# ml/forecasting/ridge.py (~200 LOC)
def fit(X, y, lambda_=1.0):
    """Closed-form ridge regression.

    Solves: (X^T X + λI)w = X^T y
    """
    XtX = X.T @ X
    reg = lambda_ * np.eye(X.shape[1])
    w = np.linalg.solve(XtX + reg, X.T @ y)
    return w
```

**Why hand-written**: Full control, no black-box, educational transparency

#### Feature Engineering

**File**: `ml/forecasting/features.py`

| Feature Type | Features | Example |
|-------------|----------|---------|
| **Calendar** | day_of_week, week_of_month, month, is_weekend, is_holiday | `is_weekend=1` |
| **Level** | rolling_mean_7d, rolling_mean_14d, rolling_mean_28d | `rolling_mean_7d=1250` |
| **Trend** | pct_change_7d, pct_change_28d | `pct_change_7d=0.05` |

**Total**: ~15 features per time step

#### Backtest Framework

Walk-forward validation with quality gates:

```python
# ml/forecasting/backtest.py
def walk_forward_validation(data, horizon=7, folds=10):
    """Expand window, forecast, measure, repeat."""
    for fold in range(folds):
        train = data[:cutoff]
        test = data[cutoff:cutoff+horizon]

        model = train_ridge(train)
        forecast = model.predict(test)

        mase = compute_mase(test, forecast, naive_baseline)
        if mase > 1.0:
            raise QualityGateFailure("Worse than naive")
```

**Metrics**:
- **MASE** (Mean Absolute Scaled Error) - vs. naive baseline
- **WAPE** (Weighted Absolute Percentage Error)

**Quality Gate**: MASE < 1.0 (must beat naive forecast)

#### Model Registry

**File**: `ml/forecasting/registry.py`

SQLite-backed model versioning:

```python
registry.register(
    model_id="revenue_forecast_2026-08-15",
    artifact_path="models/ridge_20260815.pkl",
    metrics={"mase": 0.85, "wape": 0.12},
    promoted=True,
)
```

**Promotion workflow**: dev → staging → prod

### UI (Streamlit) - 9,413 LOC

**File**: `ui/`

#### 12 Workspaces

| Workspace | File | Purpose |
|-----------|------|---------|
| **Command Center** | `1_Command_Center.py` | Executive dashboard, KPI overview |
| **AI Investigation** | `2_AI_Investigation.py` | Root cause analysis interface |
| **Decision Center** | `3_Decision_Center.py` | Recommendations, decision tracking |
| **AI Analyst** | `4_AI_Analyst.py` | Natural language conversation |
| **Sales Intelligence** | `5_Sales_Intelligence.py` | Revenue analytics, trends |
| **Customer Intelligence** | `6_Customer_Intelligence.py` | Segments, cohorts, LTV |
| **Inventory Intelligence** | `7_Inventory_Intelligence.py` | Stock positions, turnover |
| **Store Intelligence** | `8_Store_Intelligence.py` | Store performance, benchmarks |
| **Forecast Intelligence** | `9_Forecast_Intelligence.py` | Forecast accuracy, model registry |
| **Risk Center** | `10_Risk_Center.py` | Risk monitoring, anomalies |
| **Executive Briefing** | `11_Executive_Briefing.py` | Reports, narratives |
| **Admin** | `12_Admin.py` | User management, permissions |

#### Component Library

**File**: `ui/components/`

Reusable UI components:
- `charts.py` - Plotly charts (line, bar, waterfall, heatmap)
- `metrics.py` - KPI cards, delta indicators
- `tables.py` - Sortable, filterable data tables
- `filters.py` - Date, dimension, segment filters
- `statements.py` - Fact/inference/caveat displays

---

## Data Flow

### Ingestion → Warehouse

```
CSV Files (landing/)
  ↓
Ingestion Pipeline
  ↓ (discover, conform, validate)
Bronze (Parquet)
  ↓
dbt run (67 models)
  ↓
Silver (Dimensional)
  ↓
Gold (Metrics)
  ↓
Semantic Views (v_*)
  ↓
Analytics Service
  ↓
API Response
```

**Orchestration**: Manual via `make ingest` and `make dbt` (no Airflow)

### Query → Response

```
User Question (UI)
  ↓ HTTP POST
API Endpoint (/analyst/)
  ↓
BusinessAnalystService.ask()
  ↓ (classify, route)
RcaService.investigate()
  ↓
Dimensional Investigators (9)
  ↓ SQL queries
Warehouse (DuckDB)
  ↓ Results
Evidence Tier Assignment
  ↓ Facts + Inferences
AnalystAnswer (deterministic)
  ↓
AnalystNarrator (LLM)
  ↓ EvidencePackage
LLM Gateway → AnthropicProvider
  ↓ Claude API
Natural Language Explanation
  ↓
Enhanced AnalystAnswer
  ↓ JSON response
UI Display (headline + evidence)
```

**Latency**:
- Query execution: ~50-200ms (warehouse)
- RCA computation: ~100-500ms (9 investigators)
- LLM narration: ~150-300ms (Claude Sonnet 3.5) - OPTIONAL
- Total: ~300-1000ms (p50), ~2000ms (p95)

### Recommendation → Outcome

```
Signal (e.g., low inventory turnover)
  ↓
RecommendationService.generate()
  ↓
7 Generators evaluate
  ↓
Rank by profit × reversibility
  ↓
Recommendation (with impact estimate)
  ↓ User decision
RecommendationService.decide(action="accept")
  ↓
Decision ledger (who, when, why)
  ↓ Execute action
Monitor outcome
  ↓ Measure actual impact
RecommendationService.measure_outcome()
  ↓
Feedback → Generator calibration
```

**Decision Loop**: Signal → Investigate → Recommend → Decide → Measure → Learn

---

## Service Boundaries

### Bounded Contexts

| Context | Services | Database Schema | Semantic Views |
|---------|----------|-----------------|----------------|
| **Analytics** | AnalyticsService, CustomerService, InventoryService | - | `v_revenue_*`, `v_customer_*`, `v_inventory_*` |
| **Investigation** | RcaService, BusinessAnalystService | `rca_result`, `insight`, `insight_feedback` | - |
| **Forecasting** | ForecastService | `forecast`, `forecast_accuracy` | `v_forecast_*` |
| **Recommendations** | RecommendationService | `recommendation`, `recommendation_decision`, `recommendation_outcome` | - |
| **Reporting** | ReportService | `report`, `report_schedule` | - |
| **Identity** | AuthService | `user`, `role`, `permission`, `user_role` | - |

### Communication Patterns

- **Synchronous**: HTTP REST (FastAPI) between UI and backend
- **Asynchronous**: Celery tasks for:
  - Scheduled reports
  - Forecast training
  - Batch recommendations
- **Event-Driven**: None (future: Kafka for outcome tracking)

### Data Ownership

- **Analytics Context** owns warehouse semantic layer
- **Investigation Context** owns RCA findings and insights
- **Forecasting Context** owns forecast models and accuracy metrics
- **Recommendations Context** owns decision ledger and outcomes

**No shared mutable state** between contexts (read-only views allowed).

---

## Semantic Layer

### Purpose

Governed vocabulary for analytics queries. Prevents SQL injection, enforces security, enables caching.

**File**: `backend/app/services/analytics/repository.py`

### 23 Analytics Domains

| Domain | Metrics | Dimensions | Example Query |
|--------|---------|------------|---------------|
| **revenue** | revenue, avg_order_value, transactions | store, category, channel | "Revenue by store, last 30 days" |
| **store** | sales_per_sqft, foot_traffic, conversion_rate | store, region | "Store performance by region" |
| **customer** | ltv, frequency, recency, segment | segment, cohort | "Customer LTV by segment" |
| **inventory** | stock_value, turnover, days_on_hand | product, warehouse | "Inventory turnover by product" |
| **marketing** | roi, cac, attributed_revenue | campaign, channel | "Marketing ROI by campaign" |
| **profitability** | gross_margin, cogs, net_margin | category, store | "Gross margin by category" |
| ... | ... | ... | ... |

### Query Patterns

| Pattern | SQL Template | Example |
|---------|-------------|---------|
| **summary** | `SELECT SUM(metric) FROM view WHERE period = ?` | "What is total revenue this month?" |
| **breakdown** | `SELECT dimension, SUM(metric) FROM view WHERE period = ? GROUP BY dimension` | "Revenue by store this month" |
| **trend** | `SELECT date, SUM(metric) FROM view WHERE date BETWEEN ? AND ? GROUP BY date` | "Daily revenue last 7 days" |

### Metric Definitions

**File**: `backend/app/services/analytics/domains/revenue.py`

```python
MetricDefinition(
    name="revenue",
    display_name="Revenue",
    sql_expression="SUM(line_total)",
    format="currency",
    aggregation="sum",
    semantic_type="MEASURE",
)
```

### Caching Strategy

**File**: `backend/app/core/middleware/cache.py`

| Query Pattern | TTL | Key Format |
|--------------|-----|------------|
| summary | 15s | `analytics:revenue:summary:{period}` |
| breakdown | 5min | `analytics:revenue:breakdown:{dimension}:{period}` |
| trend | 1min | `analytics:revenue:trend:{start}:{end}` |

**Invalidation**: Time-based expiry only (no manual invalidation)

---

## AI Architecture

### LLM Gateway (Evidence-Based Design)

**Critical Design Principle**: Claude receives structured evidence, explains verified facts, never generates business numbers.

#### Architecture Diagram

```
Application Service (e.g., RcaService)
  ↓
Deterministic Analysis (9 investigators)
  ↓
AnalystAnswer (facts, inferences, caveats)
  ↓
AnalystNarrator
  ↓
Build EvidencePackage (verified facts only)
  ↓
PromptRegistry (versioned prompts)
  ↓
LlmGateway
  ↓
Provider Selection (Anthropic or Mock)
  ↓
PII Scrubbing (remove emails, names)
  ↓
AnthropicProvider
  ↓
Claude API (Sonnet 3.5)
  ↓
Response Validation (no new numbers allowed)
  ↓
LLMResponse (natural language explanation)
  ↓
Enhanced AnalystAnswer (headline replaced)
  ↓
Return to user (facts/inferences/caveats unchanged)
```

#### Evidence-Based Grounding

**File**: `backend/app/infrastructure/llm/models.py`

```python
@dataclass
class EvidencePackage:
    """Structured facts for LLM to explain.

    LLM receives ONLY this - no raw SQL, no database access.
    """
    fact: str                    # "Revenue decreased 15%"
    value: float | str | None    # 1250000.00
    source: str                  # "fct_sales"
    tier: EvidenceTier           # MEASURED, MODELLED, etc.
    confidence: float            # 0.95
    computation: str | None      # "SUM(line_total)"

class EvidenceTier(Enum):
    """Evidence strength classification."""
    ARITHMETIC = "arithmetic"     # Direct calculation (2+2=4)
    MECHANICAL = "mechanical"     # Deterministic system (SQL SUM)
    STATISTICAL = "statistical"   # Regression, correlation
    ASSOCIATIVE = "associative"   # Observed pattern (not causal)
    ASSUMED = "assumed"           # Hypothesis (not verified)
    UNKNOWN = "unknown"           # Explicitly unmeasured
```

**LLM receives**: List of EvidencePackage objects
**LLM generates**: Natural language explanation citing evidence
**LLM NEVER sees**: Raw SQL, database connection, warehouse access

#### Prompt Registry

**File**: `backend/app/infrastructure/llm/prompts.py`

Versioned prompts with grounding enforcement:

```python
TASK_PROMPTS = {
    "summarize_rca_v1": """Based on the verified evidence provided,
    explain what caused the metric change.

    Evidence:
    {evidence}

    Rules:
    - ONLY cite facts from the evidence above
    - DO NOT perform calculations
    - DO NOT extrapolate trends
    - DO NOT invent numbers
    - Use tier-appropriate language:
      - ARITHMETIC/MECHANICAL: "shows", "demonstrates"
      - STATISTICAL: "suggests", "indicates"
      - ASSOCIATIVE: "is associated with", "moves with"

    Provide:
    1. Root cause (one sentence)
    2. Magnitude (cite evidence)
    3. Confidence level
    """,
}
```

**Versioning**: Prompts are immutable (`summarize_rca_v1`, `summarize_rca_v2`, etc.)

#### Provider Abstraction

**File**: `backend/app/infrastructure/llm/provider.py`

```python
class LLMProvider(ABC):
    """Abstract provider interface."""

    @abstractmethod
    async def generate(self, request: LLMRequest) -> LLMResponse:
        """Generate response from prompt."""
        pass
```

**Implementations**:

| Provider | File | Purpose |
|----------|------|---------|
| **MockProvider** | `mock_provider.py` | Deterministic testing, default mode |
| **AnthropicProvider** | `anthropic_provider.py` | Claude API integration |

**Default**: MockProvider (no external API calls, zero cost)

**Configuration**:
```bash
# .env
RM_LLM_PROVIDER=mock           # Default: mock
# RM_LLM_PROVIDER=anthropic    # Enable Claude (requires API key)
RM_LLM_ANTHROPIC_API_KEY=sk-ant-...  # Only if provider=anthropic
```

#### Mock Provider

**File**: `backend/app/infrastructure/llm/mock_provider.py`

Deterministic responses for testing:

```python
class MockProvider(LLMProvider):
    """Returns templated responses, no external API calls."""

    async def generate(self, request: LLMRequest) -> LLMResponse:
        # Template-based response from evidence
        facts = [e.fact for e in request.evidence]
        response = f"Based on analysis: {', '.join(facts)}"

        return LLMResponse(
            content=response,
            status="success",
            tokens_in=len(request.prompt.split()),
            tokens_out=len(response.split()),
            estimated_cost_usd=0.0,  # Free
        )
```

#### Anthropic Provider

**File**: `backend/app/infrastructure/llm/anthropic_provider.py`

Real Claude API integration:

```python
class AnthropicProvider(LLMProvider):
    """Claude API integration via Anthropic SDK."""

    def __init__(self, api_key: str, model: str = "claude-sonnet-3-5-20240620"):
        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model

    async def generate(self, request: LLMRequest) -> LLMResponse:
        # Build messages
        messages = [{"role": "user", "content": request.prompt}]

        # Call Claude API
        response = await self._client.messages.create(
            model=self._model,
            system=request.system_prompt,
            messages=messages,
            max_tokens=request.max_tokens,
            temperature=request.temperature,
        )

        return LLMResponse(
            content=response.content[0].text,
            status="success",
            tokens_in=response.usage.input_tokens,
            tokens_out=response.usage.output_tokens,
            estimated_cost_usd=self._estimate_cost(response.usage),
        )
```

**Model**: Claude Sonnet 3.5 (default)
**Cost**: ~$0.003 per request (estimated)

#### PII Scrubbing

**File**: `backend/app/infrastructure/llm/scrubbing.py`

```python
def scrub_pii(text: str) -> str:
    """Remove PII before sending to LLM."""
    # Email addresses
    text = re.sub(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b',
                  '[EMAIL]', text)

    # Phone numbers
    text = re.sub(r'\b\d{3}[-.]?\d{3}[-.]?\d{4}\b', '[PHONE]', text)

    # Credit cards
    text = re.sub(r'\b\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}\b', '[CARD]', text)

    return text
```

**Status**: Basic regex-based scrubbing (enterprise needs advanced NER)

#### Usage Tracking

**File**: `backend/app/infrastructure/db/models/ai.py`

```python
class LlmUsage(Base):
    """LLM usage ledger for cost tracking."""
    __tablename__ = "llm_usage"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    user_id: Mapped[uuid.UUID | None]
    provider: Mapped[str]               # "anthropic", "mock"
    model_id: Mapped[str]               # "claude-sonnet-3-5-20240620"
    prompt_version: Mapped[str]         # "summarize_rca_v1"
    tokens_in: Mapped[int]
    tokens_out: Mapped[int]
    estimated_cost_usd: Mapped[Decimal]
    created_at: Mapped[datetime]
```

**Metrics**:
- Total cost per user
- Token usage per capability
- Average latency per model
- Failure rate per prompt version

#### What the LLM Can and Cannot Do

**✅ LLM CAN**:
- Explain verified facts from EvidencePackage
- Cite evidence sources
- Use tier-appropriate language (MEASURED vs. STATISTICAL)
- Generate natural language headlines
- Provide confidence levels from evidence
- Suggest follow-up questions

**❌ LLM CANNOT**:
- Generate business numbers
- Perform calculations
- Access database directly
- Execute SQL queries
- Modify recommendations
- Change forecast values
- Invent facts not in evidence
- Extrapolate trends beyond data

**Enforced by**:
- No database connection in LLM context
- Evidence-only inputs
- Response validation (rejects new numbers)
- Fallback to deterministic templates on failure

---

## Decision Loop

### Signal → Action → Outcome

```
┌─────────────────── DETECTION ───────────────────┐
│  Anomaly Detection → Signal                     │
│  (e.g., inventory turnover below threshold)     │
└──────────────────────┬──────────────────────────┘
                       ↓
┌─────────────────── INVESTIGATION ───────────────┐
│  RcaService.investigate()                       │
│  → 9 dimensional investigators                  │
│  → Evidence tier assignment                     │
│  → AnalystAnswer (facts, inferences, caveats)   │
└──────────────────────┬──────────────────────────┘
                       ↓
┌─────────────────── RECOMMENDATION ──────────────┐
│  RecommendationService.generate()               │
│  → 7 generators evaluate                        │
│  → Rank by: profit × (1 / risk)                 │
│  → Estimate impact (revenue, cost, profit)      │
└──────────────────────┬──────────────────────────┘
                       ↓
┌─────────────────── DECISION ────────────────────┐
│  User reviews recommendation                    │
│  → Accept, Reject, or Defer                     │
│  → RecommendationService.decide()               │
│  → Record: who, when, why                       │
└──────────────────────┬──────────────────────────┘
                       ↓
┌─────────────────── EXECUTION ───────────────────┐
│  Action executed (e.g., reorder 500 units)      │
│  → Manual (user performs action)                │
│  → Automated (future: workflow engine)          │
└──────────────────────┬──────────────────────────┘
                       ↓
┌─────────────────── MEASUREMENT ─────────────────┐
│  RecommendationService.measure_outcome()        │
│  → Query actual metrics post-action             │
│  → Compare to projected impact                  │
│  → Variance = actual - projected                │
└──────────────────────┬──────────────────────────┘
                       ↓
┌─────────────────── LEARNING ────────────────────┐
│  RecommendationService.calibrate()              │
│  → Adjust generator weights                     │
│  → Update risk profiles                         │
│  → Refine impact estimation                     │
└──────────────────────────────────────────────────┘
```

### Decision Ledger

**File**: `backend/app/infrastructure/db/models/recommendations.py`

```python
class RecommendationDecision(Base):
    """Tracks user decisions on recommendations."""
    __tablename__ = "recommendation_decision"

    recommendation_id: Mapped[uuid.UUID]  # FK to recommendation
    user_id: Mapped[uuid.UUID]            # Who decided
    decision: Mapped[str]                 # "accept", "reject", "defer"
    rationale: Mapped[str | None]         # Why
    decided_at: Mapped[datetime]
```

### Outcome Measurement

**File**: `backend/app/infrastructure/db/models/recommendations.py`

```python
class RecommendationOutcome(Base):
    """Tracks actual vs. projected impact."""
    __tablename__ = "recommendation_outcome"

    recommendation_id: Mapped[uuid.UUID]
    measured_at: Mapped[datetime]

    # Projected (from generator)
    projected_revenue: Mapped[Decimal | None]
    projected_profit: Mapped[Decimal | None]

    # Actual (from warehouse)
    actual_revenue: Mapped[Decimal | None]
    actual_profit: Mapped[Decimal | None]

    # Variance
    variance_revenue: Mapped[Decimal | None]
    variance_profit: Mapped[Decimal | None]
```

### Calibration Loop

Generators learn from outcomes:

```python
# backend/app/services/recommendations/service.py
def calibrate_generator(generator_id: str):
    """Adjust generator based on historical accuracy."""
    outcomes = repository.get_outcomes(generator_id)

    # Compute average variance
    avg_variance = mean([o.variance_profit for o in outcomes])

    # Penalize overconfident generators
    if avg_variance > threshold:
        generator.weight *= 0.9  # Reduce influence
    else:
        generator.weight *= 1.1  # Increase trust
```

---

## Technology Stack

### Backend

| Component | Technology | Version | Purpose |
|-----------|-----------|---------|---------|
| **Framework** | FastAPI | 0.115+ | Async Python web framework |
| **ORM** | SQLAlchemy | 2.0+ | Database ORM (async) |
| **Database** | PostgreSQL | 16+ | Application database |
| **Cache** | Redis | 7+ | Volatile cache (LRU eviction) |
| **State** | Redis | 7+ | Durable state (AOF persistence) |
| **Auth** | JWT (RS256) | - | Asymmetric key authentication |
| **Password** | bcrypt | - | Password hashing |
| **Migrations** | Alembic | - | Database schema versioning |
| **Testing** | pytest | - | Unit + integration tests |
| **Linting** | ruff | - | Fast Python linter |
| **Type Check** | mypy | - | Static type checking |
| **LLM** | Anthropic SDK | 0.40+ | Claude API integration |

### Data Platform

| Component | Technology | Version | Purpose |
|-----------|-----------|---------|---------|
| **Warehouse** | DuckDB | 1.0+ | In-process analytical database |
| **Transformation** | dbt | 1.8+ | SQL-based data modeling |
| **Storage** | Parquet | - | Columnar file format (Bronze) |
| **Validation** | Great Expectations | - | Data quality gates |
| **Ingestion** | Python | 3.12+ | CSV → Bronze pipeline |

### ML

| Component | Technology | Version | Purpose |
|-----------|-----------|---------|---------|
| **Compute** | NumPy | 2.0+ | Array operations |
| **Forecasting** | Hand-written ridge | - | ~200 LOC ridge regression |
| **Model Registry** | SQLite | - | Model versioning |

**Note**: No scikit-learn, no XGBoost, no TensorFlow. All ML is hand-written for transparency.

### UI

| Component | Technology | Version | Purpose |
|-----------|-----------|---------|---------|
| **Framework** | Streamlit | 1.38+ | Python-based UI |
| **Charts** | Plotly | 5.24+ | Interactive visualizations |
| **Tables** | Pandas | 2.2+ | Data manipulation |

### Infrastructure

| Component | Technology | Version | Purpose |
|-----------|-----------|---------|---------|
| **Container** | Docker | 27+ | Service containerization |
| **Orchestration** | Docker Compose | 2.29+ | Multi-service orchestration |
| **Reverse Proxy** | Nginx | 1.27+ | TLS termination, routing |
| **Task Queue** | Celery | 5.4+ | Background jobs |
| **Message Broker** | Redis | 7+ | Celery backend |
| **Monitoring** | Prometheus | 2.54+ | Metrics collection |
| **Dashboards** | Grafana | 11+ | Metrics visualization |

### Development

| Component | Technology | Version | Purpose |
|-----------|-----------|---------|---------|
| **Package Manager** | uv | 0.4+ | Fast Python dependency management |
| **Workspace** | uv workspace | - | Monorepo (4 members) |
| **CI** | GitHub Actions | - | Automated testing, linting |
| **Architecture** | import-linter | - | Layer dependency enforcement |

---

## Deployment Architecture

### Docker Compose Orchestration

**Files**:
- `infra/compose/compose.yml` - Base services
- `infra/compose/compose.dev.yml` - Development overrides
- `infra/compose/compose.staging.yml` - Staging overrides
- `infra/compose/compose.prod.yml` - Production overrides

### Service Topology (Production)

```
┌──────────────────── EDGE NETWORK ────────────────────┐
│                                                       │
│  ┌─────────────────────────────────────────────┐     │
│  │  nginx (TLS termination)                    │     │
│  │  Port: 443 (external)                       │     │
│  └──────────────────┬──────────────────────────┘     │
│                     │                                │
└─────────────────────┼────────────────────────────────┘
                      │
┌─────────────────────▼─── APP NETWORK ────────────────┐
│                                                       │
│  ┌──────────────────────────────────────────────┐    │
│  │  backend-api (FastAPI)                       │    │
│  │  Port: 8000 (internal only)                  │    │
│  │  Workers: 2 (prod), 1 (dev)                  │    │
│  │  Resources: 1 CPU, 512MB RAM                 │    │
│  └──────────────────┬───────────────────────────┘    │
│                     │                                │
│  ┌──────────────────▼───────────────────────────┐    │
│  │  ui (Streamlit)                              │    │
│  │  Port: 8501 (internal only)                  │    │
│  └──────────────────────────────────────────────┘    │
│                                                       │
│  ┌──────────────────────────────────────────────┐    │
│  │  celery-worker (background tasks)            │    │
│  │  Queues: default, forecasting, reports       │    │
│  └──────────────────────────────────────────────┘    │
│                                                       │
│  ┌──────────────────────────────────────────────┐    │
│  │  celery-beat (scheduler)                     │    │
│  └──────────────────────────────────────────────┘    │
│                                                       │
└───────────────────────────────────────────────────────┘
                      │
┌─────────────────────▼─── DATA NETWORK ───────────────┐
│                                                       │
│  ┌──────────────────────────────────────────────┐    │
│  │  postgres (application DB)                   │    │
│  │  Port: 5432 (internal only)                  │    │
│  │  Volume: pgdata (persistent)                 │    │
│  └──────────────────────────────────────────────┘    │
│                                                       │
│  ┌──────────────────────────────────────────────┐    │
│  │  redis-cache (volatile, LRU)                 │    │
│  │  Port: 6379 (internal only)                  │    │
│  │  Persistence: none                           │    │
│  └──────────────────────────────────────────────┘    │
│                                                       │
│  ┌──────────────────────────────────────────────┐    │
│  │  redis-state (durable, AOF)                  │    │
│  │  Port: 6380 (internal only)                  │    │
│  │  Persistence: appendonly.aof                 │    │
│  │  Volume: redis-state (persistent)            │    │
│  └──────────────────────────────────────────────┘    │
│                                                       │
└───────────────────────────────────────────────────────┘
```

### Network Isolation

| Network | Purpose | Exposed Ports |
|---------|---------|---------------|
| **edge** | Public-facing | 443 (nginx TLS) |
| **app** | Application services | None (internal only) |
| **data** | Data tier | None (internal only) |

**Security**: Production unpublishes all internal ports (`compose.prod.yml`)

### Container Hardening

**File**: `infra/docker/backend/Dockerfile`

```dockerfile
# Run as non-root
USER appuser

# Read-only filesystem
RUN --mount=type=bind,target=/tmp/app,source=. \
    uv sync --frozen

# Security options (compose.prod.yml)
security_opt:
  - no-new-privileges:true
read_only: true
tmpfs:
  - /tmp:size=100M,mode=1777
```

### Volume Management

| Volume | Purpose | Backup | Retention |
|--------|---------|--------|-----------|
| **pgdata** | PostgreSQL data | Daily | 30 days |
| **redis-state** | Durable state | Daily | 7 days |
| **warehouse** | DuckDB files | Weekly | 90 days |
| **models** | Forecast models | On promotion | Indefinite |

**Backup Script**: `infra/scripts/backup.sh` (pg_dump + tar + gzip)

### Environment Configuration

**File**: `.env.example`

```bash
# Application
RM_APP_NAME=RetailMind AI
RM_APP_BASE_URL=https://retailmind.example.com
RM_ENV=production

# Database
RM_DB_HOST=postgres
RM_DB_PORT=5432
RM_DB_NAME=retailmind
RM_DB_USER=retailmind
RM_DB_PASSWORD_FILE=/run/secrets/db_password  # Secrets via files
RM_DB_SSLMODE=require

# Redis
RM_REDIS_CACHE_HOST=redis-cache
RM_REDIS_STATE_HOST=redis-state

# Auth
RM_AUTH_JWT_PRIVATE_KEY_FILE=/run/secrets/jwt_private_key  # RS256
RM_AUTH_JWT_PUBLIC_KEY_FILE=/run/secrets/jwt_public_key
RM_AUTH_ACCESS_TOKEN_TTL_MINUTES=15
RM_AUTH_REFRESH_TOKEN_TTL_DAYS=7

# LLM (optional)
RM_LLM_PROVIDER=mock  # or "anthropic"
# RM_LLM_ANTHROPIC_API_KEY_FILE=/run/secrets/anthropic_api_key

# Rate Limiting
RM_RATE_LIMIT_ENABLED=true
RM_RATE_LIMIT_PER_IP=100/minute
RM_RATE_LIMIT_PER_USER=200/minute

# Monitoring
RM_PROMETHEUS_ENABLED=true
RM_PROMETHEUS_PORT=9090
```

**Secrets Management**: File-based secrets (`/run/secrets/`) mounted read-only

---

## Security Architecture

### Authentication Flow

```
User → POST /api/v1/auth/login
  ↓
Verify password (bcrypt)
  ↓
Generate JWT (RS256 with private key)
  ↓
Return: {access_token, refresh_token}
  ↓
User → GET /api/v1/analytics/... (Authorization: Bearer <token>)
  ↓
Verify JWT (RS256 with public key)
  ↓
Extract user_id, roles, permissions
  ↓
Check permission for resource
  ↓
Return data or 403 Forbidden
```

**Files**:
- `backend/app/services/auth/service.py` - Authentication logic
- `backend/app/api/middleware/auth.py` - JWT verification middleware

### RBAC Model

| Role | Permissions | Example User |
|------|-------------|--------------|
| **CEO** | All modules, all actions | Executive |
| **CFO** | Financials, profitability, forecasting | Finance lead |
| **CMO** | Marketing, customers, campaigns | Marketing lead |
| **Store Manager** | Store-specific analytics, local inventory | Regional manager |
| **Inventory Planner** | Inventory, purchasing, allocation | Supply chain team |
| **Analyst** | Read-only analytics, investigation | Data analyst |

**File**: `backend/app/infrastructure/db/models/auth.py`

```python
class Permission(Base):
    """Granular permissions."""
    __tablename__ = "permission"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    resource: Mapped[str]  # "analytics", "rca", "recommendations"
    action: Mapped[str]    # "read", "write", "execute"
    scope: Mapped[str]     # "all", "own", "region"

class UserRole(Base):
    """Many-to-many: users ↔ roles."""
    __tablename__ = "user_role"

    user_id: Mapped[uuid.UUID]
    role_id: Mapped[uuid.UUID]
```

### Rate Limiting

**File**: `backend/app/core/middleware/rate_limit.py`

Redis sliding window implementation:

```python
# ZSET: "rate_limit:{ip_address}" → [(timestamp, request_id), ...]
async def check_rate_limit(ip: str, limit: int = 100, window_seconds: int = 60):
    now = time.time()
    window_start = now - window_seconds

    # Remove expired entries
    await redis.zremrangebyscore(f"rate_limit:{ip}", 0, window_start)

    # Count requests in window
    count = await redis.zcard(f"rate_limit:{ip}")

    if count >= limit:
        raise RateLimitExceeded(f"{limit} requests per {window_seconds}s")

    # Add current request
    await redis.zadd(f"rate_limit:{ip}", {str(uuid.uuid4()): now})
```

**Limits**:
- 100 requests/min per IP address
- 200 requests/min per authenticated user
- 10 requests/min for /auth/login (brute force protection)

### Idempotency

**File**: `backend/app/core/middleware/idempotency.py`

Request deduplication via Redis cache:

```python
# Client sends: Idempotency-Key: <uuid>
# Cache key: idempotency:{user_id}:{idempotency_key}
# Value: {status_code, headers, body, created_at}
# TTL: 24 hours

async def handle_idempotent_request(request: Request):
    key = request.headers.get("Idempotency-Key")
    if not key:
        return await process_request(request)

    cache_key = f"idempotency:{user_id}:{key}"

    # Check cache
    cached = await redis.get(cache_key)
    if cached:
        return Response(**json.loads(cached))  # Return cached response

    # Execute request
    response = await process_request(request)

    # Cache successful mutations (2xx, 3xx)
    if 200 <= response.status_code < 400:
        await redis.setex(cache_key, 86400, json.dumps({
            "status_code": response.status_code,
            "headers": dict(response.headers),
            "body": response.body.decode(),
        }))

    return response
```

**Applied to**: POST, PUT, PATCH, DELETE (mutations only)

### Security Headers

**File**: `backend/app/core/middleware/security.py`

```python
headers = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "X-XSS-Protection": "1; mode=block",
    "Strict-Transport-Security": "max-age=31536000",
}
```

### TLS Configuration

**File**: `infra/docker/nginx/nginx.conf`

```nginx
server {
    listen 443 ssl http2;
    server_name retailmind.example.com;

    ssl_certificate /etc/nginx/tls/cert.pem;
    ssl_certificate_key /etc/nginx/tls/key.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;

    location /api/ {
        proxy_pass http://backend-api:8000;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location / {
        proxy_pass http://ui:8501;
    }
}
```

**Certificate Generation**: `scripts/generate_tls.sh` (self-signed for dev)

---

## Appendix

### Line Counts

Generated via `cloc`:

```
Language         Files    Blank    Comment       Code
--------         -----    -----    -------       ----
Python            324     8,459     15,203     29,932  (backend)
Python             42     1,205      1,847      8,116  (data_platform)
Python             23       582        941      3,848  (ml)
Python             48     1,342      1,679      9,413  (ui)
SQL                67       834        412      4,201  (dbt models)
YAML               45       123         89      1,892
Dockerfile         12        78        124        186
Nginx               1        12         15         42
--------         -----    -----    -------       ----
TOTAL             562    12,635     20,310     57,630
```

### Test Counts

```bash
$ make test
===== 794 passed in 58.32s =====

$ make test-integration
===== 305 passed in 847.19s =====
```

**Coverage**:
- Unit tests: 87% line coverage
- Integration tests: 73% line coverage

### Architecture Decision Records

**File**: `docs/architecture/adr/`

Key ADRs (referenced in code comments):

- ADR-001: Clean Architecture with 4 layers
- ADR-002: Medallion data pipeline (Bronze/Silver/Gold)
- ADR-003: Hand-written ridge regression (no scikit-learn)
- ADR-004: LLM gateway with evidence-based grounding
- ADR-005: Semantic layer over raw warehouse
- ADR-006: Docker Compose (not Kubernetes)

**Status**: ADRs referenced in code but not yet committed to docs/ (TODO)

### References

- [Clean Architecture](https://blog.cleancoder.com/uncle-bob/2012/08/13/the-clean-architecture.html) - Robert C. Martin
- [Medallion Architecture](https://www.databricks.com/glossary/medallion-architecture) - Databricks
- [Evidence-Based AI](https://arxiv.org/abs/2302.00093) - Grounding in retrieval (RAG pattern)
- [dbt Best Practices](https://docs.getdbt.com/best-practices) - dbt Labs

---

**Maintained by**: RetailMind AI Contributors
**License**: MIT
**Last Reviewed**: 2026-08-15
