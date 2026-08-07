"""Account lockout, end to end.

Kept in its own module because it burns login attempts against a shared seeded
user: mixing it with the flow tests would make their failure counts depend on
execution order.

**And it unlocks the account afterwards.** Lockout is derived from the
`auth_event` ledger rather than from a counter column, so leaving those rows
behind leaves the account locked for every later suite. That dependency was
invisible while each suite spent three minutes building its own warehouse —
the lockout window expired during the build — and surfaced the moment the
warehouse became shared and the suites started running back to back. A test
that only passes because the suite is slow is a test waiting to fail.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import text

pytestmark = pytest.mark.integration

TARGET = "yusuf@northwind.example"  # finance; other suites sign in as this user too
CORRECT = "ChangeMe-Demo1!"  # noqa: S105 — seeded demo credential


@pytest.fixture(autouse=True)
async def unlock_afterwards(migrated_db: dict[str, str]):  # type: ignore[no-untyped-def]
    """Clear this module's failed logins so the account is usable again.

    Autouse and after every test, not once per module: a failure part-way
    through would otherwise leave the account locked for everything that
    follows, and the resulting errors would point at innocent suites.
    """
    yield

    from app.infrastructure.db.session import create_engine

    engine = create_engine()
    async with engine.begin() as conn:
        # Lockout counts `login.failed` rows for a user id inside a window, so
        # clearing this user's failures is exactly the unlock.
        await conn.execute(
            text(
                "DELETE FROM auth_event WHERE event = 'login.failed' AND user_id = "
                "(SELECT id FROM app_user WHERE email = :email)"
            ),
            {"email": TARGET},
        )
    await engine.dispose()


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
