"""Integration tests for calibration API endpoints.

Tests the full stack: API → Service → Repository → Database.

``auth_headers`` is a plain async helper in conftest.py, not a pytest
fixture — every other integration test file calls it explicitly
(``await auth_headers(client, role)``) rather than declaring it as a test
parameter, and this file now follows the same convention. ``session`` and
``tenant_id`` aren't conftest fixtures anywhere in this suite either; built
locally here the same way ``test_notifications_repository.py``'s
``repository`` fixture builds its own engine/session from ``migrated_db``.
"""

from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.infrastructure.db.models.enums import OutcomeStatus
from app.infrastructure.db.models.recommendations import (
    Recommendation,
    RecommendationOutcome,
)
from tests.integration.conftest import auth_headers


@pytest.fixture
async def session(migrated_db: dict[str, str]):
    """An AsyncSession bound to the migrated, seeded test database."""
    url = (
        f"postgresql+asyncpg://{migrated_db['RM_DB_USER']}:{migrated_db['RM_DB_PASSWORD']}"
        f"@{migrated_db['RM_DB_HOST']}:{migrated_db['RM_DB_PORT']}/{migrated_db['RM_DB_NAME']}"
    )
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as db_session:
        yield db_session

    await engine.dispose()


@pytest.fixture
async def tenant_id(session: AsyncSession) -> str:
    """The seeded demo tenant's id."""
    result = await session.execute(sa.text("SELECT id FROM tenant ORDER BY created_at LIMIT 1"))
    return str(result.scalar_one())


_TEST_DEDUP_KEYS = ("test-key", "test-key-2", "test-key-3", "test-key-pricing")


@pytest.fixture(autouse=True)
async def _clean_recommendations_after_each_test(session: AsyncSession):
    """`migrated_db` is session-scoped — one Postgres container for the whole
    *suite* (shared with every other integration test file, not just this
    one) — and the API reads through its own, separate connection, so test
    data has to be genuinely committed (not just rolled back) to be visible
    to it. That means it also persists for every test after the one that
    committed it: `test_generator_calibration_success` was seeing 50
    "inventory" outcomes (25 of its own plus 25 left over from
    `test_calibration_summary_with_measured_outcomes`, which runs first)
    instead of the 25 it created, until this fixture started clearing rows
    after each test instead of leaving them for the next one to inherit.

    A blanket `DELETE FROM recommendation` (the first version of this fix)
    over-corrected: it deleted every row in the table regardless of who
    created it, including the seed script's demo recommendation that
    `test_dashboard_api.py` depends on — since that file shares this same
    session-scoped database and runs alphabetically after this one, its
    tests started failing with empty recommendation lists (Prompt 11.5
    regression). Scoping the delete to this file's own `dedup_key` values
    fixes the original over-accumulation problem without touching rows this
    file didn't create."""
    yield
    await session.execute(
        sa.text("DELETE FROM recommendation WHERE dedup_key = ANY(:keys)"),
        {"keys": list(_TEST_DEDUP_KEYS)},
    )
    await session.commit()


@pytest.fixture
async def data_snapshot_id(session: AsyncSession, tenant_id: str) -> str:
    """A real `data_snapshot` row — `recommendation.data_snapshot_id` is a
    real foreign key, not a free-text field; a literal placeholder string
    fails with a ForeignKeyViolationError before the row is ever written."""
    snapshot_id = "snap-calibration-test-1"
    await session.execute(
        sa.text(
            "INSERT INTO data_snapshot "
            "(id, tenant_id, dag_run_id, manifest_digest, mart_row_counts, published_at) "
            "VALUES (:id, :tenant_id, 'test-run', 'test-digest', '{}', now()) "
            "ON CONFLICT (id) DO NOTHING"
        ),
        {"id": snapshot_id, "tenant_id": tenant_id},
    )
    await session.commit()
    return snapshot_id


@pytest.mark.asyncio
async def test_calibration_summary_with_no_outcomes(client: AsyncClient):
    """Returns empty summary when no measured outcomes exist."""
    response = await client.get(
        "/api/v1/recommendations/calibration",
        headers=await auth_headers(client),
    )

    assert response.status_code == 200
    data = response.json()

    assert data["total_measured_outcomes"] == 0
    assert data["overall_metrics"]["sample_size"] == 0
    assert data["overall_metrics"]["is_statistically_significant"] is False
    assert "No measured outcomes available yet." in data["limitations"]
    assert data["generator_performance"] == []
    assert data["best_performing_generators"] == []


@pytest.mark.asyncio
async def test_calibration_summary_with_measured_outcomes(
    client: AsyncClient,
    session: AsyncSession,
    tenant_id: str,
    data_snapshot_id: str,
):
    """Returns calibration summary when measured outcomes exist."""
    # Create a recommendation with measured outcomes
    rec = Recommendation(
        tenant_id=tenant_id,
        type="reorder",
        category="inventory",
        subject={"sku": "TEST001"},
        dedup_key="test-key",
        expected_impact={"metric": "revenue", "value_usd": 1000, "method": "measured"},
        rule_id="test-rule",
        rule_version="1.0",
        score=100.0,
        confidence="high",
        evidence={},
        data_snapshot_id=data_snapshot_id,
        expires_at=datetime(2026, 12, 31, tzinfo=UTC),
    )
    session.add(rec)
    await session.flush()

    # Add 25 measured outcomes (enough for statistical significance)
    for _i in range(25):
        outcome = RecommendationOutcome(
            recommendation_id=rec.id,
            status=OutcomeStatus.MEASURED,
            window_days=7,
            baseline_method="comparable_period",
            baseline_value=900.0,
            observed_value=950.0,
            realized_impact=50.0,
            expected_impact=100.0,
            absolute_error=50.0,
            realization_ratio=0.5,
            direction_correct=True,
            measurement_confidence="high",
        )
        session.add(outcome)

    await session.commit()

    # Query calibration
    response = await client.get(
        "/api/v1/recommendations/calibration",
        headers=await auth_headers(client),
    )

    assert response.status_code == 200
    data = response.json()

    assert data["total_measured_outcomes"] == 25
    assert data["overall_metrics"]["sample_size"] == 25
    assert data["overall_metrics"]["is_statistically_significant"] is True
    assert data["overall_metrics"]["mean_realization_ratio"] is not None
    assert data["overall_metrics"]["direction_accuracy"] == 1.0
    assert len(data["generator_performance"]) > 0


@pytest.mark.asyncio
async def test_generator_calibration_not_found(client: AsyncClient):
    """Returns 404 when no outcomes exist for generator."""
    response = await client.get(
        "/api/v1/recommendations/calibration/generators/nonexistent",
        headers=await auth_headers(client),
    )

    assert response.status_code == 404
    assert "No measured outcomes" in response.json()["detail"]


@pytest.mark.asyncio
async def test_generator_calibration_success(
    client: AsyncClient,
    session: AsyncSession,
    tenant_id: str,
    data_snapshot_id: str,
):
    """Returns generator-specific calibration."""
    # Create inventory recommendation with outcomes
    rec = Recommendation(
        tenant_id=tenant_id,
        type="reorder",
        category="inventory",
        subject={"sku": "TEST001"},
        dedup_key="test-key-2",
        expected_impact={"metric": "revenue", "value_usd": 1000, "method": "measured"},
        rule_id="test-rule",
        rule_version="1.0",
        score=100.0,
        confidence="high",
        evidence={},
        data_snapshot_id=data_snapshot_id,
        expires_at=datetime(2026, 12, 31, tzinfo=UTC),
    )
    session.add(rec)
    await session.flush()

    for _i in range(25):
        outcome = RecommendationOutcome(
            recommendation_id=rec.id,
            status=OutcomeStatus.MEASURED,
            window_days=7,
            baseline_value=900.0,
            observed_value=950.0,
            realized_impact=50.0,
            expected_impact=100.0,
            absolute_error=50.0,
            realization_ratio=0.5,
            direction_correct=True,
        )
        session.add(outcome)

    await session.commit()

    response = await client.get(
        "/api/v1/recommendations/calibration/generators/inventory",
        headers=await auth_headers(client),
    )

    assert response.status_code == 200
    data = response.json()

    assert data["generator_name"] == "inventory"
    assert data["metrics"]["sample_size"] == 25
    assert data["metrics"]["is_statistically_significant"] is True
    assert "estimate_basis_breakdown" in data
    assert "confidence_bands" in data


@pytest.mark.asyncio
async def test_generator_calibration_for_a_second_distinct_generator(
    client: AsyncClient,
    session: AsyncSession,
    tenant_id: str,
    data_snapshot_id: str,
):
    """A different generator (pricing, not inventory) filters correctly too
    — proves the filter dispatches on the real `category` value rather than
    happening to work for one hardcoded case."""
    rec = Recommendation(
        tenant_id=tenant_id,
        type="markdown",
        category="pricing",
        subject={"sku": "TEST003"},
        dedup_key="test-key-pricing",
        expected_impact={"metric": "profit", "value_usd": 500, "method": "modelled"},
        rule_id="test-rule",
        rule_version="1.0",
        score=80.0,
        confidence="medium",
        evidence={},
        data_snapshot_id=data_snapshot_id,
        expires_at=datetime(2026, 12, 31, tzinfo=UTC),
    )
    session.add(rec)
    await session.flush()

    for _i in range(25):
        session.add(
            RecommendationOutcome(
                recommendation_id=rec.id,
                status=OutcomeStatus.MEASURED,
                window_days=7,
                baseline_value=450.0,
                observed_value=480.0,
                realized_impact=30.0,
                expected_impact=50.0,
                absolute_error=20.0,
                realization_ratio=0.6,
                direction_correct=True,
            )
        )
    await session.commit()

    response = await client.get(
        "/api/v1/recommendations/calibration/generators/pricing",
        headers=await auth_headers(client),
    )

    assert response.status_code == 200
    data = response.json()
    assert data["generator_name"] == "pricing"
    assert data["metrics"]["sample_size"] == 25

    # And a different generator's endpoint sees none of this pricing data —
    # the filter genuinely isolates by category, not just returning "some
    # measured outcomes exist somewhere for this tenant".
    other = await client.get(
        "/api/v1/recommendations/calibration/generators/inventory",
        headers=await auth_headers(client),
    )
    assert other.status_code == 404


@pytest.mark.asyncio
async def test_confidence_calibration(
    client: AsyncClient,
    session: AsyncSession,
    tenant_id: str,
    data_snapshot_id: str,
):
    """Returns confidence band calibration analysis."""
    # Create outcomes across different confidence levels
    rec = Recommendation(
        tenant_id=tenant_id,
        type="markdown",
        category="pricing",
        subject={"sku": "TEST002"},
        dedup_key="test-key-3",
        expected_impact={"metric": "profit", "value_usd": 500, "method": "modelled"},
        rule_id="test-rule",
        rule_version="1.0",
        score=80.0,
        confidence="medium",
        evidence={},
        data_snapshot_id=data_snapshot_id,
        expires_at=datetime(2026, 12, 31, tzinfo=UTC),
    )
    session.add(rec)
    await session.flush()

    # Add outcomes
    for _i in range(30):
        outcome = RecommendationOutcome(
            recommendation_id=rec.id,
            status=OutcomeStatus.MEASURED,
            window_days=7,
            baseline_value=450.0,
            observed_value=480.0,
            realized_impact=30.0,
            expected_impact=50.0,
            absolute_error=20.0,
            realization_ratio=0.6,
            direction_correct=True,
        )
        session.add(outcome)

    await session.commit()

    response = await client.get(
        "/api/v1/recommendations/calibration/confidence",
        headers=await auth_headers(client),
    )

    assert response.status_code == 200
    data = response.json()

    assert "confidence_bands" in data
    assert "interpretation" in data
    assert isinstance(data["confidence_bands"], list)


@pytest.mark.asyncio
async def test_calibration_requires_authentication(client: AsyncClient):
    """Calibration endpoints require authentication."""
    response = await client.get("/api/v1/recommendations/calibration")
    assert response.status_code == 401
