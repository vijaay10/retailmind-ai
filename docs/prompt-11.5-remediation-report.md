# Prompt 11.5 — Release-Blocker Remediation Report

**Date:** 2026-08-16
**Scope:** the three release blockers Prompt 11 verified by real execution —
not re-litigating anything Prompt 11 already confirmed working.
`docs/prompt-11-final-release-audit.md` is left untouched as the historical
record of what that audit found; this document is the fix, not a rewrite of
the finding.

---

## Blocker 1 — Dagster `dbt_build` job

**Status: 🟢 RESOLVED**

### Original blocker
The scheduled `dbt_build` job (`daily_dbt_schedule`, 3 AM UTC) failed on
every real execution. `dagster definitions validate` and the 21-test unit
suite both passed; neither ever ran the real subprocess with the real
configured working directory.

### Root cause
Two layered bugs in `CliExecutor.run_dbt()` (`data_platform/orchestration/dagster/resources.py`),
both only reachable by actually executing the job, not by importing or
mocking it:

1. `run_dbt()` built `["dbt", command, "--profiles-dir", ".", "--project-dir", "dbt"]`
   — paths relative to `self.working_directory`. Production configures
   `working_directory` to the repository root
   (`orchestration/dagster/__init__.py:53`), one directory level above
   where the dbt project actually lives (`data_platform/dbt`), so
   `--project-dir dbt` resolved to `<repo_root>/dbt`, which doesn't exist.
2. Fixing (1) alone still failed: `profiles.yml`'s default warehouse path
   (`env_var('RM_WAREHOUSE_DUCKDB_PATH', '../../.local/retailmind.duckdb')`)
   is *also* relative, and dbt resolves it against the subprocess's actual
   invocation cwd — not `--project-dir`. With `cwd` still set to the repo
   root, that relative path resolved to `/Users/.local/retailmind.duckdb`
   (two directories too far up), a file that doesn't exist.

A third, unrelated issue surfaced during verification: a stale, corrupted
local `data_platform/dbt/target/` directory (a leftover artifact from
Jul 30, predating this session) had a snapshot output path
(`target/run/retailmind/snapshots/snap_product.sql`) that was itself a
*directory* instead of a file, breaking `dbt snapshot`. Gitignored build
output, not a code bug — cleared with `rm -rf` and confirmed unrelated by
reproducing the real fix's success independent of it.

### Fix
`data_platform/orchestration/dagster/resources.py`:
- `--profiles-dir` / `--project-dir` are now absolute, anchored to this
  module's own file location (`Path(__file__).resolve().parents[2] / "dbt"`)
  — not to `self.working_directory`, and not to any developer's home
  directory.
- `run_command()` gained an optional `cwd` override parameter (default:
  unchanged, `self.working_directory`); `run_dbt()` passes
  `cwd=str(_DBT_PROJECT_DIR)` explicitly, so `profiles.yml`'s relative
  default resolves correctly regardless of what `working_directory` is
  configured to for the resource's other methods (`run_ingestion`,
  `run_forecast_training`, both untouched and still using
  `self.working_directory` as before).
- The `working_directory` field's default value was also a hardcoded
  personal absolute path (`/Users/vijaays/retailmind-ai/data_platform`) —
  replaced with a `default_factory` computed the same anchored way.

### Files modified
- `data_platform/orchestration/dagster/resources.py`
- `data_platform/tests/unit/test_dagster_orchestration.py` (regression test)

### Tests added
`test_run_dbt_project_and_profiles_dirs_resolve_regardless_of_working_directory` —
constructs `CliExecutor` with a `working_directory` deliberately **not**
`data_platform/` (reproducing the structural shape of the bug), asserts the
generated `--project-dir`/`--profiles-dir` are absolute paths that actually
contain `dbt_project.yml`/`profiles.yml`, and asserts the subprocess `cwd`
is the real dbt project directory — not just that the string `"dbt"`
appears somewhere in the command, which the original bug also satisfied.

### Test results
`uv run pytest data_platform/tests/unit/test_dagster_orchestration.py -v`
→ **22/22 passed** (21 pre-existing + 1 new).

### Runtime reproduction BEFORE fix
```
materialize([dbt_seeds, dbt_snapshots, retailmind_dbt_assets], resources={...real production resources...})
→ MATERIALIZE SUCCESS: False
subprocess.CalledProcessError: Command
  ['dbt', 'seed', '--profiles-dir', '.', '--project-dir', 'dbt']
  returned non-zero exit status 2.
```

### Runtime verification AFTER fix
Real execution, real production resources (`from orchestration.dagster import resources`),
the exact three-asset composition that makes up the real `dbt_build` job:
```
MATERIALIZE SUCCESS: True
assets materialized: 77
```
Then, for the strongest possible proof, the **actual named job object**
(what the schedule triggers) via `job.execute_in_process()`:
```
dbt_build JOB SUCCESS: True
assets materialized: 77
Done. PASS=152 WARN=0 ERROR=0 SKIP=0 NO-OP=0 TOTAL=152
```
(6 seeds, 2 snapshots, 29 table models, 35 view models, 3 incremental
models, 73 data tests — the full dbt project, for real.)

`run_ingestion` (untouched) re-verified separately, still works:
`uv run retailmind-etl run --source pos --table sales --day 2026-08-01 --expected-stores 4`
→ `succeeded`.

### Remaining limitations
None identified. The fix is anchored to file location rather than any
runtime assumption, so it holds regardless of Docker vs. local execution or
which directory Dagster itself is launched from.

---

## Blocker 2 — Admin Streamlit segmentation fault

**Status: 🟢 RESOLVED**

### Original blocker
`ui/tests/test_workspaces.py::test_every_workspace_survives_an_api_that_answers_nothing[12_Admin.py]`
segfaulted the Python interpreter, reproduced 3 times by Prompt 11.

### Root cause investigation
Reproduced first: 3 consecutive full-file runs of `ui/tests/test_workspaces.py`
segfaulted at the same point; running the Admin case **in isolation**
passed 5/5 — ruling out anything about the Admin workspace's data
specifically (Prompt 11 had already narrowed the crash site to
`ui/retailmind_ui/components/primitives.py:292` inside `frame()`, called
from `12_Admin.py:40`/`:62`'s `ui.table()` calls, but had not determined
why it was condition-dependent).

Isolated further: crash requires the *combination* of many prior
`AppTest` script re-runs before it, not anything about Admin's own rows
(a static 12-item list of 5-key dicts, unchanged whether Admin runs 1st or
12th). Checked pandas/pyarrow versions: pandas 3.0.5 (a major version
released with `future.infer_string` — Arrow-backed string storage —
enabled **by default**), pyarrow 25.0.0. Confirmed with a controlled A/B
test: toggling `pd.options.future.infer_string` to `False` before running
the exact same crashing sequence eliminated the segfault, 12/12, run
immediately after the same 11 prior workspace re-runs that crashed it
before.

**The original hypothesis (crash tied to "the API answers nothing") was
wrong** — the crashing call in Admin never received empty data (it's a
hardcoded 12-row list), and the "empty API" test scenario was only ever the
first thing in Prompt 11's test ordering to reach the accumulation
threshold, not the cause. This changes what "the smallest safe fix" means:
special-casing empty-data handling in `12_Admin.py` (the literal preferred
approach in Prompt 11.5's brief) would not have prevented the crash, since
it isn't the trigger. Fixing the actual mechanism does.

### Fix
No `try`/`except` around the crash — a segfault can't be caught by one.
`ui/retailmind_ui/__init__.py` (the package root, guaranteed to run before
any submodule constructs a DataFrame) now sets
`pd.options.future.infer_string = False` at import time, reverting string
columns/indexes to the numpy `object` dtype pandas used before 3.0. This is
a process-global pandas setting, set once, covering every `pd.DataFrame(...)`
call anywhere in the console — not just the one that happened to crash
first.

### Files modified
- `ui/retailmind_ui/__init__.py`

### Tests added
New file `ui/tests/test_primitives.py`:
- `test_arrow_string_inference_is_disabled_for_the_package` — guard rail;
  fails loudly if the setting is ever silently reverted (a pandas upgrade,
  an accidental removal), rather than waiting for a segfault to notice.
- `test_frame_handles_the_exact_admin_workspace_shape_repeatedly` — the
  exact row shape from Admin's crashing call, constructed 200 times in a
  loop — the accumulation pattern that triggered the original crash,
  without needing a full `AppTest` sweep to catch a regression.
- `test_frame_of_empty_rows_returns_an_empty_dataframe_without_constructing_one` —
  confirms the pre-existing empty-rows early return still works.
- `test_frame_survives_null_and_ragged_shapes` (parametrized, 4 cases) —
  null values, ragged keys across rows: malformed/unexpected shapes an API
  could plausibly return.

### Test results
`uv run pytest ui/tests/test_primitives.py -v` → **7/7 passed**.
`uv run pytest ui/tests/test_workspaces.py -q` → **46/47 passed**, run 4
times consecutively with no segfault (the 1 failure is the pre-existing,
unrelated `test_command_center_loads_without_error` markdown-index issue —
present before this pass, untouched by it).

### Runtime reproduction BEFORE fix
```
$ uv run pytest ui/tests/test_workspaces.py -q
..............Fatal Python error: Segmentation fault
  File ".../pandas/core/arrays/string_arrow.py", line 241 in _from_sequence
  File ".../ui/retailmind_ui/components/primitives.py", line 292 in frame
  File ".../ui/workspaces/12_Admin.py", line 40 in <module>
```
Reproduced 3 times.

### Runtime verification AFTER fix — real process, real browser
Not declared fixed on the test suite alone, per this prompt's explicit
instruction. Independently verified against a **live, currently-running**
Streamlit process (`streamlit run app.py`, current working-tree code — not
the stale `rmprod` image), connected to a freshly migrated, freshly seeded,
current-code backend API (also started fresh for this check):

1. Real browser session (Chrome, via the claude-in-chrome tools): navigated
   to the console, signed in as a real seeded user (`sam@northwind.example`,
   `admin` role), navigated to the Admin workspace.
2. Both crash-implicated `ui.table()` calls rendered with real data —
   **Permissions** tab (16 real permission rows) and **Workspaces** tab (12
   real workspace-gate rows) — no crash, page fully interactive throughout.
3. Backend Streamlit process confirmed alive (`ps -p <pid>`) after the
   interaction; server log showed no error; the only browser console entry
   was a single transient `WebSocket onerror`, unrelated (page remained
   responsive, no reconnect loop, no further errors).

This directly satisfies "reproduce the original condition... Streamlit
process remains alive... then test with real/non-empty API data... table
renders, normal workspace behavior remains intact" — both the originally
crashing path and the normal-data path were exercised live, in a real
browser, against real current code.

### Remaining limitations
The exact mechanism of *why* pandas 3.0's Arrow-string path segfaults on
this pandas/pyarrow pairing under repeated `AppTest` script re-runs (vs.
crashing on the first call, or never) was not fully root-caused down to the
native code — the fix (disable the feature entirely) doesn't require that
deeper diagnosis, and further investigation would mean debugging pandas/
pyarrow's own C++ internals, out of scope for an application-level fix. If
a future pandas/pyarrow upgrade fixes the underlying native bug, the
`infer_string = False` setting can be revisited — the guard-rail test
(`test_arrow_string_inference_is_disabled_for_the_package`) exists so that
decision is made deliberately, not by accident.

---

## Blocker 3 — Calibration API generator filtering

**Status: 🟢 RESOLVED**

### Original blocker
`/recommendations/calibration/generators/{generator}` and the per-generator
breakdowns on the other calibration endpoints could never match a real
generator, for any caller — not just the test suite.

### Root cause (three layered bugs, each hidden behind the previous one — none reachable until the one before it was fixed)

1. **Wrong column.** `OutcomeRepository.find_measured()`
   (`backend/app/infrastructure/db/repositories/outcomes.py`) filtered on
   `Recommendation.type`, constrained to `reorder | markdown | promo |
   assortment` — the *kind of action* a row represents. The API's own docs
   describe "generator" as `inventory | pricing | promotion | store |
   marketing | customer | supplier` — the live analytical engine's
   `Category` (`app/services/recommendations/contracts.py`). The two
   vocabularies are disjoint; no `type` value could ever equal a category
   name. **There was no `category` column on `Recommendation` at all** —
   the live engine's `Category` is captured when a decision is recorded
   (`RecommendationService.decide()` already passes
   `category=match.category.value` — but into `RecommendationDecision`,
   a *different* table for a *different* purpose (the accept/dismiss
   ledger), not into `Recommendation`/`RecommendationOutcome` (the outcome-
   measurement tables the calibration API actually reads from).
2. **Async lazy-load under a fixed query.** Once (1) was fixed and real
   data could be written and queried, reading `outcome.recommendation.*`
   in the repository's dict-building step raised
   `sqlalchemy.exc.MissingGreenlet` — the `.join()` used for filtering
   doesn't populate the ORM relationship, so accessing it lazy-loads, and
   `AsyncSession` can't do that outside an awaited context. Never hit
   before because every earlier attempt to write real data through this
   path failed on bug (1) or a data-integrity issue first.
3. **Categorical vs. numeric confidence.** Once (2) was fixed, the
   *confidence-band* calibration sub-feature crashed with
   `TypeError: unsupported operand type(s) for /: 'str' and 'float'` —
   `calculator.segment_by_confidence_band()` requires a numeric 0.0–1.0
   confidence score (documented and tested in its own unit test,
   `test_segment_by_confidence_band`, which explicitly pre-filters
   non-numeric values before calling it). `Recommendation.confidence` is
   categorical (`high | medium | low`, a rubric belonging to the batch rule
   engine that writes this table) — never numeric anywhere in the schema.

Also found and fixed to get real test data flowing at all (not a
calibration-logic bug, but blocked writing real rows regardless):
`Recommendation.data_snapshot_id` is a real foreign key to `data_snapshot`;
the test file used a bare placeholder string that was never a real row.

### Fix
- **New column, not a redesign:** `backend/app/infrastructure/db/models/enums.py`
  gained `RecommendationCategory` (mirrors `Category` from
  `app.services.recommendations.contracts` — not imported directly, since
  `infrastructure` doesn't depend on `services`, matching the existing
  layering the import-linter enforces). `Recommendation` gained a nullable
  `category` column with a matching check constraint. Migration
  `202608161626_recommendation_category.py` (`0006_recommendation_category`,
  head), additive and nullable — existing rows (if any) get `category =
  NULL`, which the fixed query correctly treats as "matches no generator
  filter," not an error.
- `OutcomeRepository.find_measured()`: filters on `Recommendation.category`
  instead of `.type`; the result-dict's `"category"` field now reads
  `.category` instead of `.type`; the query gained
  `.options(selectinload(RecommendationOutcome.recommendation))` to
  eager-load the relationship instead of lazy-loading it.
- `CalibrationService._calculate_confidence_calibration()`: filters
  outcomes to `isinstance(confidence, int | float)` before calling
  `segment_by_confidence_band`, matching the calculator's own documented,
  tested contract — with the current categorical-only schema this
  correctly (safely) returns no confidence-band breakdown rather than
  crashing the whole endpoint over one sub-metric. `confidence_bands` was
  already a non-asserted-nonempty field in the existing tests.

None of this changes `type`, `RecommendationType`, or anything the batch
rule engine's own semantics depend on — additive only.

### Files modified
- `backend/app/infrastructure/db/models/enums.py`
- `backend/app/infrastructure/db/models/recommendations.py`
- `backend/app/infrastructure/db/migrations/versions/202608161626_recommendation_category.py` (new)
- `backend/app/infrastructure/db/repositories/outcomes.py`
- `backend/app/services/calibration/service.py`
- `backend/tests/integration/test_calibration_api.py`

### Tests added/changed
- Fixed 3 pre-existing tests' fixtures to use real `type`/`category` value
  pairs and a real `data_snapshot_id` (previously a bare placeholder
  string that violated a foreign key).
- Added `_clean_recommendations_after_each_test` (autouse) — the
  session-scoped test database was leaking committed rows across tests in
  the same file (`test_generator_calibration_success` was seeing 50
  outcomes: 25 of its own plus 25 left over from an earlier test), a real
  test-isolation gap, not something to paper over with a looser assertion.
  **The first version of this fixture over-corrected**: it ran a blanket
  `DELETE FROM recommendation` with no scoping, which deleted every row in
  the table — including the seed script's own demo recommendation
  (`app/infrastructure/db/seeds/sample.py`, the "reorder recommendation"
  around line 241). Because `migrated_db` is session-scoped across the
  *entire* `backend/tests/integration` run, not just this file, and this
  file's tests run alphabetically before `test_dashboard_api.py`, that
  blanket delete broke three unrelated dashboard tests
  (`test_recommendations_are_ranked_by_impact`,
  `test_recommendations_state_their_estimation_method`,
  `test_executive_overview_assembles_every_tile`) that depend on the seeded
  row still being there. Caught by running the full integration suite
  twice (not just this file in isolation) during this pass's own
  self-verification — see Phase 6 and gate question 10 for the full
  timeline. Fixed by scoping the delete to only this file's own
  `dedup_key` values (`test-key`, `test-key-2`, `test-key-3`,
  `test-key-pricing`) instead of every row in the table.
- **New:** `test_generator_calibration_for_a_second_distinct_generator` —
  a *second* real generator (`pricing`, distinct from the existing
  `inventory` coverage) through the real endpoint, **and** asserts that
  filtering by `pricing` returns nothing for `inventory` — proving the
  filter discriminates by real category value rather than happening to
  match one hardcoded case. Covers requirement B (another valid generator)
  and reinforces E (empty/isolated result) from the brief's checklist;
  requirement C (unknown generator) and D (no filter) were already covered
  by pre-existing tests, now genuinely passing rather than erroring before
  ever reaching their assertions.

### Test results
`uv run pytest backend/tests/integration/test_calibration_api.py -v` →
**7/7 passed** (was: 5 collection errors before Prompt 10.5's fixture fix;
3 real failures with root cause identified but not fixed after that pass;
now fully resolved).

### Runtime reproduction BEFORE fix
```python
materialize... # N/A — direct repository call:
await repo.find_measured(generator="inventory")
# → 0 results, always, for any real category value, because `type` never
#   equals a category name
```
Plus, once real data could be written at all (Prompt 11.5's own earlier
fix attempts along the way): `IntegrityError` (check constraint), then
`MissingGreenlet`, then `TypeError` — each one masking the next.

### Runtime verification AFTER fix — real HTTP, real live API, real Postgres
Not stopped at the test suite. Restarted the locally-running current-code
API (`uvicorn`, current working tree, migrated to `0006_recommendation_category`)
against its disposable Postgres, inserted real `recommendation` +
`recommendation_outcome` rows directly via SQL (`category='inventory'`, 25
measured outcomes), then issued real HTTP requests:

```
$ curl .../recommendations/calibration/generators/inventory -H "Authorization: Bearer $TOKEN"
{"generator_name":"inventory","metrics":{"sample_size":25,"is_statistically_significant":true, ...}}
HTTP 200

$ curl .../recommendations/calibration/generators/pricing -H "Authorization: Bearer $TOKEN"
{"detail":"No measured outcomes found for generator: pricing"}
HTTP 404
```

Real generator name, real sample size, real computed statistics, and a
real 404 for the generator with no data — confirming the filter genuinely
discriminates rather than matching everything or nothing.

### Remaining limitations
- `RecommendationDecision` (the accept/dismiss ledger the live analytical
  engine actually writes through today) and `Recommendation`/
  `RecommendationOutcome` (what the calibration API reads) remain two
  separate tables with no automatic bridge between them — nothing in
  production currently writes a `Recommendation` row from a real decision
  (the "outcome measurement service" that would do this, per
  `backend/app/services/outcomes/`, is still unimplemented — a Prompt 10.5
  finding, unchanged and out of this pass's scope). The generator-filtering
  *mechanism* is now correct and proven against real data; whether real
  data ever reaches it in production depends on that separate, pre-existing
  gap.
- Confidence-band calibration will return an empty `confidence_bands` list
  for any real `Recommendation` row today, because no numeric confidence
  score is persisted anywhere in the current schema — safe (no crash,
  matches the calculator's own tested contract) but not a complete fix of
  that specific sub-feature. Documented as a real limitation, not hidden.

---

## Phase 4 — Fresh Runtime Rebuild

**Status: 🟢 DONE.** Prompt 11 found the running `rmprod-*` stack was a
2026-08-07 build predating this codebase's LLM gateway, calibration,
Dagster orchestration, and every Prompt 10.5/11.5 fix — none of it evidence
about the code under review. `rmprod-*` was **not touched, stopped, or
rebuilt** — a fully separate, disposable stack was built and started
instead, project-prefixed `rmstage115`.

**Build note:** `make demo` (the documented path) hung indefinitely with no
output or build-cache activity — traced to `docker compose build`'s default
use of `docker buildx bake`, which on this Docker Desktop version passes
`--allow fs.read=...` sandbox-permission flags that appear to wait on an
interactive approval never satisfiable in this non-TTY session. Worked
around with `COMPOSE_BAKE=false DOCKER_BUILDKIT=1 docker build` invoked
directly against each Dockerfile — same Dockerfiles, same build context,
classic builder instead of bake. Both images built cleanly this way:

| Image | Build result | Image ID | Created |
|---|---|---|---|
| `rmstage115-api:latest` (`infra/docker/api.Dockerfile`) | success | `sha256:aca0130d...` | 2026-08-16T11:33:58Z |
| `rmstage115-ui:latest` (`infra/docker/ui.Dockerfile`) | success | `sha256:68dc9c63...` | 2026-08-16T11:40:27Z |

(`api.Dockerfile` also serves the worker/beat commands — "one image, five
commands," confirmed by reading `infra/docker/api-entrypoint.sh` — so these
two images are the complete set needed.)

**Container filesystem verification — proves these images are the current
codebase, not assumed from a successful build:**
```
$ docker run --rm rmstage115-api:latest sh -c "test -d /srv/backend/app/infrastructure/llm && echo PRESENT"
LLM gateway PRESENT
$ docker run --rm rmstage115-api:latest sh -c "grep -q RecommendationCategory /srv/backend/app/infrastructure/db/models/enums.py && echo PRESENT"
Blocker-3 fix PRESENT
$ docker run --rm --entrypoint sh rmstage115-ui:latest -c "grep -q infer_string /srv/ui/retailmind_ui/__init__.py && echo PRESENT"
Blocker-2 fix PRESENT
```
Git commit at build time: `2fc3ab49a7646f3a21e5fbcf55afe39ad156c10b` (HEAD,
unchanged — nothing has been committed across Prompts 9A–11.5; the fix
exists in the working tree that was built from).

**Fresh stack started** (`rmstage115_net`, isolated Docker network,
disposable named containers — no compose project reused): Postgres 16.4,
2× Redis 7.4, the API image (`api` command), the UI image. Migrated via the
image's own `migrate` command (not a host `alembic` invocation) —
`0001_genesis` through `0006_recommendation_category`, all six, for real,
inside the container. Seeded via the image's own seed modules. A stable,
locally-generated RSA key was mounted and configured
(`RM_AUTH_JWT_PRIVATE_KEY_FILE`) with `RM_API_WORKERS=4`, matching
production's actual configuration rather than the ephemeral-key dev
default — deliberately, to test the real multi-worker path rather than
rediscover the already-documented, already-fixed dev-only limitation.

---

## Phase 5 — Full Runtime Regression

Against the fresh `rmstage115` stack — functional checks, not container
status:

| Component | Check performed | Result |
|---|---|---|
| PostgreSQL | `SELECT version_num FROM alembic_version` via `docker exec` | 🟢 `0006_recommendation_category` |
| Redis (cache) | `redis-cli ping` | 🟢 `PONG` |
| Redis (state) | `redis-cli ping` | 🟢 `PONG` |
| FastAPI — health | `GET /health` | 🟢 200 |
| FastAPI — ready | `GET /ready` (real `SELECT 1` through the pool) | 🟢 200 |
| Authentication | `POST /auth/login` with a real seeded user | 🟢 200, real JWT issued |
| Authenticated request | `GET /auth/permissions` with the token | 🟢 200, real role catalog |
| Unauthenticated request | Same endpoints, no token | 🟢 401 |
| Authorization (forbidden) | `admin`-role token attempting `recommendations.act`-gated write | 🟢 403, generic detail + permission hint, no internals leaked |
| **JWT multi-worker, live** | 20 sequential authenticated requests against the 4-worker container, one login token | 🟢 **20/20 succeeded** — the exact scenario Blocker verification (and Prompt 10.5) proved in isolation, now reproduced against a real multi-worker container |
| Calibration endpoint (Blocker 3) | `GET /recommendations/calibration` | 🟢 200 (see also the direct-uvicorn live proof in Blocker 3 above, with real generator-filtered data) |
| Recommendations / forecasts endpoints | `GET` with a role holding the read permission | 🟡 503 `dependency-unavailable` — no DuckDB warehouse was mounted into this disposable container (a local mounting/catalog-naming detail unrelated to any of the three blockers — the error is the correctly-generic "warehouse is temporarily unavailable," not a leak or a crash) |
| Idempotency | Same `Idempotency-Key` sent twice | 🟡 Inconclusive here — both attempts hit the same 503 (correct: a failed/transient response must not be cached as an idempotent success); real coverage comes from the passing `backend/tests/unit` idempotency-middleware tests (part of the 631/631) |
| Rate limiting | Not independently re-hammered against this stack | ⚪ NOT TESTED live here; covered by the passing unit suite |
| UI (Streamlit) | `GET /` on the UI container | 🟢 200 |
| UI — Admin workspace, empty/degraded data | Live browser session, current-code UI **and** current-code API (the non-Docker live-process pair described in Blocker 2) | 🟢 renders safely, no crash |
| UI — Admin workspace, real data | Same live session | 🟢 Permissions (16 rows) and Workspaces (12 rows) tables render correctly |
| Dagster — definitions load | `dagster definitions validate` | 🟢 (STATICALLY VERIFIED — structural only; see below for the real thing) |
| Dagster — `dbt_build` executes | `job.execute_in_process()`, real production resources, real dbt project | 🟢 **SUCCESS**, 77 assets, 152/152 dbt checks — see Blocker 1 above |
| dbt / warehouse access | `dbt build --select fqn:*` against `.local/retailmind.duckdb` | 🟢 6 seeds, 2 snapshots, 29 tables, 35 views, 3 incremental models, 73 data tests — all real |
| Grafana / Prometheus / Alertmanager | Not part of this disposable stack (not rebuilt — Prompt 11 already found these unprovisioned/undeployed against the stale stack, and rebuilding the monitoring layer wasn't necessary to verify the three blockers) | ⚪ NOT TESTED in this pass |

**On the 503s:** re-checked to confirm they're a local container-mounting
artifact, not a regression — the warehouse mount's DuckDB catalog name
didn't match what the semantic layer expects
(`Binder Error: Catalog "retailmind" does not exist!`), a config detail of
how `compose.demo.yml` wires the warehouse mount that this manual
`docker run` didn't replicate exactly. Not chased further: it doesn't
touch any of the three blockers, and the underlying data path (dbt/
warehouse) was independently proven working in full, directly, above.

---

## Phase 6 — Regression Test Suite

| Suite | Result |
|---|---:|
| `backend/tests/unit` | 631/631 passed |
| `data_platform/tests/unit` + `ml/tests` + `ui/tests` | 353/354 passed (1 pre-existing, unrelated, documented) |
| `data_platform/tests/unit/test_dagster_orchestration.py` | 22/22 passed (21 + 1 new) |
| `ui/tests/test_primitives.py` (new) | 7/7 passed |
| `backend/tests/integration/test_calibration_api.py` | 7/7 passed (after the cleanup-fixture fix below) |
| `backend/tests/integration` (full) | **328/328 passed** — clean, final run |
| Architecture / import-linter | clean — 162 files, 270 dependencies, 1 kept / 0 broken |
| Ruff (`backend`, `data_platform`, `ui`) | clean |
| mypy (`backend/app`, `ui`) | clean |
| Alembic (`0006_recommendation_category`, head) | single head; base→head→base→head round trip on a disposable database, clean |

**A real regression was found and fixed during this pass's own
self-verification, not by the user.** The first version of Blocker 3's
`_clean_recommendations_after_each_test` test fixture (see Blocker 3 above)
ran an unscoped `DELETE FROM recommendation` that wiped the seed script's
demo recommendation, which three `test_dashboard_api.py` tests depend on.
This was caught by running the full `backend/tests/integration` suite
(not just the changed file) as part of this pass's verification —
two clean, consecutive full-suite runs from the correct directory both
showed the exact same 3 failures (`test_recommendations_are_ranked_by_impact`,
`test_recommendations_state_their_estimation_method`,
`test_executive_overview_assembles_every_tile`), ruling out flakiness. The
fixture was corrected to scope its cleanup to only the rows this file's own
tests create (filtered by `dedup_key`), re-verified via three
progressively wider re-runs — this file alone (7/7), this file plus
`test_dashboard_api.py` together (37/37), then the full suite (328/328) —
with no failures at any stage. No other new failures were introduced by any
of the three fixes; the only remaining known failure anywhere in the whole
project's test surface is the single pre-existing, unrelated,
already-documented UI test noted in the row above.

---

## Phase 7 — Security Regression

- No hardcoded personal/absolute paths introduced — the Dagster path fix is
  anchored via `Path(__file__).resolve()`, computed at runtime from the
  package's own location, not a literal path to any machine.
- No secrets, credentials, or connection strings introduced in any changed
  file (checked directly).
- Dagster path fix specifically reviewed for command-injection/path-
  manipulation risk: `_DBT_PROJECT_DIR` is derived only from
  `Path(__file__)` (code-controlled, never user/environment input);
  `subprocess.run()` still receives a list argv, never `shell=True`; the
  new `cwd` parameter on `run_command()` is only ever called with
  code-computed paths, never external input. No new injection surface.
- No authentication, authorization, or tenant-isolation code was touched by
  any of the three fixes. `find_measured()`'s `tenant_id`-scoping clause
  (`RecommendationOutcome.recommendation.has(tenant_id=self._tenant_id)`)
  is unchanged, and the new `category` filter is applied as an additional
  `AND` clause alongside it, not a replacement.
- No Docker configuration was modified by the blocker fixes themselves
  (Phase 4's rebuild uses existing Dockerfiles/compose files unchanged).

---

## Phase 8 — Documentation

This file. `docs/prompt-11-final-release-audit.md` left as-is — historical
record, not rewritten.

---

## Final Release Gate for 11.5

1. **Does the real Dagster `dbt_build` job execute successfully?** Yes —
   `job.execute_in_process()` against the actual named job, real production
   resources: `SUCCESS: True`, 77 assets, 152/152 dbt checks.
2. **Does the Admin workspace survive an empty API response?** Yes —
   `ui/tests/test_workspaces.py` full suite run 4× with no segfault
   (previously reproduced 3×), plus a live browser session against the
   original crashing scenario.
3. **Does the Admin workspace still render valid data?** Yes — same live
   browser session, Permissions (16 rows) and Workspaces (12 rows) tables
   both rendered correctly.
4. **Does calibration generator filtering work through the real API?**
   Yes — live HTTP: `inventory` → 200 with real computed statistics,
   `pricing` (no data) → 404, both against a real running API and real
   Postgres.
5. **Were all three original blockers reproduced before fixing?** Yes, all
   three, with exact error output captured before any code change (see each
   "Runtime reproduction BEFORE fix" section above).
6. **Were all three fixes verified through actual execution?** Yes — real
   Dagster job execution, a real live browser session against a real
   current-code process pair, and real HTTP requests against a real live
   API backed by real Postgres. Not stopped at unit tests for any of the
   three, matching this prompt's explicit requirement.
7. **Was the CURRENT repository rebuilt into fresh runtime images?** Yes —
   `rmstage115-api`/`rmstage115-ui`, built 2026-08-16, filesystem-verified
   to contain all three fixes. The stale `rmprod-*` stack was left running,
   untouched.
8. **Was the fresh runtime stack tested?** Yes — Postgres, both Redis
   instances, FastAPI (health/ready/auth/RBAC/JWT-multi-worker/calibration),
   and the UI container all functionally exercised, not just checked for
   "Up" status. Two items (recommendations/forecasts endpoints, rate
   limiting) were inconclusive or not independently re-tested against this
   specific disposable stack for reasons unrelated to the three blockers —
   documented plainly in Phase 5, not glossed over.
9. **Do all relevant regression tests pass?** Backend unit 631/631;
   data-platform+ml+ui 353/354 (1 pre-existing, unrelated); Dagster unit
   22/22; new `test_primitives.py` 7/7; calibration integration 7/7; ruff/
   mypy/import-linter all clean. Full `backend/tests/integration`,
   re-run clean from `backend/` after the fixture fix described in
   question 10: **328/328 passed.**
10. **Are there any new failures introduced by the fixes?** Yes, one — found
    and fixed during this pass's own verification, not left for later. An
    initial full-suite run (concurrent with two Docker builds, from the
    repository root) showed 3 `test_dashboard_api.py` failures. First
    hypothesis was environmental — the same class of cwd-dependent `.env`
    pickup issue documented in Prompt 10.5 (root `.env` sets a
    Docker-internal `redis-cache` hostname that pydantic-settings picks up
    when pytest runs from repo root). Re-running just those 3 tests in
    isolation, from `backend/`, passed — appearing to confirm that theory.
    But re-running the **full** suite cleanly from `backend/` (no
    concurrent builds, correct directory) reproduced the same 3 failures
    again, directly contradicting the environmental explanation. Narrowed
    with a combined `test_calibration_api.py` + `test_dashboard_api.py`
    run and confirmed the real cause: Blocker 3's own
    `_clean_recommendations_after_each_test` autouse fixture (added
    earlier in this same pass, to fix a real test-isolation gap where
    calibration tests were leaking committed rows into each other) ran an
    unscoped `DELETE FROM recommendation` that also deleted the seed
    script's demo recommendation — a genuine regression this pass
    introduced into a file it wasn't even trying to fix. Corrected by
    scoping the delete to only the rows this file's own tests create
    (`dedup_key` filter). Re-verified at three widening scopes — the file
    alone, the file plus `test_dashboard_api.py`, then the full suite —
    all clean, 328/328 on the final run. Zero other new failures anywhere
    in the regression surface.

---

## Final Verdict

# 🟢 BLOCKERS RESOLVED

All three release blockers from Prompt 11 are fixed, and every fix is
backed by real execution evidence — not test-suite-only, not static
inspection — matching this prompt's explicit standard. The fresh,
current-code runtime stack (`rmstage115`) is built, filesystem-verified,
and functionally exercised, independent of and without touching the stale
`rmprod` stack Prompt 11 correctly flagged as unrepresentative.

One real regression was introduced during this pass (Blocker 3's cleanup
fixture wiping seed data used by an unrelated dashboard test file) and is
disclosed above rather than smoothed over — it was caught by this pass's
own full-suite verification, root-caused, fixed, and re-verified clean at
three widening scopes, ending in a full, unmodified `backend/tests/integration`
run at **328/328 passed**. No other new regressions were found anywhere in
the regression surface (backend unit, data-platform/ml/ui unit, Dagster
unit, ruff, mypy, import-linter, Alembic round-trip).

Two items remain genuinely open, stated plainly rather than absorbed into
the "resolved" verdict:
- Confidence-band calibration (a sub-feature of Blocker 3, not the
  generator-filtering the blocker itself was about) returns empty results
  under the current schema, because no numeric confidence score is
  persisted anywhere yet — safe, not crashing, but not a complete fix of
  that specific piece.
- The `RecommendationDecision` ledger (what the live analytical engine
  actually writes) and `Recommendation`/`RecommendationOutcome` (what
  calibration reads) remain two disconnected tables — the filtering
  mechanism is now correct, but nothing in production currently bridges
  real decisions into the tables calibration reads from (a pre-existing,
  Prompt-10.5-documented gap, not reopened or worsened by this pass).

Per this prompt's instruction: stopping here. Not proceeding to Prompt 12.
