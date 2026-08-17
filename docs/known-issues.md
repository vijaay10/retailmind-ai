# Known Issues

**Last Updated:** 2026-08-15
**Version:** 0.9.0

This document lists all known issues, their impact, workarounds, and planned resolutions. Issues are categorized by severity and production impact.

---

## Table of Contents

- [Critical (Production Blocking)](#critical-production-blocking)
- [High (Functional Impact)](#high-functional-impact)
- [Medium (Degraded Experience)](#medium-degraded-experience)
- [Low (Minor/Cosmetic)](#low-minorcosmetic)
- [Documented Limitations (By Design)](#documented-limitations-by-design)

---

## Critical (Production Blocking)

**None currently identified.**

---

## High (Functional Impact)

### 1. Multiple JWT Signing Keys (Multi-Worker Deployments)

**Status: 🟢 RESOLVED 2026-08-15 (Prompt 10.5).** Direct inspection during
Prompt 10.5 found this was already correctly handled in code — outside
`dev`, `TokenSigner` raises `RuntimeError` at startup rather than minting an
ephemeral key, and `compose.prod.yml` wires `RM_AUTH_JWT_PRIVATE_KEY_FILE`
to a Docker secret shared by every worker/replica. What was actually missing
was test coverage proving it; four tests were added in
`backend/tests/unit/test_auth_security.py`
(`test_worker_b_verifies_a_token_signed_by_worker_a`,
`test_restart_does_not_invalidate_tokens_when_key_is_configured`,
`test_unconfigured_ephemeral_dev_keys_differ_per_worker`,
`test_missing_production_key_fails_fast_instead_of_generating_one`) — all
pass. The dev-only ephemeral-key behavior below remains accurate and is
unchanged; it is not a bug, and `make demo` already pins one worker as a
workaround for it. This item's file reference below
(`backend/app/infrastructure/auth/jwt.py`) does not exist — the real file is
`backend/app/core/security.py`; corrected here rather than left to mislead
the next reader.

**Issue:** Each uvicorn worker generates its own ephemeral RSA key pair when no configured key is provided, causing tokens issued by one worker to be rejected by another.

**Affected Component:** Authentication (`backend/app/core/security.py`)

**Impact:**
- Intermittent 401 Unauthorized errors in multi-worker deployments
- Default compose configuration (`RM_API_WORKERS=2`) exhibits this behavior
- Users may need to retry requests to hit the worker that issued their token

**Reproduction:**
```bash
# Start with default config (2 workers)
make up

# Login and make requests
# Some requests will fail with 401 if they hit the "other" worker
```

**Workaround:**
1. **Option A:** Run with single worker (demo mode)
   ```bash
   make demo  # Pins RM_API_WORKERS=1
   ```

2. **Option B:** Configure production JWT key
   ```bash
   # Generate key
   openssl genrsa -out jwt_private_key.pem 4096

   # Add to secrets
   docker secret create jwt_private_key jwt_private_key.pem

   # Set env var
   RM_AUTH_JWT_PRIVATE_KEY_FILE=/run/secrets/jwt_private_key
   ```

**Fix Status:** ✅ Documented workaround exists
**Priority:** P0 for production deployments
**Planned Resolution:** Enforce configured key in staging/prod overlays

**References:**
- `CLAUDE.md` - Documents this as a known issue
- `backend/app/core/config.py:38-45` - JWT key configuration

---

## Medium (Degraded Experience)

### 2. Migration Chain Broken (Migrations 0004, 0005 Cannot Apply)

**Status: 🟢 RESOLVED 2026-08-15 (Prompt 10.5).** The `down_revision`
reference described below was already correct in the working tree when this
pass started (`alembic heads` returned exactly one head,
`0005_llm_usage_tracking`) — this section's specific complaint was already
stale. However, actually *running* `alembic upgrade head` against a
disposable Postgres (never done before this pass) found two real,
independent bugs that a correct revision graph doesn't protect against:

1. `202608131600_outcome_measurement.py` wrapped `op.drop_constraint(...)` in
   `try: ... except Exception: pass` to handle "constraint doesn't exist yet
   on a fresh database" — but a failed statement aborts the whole Postgres
   transaction regardless of whether Python catches the exception, so every
   statement after it failed with "current transaction is aborted." Fixed by
   using `ALTER TABLE ... DROP CONSTRAINT IF EXISTS ...` instead (same
   pattern already used in `202607301500_enterprise_roles.py`).
2. `202608131800_llm_usage_tracking.py` unconditionally called
   `op.create_table("llm_request_log", ...)`, colliding with genesis's
   `Base.metadata.create_all()` on any fresh database (see "Genesis
   Migration Not Replayable" below — this is exactly the collision that
   limitation describes). Fixed with the same `if _exists(): return` guard
   `202608061347_recommendation_decisions.py` already uses for the same
   reason.

Also found and fixed while seeding a freshly-migrated database:
`AlertRule.min_severity_notify` had `server_default='warn'`, a value that
doesn't exist in the current 5-tier `Severity` enum (`info | low | medium |
high | critical`) and violated the column's own check constraint on any
insert relying on the default — changed to `'medium'`
(`backend/app/infrastructure/db/models/config.py`).

Full round trip (`upgrade base → head`, `downgrade head → base`,
`upgrade base → head` again) now passes against a disposable Postgres, table
count stable at 45 both times, single head, no data corruption. See
`backend/tests/integration/test_migrations.py` (6/6 passing) and
`docs/prompt-10.5-final-report.md` for the full verification record.

**Issue:** Migration `202608131800_llm_usage_tracking.py` has incorrect `down_revision` reference, breaking the migration chain.

**Affected Component:** Database migrations

**Details:**
```python
# Current (incorrect):
revision = "202608131800"
down_revision = "202608131600"  # Does not exist

# Should be:
revision = "0005_llm_usage_tracking"
down_revision = "0004_outcome_measurement"
```

**Migration Chain Status:**
```
✅ 0001_genesis (applied)
✅ 0002_enterprise_roles (applied)
✅ 0003_recommendation_decisions (applied) ← CURRENT
⚠️ 0004_outcome_measurement (pending - cannot apply due to broken chain)
❌ 0005_llm_usage_tracking (blocked - down_revision mismatch)
```

**Impact:**
- Migrations 0004 and 0005 cannot be applied
- Missing features:
  - **0004:** Recommendation outcome measurement lifecycle tracking
  - **0005:** LLM usage tracking table for cost accounting
- Core functionality unaffected (runs on migration 0003)

**Workaround:**
- System operational without these migrations
- Features are additive (measurement tracking, LLM cost accounting)

**Fix:**
```python
# Edit: backend/app/infrastructure/db/migrations/versions/202608131800_llm_usage_tracking.py
# Change line 16:
down_revision = "0004_outcome_measurement"
```

**Fix Status:** ⚠️ Requires code change
**Priority:** P1 (before applying outcome measurement features)
**Planned Resolution:** Update migration file, apply both migrations

**References:**
- `docs/e2e-verification-corrected.md` - Documents this issue
- `backend/app/infrastructure/db/migrations/versions/202608131800_llm_usage_tracking.py:16`

---

## Low (Minor/Cosmetic)

### 3. Dagster Test Failures (18 Tests)

**Status: 🟢 RESOLVED 2026-08-15 (Prompt 10.5).** The specific API this
section names (`get_all_job_defs()`) had already been replaced with
`resolve_all_job_defs()` before this pass started — that part of the
complaint was stale. Running the suite for real found the actual failures
were 14 different, unreported problems: `Mock(spec=CliExecutor)` /
`Mock(spec=DuckDBWarehouse)` failing Dagster's resource-type validation on
direct asset invocation; a removed `Definitions.get_all_asset_specs()` API
(replacement: `resolve_all_asset_specs()`); an `AssetsDefinition` no longer
exposing a `_op_def` attribute; `AssetCheckResult.metadata` values now being
`MetadataValue` wrappers instead of raw ints; a `:memory:` DuckDB test
assuming state survives across connections it doesn't; and a substring
key-match that silently matched the wrong asset. All fixed in
`data_platform/tests/unit/test_dagster_orchestration.py` — 21/21 pass. Also
removed 4 uses of the deprecated `define_asset_job(partitions_def=...)`
parameter from production code (`data_platform/orchestration/dagster/schedules.py`),
confirmed Dagster still resolves the same partition scheme without it.

**Issue:** Dagster tests use deprecated 1.x API method that no longer exists in Dagster 2.x

**Affected Component:** Test suite (`data_platform/tests/unit/test_dagster_orchestration.py`)

**Details:**
```python
# Current (failing):
jobs = list(defs.get_all_job_defs())  # AttributeError

# Should be:
jobs = list(defs.resolve_all_job_defs())  # Dagster 2.x API
```

**Affected Tests:**
- `test_jobs_defined()` (line 374)
- `test_partition_based_backfill_supported()` (line 424)
- 16 tests that call these functions

**Impact:**
- **Production:** ✅ No impact (method not used in production code)
- **Test Suite:** ❌ 18 tests failing (15% of data platform tests)
- **Dagster Runtime:** ✅ Fully operational
  - Definitions load successfully
  - 90 assets discovered
  - 8 jobs executable
  - Validation passes

**Verification:**
```bash
# Dagster runtime works correctly:
cd data_platform
uv run python -m dagster definitions validate -m orchestration.dagster
# Output: "Validation successful for code location orchestration.dagster"

uv run python -m dagster asset list -m orchestration.dagster
# Output: 90 assets listed

uv run python -m dagster job list -m orchestration.dagster
# Output: 8 jobs listed
```

**Fix:**
```python
# Edit: data_platform/tests/unit/test_dagster_orchestration.py
# Replace lines 374 and 424:
jobs = list(defs.resolve_all_job_defs())
```

**Fix Status:** ⚠️ Requires code change (test-only)
**Priority:** P2 (test suite integrity)
**Planned Resolution:** Update test suite to Dagster 2.x API

**References:**
- `docs/e2e-verification-corrected.md` - Documents as test-only issue
- `data_platform/tests/unit/test_dagster_orchestration.py:374,424`

---

### 4. Database Schema Test Failures (2 Tests)

**Status: 🟢 ALREADY CORRECT — verified 2026-08-15 (Prompt 10.5).** Both
assertions below were already updated in the working tree before this pass
started (42 tables, 5-tier severity check) — this section's specific
complaint was stale. `backend/tests/unit/test_db_schema.py` (the real file;
this section's `tests/unit/test_schema.py` path does not exist) — all 131
tests pass as-is, no change needed. Separately, `backend/tests/integration/test_migrations.py::test_all_tables_created`
asserted a stale raw table count (44) against real Postgres; updated to 45
(42 model tables + `alembic_version` + 2 partition-default tables) and
verified against a disposable database.

**Issue:** Tests have outdated expectations that don't match current database schema

**Affected Component:** Test suite (`backend/tests/unit/test_db_schema.py`)

#### 4a. Table Count Mismatch

**Details:**
```python
# Test expects:
assert len(_tables()) == 41

# Actual:
len(_tables()) == 44
```

**Cause:** Test written before additional tables added (partitions, materialized views)

**Impact:** ⚪ Cosmetic (test expectation needs update)

**Fix:**
```python
# Update test expectation:
assert len(_tables()) == 44
```

#### 4b. Alert Severity Enum Mismatch

**Details:**
```python
# Test expects:
assert "severity IN ('info', 'warn', 'critical')" in alert_ddl

# Actual (database):
CHECK (severity IN ('info', 'low', 'medium', 'high', 'critical'))
```

**Cause:** Implementation evolved to more granular severity levels, test not updated

**Impact:** ⚪ Cosmetic (implementation is correct, test expectation outdated)

**Fix:**
```python
# Update test expectation:
assert "severity IN ('info', 'low', 'medium', 'high', 'critical')" in alert_ddl
```

**Fix Status:** ⚠️ Requires test update
**Priority:** P3 (test suite accuracy)
**Planned Resolution:** Update test expectations to match current schema

**References:**
- `docs/e2e-verification-corrected.md` - Documents as test-only issues

---

### 5. Ruff Linting Warnings (11 Issues)

**Status: 🟢 RESOLVED 2026-08-15 (Prompt 10.5).** The actual count was 60,
not 11, when measured for real (`ruff check .`) — this section's number was
never checked against the repository. All 60 fixed: 15 by `ruff check --fix`
(PEP 585 modernization), the rest by hand — real `try/except/pass` around a
transaction-aborting migration statement (see item 2 above, not cosmetic),
relative imports converted to absolute in `data_platform/orchestration/dagster/assets/*.py`,
a duplicate mid-file import, an unused variable, line-length wraps, and
`noqa` annotations (with stated reasons) on subprocess/SQL-construction
findings that are genuine false positives — internal identifiers and
Dagster-managed partition keys, never external input. `ruff check .` now
reports zero errors.

**Issue:** Code contains 11 ruff linting warnings, primarily `try-except-pass` without logging

**Affected Component:** Various files

**Details:**
```python
# Current (flagged by ruff):
try:
    operation()
except Exception:
    pass  # S110: try-except-pass without logging

# Suggested:
with contextlib.suppress(Exception):
    operation()
```

**Impact:** ⚪ Code quality (not functionality)

**Fix:**
```bash
# Auto-fix safe issues:
make lint-fix

# Manual review for remaining issues
```

**Fix Status:** ⚠️ Can be auto-fixed
**Priority:** P3 (code quality)
**Planned Resolution:** Run `ruff check --fix`, review remaining issues

---

### 6. Shared Analytics Warehouse Across Tenants

**Status: 🟢 RESOLVED 2026-08-17 (Prompt 12.5).** Prompt 12 (2026-08-16)
added real multi-tenant onboarding — tenant creation, RBAC-scoped company
profile, dataset detection/mapping/validation — but found one warehouse
served every tenant: `SemanticLayerClient` was built once at process
startup from a single global `RM_WAREHOUSE_DUCKDB_PATH`, so a newly
onboarded company's dashboard, analytics, recommendations, and forecasts all
read the demo tenant's shared DuckDB file regardless of which tenant asked.

**Root cause and fix:** each tenant now has its own DuckDB warehouse file,
resolved per-request from `principal.tenant_id`
(`app.infrastructure.semantic.tenancy.resolve_warehouse_path`), replacing
the single process-wide client. Two additional, separate instances of the
same underlying bug were found and fixed during remediation: the dashboard
service's dependency read `app.state.semantic_client` directly, bypassing
the (already-being-fixed) analytics service's resolution; and the scheduled
notification-sweep worker read `RM_WAREHOUSE_DUCKDB_PATH` from the OS
environment unconditionally, ignoring which tenant's sweep was running.
Proven end-to-end with two real, differently-shaped tenant warehouses built
by the unmodified ingestion+dbt pipeline, queried through the same running
API, asserting genuinely independent, distinct revenue and forecast figures
— not mocked. Full detail: `docs/multi-tenancy-architecture.md`,
`docs/prompt-12.5-tenant-isolation-report.md`.

**No dbt model changes were required** — isolation is physical (separate
files), not a `WHERE tenant_id` filter threaded through 67+ models.

---

## Documented Limitations (By Design)

These are not bugs, but documented design constraints that users should be aware of.

### 1. Genesis Migration Not Replayable

**Limitation:** Initial migration uses `Base.metadata.create_all()` instead of individual `op.create_table()` calls

**Impact:**
- Cannot replay full migration history from scratch with column-level detail
- Subsequent migrations work correctly
- Does not affect operational deployments

**Reason:** Pragmatic choice for initial schema creation

**Documentation:** `CLAUDE.md` - "Known issues" section

**Workaround:** Export schema from working database if replay needed

---

### 1a. Backup/Restore Scripts — Update 2026-08-15 (Prompt 10.5)

**Status: 🟢 Actually executed and verified for the first time.** Both
`scripts/backup-postgres.sh` and `scripts/restore-postgres.sh` had only ever
been syntax-checked (`bash -n`), never run. Running them for real against a
disposable Postgres (seeded, not empty) found and fixed two bugs that made
them fail on every invocation: the backup script's own success-verification
step failed unconditionally due to a `pipefail`/SIGPIPE interaction with
`head`/`grep -q`, and the restore script's `--single-transaction` flag is
incompatible with `DROP DATABASE`/`CREATE DATABASE` (which the dump always
contains) and failed immediately. Both fixed; full backup → restore → verify
round trip now passes for the realistic disaster-recovery case (restoring a
database's own backup back into itself). A genuine, documented (not fixed)
limitation was also found: restoring into a *differently-named* database
does not work, because `pg_dump --create`'s embedded `\connect` statement
hardcodes the source database's name — see
`docs/backup-restore-test-results.md` for the full writeup, real timings,
and row counts. Production `rmprod-postgres-1` was never touched.

### 2. Backup Service Requires Profile

**Limitation:** Automated backup service defined in `compose.prod.yml` but requires explicit profile activation

**Impact:**
- Backups not running by default in standard `make up`
- Must explicitly start with `--profile backup`

**Reason:** Avoids backup overhead in development deployments

**How to Enable:**
```bash
# Production deployment with backups:
docker compose --profile backup up -d

# Or via environment:
COMPOSE_PROFILES=backup docker compose up -d
```

**Documentation:** `docs/backup-restore.md`

---

### 3. LLM Mock Mode Default

**Limitation:** LLM gateway defaults to mock provider, not real Anthropic API

**Impact:**
- LLM narration uses templated responses by default
- Real Claude API integration requires explicit configuration

**Reason:** Avoids unexpected API costs in development/testing

**How to Enable Real LLM** (corrected 2026-08-15 — the variable names below
were wrong; verified against `backend/app/core/config.py::LLMSettings` and
`.env.example`, which now documents this section in full):
```bash
# In .env:
RM_LLM_PROVIDER=anthropic
RM_LLM_ANTHROPIC_API_KEY=sk-ant-...
# prod: RM_LLM_ANTHROPIC_API_KEY_FILE=/run/secrets/llm_api_key
```

**Note on CLAUDE.md:** this LLM gateway is real, working, uncommitted code
(`backend/app/infrastructure/llm/`) that contradicts CLAUDE.md's "Things
that specifically do not exist" table, which currently states "LLM / Claude
/ OpenAI integration: None. Zero API calls." That line is stale as of this
gateway's existence. It defaults to mock (no external calls) exactly as
described above, so the *behavior* CLAUDE.md implies (no surprise API
costs) still holds — but the claim "zero API calls, period" is no longer
literally true once a key is configured. Not corrected in CLAUDE.md by this
pass — flagged here so it isn't silently left wrong; CLAUDE.md itself says
to re-verify its claims rather than trust them.

**Documentation:** `docs/ai.md`

---

### 4. Offsite Backups Not Configured

**Limitation:** Backups write to local Docker volume only, not offsite storage

**Impact:**
- No protection against host failure
- RPO/RTO assumes local backups accessible

**Reason:** S3/cloud storage credentials environment-specific

**Recommendation:** Configure S3 sync or rsync to remote storage for production

**Documentation:** `docs/backup-restore.md` - "Offsite Backup Configuration"

---

### 4a. Environment/Port Contract Drift — Update 2026-08-15 (Prompt 10.5)

**Status: 🟢 RESOLVED.** `scripts/check_env.py` (`.env.example` vs. the
settings model, and vs. every `os.environ.get("RM_...")`/`${RM_...}` read in
the codebase) was failing with 17 undocumented variables — 10 for the LLM
gateway, 7 for Alertmanager's SMTP/email/Slack routing
(`infra/monitoring/alertmanager.yml`, `infra/compose/compose.prod.yml`).
`.env.example` updated with real sections for both. `scripts/check_ports.py`
was failing too: Alertmanager's `127.0.0.1:9093` binding (deliberate,
SSH-tunnel-only, same rationale as Grafana's `127.0.0.1:3000`) was never
added to the script's own allowlist. Both scripts now exit 0.

### 5. Alertmanager Notification Endpoint Not Configured

**Limitation:** Alertmanager configured but notification webhook/email not set

**Impact:**
- Alerts fire and are logged but no external notifications sent
- Requires manual Prometheus/Alertmanager UI monitoring

**Reason:** Webhook URL and email credentials environment-specific

**How to Configure:**
```yaml
# Edit infra/monitoring/alertmanager.yml
receivers:
  - name: 'default-receiver'
    webhook_configs:
      - url: 'https://your-webhook-endpoint'
    email_configs:
      - to: 'ops@example.com'
        from: 'alertmanager@retailmind.local'
```

**Documentation:** `docs/alerting.md`

---

## Issue Summary

**Updated 2026-08-15 (Prompt 10.5).** The table below is what this document
claimed before this pass — kept for history. Every numbered item in it
(1–5) is now RESOLVED or found to have already been correct; see the
RESOLVED/ALREADY CORRECT notes inline above for what was actually true and
what was actually fixed. Two additional real bugs were found during
remediation that this table never anticipated: the backup/restore scripts
(item 1a) and the env/port contract drift (item 4a) — both also resolved.
The one exception is the backup/restore rename-target limitation, which is
real, documented, and intentionally not fixed (see item 1a and
`docs/backup-restore-test-results.md`).

| Severity | Count | Production Blocking | Fix Status (original) |
|----------|-------|-------------------|------------|
| Critical | 0 | 0 | N/A |
| High | 1 | 0 (workaround exists) | Documented |
| Medium | 1 | 0 (features not active) | Requires fix |
| Low | 4 | 0 (test/quality only) | Requires fix |
| **Total** | **6** | **0** | **5 need fixes** |

### Test Suite Impact

**Historical claim (unverified when written):** 824 total, 804 passing
(97.6%). This number was never reconciled against `docs/production-readiness-baseline.md`'s
separate claim of "1,099 tests passing" — the two are irreconcilable, and
neither was re-derived from an actual run before this pass.

**Actual, as of 2026-08-15 (Prompt 10.5), `uv run pytest backend/tests/unit
data_platform/tests/unit ml/tests ui/tests -q`:** 977 collected, 976 passing,
1 failing — `ui/tests/test_workspaces.py::test_command_center_loads_without_error`,
pre-existing (present before this remediation pass touched anything, in
`ui/` files this pass did not modify), unrelated to any P0–P3 item in scope
here. Root cause: the test asserts the greeting markdown is `app.markdown[0]`,
but `design.configure()` emits a global CSS block as the first `st.markdown`
call, ahead of it. Left as a documented, out-of-scope finding rather than
fixed blind, since UI test-fixture assumptions weren't part of this
remediation's mandate. See `docs/prompt-10.5-test-results.md` for the full
suite-by-suite breakdown.

---

## Reporting New Issues

1. Check this document first
2. Search existing GitHub issues
3. Create new issue with:
   - Clear reproduction steps
   - Expected vs actual behavior
   - Environment details
   - Logs/screenshots

**Issue Template:** `.github/ISSUE_TEMPLATE.md`

---

## Version History

| Version | Date | Changes |
|---------|------|---------|
| 0.9.0 | 2026-08-15 | Initial comprehensive known issues documentation |
| 0.9.1 | 2026-08-15 | Prompt 10.5 remediation: items 1–5 resolved or found already-correct; 1a (backup/restore) and 4a (env/port contract) added and resolved; corrected two hallucinated file paths and the LLM env var names; test counts re-measured for real |

---

**Maintained by:** RetailMind AI Contributors
**See Also:**
- `CLAUDE.md` - Additional implementation notes
- `docs/e2e-verification-corrected.md` - Runtime verification findings
- `docs/production-readiness-final-report.md` - Production assessment
