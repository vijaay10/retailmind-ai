"""Recommendation endpoints against a real warehouse.

The unit suite proves the arithmetic. This one proves the engine reaches the
warehouse through the governed registry and comes back with advice whose
numbers hold together — that no recommendation claims more certainty than its
basis allows, that the portfolio totals do not promise the same pounds twice,
and that a campaign is never aimed at customers who are not at risk.
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
HISTORY_DAYS = 56
DEMO_PASSWORD = "ChangeMe-Demo1!"  # noqa: S105 — seeded demo credential

USERS = {"ceo": "priya@northwind.example", "admin": "sam@northwind.example"}

BASIS_CEILINGS = {"measured": 0.90, "modelled": 0.70, "assumed": 0.45}


@pytest.fixture(scope="module")
def recommendation_warehouse(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
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

    root = tmp_path_factory.mktemp("rec_wh")
    settings = EtlSettings(
        landing_root=root / "lake",
        inbox_root=root / "inbox",
        warehouse_path=root / "wh.duckdb",
        reject_rate_threshold=0.10,
    )

    stores = 10
    first_day = LAST_DAY - timedelta(days=HISTORY_DAYS - 1)
    for offset in range(HISTORY_DAYS):
        day = first_day + timedelta(days=offset)
        pos_files.generate_day(
            settings.inbox_dir("pos"),
            day,
            stores=stores,
            lines_per_store=24,
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
            lines=20,
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
    migrated_db: dict[str, str], recommendation_warehouse: Path
) -> AsyncIterator[AsyncClient]:
    os.environ["RM_WAREHOUSE_DUCKDB_PATH"] = str(recommendation_warehouse)
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


async def _get(client: AsyncClient, role: str = "ceo", **params: object) -> dict:
    payload: dict[str, object] = {"end_date": LAST_DAY.isoformat()}
    payload.update(params)
    response = await client.get(
        "/api/v1/recommendations", headers=await _auth(client, role), params=payload
    )
    assert response.status_code == 200, response.text
    return response.json()


# ── The engine produces usable advice ────────────────────────────────


async def test_recommendations_are_produced(client: AsyncClient) -> None:
    body = await _get(client)
    assert body["recommendations"], "no recommendations from a live warehouse"


async def test_several_categories_are_covered(client: AsyncClient) -> None:
    """A recommender that only ever talks about stock is an inventory report."""
    body = await _get(client)
    assert len(body["by_category"]) >= 2


async def test_a_single_category_can_be_requested(client: AsyncClient) -> None:
    body = await _get(client, categories=["inventory"])
    assert set(body["by_category"]) <= {"inventory"}


async def test_empty_categories_are_explained_not_omitted(client: AsyncClient) -> None:
    """A silently absent category looks identical to one that never ran."""
    body = await _get(client)
    for reason in body["categories_empty"].values():
        assert reason


# ── Numbers hold together ────────────────────────────────────────────


async def test_no_recommendation_claims_more_certainty_than_its_basis(
    client: AsyncClient,
) -> None:
    """The load-bearing rule: an estimate cannot outrun its weakest input."""
    for item in (await _get(client))["recommendations"]:
        basis = item["impact"]["basis"]
        assert item["confidence"] <= BASIS_CEILINGS[basis] + 1e-9, (
            f"{item['subject']} claims {item['confidence']} on a {basis} estimate"
        )
        assert item["confidence"] <= item["confidence_ceiling"]


async def test_the_portfolio_does_not_promise_the_same_pounds_twice(
    client: AsyncClient,
) -> None:
    body = await _get(client)
    assert body["net_profit_opportunity"] <= body["gross_profit_opportunity"] + 1e-6


async def test_capital_freed_is_reported_apart_from_profit(client: AsyncClient) -> None:
    """Clearing dead stock releases cash and books a loss.

    If the two were combined, every markdown would look like a profit
    opportunity — which is how a clearance programme gets approved on the
    strength of the thing that makes it expensive.
    """
    body = await _get(client)
    markdowns = [
        item
        for item in body["recommendations"]
        if item["category"] == "pricing" and item["impact"]["capital_freed"] > 0
    ]
    for item in markdowns:
        assert item["impact"]["profit"] < item["impact"]["capital_freed"]


async def test_recommendations_are_ranked_by_risk_adjusted_profit(
    client: AsyncClient,
) -> None:
    scores = [item["risk_adjusted_profit"] for item in (await _get(client))["recommendations"]]
    assert scores == sorted(scores, reverse=True)


# ── Every recommendation is defensible ───────────────────────────────


async def test_every_recommendation_states_its_method(client: AsyncClient) -> None:
    for item in (await _get(client))["recommendations"]:
        assert item["impact"]["method"], f"{item['subject']} gives a number with no derivation"


async def test_unmeasured_parameters_are_declared_as_placeholders(
    client: AsyncClient,
) -> None:
    """An assumption dressed as a measurement is the failure mode here."""
    for item in (await _get(client))["recommendations"]:
        for assumption in item["impact"]["assumptions"]:
            assert assumption["source"] in {"measured", "industry default", "placeholder"}
            if assumption["source"] != "measured":
                assert not assumption["is_evidenced"]


async def test_assumed_estimates_ship_a_sensitivity_range(client: AsyncClient) -> None:
    for item in (await _get(client))["recommendations"]:
        if item["impact"]["basis"] == "assumed":
            assert item["impact"]["pessimistic_profit"] is not None
            assert item["impact"]["optimistic_profit"] is not None


async def test_every_recommendation_carries_a_downside(client: AsyncClient) -> None:
    """A response reporting only upside is a sales pitch."""
    for item in (await _get(client))["recommendations"]:
        assert "downside_profit" in item["risk"]
        assert item["risk"]["principal_risk"]
        assert item["risk"]["band"] in {"low", "medium", "high"}


async def test_irreversible_actions_are_banded_high(client: AsyncClient) -> None:
    for item in (await _get(client))["recommendations"]:
        if item["risk"]["reversibility"] == "irreversible":
            assert item["risk"]["band"] == "high"


async def test_every_recommendation_names_its_disqualifier(client: AsyncClient) -> None:
    """The reader is usually the only one who can check it."""
    for item in (await _get(client))["recommendations"]:
        assert item["do_not_act_if"], f"{item['subject']} has no disqualifying condition"


async def test_every_recommendation_carries_evidence_and_an_owner(
    client: AsyncClient,
) -> None:
    for item in (await _get(client))["recommendations"]:
        assert item["evidence"]
        assert item["owner"]


# ── Restraint ────────────────────────────────────────────────────────


async def test_customers_who_are_not_at_risk_are_never_targeted(
    client: AsyncClient,
) -> None:
    """Spending retention budget on customers behaving normally."""
    for item in (await _get(client))["recommendations"]:
        if item["category"] in {"customer", "marketing"}:
            assert not any(band in item["subject"].lower() for band in ("none", "low", "unknown"))


async def test_the_response_admits_its_estimates_are_estimates(
    client: AsyncClient,
) -> None:
    body = await _get(client)
    assert any("estimate" in caveat.lower() for caveat in body["caveats"])


async def test_unmeasured_parameters_are_summarised_in_the_caveats(
    client: AsyncClient,
) -> None:
    body = await _get(client)
    if any(item["impact"]["rests_on_unmeasured_assumptions"] for item in body["recommendations"]):
        assert any("unmeasured" in caveat for caveat in body["caveats"])


async def test_the_limit_is_respected(client: AsyncClient) -> None:
    body = await _get(client, limit=3)
    assert len(body["recommendations"]) <= 3


# ── Authorization ────────────────────────────────────────────────────


async def test_a_role_without_access_is_refused(client: AsyncClient) -> None:
    response = await client.get("/api/v1/recommendations", headers=await _auth(client, "admin"))
    assert response.status_code == 403


async def test_anonymous_access_is_rejected(client: AsyncClient) -> None:
    response = await client.get("/api/v1/recommendations")
    assert response.status_code == 401
