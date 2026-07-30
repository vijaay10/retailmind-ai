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
