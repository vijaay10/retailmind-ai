# Operations Guide

RetailMind AI operations - monitoring, alerting, backup/restore, troubleshooting, and runbook procedures.

**Last Updated**: 2026-08-15
**Version**: 0.9.0

---

## Table of Contents

- [Monitoring](#monitoring)
- [Alerting](#alerting)
- [Backup & Restore](#backup--restore)
- [Log Management](#log-management)
- [Health Checks](#health-checks)
- [Troubleshooting](#troubleshooting)
- [Runbooks](#runbooks)
- [Maintenance Procedures](#maintenance-procedures)

---

## Monitoring

### Prometheus Metrics

**Endpoint**: `http://localhost:8000/metrics`

**Instrumentation Location**: `backend/app/infrastructure/monitoring/prometheus.py`

### Available Metrics

#### HTTP Metrics

```
http_requests_total{method, endpoint, status}
  Counter: Total HTTP requests by endpoint and status code

http_request_duration_seconds{endpoint}
  Histogram: Request latency distribution by endpoint
  Buckets: [0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10]

http_requests_in_progress{method, endpoint}
  Gauge: Active requests currently being processed
```

#### Cache Metrics

```
cache_requests_total{domain, status}
  Counter: Cache hits/misses by analytics domain
  Labels: status=hit|miss

cache_evictions_total{reason}
  Counter: Cache evictions by reason (ttl_expired, size_limit, manual)
```

#### LLM Metrics

```
llm_requests_total{provider, status}
  Counter: LLM API calls by provider and outcome
  Labels: provider=mock|anthropic, status=success|error|timeout

llm_request_duration_seconds{provider}
  Histogram: LLM request latency by provider
```

#### Business Metrics

```
retailmind_last_sweep_timestamp_seconds
  Gauge: Unix timestamp of last completed detection sweep
  Purpose: Alert if sweep stops (silence looks like success)
```

### Grafana Dashboards

**Location**: `infra/monitoring/grafana/provisioning/dashboards/`

#### 1. API Dashboard (`api-dashboard.json`)

**Panels**:
- Request rate (requests/second) by endpoint
- Request latency (p50, p95, p99)
- Error rate (4xx vs 5xx)
- Top endpoints by volume
- Status code breakdown

**Purpose**: API health, performance regression detection

#### 2. Data Pipeline Dashboard (`pipeline-dashboard.json`)

**Panels**:
- Ingestion row counts by source
- Quality gate failures by rule
- dbt model execution time
- Bronze/Silver/Gold data freshness
- Reconciliation variance

**Purpose**: Data quality monitoring, pipeline performance

#### 3. BI Dashboard (`bi-dashboard.json`)

**Panels**:
- Cache hit rate by domain
- Query latency by domain
- Top cached queries
- Analytics request volume
- RBAC permission denials

**Purpose**: Semantic layer performance, access control audit

### Accessing Grafana

**Dev/Staging**:
```bash
# Start monitoring stack
docker compose -f infra/compose/compose.yml \
               -f infra/compose/compose.dev.yml \
               up -d grafana prometheus

# Access Grafana
open http://localhost:3000

# Default credentials
# User: admin
# Pass: admin (change on first login)
```

**Production**:
```bash
# Use secret-managed credentials
GRAFANA_PASSWORD=$(cat /run/secrets/grafana_password)
```

---

## Alerting

### Alert Philosophy

> "An alert that fires on something nobody acts on trains people to close the tab, and after that the one that mattered is closed too."
> — `infra/monitoring/alerts.yml`

**Only 4 alerts** (deliberately few):
1. ApiDown
2. HighErrorRate
3. SlowRequests
4. NoDetectionSweep

Each alert **names an action** in its annotation. A rule that cannot specify an action is not an alert—it's a dashboard panel.

### Alert Definitions

**File**: `infra/monitoring/alerts.yml`

#### 1. ApiDown

**Trigger**: API has not responded to Prometheus scrape for 2 minutes

**Severity**: Critical

**Expression**:
```promql
up{job="api"} == 0
```

**Action**:
> "Check `docker compose ps` and the api container's logs. If the container is up but unscrapeable, the process is wedged—restart it."

**Remediation**:
```bash
docker compose ps backend-api
docker compose logs backend-api --tail=100
docker compose restart backend-api
```

#### 2. HighErrorRate

**Trigger**: >5% of requests failing with 5xx for 5 minutes

**Severity**: Critical

**Expression**:
```promql
sum(rate(http_requests_total{status=~"5.."}[5m]))
  / sum(rate(http_requests_total[5m])) > 0.05
```

**Note**: Only 5xx errors. A wall of 403s means someone is probing, not that the platform is broken.

**Action**:
> "Read the most recent error-level log lines; they carry the request_id and the endpoint."

**Remediation**:
```bash
docker compose logs backend-api --tail=200 | grep ERROR
# Correlate request_id to find root cause
```

#### 3. SlowRequests

**Trigger**: p95 request latency >5 seconds for 10 minutes

**Severity**: Warning

**Expression**:
```promql
histogram_quantile(0.95, sum(rate(http_request_duration_seconds_bucket[5m])) by (le)) > 5
```

**Action**:
> "An investigation sweeps nine dimensions and a report renders three documents; both are legitimately slow. Check whether the slowness is on those endpoints before treating it as a regression."

**Expected Slow Endpoints**:
- `/api/v1/rca/investigate` - Sweeps 9 dimensional investigators (~3-8 seconds)
- `/api/v1/analyst/` - May trigger investigations + LLM narration (~2-5 seconds)
- `/api/v1/reports/generate` - Renders multiple report sections (~4-10 seconds)

**Remediation**:
```bash
# Check slow queries
grep "request_duration_seconds" backend-api-logs.txt | awk '{print $NF}' | sort -n
```

#### 4. NoDetectionSweep

**Trigger**: No detection sweep completed in 2 hours

**Severity**: Critical

**Expression**:
```promql
time() - max(retailmind_last_sweep_timestamp_seconds) > 7200
```

**Why Critical**:
> "The sweep is what turns a condition into an alert. If it stops, the platform goes quiet—which looks exactly like nothing being wrong. Silence here is indistinguishable from good news, which is why it pages."

**Action**:
> "Check the beat and worker containers."

**Remediation**:
```bash
docker compose ps celery-beat celery-worker
docker compose logs celery-beat --tail=100
docker compose logs celery-worker --tail=100
docker compose restart celery-beat celery-worker
```

### Alertmanager Configuration

**File**: `infra/monitoring/alertmanager.yml`

**Current Status**: Alerts evaluate but **notify nobody**.

**Reason**: No Slack webhook, PagerDuty key, or email SMTP configured.

**To Enable Notifications**:

1. Configure receiver (example: Slack)
```yaml
receivers:
  - name: 'slack'
    slack_configs:
      - api_url_file: /run/secrets/slack_webhook
        channel: '#retailmind-alerts'
        title: '{{ .GroupLabels.alertname }}'
        text: '{{ range .Alerts }}{{ .Annotations.summary }}{{ end }}'
```

2. Set secret:
```bash
echo "https://hooks.slack.com/services/YOUR/WEBHOOK/URL" > \
  infra/secrets/slack_webhook
chmod 600 infra/secrets/slack_webhook
```

3. Restart Alertmanager:
```bash
docker compose restart alertmanager
```

---

## Backup & Restore

### Automated Backups

**Script**: `scripts/backup-postgres.sh`

**Schedule**: Daily at 3:00 AM (via cron)

**Retention**: 30 days (configurable via `RETENTION_DAYS`)

**Location**: `./backups/` (default) or `BACKUP_DIR`

#### Backup Process

```bash
# Manual backup
./scripts/backup-postgres.sh

# With custom location
./scripts/backup-postgres.sh /mnt/backups

# Environment variables
export RM_DB_NAME=retailmind
export RM_DB_USER=retailmind
export PGPASSWORD=<password>
export RETENTION_DAYS=60
./scripts/backup-postgres.sh
```

**Output**:
```
Starting backup: ./backups/retailmind_retailmind_20260815_030000.sql.gz
Database: retailmind@postgres/retailmind
Backup complete: ./backups/retailmind_retailmind_20260815_030000.sql.gz (12M)
No expired backups to delete (retention: 30 days)
Backup verification: OK (valid gzip, contains SQL dump)

Backup Summary
──────────────
Latest backup:  ./backups/retailmind_retailmind_20260815_030000.sql.gz
Size:           12M
Total backups:  30
Total size:     360M
Retention:      30 days
```

#### Backup Features

1. **Compression**: gzip compression (~10:1 ratio)
2. **Rotation**: Automatic deletion of backups older than retention period
3. **Verification**: Validates gzip integrity and SQL content
4. **Atomicity**: Uses `pg_dump --clean --if-exists --create`
5. **Portability**: `--no-owner --no-acl` for environment independence

#### Cron Schedule (Production)

```bash
# Add to crontab
0 3 * * * /opt/retailmind/scripts/backup-postgres.sh /var/backups/postgres >> /var/log/retailmind/backup.log 2>&1
```

### Restore Process

**Script**: `scripts/restore-postgres.sh`

**⚠️ DESTRUCTIVE OPERATION** - Drops and recreates the database.

#### Restore Procedure

```bash
# List available backups
ls -lh ./backups/

# Restore from backup (with confirmation prompt)
./scripts/restore-postgres.sh ./backups/retailmind_retailmind_20260814_030000.sql.gz

# Review confirmation prompt:
# ──────────────────────────────────────────────────
# RESTORE DATABASE FROM BACKUP
# ──────────────────────────────────────────────────
#
# This operation will:
#   1. DROP the existing database: retailmind
#   2. CREATE a new database from backup
#   3. RESTORE all data from: retailmind_retailmind_20260814_030000.sql.gz
#
# Target:
#   Host:     postgres
#   Database: retailmind
#   User:     retailmind
#
# Backup:
#   File: ./backups/retailmind_retailmind_20260814_030000.sql.gz
#   Size: 12M
#   Date: 2026-08-14 03:00:00
#
# ──────────────────────────────────────────────────
# WARNING: THIS WILL DELETE ALL CURRENT DATA
# ──────────────────────────────────────────────────
#
# Type 'yes' to proceed: yes

Starting restore from: ./backups/retailmind_retailmind_20260814_030000.sql.gz
Restore complete.

Verification
────────────
Tables restored: 37
Alembic revision: 5d8f2a1b3c4e

Restore successful. Database: retailmind @ postgres

Next steps:
  1. Verify application can connect
  2. Run smoke tests
  3. Check critical tables have expected row counts
```

#### Skip Confirmation (Automated Restore)

```bash
# DANGEROUS - only for non-production automation
SKIP_CONFIRM=true ./scripts/restore-postgres.sh <backup_file>
```

#### Restore Verification

**Smoke Tests**:
```bash
# 1. Table count
docker compose exec postgres psql -U retailmind -d retailmind \
  -c "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='public'"

# 2. Alembic migration version
docker compose exec backend-api alembic current

# 3. Row counts
docker compose exec postgres psql -U retailmind -d retailmind \
  -c "SELECT 'fct_sales', COUNT(*) FROM fct_sales UNION ALL
      SELECT 'dim_customer', COUNT(*) FROM dim_customer"
```

---

## Log Management

### Structured Logging

**Format**: JSON (production) or pretty-printed (dev)

**Fields**:
- `timestamp` - ISO 8601 UTC
- `level` - DEBUG, INFO, WARNING, ERROR, CRITICAL
- `logger` - Module path (e.g., `app.services.analytics.service`)
- `message` - Human-readable message
- `request_id` - Request correlation ID (UUID)
- `user_id` - Authenticated user (if applicable)
- `duration_ms` - Request duration (on completion)
- `endpoint` - API route
- `status_code` - HTTP status
- `error` - Exception details (if error)

**Example**:
```json
{
  "timestamp": "2026-08-15T14:32:10.123456Z",
  "level": "INFO",
  "logger": "app.api.routes.analytics",
  "message": "analytics query completed",
  "request_id": "a3f2b8c9-4d5e-6f7a-8b9c-0d1e2f3a4b5c",
  "user_id": "550e8400-e29b-41d4-a716-446655440000",
  "endpoint": "/api/v1/analytics/revenue/summary",
  "status_code": 200,
  "duration_ms": 127
}
```

### Log Levels

**DEBUG**: Variable dumps, SQL queries (dev only)
**INFO**: Request completed, cache hit/miss, LLM call
**WARNING**: Rate limit exceeded, fallback activated, deprecated endpoint
**ERROR**: Validation failed, database error, external API error
**CRITICAL**: Service unavailable, configuration error, unrecoverable failure

### Viewing Logs

```bash
# All services
docker compose logs -f --tail=100

# Specific service
docker compose logs -f backend-api --tail=100

# Grep for errors
docker compose logs backend-api | grep ERROR

# Grep by request_id
docker compose logs backend-api | grep "a3f2b8c9-4d5e-6f7a-8b9c-0d1e2f3a4b5c"

# JSON parsing with jq
docker compose logs --no-log-prefix backend-api | jq 'select(.level == "ERROR")'
```

### Log Rotation

**Docker default**: JSON file driver with rotation

**Production Configuration**:
```yaml
# compose.prod.yml
services:
  backend-api:
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "3"
```

**Total retention**: 30MB per service (10MB × 3 files)

---

## Health Checks

### Endpoints

#### 1. Overall Health

```bash
GET /health
```

**Response** (healthy):
```json
{
  "status": "healthy",
  "timestamp": "2026-08-15T14:32:10Z",
  "version": "0.9.0"
}
```

#### 2. Database Health

```bash
GET /health/db
```

**Response**:
```json
{
  "status": "healthy",
  "latency_ms": 2.3,
  "pool_size": 5,
  "pool_available": 4
}
```

#### 3. Cache Health

```bash
GET /health/cache
```

**Response**:
```json
{
  "status": "healthy",
  "redis_cache": "connected",
  "redis_state": "connected"
}
```

#### 4. LLM Health (if enabled)

```bash
GET /health/llm
```

**Response**:
```json
{
  "status": "healthy",
  "provider": "anthropic",
  "api_reachable": true
}
```

**Or (mock mode)**:
```json
{
  "status": "healthy",
  "provider": "mock",
  "note": "Using deterministic mock, no external API calls"
}
```

### Docker Health Checks

**File**: `infra/compose/compose.yml`

**API Health Check**:
```yaml
services:
  backend-api:
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 40s
```

**Database Health Check**:
```yaml
services:
  postgres:
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U retailmind"]
      interval: 10s
      timeout: 5s
      retries: 5
```

**Viewing Health Status**:
```bash
docker compose ps
# Shows "healthy" or "unhealthy" per service
```

---

## Troubleshooting

### Common Issues

#### 1. "JWT verification failed"

**Symptom**: 401 Unauthorized with message "Invalid token signature"

**Cause**: Multiple uvicorn workers with ephemeral keys (each worker generates different RSA keys)

**Diagnosis**:
```bash
# Check number of workers
docker compose exec backend-api ps aux | grep uvicorn

# Check if JWT key is configured
docker compose exec backend-api ls -l /run/secrets/jwt_private_key
```

**Fix** (development):
```bash
# Use single worker
# In compose.dev.yml:
command: uvicorn app.main:app --host 0.0.0.0 --workers 1 --reload
```

**Fix** (production):
```bash
# Configure persistent JWT key (see SECURITY.md)
./scripts/generate-jwt-keys.sh
# Then mount in compose.prod.yml
```

#### 2. "Database connection failed"

**Symptom**: 500 Internal Server Error, logs show "could not connect to server"

**Diagnosis**:
```bash
# Check if PostgreSQL is running
docker compose ps postgres

# Check PostgreSQL logs
docker compose logs postgres --tail=100

# Test connection from API container
docker compose exec backend-api pg_isready -h postgres -U retailmind

# Test with psql
docker compose exec postgres psql -U retailmind -d retailmind -c "SELECT 1"
```

**Fix**:
```bash
# If PostgreSQL is down
docker compose start postgres

# If password mismatch
# Check .env or secrets/db_password match PostgreSQL

# If connection pool exhausted
# Increase pool size in backend/app/core/config.py:
# DATABASE_POOL_SIZE=20  # default: 5
```

#### 3. "Rate limit exceeded"

**Symptom**: 429 Too Many Requests

**Diagnosis**:
```bash
# Check Redis connection
docker compose exec redis-cache redis-cli ping
# Expected: PONG

# View rate limit keys
docker compose exec redis-cache redis-cli --scan --pattern "rate_limit:*"

# Check specific IP
docker compose exec redis-cache redis-cli GET "rate_limit:ip:192.168.1.100"
```

**Fix** (temporary):
```bash
# Clear rate limits for an IP
docker compose exec redis-cache redis-cli DEL "rate_limit:ip:192.168.1.100"

# Clear all rate limits (DANGEROUS in production)
docker compose exec redis-cache redis-cli --scan --pattern "rate_limit:*" | \
  xargs docker compose exec redis-cache redis-cli DEL
```

**Fix** (permanent):
```bash
# Increase rate limit in .env
RM_RATE_LIMIT_PER_IP=200/minute  # default: 100/minute
RM_RATE_LIMIT_PER_USER=400/minute  # default: 200/minute

# Restart API
docker compose restart backend-api
```

#### 4. "Cache miss rate >50%"

**Symptom**: High database load, slow queries

**Diagnosis**:
```bash
# Check cache hit rate
curl http://localhost:8000/metrics | grep cache_requests_total

# Expected:
# cache_requests_total{domain="revenue",status="hit"} 850
# cache_requests_total{domain="revenue",status="miss"} 150
# Hit rate: 850 / (850 + 150) = 85%
```

**Causes**:
- Redis cache is down
- TTL too short (default: 5 minutes for analytics)
- Unique queries (no cache benefit)

**Fix**:
```bash
# Check Redis
docker compose ps redis-cache

# Increase TTL in backend/app/services/analytics/service.py:
# CACHE_TTL_SECONDS = 600  # default: 300 (5 min)
```

#### 5. "Slow detection sweeps"

**Symptom**: NoDetectionSweep alert, or long gaps between alerts

**Diagnosis**:
```bash
# Check Celery worker status
docker compose exec celery-worker celery -A app.worker inspect active

# Check Celery beat schedule
docker compose exec celery-beat celery -A app.worker inspect scheduled

# View worker logs
docker compose logs celery-worker --tail=100
```

**Fix**:
```bash
# If no workers active
docker compose restart celery-worker

# If beat is not scheduling
docker compose restart celery-beat

# Check task queue length
docker compose exec redis-state redis-cli LLEN celery
# If backlog is large (>100), scale workers:
docker compose up -d --scale celery-worker=3
```

---

## Runbooks

### Runbook 1: Deploy New Version

**Scenario**: Deploy updated Docker image to production

**Prerequisites**:
- All tests passing (`make test`, `make test-integration`)
- Changelog updated
- Backup completed

**Steps**:

1. **Tag release**:
```bash
git tag v0.9.1
git push origin v0.9.1
```

2. **Build image**:
```bash
docker compose -f infra/compose/compose.yml build backend-api
```

3. **Backup database**:
```bash
./scripts/backup-postgres.sh /var/backups/postgres
```

4. **Apply migrations** (if any):
```bash
docker compose exec backend-api alembic upgrade head
```

5. **Deploy with zero-downtime**:
```bash
# Stop old container, start new one
docker compose up -d --no-deps backend-api

# Wait for health check
docker compose ps backend-api
# Should show "healthy" after ~40 seconds
```

6. **Verify**:
```bash
# Check version
curl http://localhost:8000/health
# Should show new version

# Smoke test
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/v1/analytics/revenue/summary
```

7. **Monitor**:
```bash
# Watch logs for 5 minutes
docker compose logs -f backend-api --tail=100

# Check error rate
curl http://localhost:8000/metrics | grep http_requests_total
```

8. **Rollback** (if issues):
```bash
# Revert to previous image
docker compose pull backend-api  # pulls old tag
docker compose up -d --no-deps backend-api

# Downgrade migrations if needed
docker compose exec backend-api alembic downgrade -1
```

### Runbook 2: Database Migration

**Scenario**: Apply new Alembic migration

**Prerequisites**:
- Migration tested on staging
- Backup completed
- Rollback plan prepared

**Steps**:

1. **Backup database**:
```bash
./scripts/backup-postgres.sh
```

2. **Check current version**:
```bash
docker compose exec backend-api alembic current
# Example output: 5d8f2a1b3c4e (head)
```

3. **Review migration SQL** (dry run):
```bash
docker compose exec backend-api alembic upgrade head --sql > migration.sql
cat migration.sql  # Review changes
```

4. **Apply migration**:
```bash
docker compose exec backend-api alembic upgrade head
```

5. **Verify**:
```bash
# Check new version
docker compose exec backend-api alembic current

# Check table exists
docker compose exec postgres psql -U retailmind -d retailmind \
  -c "\d new_table_name"
```

6. **Test application**:
```bash
# Run smoke tests
make test-integration
```

7. **Rollback** (if issues):
```bash
# Downgrade one migration
docker compose exec backend-api alembic downgrade -1

# Verify
docker compose exec backend-api alembic current
```

### Runbook 3: Restore from Backup

**Scenario**: Database corruption, restore from backup

**Prerequisites**:
- Backup file verified (gzip integrity)
- All services stopped
- Users notified (downtime expected)

**Steps**:

1. **Stop all services** (except PostgreSQL):
```bash
docker compose stop backend-api ui celery-worker celery-beat
```

2. **List available backups**:
```bash
ls -lh ./backups/
```

3. **Restore from backup**:
```bash
./scripts/restore-postgres.sh ./backups/retailmind_retailmind_20260814_030000.sql.gz
# Follow confirmation prompts
```

4. **Verify restoration**:
```bash
# Check table count
docker compose exec postgres psql -U retailmind -d retailmind \
  -c "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='public'"

# Check critical tables
docker compose exec postgres psql -U retailmind -d retailmind \
  -c "SELECT COUNT(*) FROM fct_sales; SELECT COUNT(*) FROM dim_customer;"
```

5. **Restart services**:
```bash
docker compose start backend-api ui celery-worker celery-beat
```

6. **Smoke tests**:
```bash
# Health check
curl http://localhost:8000/health

# Analytics query
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/v1/analytics/revenue/summary
```

7. **Monitor logs**:
```bash
docker compose logs -f --tail=100
```

---

## Maintenance Procedures

### Weekly Maintenance

**Every Monday at 2:00 AM** (low-traffic window):

1. **Review metrics**:
   - Error rate (should be <1%)
   - p95 latency (should be <2s for most endpoints)
   - Cache hit rate (should be >80%)

2. **Check disk space**:
```bash
df -h /var/lib/docker
# Warning if >80%, critical if >90%
```

3. **Review alerts**:
   - Check if any alerts fired in past week
   - Investigate root causes
   - Update runbooks if new issues found

4. **Verify backups**:
```bash
ls -lh ./backups/ | wc -l
# Should have 30 backups (1 per day, 30 day retention)
```

### Monthly Maintenance

**First Sunday of each month at 1:00 AM**:

1. **Docker cleanup**:
```bash
# Remove unused images
docker image prune -a -f

# Remove unused volumes (CAREFUL)
docker volume prune -f

# Remove build cache
docker builder prune -f
```

2. **Update dependencies**:
```bash
# Update Python dependencies
uv lock --upgrade
uv sync

# Run tests
make test
make test-integration
```

3. **Security updates**:
```bash
# Update base images
docker compose pull

# Rebuild with security patches
docker compose build --no-cache
```

4. **Review logs for anomalies**:
```bash
# Count ERROR logs
docker compose logs backend-api --since 1m | grep ERROR | wc -l

# Top error messages
docker compose logs backend-api --since 1m | grep ERROR | \
  jq -r '.message' | sort | uniq -c | sort -rn | head -10
```

### Quarterly Maintenance

**Every 3 months**:

1. **Disaster recovery drill**:
   - Test restore from backup
   - Measure RTO (Recovery Time Objective)
   - Update runbooks

2. **Performance review**:
   - Analyze slow query logs
   - Review cache strategy
   - Optimize database indexes

3. **Security audit**:
   - Review RBAC permissions
   - Audit API key usage
   - Check for unused accounts

4. **Capacity planning**:
   - Review database size growth
   - Check Redis memory usage
   - Forecast infrastructure needs

---

## Operational Metrics

### Service Level Objectives (SLOs)

**Availability**: 99.5% uptime (43.8 hours downtime/year allowed)

**Latency**:
- p50 < 500ms
- p95 < 2s
- p99 < 5s

**Error Rate**: <1% (5xx only)

**Data Freshness**:
- Ingest → Bronze: <5 minutes
- Bronze → Silver: <10 minutes
- Silver → Gold: <15 minutes

### Key Performance Indicators (KPIs)

**Daily**:
- Request volume
- Error count by endpoint
- Cache hit rate
- Database query time

**Weekly**:
- Detection sweep completion rate
- Backup success rate
- Alert frequency

**Monthly**:
- User growth
- API key provisioning
- Disk usage trend

---

**Maintained by**: RetailMind AI Contributors
**License**: MIT
**Last Reviewed**: 2026-08-15
