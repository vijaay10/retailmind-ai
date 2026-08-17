# Database Schema Documentation

PostgreSQL database schema for RetailMind AI - application state, authentication, metadata, and operational tracking.

**Last Updated**: 2026-08-15
**Version**: 0.9.0
**Database**: PostgreSQL 16+
**ORM**: SQLAlchemy 2.0 (async)

---

## Table of Contents

- [Overview](#overview)
- [Schema Diagram](#schema-diagram)
- [Database Architecture](#database-architecture)
- [Tables by Domain](#tables-by-domain)
- [Entity Relationships](#entity-relationships)
- [Indexes & Constraints](#indexes--constraints)
- [Migrations](#migrations)
- [Data Retention](#data-retention)
- [Security Model](#security-model)
- [Backup Strategy](#backup-strategy)

---

## Overview

RetailMind AI uses two separate databases with distinct purposes:

| Database | Technology | Purpose | Tables | Size (typical) |
|----------|-----------|---------|--------|----------------|
| **Application DB** | PostgreSQL 16 | Auth, metadata, operational tracking | 35+ | <1 GB |
| **Warehouse** | DuckDB | Analytical queries, metrics | 67 dbt models | 5-50 GB |

**This document covers the Application DB (PostgreSQL).**

### Key Characteristics

- **35+ tables** across 8 domains
- **Multi-tenancy** - All data scoped to `tenant_id`
- **RBAC** - 6 roles with granular permissions
- **Audit trails** - Decision ledgers, outcome tracking
- **LLM usage tracking** - Token consumption, cost estimation
- **SCD Type 2** not used here (warehouse handles SCD)

### Design Principles

1. **Single Source of Truth** - Evidence stored as pointers (query IDs), not copies
2. **Explicit Cascades** - `ondelete` behavior documented per relationship
3. **Structural Deduplication** - Partial unique indexes prevent duplicate active recommendations
4. **Honesty Rule** - Impact estimation methods are mandatory (no "magic" predictions)
5. **Tenant Isolation** - Foreign keys enforce row-level security

---

## Schema Diagram

### High-Level Domains

```
┌─────────────────────────────────────────────────────────────┐
│  IDENTITY & TENANCY                                         │
│  ┌──────────┐   ┌──────────┐   ┌─────────┐   ┌──────────┐  │
│  │  tenant  │──<│ app_user │──<│  role   │──<│ user_role│  │
│  └──────────┘   └──────────┘   └─────────┘   └──────────┘  │
│                      │                                       │
│                      ├──< refresh_token                      │
│                      └──< api_key                            │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│  DECISION INTELLIGENCE                                      │
│  ┌──────────────────┐   ┌──────────────────────┐            │
│  │ recommendation   │──<│ recommendation_      │            │
│  │                  │   │ feedback             │            │
│  └───────┬──────────┘   └──────────────────────┘            │
│          │                                                   │
│          └──< recommendation_outcome                        │
│          └──< recommendation_decision (future)              │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│  INVESTIGATION & INSIGHTS                                   │
│  ┌──────────────┐   ┌───────────┐   ┌────────────────────┐  │
│  │  rca_result  │   │  insight  │──<│ insight_feedback   │  │
│  └──────────────┘   └───────────┘   └────────────────────┘  │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│  AI & LLM TRACKING                                          │
│  ┌──────────────┐   ┌───────────────┐   ┌────────────────┐  │
│  │  llm_usage   │   │  nlq_turn     │   │  nlq_feedback  │  │
│  └──────────────┘   └───────────────┘   └────────────────┘  │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│  REPORTING & DASHBOARDS                                     │
│  ┌──────────────┐   ┌────────────────┐   ┌──────────────┐   │
│  │  report      │   │ report_        │   │  dashboard   │   │
│  │              │──<│ schedule       │   │              │   │
│  └──────────────┘   └────────────────┘   └──────────────┘   │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│  ALERTS & NOTIFICATIONS                                     │
│  ┌───────────────┐   ┌─────────────────┐   ┌──────────────┐ │
│  │  alert_rule   │   │  alert_trigger  │   │ notification │ │
│  └───────────────┘   └─────────────────┘   └──────────────┘ │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│  DATA PLATFORM METADATA                                     │
│  ┌───────────────┐   ┌────────────────┐   ┌──────────────┐  │
│  │  data_source  │   │ data_snapshot  │   │ audit_ledger │  │
│  └───────────────┘   └────────────────┘   └──────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

---

## Database Architecture

### Connection Configuration

**File**: `backend/app/core/config.py`

```python
class DatabaseSettings(BaseSettings):
    host: str = "postgres"
    port: int = 5432
    name: str = "retailmind"
    user: str = "retailmind"
    password_file: str | None = None  # Secrets via file
    sslmode: str = "prefer"  # "require" in production

    @property
    def url(self) -> str:
        password = self._read_password()
        return f"postgresql+asyncpg://{self.user}:{password}@{self.host}:{self.port}/{self.name}"
```

**Environment Variables**:

```bash
RM_DB_HOST=postgres
RM_DB_PORT=5432
RM_DB_NAME=retailmind
RM_DB_USER=retailmind
RM_DB_PASSWORD_FILE=/run/secrets/db_password  # Production
RM_DB_SSLMODE=require  # Production
```

### Connection Pool

**File**: `backend/app/infrastructure/db/session.py`

```python
engine = create_async_engine(
    settings.database.url,
    pool_size=10,                # Max connections
    max_overflow=20,             # Burst capacity
    pool_pre_ping=True,          # Health check before use
    pool_recycle=3600,           # Recycle after 1 hour
    echo=False,                  # SQL logging (dev only)
)
```

**Pool Sizing**:
- Development: 5 connections
- Staging: 10 connections
- Production: 20 connections (2 uvicorn workers × 10 pool size)

### Session Management

```python
async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,  # Don't expire objects after commit
)

async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency for database session."""
    async with async_session_factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
```

---

## Tables by Domain

### 1. Identity & Tenancy (5 tables)

#### `tenant`

Root of all row scoping. Every data row belongs to a tenant.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | UUID | PK | Primary key |
| `slug` | VARCHAR | UNIQUE, NOT NULL | URL-safe identifier (e.g., "acme-retail") |
| `name` | VARCHAR | NOT NULL | Display name |
| `plan` | VARCHAR | DEFAULT 'standard' | Subscription plan |
| `base_currency` | VARCHAR(3) | DEFAULT 'USD' | ISO-4217 currency code |
| `llm_budget_tokens_month` | BIGINT | DEFAULT 5000000 | Monthly token budget |
| `created_at` | TIMESTAMP | DEFAULT now() | Created timestamp |
| `updated_at` | TIMESTAMP | DEFAULT now() | Last updated |

**File**: `backend/app/infrastructure/db/models/auth.py`

#### `app_user`

Application users with authentication credentials.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | UUID | PK | Primary key |
| `tenant_id` | UUID | FK(tenant.id), NOT NULL | Tenant association |
| `email` | CITEXT | UNIQUE per tenant | Case-insensitive email |
| `display_name` | VARCHAR | NOT NULL | User display name |
| `password_hash` | VARCHAR | NULL | argon2id hash (NULL for SSO) |
| `status` | VARCHAR | DEFAULT 'active' | active \| suspended \| deleted |
| `token_version` | INT | DEFAULT 1 | Incremented to invalidate JWTs |
| `last_login_at` | TIMESTAMP | NULL | Last successful login |
| `created_at` | TIMESTAMP | DEFAULT now() | Created timestamp |
| `updated_at` | TIMESTAMP | DEFAULT now() | Last updated |

**Constraints**:
- `UNIQUE(tenant_id, email)` - Email unique per tenant
- `CHECK(status IN ('active', 'suspended', 'deleted'))`

**Cascades**:
- Delete tenant → `RESTRICT` (cannot delete tenant with users)
- Delete user → `CASCADE` to `user_role`, `refresh_token`, `api_key`

#### `role`

Fixed role catalog (seeded data, not user-managed).

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | SMALLINT | PK, no autoincrement | Role ID (1-6) |
| `key` | VARCHAR | UNIQUE, NOT NULL | Role key (CEO, CFO, etc.) |
| `description` | VARCHAR | NOT NULL | Human-readable description |

**Seeded Roles**:

| ID | Key | Description |
|----|-----|-------------|
| 1 | CEO | Chief Executive - full access |
| 2 | CFO | Chief Financial Officer - financials, forecasting |
| 3 | CMO | Chief Marketing Officer - marketing, customers |
| 4 | STORE_MANAGER | Store Manager - store-scoped analytics |
| 5 | INVENTORY_PLANNER | Inventory Planner - inventory, purchasing |
| 6 | ANALYST | Analyst - read-only analytics, investigations |

**File**: `backend/app/domain/auth/permissions.py` (role definitions)

#### `user_role`

Many-to-many: users ↔ roles.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `user_id` | UUID | PK, FK(app_user.id) | User ID |
| `role_id` | SMALLINT | PK, FK(role.id) | Role ID |
| `granted_at` | TIMESTAMP | DEFAULT now() | Grant timestamp |
| `granted_by` | UUID | FK(app_user.id), NULL | Who granted the role |

**Cascades**:
- Delete user → `CASCADE` (remove user's role grants)
- Delete role → `RESTRICT` (cannot delete role in use)
- Delete granter → `SET NULL` (audit trail preserved)

#### `refresh_token`

Rotating refresh tokens with family-level theft detection.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | UUID | PK | Primary key |
| `user_id` | UUID | FK(app_user.id), NOT NULL | User ID |
| `token_hash` | VARCHAR | UNIQUE, NOT NULL | SHA-256 of opaque token |
| `family_id` | UUID | NOT NULL | Rotation family ID |
| `generation` | INT | NOT NULL | Generation in rotation chain |
| `expires_at` | TIMESTAMP | NOT NULL | Expiration timestamp |
| `rotated_at` | TIMESTAMP | NULL | Rotation timestamp |
| `revoked_at` | TIMESTAMP | NULL | Revocation timestamp |
| `created_at` | TIMESTAMP | DEFAULT now() | Created timestamp |

**Indexes**:
- `ix_refresh_token_family(user_id, family_id)` - Fast family lookup

**Theft Detection**: If a rotated token (`rotated_at IS NOT NULL`) is reused, the entire `family_id` is revoked.

#### `api_key`

Tenant-scoped programmatic access keys.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | UUID | PK | Primary key |
| `tenant_id` | UUID | FK(tenant.id), NOT NULL | Tenant ID |
| `prefix` | VARCHAR | UNIQUE, NOT NULL | Displayable prefix (rmk_live_xxxx) |
| `key_hash` | VARCHAR | NOT NULL | SHA-256 of full key |
| `name` | VARCHAR | NOT NULL | Human-readable name |
| `scopes` | JSONB | DEFAULT '[]'::jsonb | Permission subset |
| `last_used_at` | TIMESTAMP | NULL | Last use timestamp |
| `revoked_at` | TIMESTAMP | NULL | Revocation timestamp |
| `created_at` | TIMESTAMP | DEFAULT now() | Created timestamp |
| `updated_at` | TIMESTAMP | DEFAULT now() | Last updated |

**Scopes Format**:
```json
["analytics:read", "rca:execute", "forecasts:read"]
```

---

### 2. Decision Intelligence (4 tables)

#### `recommendation`

Actionable recommendations with impact estimates.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | UUID | PK | Primary key |
| `tenant_id` | UUID | FK(tenant.id), NOT NULL | Tenant ID |
| `type` | VARCHAR | NOT NULL | reorder \| markdown \| price_adjust |
| `subject` | JSONB | NOT NULL | Typed per rec type |
| `dedup_key` | VARCHAR | NOT NULL | Deterministic digest |
| `expected_impact` | JSONB | NOT NULL | Impact estimation |
| `rationale` | TEXT | NULL | LLM-generated explanation |
| `rule_id` | VARCHAR | NOT NULL | Which rule fired |
| `rule_version` | VARCHAR | NOT NULL | Rule version |
| `model_run_id` | VARCHAR | NULL | Forecast run provenance |
| `score` | NUMERIC(12,2) | NOT NULL | Ranking score |
| `status` | VARCHAR | DEFAULT 'proposed' | proposed \| accepted \| dismissed |
| `confidence` | VARCHAR | NOT NULL | HIGH \| MEDIUM \| LOW |
| `evidence` | JSONB | NOT NULL | Query IDs (pointers) |
| `expires_at` | TIMESTAMP | NOT NULL | Recommendation expiry |
| `data_snapshot_id` | VARCHAR | FK(data_snapshot.id) | Data version |
| `created_at` | TIMESTAMP | DEFAULT now() | Created timestamp |

**Indexes**:
- `ix_rec_inbox(tenant_id, status, type, expires_at)` - Inbox hot path
- `uq_rec_active_dedup(tenant_id, dedup_key)` UNIQUE WHERE status='proposed' - Structural deduplication

**Subject Format** (example for reorder):
```json
{
  "sku": "SKU-12345",
  "store_id": "STORE-42",
  "suggested_qty": 500,
  "order_by_date": "2026-08-20"
}
```

**Expected Impact Format**:
```json
{
  "metric": "revenue",
  "value_usd": 45000.00,
  "method": "forecast_driven",  // MANDATORY (honesty rule)
  "confidence": "high"
}
```

**Evidence Format** (pointers, not copies):
```json
{
  "query_ids": ["nlq-abc123", "forecast-def456"],
  "snapshot_id": "snapshot-20260815-1200"
}
```

#### `recommendation_feedback`

User acceptance/dismissal events.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | UUID | PK | Primary key |
| `recommendation_id` | UUID | FK(recommendation.id), NOT NULL | Recommendation ID |
| `actor_id` | UUID | FK(app_user.id), NOT NULL | Who acted |
| `action` | VARCHAR | NOT NULL | accepted \| dismissed |
| `reason_code` | VARCHAR | NULL | Required for dismissals |
| `note` | TEXT | NULL | Optional note |
| `created_at` | TIMESTAMP | DEFAULT now() | Action timestamp |

**Cascades**:
- Delete recommendation → `CASCADE` (remove feedback)
- Delete actor → `RESTRICT` (preserve audit trail)

**Reason Codes** (for dismissals):
- `NOT_RELEVANT` - Not applicable to current situation
- `ALREADY_PLANNED` - Action already in progress
- `TIMING_BAD` - Good idea, wrong timing
- `TRUST_LOW` - Don't trust the impact estimate
- `OTHER` - Free-form note required

#### `recommendation_outcome`

Actual vs. projected impact measurement.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | UUID | PK | Primary key |
| `recommendation_id` | UUID | FK(recommendation.id), NOT NULL | Recommendation ID |
| `measured_at` | TIMESTAMP | NOT NULL | Measurement timestamp |
| `projected_revenue` | NUMERIC(15,2) | NULL | Projected revenue impact |
| `actual_revenue` | NUMERIC(15,2) | NULL | Actual revenue impact |
| `projected_profit` | NUMERIC(15,2) | NULL | Projected profit impact |
| `actual_profit` | NUMERIC(15,2) | NULL | Actual profit impact |
| `variance_revenue` | NUMERIC(15,2) | NULL | actual - projected |
| `variance_profit` | NUMERIC(15,2) | NULL | actual - projected |
| `status` | VARCHAR | DEFAULT 'measuring' | measuring \| confirmed \| inconclusive |
| `notes` | TEXT | NULL | Analysis notes |

**Cascades**:
- Delete recommendation → `CASCADE`

**Measurement Period**: Typically 7-30 days after action execution.

#### `recommendation_decision` (future)

Formal decision ledger for governance.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | UUID | PK | Primary key |
| `recommendation_id` | UUID | FK(recommendation.id), NOT NULL | Recommendation ID |
| `user_id` | UUID | FK(app_user.id), NOT NULL | Decision maker |
| `decision` | VARCHAR | NOT NULL | accept \| reject \| defer |
| `rationale` | TEXT | NULL | Decision rationale |
| `decided_at` | TIMESTAMP | DEFAULT now() | Decision timestamp |

**Status**: Migration exists but not yet wired to UI.

---

### 3. Investigation & Insights (3 tables)

#### `rca_result`

Root cause analysis results (cached).

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | UUID | PK | Primary key |
| `tenant_id` | UUID | FK(tenant.id), NOT NULL | Tenant ID |
| `query_fingerprint` | VARCHAR | NOT NULL | Hash of query params |
| `metric` | VARCHAR | NOT NULL | Metric investigated |
| `period_start` | DATE | NOT NULL | Period start |
| `period_end` | DATE | NOT NULL | Period end |
| `findings` | JSONB | NOT NULL | Analysis results |
| `confidence_score` | NUMERIC(3,2) | NOT NULL | 0.0 - 1.0 |
| `prompt_version` | VARCHAR | NULL | LLM prompt version (if used) |
| `model_id` | VARCHAR | NULL | LLM model ID (if used) |
| `runtime_ms` | INT | NULL | Computation time |
| `created_at` | TIMESTAMP | DEFAULT now() | Created timestamp |

**Indexes**:
- `ix_rca_cache(tenant_id, query_fingerprint, created_at DESC)` - Cache lookup

**Cache TTL**: 1 hour (application-level eviction)

**Findings Format**:
```json
{
  "root_cause": {"dimension": "store", "value": "STORE-42", "contribution": -120000},
  "secondary_causes": [...],
  "evidence_tier": "MECHANICAL",
  "confidence": 0.95
}
```

#### `insight`

User-facing insights and anomaly detections.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | UUID | PK | Primary key |
| `tenant_id` | UUID | FK(tenant.id), NOT NULL | Tenant ID |
| `type` | VARCHAR | NOT NULL | anomaly \| trend \| correlation |
| `title` | VARCHAR | NOT NULL | Insight headline |
| `description` | TEXT | NOT NULL | Full explanation |
| `severity` | VARCHAR | NOT NULL | critical \| high \| medium \| low |
| `metric` | VARCHAR | NOT NULL | Affected metric |
| `dimensions` | JSONB | NOT NULL | Affected dimensions |
| `evidence` | JSONB | NOT NULL | Supporting data |
| `detected_at` | TIMESTAMP | DEFAULT now() | Detection timestamp |
| `expires_at` | TIMESTAMP | NULL | Insight expiry |
| `dismissed_at` | TIMESTAMP | NULL | User dismissal |

**Indexes**:
- `ix_insight_inbox(tenant_id, dismissed_at, severity, detected_at DESC)`

#### `insight_feedback`

User feedback on insights.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | UUID | PK | Primary key |
| `insight_id` | UUID | FK(insight.id), NOT NULL | Insight ID |
| `user_id` | UUID | FK(app_user.id), NOT NULL | User ID |
| `helpful` | BOOLEAN | NOT NULL | Was it helpful? |
| `action_taken` | VARCHAR | NULL | What action was taken |
| `note` | TEXT | NULL | Optional feedback |
| `created_at` | TIMESTAMP | DEFAULT now() | Feedback timestamp |

**Cascades**:
- Delete insight → `CASCADE`

---

### 4. AI & LLM Tracking (3 tables)

#### `llm_usage`

LLM request ledger for cost tracking.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | UUID | PK | Primary key |
| `tenant_id` | UUID | FK(tenant.id), NULL | Tenant ID (NULL for system) |
| `user_id` | UUID | FK(app_user.id), NULL | User who triggered request |
| `provider` | VARCHAR | NOT NULL | anthropic \| mock |
| `model_id` | VARCHAR | NOT NULL | claude-sonnet-3-5-20240620 |
| `prompt_version` | VARCHAR | NOT NULL | summarize_rca_v1 |
| `tokens_in` | INT | NOT NULL | Input tokens |
| `tokens_out` | INT | NOT NULL | Output tokens |
| `estimated_cost_usd` | NUMERIC(10,4) | NOT NULL | Estimated cost |
| `latency_ms` | INT | NULL | Request latency |
| `status` | VARCHAR | NOT NULL | success \| error \| timeout |
| `error` | TEXT | NULL | Error message (if failed) |
| `created_at` | TIMESTAMP | DEFAULT now() | Request timestamp |

**Indexes**:
- `ix_llm_usage_billing(tenant_id, created_at DESC)` - Billing queries

**Prometheus Metrics**: Exported via `/metrics` endpoint for real-time monitoring.

#### `nlq_turn`

Natural language query conversation turns.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | UUID | PK | Primary key |
| `tenant_id` | UUID | FK(tenant.id), NOT NULL | Tenant ID |
| `user_id` | UUID | FK(app_user.id), NOT NULL | User ID |
| `session_id` | UUID | NOT NULL | Conversation session |
| `turn_number` | INT | NOT NULL | Turn in conversation (1, 2, 3...) |
| `question` | TEXT | NOT NULL | User's question |
| `capability` | VARCHAR | NOT NULL | INVESTIGATE \| ANSWER \| etc. |
| `plan` | JSONB | NULL | Query plan (if NLQ) |
| `answer` | JSONB | NOT NULL | Structured answer |
| `prompt_version` | VARCHAR | NULL | LLM prompt version (if used) |
| `model_id` | VARCHAR | NULL | LLM model ID (if used) |
| `tokens_in` | INT | NULL | Input tokens (if LLM used) |
| `tokens_out` | INT | NULL | Output tokens (if LLM used) |
| `created_at` | TIMESTAMP | DEFAULT now() | Turn timestamp |

**Indexes**:
- `ix_nlq_session(user_id, session_id, turn_number)` - Conversation retrieval

#### `nlq_feedback`

User feedback on NLQ answers.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | UUID | PK | Primary key |
| `nlq_turn_id` | UUID | FK(nlq_turn.id), NOT NULL | NLQ turn ID |
| `helpful` | BOOLEAN | NOT NULL | Was it helpful? |
| `correct` | BOOLEAN | NULL | Was the answer correct? |
| `note` | TEXT | NULL | Free-form feedback |
| `created_at` | TIMESTAMP | DEFAULT now() | Feedback timestamp |

**Cascades**:
- Delete turn → `CASCADE`

---

### 5. Reporting & Dashboards (3 tables)

#### `report`

Saved reports and executive briefings.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | UUID | PK | Primary key |
| `tenant_id` | UUID | FK(tenant.id), NOT NULL | Tenant ID |
| `name` | VARCHAR | NOT NULL | Report name |
| `type` | VARCHAR | NOT NULL | executive_briefing \| deep_dive |
| `configuration` | JSONB | NOT NULL | Report parameters |
| `created_by` | UUID | FK(app_user.id), NULL | Creator |
| `created_at` | TIMESTAMP | DEFAULT now() | Created timestamp |
| `updated_at` | TIMESTAMP | DEFAULT now() | Last updated |

**Configuration Format**:
```json
{
  "period": "last_30_days",
  "sections": ["revenue", "profitability", "inventory"],
  "format": "pdf"
}
```

#### `report_schedule`

Scheduled report delivery.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | UUID | PK | Primary key |
| `report_id` | UUID | FK(report.id), NOT NULL | Report ID |
| `frequency` | VARCHAR | NOT NULL | daily \| weekly \| monthly |
| `recipients` | JSONB | NOT NULL | Email recipients |
| `last_run_at` | TIMESTAMP | NULL | Last execution |
| `next_run_at` | TIMESTAMP | NOT NULL | Next scheduled run |
| `enabled` | BOOLEAN | DEFAULT TRUE | Schedule enabled? |
| `created_at` | TIMESTAMP | DEFAULT now() | Created timestamp |

**Cascades**:
- Delete report → `CASCADE` (delete schedules)

**Recipients Format**:
```json
["cfo@example.com", "ceo@example.com"]
```

#### `dashboard`

Custom dashboard configurations.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | UUID | PK | Primary key |
| `tenant_id` | UUID | FK(tenant.id), NOT NULL | Tenant ID |
| `name` | VARCHAR | NOT NULL | Dashboard name |
| `layout` | JSONB | NOT NULL | Widget layout |
| `owner_id` | UUID | FK(app_user.id), NULL | Owner |
| `shared` | BOOLEAN | DEFAULT FALSE | Shared with team? |
| `created_at` | TIMESTAMP | DEFAULT now() | Created timestamp |
| `updated_at` | TIMESTAMP | DEFAULT now() | Last updated |

**Layout Format**:
```json
{
  "widgets": [
    {"type": "metric_card", "metric": "revenue", "period": "mtd"},
    {"type": "chart", "chart_type": "line", "metric": "orders", "period": "last_7_days"}
  ]
}
```

---

### 6. Alerts & Notifications (3 tables)

#### `alert_rule`

Alert rule definitions.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | UUID | PK | Primary key |
| `tenant_id` | UUID | FK(tenant.id), NOT NULL | Tenant ID |
| `name` | VARCHAR | NOT NULL | Rule name |
| `metric` | VARCHAR | NOT NULL | Monitored metric |
| `condition` | JSONB | NOT NULL | Trigger condition |
| `threshold` | NUMERIC(15,2) | NOT NULL | Alert threshold |
| `severity` | VARCHAR | NOT NULL | critical \| warning \| info |
| `enabled` | BOOLEAN | DEFAULT TRUE | Rule enabled? |
| `created_at` | TIMESTAMP | DEFAULT now() | Created timestamp |

**Condition Format**:
```json
{
  "operator": "less_than",
  "lookback_period": "1_hour",
  "aggregation": "average"
}
```

#### `alert_trigger`

Alert trigger history.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | UUID | PK | Primary key |
| `alert_rule_id` | UUID | FK(alert_rule.id), NOT NULL | Alert rule ID |
| `triggered_at` | TIMESTAMP | DEFAULT now() | Trigger timestamp |
| `value` | NUMERIC(15,2) | NOT NULL | Metric value |
| `resolved_at` | TIMESTAMP | NULL | Resolution timestamp |
| `acknowledged_by` | UUID | FK(app_user.id), NULL | Acknowledger |

**Cascades**:
- Delete rule → `CASCADE` (delete trigger history)

#### `notification`

Notification delivery tracking.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | UUID | PK | Primary key |
| `tenant_id` | UUID | FK(tenant.id), NOT NULL | Tenant ID |
| `user_id` | UUID | FK(app_user.id), NOT NULL | Recipient |
| `type` | VARCHAR | NOT NULL | alert \| recommendation \| report |
| `channel` | VARCHAR | NOT NULL | email \| slack \| sms |
| `subject` | VARCHAR | NOT NULL | Notification subject |
| `body` | TEXT | NOT NULL | Notification body |
| `sent_at` | TIMESTAMP | NULL | Delivery timestamp |
| `read_at` | TIMESTAMP | NULL | Read timestamp |
| `created_at` | TIMESTAMP | DEFAULT now() | Created timestamp |

---

### 7. Data Platform Metadata (3 tables)

#### `data_source`

External data source registry.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | UUID | PK | Primary key |
| `tenant_id` | UUID | FK(tenant.id), NOT NULL | Tenant ID |
| `name` | VARCHAR | NOT NULL | Source name |
| `type` | VARCHAR | NOT NULL | csv \| sftp \| api |
| `connection_config` | JSONB | NOT NULL | Connection details |
| `schema_definition` | JSONB | NOT NULL | Expected schema |
| `last_sync_at` | TIMESTAMP | NULL | Last successful sync |
| `enabled` | BOOLEAN | DEFAULT TRUE | Source enabled? |
| `created_at` | TIMESTAMP | DEFAULT now() | Created timestamp |

**Connection Config Format** (CSV example):
```json
{
  "type": "local_path",
  "path": "/data/landing/pos_sales",
  "file_pattern": "sales_*.csv"
}
```

#### `data_snapshot`

Data version snapshots for reproducibility.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | VARCHAR | PK | Snapshot ID (timestamp-based) |
| `tenant_id` | UUID | FK(tenant.id), NOT NULL | Tenant ID |
| `created_at` | TIMESTAMP | DEFAULT now() | Snapshot timestamp |
| `bronze_row_count` | BIGINT | NOT NULL | Bronze layer rows |
| `silver_row_count` | BIGINT | NOT NULL | Silver layer rows |
| `gold_row_count` | BIGINT | NOT NULL | Gold layer rows |
| `dbt_manifest_hash` | VARCHAR | NOT NULL | dbt manifest checksum |

**Purpose**: Recommendations and insights reference specific data snapshots for reproducibility.

#### `audit_ledger`

Ingestion audit trail.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | UUID | PK | Primary key |
| `tenant_id` | UUID | FK(tenant.id), NOT NULL | Tenant ID |
| `source_id` | UUID | FK(data_source.id), NULL | Data source |
| `file_name` | VARCHAR | NOT NULL | Ingested file |
| `row_count` | INT | NOT NULL | Rows ingested |
| `bytes` | BIGINT | NOT NULL | File size |
| `checksum` | VARCHAR | NOT NULL | File checksum (SHA-256) |
| `status` | VARCHAR | NOT NULL | success \| failed \| quarantined |
| `error` | TEXT | NULL | Error message (if failed) |
| `ingested_at` | TIMESTAMP | DEFAULT now() | Ingestion timestamp |

---

## Entity Relationships

### Key Relationships

```
tenant (1) ──< (N) app_user
app_user (N) ──< (N) role (via user_role)
app_user (1) ──< (N) refresh_token
app_user (1) ──< (N) api_key

tenant (1) ──< (N) recommendation
recommendation (1) ──< (N) recommendation_feedback
recommendation (1) ──< (N) recommendation_outcome

tenant (1) ──< (N) rca_result
tenant (1) ──< (N) insight
insight (1) ──< (N) insight_feedback

tenant (1) ──< (N) llm_usage
app_user (1) ──< (N) nlq_turn
nlq_turn (1) ──< (N) nlq_feedback

tenant (1) ──< (N) report
report (1) ──< (N) report_schedule

tenant (1) ──< (N) alert_rule
alert_rule (1) ──< (N) alert_trigger

tenant (1) ──< (N) data_source
tenant (1) ──< (N) data_snapshot
data_snapshot (1) ──< (N) recommendation (via data_snapshot_id)
```

### Foreign Key Cascade Rules

| FK | `ondelete` | Rationale |
|----|------------|-----------|
| `app_user.tenant_id → tenant.id` | `RESTRICT` | Cannot delete tenant with users |
| `user_role.user_id → app_user.id` | `CASCADE` | User deletion removes role grants |
| `refresh_token.user_id → app_user.id` | `CASCADE` | User deletion invalidates tokens |
| `recommendation_feedback.recommendation_id → recommendation.id` | `CASCADE` | Recommendation deletion removes feedback |
| `recommendation_outcome.recommendation_id → recommendation.id` | `CASCADE` | Recommendation deletion removes outcomes |
| `insight_feedback.insight_id → insight.id` | `CASCADE` | Insight deletion removes feedback |
| `nlq_feedback.nlq_turn_id → nlq_turn.id` | `CASCADE` | Turn deletion removes feedback |
| `report_schedule.report_id → report.id` | `CASCADE` | Report deletion removes schedules |
| `alert_trigger.alert_rule_id → alert_rule.id` | `CASCADE` | Rule deletion removes trigger history |
| `user_role.granted_by → app_user.id` | `SET NULL` | Granter deletion preserves audit trail |

---

## Indexes & Constraints

### Primary Keys

All tables use UUID primary keys except:
- `role` - SMALLINT (fixed catalog, IDs 1-6)
- `user_role` - Composite PK (user_id, role_id)

### Unique Constraints

| Table | Constraint | Purpose |
|-------|-----------|---------|
| `tenant` | `UNIQUE(slug)` | URL-safe identifier |
| `app_user` | `UNIQUE(tenant_id, email)` | Email unique per tenant |
| `role` | `UNIQUE(key)` | Role key uniqueness |
| `refresh_token` | `UNIQUE(token_hash)` | Token uniqueness |
| `api_key` | `UNIQUE(prefix)` | Prefix uniqueness |
| `recommendation` | `UNIQUE(tenant_id, dedup_key) WHERE status='proposed'` | Partial unique index - structural deduplication |

### Performance Indexes

| Table | Index | Purpose |
|-------|-------|---------|
| `app_user` | `ix_app_user_tenant_id` | Tenant-scoped queries |
| `refresh_token` | `ix_refresh_token_family(user_id, family_id)` | Family-level theft detection |
| `recommendation` | `ix_rec_inbox(tenant_id, status, type, expires_at)` | Inbox hot path |
| `rca_result` | `ix_rca_cache(tenant_id, query_fingerprint, created_at DESC)` | Cache lookup |
| `insight` | `ix_insight_inbox(tenant_id, dismissed_at, severity, detected_at DESC)` | Insight feed |
| `llm_usage` | `ix_llm_usage_billing(tenant_id, created_at DESC)` | Billing queries |
| `nlq_turn` | `ix_nlq_session(user_id, session_id, turn_number)` | Conversation retrieval |

### Check Constraints

| Table | Constraint | Validation |
|-------|-----------|------------|
| `app_user` | `CHECK(status IN ('active', 'suspended', 'deleted'))` | Valid status |
| `role` | `CHECK(key IN ('CEO', 'CFO', ...))` | Valid role key |
| `recommendation` | `CHECK(type IN ('reorder', 'markdown', ...))` | Valid rec type |
| `recommendation` | `CHECK(status IN ('proposed', 'accepted', 'dismissed'))` | Valid status |
| `recommendation` | `CHECK(confidence IN ('HIGH', 'MEDIUM', 'LOW'))` | Valid confidence |

---

## Migrations

### Migration Framework

**Tool**: Alembic (SQLAlchemy's migration tool)

**File**: `backend/app/infrastructure/db/migrations/`

### Migration History

| Migration | Date | Description |
|-----------|------|-------------|
| `202607301200_genesis_schema.py` | 2026-07-30 | Initial schema (Base.metadata.create_all) |
| `202607301500_enterprise_roles.py` | 2026-07-30 | Add 6 enterprise roles |
| `202608061347_recommendation_decisions.py` | 2026-08-06 | Add decision tracking |
| `202608131600_outcome_measurement.py` | 2026-08-13 | Add outcome measurement |
| `202608131800_llm_usage_tracking.py` | 2026-08-13 | Add LLM usage ledger |

### Running Migrations

```bash
# Generate new migration
alembic revision --autogenerate -m "add_new_feature"

# Apply migrations
alembic upgrade head

# Rollback one migration
alembic downgrade -1

# Show current version
alembic current

# Show migration history
alembic history
```

### Genesis Migration Caveat

The initial migration (`202607301200_genesis_schema.py`) uses `Base.metadata.create_all()`, not explicit `op.create_table()` calls. This means there's no column-level history for the initial schema.

**Later migrations** defensively check whether tables exist before adding columns.

---

## Data Retention

### Retention Policies

| Table | Retention | Policy |
|-------|-----------|--------|
| `app_user` | Indefinite | Soft delete (status='deleted') |
| `refresh_token` | 7 days after expiry | Cron job deletes expired tokens |
| `llm_usage` | 90 days | Rolling window for cost analysis |
| `nlq_turn` | 30 days | Conversation history |
| `rca_result` | 7 days | Cache eviction |
| `insight` | Until dismissed + 30 days | Auto-expire stale insights |
| `recommendation` | Until expired + 90 days | Archive old recommendations |
| `audit_ledger` | 1 year | Compliance requirement |

### Cleanup Jobs

**Celery Beat Schedule**:

```python
# backend/app/workers/tasks/cleanup.py
@app.task
def cleanup_expired_tokens():
    """Delete refresh tokens expired >7 days ago."""
    cutoff = datetime.now() - timedelta(days=7)
    db.execute(
        delete(RefreshToken).where(RefreshToken.expires_at < cutoff)
    )

@app.task
def cleanup_llm_usage():
    """Delete LLM usage records >90 days old."""
    cutoff = datetime.now() - timedelta(days=90)
    db.execute(
        delete(LlmUsage).where(LlmUsage.created_at < cutoff)
    )
```

**Schedule**: Daily at 2 AM UTC

---

## Security Model

### Row-Level Security

All tenant-scoped tables include `tenant_id` foreign key. Application enforces row filtering:

```python
# backend/app/infrastructure/db/repositories/base.py
class TenantScopedRepository:
    def __init__(self, session: AsyncSession, tenant_id: UUID):
        self.session = session
        self.tenant_id = tenant_id

    async def list(self, model: Type[Base]):
        """Auto-filter by tenant_id."""
        stmt = select(model).where(model.tenant_id == self.tenant_id)
        result = await self.session.execute(stmt)
        return result.scalars().all()
```

**Critical**: PostgreSQL RLS is NOT enabled. Tenant isolation is application-enforced.

### Password Security

- **Algorithm**: argon2id (OWASP recommended)
- **Memory**: 64 MB
- **Iterations**: 3
- **Parallelism**: 4 threads

**File**: `backend/app/services/auth/password.py`

### Token Security

#### JWT (Access Tokens)

- **Algorithm**: RS256 (asymmetric)
- **TTL**: 15 minutes
- **Payload**: `{user_id, tenant_id, roles, token_version}`

**Invalidation**: Bump `app_user.token_version` on password/role change.

#### Refresh Tokens

- **Storage**: Hashed (SHA-256) in database
- **TTL**: 7 days
- **Rotation**: New token on each refresh
- **Theft Detection**: Reuse of rotated token revokes entire family

#### API Keys

- **Format**: `rmk_live_{random_20_chars}`
- **Storage**: SHA-256 hash in database
- **Prefix**: First 12 chars displayed (`rmk_live_xxxx`)

### SQL Injection Prevention

- **ORM**: SQLAlchemy 2.0 with bound parameters
- **No raw SQL** in application code (except migrations)

**Vulnerable Pattern** (NEVER):
```python
# BAD - SQL injection risk
db.execute(f"SELECT * FROM users WHERE email = '{email}'")
```

**Safe Pattern** (ALWAYS):
```python
# GOOD - Bound parameters
stmt = select(AppUser).where(AppUser.email == email)
```

---

## Backup Strategy

### Backup Configuration

**File**: `infra/scripts/backup.sh`

```bash
#!/bin/bash
# Daily PostgreSQL backup

BACKUP_DIR=/var/backups/postgres
DATE=$(date +%Y%m%d_%H%M%S)

# Full database dump
pg_dump -h postgres -U retailmind -F c -b -v \
  -f "${BACKUP_DIR}/retailmind_${DATE}.dump" \
  retailmind

# Compress
gzip "${BACKUP_DIR}/retailmind_${DATE}.dump"

# Retain 30 days
find "${BACKUP_DIR}" -name "*.dump.gz" -mtime +30 -delete
```

**Schedule**: Daily at 3 AM UTC (Cron)

### Restore Procedure

```bash
# List available backups
ls -lh /var/backups/postgres/*.dump.gz

# Restore from backup
gunzip retailmind_20260815_030000.dump.gz
pg_restore -h postgres -U retailmind -d retailmind_new \
  --clean --if-exists \
  retailmind_20260815_030000.dump
```

**Safety**: Restore to new database (`retailmind_new`), test, then swap.

### Disaster Recovery

**RPO** (Recovery Point Objective): 24 hours (daily backups)
**RTO** (Recovery Time Objective): 4 hours (manual restore + validation)

**Future Improvement**: Point-in-time recovery with WAL archiving.

---

## Appendix

### File Reference

| File | Purpose |
|------|---------|
| `backend/app/infrastructure/db/models/auth.py` | Identity & tenancy models |
| `backend/app/infrastructure/db/models/recommendations.py` | Decision intelligence models |
| `backend/app/infrastructure/db/models/ai.py` | LLM tracking, NLQ, RCA models |
| `backend/app/infrastructure/db/models/reports.py` | Reporting & dashboards |
| `backend/app/infrastructure/db/models/alerts.py` | Alerts & notifications |
| `backend/app/infrastructure/db/models/platform.py` | Data platform metadata |
| `backend/app/infrastructure/db/session.py` | Database session management |
| `backend/app/infrastructure/db/migrations/` | Alembic migrations |

### SQL Schema Dump

Generate full schema:

```bash
pg_dump -h postgres -U retailmind -d retailmind --schema-only \
  > docs/database/schema.sql
```

### ERD Generation

Generate ERD with SchemaSpy:

```bash
docker run --rm -v "$PWD:/output" \
  schemaspy/schemaspy:latest \
  -t pgsql -host postgres -port 5432 -db retailmind \
  -u retailmind -p <password> \
  -o /output/docs/database/erd
```

---

**Maintained by**: RetailMind AI Contributors
**License**: MIT
**Last Reviewed**: 2026-08-15
