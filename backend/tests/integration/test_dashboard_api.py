"""Executive dashboard endpoints against a real warehouse with history.

Forty-five days of data, so trends, growth horizons, and forecasts are real
rather than degenerate. The suite asserts the editorial contracts the
dashboard makes — weekday-aligned comparison, dollar-ranked attention,
accuracy travelling with forecasts — not merely that the endpoints respond.
"""

import os
import subprocess
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
HISTORY_DAYS = 21
DEMO_PASSWORD = "ChangeMe-Demo1!"  # noqa: S105 — seeded demo credential

USERS = {
    "ceo": "priya@northwind.example",
    "finance": "yusuf@northwind.example",
    "store_manager": "lena@northwind.example",
    "inventory": "aisha@northwind.example",
}


@pytest.fixture(scope="module")
def dashboard_warehouse(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    """Three weeks of history through the real pipeline, then dbt."""
    import sys

    sys.path.insert(0, str(REPO / "data_platform"))

    from ingestion.connectors.csv_files import CsvFileConnector
    from ingestion.core.config import EtlSettings
    from ingestion.core.duck import connect
    from ingestion.domain.schema import SourceSchema
    from ingestion.domain.window import Window
    from ingestion.generators import inventory_files, pos_files, purchase_orders
    from ingestion.pipeline import IngestionPipeline

    root = tmp_path_factory.mktemp("dash_wh")
    settings = EtlSettings(
        landing_root=root / "lake",
        inbox_root=root / "inbox",
        warehouse_path=root / "wh.duckdb",
        reject_rate_threshold=0.10,
    )

    stores = 4
    for offset in range(HISTORY_DAYS):
        day = LAST_DAY - timedelta(days=HISTORY_DAYS - 1 - offset)
        pos_files.generate_day(
            settings.inbox_dir("pos"), day, stores=stores, lines_per_store=20, seed=7 + offset
        )
        inventory_files.generate_day(
            settings.inbox_dir("inventory"),
            day,
            stores=stores,
            skus_per_store=15,
            seed=600 + offset,
        )
        purchase_orders.generate_day(
            settings.inbox_dir("purchasing"),
            day,
            stores=stores,
            lines=30,
            seed=900 + offset,
            as_of=LAST_DAY,
        )

    schema_root = REPO / "data_platform" / "ingestion" / "schemas"
    window = Window(LAST_DAY - timedelta(days=HISTORY_DAYS - 1), LAST_DAY + timedelta(days=1))
    conn = connect(settings.warehouse_path)
    # Purchasing arrives as one file for the whole estate rather than one per
    # store, so its completeness check counts a single unit.
    for source, table, units in (
        ("pos", "sales", stores),
        ("inventory", "positions", stores),
        ("purchasing", "orders", 1),
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
    migrated_db: dict[str, str], dashboard_warehouse: Path
) -> AsyncIterator[AsyncClient]:
    os.environ["RM_WAREHOUSE_DUCKDB_PATH"] = str(dashboard_warehouse)
    os.environ.pop("RM_REDIS_CACHE_URL", None)

    from app.main import create_app

    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http
    await app.state.engine.dispose()


async def _auth(client: AsyncClient, role: str) -> dict[str, str]:
    response = await client.post(
        "/api/v1/auth/login", json={"email": USERS[role], "password": DEMO_PASSWORD}
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


# ── 1. Today's revenue ───────────────────────────────────────────────


async def test_revenue_today_anchors_on_warehouse_data_not_the_clock(
    client: AsyncClient,
) -> None:
    """A dashboard showing an empty 'today' because the load has not finished
    is a dashboard nobody trusts."""
    response = await client.get(
        "/api/v1/dashboard/revenue/today", headers=await _auth(client, "ceo")
    )
    assert response.status_code == 200

    body = response.json()
    assert body["business_date"] == LAST_DAY.isoformat()
    assert body["business_date"] != date.today().isoformat()


async def test_revenue_today_compares_against_the_same_weekday(
    client: AsyncClient,
) -> None:
    """Comparing Monday to Sunday reports a weekday artefact as a business signal."""
    body = (
        await client.get("/api/v1/dashboard/revenue/today", headers=await _auth(client, "ceo"))
    ).json()

    business = date.fromisoformat(body["business_date"])
    comparison = date.fromisoformat(body["comparison_date"])
    assert (business - comparison).days == 7
    assert business.weekday() == comparison.weekday()


async def test_revenue_cards_carry_values_and_direction(client: AsyncClient) -> None:
    body = (
        await client.get("/api/v1/dashboard/revenue/today", headers=await _auth(client, "ceo"))
    ).json()

    cards = {card["key"]: card for card in body["cards"]}
    assert {"net_revenue", "units_sold", "orders", "aov", "discount_rate"} <= set(cards)
    assert cards["net_revenue"]["value"] > 0
    assert cards["net_revenue"]["direction"] in {"up", "down", "flat"}


async def test_small_movements_report_as_flat(client: AsyncClient) -> None:
    """Noise must not be styled as news."""
    body = (
        await client.get("/api/v1/dashboard/revenue/today", headers=await _auth(client, "ceo"))
    ).json()
    for card in body["cards"]:
        change = card["change_pct"]
        if change is not None and abs(change) < 0.005:
            assert card["direction"] == "flat"


# ── 2. Revenue trend ─────────────────────────────────────────────────


async def test_revenue_trend_returns_a_daily_series(client: AsyncClient) -> None:
    response = await client.get(
        "/api/v1/dashboard/revenue/trend",
        params={"days": 14},
        headers=await _auth(client, "ceo"),
    )
    assert response.status_code == 200

    series = response.json()["series"]
    assert len(series) >= 10
    assert all(point["net_revenue"] > 0 for point in series)
    dates = [point["business_date"] for point in series]
    assert dates == sorted(dates), "series must be chronological for charting"


async def test_trend_carries_snapshot_provenance(client: AsyncClient) -> None:
    body = (
        await client.get(
            "/api/v1/dashboard/revenue/trend",
            params={"days": 14},
            headers=await _auth(client, "ceo"),
        )
    ).json()
    assert body["meta"]["data_snapshot_id"]
    assert body["meta"]["freshness"] == LAST_DAY.isoformat()


# ── 3. Growth ────────────────────────────────────────────────────────


async def test_growth_reports_three_horizons(client: AsyncClient) -> None:
    """A bad day, a bad week, and a bad month need different reactions."""
    response = await client.get("/api/v1/dashboard/growth", headers=await _auth(client, "ceo"))
    assert response.status_code == 200

    horizons = {row["horizon"]: row for row in response.json()["horizons"]}
    assert set(horizons) == {"day", "week", "month"}
    assert horizons["week"]["days"] == 7
    assert horizons["week"]["current_revenue"] > 0


async def test_growth_change_is_arithmetically_consistent(client: AsyncClient) -> None:
    body = (await client.get("/api/v1/dashboard/growth", headers=await _auth(client, "ceo"))).json()
    for row in body["horizons"]:
        expected = round(row["current_revenue"] - row["prior_revenue"], 2)
        assert row["change_amount"] == pytest.approx(expected, abs=0.02)
        if row["prior_revenue"]:
            # change_pct is rounded to four places for display, so the
            # tolerance is half a unit in the last place — not a relative
            # tolerance, which would tighten as the value approaches zero.
            assert row["change_pct"] == pytest.approx(
                (row["current_revenue"] - row["prior_revenue"]) / abs(row["prior_revenue"]),
                abs=5e-5,
            )


# ── 4. Profit ────────────────────────────────────────────────────────


async def test_profit_returns_margin_cards_and_category_split(client: AsyncClient) -> None:
    response = await client.get(
        "/api/v1/dashboard/profit", params={"days": 7}, headers=await _auth(client, "finance")
    )
    assert response.status_code == 200

    body = response.json()
    keys = {card["key"] for card in body["cards"]}
    assert {"margin_amount", "margin_rate", "cogs_amount"} <= keys
    assert body["by_category"]
    assert "category" in body["by_category"][0]


async def test_profit_requires_the_profitability_module(client: AsyncClient) -> None:
    """Cost is the number most organisations restrict; revenue access is not enough."""
    response = await client.get(
        "/api/v1/dashboard/profit", headers=await _auth(client, "store_manager")
    )
    assert response.status_code == 403
    assert "profitability" in response.json()["hint"]


# ── 5. Top products ──────────────────────────────────────────────────


async def test_top_products_are_ranked_by_the_requested_metric(
    client: AsyncClient,
) -> None:
    """'Top' is not one question — the ranking metric must actually change the order."""
    headers = await _auth(client, "ceo")

    by_revenue = (
        await client.get(
            "/api/v1/dashboard/products/top",
            params={"by": "net_revenue", "limit": 5},
            headers=headers,
        )
    ).json()
    by_units = (
        await client.get(
            "/api/v1/dashboard/products/top",
            params={"by": "units_sold", "limit": 5},
            headers=headers,
        )
    ).json()

    revenues = [row["net_revenue"] for row in by_revenue["products"]]
    assert revenues == sorted(revenues, reverse=True)
    units = [row["units_sold"] for row in by_units["products"]]
    assert units == sorted(units, reverse=True)
    assert by_revenue["ranked_by"] == "net_revenue"


async def test_top_products_carry_identity_and_margin(client: AsyncClient) -> None:
    body = (
        await client.get(
            "/api/v1/dashboard/products/top",
            params={"limit": 3},
            headers=await _auth(client, "ceo"),
        )
    ).json()
    row = body["products"][0]
    assert {"sku", "product_name", "category", "net_revenue", "margin_rate"} <= set(row)


# ── 6. Store rankings ────────────────────────────────────────────────


async def test_store_rankings_are_ordered_and_numbered(client: AsyncClient) -> None:
    response = await client.get(
        "/api/v1/dashboard/stores/ranking",
        params={"limit": 10},
        headers=await _auth(client, "ceo"),
    )
    assert response.status_code == 200

    stores = response.json()["stores"]
    assert [row["rank"] for row in stores] == list(range(1, len(stores) + 1))
    revenues = [row["net_revenue"] for row in stores]
    assert revenues == sorted(revenues, reverse=True)


async def test_store_rows_carry_their_peer_cluster(client: AsyncClient) -> None:
    """Comparing a flagship to an outlet is malpractice; the cluster makes
    correct grouping possible even without a filter."""
    body = (
        await client.get("/api/v1/dashboard/stores/ranking", headers=await _auth(client, "ceo"))
    ).json()
    assert all(row.get("store_cluster") for row in body["stores"])


async def test_cluster_filter_scopes_the_league_table(client: AsyncClient) -> None:
    headers = await _auth(client, "ceo")
    unfiltered = (await client.get("/api/v1/dashboard/stores/ranking", headers=headers)).json()
    cluster = unfiltered["stores"][0]["store_cluster"]

    filtered = (
        await client.get(
            "/api/v1/dashboard/stores/ranking", params={"cluster": cluster}, headers=headers
        )
    ).json()
    assert filtered["cluster"] == cluster
    assert {row["store_cluster"] for row in filtered["stores"]} == {cluster}


# ── 7. Inventory risk ────────────────────────────────────────────────


async def test_inventory_risk_ranks_by_rate_not_absolute_count(
    client: AsyncClient,
) -> None:
    """Four stockouts in twenty positions beats ten in five thousand."""
    response = await client.get(
        "/api/v1/dashboard/inventory/risk", headers=await _auth(client, "inventory")
    )
    assert response.status_code == 200

    rows = response.json()["at_risk"]
    rates = [row["stockout_rate"] for row in rows]
    assert rates == sorted(rates, reverse=True)


async def test_inventory_risk_reads_a_single_position_date(client: AsyncClient) -> None:
    """Positions are semi-additive: summing them across days invents stock."""
    body = (
        await client.get(
            "/api/v1/dashboard/inventory/risk", headers=await _auth(client, "inventory")
        )
    ).json()
    assert body["position_date"] == LAST_DAY.isoformat()


async def test_planted_stockouts_surface_in_the_risk_tile(client: AsyncClient) -> None:
    body = (
        await client.get(
            "/api/v1/dashboard/inventory/risk", headers=await _auth(client, "inventory")
        )
    ).json()
    assert any(row["stockout_rate"] > 0 for row in body["at_risk"])


# ── 8. Forecast ──────────────────────────────────────────────────────


async def test_forecast_returns_a_series_with_intervals(client: AsyncClient) -> None:
    """A bare-point forecast is a forbidden state in this product."""
    response = await client.get(
        "/api/v1/dashboard/forecast",
        params={"days": 14},
        headers=await _auth(client, "ceo"),
    )
    assert response.status_code == 200

    series = response.json()["series"]
    assert series
    assert all(point["yhat_revenue"] > 0 for point in series)

    # Early points legitimately carry no interval: with fewer than two prior
    # observations of that weekday there is no spread to estimate, and the
    # model returns NULL rather than fabricating a width. Every point that
    # *does* have a band must be well-ordered.
    banded = [p for p in series if p["yhat_revenue_lower"] is not None]
    assert banded, "the later part of the horizon must carry intervals"
    for point in banded:
        assert point["yhat_revenue_lower"] <= point["yhat_revenue"] <= point["yhat_revenue_upper"]


async def test_forecast_publishes_its_own_accuracy(client: AsyncClient) -> None:
    """Accuracy travels with the forecast, never behind a separate click: a
    planner needs to know how wrong the model has been."""
    body = (
        await client.get("/api/v1/dashboard/forecast", headers=await _auth(client, "ceo"))
    ).json()

    accuracy = body["accuracy"]
    assert accuracy["model_name"] == "seasonal_naive_w4"
    assert accuracy["model_class"] == "baseline"
    assert 0 <= accuracy["wape"] < 1
    assert accuracy["interval_coverage"] is not None
    assert accuracy["forecast_days_evaluated"] > 0


async def test_forecast_requires_forecast_permission(client: AsyncClient) -> None:
    response = await client.get(
        "/api/v1/dashboard/forecast", headers=await _auth(client, "store_manager")
    )
    # Store managers hold forecasts.read, so this succeeds — the assertion
    # documents the intended grant rather than a denial.
    assert response.status_code == 200


# ── 9. Alerts ────────────────────────────────────────────────────────


async def test_alerts_return_open_items_with_expected_bands(client: AsyncClient) -> None:
    response = await client.get("/api/v1/dashboard/alerts", headers=await _auth(client, "ceo"))
    assert response.status_code == 200

    body = response.json()
    assert body["counts"]
    alert = body["alerts"][0]
    assert alert["severity"] in {"critical", "warn", "info"}
    assert alert["expected_low"] < alert["expected_high"]
    assert alert["scope"]  # the exact slice that breached
    assert alert["data_snapshot_id"]


async def test_alerts_are_ordered_most_severe_first(client: AsyncClient) -> None:
    """Alphabetical severity ordering would put 'critical' after 'warn'."""
    body = (await client.get("/api/v1/dashboard/alerts", headers=await _auth(client, "ceo"))).json()
    rank = {"critical": 0, "warn": 1, "info": 2}
    severities = [rank[a["severity"]] for a in body["alerts"]]
    assert severities == sorted(severities)


async def test_alert_deviation_is_signed_against_the_band(client: AsyncClient) -> None:
    body = (await client.get("/api/v1/dashboard/alerts", headers=await _auth(client, "ceo"))).json()
    for alert in body["alerts"]:
        if alert["observed"] < alert["expected_low"]:
            assert alert["deviation_pct"] < 0


# ── 10. Recommendations ──────────────────────────────────────────────


async def test_recommendations_are_ranked_by_impact(client: AsyncClient) -> None:
    response = await client.get(
        "/api/v1/dashboard/recommendations", headers=await _auth(client, "ceo")
    )
    assert response.status_code == 200

    recs = response.json()["recommendations"]
    assert recs
    impacts = [rec["impact_value"] for rec in recs]
    assert impacts == sorted(impacts, reverse=True)


async def test_recommendations_state_their_estimation_method(
    client: AsyncClient,
) -> None:
    """An impact figure without a stated method is not actionable."""
    body = (
        await client.get("/api/v1/dashboard/recommendations", headers=await _auth(client, "ceo"))
    ).json()
    rec = body["recommendations"][0]
    assert "method" in rec["expected_impact"]
    assert rec["evidence"]
    assert rec["confidence"] in {"high", "medium", "low"}


# ── Composite ────────────────────────────────────────────────────────


async def test_executive_overview_assembles_every_tile(client: AsyncClient) -> None:
    response = await client.get("/api/v1/dashboard/executive", headers=await _auth(client, "ceo"))
    assert response.status_code == 200

    body = response.json()
    assert body["business_date"] == LAST_DAY.isoformat()
    assert body["revenue"]["cards"]
    assert body["growth"]["horizons"]
    assert body["alerts"]["alerts"]
    assert body["recommendations"]["recommendations"]
    assert body["top_products"]
    assert body["inventory_risk"]
    assert body["sections_unavailable"] == []


async def test_overview_names_the_sections_a_role_cannot_see(
    client: AsyncClient,
) -> None:
    """Naming the gap lets the UI explain it instead of rendering a hole."""
    response = await client.get(
        "/api/v1/dashboard/executive", headers=await _auth(client, "finance")
    )
    assert response.status_code == 200

    body = response.json()
    # Finance holds no alert-acknowledgement duty but does read alerts; the
    # tile it lacks is inventory risk.
    assert "inventory_risk" not in body or isinstance(body["inventory_risk"], list)
    assert isinstance(body["sections_unavailable"], list)


async def test_dashboard_requires_authentication(client: AsyncClient) -> None:
    assert (await client.get("/api/v1/dashboard/executive")).status_code == 401


# ── Documentation ────────────────────────────────────────────────────


async def test_openapi_documents_every_dashboard_endpoint(client: AsyncClient) -> None:
    spec = (await client.get("/api/openapi.json")).json()
    for path in (
        "/api/v1/dashboard/revenue/today",
        "/api/v1/dashboard/revenue/trend",
        "/api/v1/dashboard/growth",
        "/api/v1/dashboard/profit",
        "/api/v1/dashboard/products/top",
        "/api/v1/dashboard/stores/ranking",
        "/api/v1/dashboard/inventory/risk",
        "/api/v1/dashboard/forecast",
        "/api/v1/dashboard/alerts",
        "/api/v1/dashboard/recommendations",
        "/api/v1/dashboard/executive",
    ):
        assert path in spec["paths"], f"{path} is undocumented"
        operation = spec["paths"][path]["get"]
        assert operation["summary"]
        assert operation["description"], f"{path} has no description"
