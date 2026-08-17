# Contributing Guide

RetailMind AI contribution workflow - setup, development process, coding standards, testing requirements, and PR workflow.

**Last Updated**: 2026-08-15
**Version**: 0.9.0

---

## Table of Contents

- [Getting Started](#getting-started)
- [Development Workflow](#development-workflow)
- [Coding Standards](#coding-standards)
- [Testing Requirements](#testing-requirements)
- [Pull Request Process](#pull-request-process)
- [Architecture Principles](#architecture-principles)
- [Commit Conventions](#commit-conventions)

---

## Getting Started

### Prerequisites

**Required**:
- **Python 3.12+** - Language runtime
- **uv 0.4+** - Package manager ([installation](https://docs.astral.sh/uv/getting-started/installation/))
- **Docker 24+** - Container runtime for integration tests
- **Docker Compose v2** - Multi-container orchestration
- **Git 2.30+** - Version control

**Optional**:
- **Make** - Task runner (all targets documented via `make help`)

### Initial Setup

```bash
# Clone repository
git clone https://github.com/org/retailmind-ai.git
cd retailmind-ai

# Verify Python version
python3 --version  # Should be 3.12 or higher

# Install uv (if not already installed)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install dependencies
uv sync

# Install pre-commit hooks
uv run pre-commit install

# Copy environment template
cp .env.example .env

# Start development stack
make up

# Run tests to verify setup
make test
make test-integration
```

**Expected Output** (real counts as of 2026-08-17 — re-run `make test` /
`make test-integration` yourself for the current numbers rather than
trusting this snapshot):
```
===== 1022 passed, 1 failed in 49.45s =====   (unit tests — the 1 failure is
                                                pre-existing and documented
                                                in docs/known-issues.md)
===== 339 passed in 921.53s =====             (integration tests)
```

### Repository Structure

```
retailmind-ai/
├── backend/         # FastAPI application (clean architecture)
├── data_platform/   # Ingestion pipeline + dbt models
├── ml/              # Forecasting engine (ridge regression)
├── ui/              # Streamlit console (12 workspaces)
├── infra/           # Docker, compose, nginx, monitoring
├── scripts/         # Operational scripts (backup, TLS, checks)
├── docs/            # Documentation
├── tests/           # Shared test fixtures
├── pyproject.toml   # Workspace root (tooling config)
└── Makefile         # Developer entrypoints
```

---

## Development Workflow

### Daily Development

```bash
# Start dev stack (hot reload enabled)
make up

# View logs
make logs

# Run unit tests (fast, no Docker)
make test

# Run linters
make lint  # ruff + mypy

# Stop stack
make down
```

### Working on a Feature

**1. Create feature branch**:
```bash
git checkout -b feat/add-cohort-analysis
```

**2. Make changes**:
```bash
# Edit files
vim backend/app/services/analytics/cohort.py

# Run tests continuously
make test  # Re-run after each change
```

**3. Pre-commit checks** (automatic):
```bash
# Runs on `git commit`:
# - ruff (format + lint)
# - gitleaks (secret scanning)
# - trailing whitespace, EOF fixers
# - YAML validation
# - large file check (500KB limit)
# - main branch protection
```

**4. Run integration tests**:
```bash
make test-integration
```

**5. Architecture validation**:
```bash
make lint-architecture
# Enforces: api → services → domain → infrastructure
```

**6. Commit**:
```bash
git add .
git commit -m "feat: add cohort analysis to analytics service

Implements RFM (recency, frequency, monetary) cohort segmentation
for customer intelligence workspace.

- Add CohortAnalyzer to services/analytics/cohort.py
- Add cohort metrics to metric registry
- Add /api/v1/analytics/customer/cohorts endpoint
- Add 15 unit tests, 3 integration tests

Closes #42"
```

**7. Push and create PR**:
```bash
git push origin feat/add-cohort-analysis
# Create PR on GitHub
```

### Hot Reload

**Backend** (FastAPI):
- File changes → automatic reload (~1 second)
- No need to restart containers

**UI** (Streamlit):
- File changes → automatic reload
- Browser refresh required

**Data Platform** (dbt):
- Model changes require explicit run:
```bash
cd data_platform
uv run dbt run --select model_name
```

---

## Coding Standards

### Python Style

**Enforced by Ruff** (configuration in `pyproject.toml`):

```toml
[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = [
    "E", "W", "F",  # pycodestyle + pyflakes
    "I",            # isort (import sorting)
    "B",            # bugbear (likely bugs)
    "UP",           # pyupgrade (modern syntax)
    "S",            # bandit (security)
    "N",            # naming conventions
    "SIM",          # simplify (code complexity)
    "TID",          # tidy imports (no relative parent imports)
]
```

**Run formatter**:
```bash
make lint  # Runs ruff check + ruff format + mypy
```

### Type Annotations

**Strict mypy** enforced across all modules:

```python
# Good ✅
def calculate_aov(revenue: Decimal, orders: int) -> Decimal:
    """Average order value."""
    if orders == 0:
        return Decimal(0)
    return revenue / orders

# Bad ❌ (mypy error: missing return type)
def calculate_aov(revenue: Decimal, orders: int):
    return revenue / orders
```

**Type checking**:
```bash
make lint  # Includes mypy --strict
```

**Exceptions** (scoped, not global):
- `celery.*` - No type stubs available (scoped to `app.workers`)
- `plotly.*`, `st_aggrid.*` - No type info (scoped to UI chart module)

### Naming Conventions

**Modules**: `snake_case` (e.g., `analytics_service.py`)

**Classes**: `PascalCase` (e.g., `AnalyticsService`)

**Functions/Variables**: `snake_case` (e.g., `calculate_aov`)

**Constants**: `UPPER_SNAKE_CASE` (e.g., `DEFAULT_CACHE_TTL`)

**Private**: `_leading_underscore` (e.g., `_build_query`)

**Domain models**: No abbreviations
```python
# Good ✅
class CustomerSegment(BaseModel):
    segment_id: UUID
    segment_name: str

# Bad ❌
class CustSeg(BaseModel):
    seg_id: UUID
    seg_nm: str
```

### Docstrings

**Required** for:
- Public functions
- Classes
- Service methods
- Complex logic

**Format**: Google-style docstrings

```python
def investigate_variance(
    metric: str,
    current_period: Period,
    baseline_period: Period,
) -> AnalystAnswer:
    """Investigate metric variance via dimensional sweep.

    Sweeps nine dimensions (store, product, customer, channel, day_of_week,
    payment_method, promotion, region, segment) to identify drivers of
    variance between current and baseline periods.

    Args:
        metric: Metric key from registry (e.g., "revenue", "margin")
        current_period: Period being investigated
        baseline_period: Comparison period

    Returns:
        AnalystAnswer with facts (mechanical), inferences (statistical),
        and caveats (assumptions/limitations)

    Raises:
        ValueError: If metric is unknown or periods overlap
        PermissionDenied: If principal lacks access to metric domain
    """
```

### Import Organization

**Order** (enforced by ruff/isort):
1. Standard library
2. Third-party packages
3. Local modules (app.*)

**Example**:
```python
# Standard library
import uuid
from datetime import datetime, timezone
from decimal import Decimal

# Third-party
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
import numpy as np

# Local
from app.domain.analytics import Metric, MetricRegistry
from app.services.analytics.service import AnalyticsService
from app.api.dependencies import get_principal
```

**Avoid**:
- Wildcard imports (`from module import *`)
- Relative parent imports (`from ..services import foo` - blocked by importlinter)

---

## Testing Requirements

### Test Coverage Requirements

**Minimum coverage**: 85% (measured per workspace member)

**Run coverage report**:
```bash
make test-coverage
```

**Example output**:
```
Name                                    Stmts   Miss  Cover
-----------------------------------------------------------
app/services/analytics/service.py         142      8    94%
app/services/analytics/registry.py         89      2    98%
app/services/rca/service.py               156     12    92%
-----------------------------------------------------------
TOTAL                                    8247    894    89%
```

### Unit Tests

**Location**: `backend/tests/unit/`, `data_platform/tests/unit/`, etc.

**Characteristics**:
- Fast (<1ms per test)
- No I/O (no database, no Redis, no files, no network)
- Mocked dependencies

**Example**:
```python
# tests/unit/test_analytics_service.py
import pytest
from app.services.analytics import AnalyticsService

@pytest.fixture
def analytics_service(mock_repository):
    return AnalyticsService(repository=mock_repository)

def test_query_validates_domain(analytics_service, principal):
    """Service rejects unknown domains."""
    with pytest.raises(ValueError, match="Unknown domain"):
        await analytics_service.query(
            principal,
            domain_key="invalid_domain",
            metrics=["revenue"]
        )
```

**Run unit tests**:
```bash
make test
# Or specific module:
uv run pytest tests/unit/test_analytics_service.py -v
```

### Integration Tests

**Location**: `backend/tests/integration/`, `data_platform/tests/integration/`

**Characteristics**:
- Slow (~2-5 seconds per test)
- Real services (PostgreSQL, Redis, DuckDB via testcontainers)
- End-to-end workflows

**Example**:
```python
# tests/integration/test_api_endpoints.py
@pytest.mark.asyncio
async def test_analytics_summary_endpoint(client, auth_token):
    """End-to-end analytics query."""
    response = await client.post(
        "/api/v1/analytics/revenue/summary",
        headers={"Authorization": f"Bearer {auth_token}"},
        json={
            "metrics": ["net_revenue", "aov"],
            "start_date": "2026-08-01",
            "end_date": "2026-08-15"
        }
    )

    assert response.status_code == 200
    data = response.json()
    assert data["domain"] == "revenue"
    assert len(data["rows"]) == 1
```

**Run integration tests**:
```bash
make test-integration
# Or specific test:
uv run pytest tests/integration/test_api_endpoints.py::test_analytics_summary_endpoint -v
```

### Test Markers

**Available markers** (defined in `pyproject.toml`):

```python
@pytest.mark.integration  # Requires Docker services
@pytest.mark.scenario     # Full-pipeline, slow (merge-to-main only)
```

**Run by marker**:
```bash
# Integration tests only
uv run pytest -m integration

# Skip slow tests
uv run pytest -m "not scenario"
```

### Test Naming

**Pattern**: `test_<subject>_<scenario>`

```python
# Good ✅
def test_query_validates_domain()
def test_query_enforces_permissions()
def test_query_returns_rows_with_correct_schema()

# Bad ❌
def test_1()
def test_query()
def test_it_works()
```

### Fixtures

**Shared fixtures**: `tests/conftest.py` (workspace root)

**Common fixtures**:
```python
@pytest.fixture
async def db_session():
    """PostgreSQL test database session."""

@pytest.fixture
def mock_llm_gateway():
    """Mock LLM gateway (no external API calls)."""

@pytest.fixture
def auth_token(test_user):
    """JWT token for API tests."""

@pytest.fixture
def principal(test_user):
    """Authenticated principal with default permissions."""
```

---

## Pull Request Process

### Before Submitting PR

**Checklist**:
- [ ] All tests pass (`make test`, `make test-integration`)
- [ ] Linters pass (`make lint`, `make lint-architecture`)
- [ ] Coverage maintained (≥85%)
- [ ] Documentation updated (if public API changed)
- [ ] CHANGELOG.md updated (if user-facing)
- [ ] No secrets committed (gitleaks pre-commit hook blocks)
- [ ] Commit messages follow conventional commits

**Run full validation**:
```bash
make lint
make test
make test-integration
make lint-architecture
```

### PR Template

**Title**: Use conventional commit format
```
feat: add cohort analysis to customer intelligence
fix: correct MASE calculation in forecast backtest
docs: update deployment guide with TLS instructions
```

**Description**:
```markdown
## Summary
Implements RFM cohort analysis for customer segmentation.

## Changes
- Add `CohortAnalyzer` to `app/services/analytics/cohort.py`
- Add cohort metrics to metric registry (recency, frequency, monetary)
- Add `/api/v1/analytics/customer/cohorts` endpoint
- Add 15 unit tests, 3 integration tests

## Testing
- [x] Unit tests pass (18 new tests)
- [x] Integration tests pass (3 new tests)
- [x] Manual testing in Customer Intelligence workspace

## Documentation
- [x] Added docstrings to new methods
- [x] Updated `docs/analytics.md` with cohort analysis section

Closes #42
```

### PR Review Checklist

**Reviewers check**:
- [ ] Code follows architecture layers (api → services → domain)
- [ ] No business logic in API layer (controllers are thin)
- [ ] Domain models are immutable (no setters)
- [ ] Services have single responsibility
- [ ] Tests cover edge cases (not just happy path)
- [ ] Error messages are actionable
- [ ] No hardcoded credentials
- [ ] No commented-out code
- [ ] No overly complex functions (cyclomatic complexity <10)

### Merge Requirements

**All of**:
- ✅ All CI checks pass
- ✅ At least 1 approval from core contributor
- ✅ No unresolved comments
- ✅ Branch up-to-date with main
- ✅ Pre-commit hooks pass

**CI checks** (`.github/workflows/ci.yml`):
```yaml
- Lint (ruff + mypy)
- Unit tests (~1,020 tests)
- Integration tests (339 tests)
- Architecture validation (import-linter)
```

### Merge Strategy

**Squash and merge** (default):
- Keeps main branch linear
- Single commit per PR
- Commit message becomes PR title + description

**Example**:
```
feat: add cohort analysis to customer intelligence (#42)

Implements RFM cohort analysis for customer segmentation.

- Add CohortAnalyzer to services/analytics/cohort.py
- Add cohort metrics to metric registry
- Add /api/v1/analytics/customer/cohorts endpoint
- Add 15 unit tests, 3 integration tests
```

---

## Architecture Principles

### Clean Architecture Layers

**Enforced by import-linter** (fails CI if violated):

```
api → services → domain → infrastructure
```

**Rules**:
1. **API layer** (`app/api`) - HTTP routes, request/response schemas
   - Depends on: services, domain
   - No business logic (thin controllers)

2. **Service layer** (`app/services`) - Business workflows
   - Depends on: domain, infrastructure (via ports)
   - Orchestrates domain logic and I/O

3. **Domain layer** (`app/domain`) - Business rules, entities
   - Depends on: nothing
   - Pure Python, no framework dependencies

4. **Infrastructure layer** (`app/infrastructure`) - External I/O
   - Implements: domain ports (repositories, gateways)
   - Database, cache, LLM, external APIs

**Verify**:
```bash
make lint-architecture
# Output: ✅ All contracts passed
```

### Design Patterns

**Dependency Injection**:
```python
# Good ✅ - Dependencies injected
class AnalyticsService:
    def __init__(self, repository: AnalyticsRepository):
        self._repository = repository

# Bad ❌ - Direct instantiation
class AnalyticsService:
    def __init__(self):
        self._repository = PostgresAnalyticsRepository()
```

**Port-Adapter** (for external I/O):
```python
# Domain port (abstract)
class LlmGateway(Protocol):
    async def complete(self, request: LlmRequest) -> LlmResponse: ...

# Infrastructure adapter (concrete)
class AnthropicProvider(LlmGateway):
    async def complete(self, request: LlmRequest) -> LlmResponse:
        # Call Anthropic API
        ...
```

**Value Objects** (immutable):
```python
@dataclass(frozen=True)
class Period:
    start_date: date
    end_date: date

    def __post_init__(self):
        if self.end_date < self.start_date:
            raise ValueError("end_date must be >= start_date")
```

### Code Organization

**File structure** (example: analytics service):
```
app/services/analytics/
├── __init__.py
├── service.py        # AnalyticsService (orchestration)
├── registry.py       # MetricRegistry (domain logic)
├── query_builder.py  # SQL generation
└── cache.py          # Caching logic
```

**Keep modules focused**:
- Single responsibility
- <300 lines per file (guideline, not hard rule)
- Clear separation of concerns

---

## Commit Conventions

### Conventional Commits

**Format**: `<type>(<scope>): <description>`

**Types**:
- `feat:` - New feature
- `fix:` - Bug fix
- `docs:` - Documentation only
- `test:` - Test additions/changes
- `refactor:` - Code restructuring (no behavior change)
- `chore:` - Maintenance (dependencies, tooling)
- `ci:` - CI/CD changes
- `build:` - Build system changes

**Examples**:
```bash
feat(analytics): add cohort analysis to customer intelligence
fix(rca): correct MASE calculation in forecast backtest
docs(deployment): update TLS configuration instructions
test(analytics): add edge case tests for zero-revenue scenarios
refactor(api): extract validation logic to shared module
chore(deps): update pydantic to 2.9.0
ci(github): add dependency review action
build(docker): optimize backend image layers
```

**Scope** (optional but recommended):
- `analytics`, `rca`, `forecast`, `recommendations`, `auth`, `api`, `etl`, `dbt`, `ui`

### Breaking Changes

**Format**:
```
feat(api)!: change analytics endpoint response schema

BREAKING CHANGE: /api/v1/analytics/{domain}/summary now returns
paginated response with `items` and `total` fields instead of
direct array.

Migration: Update clients to access data via `response.items`
instead of `response` directly.
```

**Trigger**: Major version bump (v0.9.0 → v1.0.0)

### Commit Message Body

**Include** (when relevant):
- Why the change was needed
- What alternatives were considered
- Links to related issues/PRs
- Migration instructions (for breaking changes)

**Example**:
```
feat: add Redis-backed rate limiting

Implements per-IP and per-user rate limiting using Redis sliding
window algorithm to prevent API abuse and ensure fair usage.

Alternatives considered:
- In-memory rate limiting: doesn't scale across workers
- Fixed window: allows burst at window boundary

Configuration:
- RM_RATE_LIMIT_PER_IP=100/minute (default)
- RM_RATE_LIMIT_PER_USER=200/minute (default)

Closes #38
```

### Pre-commit Hooks

**Installed via**:
```bash
uv run pre-commit install
```

**Runs on every commit**:
1. **ruff** - Format + lint (auto-fixes)
2. **gitleaks** - Secret scanning (blocks commit if secrets found)
3. **trailing-whitespace** - Remove trailing whitespace
4. **end-of-file-fixer** - Ensure files end with newline
5. **check-yaml** - Validate YAML syntax
6. **check-added-large-files** - Block files >500KB
7. **no-commit-to-branch** - Prevent direct commits to main

**Skip hooks** (emergencies only):
```bash
git commit --no-verify -m "emergency fix"
```

---

## Development Tips

### Debugging

**Backend** (FastAPI):
```python
# Add breakpoint
import pdb; pdb.set_trace()

# Or use debugpy (VS Code)
# Launch config in .vscode/launch.json
```

**UI** (Streamlit):
```python
# Use st.write for debugging
import streamlit as st
st.write(f"Debug: {variable}")
```

**Database**:
```bash
# Connect to PostgreSQL
docker compose exec postgres psql -U retailmind -d retailmind

# View tables
\dt

# Query
SELECT * FROM fct_sales LIMIT 10;
```

### Performance Profiling

**Backend**:
```python
# Add to endpoint
import time
start = time.perf_counter()
result = heavy_operation()
duration = time.perf_counter() - start
logger.info("operation_complete", duration_ms=duration * 1000)
```

**Query profiling**:
```sql
EXPLAIN ANALYZE
SELECT ...
```

### Useful Commands

```bash
# Find TODOs
rg "TODO|FIXME" --type py

# Count lines of code
find . -name "*.py" -not -path "*/tests/*" -not -path "*/.venv/*" | xargs wc -l

# Generate dependency graph
uv pip tree

# Check for security vulnerabilities
uv pip list --format json | uv run safety check --stdin
```

---

## Getting Help

**Questions**:
- GitHub Discussions: General questions, architecture discussions
- GitHub Issues: Bug reports, feature requests

**Response Time**:
- Core hours (9 AM - 5 PM PT): <4 hours
- Off-hours: <24 hours

**Before Asking**:
1. Search existing issues/discussions
2. Check documentation (docs/)
3. Read error messages carefully
4. Provide minimal reproduction

---

**Maintained by**: RetailMind AI Contributors
**License**: MIT
**Last Reviewed**: 2026-08-15
