# RetailMind AI — developer entrypoints
# Convention: every target is safe to re-run; anything destructive asks first.

COMPOSE      := docker compose -f infra/compose/compose.yml
COMPOSE_DEMO := $(COMPOSE) -f infra/compose/compose.demo.yml
COMPOSE_DEV  := $(COMPOSE) -f infra/compose/compose.dev.yml

.DEFAULT_GOAL := help

.PHONY: help
help: ## List available targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

# ── Stack ─────────────────────────────────────────────────────────────
.PHONY: demo
demo: ## Boot full stack + synthetic retailer (the recruiter path)
	$(COMPOSE_DEMO) up -d --build
	@echo "TODO(S1): seed synthetic retailer + run first DAG cycle"
	@echo "→ app will be at http://localhost:$${RM_API_PORT:-8090} once services are healthy"

.PHONY: up
up: ## Dev stack with hot reload
	$(COMPOSE_DEV) up -d --build

.PHONY: down
down: ## Stop the stack (volumes preserved — never pass -v casually)
	$(COMPOSE) down

.PHONY: logs
logs: ## Tail all service logs
	$(COMPOSE) logs -f --tail=100

# ── Quality ───────────────────────────────────────────────────────────
.PHONY: lint
lint: ## Lint + typecheck everything
	uv run ruff check backend data_platform ml
	uv run ruff format --check backend data_platform ml
	uv run mypy backend/app
	@echo "TODO(S2): sqlfluff lint data_platform/dbt"

.PHONY: fmt
fmt: ## Auto-format
	uv run ruff format backend data_platform ml
	uv run ruff check --fix backend data_platform ml

.PHONY: test
test: ## Run the fast test ladder (unit)
	uv run pytest backend/tests/unit data_platform/tests/unit -q

.PHONY: test-integration
test-integration: ## Integration tests (needs Docker)
	uv run pytest backend/tests/integration data_platform/tests/integration -q

# ── Data platform ─────────────────────────────────────────────────────
.PHONY: backfill
backfill: ## Backfill every source over a window: make backfill START=2026-06-01 END=2026-06-22
	@for pair in $(PER_STORE_SOURCES); do \
		echo "→ backfilling $${pair%%:*}.$${pair##*:}"; \
		uv run python -m ingestion.cli backfill $(START) $(END) \
			--source $${pair%%:*} --table $${pair##*:} \
			--expected-stores $(RM_DEMO_STORES) || exit 1; \
	done
	@for pair in $(ESTATE_SOURCES); do \
		echo "→ backfilling $${pair%%:*}.$${pair##*:}"; \
		uv run python -m ingestion.cli backfill $(START) $(END) \
			--source $${pair%%:*} --table $${pair##*:} || exit 1; \
	done

# The demo estate is smaller than the production one the schemas declare, so
# completeness has to be told how many stores to expect. The split below is
# not cosmetic: per-store sources land one file per store and must be checked
# against the estate size, while purchasing lands a single file for the whole
# chain. Passing the store count to that one quarantines every partition for
# arriving 39 files short.
RM_DEMO_STORES ?= 40
RM_DEMO_DAY    ?= 2026-07-21
PER_STORE_SOURCES := pos:sales inventory:positions
ESTATE_SOURCES    := purchasing:orders weather:observations fulfilment:deliveries

.PHONY: etl-demo
etl-demo: ## Generate synthetic source files and ingest every source
	uv run python -m ingestion.cli generate --day $(RM_DEMO_DAY) --stores $(RM_DEMO_STORES)
	@for pair in $(PER_STORE_SOURCES); do \
		echo "→ ingesting $${pair%%:*}.$${pair##*:}"; \
		uv run python -m ingestion.cli run --source $${pair%%:*} --table $${pair##*:} \
			--day $(RM_DEMO_DAY) --expected-stores $(RM_DEMO_STORES) || exit 1; \
	done
	@for pair in $(ESTATE_SOURCES); do \
		echo "→ ingesting $${pair%%:*}.$${pair##*:}"; \
		uv run python -m ingestion.cli run --source $${pair%%:*} --table $${pair##*:} \
			--day $(RM_DEMO_DAY) || exit 1; \
	done

.PHONY: warehouse
warehouse: ## Build the dimensional warehouse (seeds, snapshots, models, tests)
	cd data_platform/dbt && uv run dbt seed --profiles-dir .
	cd data_platform/dbt && uv run dbt snapshot --profiles-dir .
	cd data_platform/dbt && uv run dbt build --profiles-dir .

.PHONY: forecast
forecast: ## Train forecast models and publish predictions (run after `make warehouse`)
	uv run retailmind-forecast train
	@echo "→ rebuilding so dbt unions the forecasts into fct_forecast"
	cd data_platform/dbt && uv run dbt build --profiles-dir . --select fct_forecast+

.PHONY: report-demo
report-demo: ## Render a report to PDF, PPTX, and XLSX in ./build
	uv run python scripts/render_report.py build

.PHONY: warehouse-docs
warehouse-docs: ## Generate and serve the dbt documentation site
	cd data_platform/dbt && uv run dbt docs generate --profiles-dir .
	cd data_platform/dbt && uv run dbt docs serve --profiles-dir .

# ── Backend ───────────────────────────────────────────────────────────
.PHONY: migrate
migrate: ## Apply database migrations
	cd backend && uv run alembic upgrade head

.PHONY: migrate-sql
migrate-sql: ## Print migration SQL for review without applying
	cd backend && uv run alembic upgrade head --sql

.PHONY: seed
seed-db: ## Seed reference data (roles) — safe everywhere, idempotent
	cd backend && uv run python -m app.infrastructure.db.seeds.reference

.PHONY: seed-demo
seed-demo: seed-db ## Seed the Northwind Threads demo tenant (refuses in prod)
	cd backend && uv run python -m app.infrastructure.db.seeds.sample

.PHONY: api
api: ## Run the API locally (no Docker)
	cd backend && uv run uvicorn app.main:create_app --factory --reload --port 8000
