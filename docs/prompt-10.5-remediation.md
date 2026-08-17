# Prompt 10.5 — Remediation Checklist

Status as of 2026-08-15. This is a live checklist, updated as each item is
verified against the actual repository (not against prior audit narrative).
Per CLAUDE.md: the filesystem is the source of truth, not the prior 9A/9B/10
reports. Several items below were reported as broken in those docs and were
found, on direct inspection, to already be fixed in the current (uncommitted)
working tree — those are marked accordingly rather than re-"fixed" for show.

## P0 / CRITICAL

1. **JWT signing key must be stable/configurable for multi-worker deployments.**
   Status: **code already correct, tests were missing.**
   `backend/app/core/security.py::_load_or_generate_keypair` + `backend/app/core/config.py::AuthSettings.require_configured_keys`
   already implement the required behavior: a configured `RM_AUTH_JWT_PRIVATE_KEY_PEM`/`_FILE`
   is used verbatim (all workers/replicas load the same key from the same
   file); outside `dev`, an unconfigured key is a hard `RuntimeError` at
   startup, not a silently-generated one; `infra/compose/compose.prod.yml`
   wires `RM_AUTH_JWT_PRIVATE_KEY_FILE` to a Docker secret shared by all
   replicas. This was likely fixed in an earlier, already-committed change —
   `git diff` shows no pending changes to these lines. What was missing:
   tests proving the cross-worker/fail-safe behavior (Phase 2 requirement).
   Added in this pass. See `docs/prompt-10.5-final-report.md` for the
   configuration writeup.

## P1 / HIGH

2. **Alembic migration chain broken (0004/0005).**
   Status: **already fixed, verified.** `alembic heads` returns exactly one
   head (`0005_llm_usage_tracking`); `alembic history` shows an unbroken
   0001→0002→0003→0004→0005 chain. The `0005_llm_usage_tracking` migration
   file's `down_revision` correctly points at `"0004_outcome_measurement"`.
   Confirmed by direct inspection of
   `backend/app/infrastructure/db/migrations/versions/202608131800_llm_usage_tracking.py`,
   not by trusting the prior audit (which described a stale/wrong state).
   Round-trip upgrade/downgrade/upgrade test against a disposable Postgres:
   see Phase 3 results below.

3. **Backup execution never verified.**
   Status: confirmed unverified prior to this pass — scripts existed but had
   only been syntax-checked (`bash -n`), never actually run. Addressed in
   Phase 8; see `docs/backup-restore-test-results.md` for the real run.

## P2 / MEDIUM

4. **Dagster test failures from deprecated API usage.**
   Status: **fixed.** The specific API named in the original ticket
   (`get_all_job_defs()`) had already been replaced with
   `resolve_all_job_defs()` in the working tree. The real failures (14 of 21
   tests) were a different, unreported problem: direct asset/check invocation
   passing `Mock(spec=CliExecutor)` / `Mock(spec=DuckDBWarehouse)`, which
   fails Dagster 1.13's resource-type validation on direct invocation even
   though `isinstance(mock, ResourceClass)` holds in isolation; a removed
   `Definitions.get_all_asset_specs()` API (replacement:
   `resolve_all_asset_specs()`); a test assuming `AssetsDefinition` exposes a
   `_op_def` attribute (it doesn't, in this Dagster version); an
   `AssetCheckResult.metadata` test comparing a raw int against a
   `MetadataValue` wrapper; a `:memory:` DuckDB test relying on state
   surviving across connections it doesn't (each `:memory:` connect is
   independent); and a substring key-match bug that silently matched the
   wrong asset. All fixed; 21/21 tests pass. Also removed 4 uses of the
   deprecated `define_asset_job(partitions_def=...)` parameter from
   production code (`orchestration/dagster/schedules.py`) — confirmed
   Dagster still infers and resolves the same partition scheme without it.

5. **DB schema table-count / metadata test.**
   Status: **already correct.** `backend/tests/unit/test_db_schema.py`
   asserts 42 tables and documents why (genesis set +
   `recommendation_decision` + `recommendation_outcome` + `llm_request_log`).
   All 131 tests in this file pass as-is. The prior audit's claim of a
   41-vs-44 mismatch does not match the file on disk.

6. **Enum/check constraint (severity) test.**
   Status: **already correct.** The same file asserts
   `severity IN ('info', 'low', 'medium', 'high', 'critical')`, matching the
   intended 5-value severity scale. Passes as-is.

## P3 / LOW

7. **Ruff issues.**
   Status: the "11 issues" figure in the original ticket does not match
   reality — a full `ruff check .` found **60** errors repo-wide before this
   pass (see breakdown in the final report). Not all are "safe" fixes;
   addressed the ones directly touched by this remediation and re-ran
   `ruff check .` for the true remaining count — see
   `docs/prompt-10.5-test-results.md`.

8. **TODO/FIXME review.** See Phase 7 results in the final report.

## Additional blockers found during this pass (not in the original ticket)

- Migration round-trip integration test (`backend/tests/integration/test_migrations.py`)
  had never actually been run against a live Postgres — verified in Phase 3.
- Prior audit docs contain internally inconsistent test counts and at least
  one hallucinated file path (`backend/app/infrastructure/auth/jwt_signer.py`,
  which does not exist — the real file is `backend/app/core/security.py`).
  Treat every number in `docs/known-issues.md`, `docs/e2e-verification-corrected.md`,
  `docs/production-readiness-final-report.md`, and `docs/prompt-10-completion-summary.md`
  as unverified narrative until re-checked against the filesystem.
- The working tree contains a substantial, uncommitted LLM gateway
  (`backend/app/infrastructure/llm/`, `backend/app/services/analyst/narrator.py`,
  `backend/app/services/calibration/`, `backend/app/services/outcomes/`) that
  directly contradicts CLAUDE.md's "Things that specifically do not exist"
  table ("LLM / Claude / OpenAI integration: None. Zero API calls."). The
  gateway defaults to a mock provider (`RM_LLM_MOCK=true` when no API key is
  configured — see `backend/app/core/config.py::LLMSettings`), so no real API
  calls happen without explicit configuration, but CLAUDE.md is stale on this
  point and should be corrected in a separate, explicit change — out of scope
  for this remediation pass, flagged here so it isn't silently ignored.
