"""The notification ledger against real Postgres.

Suppression is the whole point of this table: the sweep asks the ledger what
it has already sent and stays quiet about the rest. Everything here needs a
real database, because the bug this file exists for — a JSON expression that
compiles fine and is rejected by Postgres at execution — is invisible to any
test that fakes the session.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa

pytest.importorskip("testcontainers", reason="integration extra not installed")
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from app.infrastructure.db.repositories.notifications import (  # noqa: E402
    SUPPRESSION_LOOKBACK_DAYS,
    NotificationRepository,
)

pytestmark = pytest.mark.integration


@pytest.fixture
async def repository(migrated_db: dict[str, str]):  # type: ignore[no-untyped-def]
    """A repository bound to the demo tenant in the migrated database."""
    url = (
        f"postgresql+asyncpg://{migrated_db['RM_DB_USER']}:{migrated_db['RM_DB_PASSWORD']}"
        f"@{migrated_db['RM_DB_HOST']}:{migrated_db['RM_DB_PORT']}/{migrated_db['RM_DB_NAME']}"
    )
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        tenant_id = (
            await session.execute(sa.text("SELECT id FROM tenant ORDER BY created_at LIMIT 1"))
        ).scalar_one()
        user_id = (
            await session.execute(
                sa.text("SELECT id FROM app_user WHERE tenant_id = :t LIMIT 1"),
                {"t": tenant_id},
            )
        ).scalar_one()

        yield NotificationRepository(session, tenant_id), str(user_id), session

    await engine.dispose()


async def test_the_suppression_memory_reads_back_from_postgres(repository) -> None:  # type: ignore[no-untyped-def]
    """The regression this file was written for.

    ``payload['fingerprint'].astext`` written separately in the select and the
    GROUP BY renders two different bind parameters. Postgres compares grouping
    expressions structurally, so `payload ->> $1` and `payload ->> $3` are not
    the same expression and it rejects the statement — at execution time, on
    the sweep endpoint, having compiled without complaint. Every alert in the
    system stops going out.
    """
    repo, user_id, session = repository
    fingerprint = f"fp-{uuid.uuid4().hex[:12]}"

    await repo.record(
        user_id=user_id,
        channel="in_app",
        event_type="low_inventory",
        payload={"fingerprint": fingerprint, "severity": "warn", "title": "t", "body": "b"},
    )
    await session.commit()

    history = await repo.last_notified()

    assert fingerprint in history
    assert isinstance(history[fingerprint], datetime)


async def test_a_fingerprint_maps_to_its_most_recent_send(repository) -> None:  # type: ignore[no-untyped-def]
    """Suppression asks "how long since we last said this", so an older
    duplicate must not shadow the newer one and reopen the quiet window."""
    repo, user_id, session = repository
    fingerprint = f"fp-{uuid.uuid4().hex[:12]}"

    for _ in range(3):
        await repo.record(
            user_id=user_id,
            channel="in_app",
            event_type="sales_drop",
            payload={"fingerprint": fingerprint, "severity": "critical"},
        )
    await session.commit()

    history = await repo.last_notified()
    latest = (
        await session.execute(
            sa.text(
                "SELECT max(created_at) FROM notification "
                "WHERE payload->>'fingerprint' = :fingerprint"
            ),
            {"fingerprint": fingerprint},
        )
    ).scalar_one()

    assert history[fingerprint] == latest


async def test_notifications_without_a_fingerprint_are_ignored(repository) -> None:  # type: ignore[no-untyped-def]
    """Not every row is an alert. A digest carries no fingerprint, and reading
    one back as a null key would suppress an entire class of real alerts."""
    repo, user_id, session = repository

    await repo.record(
        user_id=user_id,
        channel="in_app",
        event_type="recommendation_ready",
        payload={"severity": "info"},
    )
    await session.commit()

    assert None not in (await repo.last_notified())


async def test_the_memory_does_not_reach_past_its_window(repository) -> None:  # type: ignore[no-untyped-def]
    """A fingerprint older than the lookback must fall out, or an alert that
    fired once would be suppressed forever."""
    repo, user_id, session = repository
    fingerprint = f"fp-{uuid.uuid4().hex[:12]}"

    await repo.record(
        user_id=user_id,
        channel="in_app",
        event_type="fraud",
        payload={"fingerprint": fingerprint, "severity": "critical"},
    )
    await session.flush()
    await session.execute(
        sa.text(
            "UPDATE notification SET created_at = :old WHERE payload->>'fingerprint' = :fingerprint"
        ),
        {
            "old": datetime.now(UTC) - timedelta(days=SUPPRESSION_LOOKBACK_DAYS + 1),
            "fingerprint": fingerprint,
        },
    )
    await session.commit()

    assert fingerprint not in (await repo.last_notified())
