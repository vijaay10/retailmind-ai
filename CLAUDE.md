# Working on RetailMind AI

Instructions for Claude Code sessions in this repository.

## What this is

An existing, working, production-style platform — not a scaffold and not a
greenfield project. Roughly 60,000 lines across four uv workspace members, 794
unit tests and 305 integration tests passing, a verified production deployment,
and a one-command demo. It has been built and reviewed over many sessions.

**Treat it accordingly.** The bar for changing something here is higher than
the bar for writing it in the first place.

## The filesystem is the source of truth

Not a previous conversation, not a summary, not this file. Before you state
that a feature exists, open the file and confirm it.

This matters because the repository has a documented history of claiming things
that were not true — a README describing a Next.js frontend that was never
built, an Airflow tier that is an empty directory, an LLM gateway that was an
empty Python package. Several of those claims were written by earlier Claude
sessions. **Verify, then speak.**

Two rules follow:

- **Never invent an implemented feature.** If you cannot point at the file, it
  does not exist.
- **Never claim an unimplemented feature exists.** Including in comments,
  docstrings, commit messages, and especially the README.

### Things that specifically do not exist

Checked at the time of writing; re-verify rather than trusting this list.

| Frequently assumed | Reality |
|---|---|
| LLM / Claude / OpenAI integration | **None.** Zero API calls. Narration is deterministic and templated |
| Airflow / Dagster orchestration | None. Pipelines run via CLI |
| Terraform / Kubernetes | None |
| scikit-learn | Not a dependency. `RidgeForecaster` is hand-written numpy |
| Next.js frontend | None. The console is Streamlit |
| Backups / DR / Alertmanager | None |
| The "nine design documents" cited ~5,400 times in comments | Never committed |

## How to change things

**Inspect before modifying.** Read the file, and read what calls it. This
codebase carries unusually dense comments explaining *why* a thing is the way
it is — a decision that looks wrong is usually load-bearing, and the comment
above it usually says so.

**Make small changes.** One concern per change, one concern per commit.

**Do not redesign the architecture without approval.** Layering
(`api → services → domain`, with `infrastructure` implementing ports) is
enforced by import-linter in CI. Propose architectural changes and explain the
trade-off before writing code.

**Preserve existing interfaces** unless the change is explicitly approved.
Function signatures, API routes, database columns, and environment variable
names are all contracts something else depends on.

**Keep documentation synchronized.** If you change behaviour, update the
document that describes it in the same commit. `scripts/check_env.py` already
enforces this for environment variables; do the same by hand elsewhere.

## Verification is not optional

Run the relevant tests after every change and report the actual result.

```bash
make lint     # ruff + mypy — exactly what CI runs
make test     # 794 tests, no Docker, ~60s
```

Touching the API, the database, or the warehouse also means:

```bash
make test-integration   # 305 tests, needs Docker, ~15 min
```

**Never make a test pass by weakening it.** Do not delete assertions, do not
add `skip`, do not loosen a threshold to accommodate a regression. A failing
test is information.

**Never hide a failure.** No `|| true`, no `continue-on-error`, no swallowed
exceptions to make output look clean. This repository once shipped three CI
jobs that could not fail; they were labelled honestly rather than left to imply
a guarantee they did not provide.

**Report failures plainly.** If tests fail, say so and show the output. If you
skipped a step, say which.

## Git

Preserve history. Every commit is part of the record.

**Ask before anything destructive** — `git reset --hard`, `git clean -fd`,
force push, branch deletion, history rewriting. Do not do these because they
would be convenient.

**Conventional commits.** `feat:`, `fix:`, `docs:`, `test:`, `refactor:`,
`chore:`, `ci:`, `build:`. Explain *why* in the body — the existing history is
worth matching.

**Never add `Co-Authored-By` or AI attribution trailers.** The repository owner
is the sole author.

## Production configuration

`infra/compose/compose.prod.yml`, `infra/docker/nginx/`, and
`infra/monitoring/` are live production configuration. Changes there need
explicit approval and verification, not a quick edit.

Two properties are enforced by scripts because comments could not enforce them:

```bash
uv run python scripts/check_env.py     # .env.example matches the settings model
uv run python scripts/check_ports.py   # production publishes only the edge
```

If you change compose files, run both.

## Layout

```
backend/         FastAPI. api → services → domain → infrastructure
data_platform/   Ingestion, dbt (67 models), quality gates
ml/              Forecasting: features, models, backtest, registry
ui/              Streamlit console, 12 workspaces
infra/           Dockerfiles, compose overlays, nginx, monitoring
scripts/         Contract checks and TLS helpers
docs/            Deployment and testing guides
```

`make help` lists all 27 targets. `docs/development.md` has the local workflow.

## Known issues

Real, verified, and not yet fixed. Do not rediscover them as bugs.

- **Multiple uvicorn workers mint different JWT signing keys.** With no key
  configured the app generates an ephemeral RSA pair per process, so a token
  from one worker is rejected by another. The entrypoint defaults to two
  workers, meaning `make up` has intermittent 401s. `make demo` pins one
  worker. Only `staging`/`prod` enforce a configured key.
- **The genesis migration is `Base.metadata.create_all`**, not real
  `op.create_table` calls, so there is no replayable column-level history.
  Later migrations defensively check whether the table exists.
- **No backups, no Alertmanager, no Grafana dashboards.** The four Prometheus
  alert rules evaluate and notify nobody.
- **The README contains several false claims** (see the table above). A
  rewrite is planned and approved but not yet done.
