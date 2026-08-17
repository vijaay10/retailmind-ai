# Production Deployment Checklist

**Last Updated:** 2026-08-15
**Version:** 0.9.0

This checklist ensures all critical configuration, security, and operational requirements are met before production deployment.

---

## Pre-Deployment

### Infrastructure

- [ ] **Environment Variables**
  - [ ] All required env vars set (verify with `uv run python scripts/check_env.py`)
  - [ ] Production-specific values (not dev defaults)
  - [ ] `RM_APP_ENV=prod`
  - [ ] `RM_APP_BASE_URL` set to actual domain
  - [ ] `RM_APP_VERSION` set to git SHA (CI should inject)

- [ ] **Secrets Management**
  - [ ] Database password via `RM_DB_PASSWORD_FILE` (NOT env var)
  - [ ] JWT private key via `RM_AUTH_JWT_PRIVATE_KEY_FILE`
  - [ ] SMTP password via `RM_SMTP_PASSWORD_FILE` (if using email)
  - [ ] LLM API key via `RM_LLM_API_KEY_FILE` (if using real LLM)
  - [ ] All secrets mounted via Docker secrets (not in compose file)

- [ ] **Database**
  - [ ] PostgreSQL 16+ running
  - [ ] SSL mode set to `require` (NOT `disable`)
  - [ ] Connection pool size appropriate (`RM_DB_POOL_SIZE`)
  - [ ] Database user has only necessary permissions (NOT superuser)

- [ ] **Redis**
  - [ ] Two separate instances (cache vs state)
  - [ ] Cache instance: LRU eviction enabled
  - [ ] State instance: No eviction (persistence enabled)
  - [ ] Persistence configured (AOF or RDB)

- [ ] **Warehouse (DuckDB)**
  - [ ] Warehouse path on persistent volume
  - [ ] Regular snapshots configured
  - [ ] Sufficient disk space (monitor growth)

---

### Security

- [ ] **TLS/SSL**
  - [ ] Valid TLS certificate installed
  - [ ] Certificate auto-renewal configured (Let's Encrypt)
  - [ ] HTTP redirects to HTTPS
  - [ ] Nginx TLS configuration verified
  - [ ] `RM_AUTH_COOKIE_SECURE=true`

- [ ] **Authentication**
  - [ ] **CRITICAL:** JWT private key configured (NOT ephemeral)
  - [ ] Verify single key shared across all workers
  - [ ] Access token TTL appropriate (15 min default)
  - [ ] Refresh token TTL appropriate (30 days default)
  - [ ] Refresh cookie HTTP-only and secure

- [ ] **Network Security**
  - [ ] Database NOT exposed to public internet
  - [ ] Redis NOT exposed to public internet
  - [ ] Only edge (nginx) exposed on 80/443
  - [ ] Firewall rules configured
  - [ ] Docker networks isolated

- [ ] **Rate Limiting**
  - [ ] Enabled (default: 1000 req/hr authenticated, 100 unauthenticated)
  - [ ] Limits appropriate for expected load
  - [ ] Redis-backed (not in-memory)

- [ ] **API Keys**
  - [ ] Prefix-based keys for external integrations
  - [ ] Keys stored hashed in database
  - [ ] Rotation policy defined

---

### Application

- [ ] **Workers & Processes**
  - [ ] `RM_API_WORKERS` set appropriately (4+ for production)
  - [ ] `RM_WORKER_CONCURRENCY` set appropriately
  - [ ] Worker health checks configured
  - [ ] Beat scheduler running (single instance only)

- [ ] **Database Migrations**
  - [ ] All migrations applied
  - [ ] Migration 0005 down_revision fixed (see known-issues.md)
  - [ ] Genesis migration documented (not replayable)
  - [ ] Alembic current matches expected version

- [ ] **Feature Flags**
  - [ ] Review all feature flags for production appropriateness
  - [ ] LLM narration enabled/disabled as desired
  - [ ] Any beta features disabled

---

### Monitoring & Observability

- [ ] **Prometheus**
  - [ ] Scraping all targets (api, worker, beat, postgres_exporter)
  - [ ] Retention period configured
  - [ ] Storage sufficient

- [ ] **Grafana**
  - [ ] Dashboards provisioned (api-dashboard, pipeline-dashboard, bi-dashboard)
  - [ ] Data source connected
  - [ ] Admin password changed from default
  - [ ] User access configured

- [ ] **Alertmanager**
  - [ ] Alert rules loaded (ApiDown, HighErrorRate, SlowRequests, NoDetectionSweep)
  - [ ] **CRITICAL:** Notification endpoint configured (webhook/email)
  - [ ] Test alert sent and received
  - [ ] Escalation policy defined

- [ ] **Logging**
  - [ ] Log level set to INFO (NOT DEBUG in production)
  - [ ] Structured logging enabled
  - [ ] Log aggregation configured (if using central logging)
  - [ ] Log rotation configured

---

### Backup & Disaster Recovery

- [ ] **Automated Backups**
  - [ ] Backup service enabled (`--profile backup`)
  - [ ] Backup schedule verified (default: daily 02:00 UTC)
  - [ ] Retention period set (`RETENTION_DAYS`, default 30)
  - [ ] **CRITICAL:** Offsite backups configured (S3/rsync)

- [ ] **Backup Verification**
  - [ ] Test backup created successfully
  - [ ] Backup file integrity verified (gzip -t)
  - [ ] Backup contains expected data (zcat | head)

- [ ] **Restore Testing**
  - [ ] Restore procedure documented
  - [ ] Restore tested in staging environment
  - [ ] RTO/RPO documented (default: 30 min / 24 hrs)

- [ ] **Data Retention**
  - [ ] Backup retention policy matches compliance requirements
  - [ ] Database backup rotation tested
  - [ ] Old backups actually deleted after retention

---

### Data Pipeline

- [ ] **Ingestion**
  - [ ] Inbox directory writable by ingestion service
  - [ ] Source file arrival monitoring configured
  - [ ] Quality gates configured (reject rate threshold)
  - [ ] Quarantine alerting enabled

- [ ] **dbt**
  - [ ] dbt profiles configured correctly
  - [ ] All 67 models compile
  - [ ] All 77 tests passing
  - [ ] Scheduled execution configured (daily via Dagster)

- [ ] **Dagster**
  - [ ] 90 assets loading successfully
  - [ ] 8 jobs defined and executable
  - [ ] Schedules configured (ingestion, dbt, forecast)
  - [ ] Sensors configured (retry, quarantine alert)

- [ ] **ML Models**
  - [ ] Forecast models trained
  - [ ] Model accuracy meets threshold (MASE < 1.0)
  - [ ] Model retraining schedule configured

---

### Pre-Launch Testing

- [ ] **Smoke Tests**
  - [ ] `/health` returns 200 OK
  - [ ] `/ready` returns 200 OK (database connected)
  - [ ] `/metrics` returns Prometheus format
  - [ ] Login flow works (auth/login → token → authenticated request)

- [ ] **Integration Tests**
  - [ ] Full test suite passing (97.6%+ pass rate acceptable)
  - [ ] Known test failures documented (known-issues.md)
  - [ ] No critical failures

- [ ] **End-to-End Flows**
  - [ ] Data ingestion → warehouse → analytics → UI
  - [ ] Question → Analyst → LLM narration → Response
  - [ ] Recommendation generation → display → accept/dismiss
  - [ ] Report generation → sections → delivery

- [ ] **Load Testing**
  - [ ] Load test executed at expected traffic +20%
  - [ ] No degradation under sustained load
  - [ ] Rate limiting triggers appropriately
  - [ ] Worker autoscaling tested (if applicable)

---

### Deployment

- [ ] **Pre-Deployment Communication**
  - [ ] Deployment window communicated to users
  - [ ] Rollback plan documented
  - [ ] On-call engineer identified

- [ ] **Deployment Steps**
  - [ ] Git tag created (version 0.9.0 or appropriate)
  - [ ] Docker images built with version tag
  - [ ] Images scanned for vulnerabilities
  - [ ] Database backup taken immediately before deployment
  - [ ] Deployment executed during maintenance window
  - [ ] Health checks pass post-deployment

- [ ] **Post-Deployment Verification**
  - [ ] All services running (`docker compose ps`)
  - [ ] All services healthy
  - [ ] No error spikes in logs
  - [ ] Prometheus targets up
  - [ ] Grafana dashboards rendering
  - [ ] Test user login successful
  - [ ] Sample queries return correct results

---

## Post-Deployment

### First 24 Hours

- [ ] **Monitor Error Rates**
  - [ ] Check Grafana API dashboard every hour
  - [ ] Check application logs for ERROR level
  - [ ] Verify no 5xx spike

- [ ] **Monitor Performance**
  - [ ] Request latency within expected range (p95 < 5s)
  - [ ] Database query performance normal
  - [ ] Cache hit rate healthy (>70%)

- [ ] **Verify Backups**
  - [ ] First automated backup completed successfully
  - [ ] Backup file size reasonable
  - [ ] Offsite sync completed

- [ ] **Verify Alerting**
  - [ ] No false positive alerts
  - [ ] Alert notifications received
  - [ ] Alert manager routing working

### First Week

- [ ] **Usage Metrics**
  - [ ] User login rate as expected
  - [ ] API request volume as expected
  - [ ] No unusual traffic patterns

- [ ] **Data Pipeline**
  - [ ] Daily ingestion completing successfully
  - [ ] dbt runs completing within SLA
  - [ ] Forecast model updates running
  - [ ] No quality gate failures

- [ ] **User Feedback**
  - [ ] No critical user-reported issues
  - [ ] Performance acceptable to users
  - [ ] Feature availability as expected

---

## Rollback Procedure

If critical issues detected:

1. **Immediate Actions**
   ```bash
   # Stop current deployment
   docker compose down

   # Restore previous version
   git checkout {previous-tag}
   docker compose up -d

   # Verify health
   curl http://localhost/health
   ```

2. **Database Rollback** (if migrations applied)
   ```bash
   # Restore from backup
   ./scripts/restore-postgres.sh /backups/retailmind_YYYYMMDD_HHMMSS.sql.gz
   ```

3. **Communication**
   - Notify users of rollback
   - Log incident details
   - Schedule post-mortem

---

## Critical Issues That Block Deployment

These MUST be resolved before production deployment:

1. ❌ **JWT private key not configured** (causes intermittent 401s with multiple workers)
   - Fix: Set `RM_AUTH_JWT_PRIVATE_KEY_FILE`

2. ❌ **Database SSL disabled** (security risk)
   - Fix: Set `RM_DB_SSLMODE=require`

3. ❌ **Secrets in environment variables** (visible via docker inspect)
   - Fix: Use `*_FILE` convention with Docker secrets

4. ❌ **No alerting configured** (issues go unnoticed)
   - Fix: Configure Alertmanager webhook/email

5. ❌ **No offsite backups** (data loss if host fails)
   - Fix: Configure S3 sync or rsync to remote storage

---

## Non-Blocking Issues (Can Deploy With)

These should be fixed post-deployment but don't block go-live:

- ⚠️ Migration chain broken (migrations 0004, 0005 unapplied)
  - Impact: Missing outcome measurement and LLM usage tracking
  - Core functionality unaffected

- ⚠️ 20 test failures (test-only issues, not production code)
  - Impact: Test suite integrity
  - Production runtime verified separately

- ⚠️ 11 ruff linting warnings (code quality)
  - Impact: Code style only
  - Functionality unaffected

See `docs/known-issues.md` for complete list.

---

## Compliance & Legal

- [ ] **Data Privacy**
  - [ ] GDPR compliance reviewed (if applicable)
  - [ ] Data retention policy documented
  - [ ] PII handling reviewed
  - [ ] Customer data export capability verified

- [ ] **Security**
  - [ ] Security audit completed (see docs/security-audit.md)
  - [ ] Vulnerability scan passed
  - [ ] Penetration test completed (if required)

- [ ] **Licensing**
  - [ ] All dependencies licenses reviewed
  - [ ] License compliance verified
  - [ ] Third-party attribution included

---

## Sign-Off

Before deployment to production, the following roles must sign off:

- [ ] **Tech Lead:** Infrastructure, security, and architecture reviewed
- [ ] **QA Lead:** Testing complete, known issues documented
- [ ] **DevOps:** Monitoring, backups, and runbooks ready
- [ ] **Product Owner:** Feature scope approved, user communication ready

**Deployment Authorized By:** _________________ **Date:** _________

---

## Post-Deployment Checklist

After successful deployment:

- [ ] Update version in README.md
- [ ] Update CHANGELOG.md
- [ ] Tag release in GitHub
- [ ] Notify users of new version
- [ ] Update status page (if applicable)
- [ ] Schedule post-deployment review (1 week out)

---

## References

- `docs/operations.md` - Operational procedures
- `docs/known-issues.md` - Known issues and workarounds
- `docs/backup-restore.md` - Backup/restore procedures
- `docs/security-audit.md` - Security assessment
- `docs/production-readiness-final-report.md` - Production readiness assessment
- `docs/e2e-verification-corrected.md` - Runtime verification results

---

**Version History:**

| Version | Date | Changes |
|---------|------|---------|
| 0.9.0 | 2026-08-15 | Initial production deployment checklist |

---

**Maintained by:** RetailMind AI Operations Team
