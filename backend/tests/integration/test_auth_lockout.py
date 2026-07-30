"""Account lockout, end to end.

Kept in its own module because it burns login attempts against a shared seeded
user: mixing it with the flow tests would make their failure counts depend on
execution order.
"""

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.integration

TARGET = "yusuf@northwind.example"  # finance user, untouched by the flow tests
CORRECT = "ChangeMe-Demo1!"  # noqa: S105 — seeded demo credential


async def test_repeated_failures_lock_the_account(client: AsyncClient) -> None:
    """Five failures inside the window lock the account with backoff.

    The final assertion is the important one: after lockout, even the *correct*
    password is refused. A lockout that only blocks wrong passwords protects
    nothing.
    """
    for _ in range(5):
        response = await client.post(
            "/api/v1/auth/login", json={"email": TARGET, "password": "wrong-password"}
        )
        assert response.status_code == 401

    locked = await client.post("/api/v1/auth/login", json={"email": TARGET, "password": CORRECT})
    assert locked.status_code == 401
    assert locked.json()["type"].endswith("/account-locked")
    assert "locked" in locked.json()["detail"].lower()


async def test_failed_attempts_are_recorded_in_the_security_ledger(
    client: AsyncClient, migrated_db: dict[str, str]
) -> None:
    """The ledger must survive the 401 it accompanies.

    These rows are written and then an exception is raised; without an explicit
    mid-request commit they would roll back, and lockout would never trigger.
    """
    from sqlalchemy import text

    from app.infrastructure.db.session import create_engine

    await client.post(
        "/api/v1/auth/login",
        json={"email": "ghost@northwind.example", "password": "does-not-matter"},
    )

    engine = create_engine()
    async with engine.connect() as conn:
        count = await conn.scalar(
            text("SELECT count(*) FROM auth_event WHERE event = 'login.failed'")
        )
    await engine.dispose()
    assert count and count > 0
