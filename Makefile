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
	@echo "→ app will be at http://localhost:8080 once services are healthy"

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
.PHONY: seed
seed: ## Generate + land the synthetic retailer (Northwind Threads)
	@echo "TODO(S1): data_platform/ingestion/generators entrypoint"

.PHONY: backfill
backfill: ## Backfill a window: make backfill START=2026-06-01 END=2026-06-15 SOURCES=pos
	@echo "TODO(S2): data_platform/ingestion/cli backfill $(START) $(END) $(SOURCES)"

.PHONY: dbt-build
dbt-build: ## dbt build against local DuckDB profile
	@echo "TODO(S2): cd data_platform/dbt && dbt build --profiles-dir ."

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
