"""Migration + seed round-trip against a real Postgres 16 (testcontainers).

Proves what dialect-compile tests cannot: extensions, the UUIDv7 function,
partitioned-table DDL, triggers, views/MVs, and both seeds — the DB doctrine
that an untested migration is a hope.

Marked ``integration``: requires Docker; excluded from the default `make test`.
"""

import os
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

pytest.importorskip("testcontainers", reason="integration extra not installed")
from testcontainers.postgres import PostgresContainer  # noqa: E402

pytestmark = pytest.mark.integration

BACKEND_DIR = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def pg_env() -> Iterator[dict[str, str]]:
    """A migrated Postgres 16 and the RM_DB_* env pointing at it."""
    with PostgresContainer("postgres:16.4", driver=None) as pg:
        env = {
            **os.environ,
            "RM_DB_HOST": pg.get_container_host_ip(),
            "RM_DB_PORT": str(pg.get_exposed_port(5432)),
            "RM_DB_NAME": pg.dbname,
            "RM_DB_USER": pg.username,
            "RM_DB_PASSWORD": pg.password,
        }
        subprocess.run(  # noqa: S603 — fixed argv, test-only
            ["uv", "run", "alembic", "upgrade", "head"],  # noqa: S607
            cwd=BACKEND_DIR,
            env=env,
            check=True,
            capture_output=True,
        )
        yield env


def _psql(env: dict[str, str], sql: str) -> str:
    """Run a query through the app engine (sync bridge for test assertions)."""
    script = (
        "import asyncio, os, json\n"
        "from sqlalchemy import text\n"
        "from app.infrastructure.db.session import create_engine\n"
        "async def go():\n"
        "    e = create_engine()\n"
        "    async with e.connect() as c:\n"
        f"        r = await c.execute(text({sql!r}))\n"
        "        rows = [list(map(str, row)) for row in r] if r.returns_rows else []\n"
        "        await c.commit()\n"
        "        print(json.dumps(rows))\n"
        "    await e.dispose()\n"
        "asyncio.run(go())\n"
    )
    out = subprocess.run(  # noqa: S603
        ["uv", "run", "python", "-c", script],  # noqa: S607
        cwd=BACKEND_DIR,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    return out.stdout.strip().splitlines()[-1]


def test_all_tables_created(pg_env: dict[str, str]) -> None:
    count = _psql(
        pg_env,
        "SELECT count(*) FROM information_schema.tables "
        "WHERE table_schema='public' AND table_type='BASE TABLE'",
    )
    # 42 model tables (see backend/tests/unit/test_db_schema.py for why it's
    # 42, not 41 — recommendation_decision, recommendation_outcome, and
    # llm_request_log were added after this count was first written) +
    # alembic_version + 2 default partitions (audit_event_default,
    # llm_usage_default). Verified against a real disposable Postgres during
    # Prompt 10.5: 45 rows, exact table list checked by hand.
    assert '"45"' in count


def test_uuid_v7_function_is_versioned_and_time_ordered(pg_env: dict[str, str]) -> None:
    # Version nibble must be 7; ordering needs a real time gap (same-millisecond
    # calls tie on the timestamp prefix and fall to random bits).
    version = _psql(pg_env, "SELECT substring(uuid_generate_v7()::text FROM 15 FOR 1)")
    assert "7" in version
    ordered = _psql(
        pg_env,
        "SELECT a < b FROM (SELECT uuid_generate_v7() a, pg_sleep(0.005), uuid_generate_v7() b) s",
    )
    assert "True" in ordered


def test_views_and_matviews_exist(pg_env: dict[str, str]) -> None:
    views = _psql(
        pg_env,
        "SELECT viewname FROM pg_views WHERE schemaname='public' "
        "UNION SELECT matviewname FROM pg_matviews WHERE schemaname='public' ORDER BY 1",
    )
    for name in (
        "mv_alert_quality",
        "mv_llm_spend_month",
        "mv_pipeline_sla",
        "v_alert_inbox",
        "v_pipeline_health",
    ):
        assert name in views


def test_seeds_run_and_demo_story_is_queryable(pg_env: dict[str, str]) -> None:
    for module in (
        "app.infrastructure.db.seeds.reference",
        "app.infrastructure.db.seeds.sample",
    ):
        subprocess.run(  # noqa: S603
            ["uv", "run", "python", "-m", module],  # noqa: S607
            cwd=BACKEND_DIR,
            env=pg_env,
            check=True,
            capture_output=True,
        )
    # Idempotency: second run must be a no-op, not a violation.
    subprocess.run(  # noqa: S603
        ["uv", "run", "python", "-m", "app.infrastructure.db.seeds.sample"],  # noqa: S607
        cwd=BACKEND_DIR,
        env=pg_env,
        check=True,
        capture_output=True,
    )
    inbox = _psql(
        pg_env,
        "SELECT metric_key, severity, status FROM v_alert_inbox",
    )
    assert "net_revenue" in inbox and "critical" in inbox

    # MVs refresh concurrently thanks to their unique indexes (DB).
    _psql(pg_env, "REFRESH MATERIALIZED VIEW mv_alert_quality")
    quality = _psql(pg_env, "SELECT alerts_total, alerts_acked FROM mv_alert_quality")
    assert '"1", "1"' in quality


def test_updated_at_trigger_fires(pg_env: dict[str, str]) -> None:
    changed = _psql(
        pg_env,
        "UPDATE tenant SET name = name || '' RETURNING (updated_at > created_at)",
    )
    assert "True" in changed


def test_downgrade_round_trip(pg_env: dict[str, str]) -> None:
    """upgrade → downgrade → upgrade must be clean (Backend migration test)."""
    for target in ("base", "head"):
        subprocess.run(  # noqa: S603
            ["uv", "run", "alembic", "downgrade" if target == "base" else "upgrade", target],  # noqa: S607
            cwd=BACKEND_DIR,
            env=pg_env,
            check=True,
            capture_output=True,
        )
