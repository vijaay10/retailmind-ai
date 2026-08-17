# Prompt 11 — Independent Audit Baseline

**Established:** 2026-08-16, at the start of this audit, by running the
commands below directly — not copied from any prior report.

## Repository state

- **Branch:** `main`
- **HEAD commit:** `2fc3ab49a7646f3a21e5fbcf55afe39ad156c10b` — "fix: let `make api` bind a port other than 8000" (2026-08-13 13:17:38 +0530)
- **Last 5 commits:**
  ```
  2fc3ab4 fix: let `make api` bind a port other than 8000
  f981285 chore: make the repository reproducible outside the machine that wrote it
  24047fa Make `make demo` actually boot a working demo
  13f612b Remove placeholders from directories that have real contents
  6fe0cb2 Make CI report what it actually enforces
  ```
- **Working tree:** 181 modified/untracked paths (`git status --porcelain=v1`).
  **The repository did not change during Prompt 10.5 in the sense that
  matters for an audit: HEAD is unchanged, and nothing was committed.**
  Every fix Prompt 10.5 made — the migration transaction bug, the backup
  script bug, the restore script bug, the ruff/mypy fixes, the calibration
  test fix, all of it — exists only in the uncommitted working tree, exactly
  where the Prompt 9A/9B/10 work already was. This audit evaluates that
  working tree as it stands, not a commit, because there is no commit to
  point at.

## Toolchain versions (measured, not assumed)

| Component | Version | How measured |
|---|---|---|
| Python (system `python3`) | 3.14.0 | `python3 --version` |
| Python (project venv, `uv run`) | 3.12.0 | `uv run python --version` — this is the version that actually matters; the repo pins to it |
| uv | 0.12.0 | `uv --version` |
| Docker | 28.5.1 | `docker --version` |
| Docker Compose | v2.40.3-desktop.1 | `docker compose version` |
| Node.js | v22.14.0 | `node --version` (not load-bearing — no Node.js code in this repo; the UI is Streamlit) |
| PostgreSQL (production container) | 16.4 (Debian 16.4-1.pgdg120+2) | `docker exec rmprod-postgres-1 postgres --version` |
| Redis (production container) | 7.4.10 | `docker exec rmprod-redis-state-1 redis-server --version` |
| DuckDB (Python package) | 1.5.5 | `uv run python -c "import duckdb; print(duckdb.__version__)"` |
| dbt-core | 1.11.13 (1.12.2 available) | `dbt --version` |
| dbt-duckdb plugin | 1.10.1 | `dbt --version` |
| Dagster | 1.13.17 | `uv run python -c "import dagster; print(dagster.__version__)"` |
| Local `psql`/`pg_dump` client | **not installed on this host** | `psql --version` → command not found. Every Postgres-touching command in this audit runs inside a container or through `uv run` (asyncpg/psycopg), never a bare host `psql`. |

## Dependency lockfile

- `uv.lock` shows as modified in `git status` (part of the same uncommitted
  working tree as everything else — not something any Prompt 10.5 action
  touched; no dependency-management command was run during that pass).
- `uv lock --check` → **passes** ("Resolved 275 packages"), meaning
  `uv.lock` is currently consistent with `pyproject.toml` as of this audit,
  regardless of what changed it before.

## Running infrastructure at audit start

`docker ps` shows an already-running `rmprod-*` stack (11 containers), up 8
days, predating this audit and the Prompt 10.5 session both:
`rmprod-ui-1`, `rmprod-api-1`, `rmprod-worker-1`, `rmprod-beat-1`,
`rmprod-edge-1`, `rmprod-grafana-1`, `rmprod-postgres-1`,
`rmprod-redis-state-1`, `rmprod-redis-cache-1`, `rmprod-prometheus-1`,
`rmprod-minio-1`. This audit treats it as read-only infrastructure to probe,
per the prompt's constraints — no container in it is started, stopped, or
modified by this audit.

## What this baseline does NOT assume

Per this audit's own instructions, no file count, test count, or pass/fail
number from `docs/prompt-10.5-*` is treated as current truth here without
independent re-execution. See `docs/prompt-11-final-release-audit.md` §3 for
the reproduced verification matrix.
