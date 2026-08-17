# Prompt 10.5 — Test Results

**Date:** 2026-08-15/16
**Method:** every number below is from an actual command run during this
pass, not carried over from a prior report. Commands are given so results
are reproducible.

## Summary table

| Suite | Total | Passed | Failed | Skipped | Status |
|---|---:|---:|---:|---:|---|
| Backend unit (`backend/tests/unit`) | 631 | 631 | 0 | 0 | 🟢 PASS |
| Data platform unit (`data_platform/tests/unit`) | included below | — | 0 | 0 | 🟢 PASS |
| Combined fast ladder (`backend/tests/unit data_platform/tests/unit ml/tests ui/tests`) | 977 | 976 | 1 | 0 | 🟡 PASS WITH KNOWN FAILURE |
| Dagster orchestration unit (`data_platform/tests/unit/test_dagster_orchestration.py`) | 21 | 21 | 0 | 0 | 🟢 PASS |
| Backend migration integration (`backend/tests/integration/test_migrations.py`, testcontainers) | 6 | 6 | 0 | 0 | 🟢 PASS |
| Backend + data-platform integration (`backend/tests/integration data_platform/tests/integration`, Docker, first run, before fixes) | 331 | 326 | 0 | 5 errors | 🟡 5 collection ERRORs, all in one file (see below) |
| Backend integration, full re-run after fixes (`backend/tests/integration`) | 327 | 324 | 3 | 0 | 🟡 collection error fixed (2 more tests now pass); 3 honest failures remain, real production bug identified, not fixed (out of scope — see below) |
| dbt integration (`data_platform/tests/integration/test_dbt_models.py`, 67 dbt models built for real) | 4 | 4 | 0 | 0 | 🟢 PASS (confirmed in the first combined run; not re-run since nothing touched by this pass affects it) |
| **Backend + data-platform integration, final combined total** | **331** | **328** | **3** | **0** | 🟡 PASS WITH KNOWN, DOCUMENTED FAILURE |
| Architecture / import-linter (`uv run lint-imports`) | 1 contract, 270 deps, 162 files | 1 | 0 | 0 | 🟢 PASS |
| Ruff (`uv run ruff check .`) | 60 issues found before this pass | 60 fixed | 0 | — | 🟢 PASS |
| Ruff format (`uv run ruff format --check backend data_platform ml ui`) | 18 files needed reformatting | 18 fixed | 0 | — | 🟢 PASS |
| mypy (`uv run mypy backend/app` + `uv run mypy ui`, exactly what `make lint` runs) | 4 errors found | 4 fixed | 0 | — | 🟢 PASS |
| `scripts/check_env.py` | 17 problems found | 17 fixed | 0 | — | 🟢 PASS |
| `scripts/check_ports.py` | 1 problem found | 1 fixed | 0 | — | 🟢 PASS |
| `scripts/check_docs_integrity.py` | — | — | 0 | — | 🟢 PASS |
| Backup script, real execution | 1 | 1 (after fix) | 0 | — | 🟢 PASS |
| Restore script, real execution (same-name target) | 1 | 1 (after fix) | 0 | — | 🟢 PASS |
| Restore script, real execution (renamed target) | 1 | 0 | 1 | — | 🔴 NOT SUPPORTED (documented limitation, not a regression) |
| Documentation integrity checker | — | — | 0 | — | 🟢 PASS |

*Note on the combined fast ladder:* run twice, independently, before and
after the mypy/ruff-format fixes, with identical results both times (976/977)
— the one failure is stable, not flaky.

*Note on the integration re-run:* `backend/tests/integration` alone
(327 tests) was run standalone rather than combined with `data_platform/tests/integration`
again, since the dbt suite (4 tests) is untouched by anything fixed in this
pass and was already confirmed green — no reason to pay for another ~5-minute
dbt build to re-prove it. `backend/tests/integration/test_calibration_api.py`'s
result is unchanged between its isolated run and this full-suite run (3
failed, 3 passed both times) — the failures are deterministic, not order- or
isolation-dependent.

## Every remaining failure, in detail

### `backend/tests/integration/test_calibration_api.py` — collection error, fixed; 3 real failures found and left open

**Collection error (fixed):** all 5 tests referencing `auth_headers` as a test
parameter (`async def test_x(client, auth_headers: dict)`) errored with
`fixture 'auth_headers' not found`. `auth_headers` is a plain async helper
function in `conftest.py`, not a `@pytest.fixture` — every other integration
test file in the suite already knows this and calls it explicitly
(`await auth_headers(client, role)`); this file was the one exception.
Fixed by matching the established convention: explicit import + `await`
call, plus locally-defined `session`/`tenant_id` fixtures (neither exists in
`conftest.py` either — built the same way `test_notifications_repository.py`'s
`repository` fixture does, from `migrated_db`). Also fixed a trivial
punctuation mismatch (`"No measured outcomes available yet"` vs. the real
`"No measured outcomes available yet."`) once the fixture error stopped
masking it.

**3 real failures found, root cause identified, not fixed:**
`test_calibration_summary_with_measured_outcomes`,
`test_generator_calibration_success`, `test_confidence_calibration` — all
three build a `Recommendation` row with `type="inventory"` or
`type="pricing"`, then query `/recommendations/calibration/generators/inventory`
expecting a match. This fails at the database layer
(`ck_recommendation_type_valid` — `Recommendation.type` only accepts
`reorder | markdown | promo | assortment`, per `RecommendationType` in
`backend/app/infrastructure/db/models/enums.py`) and, if the constraint were
loosened, would still fail at the application layer:
`OutcomeRepository.find_measured()` in
`backend/app/infrastructure/db/repositories/outcomes.py` filters on
`Recommendation.type == generator`, but the "generator" concept the
calibration API documents (`inventory | pricing | promotion | store |
customer | supplier` — see `CalibrationService.get_generator_performance`'s
own docstring) is a different vocabulary than `RecommendationType` entirely.
**This is a real, pre-existing bug in the calibration feature's data model —
the "filter by generator" capability cannot work as documented, for any
caller, not just this test** — but it belongs to the outcome-measurement/
calibration feature that is itself out of Prompt 10.5's P0–P3 scope (it's
part of the same uncommitted "Prompt 9/10" work already flagged elsewhere in
this remediation as needing its own review). Fixing it means a product
decision — does `Recommendation.type` become the seven-category vocabulary,
or does a separate `category` column get added — which is exactly the kind
of architectural call CLAUDE.md says needs approval before code, not a
remediation-pass patch. Left failing, honestly, rather than adjusted to pass
on a fixed test value that wouldn't reflect what a real caller hits.

### `ui/tests/test_workspaces.py::test_command_center_loads_without_error`

- **Exact test:** `ui/tests/test_workspaces.py::test_command_center_loads_without_error`
- **Root cause:** the test asserts the page's greeting text is
  `app.markdown[0].value`. In the current `ui/workspaces/1_Command_Center.py`,
  `design.configure(...)` (called at module import, before the greeting) emits
  a global `<style>` block via `st.markdown` first, so index 0 is CSS, not the
  greeting.
- **Production impact:** none directly — this is a test assumption about
  Streamlit's `AppTest` markdown-element ordering, not a defect in what the
  page renders to a real browser.
- **Acceptable:** yes, as a known, pre-existing, out-of-scope finding. It
  predates this remediation pass (the file was already modified in the
  working tree before this session touched anything, and this session made
  no changes to `ui/workspaces/1_Command_Center.py`, `ui/tests/test_workspaces.py`,
  or `ui/retailmind_ui/design.py`).
- **Next action:** update the test to search `app.markdown` for the greeting
  text rather than assume index 0, or reorder `design.configure()`'s CSS
  injection to not be the first markdown element. Neither was done here —
  out of Prompt 10.5's P0–P3 scope, and CLAUDE.md's instruction against
  "redesign the architecture without approval" argues for leaving UI-layer
  test-fixture assumptions to a dedicated pass rather than a quick patch
  during backend/data-platform remediation.

### Restore into a renamed target database

- **Exact scenario:** `scripts/restore-postgres.sh` run with `RM_DB_NAME`
  set to a database name different from the one the backup was taken from.
- **Root cause:** `pg_dump --create` bakes the source database's name into
  an embedded `\connect <source-name>` directive; `psql` honors it and
  switches onto that database regardless of `RM_DB_NAME`.
- **Production impact:** none for the actual disaster-recovery path (restore
  prod's own backup back into itself, same name) — proven working. Only
  affects restoring into an intentionally-renamed sandbox/disposable
  database for drills.
- **Acceptable:** yes, as a documented limitation — fixing it means changing
  the dump format (`pg_restore`/custom format, or post-processing the SQL),
  which is an architectural trade-off outside a remediation pass's mandate.
- **Next action:** if renamed-target restores become a real requirement,
  switch `backup-postgres.sh` to `pg_dump --format=custom` (drop `--create`)
  and `restore-postgres.sh` to `pg_restore --dbname="${DB_NAME}" --clean
  --if-exists`, which supports arbitrary target names natively.

## Not (yet) run

- **k6 load tests** — `docs/load-testing-results.md`'s numbers were already
  estimated, not measured (k6 not installed); this pass didn't install it or
  re-run load tests. Out of Prompt 10.5's explicit scope.
- **Backup cron schedule** (`compose.prod.yml`'s `backup` profile, daily
  02:00 UTC) — the underlying script it calls is now proven to work; the
  schedule itself wasn't exercised.
- **ml/tests, ui/tests individually broken out** — included in the combined
  977-test run above; not re-run in isolation since the combined run is what
  `make test` actually executes.
