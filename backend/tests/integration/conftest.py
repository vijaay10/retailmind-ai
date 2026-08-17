"""Integration fixtures: a migrated Postgres, a live app, and seeded users.

Container and migrations are module-scoped (expensive, immutable); the app and
its HTTP client are function-scoped so no test can leak state into another.
"""

import os
import subprocess
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest

pytest.importorskip("testcontainers", reason="integration extra not installed")
from httpx import ASGITransport, AsyncClient  # noqa: E402
from testcontainers.postgres import PostgresContainer  # noqa: E402

from tests.integration import warehouse  # noqa: E402

BACKEND_DIR = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session")
def pg_container() -> Iterator[PostgresContainer]:
    with PostgresContainer("postgres:16.4", driver=None) as container:
        yield container


@pytest.fixture(scope="session")
def db_env(pg_container: PostgresContainer) -> dict[str, str]:
    """RM_DB_* pointing at the container, exported into this process.

    The app reads settings from the environment, so setting them here is what
    makes ``create_app()`` connect to the throwaway database.
    """
    env = {
        "RM_DB_HOST": pg_container.get_container_host_ip(),
        "RM_DB_PORT": str(pg_container.get_exposed_port(5432)),
        "RM_DB_NAME": pg_container.dbname,
        "RM_DB_USER": pg_container.username,
        "RM_DB_PASSWORD": pg_container.password,
    }
    os.environ.update(env)
    return env


@pytest.fixture(scope="session")
def migrated_db(db_env: dict[str, str]) -> dict[str, str]:
    """Migrate to head and seed roles + the demo tenant."""
    run_env = {**os.environ, **db_env}
    subprocess.run(  # noqa: S603
        ["uv", "run", "alembic", "upgrade", "head"],  # noqa: S607
        cwd=BACKEND_DIR,
        env=run_env,
        check=True,
        capture_output=True,
    )
    for module in (
        "app.infrastructure.db.seeds.reference",
        "app.infrastructure.db.seeds.sample",
    ):
        subprocess.run(  # noqa: S603
            ["uv", "run", "python", "-m", module],  # noqa: S607
            cwd=BACKEND_DIR,
            env=run_env,
            check=True,
            capture_output=True,
        )
    return db_env


@pytest.fixture
async def client(migrated_db: dict[str, str]) -> AsyncIterator[AsyncClient]:
    """An ASGI client wired to a freshly built app instance."""
    from app.main import create_app

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        yield http
    await app.state.engine.dispose()


# ── Shared warehouse and client ──────────────────────────────────────


@pytest.fixture(scope="session")
def warehouse_builder(tmp_path_factory: pytest.TempPathFactory):  # type: ignore[no-untyped-def]
    """Build a warehouse of a given shape once per session.

    Cached on the shape rather than on the requesting module, so a suite asking
    for the same estate as an earlier one pays nothing. This is what turns a
    thirty-minute integration run into a few minutes — and a thirty-minute job
    is one that never runs in CI.
    """
    built: dict[str, tuple[Path, Path]] = {}

    def make(shape: warehouse.Shape) -> tuple[Path, Path]:
        """Returns the warehouse path and the root it was built under."""
        if shape.slug not in built:
            root = tmp_path_factory.mktemp(shape.slug)
            built[shape.slug] = (warehouse.build(shape, root), root)
        return built[shape.slug]

    return make


@pytest.fixture(scope="session")
def estate_warehouse(warehouse_builder) -> Path:  # type: ignore[no-untyped-def]
    """A realistic estate: ten stores over nine weeks."""
    return warehouse_builder(warehouse.ESTATE)[0]  # type: ignore[no-any-return]


@pytest.fixture(scope="session")
def tenant_a_warehouse(warehouse_builder) -> Path:  # type: ignore[no-untyped-def]
    """A small, real, distinct warehouse for Prompt 12.5's Tenant A."""
    return warehouse_builder(warehouse.TENANT_A_SHAPE)[0]  # type: ignore[no-any-return]


@pytest.fixture(scope="session")
def tenant_b_warehouse(warehouse_builder) -> Path:  # type: ignore[no-untyped-def]
    """A different-scale, real, distinct warehouse for Prompt 12.5's Tenant B."""
    return warehouse_builder(warehouse.TENANT_B_SHAPE)[0]  # type: ignore[no-any-return]


@pytest.fixture(scope="session")
def deep_warehouse(warehouse_builder) -> Path:  # type: ignore[no-untyped-def]
    """Deep history, a narrow estate, and a real training run over it.

    Forecast suites assert on published predictions and on the accuracy
    scoreboard, so the models have to have actually been trained — a fixture
    that only builds the marts would leave every forecast endpoint empty and
    every assertion about bands trivially unreachable.
    """
    path, root = warehouse_builder(warehouse.DEEP_HISTORY)
    warehouse.train_forecasts(path, root)
    return path  # type: ignore[no-any-return]


async def _point_demo_tenant_at(migrated_db: dict[str, str], warehouse_path: Path) -> None:
    """Repoint the demo tenant's resolved warehouse at this test run's file.

    Prompt 12.5: the API resolves a tenant's warehouse from
    `Tenant.warehouse_path` (falling back to a per-slug convention), not from
    `RM_WAREHOUSE_DUCKDB_PATH` directly — that env var is what the *migration*
    used to backfill the demo tenant's row once, not what request handling
    reads. Each test run builds its own throwaway warehouse at a fresh
    `tmp_path`, so the demo tenant's row has to be pointed at it explicitly,
    the same way a real onboarding flow would point a newly provisioned
    tenant at its own file.
    """
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    url = (
        f"postgresql+asyncpg://{migrated_db['RM_DB_USER']}:{migrated_db['RM_DB_PASSWORD']}"
        f"@{migrated_db['RM_DB_HOST']}:{migrated_db['RM_DB_PORT']}/{migrated_db['RM_DB_NAME']}"
    )
    engine = create_async_engine(url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text("UPDATE tenant SET warehouse_path = :path WHERE slug = 'northwind-threads'"),
                {"path": str(warehouse_path)},
            )
    finally:
        await engine.dispose()


async def _client_for(
    migrated_db: dict[str, str], warehouse_path: Path
) -> AsyncIterator[AsyncClient]:
    await _point_demo_tenant_at(migrated_db, warehouse_path)
    # No Redis in the test process: a cache shared across suites would serve
    # one suite's rows to another's assertions.
    os.environ.pop("RM_REDIS_CACHE_URL", None)

    from app.main import create_app

    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http
    await app.state.engine.dispose()


@pytest.fixture
async def api(migrated_db: dict[str, str], estate_warehouse: Path) -> AsyncIterator[AsyncClient]:
    """A client over the shared estate warehouse.

    Function-scoped even though the warehouse is not: the app holds a database
    engine and a cache, and leaking those between tests is how one test's
    connection pool becomes another's flake.
    """
    async for client in _client_for(migrated_db, estate_warehouse):
        yield client


@pytest.fixture
async def deep_api(migrated_db: dict[str, str], deep_warehouse: Path) -> AsyncIterator[AsyncClient]:
    """The same, over the deep-history warehouse."""
    async for client in _client_for(migrated_db, deep_warehouse):
        yield client


async def auth_headers(client: AsyncClient, role: str = "ceo") -> dict[str, str]:
    """Sign in as one of the seeded demo users."""
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": warehouse.USERS[role], "password": warehouse.DEMO_PASSWORD},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}
