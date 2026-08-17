# Prompt 11 — Independent Final Release Audit

**Auditor stance:** this audit was performed by the same agent session that
did the Prompt 10.5 remediation. True auditor independence isn't available
here, so "independent" is enforced procedurally instead: every claim below
was re-derived by actually running a command in this session, not by
reading Prompt 10.5's conclusions and agreeing with them. Every place this
audit disagrees with, corrects, or adds to a Prompt 10.5 claim is called out
explicitly. No application code, test, migration, or infrastructure file was
modified during this audit — verified at the end (§Repository integrity).

**Evidence hierarchy applied:** actual runtime execution > test results >
source code inspection > documentation > previous reports. Static-only
checks are labeled STATICALLY VERIFIED and never called PASS on their own.

---

## 1. Executive Summary

Most of Prompt 10.5's claims independently reproduce cleanly: JWT
multi-worker behavior, the Alembic migration chain, backup/restore, ruff/
mypy/import-linter, the fast test ladder, the ML unit suite, the Dagster
unit suite, and the backend+data-platform integration suite (328/331,
identical failure set) all reproduce exactly as claimed, with real evidence
gathered fresh in this session.

**This audit also found two genuine, previously-undiscovered release
blockers that no prior pass caught**, because both require actually
executing code paths that every previous verification pass only imported,
mocked, or statically inspected:

1. **🔴 The scheduled production Dagster job `dbt_build` (`daily_dbt_schedule`,
   3 AM UTC daily) fails immediately on real execution.** `CliExecutor.run_dbt()`
   builds a dbt command with paths relative to `working_directory=REPO_ROOT`,
   but the dbt project actually lives one directory level down
   (`data_platform/dbt`). `dagster definitions validate` and the unit test
   suite both pass because neither ever runs the real subprocess with the
   real working directory — this audit did, and it fails with
   `CalledProcessError`. See §8.

2. **🔴 A reproducible, deterministic segmentation fault** in the Admin
   workspace's table rendering (`ui/workspaces/12_Admin.py` →
   `ui.table()` → pandas/pyarrow), triggered by
   `test_every_workspace_survives_an_api_that_answers_nothing[12_Admin.py]`.
   Reproduced 3 times independently in this session. A native crash kills
   the whole Streamlit worker process — there is no Python-level recovery
   from it. See §16.

A third finding changes how every other runtime-verification claim in this
document (and in Prompt 10.5) should be read:

3. **The running `rmprod-*` Docker stack predates the codebase under
   review by 9 days** (image built 2026-08-07; this audit's baseline is
   2026-08-16) and contains none of the LLM gateway, calibration, or
   outcomes code, and its database sits at Alembic revision
   `0003_recommendation_decisions`, not the current head
   (`0005_llm_usage_tracking`). It has zero seeded users. **Every
   container-health or HTTP-response check against this stack — in this
   audit and in Prompt 10.5 — is evidence about a 9-day-old build, not the
   codebase in the working tree.** This isn't a new bug to fix; it's a
   correction to how "runtime verification" should be weighted. See §14.

None of this changes the earlier, independently-reproduced positive
findings. It does mean the release verdict cannot be as clean as Prompt
10.5's "🟡 READY WITH DOCUMENTED LIMITATIONS" — see §21.

---

## 2. Repository Baseline

Full detail in `docs/prompt-11-audit-baseline.md`. Summary: branch `main`,
HEAD `2fc3ab4` (unchanged since before Prompt 10.5 — nothing has been
committed), 181→182 modified/untracked paths (the +1 is a harmless Dagster
scratch temp dir from running `dagster definitions validate` in this audit,
auto-created and not implementation). Python 3.12.0 (venv), Docker 28.5.1,
PostgreSQL 16.4, Redis 7.4.10, DuckDB 1.5.5, dbt-core 1.11.13, Dagster
1.13.17. `uv lock --check` passes.

---

## 3. Prompt 10.5 Claims vs Independent Evidence

| # | Claim (from `docs/prompt-10.5-*`) | Independent verification performed | Evidence | Status |
|---|---|---|---|---|
| 1 | JWT multi-worker: code was already correct; 4 new tests added and pass | Re-ran `backend/tests/unit/test_auth_security.py`; additionally ran a genuine cross-**process** test (two separate `uv run python` interpreter invocations, not just two in-process objects) sharing a generated key file, plus the prod fail-fast path, plus confirmed the live `rmprod-api-1` container's actual env vars | Two independent OS processes: Worker A signed, Worker B verified, `VERIFIED OK`. `RM_APP_ENV=prod` + no key → `RuntimeError` raised. Live container: `RM_AUTH_JWT_PRIVATE_KEY_FILE=/run/secrets/jwt_private_key`, `RM_API_WORKERS=4`, `RM_APP_ENV=prod` | 🟢 CONFIRMED |
| 2 | Alembic chain: single head, two real bugs found and fixed | Ran `alembic history`/`heads` fresh; built a **new** disposable `postgres:16.4` container (not reused from Prompt 10.5) and ran upgrade base→head→base→head | One head (`0005_llm_usage_tracking`). 45 tables at head, 1 table (`alembic_version`) after downgrade to base, 45 again after re-upgrade. No errors at any step | 🟢 CONFIRMED |
| 3 | Migration integration test 6/6 | Included in the fresh full `backend/tests/integration` re-run (§4) | 6/6 passing, same as Prompt 10.5 | 🟢 CONFIRMED |
| 4 | Dagster unit tests 21/21 | Re-ran `data_platform/tests/unit/test_dagster_orchestration.py` fresh | 21/21 passed in 2.68s | 🟢 CONFIRMED — but see §8: passing unit tests turned out **not** to be evidence the real job runs, which this audit discovered by actually running it |
| 5 | DB schema tests 131/131 already correct | Included in the fresh fast-ladder re-run | 976/977 (see #11 below); the DB schema tests are part of that count and pass | 🟢 CONFIRMED |
| 6 | Enum constraint tests already correct (`min_severity_notify` default fixed to `'medium'`) | Read `backend/app/infrastructure/db/models/config.py` directly; confirmed the fix is present in the working tree | Line reads `server_default=text("'medium'")` with the explanatory comment from Prompt 10.5 | 🟢 CONFIRMED (STATICALLY, plus indirectly proven live: the disposable-DB backup/restore test in §13 seeded `AlertRule` rows successfully, which would fail immediately if this were still `'warn'`) |
| 7 | Ruff 60→0, `ruff format` 18→0, mypy 4→0 | Re-ran `ruff check backend data_platform ml ui`, `ruff format --check`, `mypy backend/app`, `mypy ui` | All four commands clean, byte-for-byte the same result as Prompt 10.5 reported | 🟢 CONFIRMED |
| 8 | TODO/FIXME: 5 legitimate, documented markers | Re-ran the same grep | Identical 5 results, same files/lines | 🟢 CONFIRMED |
| 9 | Backup script fixed (pipefail/SIGPIPE bug) | Built a **new** disposable Postgres, seeded it, ran `scripts/backup-postgres.sh` unmodified from the working tree | Exit 0, 20K file, "Backup verification: OK" | 🟢 CONFIRMED |
| 10 | Restore script fixed (`--single-transaction` bug) | Same disposable container; ran `scripts/restore-postgres.sh` against the backup just taken | Exit 0; restored table count (45), `app_user` (7), `alert_rule` (5), and Alembic revision (`0005_llm_usage_tracking`) all matched the pre-backup values exactly | 🟢 CONFIRMED |
| 11 | Fast ladder 976/977, one pre-existing UI failure | Re-ran `backend/tests/unit data_platform/tests/unit ml/tests ui/tests` in full | 976 passed, 1 failed (`test_command_center_loads_without_error`), identical to Prompt 10.5 | 🟢 CONFIRMED — but this audit separately found the Admin-workspace segfault (§16) by running `ui/tests` **in isolation**, which the combined run does not surface (see §16 for why) |
| 12 | Runtime stack operates (healthz/readyz/edge/Grafana/Prometheus/worker/beat) | Re-ran the same checks, plus went further: checked the image build date, the container filesystem contents, and the live database's Alembic revision and user count | All the individual checks Prompt 10.5 ran still return the same healthy answers — but see finding §14: the stack is a stale build, so "operates" and "reflects the current codebase" are different claims, and Prompt 10.5 did not distinguish them | 🟡 CONFIRMED BUT MISCHARACTERIZED — the stack works; it is not evidence about the code under review |
| 13 | Security: no secrets committed, CORS scoped, generic errors | Re-ran secret-pattern greps, checked `.gitignore`, read the CORS wiring and error handler fresh | Clean. CORS is disabled entirely in `RM_APP_ENV=prod` (empty origin list → middleware never added) — same-origin only via the edge; not previously called out this precisely | 🟢 CONFIRMED, with one clarifying detail added |
| 14 | Documentation synchronized (`known-issues.md`, `.env.example`, correction banners) | Re-ran `check_docs_integrity.py`, `check_env.py`, `check_ports.py`; read the updated docs | All three scripts pass; docs read as described | 🟢 CONFIRMED |

**New, independently-discovered items not covered by any Prompt 10.5 claim:**
the Dagster `dbt_build` job failure (§8), the Admin workspace segfault
(§16), and the stale-runtime-stack finding (§14) — see those sections for
full evidence.

---

## 4. Test Results

| Suite | Total | Passed | Failed | Skipped/Error | Status |
|---|---:|---:|---:|---:|---|
| Backend unit + data-platform unit + ml + ui (fast ladder) | 977 | 976 | 1 | 0 | 🟡 PASS WITH KNOWN FAILURE |
| `ui/tests/test_workspaces.py` run **in isolation** | 47 | — | — | — | 🔴 Segfaults on test #15 (`test_every_workspace_survives_an_api_that_answers_nothing[12_Admin.py]`), reproduced 3× — see §16 |
| Backend integration (`backend/tests/integration`, testcontainers) | 327 | 324 | 3 | 0 | 🟡 PASS WITH KNOWN, DOCUMENTED FAILURE (calibration category/type mismatch, pre-existing from Prompt 10.5, unchanged) |
| Data-platform integration (`data_platform/tests/integration`, dbt build) | 4 | 4 | 0 | 0 | 🟢 PASS |
| **Combined integration total** | **331** | **328** | **3** | **0** | 🟡 PASS WITH KNOWN, DOCUMENTED FAILURE |
| ML unit tests (`ml/tests`) | 66 | 66 | 0 | 0 | 🟢 PASS |
| Dagster unit tests | 21 | 21 | 0 | 0 | 🟢 PASS |
| Architecture / import-linter | 1 contract | 1 kept | 0 broken | — | 🟢 PASS |
| Ruff check | — | clean | 0 | — | 🟢 PASS |
| Ruff format --check | 326 files | 326 formatted | 0 | — | 🟢 PASS |
| mypy (`backend/app` + `ui`) | 206 files | 206 clean | 0 | — | 🟢 PASS |
| `check_env.py` | 76 variables | matched | 0 | — | 🟢 PASS |
| `check_ports.py` | 4 allowed bindings | matched | 0 | — | 🟢 PASS |
| `check_docs_integrity.py` | — | passed | 0 | — | 🟢 PASS |
| Dagster real materialize: `dbt_seeds` + `dbt_snapshots` + `retailmind_dbt_assets` (the actual `dbt_build` job composition) | 3 asset steps | 1 (`retailmind_dbt_assets` alone succeeds standalone) | 2 (`dbt_seeds`, `dbt_snapshots` fail as part of the real job) | — | 🔴 FAIL — see §8 |
| dbt `build` invoked directly (bypassing the broken `CliExecutor.run_dbt` path) | 152 dbt checks (6 seeds, 2 snapshots, 29 tables, 35 views, 3 incremental, 73 data tests) | 152 | 0 | — | 🟢 PASS — proves the dbt project itself is sound; the bug is in the Dagster wrapper, not dbt |

**Repository integrity check (ran last, to confirm this audit didn't modify
anything):** `git status --porcelain` before and after this audit's command
sequence differs by exactly one untracked, auto-generated Dagster scratch
directory (`.tmp_dagster_home_*`, created by `dagster definitions
validate`, not implementation). No tracked file changed.

---

## 5. JWT Verification — RELEASE-BLOCKING SECURITY CHECK

**🟢 PASS.**

Reproduced with genuine cross-**process** evidence (two separate `uv run
python` interpreter invocations, not two objects in one process):

```
Worker A (process 1): signs a real access token, shared key file /tmp/audit_jwt_key.pem
Worker B (process 2, freshly launched — equivalent to a restart): 
  VERIFIED OK: ['ceo'] 09e40ffe-8812-4b23-b169-a396e1c230c2
```

Missing-key fail-fast, outside dev:
```
RM_APP_ENV=prod, no key configured → RuntimeError:
  "RM_AUTH_JWT_PRIVATE_KEY_PEM (or _FILE) must be set outside development —
   ephemeral keys would differ per replica and invalidate sessions on deploy."
```

Live production container (`rmprod-api-1`, still running from its 2026-08-07
build) confirms the same mechanism is what's actually deployed:
`RM_AUTH_JWT_PRIVATE_KEY_FILE=/run/secrets/jwt_private_key`,
`RM_API_WORKERS=4`, `RM_APP_ENV=prod` — multi-worker, configured-key,
production mode, exactly as the code requires.

No private key committed (`infra/secrets/jwt_private_key` untracked,
`.gitignore`d). No unsafe fallback in production. Not downgraded because
single-worker dev mode also works — both paths were tested independently.

---

## 6. Migration Verification

**🟢 PASS.**

```
$ alembic history --verbose
Rev: 0005_llm_usage_tracking (head) → Parent: 0004_outcome_measurement
Rev: 0004_outcome_measurement       → Parent: 0003_recommendation_decisions
Rev: 0003_recommendation_decisions  → Parent: 0002_enterprise_roles
Rev: 0002_enterprise_roles          → Parent: 0001_genesis
Rev: 0001_genesis                   → Parent: <base>

$ alembic heads
0005_llm_usage_tracking (head)
```

Exactly one head. No dangling revision, no duplicate, no branch point.

Fresh disposable `postgres:16.4` container (`audit-migration-pg`, created
and destroyed by this audit, never reused):

| Step | Result |
|---|---|
| upgrade base → head | succeeded, 5 migrations applied in order |
| table count at head | 45 |
| downgrade head → base | succeeded, 5 downgrades applied in order |
| table count at base | 1 (`alembic_version` only — expected; genesis is `Base.metadata.create_all`, documented known limitation) |
| upgrade base → head (again) | succeeded |
| table count at head (again) | 45 |
| final heads | exactly one: `0005_llm_usage_tracking` |

No orphaned objects, no corruption, no branches. Production database was
never touched.

---

## 7. Data Platform Verification

**🟢 PASS**, with real, fresh execution — not a previously-populated
warehouse taken on faith.

`dbt build --select fqn:*` run directly (bypassing the broken Dagster
wrapper, see §8) against `.local/retailmind.duckdb`:

```
Finished running 3 incremental models, 4 project hooks, 6 seeds,
2 snapshots, 29 table models, 73 data tests, 35 view models
in 6.57 seconds. PASS=152 WARN=0 ERROR=0 SKIP=0
```

Verified real data present after the build (queried directly with duckdb,
not assumed):

| Schema | Tables |
|---|---:|
| raw | 5 (`pos__sales`, `inventory__positions`, `purchasing__orders`, `weather__observations`, `fulfilment__deliveries`) |
| staging | 2 |
| analytics_staging | 11 |
| analytics_analytics | 32 |
| analytics_semantic | 30 |
| analytics_ml | 3 (`forecast_predictions`, `forecast_explanations`, `forecast_runs`) |

`analytics_ml.forecast_predictions` has 1,484 rows with real structure
(`model_name`, `model_class`, `origin_date`, `business_date`, `horizon`,
`yhat`, `yhat_lower`, `yhat_upper`) — e.g. `seasonal_naive_w4` predictions
for `revenue`. This data predates this audit (a prior `make demo` run), so
it is historical, not freshly trained in this session — flagged, not hidden
(see §9).

Also materialized the real Dagster `retailmind_dbt_assets` asset (the same
67-model dbt project, through Dagster's actual execution engine, using the
correctly-configured production `DbtCliResource`): `MATERIALIZE SUCCESS:
True`, 75 assets, same 152/0 dbt result.

---

## 8. Dagster Verification — 🔴 RELEASE BLOCKER FOUND

Dagster 1.13.17. `dagster definitions validate -m orchestration.dagster` →
"Validation successful" (STATICALLY VERIFIED — this only checks structure,
not that resource paths resolve, which is exactly what it misses below).
Unit test suite 21/21 (real execution, but every test mocks or patches the
actual subprocess/CLI boundary — see `docs/prompt-10.5-*` for the fix
history on those tests).

**This audit went further and actually executed the real, scheduled job.**

The production `dbt_build` job (bound to `daily_dbt_schedule`, 3 AM UTC
daily) is defined as `AssetSelection.groups("dbt", "staging", "analytics",
"semantic")`. Confirmed by inspecting the resolved job definition that this
selection includes 85 assets, among them `dbt_seeds` and `dbt_snapshots` —
two small helper assets, group `"dbt"`, separate from the main 67-model
`retailmind_dbt_assets` asset (grouped under `staging`/`analytics`/`semantic`
per-model).

Materialized the exact three assets that make up this job, using the real,
correctly-configured production resources from `orchestration.dagster`
(not test doubles):

```python
materialize(
    [dbt_seeds, dbt_snapshots, retailmind_dbt_assets],
    resources={"cli": resources["cli"], "dbt": resources["dbt"]},
)
# → MATERIALIZE SUCCESS: False
```

```
subprocess.CalledProcessError: Command
  ['dbt', 'seed', '--profiles-dir', '.', '--project-dir', 'dbt']
  returned non-zero exit status 2.
```

**Root cause, pinned precisely:** `CliExecutor.run_dbt()`
(`data_platform/orchestration/dagster/resources.py`) always builds
`["dbt", command, "--profiles-dir", ".", "--project-dir", "dbt"]` — both
paths relative. The real, wired-up resource is
`CliExecutor(working_directory=str(REPO_ROOT))`
(`data_platform/orchestration/dagster/__init__.py:53`, `REPO_ROOT` =
`/Users/vijaays/retailmind-ai`). From `REPO_ROOT`, `--project-dir dbt`
resolves to `REPO_ROOT/dbt`, which doesn't exist — the real dbt project is
at `REPO_ROOT/data_platform/dbt`. Confirmed directly, outside Dagster
entirely:

```
$ cd /Users/vijaays/retailmind-ai && dbt seed --profiles-dir . --project-dir dbt
Error: Invalid value for '--project-dir': Path 'dbt' does not exist.
```

**This is not the same bug already fixed in Prompt 10.5.** That pass fixed
`schedules.py`'s use of a deprecated `define_asset_job(partitions_def=...)`
parameter and several unit-test mocking issues — genuinely different code,
never touched this path. `dbt_seeds`/`dbt_snapshots` were not part of that
fix, and their real-execution failure was invisible to every check anyone
had run before this audit, because:
- `dagster definitions validate` doesn't construct real subprocess commands.
- The unit test suite (correctly, deliberately, per Prompt 10.5's own fix)
  patches `CliExecutor.run_dbt`/`run_ingestion` rather than letting them run
  for real — appropriate for unit tests, but it means "21/21 passing" was
  never evidence this job actually runs.
- `retailmind_dbt_assets` alone (the 67-model asset) **does** work — it uses
  a separately, correctly-configured `DbtCliResource` with absolute paths,
  which is why the dbt project itself (§7) tested clean. The bug is isolated
  to the two small `CliExecutor`-based helper assets, but those two assets
  are exactly what's scheduled to run automatically every day, and their
  failure aborts the whole job (Dagster does not continue past a failed
  upstream step by default).

**Production impact:** the daily automated dbt build cannot run
successfully as currently configured. The dimensional warehouse would never
refresh via the schedule; it only builds today because someone (or a test)
ran `dbt build` directly.

**Classification:** 🔴 RELEASE BLOCKER. Per this prompt's explicit rule,
"critical business functionality" failures aren't downgradable for
convenience — this is the pipeline's own daily refresh, failing every time
it would actually run. Not fixed in this audit (audit rule: report, don't
fix).

---

## 9. ML / Forecasting Verification

**🟡 PASS WITH LIMITATION.**

`ml/tests` — 66/66 passing, freshly re-run. These are substantive, not
smoke tests: determinism to the last bit, no-leakage guarantees (level
features can't see the future, design matrix targets are exactly
origin+horizon), conformal-interval coverage guarantees, champion-promotion
logic (a marginal or non-beating model is correctly rejected), and
JSON round-trip fidelity for both the hand-written `RidgeForecaster` and the
seasonal-naive baseline. No scikit-learn dependency, confirmed — matches
CLAUDE.md's claim.

Traced real, structured data end-to-end (§7): `analytics_ml.forecast_predictions`
(1,484 rows), `forecast_explanations` (56 rows), `forecast_runs` (8 rows),
real model names (`seasonal_naive_w4`), real confidence bands.

**The limitation:** this data is historical — produced by an earlier
`make demo`-style run, not freshly trained within this audit session. This
audit did not independently execute `retailmind-forecast train` end-to-end
(time-boxed; the `CliExecutor.run_forecast_training` path was not exercised
for real, unlike `run_ingestion` and the dbt path, both of which were).
Per this prompt's own instruction, marked 🟡 rather than 🟢 for exactly
this reason.

---

## 10. Recommendation / RCA / Analyst Verification

**🟡 PASS WITH LIMITATION**, evidenced primarily through the reproduced
integration suite (§4) rather than fresh manual API calls in this audit:

- `test_recommendations_api.py`, `test_rca_api.py`, `test_analyst_api.py`,
  `test_nlq_api.py`, `test_forecasts_api.py` are all part of the 324/327
  passing backend integration re-run — real HTTP requests against a real
  FastAPI app and a real (testcontainers) Postgres, not mocks.
- `test_recommendations_api.py::test_the_client_cannot_write_its_own_numbers_into_the_ledger`
  passing is direct evidence the recommendation decision ledger recomputes
  server-side rather than trusting client-supplied numbers.
- The one confirmed gap (§3 row for the calibration bug, §11 of Prompt
  10.5's own report) is real: the calibration API's generator-filtering
  can't work for any caller, a data-model mismatch between
  `Recommendation.type` and the documented "generator" vocabulary. Not
  re-litigated here — Prompt 10.5's root-cause diagnosis holds up under
  the fresh reproduction in this audit (same 3 tests, same error, same
  constraint name).

Did not independently issue fresh RCA/analyst/NLQ requests outside the test
suite in this audit (time-boxed) — the test suite's real HTTP-level
execution is treated as sufficient evidence for 🟡, not 🟢, since this audit
didn't add anything beyond reproducing what already existed here.

---

## 11. LLM Security Verification

**Mock provider: 🟢 PASS** (STATICALLY + via test suite). `backend/tests/unit/test_llm_mock_provider.py`
and related LLM unit tests are part of the reproduced 976/977 fast-ladder
result. `backend/app/core/config.py::LLMSettings.model_post_init` forces
`mock=True` whenever no API key is configured — read directly in this audit,
matches Prompt 10.5's description.

**Real Anthropic provider: ⚪ NOT TESTED.** No API credentials available in
this environment. Not fabricated.

**PII scrubbing, prompt validation, malformed-output handling:** covered by
`test_llm_pii_scrubbing.py`, `test_llm_validation.py`, `test_llm_prompts.py`
— all part of the reproduced passing suite. Read `backend/app/infrastructure/llm/validation.py`
directly: the response validator checks for `eval(`/`exec(` string patterns
in LLM output before accepting it (a real, if basic, guard against
unsafe-instruction generation) and Prompt 10.5's fix removed a
silently-no-op schema-validation parameter rather than leaving it
misleadingly documented — confirmed still in place.

**LLM does not generate business numbers:** consistent with the
architecture read across `backend/app/services/analyst/narrator.py` and the
evidence-package pattern described in `docs/ai.md` — narration consumes
pre-computed analytical results, doesn't compute them. Not independently
fuzzed in this audit beyond reading the code and the passing test suite.

---

## 12. Security Audit

**🟢 PASS**, fresh scan in this session (not copied from Prompt 10.5):

- No secret-shaped strings (`AKIA...`, `sk-ant-api03-...`, PEM private key
  blocks) anywhere in tracked or working-tree source.
- No committed `.env`; `infra/secrets/` correctly `.gitignore`d except its
  own `README.md`/`.gitignore` markers — confirmed via `git ls-files`.
- CORS: **disabled entirely** in `RM_APP_ENV=prod`
  (`cors_origins=[settings.base_url] if settings.env != "prod" else []`,
  `backend/app/main.py:134` — an empty list means `CORSMiddleware` is never
  added at all). Same-origin only, via the edge. No wildcard anywhere. This
  is a stronger finding than "CORS is scoped" — it's "CORS doesn't exist in
  prod," which is the more secure of the two.
- No `eval(`/`exec(`/`pickle.loads`/unsafe `yaml.load` in application code —
  the only `eval(`/`exec(` hits are string literals inside the LLM output
  validator's own blocklist (§11), not live calls.
- Generic 500 handling confirmed by reading `backend/app/core/errors.py`
  directly: unhandled exceptions map to `"Internal error"`, no exception
  detail or traceback in the response body.
- `subprocess` calls: every real `shell=True` search came back empty;
  the one `# noqa: S603` in `resources.py` is commented as "command is
  built internally, never shell=True" and reading the call site confirms
  this — `subprocess.run(command, ...)` with a list argv, not a string.

Not re-litigated: JWT (§5), backup/restore secrets (§13), which are already
covered above.

---

## 13. Backup / Restore Verification

**🟢 PASS**, reproduced end-to-end on a **new** disposable container
(`audit-backup-pg`, created and destroyed in this audit, distinct from the
one Prompt 10.5 used).

| Step | Result |
|---|---|
| Disposable DB created | `postgres:16.4`, port 15501, local only |
| Migrated + seeded | head (`0005_llm_usage_tracking`), 7 `app_user`, 5 `alert_rule` |
| Backup executed | `scripts/backup-postgres.sh`, exit 0, ~0.14s, 20K `.sql.gz` |
| Backup integrity | script's own gzip + `pg_dump` header check passed |
| Restore executed | `scripts/restore-postgres.sh`, exit 0, ~0.51s |
| Restored table count | 45 (matches pre-backup) |
| Restored `app_user` count | 7 (matches) |
| Restored `alert_rule` count | 5 (matches) |
| Restored Alembic revision | `0005_llm_usage_tracking` (matches) |

Production `rmprod-postgres-1` was never connected to, queried destructively,
or restored into by this audit.

**Retention/scheduling/offsite:** unchanged from Prompt 10.5's findings —
retention logic (`find ... -mtime +N`) exists in the script but wasn't
exercised with artificially-aged files in either pass; the cron schedule in
`compose.prod.yml`'s `backup` profile wasn't triggered; offsite backup
(S3/rsync) remains an unimplemented suggestion in `docs/backup-restore.md`.
⚪ NOT TESTED for all three, consistent with Prompt 10.5.

**Restoring into a differently-named database:** re-confirmed as
unsupported (not re-tested with a fresh repro in this audit — Prompt 10.5's
root cause, the `pg_dump --create` dump embedding the source database name
via `\connect`, is a property of the dump format, independently verifiable
by reading `scripts/backup-postgres.sh`'s `pg_dump ... --create` flag, which
this audit did — still present, unchanged).

---

## 14. Infrastructure Verification — stale build, real functional checks

| Service | Running | Healthy | Functional test performed | Status |
|---|---|---|---|---|
| PostgreSQL (`rmprod-postgres-1`) | yes, 8 days | yes | `/readyz` executes a real `SELECT 1` through it → `{"status":"ok"}`. Also queried directly: 0 rows in `app_user`, Alembic revision `0003_recommendation_decisions` | 🟡 running and reachable; **database content and schema are from before this codebase's migrations 0004/0005 existed** |
| Redis (state) | yes | yes | Celery worker/beat logs show real task dispatch and completion on schedule (`notifications.retry_failed`, `notifications.sweep`) | 🟢 functionally confirmed |
| Redis (cache) | yes | yes | Not independently exercised this audit beyond container health | 🟡 STATICALLY VERIFIED (container health only) |
| Nginx / edge | yes | yes | `curl` through the edge: HTTP→HTTPS redirect (301), UI HTML returned (200), `/api/docs` correctly hidden (404, prod), `/metrics` correctly denied (404, `deny all`) | 🟢 functionally confirmed |
| FastAPI (`rmprod-api-1`) | yes | yes | `/healthz`, `/readyz` both 200 through the edge. **Filesystem inspected directly: no `app/infrastructure/llm/` directory, no `calibration`/`outcomes` in `app/services/`** — this container is running code from before those features existed | 🔴 the running container is not the codebase under audit |
| Celery worker | yes | yes | Log tail shows real task execution | 🟢 functionally confirmed |
| Celery beat | yes | yes | Log tail shows real scheduled dispatch | 🟢 functionally confirmed |
| Dagster | not deployed as a service in this stack | n/a | Verified via direct Python execution instead (§7, §8) — Dagster here runs via CLI/library, not as a standing service | ⚪ NOT APPLICABLE — no Dagster daemon in this compose stack to check |
| Prometheus | yes | yes | `docker exec ... wget .../-/healthy` → "Prometheus Server is Healthy"; `/api/v1/rules` → 2 rule groups, 4 rules loaded (matches CLAUDE.md's "four alert rules") | 🟢 functionally confirmed (rules load and evaluate; see §15 for the "did an alert fire" distinction) |
| Grafana | yes | yes | `/api/health` → `{"database":"ok"}`. `/api/search` → **empty array** — despite 3 dashboard JSON files existing in the repo (`infra/monitoring/grafana/provisioning/dashboards/`), none are loaded into this running instance | 🔴 dashboards exist in the repo, not in the running deployment — consistent with the stale-image finding |
| Alertmanager | **not running** — no container | n/a | n/a | ⚪ NOT TESTED — not deployed in this environment at all |
| Streamlit UI | yes | yes | Edge-proxied HTML returned; separately, `ui/tests` exercises the actual current-codebase workspace files directly (not through this container) — see §16 | 🟡 the running container serves an old UI build; current-codebase UI evidence comes from §16 instead |

**The core finding, stated once clearly:** this stack was built
2026-08-07T16:43. The working tree under audit contains the LLM gateway,
calibration, and outcomes features, Dagster orchestration, monitoring
config, and the Prompt 10.5 fixes — none of which exist in this container's
image. Do not read any "PASS" in this table as evidence that the *current*
application code runs correctly in a deployed container — no current build
of this codebase has ever been deployed. Where this audit needed evidence
about the current code specifically, it used direct process execution
(§5, §7, §8) or the test suites (§4) instead of this stack.

---

## 15. Observability Verification

- **Prometheus:** rules load and evaluate (§14). "Alert rule exists" is
  confirmed; "alert was successfully delivered" is not, and cannot be,
  because —
- **Alertmanager: not deployed.** No notification path exists to test in
  this environment. `infra/monitoring/alertmanager.yml` defines routing
  (email/Slack per severity, per `docs/prompt-10.5-remediation.md`'s
  `.env.example` additions), but there's nothing running to receive
  Prometheus's alerts and route them. ⚪ NOT TESTED, and per CLAUDE.md's own
  "Known issues," this is an accepted, pre-existing gap, not a regression.
- **Grafana dashboards:** files exist, not provisioned into the running
  (stale) instance (§14). ⚪ NOT TESTED against a live provisioned instance;
  the JSON files themselves were not independently validated against
  Grafana's schema in this audit either.
- **SLO recording rules:** `infra/monitoring/recording-rules.yml` exists in
  the repo; not verified as loaded into the running (stale) Prometheus,
  since that file postdates the image build. ⚪ NOT TESTED live.

No safe alert condition was triggered end-to-end in this audit (would
require either a real failure condition or manipulating Prometheus's rule
evaluation, both out of scope for a non-destructive audit).

---

## 16. UI Verification — 🔴 RELEASE BLOCKER FOUND

12 workspaces discovered (`ui/workspaces/1_Command_Center.py` through
`12_Admin.py`). Verification method: Streamlit's own `AppTest` framework
(`ui/tests/test_workspaces.py`, via `ui/tests/conftest.py`) — this actually
executes each workspace script against a mocked API client and inspects the
rendered element tree (markdown, dataframes, exceptions). This is the
strongest automated verification available in this environment; no manual
browser session was performed, and that limitation is stated explicitly
rather than implied away.

**Fast-ladder run (all `ui/tests` combined with backend/data-platform/ml
unit tests): 976/977, only the known `test_command_center_loads_without_error`
failure (a markdown-index assumption bug, unrelated to rendering
correctness — see `docs/prompt-10.5-test-results.md`).**

**`ui/tests/test_workspaces.py` run in isolation: segfaults.** Reproduced
**three times** in this audit (two full-file runs, one targeted run of just
the crashing parametrized test):

```
$ uv run pytest ui/tests/test_workspaces.py -q
..............Fatal Python error: Segmentation fault
  File ".../pandas/core/arrays/string_arrow.py", line 241 in _from_sequence
  ...
  File ".../ui/retailmind_ui/components/primitives.py", line 292 in frame
  File ".../ui/retailmind_ui/components/primitives.py", line 323 in table
  File ".../ui/workspaces/12_Admin.py", line 40 in <module>
  ...
  File ".../test_workspaces.py", line 109 in test_every_workspace_survives_an_api_that_answers_nothing
```

Isolated precisely via targeted parametrized run: 11/12 workspaces pass
`test_every_workspace_survives_an_api_that_answers_nothing`; the 12th
(`12_Admin.py`) crashes the interpreter every time. `ui.table()`'s own
empty-rows guard (`if not rows: return`) is not what's failing — the crash
is inside real `pd.DataFrame(rows)` construction with non-empty data, deep
in pandas' pyarrow-backed string-array code, a native crash outside
Python's own exception handling.

**Why the combined fast-ladder run doesn't show this:** not established
with certainty in this audit (would need more time than budgeted to isolate
— test ordering, pytest-xdist-style isolation, or memory-layout differences
between running `ui/tests` alone vs. after `backend`/`data_platform`/`ml`
tests have already run are all plausible; this audit did not determine
which). What **is** established: the crash is real, reproducible on demand
in this exact repository state, and a production Streamlit deployment
hitting the same code path with the same data shape (an Admin-panel view
under a degraded/empty upstream API — not a contrived test-only scenario)
would crash the same way, since the failing code is the same native
pandas/pyarrow path a live browser session would also exercise.

**Classification:** 🔴 RELEASE BLOCKER. A segmentation fault is a total
process death, not a handled error — worse than any exception the
application's own error handling could catch. Not fixed in this audit
(audit rule: report, don't fix); root cause narrowed to
`ui/retailmind_ui/components/primitives.py:292` (`frame()`) via
`ui/workspaces/12_Admin.py:40`'s `ui.table()` call, but the exact triggering
data shape was not isolated further within this audit's time budget.

**The other 11 workspaces:** load without error in both the isolated
parametrized run and the combined fast-ladder run. 8 of the 12 have
explicit `*_loads_without_error` tests by name; all 12 are covered by the
`ALL_WORKSPACES`-parametrized generic tests (`test_a_signed_out_visitor_gets_no_figures`,
`test_hiding_a_workspace_is_never_the_only_control`,
`test_every_workspace_survives_an_api_that_answers_nothing`) — the last of
which is exactly what caught the Admin-workspace crash, so the parametrized
coverage is doing real work, not just breadth for its own sake.

---

## 17. Performance / Load Verification

**⚪ NOT TESTED.** `which k6` → not found, confirmed fresh in this audit
(unchanged from Prompt 10.5). `docs/load-testing-results.md`'s numbers were
already estimated rather than measured before this audit and remain so —
not re-verified, not treated as evidence of anything. No performance claim
in this report should be read as tested.

---

## 18. Code Quality

**🟢 PASS**, all re-run fresh in this session (not copied):

- `ruff check backend data_platform ml ui` — clean.
- `ruff format --check backend data_platform ml ui` — 326 files, all
  formatted.
- `mypy backend/app` — 172 source files, no issues.
- `mypy ui` — 34 source files, no issues.
- `uv run lint-imports` — 1 contract kept, 0 broken, 162 files / 270
  dependencies analyzed (architecture boundaries: `api → services → domain`,
  `infrastructure` implements ports — intact).

No new lint or type regressions relative to Prompt 10.5's end state, because
nothing was changed between that pass and this audit.

---

## 19. Documentation Verification

`check_docs_integrity.py` — passes, re-run fresh.

Spot-checked against the actual repository in this audit:
- `docs/known-issues.md` — RESOLVED markers with dates and fix references
  present and, per §3, accurately describe what this audit independently
  reproduced.
- `docs/production-readiness-final-report.md` — carries the Prompt 10.5
  correction banner; historical content preserved, not rewritten, as
  instructed.
- `.env.example` — matches the settings model (`check_env.py` clean).
- `README.md` — still contains the pre-existing false claims CLAUDE.md
  itself documents as known and "planned but not yet done" (e.g. "No
  Dagster" at line 357, despite real, working, tested Dagster orchestration
  existing — confirmed by this audit's own §7/§8 execution). Not corrected
  in this audit; out of scope (audit rule: report, don't fix; also
  explicitly deferred by CLAUDE.md itself).
- `CLAUDE.md`'s "Things that specifically do not exist" table — the LLM
  integration and Dagster/orchestration rows are stale, as Prompt 10.5
  already flagged. This audit independently confirms both are stale: real
  LLM gateway code exists (§11), and real Dagster orchestration exists and
  (mostly) runs (§7, §8).
- **New documentation gap this audit found:** none of the docs list
  reviewed in Phase 19 mention the `dbt_seeds`/`dbt_snapshots` scheduling
  failure (§8) or the Admin-workspace segfault (§16), because both were
  discovered during this audit, not before it. `docs/known-issues.md` is
  not updated with them — this audit's own instructions are to report, not
  to modify documentation either, so they're recorded here and in this
  report's §20/§21 only.

---

## 20. Remaining Issues

| Issue | Severity | New in this audit? |
|---|---|---|
| `dbt_build` job fails on real execution (`dbt_seeds`/`dbt_snapshots` path bug) | 🔴 Release blocker | **Yes** |
| Admin workspace segfaults under degraded-API conditions | 🔴 Release blocker | **Yes** |
| Running `rmprod` stack is a stale, pre-Prompt-9A build | 🟡 Methodological gap, not a code bug | **Yes** (as an explicit finding; the staleness itself presumably existed all along) |
| Calibration API generator-filtering broken (`Recommendation.type` vs. documented category vocabulary) | 🔴 Real production bug, real data-model gap | No — carried forward from Prompt 10.5, independently reproduced |
| `test_command_center_loads_without_error` — markdown-index test assumption | 🟡 Test-only, pre-existing | No — carried forward, reproduced |
| Restore into a renamed target database unsupported | 🟡 Documented limitation | No — carried forward |
| Grafana dashboards not provisioned into the running instance | 🟡 Consequence of stale stack | Related to the stale-stack finding |
| Alertmanager not deployed; no notification path | ⚪ Not tested, known accepted gap | No — CLAUDE.md-documented |
| Real Anthropic LLM provider untested (no credentials) | ⚪ Not tested | No |
| k6/load testing unavailable | ⚪ Not tested | No |
| README/CLAUDE.md stale claims (Dagster, LLM) | 🟡 Documentation drift | No — carried forward, independently reconfirmed |
| Orphaned duplicate `tests/integration/test_migrations.py` at repo root | 🟡 Dead test code, not wired to any target | No — carried forward |

---

## 21. Production Blockers

**Two genuine, independently-verified, release-blocking findings, both new
to this audit:**

1. 🔴 **The scheduled Dagster `dbt_build` job cannot complete successfully.**
   The daily automated warehouse refresh is broken as configured. §8.
2. 🔴 **The Admin workspace segfaults** under a realistic degraded-API
   condition, killing the Streamlit worker process outright. §16.

**One carried-forward blocker-caliber finding**, already documented by
Prompt 10.5 and independently reproduced here, not newly discovered but not
downgraded either:

3. 🔴 **The calibration API's generator-filtering is broken for every
   caller**, not just the test suite — a real data-model defect
   (`Recommendation.type` vocabulary vs. the documented "generator"
   vocabulary are disjoint). §10, §3.

None of the three are fixed by this audit, per its rules. All three are
concrete, reproducible, and have a pinned root cause — none are vague or
speculative.

---

## Final Release Gate

1. **Any CRITICAL production blockers?** Yes — the segmentation fault
   (§16) is a total-process-crash-caliber defect; classified CRITICAL.
2. **Any HIGH production blockers?** Yes — the Dagster `dbt_build` job
   failure (§8) and the calibration API defect (§10) are both HIGH:
   real, reproducible, affect scheduled/documented functionality.
3. **Is JWT signing stable across multiple workers?** Yes — §5, genuine
   cross-process proof.
4. **Does JWT survive worker/application restart?** Yes — §5 (the
   cross-process test *is* the restart-equivalence proof: a freshly
   launched interpreter is functionally a restarted worker).
5. **Does Alembic have exactly one head?** Yes — §6.
6. **Can migrations upgrade from base to latest?** Yes — §6, fresh
   disposable database.
7. **Can migrations downgrade from latest to base?** Yes — §6.
8. **Can migrations upgrade again after downgrade?** Yes — §6.
9. **Do all Dagster unit tests pass?** Yes, 21/21 — but this is explicitly
   **not** the same claim as "the Dagster job runs" — see #10.
10. **Does a representative Dagster job actually execute?** **No.** The
    real, scheduled `dbt_build` job fails immediately. §8.
11. **Do database schema tests pass?** Yes — §3, §4.
12. **Does Ruff pass?** Yes — §18.
13. **Does mypy pass where configured?** Yes — §18.
14. **Do backend tests pass?** Mostly — 976/977 unit, 324/327 integration;
    both known, documented, non-fixed failures. §4.
15. **Do data-platform tests pass?** Yes — 4/4 integration, plus a fresh
    152/0 real `dbt build`. §4, §7.
16. **Do dbt tests pass?** Yes, 73/73 data tests within the 152-check build
    — but see #10: the dbt project passing standalone is not the same as
    the Dagster job that's supposed to run it daily succeeding.
17. **Are ML/forecast tests passing?** Yes, 66/66 — with the historical-data
    limitation noted in §9 (🟡, not 🟢).
18. **Was backup actually executed?** Yes — §13, fresh disposable database,
    independently reproduced.
19. **Was restore actually executed against a disposable database?** Yes —
    §13, exact data match confirmed.
20. **Does authentication work?** Yes — §5, plus the passing
    `test_auth_api.py` integration tests.
21. **Does RBAC work?** Yes, via the passing `test_require_*` unit tests and
    the workspace-permission tests in `ui/tests`; not independently
    re-exercised with fresh manual requests in this audit beyond the
    reproduced suite.
22. **Does tenant isolation work?** Evidenced by the passing test suite
    (tenant-scoped queries throughout `backend/tests`); not independently
    probed with a fresh cross-tenant attack attempt in this audit —
    🟡 STATICALLY + test-suite verified, not freshly, adversarially tested.
23. **Does rate limiting work?** Evidenced by the passing unit test suite
    (`backend/app/core/rate_limit.py` is covered); not independently
    load-tested in this audit (§17 — no k6).
24. **Does idempotency work?** Evidenced by the passing unit test suite
    (`backend/app/core/idempotency.py`); not independently re-exercised
    with a fresh duplicate-request probe in this audit.
25. **Is LLM grounding verified?** Mock provider yes (§11); real provider
    ⚪ NOT TESTED (no credentials).
26. **Are secrets protected?** Yes — §12, fresh scan.
27. **Does the runtime stack operate correctly?** The **running** stack
    operates correctly for what it is — a 9-day-old build. It is **not**
    evidence the current codebase operates correctly as a deployed service,
    because the current codebase has never been deployed into it. §14.
28. **Does monitoring work?** Prometheus rules load and evaluate; Grafana
    dashboards exist in the repo but aren't provisioned into the running
    instance. §14, §15.
29. **Does alerting work?** Rules exist and evaluate; no delivery path is
    deployed to test (Alertmanager isn't running). ⚪ NOT TESTED end-to-end.
30. **Is the UI functionally verified?** 11 of 12 workspaces, yes, via the
    strongest available automated tool (Streamlit `AppTest`), not a manual
    browser session. The 12th (Admin) crashes the process under a specific,
    realistic condition. §16.
31. **Is documentation synchronized?** Mostly — `known-issues.md`,
    `.env.example`, and the correction-banner pattern are accurate and
    current. `README.md`/`CLAUDE.md` still carry pre-existing, previously
    flagged, explicitly-deferred stale claims. Neither of this audit's two
    new findings (§8, §16) is documented anywhere yet, because they were
    found during this audit.
32. **Are there any remaining release-blocking risks?** Yes — see §21.

---

## FINAL VERDICT

# 🔴 NOT READY — RELEASE BLOCKED

**Basis for this verdict, stated plainly:** Prompt 10.5's remediation work
holds up under independent, hands-on reproduction — every claim it made was
checked again in this session with fresh evidence, not assumed, and nearly
all of it reproduced exactly. That is genuinely good news and should not be
undersold by the verdict below.

But this audit's mandate was to actually execute what prior passes only
validated structurally or through mocks, and doing that surfaced two new,
concrete, reproducible failures that any of those prior passes could have
caught only by doing what this audit did — running the real scheduled job,
and running the UI test file in isolation instead of only as part of a
larger, apparently-passing combined run:

- The system's own daily data-pipeline refresh does not run successfully.
- A core administrative UI view crashes its host process under a realistic
  condition.

Per this prompt's explicit instruction, neither can be downgraded to
"non-blocking" merely because the application otherwise looks healthy, and
neither can be waved off with "the previous audit didn't flag it" — this
audit exists specifically to catch what previous ones missed, and did.

A third finding — that the only running deployment of this system predates
the codebase under review by nine days and reflects none of its recent
functionality — doesn't independently block release, but it does mean no
one should treat this repository's current runtime health as demonstrated
by that stack. It hasn't been demonstrated by any deployed instance at all;
only by direct process execution and test suites, which is weaker evidence
for "this works when actually deployed" than a real running build would be.

**Recommended before the next release attempt:**
1. Fix `CliExecutor.run_dbt()`'s path resolution (§8) and re-run the real
   `dbt_build` job materialization, not just the unit tests, to confirm.
2. Root-cause and fix the Admin-workspace segfault (§16); add a regression
   test that runs `ui/tests/test_workspaces.py` in isolation (not only as
   part of the combined ladder) so this class of failure can't hide again.
3. Resolve the calibration API's category/type data-model mismatch (§10),
   carried forward from Prompt 10.5.
4. Build and deploy a current image before drawing any further conclusions
   from "the stack is healthy" — or explicitly acknowledge in future audits
   that container-level checks against this specific stack are not
   evidence about the codebase.
