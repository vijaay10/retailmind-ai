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
DEMO_WAREHOUSE := .local/demo/retailmind.duckdb

.PHONY: demo
demo: ## Boot the full stack with a synthetic retailer (the first-run path)
	@echo "→ building images and generating the retailer at the same time"
	@# Concurrently, and that is the whole reason this hits its time budget.
	@# The image build and the data build share nothing: run in sequence they
	@# cost the sum, run together they cost the slower one. `wait` on each PID
	@# individually rather than bare `wait`, so a failure in either is still a
	@# non-zero exit instead of a demo that boots with no data in it.
	@set -e; \
	$(MAKE) --no-print-directory demo-warehouse & WAREHOUSE=$$!; \
	$(COMPOSE_DEMO) build --quiet & IMAGES=$$!; \
	wait $$WAREHOUSE; \
	wait $$IMAGES
	@echo "→ starting services (migrations and seed run first)"
	$(COMPOSE_DEMO) up -d --wait
	@$(MAKE) --no-print-directory demo-banner

.PHONY: demo-warehouse
demo-warehouse: ## Build the demo warehouse only (generate → ingest → dbt)
	@# Skipped when it already exists: the second `make demo` of the day should
	@# take fifteen seconds, not two minutes. `make demo-rebuild` forces it.
	@if [ -f $(DEMO_WAREHOUSE) ]; then \
		echo "→ warehouse already built ($(DEMO_WAREHOUSE)) — make demo-rebuild to regenerate"; \
	else \
		cd data_platform && uv run python -m ingestion.cli demo-warehouse \
			--out ../$(DEMO_WAREHOUSE); \
	fi

.PHONY: demo-rebuild
demo-rebuild: ## Regenerate the demo warehouse from scratch
	rm -f $(DEMO_WAREHOUSE)
	@$(MAKE) --no-print-directory demo-warehouse

.PHONY: demo-banner
demo-banner: ## Print where the demo is and how to sign in
	@printf '\n  \033[1mRetailMind AI is running.\033[0m\n\n'
	@printf '  Console   \033[36mhttp://localhost:%s\033[0m\n' "$${RM_UI_PORT:-8501}"
	@printf '  API docs  \033[36mhttp://localhost:%s/api/docs\033[0m\n\n' "$${RM_API_PORT:-8090}"
	@printf '  Sign in as the CEO to land on the Command Center:\n'
	@printf '    email     \033[1mpriya@northwind.example\033[0m\n'
	@printf '    password  \033[1mChangeMe-Demo1!\033[0m\n\n'
	@printf '  Six more users exist, one per role, same password — see\n'
	@printf '  backend/app/infrastructure/db/seeds/sample.py. Demo credentials,\n'
	@printf '  seeded only when RM_APP_ENV=dev.\n\n'
	@printf '  make demo-down   stop everything and delete the data\n\n' 

.PHONY: demo-down
demo-down: ## Stop the demo and remove its data
	$(COMPOSE_DEMO) down -v
	rm -rf .local/demo

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
	uv run ruff check backend data_platform ml ui
	uv run ruff format --check backend data_platform ml ui
	# Two invocations, not one: `ui/app.py` and `backend/app/` both map to
	# the module name `app`, and mypy refuses the collision.
	uv run mypy backend/app
	uv run mypy ui
	@echo "TODO(S2): sqlfluff lint data_platform/dbt"

.PHONY: fmt
fmt: ## Auto-format
	uv run ruff format backend data_platform ml ui
	uv run ruff check --fix backend data_platform ml ui

.PHONY: test
test: ## Run the fast test ladder (no Docker, seconds)
	uv run pytest backend/tests/unit data_platform/tests/unit ml/tests ui/tests -q

.PHONY: coverage
coverage: ## Full suite with a combined coverage report
	uv run pytest -q --cov --cov-report=term-missing --cov-report=html:htmlcov
	@echo "→ open htmlcov/index.html"

.PHONY: coverage-gate
coverage-gate: ## The bars CI enforces
	uv run coverage report --include='backend/app/domain/*,backend/app/services/*' --fail-under=85
	uv run coverage report --fail-under=80

.PHONY: test-integration
test-integration: ## Integration tests — Postgres + a built warehouse (needs Docker)
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

.PHONY: console
console: ## Run the Streamlit console (point RM_API_BASE_URL at a running API)
	cd ui && RM_API_BASE_URL=$${RM_API_BASE_URL:-http://localhost:8090} \
		uv run streamlit run app.py --server.port $${RM_UI_PORT:-8501}
