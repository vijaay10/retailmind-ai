# Local development

Every command here was run against a clean clone before being written down.
Where a step has a prerequisite that is easy to miss, the prerequisite is
stated rather than assumed.

## Prerequisites

| Tool | Why |
|---|---|
| [uv](https://docs.astral.sh/uv/) | Dependency management and the Python toolchain. It installs the pinned interpreter itself — you do not need Python first. |
| Docker + Docker Compose | Postgres, Redis, and the containerised stack. |
| `make` | Every workflow's entry point. |
| Git | — |

Python is **pinned to 3.12** in `.python-version`, matching the runtime
images so local behaviour and production behaviour cannot diverge on an
interpreter difference. `uv` reads that file and installs 3.12 for you.

---

## The short version

```bash
git clone <repo-url> && cd retailmind-ai
make demo
```

About two and a half minutes, then open <http://localhost:8501> and sign in as
`priya@northwind.example` / `ChangeMe-Demo1!`.

That path builds images, generates a synthetic retailer, applies migrations and
seeds a tenant — everything below, done for you, in containers. The rest of
this document is for when you want to run the code in a debugger instead.

---

## The full sequence

### 1. Clone

```bash
git clone <repo-url> && cd retailmind-ai
```

### 2. Install dependencies

```bash
uv sync --all-packages
```

Installs Python 3.12 if absent, creates `.venv`, and resolves all four
workspace members from `uv.lock`. `--all-packages` matters: without it you get
the backend only, and `dbt`, the ETL and the ML package are missing.

### 3. Configure environment variables

```bash
cp .env.example .env
```

Optional — every service has a working default, which is why `make demo` needs
no `.env` at all.

**One thing to know if you do create it.** `.env.example` sets
`RM_DB_HOST=postgres`, which is the Docker service name and does not resolve
from your host. When running the API natively, override it:

```bash
RM_DB_HOST=localhost make migrate
```

The VS Code launch configurations set this for you.

### 4. Start infrastructure

Everything:

```bash
make up
```

Or just the databases, if you want to run the API and console in a debugger:

```bash
docker compose -f infra/compose/compose.yml -f infra/compose/compose.dev.yml \
  up -d postgres redis-cache redis-state
```

| Service | Host port |
|---|---|
| Postgres | 5432 |
| Redis (cache) | 6379 |
| Redis (state / Celery broker) | 6380 |
| API (containerised) | 8090 |
| Console (containerised) | 8501 |
| MinIO | 9000 / 9001 |
| Mailpit (caught email) | 8025 |

### 5. Run migrations

```bash
RM_DB_HOST=localhost make migrate
```

### 6. Seed development data

```bash
RM_DB_HOST=localhost make seed-demo
```

Reference roles plus the *Northwind Threads* tenant: seven users, one per role,
all with password `ChangeMe-Demo1!`. Idempotent — it keys off the tenant slug
and skips if the tenant already exists, so **a stale Postgres volume means it
does nothing.** Use `make demo-down` for a genuinely clean start.

Sign in as `priya@northwind.example` (CEO) to see the whole console;
`lena@northwind.example` (store manager) to see how much of it disappears.

### 7. Run the ETL

```bash
make demo-rebuild
```

Generates 28 days of synthetic sources across 4 stores, ingests them, then runs
`dbt seed`, `snapshot` and `build` — writing `.local/demo/retailmind.duckdb`
and verifying it is queryable before reporting success. About two minutes.

Other entry points:

```bash
make etl-demo     # generate and ingest a single day
make warehouse    # dbt seed + snapshot + build against an existing warehouse
make forecast     # train models, then rebuild so dbt unions the predictions in
```

### 8. Start the backend

```bash
make api
```

Serves on <http://localhost:8000> with reload. Needs Postgres running and
migrations applied.

In VS Code: **Run and Debug → "API (FastAPI, reload)"** — same thing, with
breakpoints, and `RM_DB_HOST` already set.

### 9. Start the console

```bash
make console
```

Serves on <http://localhost:8501>. It talks to the API over HTTP, so point it
at whichever one you are running:

```bash
RM_API_BASE_URL=http://localhost:8000 make console   # the local API from step 8
RM_API_BASE_URL=http://localhost:8090 make console   # the containerised one
```

In VS Code: **"Console (Streamlit)"**, or the **"Full stack (API + Console)"**
compound to debug both at once.

### 10. Run tests

```bash
make test               # 794 tests, no Docker, ~60s — run this constantly
make test-integration   # 305 tests, real Postgres + built warehouse, ~15 min
make coverage           # both, with a report
```

The fast ladder needs no services. The integration suite starts Postgres via
testcontainers and builds two warehouses, shared across suites.

### 11. Lint

```bash
make lint    # ruff check + ruff format --check + mypy (twice, see below)
make fmt     # apply formatting
```

mypy runs twice because `backend/app` and `ui/app.py` both resolve to the
module name `app` and mypy refuses the collision.

### 12. Type check

Included in `make lint`. Separately:

```bash
uv run mypy backend/app
uv run mypy ui
```

### 13. Stop the environment

```bash
make down        # stop containers, keep data
make demo-down   # stop containers, delete volumes and the built warehouse
```

### 14. Reset safely

Ordered from least to most destructive. **Nothing here touches git history.**

```bash
# Rebuild the warehouse only
make demo-rebuild

# Drop the database and the warehouse, keep images
make demo-down

# Also rebuild images from scratch
make demo-down && docker compose -f infra/compose/compose.yml build --no-cache

# Rebuild the Python environment
rm -rf .venv && uv sync --all-packages
```

`make demo-down` removes Docker volumes and `.local/demo`. It does not touch
tracked files, `.env`, or anything in git.

---

## Before you push

```bash
make lint && make test
uv run python scripts/check_env.py     # .env.example vs the settings model
uv run python scripts/check_ports.py   # production publishes only the edge
```

CI runs all four, plus the integration suite and the image build. In VS Code
the **"Check: everything CI checks"** task runs the sequence.

---

## Troubleshooting

**`Catalog "wh" does not exist`** — DuckDB takes its catalog name from the
filename and dbt compiles it into every view. The warehouse was renamed after
being built. Rebuild with `make demo-rebuild`.

**Intermittent `Access token is not valid`** — more than one uvicorn worker,
each minting its own ephemeral signing key. Set `RM_API_WORKERS=1` or configure
`RM_AUTH_JWT_PRIVATE_KEY_PEM`. Known issue; `make demo` pins one worker.

**`warehouse is temporarily unavailable` (503)** — no warehouse at
`RM_WAREHOUSE_DUCKDB_PATH`. Run `make demo-rebuild`.

**Seed appears to do nothing** — it is idempotent by tenant slug and a previous
volume survived. `make demo-down` first.

**Port already allocated** — something else holds 8090, 8501, or 5432. Every
port is overridable: `RM_API_PORT=9090 RM_UI_PORT=9501 make up`.

**dbt cannot find a profile** — run dbt from `data_platform/dbt` with
`--profiles-dir .`, which is what every make target does.
