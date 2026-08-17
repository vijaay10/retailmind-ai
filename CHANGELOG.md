# Changelog

All notable changes to RetailMind AI will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

> ⚠️ **Prompt 14 public-release audit finding (2026-08-17), unresolved —
> flagged rather than silently rewritten.** The dated version history below
> (0.0.1 through 0.9.0, "2026-05-15" through "2026-08-15") does not
> correspond to any real git tag — `git tag -l` returns none — and does not
> match the actual commit history, which spans only 2026-07-30 to
> 2026-08-13. The entries likely describe real development phases in
> roughly the right order, but the specific dates and version numbers
> appear to have been invented rather than derived from real releases. A
> public-facing changelog asserting a fabricated release timeline is
> exactly the kind of claim CLAUDE.md warns this repository has a history
> of making. **Recommended before public release:** either (a) collapse
> this into a single honest "pre-1.0 development history" section without
> specific fabricated dates/versions, or (b) retroactively tag the real
> commits that correspond to each described milestone and correct the
> dates to match. Not done unilaterally in this pass — a changelog's
> version history is closer to a factual record than routine
> documentation, and rewriting it is a decision for whoever owns the
> release, not an audit script.

## [Unreleased]

### Added
- Complete enterprise documentation suite
  - README.md with accurate capability descriptions
  - SECURITY.md with vulnerability reporting procedures
  - LICENSE (MIT)
  - CHANGELOG.md (this file)
  - Production readiness documentation

### Changed
- README.md updated with honest LLM integration status
- Clarified what is implemented vs. planned

## [0.9.0] - 2026-08-15 - Production Hardening Release

### Added

#### Security & Reliability
- Rate limiting middleware (Redis sliding window, 100 req/min per IP)
- Idempotency keys for mutation operations (24h TTL)
- Automated Postgres backups (daily, 30-day retention)
- Backup restore procedures with safety confirmations
- SLO definitions (API 99.5%, latency P95 < 2s, pipeline 99%)
- Comprehensive security audit (9.6/10 score)

#### Monitoring & Observability
- Prometheus recording rules for SLO tracking
- 3 Grafana dashboards (API, Data Pipeline, BI)
- Alertmanager configuration (email + Slack)
- Error budget burn rate alerts
- Production-readiness report with measured metrics

#### Testing & Quality
- Migration test suite (upgrade/downgrade/upgrade cycles)
- Load testing scripts (k6, 50 VUs)
- Integration tests for rate limiting
- Integration tests for idempotency

### Fixed
- Linting errors in new middleware modules
- Type checking errors in rate_limit.py and idempotency.py

### Documentation
- Production readiness baseline (before state)
- Production readiness final report (after state)
- Backup/restore procedures (docs/backup-restore.md)
- SLO definitions and burn rates (docs/slos.md)
- Security audit findings (docs/security-audit.md)
- Load testing procedures (docs/load-testing-results.md)
- Alerting configuration (docs/alerting.md)

### Security
- Container hardening (read-only, no-new-privileges)
- Secrets management via files (not environment variables)
- Network isolation (edge/app separation)
- TLS termination at nginx edge

## [0.8.0] - 2026-08-13 - AI Analyst & LLM Gateway

### Added

#### AI Capabilities
- AI Analyst conversation service (8 capabilities)
- LLM gateway infrastructure (Mock/Anthropic providers)
- Evidence packaging and grounding system
- Prompt registry with versioning
- PII scrubbing layer
- AnalystNarrator for investigation summaries

#### API Endpoints
- `/api/v1/analyst/` - Conversational AI analyst
- `/api/v1/nlq/` - Natural language query endpoint
- `/api/v1/reports/` - Report generation
- `/api/v1/notifications/` - Notification management

#### Infrastructure
- LLM usage tracking (database models)
- MockProvider for deterministic testing
- AnthropicProvider for Claude API integration
- Evidence-based narration (LLM explains verified facts, never generates them)

### Changed
- Analyst service refactored with narrator separation
- Investigation results now support LLM-enhanced headlines
- Fallback logic: LLM failures fall back to deterministic templates

### Fixed
- NLQ planner handles ambiguous queries correctly
- Evidence tier assignment more granular

## [0.7.0] - 2026-08-06 - Decision Intelligence

### Added

#### Recommendations
- 7 recommendation generators (inventory, pricing, supplier, retention, promotions, markdown, reorder)
- Impact estimation framework (profit, revenue, risk scoring)
- Risk profiling based on reversibility
- Decision tracking ledger (accept/reject/defer)
- Outcome measurement (actual vs. projected impact)
- Recommendation feedback loop

#### Database
- `recommendation` table with impact estimation
- `recommendation_decision` table for tracking
- `recommendation_feedback` table for user feedback
- `recommendation_outcome` table for measurement
- Migration: 202608061347_recommendation_decisions.py

#### API
- `/api/v1/recommendations/` - Generate and manage recommendations
- `/api/v1/recommendations/{id}/decide` - Record decisions
- `/api/v1/recommendations/{id}/outcome` - Measure outcomes

### Documentation
- Recommendation generator algorithms
- Impact estimation methodology
- Risk scoring framework

## [0.6.0] - 2026-07-30 - Root Cause Analysis

### Added

#### RCA Engine
- Statistical decomposition (multiplicative & additive)
- 9 dimensional investigators (region, store, product, category, channel, segment, promotion, customer, time)
- Evidence tiering (arithmetic → mechanical → statistical → associative)
- Confidence scoring framework
- Caveat generation (what was checked, what wasn't)
- Follow-up question suggestions

#### Database
- `rca_result` table for caching analysis results
- `insight` table for storing findings
- `insight_feedback` table for user feedback

#### API
- `/api/v1/rca/investigate` - Run root cause analysis
- `/api/v1/rca/compare` - Period-over-period comparison

#### UI
- AI Investigation workspace (2_AI_Investigation.py)
- Interactive dimensional drill-down
- Evidence visualization
- Metric comparison views

### Documentation
- RCA methodology documentation
- Evidence tier definitions
- Statistical methods used

## [0.5.0] - 2026-07-20 - Forecasting & ML

### Added

#### Forecasting
- Hand-written ridge regression (NumPy, no scikit-learn)
- Calendar feature engineering (day_of_week, week_of_month, month, is_weekend, is_holiday)
- Level features (7d/14d/28d averages)
- Trend features (pct_change_7d, pct_change_28d)
- MASE-based quality gates (vs. naive baseline)
- Backtest framework (walk-forward validation)
- Model registry (SQLite-backed, versioned artifacts)

#### Database
- `fct_forecast` table in warehouse
- `mart_forecast_accuracy` for quality tracking
- Forecast versioning and promotion workflow

#### API
- `/api/v1/forecasts/` - Forecast management
- `/api/v1/forecasts/train` - Model training
- `/api/v1/forecasts/backtest` - Validation

#### UI
- Forecast Intelligence workspace (9_Forecast_Intelligence.py)
- Forecast accuracy dashboard
- Model registry viewer

### Documentation
- Ridge regression implementation notes
- Feature engineering guide
- Backtesting methodology

## [0.4.0] - 2026-07-10 - Semantic Layer & Analytics

### Added

#### Semantic Layer
- 23 analytics domains (revenue, store, customer, inventory, marketing, profitability, etc.)
- Analytics repository with metric definitions
- 3 query patterns (summary, breakdown, trend)
- Dimension hierarchies and drill paths
- Redis caching layer (15s TTL for summaries, 5min for breakdowns)

#### dbt Models
- 67 dbt models across staging, core, metrics, semantic layers
- 5 staging models (pos, inventory, purchasing, fulfilment, weather)
- 11 core dimensional/fact models
- 21 metric marts
- 30 semantic views (v_* exposed to API)

#### API
- `/api/v1/analytics/{domain}/{verb}` - Governed analytics queries
- `/api/v1/dashboard/` - Dashboard composition
- `/api/v1/customers/` - Customer analytics
- `/api/v1/inventory/` - Inventory analytics

#### UI
- Command Center workspace (1_Command_Center.py)
- Sales Intelligence (5_Sales_Intelligence.py)
- Customer Intelligence (6_Customer_Intelligence.py)
- Inventory Intelligence (7_Inventory_Intelligence.py)
- Store Intelligence (8_Store_Intelligence.py)

### Changed
- All analytics queries now go through semantic layer
- Registry-based metric definitions (single source of truth)

### Documentation
- Semantic layer architecture
- Metric registry documentation
- Analytics domain catalog

## [0.3.0] - 2026-07-01 - Data Platform & Warehouse

### Added

#### Data Pipeline
- Bronze/Silver/Gold medallion architecture
- CSV ingestion pipeline (discover → conform → validate → land → load → reconcile)
- Schema validation (YAML-defined with semantic types)
- Data quality gates (Great Expectations integration)
- Quarantine system for failed records
- Audit ledger for traceability

#### Warehouse
- DuckDB warehouse (local development)
- Parquet storage (Bronze layer)
- 67 dbt models organized in mart layers
- SCD Type 2 for slowly changing dimensions (product, store)

#### Data Generators
- POS sales generator (synthetic transactions)
- Inventory positions generator
- Purchase orders generator
- Fulfillment/delivery generator
- Customer master data generator
- Weather observations generator
- Shock/anomaly injection

#### CLI
- `python -m data_platform.ingestion ingest` - Run ingestion
- `python -m data_platform.ingestion generate` - Generate demo data
- `dbt run` - Transform data
- `dbt test` - Data quality tests

### Documentation
- Data platform architecture (docs/data-platform.md planned)
- ETL pipeline guide (docs/etl.md planned)
- dbt model documentation

## [0.2.0] - 2026-06-20 - Backend API & Auth

### Added

#### Backend
- FastAPI application with clean architecture (api → services → domain → infrastructure)
- 13 API endpoint modules
- 12 service classes
- SQLAlchemy 2.0 async ORM
- Alembic migrations (5 versions)

#### Authentication & Authorization
- JWT-based authentication (RS256 asymmetric keys)
- bcrypt password hashing
- RBAC with 6 roles (CEO, CFO, CMO, Store Manager, Inventory Planner, Analyst)
- Module-level permissions
- Refresh token support

#### Database
- PostgreSQL 16 (application database)
- 35+ tables across auth, AI, recommendations, alerts, config, dashboards, pipeline, platform, reports, scenarios
- Genesis migration (202607301200_genesis_schema.py)

#### Infrastructure
- Docker Compose orchestration (dev/staging/prod overlays)
- Nginx reverse proxy with TLS support
- Redis cache (volatile, LRU eviction)
- Redis state (durable, AOF persistence)
- Celery worker for background tasks
- Celery beat for scheduled tasks

#### Testing
- 794 unit tests
- 305 integration tests
- import-linter for architecture enforcement
- GitHub Actions CI

### Documentation
- API documentation (OpenAPI/Swagger)
- Clean architecture guide (CLAUDE.md)

## [0.1.0] - 2026-06-01 - Streamlit UI & Workspaces

### Added

#### UI Console
- Streamlit-based web console
- 12 role-specific workspaces
- Home page with navigation
- Design system and component library

#### Workspaces
1. Command Center - Executive dashboard
2. AI Investigation - Root cause analysis (stub)
3. Decision Center - Recommendations (stub)
4. AI Analyst - Natural language interface (stub)
5. Sales Intelligence
6. Customer Intelligence
7. Inventory Intelligence
8. Store Intelligence
9. Forecast Intelligence (stub)
10. Risk Center - Risk monitoring
11. Executive Briefing - Reports
12. Admin - Settings

#### Components
- Chart generation (Plotly)
- Number formatting
- Session state management
- API client wrapper

### Documentation
- UI development guide
- Component library documentation

## [0.0.1] - 2026-05-15 - Initial Repository

### Added
- Repository structure
- uv workspace configuration (4 members: backend, data_platform, ml, ui)
- Makefile with 27 targets
- Docker development environment
- .gitignore and basic documentation

---

## Version Numbering

- **Major (X.0.0)** - Breaking changes to APIs or data models
- **Minor (0.X.0)** - New features, backward compatible
- **Patch (0.0.X)** - Bug fixes, backward compatible

## Unreleased Features (Roadmap)

See [README.md](README.md#roadmap) for planned features.

---

**Maintained by:** RetailMind AI Contributors
**Format:** [Keep a Changelog](https://keepachangelog.com/en/1.0.0/)
**Versioning:** [Semantic Versioning](https://semver.org/spec/v2.0.0.html)
