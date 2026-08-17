# Testing Guide

RetailMind AI testing strategy - 1,099 tests (794 unit + 305 integration), architecture enforcement, and CI/CD.

**Last Updated**: 2026-08-15
**Version**: 0.9.0

---

## Test Suite Overview

### Test Counts

```bash
$ make test
===== 794 passed in 58.32s =====

$ make test-integration
===== 305 passed in 847.19s =====

Total: 1,099 tests
```

### Coverage

- **Unit tests**: 87% line coverage
- **Integration tests**: 73% line coverage

---

## Running Tests

### Unit Tests

```bash
# All unit tests
make test

# Specific module
uv run pytest tests/unit/test_analytics_service.py

# With coverage
make test-coverage
```

**Speed**: ~58 seconds (no Docker required)

### Integration Tests

```bash
# All integration tests (requires Docker)
make test-integration

# Specific test
uv run pytest tests/integration/test_analyst_llm_narration.py

# Skip slow tests
uv run pytest -m "not slow"
```

**Speed**: ~15 minutes (Docker services required)

### Architecture Tests

```bash
# Enforce clean architecture layers
make lint-architecture

# Uses import-linter to verify:
# - api only imports from services
# - services only import from domain
# - domain has no dependencies
```

---

## Test Organization

```
tests/
├── unit/               # 794 tests (fast, no I/O)
│   ├── test_analytics_service.py
│   ├── test_rca_service.py
│   ├── test_forecasting.py
│   ├── test_narrator.py
│   └── ...
│
├── integration/        # 305 tests (slow, DB/Redis/DuckDB)
│   ├── test_api_endpoints.py
│   ├── test_warehouse_queries.py
│   ├── test_ingestion_pipeline.py
│   └── ...
│
└── fixtures/           # Test data
    ├── sample_sales.csv
    ├── sample_forecast.json
    └── ...
```

---

## Unit Testing

### Example: Analytics Service

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

def test_query_enforces_permissions(analytics_service, principal):
    """Service checks RBAC permissions."""
    # Principal without ANALYTICS_REVENUE_READ
    with pytest.raises(PermissionDenied):
        await analytics_service.query(
            principal,
            domain_key="revenue",
            metrics=["net_revenue"]
        )
```

### Example: RCA Service

```python
# tests/unit/test_rca_service.py
def test_investigate_builds_evidence(rca_service):
    """RCA assigns evidence tiers correctly."""
    answer = await rca_service.investigate(
        metric="revenue",
        current_period=Period("2026-08-01", "2026-08-15"),
        baseline_period=Period("2026-07-01", "2026-07-15")
    )

    # Verify evidence tiers
    assert all(f.tier in ["MECHANICAL", "STATISTICAL"] for f in answer.facts)
    assert answer.confidence >= 0.0 and answer.confidence <= 1.0
```

---

## Integration Testing

### Example: API Endpoint

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
    assert "net_revenue" in data["rows"][0]
    assert "aov" in data["rows"][0]
```

### Example: LLM Integration

```python
# tests/integration/test_analyst_llm_narration.py
@pytest.mark.skipif(
    os.getenv("RM_LLM_PROVIDER") != "anthropic",
    reason="Requires Anthropic API key"
)
@pytest.mark.asyncio
async def test_narrator_with_real_llm(db_session):
    """Test actual Claude API integration."""
    narrator = AnalystNarrator(gateway=real_llm_gateway)

    answer = AnalystAnswer(
        facts=["Revenue decreased 15% from $1.25M to $1.06M"],
        inferences=[],
        caveats=[],
        headline="Revenue decreased 15%."
    )

    enhanced = await narrator.narrate_investigation(answer)

    # Verify LLM generated fluent narration
    assert len(enhanced) > len(answer.headline)
    assert "$1.25M" in enhanced  # Must cite evidence
```

---

## Test Fixtures

### Pytest Fixtures

```python
# tests/conftest.py
@pytest.fixture
async def db_session():
    """PostgreSQL test database session."""
    async with async_session_factory() as session:
        yield session
        await session.rollback()

@pytest.fixture
def mock_llm_gateway():
    """Mock LLM gateway for unit tests."""
    return LlmGateway(provider=MockProvider())

@pytest.fixture
def auth_token(test_user):
    """JWT token for API tests."""
    return create_access_token(user_id=test_user.id)
```

---

## CI/CD

### GitHub Actions

**File**: `.github/workflows/ci.yml`

```yaml
name: CI

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3

      - name: Install uv
        run: curl -LsSf https://astral.sh/uv/install.sh | sh

      - name: Lint
        run: make lint

      - name: Unit Tests
        run: make test

      - name: Integration Tests
        run: make test-integration
```

**Status**: All checks must pass before merge

---

## Quality Gates

### Pre-Commit Checks

```bash
# Linting
make lint       # ruff + mypy

# Unit tests
make test       # Must pass (no xfail)

# Architecture
make lint-architecture  # import-linter
```

### PR Requirements

- ✅ All tests passing
- ✅ No linting errors
- ✅ Architecture layers respected
- ✅ Coverage maintained (>85%)

---

**Maintained by**: RetailMind AI Contributors
**License**: MIT
**Last Reviewed**: 2026-08-15
