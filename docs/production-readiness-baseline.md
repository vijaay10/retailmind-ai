# Production Readiness Baseline

**Date:** 2026-08-14
**Purpose:** Document current state before production hardening work

## Current State Summary

### ✅ Implemented and Working

1. **Database Schema**
   - Alembic migrations configured
   - 5 migration files exist
   - Genesis migration creates all tables, views, triggers, functions
   - Downgrade capability exists

2. **Monitoring Infrastructure**
   - Prometheus configured and scraping API metrics
   - 4 alert rules defined (ApiDown, HighErrorRate, SlowRequests, NoDetectionSweep)
   - Grafana container configured in compose.prod.yml
   - Prometheus datasource provisioned

3. **API Pagination**
   - Implemented in 8 endpoints with `limit` parameter (ge=1, le=100)
   - Examples: /recommendations, /inventory, /customers, /analytics, /notifications, /forecasts, /dashboard, /rca

4. **Security Basics**
   - Authentication middleware implemented
   - JWT token signing
   - CORS middleware
   - Security headers middleware
   - Secrets via Docker secrets (not environment variables)
   - Read-only containers with tmpfs
   - no-new-privileges security opt

5. **Container Security**
   - Production containers run read-only
   - Secrets mounted as files, not environment variables
   - Log rotation configured (json-file driver with max-size/max-file)
   - Resource limits defined for all services
   - TLS/HTTPS support with certbot integration

### ⚠️ Partially Implemented

1. **Database Migrations**
   - **Issue:** Genesis migration uses `Base.metadata.create_all()` at line 221
   - **Risk:** Not replayable at column level, cannot track individual column history
   - **Current Downgrade:** Uses `drop_all()` - destroys everything, not column-by-column
   - **Missing:** Upgrade → downgrade → upgrade tests

2. **Idempotency**
   - **Found:** Mentions in 5 files (workers, notifications, auth tests)
   - **Missing:** No idempotency keys on API mutations
   - **Missing:** No idempotency middleware

3. **Monitoring Metrics**
   - **Exists:** API metrics (http_requests_total, http_request_duration_seconds)
   - **Missing:** ETL failure metrics
   - **Missing:** ETL freshness metrics
   - **Missing:** Database health metrics
   - **Missing:** Worker failure metrics
   - **Missing:** Recommendation job metrics
   - **Missing:** Outcome measurement job metrics

### ❌ Not Implemented

1. **Backups**
   - No automated Postgres backups
   - No backup scripts
   - No retention policy
   - No RPO/RTO defined
   - No restore verification

2. **Alert Routing**
   - Alerts defined but route nowhere
   - No Alertmanager configured
   - From CLAUDE.md: "4 Prometheus alert rules that evaluate and notify nobody"

3. **Grafana Dashboards**
   - Grafana container exists
   - Datasource provisioned
   - **Zero dashboards** - directory empty except for datasource config
   - No API dashboard
   - No Data Pipeline dashboard
   - No Business Intelligence dashboard

4. **SLOs**
   - No SLO definitions
   - No recording rules
   - No SLO dashboards
   - No error budgets

5. **Rate Limiting**
   - No rate limiting middleware
   - No rate limits on API endpoints
   - Anthropic provider has rate limiting but not API-level protection

6. **Load Testing**
   - No load tests
   - No performance baselines
   - No k6/locust/ab scripts

7. **Restore Testing**
   - No restore test procedures
   - No documented restore process
   - No RTO measurements

8. **Error Reporting**
   - Structured logging exists
   - No centralized error tracking (Sentry, Rollbar, etc.)
   - No error rate dashboards beyond Prometheus

## Known Issues (From CLAUDE.md)

1. **Multiple uvicorn workers mint different JWT signing keys**
   - With no key configured, app generates ephemeral RSA pair per process
   - Tokens from one worker rejected by another
   - Production enforces configured key (this is OK)

2. **Genesis migration uses create_all**
   - No replayable column-level history
   - Later migrations defensively check if table exists

3. **No backups, no Alertmanager, no Grafana dashboards**
   - Explicitly documented as missing

4. **Prometheus alerts notify nobody**
   - 4 alert rules evaluate but have no receivers

## Metrics Before Hardening

### Test Suite
- Unit tests: 794 passing
- Integration tests: 305 passing
- Total: 1,099 tests passing
- Linting: Passing (ruff + mypy)

### Production Configuration
- Services: 11 (edge, api, ui, worker, beat, postgres, redis×2, minio, prometheus, grafana)
- Networks: 2 (edge, app)
- Volumes: 8 (postgres_data, redis_data, minio_data, uploads, certbot×2, prometheus_data, grafana_data)
- Secrets: 6 (db_password, jwt_private_key, smtp_password, minio×2, grafana_password)

### Security Posture
- Authentication: ✅ Implemented
- Authorization: ✅ Role-based permissions
- Rate Limiting: ❌ Not implemented
- Idempotency: ⚠️ Partial (workers only)
- Input Validation: ✅ Pydantic schemas
- SQL Injection Protection: ✅ SQLAlchemy ORM
- XSS Protection: ✅ Security headers
- CSRF Protection: ⚠️ Streamlit only
- Secrets Management: ✅ Docker secrets
- Container Security: ✅ Read-only, no-new-privileges
- Dependency Scanning: ❌ Not automated
- PII Handling: ❓ Not audited

### Operational Capabilities
- Metrics Collection: ✅ Prometheus
- Alerting: ⚠️ Rules exist, no routing
- Dashboards: ❌ None
- Backups: ❌ None
- Restore: ❌ Not tested
- Load Testing: ❌ None
- SLOs: ❌ Not defined
- Incident Response: ❌ No runbook

## Production Readiness Score: 4/10

**Rationale:**
- ✅ Core functionality works
- ✅ Security basics in place
- ✅ Metrics collection configured
- ❌ No backups (critical gap)
- ❌ No alert routing (critical gap)
- ❌ No dashboards (operational blindness)
- ❌ No SLOs (no reliability targets)
- ❌ No load testing (unknown capacity)
- ❌ No restore testing (unknown RTO)

## Priority Order for Hardening

1. **CRITICAL** - Postgres backups and restore testing
2. **CRITICAL** - Alert routing (Alertmanager)
3. **HIGH** - Grafana dashboards (API, Pipeline, BI)
4. **HIGH** - Rate limiting middleware
5. **HIGH** - Load testing and capacity planning
6. **MEDIUM** - SLO definitions and recording rules
7. **MEDIUM** - Idempotency keys for API mutations
8. **MEDIUM** - Migration replay testing
9. **LOW** - Security audit and PII handling review
10. **LOW** - Dependency scanning automation

## Next Steps

Work proceeds in priority order:
1. Implement Postgres backups (pg_dump automation, retention, restore docs)
2. Configure Alertmanager (email/Slack routing, alert grouping)
3. Create Grafana dashboards (API golden signals, pipeline health, BI metrics)
4. Add rate limiting middleware (per-IP, per-user, per-endpoint)
5. Build load tests (k6 script, 50 VUs, top endpoints)
6. Define SLOs (availability, latency, freshness, success rate)
7. Add idempotency keys (request deduplication)
8. Fix migration testing (upgrade/downgrade/upgrade)
9. Security audit (PII, secrets, containers, dependencies)
10. Provide final production-readiness report

---

**Baseline established:** 2026-08-14
**Hardening begins:** 2026-08-14
**Target completion:** TBD (no timelines, focus on completion)
