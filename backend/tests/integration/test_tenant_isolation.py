"""Cross-tenant isolation — OLTP/API layer (Prompt 12, Phase 10/18).

The platform scopes every tenant-owned table by ``tenant_id`` at the model
level (``TenantScopedMixin``) and every repository is constructed with
``principal.tenant_id`` rather than accepting it per call — this is a real,
structural guarantee, not incidental. These tests prove it end-to-end
through the real HTTP API against a second, freshly created tenant, rather
than trusting the architecture description.

Warehouse/analytics isolation — the gap Prompt 12 found and Prompt 12.5
closed — is covered separately in ``test_tenant_warehouse_isolation.py``,
which provisions two tenants each with their own real, distinct DuckDB
warehouse. This file's one warehouse-adjacent test
(``test_an_unprovisioned_tenant_never_reads_the_demo_warehouse``) only
proves the *fail-closed* half: a tenant with no warehouse of its own gets
a clean 503, never someone else's data.

``session``/``tenant_id`` aren't conftest fixtures in this suite; built
locally the same way ``test_calibration_api.py`` builds its own
engine/session from ``migrated_db``.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.security import hash_password
from app.infrastructure.db.models.auth import AppUser, Role, Tenant, UserRole
from app.infrastructure.db.models.enums import OutcomeStatus
from app.infrastructure.db.models.recommendations import Recommendation, RecommendationOutcome
from tests.integration.conftest import auth_headers

SECOND_TENANT_PASSWORD = "Isolation-Test-1!"  # noqa: S105 — test fixture, not a real credential


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
async def second_tenant(session: AsyncSession) -> dict[str, str]:
    """A real, independently created company — not the seeded demo tenant.

    Proves tenant creation itself is trivial (Prompt 12 gate question 1):
    this is exactly the ``TenantRepository``/model shape the onboarding
    flow uses, just constructed directly here rather than through the API.
    """
    slug = f"isolation-test-{uuid.uuid4().hex[:8]}"
    tenant = Tenant(
        slug=slug,
        name="Second Company Inc.",
        base_currency="EUR",
        industry="Grocery",
        country_code="DE",
    )
    session.add(tenant)
    await session.flush()

    # A brand-new company's first user plausibly wears both hats — this
    # test exercises both `recommendations.read` (ceo) and `data.manage`
    # (admin) against the SAME new tenant, rather than needing two users.
    roles = {
        r.key: r
        for r in (
            await session.scalars(sa.select(Role).where(Role.key.in_(["ceo", "admin"])))
        ).all()
    }
    assert {"ceo", "admin"} <= roles.keys(), "reference data (roles) must already be seeded"

    email = f"owner-{slug}@secondco.example"
    user = AppUser(
        tenant_id=tenant.id,
        email=email,
        display_name="Second Company Owner",
        password_hash=hash_password(SECOND_TENANT_PASSWORD),
    )
    session.add(user)
    await session.flush()
    for role in roles.values():
        session.add(UserRole(user_id=user.id, role_id=role.id))

    # `recommendation.data_snapshot_id` is a real foreign key — a literal
    # placeholder fails with a ForeignKeyViolationError before the row is
    # ever written (same fixture shape as test_calibration_api.py).
    snapshot_id = f"snap-isolation-{slug}"
    await session.execute(
        sa.text(
            "INSERT INTO data_snapshot "
            "(id, tenant_id, dag_run_id, manifest_digest, mart_row_counts, published_at) "
            "VALUES (:id, :tenant_id, 'test-run', 'test-digest', '{}', now())"
        ),
        {"id": snapshot_id, "tenant_id": tenant.id},
    )

    # One real recommendation with one measured outcome, scoped to this
    # tenant only — the thing tenant A must never be able to see. Uses the
    # same category-filtered, tenant-scoped path Prompt 11.5 fixed and
    # proved (`OutcomeRepository.find_measured`), not the live analytical
    # `/recommendations` endpoint — see the module docstring below for why.
    rec = Recommendation(
        tenant_id=tenant.id,
        type="reorder",
        category="inventory",
        subject={"sku": "ISO-TEST-SKU"},
        dedup_key=f"isolation-test-{slug}",
        expected_impact={"metric": "revenue", "value_usd": 1000, "method": "measured"},
        rule_id="test-rule",
        rule_version="1.0",
        score=42.0,
        confidence="high",
        evidence={},
        data_snapshot_id=snapshot_id,
        expires_at=datetime.now(tz=UTC) + timedelta(days=5),
    )
    session.add(rec)
    await session.flush()
    session.add(
        RecommendationOutcome(
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
    )
    await session.commit()

    return {"email": email, "password": SECOND_TENANT_PASSWORD, "tenant_id": str(tenant.id)}


@pytest.mark.asyncio
async def test_a_new_company_can_be_created_and_signed_into_independently(
    client: AsyncClient, second_tenant: dict[str, str]
) -> None:
    """Gate Q1/Q7: a second company exists with its own user and can log in —
    without touching the demo tenant's rows or requiring a code change."""
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": second_tenant["email"], "password": second_tenant["password"]},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["access_token"]


@pytest.mark.asyncio
async def test_the_second_tenants_measured_outcome_is_isolated_by_calibration(
    client: AsyncClient, second_tenant: dict[str, str]
) -> None:
    """The batch recommendation/outcome tables — what Prompt 11.5's
    calibration fix reads — are genuinely tenant-scoped: `OutcomeRepository
    .find_measured()` filters on `RecommendationOutcome.recommendation.has(
    tenant_id=self._tenant_id)`. Prove it end-to-end: tenant B's own
    measured outcome is reachable through its own token."""
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": second_tenant["email"], "password": second_tenant["password"]},
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    response = await client.get(
        "/api/v1/recommendations/calibration/generators/inventory", headers=headers
    )
    assert response.status_code == 200, response.text
    assert response.json()["metrics"]["sample_size"] == 1


@pytest.mark.asyncio
async def test_the_demo_tenant_cannot_see_the_second_tenants_outcome(
    client: AsyncClient, second_tenant: dict[str, str]
) -> None:
    """The inverse direction — the demo CEO's own calibration query must
    not be inflated by tenant B's row. The demo tenant has no measured
    "inventory" outcomes of its own at this point in the suite (Prompt
    11.5's per-file cleanup fixture keeps other test files' rows from
    leaking in), so a clean 404 — not a 200 including tenant B's 1 row —
    is the correct, isolated result."""
    headers = await auth_headers(client, "ceo")
    response = await client.get(
        "/api/v1/recommendations/calibration/generators/inventory", headers=headers
    )
    assert response.status_code == 404, response.text


@pytest.mark.asyncio
async def test_an_unprovisioned_tenant_never_reads_the_demo_warehouse(
    api: AsyncClient, second_tenant: dict[str, str]
) -> None:
    """Prompt 12.5 closed the gap this test used to document as open.

    Before: `/recommendations` (the live analytical engine) read one
    process-wide `SemanticLayerClient` regardless of tenant, so tenant B —
    zero transactions of its own — silently received the demo tenant's
    live-computed recommendations. `SemanticLayerClient` is now resolved
    per-request from `principal.tenant_id`
    (`app.infrastructure.semantic.tenancy`); tenant B has no
    `warehouse_path` of its own and no file at the per-slug default
    location, so it correctly gets the platform's existing, honest
    "warehouse unavailable" response — never the demo tenant's data. That
    is the isolation proof this test now pins: an absent warehouse fails
    closed, it does not fall through to someone else's.

    `test_tenant_warehouse_isolation.py` proves the positive case — two
    tenants each WITH their own provisioned warehouse seeing only their
    own, real, distinct figures.
    """
    login = await api.post(
        "/api/v1/auth/login",
        json={"email": second_tenant["email"], "password": second_tenant["password"]},
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    response = await api.get("/api/v1/recommendations", headers=headers)
    assert response.status_code == 503, response.text
    assert "warehouse" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_a_second_tenants_token_cannot_read_the_demo_tenants_company_profile(
    client: AsyncClient, second_tenant: dict[str, str]
) -> None:
    """Company profile is tenant-scoped by the authenticated principal, not
    by a client-suppliable id — there is no tenant parameter to tamper with,
    which is itself the isolation guarantee (Gate Q7)."""
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": second_tenant["email"], "password": second_tenant["password"]},
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    response = await client.get("/api/v1/company/profile", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["name"] == "Second Company Inc."
    assert response.json()["name"] != "Northwind Threads"


@pytest.mark.asyncio
async def test_an_unauthenticated_caller_gets_no_tenant_at_all(client: AsyncClient) -> None:
    """No token, no tenant — the floor beneath tenant isolation."""
    response = await client.get("/api/v1/recommendations")
    assert response.status_code == 401
