# Prompt 10.5 — Final Remediation Report

**Date:** 2026-08-15/16
**Scope:** fix the verified issues from the Prompt 9A/9B/10 audit trail, then
prove the fixes work with real execution — not another round of documentation.

**Governing principle applied throughout:** IMPLEMENTATION > DOCUMENTATION >
PREVIOUS REPORTS. Every claim below was checked against the actual repository
or an actual command run during this pass. Where a prior report (`docs/known-issues.md`,
`docs/e2e-verification-corrected.md`, `docs/production-readiness-final-report.md`,
`docs/prompt-10-completion-summary.md`) turned out to be wrong, that is called
out explicitly rather than silently corrected.

---

## 1. Executive summary

Of the 8 numbered issues in the remediation checklist, **3 were already
fixed** in the (uncommitted) working tree before this pass started — the
JWT production fail-safe, the Alembic revision chain, and the DB schema/enum
test assertions. The prior audit reports describing them as broken were
stale or, in one case, referenced a file that doesn't exist
(`backend/app/infrastructure/auth/jwt_signer.py`).

That said, **running things for real instead of trusting that they'd work
found five genuine, previously-undiscovered bugs** that no static review had
caught, because none of the static reviews had actually executed the code in
question:

1. A migration (`0004_outcome_measurement`) that aborted every fresh-database
   upgrade via a Postgres transaction poisoned by a `try/except/pass` around
   `DROP CONSTRAINT`.
2. A second migration (`0005_llm_usage_tracking`) that collided with
   genesis's `Base.metadata.create_all()` on any fresh database.
3. A column default (`AlertRule.min_severity_notify = 'warn'`) that violated
   its own check constraint — the seed scripts had never been run to
   completion before, so nothing had ever tripped it.
4. The backup script's own success-verification step failed on every
   invocation, always, regardless of whether the backup itself worked
   (`pipefail`/SIGPIPE interaction with `head`/`grep -q`).
5. The restore script's `--single-transaction` flag is incompatible with
   `DROP DATABASE`/`CREATE DATABASE`, which the paired backup always produces
   — restore failed immediately, every time, before this pass.

All five are fixed and re-verified. A sixth, real but out-of-scope-to-fix
issue was also found: restoring a backup into a *differently-named* database
doesn't work, because of how `pg_dump --create` embeds the source database
name — documented as a limitation, not silently patched around.

Also found and fixed, not originally in the checklist: `scripts/check_env.py`
and `scripts/check_ports.py` — two scripts CLAUDE.md explicitly requires be
run and kept clean — were both actually failing (17 undocumented environment
variables, one misconfigured port allowlist). A broken package
(`app.services.outcomes`) that raised `ModuleNotFoundError` for anyone who
imported it was found via `mypy` and fixed. `make lint`'s `ruff format
--check` (part of what CLAUDE.md says CI runs) had 18 files out of date.

## 2. Issues before remediation (checklist as given)

See `docs/prompt-10.5-remediation.md` for the full checklist with per-item
status determined by direct inspection, not by trusting the prior audit
narrative. Short version: P0(1) already fixed in code, needed tests. P1(2)
already fixed structurally, needed real execution to find the two bugs
above. P1(3) never executed. P2(4) partially stale description, real bugs
underneath. P2(5,6) already correct. P3(7) undercounted (60 vs. 11 claimed).
P3(8) not previously reviewed.

## 3. Fixes performed

| # | File(s) | What changed |
|---|---|---|
| 1 | `backend/tests/unit/test_auth_security.py` | 4 new tests proving multi-worker JWT behavior (cross-worker verify, restart survival, dev-only ephemeral-key limitation, prod fail-fast). Fixed a cwd-dependent test-isolation bug these tests exposed: `AuthSettings()` picks up a real repo-root `.env` when pytest runs from the repo root (which `make test` does), so the "unconfigured key" tests need to explicitly neutralize that. |
| 2 | `backend/app/infrastructure/db/migrations/versions/202608131600_outcome_measurement.py` | Replaced 3× `try: op.drop_constraint(...) / except Exception: pass` with `op.execute("...DROP CONSTRAINT IF EXISTS...")`, and the try/except-guarded index creation with `CREATE INDEX IF NOT EXISTS`. Wrapped two now-long constraint strings for line length. |
| 2 | `backend/app/infrastructure/db/migrations/versions/202608131800_llm_usage_tracking.py` | Added `_exists()` guard before `op.create_table`, matching the existing pattern in `202608061347_recommendation_decisions.py`. |
| 2 | `backend/app/infrastructure/db/models/config.py` | `AlertRule.min_severity_notify` default `'warn'` → `'medium'` (valid `Severity` enum member). |
| 2 | `backend/tests/integration/test_migrations.py` | Table-count assertion 44 → 45 (real count, verified against a disposable Postgres). |
| 4 | `data_platform/tests/unit/test_dagster_orchestration.py` | Rewrote 14 failing tests: real `CliExecutor`/`DuckDBWarehouse` instances with `patch.object` instead of `Mock(spec=...)` (Dagster's direct-invocation resource-type check rejects the mock); `AssetsDefinition` isinstance check instead of a nonexistent `_op_def` attribute; `resolve_all_asset_specs()` instead of removed `get_all_asset_specs()`; `.value` on `AssetCheckResult` metadata; real temp-file DuckDB paths instead of `:memory:` where connection state needs to persist; exact asset-key matching instead of a substring match that silently matched the wrong asset. |
| 4 | `data_platform/orchestration/dagster/schedules.py` | Removed 4 uses of deprecated `define_asset_job(partitions_def=...)`; confirmed Dagster still resolves the same partition scheme without it. |
| 6 | 60 ruff findings across ~15 files | See `docs/prompt-10.5-test-results.md` for the category breakdown. Real fix (not suppression) for the S110 in the migration file (item 2); `noqa` with stated reasoning for genuine false positives (internal SQL identifiers, Dagster-managed partition keys, fixed subprocess argv) consistent with the repo's existing `noqa` convention. |
| 6 | `ruff format` | 18 files reformatted (pure formatting, zero behavior change). |
| 6 | `backend/app/api/v1/recommendations.py` | 3× bare `-> dict:` → `-> dict[str, Any]:` (mypy `type-arg`). |
| 6 | `backend/app/services/outcomes/__init__.py` | Removed an import of a module that doesn't exist (`measurement.py` was never written); this had made `from app.services.outcomes import ...` raise `ModuleNotFoundError` for any caller — currently none, so latent rather than active, but real. Did not implement the missing service (new functionality, out of scope). |
| 7 | `backend/app/infrastructure/llm/validation.py` | Removed an unused, silently-ignored `schema` parameter and its dead commented-out validation code; docstring now states plainly that schema validation isn't implemented rather than implying it might run. |
| 8 (backup/restore) | `scripts/backup-postgres.sh` | Fixed the `pipefail`/SIGPIPE false-failure in the post-backup verification step. |
| 8 (backup/restore) | `scripts/restore-postgres.sh` | Removed `--single-transaction` (incompatible with the dump's embedded `DROP DATABASE`/`CREATE DATABASE`); fixed a `stat -f` cross-platform bug that showed garbage instead of the backup's date on Linux. |
| found, not in checklist | `.env.example` | Added 10 LLM-gateway variables and 7 Alertmanager-routing variables that `scripts/check_env.py` found undocumented; removed a stale, wrong "not yet wired" LLM block (wrong variable names, wrong claim about API calls). |
| found, not in checklist | `scripts/check_ports.py` | Added `alertmanager` (127.0.0.1:9093) to the production port allowlist — deliberate, SSH-tunnel-only, same rationale already documented for Grafana; the script's own allowlist just hadn't been updated when Alertmanager was added. |
| found, not in checklist | `backend/tests/integration/test_calibration_api.py` | Fixed a fixture-collection error (`auth_headers` used as a fixture parameter; it's a plain async helper everywhere else in the suite) that made all 5 outcome-related tests error before running at all. Also fixed a punctuation mismatch. Found, but did **not** fix (out of scope — see §11), a real production bug underneath: the calibration API's "filter by generator" (`inventory | pricing | ...`) cannot work, for any caller, because the repository filters on `Recommendation.type`, which is constrained to an unrelated vocabulary (`reorder | markdown | promo | assortment`). |

## 4. Tests before vs. after

See `docs/prompt-10.5-test-results.md` for the full table with exact
commands. Headline: fast ladder 976/977 stable before and after (the one
failure is pre-existing and out of scope); Dagster unit 7/21 → 21/21;
migration integration 0/6 executed successfully → 6/6 (never run before this
pass); `ruff check .` 60 → 0; `ruff format --check` 18 files stale → 0;
`mypy` 4 errors → 0; `check_env.py`/`check_ports.py` both failing → both
passing; full backend+data-platform Docker integration suite (331 tests:
327 backend + 4 dbt) 326/331 → **328/331 final**, confirmed by a full
standalone re-run of `backend/tests/integration` (324/327, 16m14s,
testcontainers). The remaining 3 failures are all in
`test_calibration_api.py`, deterministic (identical result across two
separate runs), and traced to a real production bug this pass found but
did not fix — see §11.

## 5. Migration verification

`alembic heads` → exactly one head (`0005_llm_usage_tracking`). Full
`upgrade base → head`, `downgrade head → base`, `upgrade base → head` cycle
run twice — once manually against a disposable `postgres:16.4` container,
once via `backend/tests/integration/test_migrations.py`'s testcontainers
fixture (6/6 passing, including `test_downgrade_round_trip`) — both times
with a stable 45-table count and no data corruption. Production
`rmprod-postgres-1` was never connected to for any of this.

## 6. JWT verification

`backend/app/core/security.py::_load_or_generate_keypair` +
`AuthSettings.require_configured_keys` already implement everything Phase 2
asked for. Configuration, confirmed by reading (not assuming) `infra/compose/compose.prod.yml`:
`RM_AUTH_JWT_PRIVATE_KEY_FILE` → a Docker secret shared by every
worker/replica in production; outside `dev`, an unconfigured key is a hard
`RuntimeError` at startup. Four new tests prove: worker B verifies worker A's
token when both share a configured key; a "restarted" (freshly-constructed)
signer still accepts pre-restart tokens under a configured key; two
independent unconfigured signers do *not* cross-verify (the documented
dev-only limitation, unchanged and correctly still present); and a missing
key outside `dev` raises `RuntimeError` rather than silently minting one.

## 7. Dagster verification

`data_platform/orchestration/dagster` `defs` object loads successfully:
104 asset specs resolve, jobs/schedules/sensors all present
(`daily_ingestion`, `dbt_build`, `backfill_ingestion` among the jobs;
`daily_ingestion_schedule`, `daily_dbt_schedule`, `weekly_forecast_schedule`
among the schedules; `failed_partition_retry`, `quality_quarantine_alert`
among the sensors — all asserted by the now-passing test suite, not just
inspected). 21/21 unit tests pass. Note for CLAUDE.md maintenance: its
"Things that specifically do not exist" table currently says "Airflow /
Dagster orchestration | None. Pipelines run via CLI" — this is no longer
accurate; real, working, tested Dagster orchestration exists in the
(uncommitted) working tree. Not corrected in CLAUDE.md by this pass — flagged
per its own instruction to re-verify rather than trust the list.

## 8. Backup/restore verification

Full writeup with real timings, sizes, and row counts in
`docs/backup-restore-test-results.md`. Headline: both scripts had never
actually been executed before this pass and both failed immediately when
they were; both are now fixed and proven for the realistic disaster-recovery
scenario (restore a database's own backup back into itself — 45 tables, 7
users, 5 alert rules, matching Alembic revision, all verified identical
before/after). Restoring into a renamed target database does not work and is
documented as a real limitation, not fixed. Production was never touched.

## 9. Security verification

- No secrets committed: `infra/secrets/*` correctly `.gitignore`d except
  `README.md`; the local JWT/DB/etc. secret files present on disk are
  untracked, generated material.
- JWT signing/verification: see §6.
- RBAC / permissions: `backend/tests/unit/test_auth_security.py`'s
  `RequireHasAny`-style tests (`test_require_*`) pass unchanged; not
  re-audited beyond what already existed, since RBAC wasn't in the P0–P3
  checklist.
- Rate limiting / idempotency: present (`backend/app/core/rate_limit.py`,
  `backend/app/core/idempotency.py`), passed `ruff`/`mypy` cleanly; not
  functionally re-tested in this pass (out of the explicit checklist).
- Production port surface: `scripts/check_ports.py` now passes — edge
  (80/443), Grafana and Alertmanager on loopback only. Verified against the
  actual rendered `docker compose config`, not by reading the YAML and
  assuming.
- `/metrics` on the public edge: confirmed `404` (nginx `deny all`), by an
  actual `curl` against the running `rmprod` stack, not by reading the
  config.
- `/api/docs`: confirmed `404` in this `RM_APP_ENV=prod` deployment, by an
  actual `curl`.

## 10. Runtime verification

Against the **already-running** `rmprod-*` Docker stack (not started or
stopped by this pass; nothing in it was modified):

| Component | Check | Result |
|---|---|---|
| PostgreSQL | `/readyz` executes a real `SELECT 1` against it | 🟢 `{"status":"ok"}` |
| Redis (state) | Celery worker/beat actively consuming/scheduling tasks | 🟢 tasks received and completed in worker logs |
| FastAPI | `/healthz`, `/readyz` via the edge | 🟢 200 both |
| Nginx/edge | HTTP→HTTPS redirect, UI reachable, API routes reachable | 🟢 301, 200, 200 |
| Celery worker | log tail shows tasks executing successfully | 🟢 |
| Celery beat | log tail shows scheduled dispatch on schedule | 🟢 |
| Prometheus | internal `/-/healthy` via `docker exec` | 🟢 |
| Grafana | `/api/health` on loopback | 🟢 `{"database":"ok"}` |
| Dagster | `defs` loads, assets/jobs/schedules resolve (see §7) | 🟢 — checked via the test suite against the source tree, not against a running Dagster daemon (none is part of the `rmprod` stack) |
| Streamlit UI | reachable via edge, returns real HTML | 🟢 200 |
| Alertmanager, MinIO | containers healthy (`docker ps`) | 🟢 container-level only; not functionally exercised |

Not claimed as verified merely because a container was "Up (healthy)" —
every 🟢 above has an actual command and actual output behind it, listed in
the table.

## 11. Remaining known issues

- **The calibration API's "filter by generator" cannot work, for any
  caller.** `OutcomeRepository.find_measured()` filters on
  `Recommendation.type`, constrained by the DB to `reorder | markdown |
  promo | assortment`; the calibration API's own docs describe "generator"
  as `inventory | pricing | promotion | store | customer | supplier` — a
  disjoint vocabulary. `/recommendations/calibration/generators/{generator}`
  and the confidence/summary endpoints' per-generator breakdowns are affected.
  Found via `backend/tests/integration/test_calibration_api.py` (3 tests
  fail on this, honestly, not adjusted to pass). Real production bug, not a
  test bug — but fixing it means deciding whether `Recommendation.type`
  becomes the seven-category vocabulary or a separate `category` column gets
  added, which is a data-model decision for whoever owns the
  outcome-measurement/calibration feature, not something to guess at during
  a remediation pass focused on a different checklist.
- `ui/tests/test_workspaces.py::test_command_center_loads_without_error` —
  pre-existing, unrelated to this remediation's scope. See
  `docs/prompt-10.5-test-results.md`.
- Restore into a renamed target database — real, documented limitation of
  the `--create`-format dump. See §8.
- CLAUDE.md's "does not exist" table is stale on two points (LLM gateway,
  Dagster orchestration) as of the uncommitted working tree. Flagged, not
  corrected by this pass — that file is project-level guidance, not
  something to rewrite mid-remediation without being asked.
- `docs/production-readiness-final-report.md` and other pre-existing docs
  contain conclusions reached without running the commands they cite (see
  the correction banner added to that file). Historical content preserved;
  corrected pointers added rather than rewritten.
- Backup cron schedule, offsite backups, k6 load testing — not exercised
  this pass; see `docs/prompt-10.5-test-results.md` "Not (yet) run".
- `backend/app/infrastructure/db/migrations/env.py` reads its connection
  string from `RM_DB_*` settings rather than the Alembic `Config` object
  passed to it — found while investigating an orphaned duplicate test file
  (`tests/integration/test_migrations.py` at the repo root, distinct from
  `backend/tests/integration/test_migrations.py`) that assumed the opposite
  and has therefore never actually run successfully. That root-level file
  duplicates coverage `backend/tests/integration/test_migrations.py` already
  provides (now passing) plus one thing it doesn't (per-migration-step
  upgrade/downgrade, not just the full chain); it is not referenced by
  `pyproject.toml`'s `testpaths` or by any Makefile target, so it does not
  run under `make test` or `make test-integration` today. Ruff-clean now;
  left unwired rather than force-integrated, since doing so would mean
  either changing the Makefile's test targets or reworking the fixture's
  connection-URL assumption — both judgment calls beyond this remediation's
  mandate. Documented here so it isn't rediscovered as a surprise.

## 12. Production blockers

**None found that are still open.** Every P0/P1 item either was already
resolved or is resolved as of this pass, with real execution proving it, not
static inspection. The two items closest to "blocker" — the migration chain
actually failing on a fresh database, and backup/restore never having
worked — are exactly what this pass exists to catch, and both are fixed and
re-verified.

## 13. Recommended next step

1. Commit this work. The working tree has been uncommitted since (at least)
   the Prompt 9A/9B/10 phases; that is its own risk (176 modified files,
   ~70 untracked files, no ability to `git bisect` or roll back a specific
   change).
2. Decide on CLAUDE.md's two stale claims (LLM gateway, Dagster) — either
   update the "does not exist" table or confirm those features are meant to
   be reverted before shipping.
3. Fix or retire the orphaned root-level `tests/integration/test_migrations.py`
   (§11) — either wire it into `testpaths`/a Makefile target after fixing
   its connection-URL assumption, or delete it in favor of the
   testcontainers-based `backend/tests/integration/test_migrations.py`,
   which now covers the same ground and more reliably.
4. Investigate the pre-existing UI test failure (§11) as a small, separate,
   UI-scoped fix.
5. If renamed-target database restores become an actual requirement, budget
   for the `pg_restore`/custom-format change described in §8 — it's a real
   design change, not a quick patch.
6. Decide the calibration API's category/generator data model (§11) —
   whether `Recommendation.type` becomes the seven-category vocabulary the
   API already documents, or a separate `category` column is added — then
   fix `OutcomeRepository.find_measured()`'s filter accordingly. Until then,
   `/recommendations/calibration/generators/{generator}` and the
   per-generator breakdowns on the other calibration endpoints don't work
   for any caller, not just the test suite.

---

## Final release gate

1. **Any CRITICAL production blockers?** No.
2. **Any HIGH production blockers?** No.
3. **Does Alembic have exactly one valid head?** Yes — `0005_llm_usage_tracking`, verified via `alembic heads`.
4. **Can migrations upgrade and downgrade in an isolated database?** Yes — verified twice, independently, against disposable Postgres instances (manual + testcontainers).
5. **Do all Dagster orchestration tests pass?** Yes — 21/21.
6. **Do database schema tests pass?** Yes — 131/131 (`test_db_schema.py`), plus the integration-level table-count assertion, corrected and passing.
7. **Does Ruff pass?** Yes — `ruff check .` and `ruff format --check` both clean.
8. **Does JWT work correctly with multiple workers?** Yes — proven by 4 new tests, backed by code that was already correct.
9. **Was backup + restore actually executed safely?** Yes, both, against disposable containers only. Restore into a renamed target is confirmed unsupported (documented, not silently claimed working).
10. **Does the complete available test suite pass?** Fast ladder: 976/977 (1 pre-existing, out-of-scope, documented). Migration integration: 6/6. Backend+data-platform integration Docker suite: **328/331 final**, confirmed by a full re-run — the remaining 3 are one deterministic, root-caused, out-of-scope, documented production bug (§11), not adjusted to pass and not left unexplained.
11. **Does the runtime stack still operate after the fixes?** Yes — verified against the live `rmprod` stack with real requests/checks, not container-status inspection alone. Nothing in that stack was modified.
12. **Is documentation synchronized with the actual repository?** `docs/known-issues.md` updated with RESOLVED markers, resolution dates, and fix references for every item; `docs/backup-restore-test-results.md` rewritten with real results; `docs/production-readiness-final-report.md` given a correction banner (history preserved, not rewritten); `.env.example` brought back in sync with the settings model. `README.md`'s pre-existing false claims (a separately-flagged, separately-planned rewrite per CLAUDE.md) were not touched — out of this pass's mandate.

## Verdict

🟡 **READY WITH DOCUMENTED LIMITATIONS**

Every item in the original P0–P3 checklist is resolved or was already
correct, backed by real execution rather than static review, and six
additional real bugs this pass found on its own are also fixed (five listed
in §1, plus the `test_calibration_api.py` fixture-collection error in §3/§4).
The full backend+data-platform Docker integration suite was confirmed by a
complete re-run: **328/331 passing**. The qualifier is for what's honestly
still open, all documented rather than hidden or forced to pass: one
pre-existing, out-of-scope UI test failure; one real production bug in the
calibration API's generator-filtering (3 deterministic test failures,
root-caused, not patched — see §11); one real, documented restore
limitation (renamed-target databases); two stale claims in CLAUDE.md this
pass flagged but didn't correct; and an orphaned duplicate migration-test
file. None of the six are P0/P1, and none block a release on their own —
each has a clear, scoped next action in §13.
