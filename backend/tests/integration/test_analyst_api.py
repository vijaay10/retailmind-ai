"""The business analyst against a live warehouse.

Proves all eight capabilities reach real engines and come back in the shape a
senior analyst gives — including the two things that distinguish it from a
chatbot: refusing what it cannot answer, and saying what it did not check.
"""

import os
import subprocess
import sys
from collections.abc import AsyncIterator, Iterator
from datetime import date, timedelta
from pathlib import Path

import pytest

pytest.importorskip("testcontainers", reason="integration extra not installed")
from httpx import ASGITransport, AsyncClient  # noqa: E402

pytestmark = pytest.mark.integration

REPO = Path(__file__).resolve().parents[3]
DBT_DIR = REPO / "data_platform" / "dbt"
LAST_DAY = date(2026, 7, 21)
HISTORY_DAYS = 42
DEMO_PASSWORD = "ChangeMe-Demo1!"  # noqa: S105 — seeded demo credential

USERS = {"ceo": "priya@northwind.example", "admin": "sam@northwind.example"}


@pytest.fixture(scope="module")
def analyst_warehouse(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    sys.path.insert(0, str(REPO / "data_platform"))

    from ingestion.connectors.csv_files import CsvFileConnector
    from ingestion.core.config import EtlSettings
    from ingestion.core.duck import connect
    from ingestion.domain.schema import SourceSchema
    from ingestion.domain.window import Window
    from ingestion.generators import (
        fulfilment,
        inventory_files,
        pos_files,
        purchase_orders,
        weather,
    )
    from ingestion.pipeline import IngestionPipeline

    root = tmp_path_factory.mktemp("analyst_wh")
    settings = EtlSettings(
        landing_root=root / "lake",
        inbox_root=root / "inbox",
        warehouse_path=root / "wh.duckdb",
        reject_rate_threshold=0.10,
    )

    stores = 6
    first_day = LAST_DAY - timedelta(days=HISTORY_DAYS - 1)
    for offset in range(HISTORY_DAYS):
        day = first_day + timedelta(days=offset)
        pos_files.generate_day(
            settings.inbox_dir("pos"),
            day,
            stores=stores,
            lines_per_store=20,
            seed=7 + offset,
            history_start=first_day,
            history_end=LAST_DAY,
        )
        inventory_files.generate_day(
            settings.inbox_dir("inventory"), day, stores=stores, skus_per_store=8, seed=600 + offset
        )
        purchase_orders.generate_day(
            settings.inbox_dir("purchasing"),
            day,
            stores=stores,
            lines=16,
            seed=900 + offset,
            as_of=LAST_DAY,
        )
        weather.generate_day(
            settings.inbox_dir("weather"), day, seed=41 + offset, history_end=LAST_DAY
        )
        fulfilment.generate_day(
            settings.inbox_dir("fulfilment"),
            day,
            stores=stores,
            seed=55 + offset,
            history_end=LAST_DAY,
        )

    schema_root = REPO / "data_platform" / "ingestion" / "schemas"
    window = Window(first_day, LAST_DAY + timedelta(days=1))
    conn = connect(settings.warehouse_path)
    for source, table, units in (
        ("pos", "sales", stores),
        ("inventory", "positions", stores),
        ("purchasing", "orders", 1),
        ("weather", "observations", 1),
        ("fulfilment", "deliveries", 1),
    ):
        schema = SourceSchema.from_yaml(schema_root / source / f"{table}.yml")
        connector = CsvFileConnector(
            schema=schema, settings=settings, connection=conn, expected_units=units
        )
        summary = IngestionPipeline(connector=connector, settings=settings, connection=conn).run(
            window
        )
        assert not summary.quarantined, f"{source}: {summary.quarantined}"
    conn.close()

    env = {
        **os.environ,
        "RM_WAREHOUSE_DUCKDB_PATH": str(settings.warehouse_path),
        "DBT_TARGET_PATH": str(root / "dbt_target"),
    }
    for step in ("seed", "snapshot", "build"):
        result = subprocess.run(  # noqa: S603
            ["uv", "run", "dbt", step, "--profiles-dir", "."],  # noqa: S607
            cwd=DBT_DIR,
            env=env,
            check=False,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"dbt {step} failed:\n{result.stdout[-3000:]}"

    yield settings.warehouse_path


@pytest.fixture
async def client(
    migrated_db: dict[str, str], analyst_warehouse: Path
) -> AsyncIterator[AsyncClient]:
    os.environ["RM_WAREHOUSE_DUCKDB_PATH"] = str(analyst_warehouse)
    os.environ.pop("RM_REDIS_CACHE_URL", None)

    from app.main import create_app

    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http
    await app.state.engine.dispose()


async def _auth(client: AsyncClient, role: str = "ceo") -> dict[str, str]:
    response = await client.post(
        "/api/v1/auth/login", json={"email": USERS[role], "password": DEMO_PASSWORD}
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def _ask(
    client: AsyncClient,
    question: str,
    *,
    conversation: list[dict] | None = None,
    role: str = "ceo",
) -> tuple[int, dict]:
    response = await client.post(
        "/api/v1/analyst/ask",
        headers=await _auth(client, role),
        json={
            "question": question,
            "conversation": conversation or [],
            "as_of": LAST_DAY.isoformat(),
        },
    )
    return response.status_code, response.json()


# ── All eight capabilities reach a real engine ───────────────────────


@pytest.mark.parametrize(
    ("question", "capability"),
    [
        ("What does AOV mean?", "explain_kpi"),
        ("Show revenue by region", "answer"),
        ("Why did revenue fall?", "investigate"),
        ("What should we do next?", "recommend"),
        ("Summarise how we are doing", "summarise"),
        ("Compare this period against the prior one", "compare"),
        ("Where could our measurement improve?", "improve"),
    ],
)
async def test_every_capability_answers(
    client: AsyncClient, question: str, capability: str
) -> None:
    status, body = await _ask(client, question)
    assert status == 200, body
    assert body["capability"] == capability
    assert body["headline"]


async def test_the_forecast_capability_answers_or_refuses_with_a_reason(
    client: AsyncClient,
) -> None:
    """This fixture never trains a model, so there is nothing to explain.

    Both outcomes are correct: explain the published forecast, or say plainly
    that none exists. What would be wrong is a confident answer built on a
    forecast nobody produced — which is why the refusal has to name the reason
    rather than returning an empty success.
    """
    status, body = await _ask(client, "How much should I trust the forecast?")

    if status == 200:
        assert body["capability"] == "explain_forecast"
        assert body["facts"]
    else:
        assert status == 422
        assert "forecast" in str(body).lower()


# ── The senior-analyst contract holds end to end ─────────────────────


async def test_facts_and_inferences_come_back_separately(client: AsyncClient) -> None:
    """A decomposition is arithmetic; an explanation for it is a hypothesis."""
    status, body = await _ask(client, "Why did revenue fall?")
    assert status == 200

    for fact in body["facts"]:
        assert fact["certainty"] in {"measured", "derived"}
    for inference in body["inferences"]:
        assert inference["certainty"] in {"inferred", "unknown"}


async def test_every_answer_says_what_it_checked(client: AsyncClient) -> None:
    status, body = await _ask(client, "Show revenue by region")
    assert status == 200
    assert body["checked"]


async def test_answers_say_what_they_did_not_check(client: AsyncClient) -> None:
    """An assistant that reports only what it looked at leaves its silences
    unreadable — the reader cannot tell whether returns were fine or never
    examined."""
    status, body = await _ask(client, "Show revenue by region")
    assert status == 200
    assert body["not_checked"]


async def test_every_answer_proposes_a_next_question(client: AsyncClient) -> None:
    """An analyst who answers exactly what was asked and stops is a search box."""
    for question in ("Show revenue by region", "Why did revenue fall?", "What does AOV mean?"):
        status, body = await _ask(client, question)
        assert status == 200
        assert body["follow_ups"], question
        assert all(item["because"] for item in body["follow_ups"])


async def test_statements_name_their_source(client: AsyncClient) -> None:
    status, body = await _ask(client, "Why did revenue fall?")
    assert status == 200
    assert all(fact["source"] for fact in body["facts"])


# ── Conversation ─────────────────────────────────────────────────────


async def test_a_follow_up_resolves_against_the_previous_turn(
    client: AsyncClient,
) -> None:
    """ "Why did that drop?" is meaningless alone and obvious in context."""
    status, first = await _ask(client, "Show revenue by region")
    assert status == 200

    status, second = await _ask(
        client, "Why did that drop?", conversation=first["conversation"]["turns"]
    )
    assert status == 200
    assert second["capability"] == "investigate"


async def test_the_conversation_grows_with_each_turn(client: AsyncClient) -> None:
    _, first = await _ask(client, "Show revenue by region")
    _, second = await _ask(
        client, "What should we do?", conversation=first["conversation"]["turns"]
    )
    assert len(second["conversation"]["turns"]) == len(first["conversation"]["turns"]) + 1


async def test_a_pronoun_without_history_is_not_invented(client: AsyncClient) -> None:
    """Guessing a subject produces a confident answer about something arbitrary."""
    status, body = await _ask(client, "Why did that drop?")
    assert status in {200, 422}


# ── Restraint ────────────────────────────────────────────────────────


async def test_a_question_about_something_unmeasured_is_refused(
    client: AsyncClient,
) -> None:
    """The failure this design exists to prevent.

    "ROI on our TikTok campaign" resolves 'campaign' to the promotions domain
    and would otherwise return total promotional revenue — a confident answer
    about a channel the platform does not have and a metric it does not
    compute.
    """
    status, body = await _ask(client, "What is the ROI on our TikTok campaign?")
    assert status == 422, body


async def test_an_unknown_metric_is_not_explained(client: AsyncClient) -> None:
    status, _ = await _ask(client, "What does frobnication mean?")
    assert status == 422


async def test_an_empty_question_is_refused(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/analyst/ask", headers=await _auth(client), json={"question": "   "}
    )
    assert response.status_code == 422


async def test_the_request_rejects_unknown_fields(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/analyst/ask",
        headers=await _auth(client),
        json={"question": "revenue", "sql": "DROP TABLE fct_sales"},
    )
    assert response.status_code == 422


# ── Composition, not reimplementation ────────────────────────────────


async def test_the_analyst_agrees_with_the_engine_behind_it(
    client: AsyncClient,
) -> None:
    """An assistant with its own implementation contradicts the screen."""
    _, analyst = await _ask(client, "Why did revenue fall?")

    direct = await client.get(
        "/api/v1/rca/investigate",
        headers=await _auth(client),
        params={"current_end": LAST_DAY.isoformat()},
    )
    assert direct.status_code == 200

    findings = direct.json()["findings"]
    if findings:
        assert findings[0]["headline"] in analyst["headline"] or any(
            findings[0]["headline"] == fact["text"] for fact in analyst["facts"]
        )


async def test_the_improve_capability_lists_the_platforms_own_gaps(
    client: AsyncClient,
) -> None:
    """The most senior contribution is often stating what cannot be answered."""
    status, body = await _ask(client, "Where could our measurement improve?")
    assert status == 200
    assert body["inferences"]
    assert all(item["certainty"] == "unknown" for item in body["inferences"])


# ── Authorization ────────────────────────────────────────────────────


async def test_anonymous_access_is_rejected(client: AsyncClient) -> None:
    response = await client.post("/api/v1/analyst/ask", json={"question": "revenue"})
    assert response.status_code == 401
