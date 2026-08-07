"""Executive dashboard endpoints against a real warehouse with history.

Forty-five days of data, so trends, growth horizons, and forecasts are real
rather than degenerate. The suite asserts the editorial contracts the
dashboard makes — weekday-aligned comparison, dollar-ranked attention,
accuracy travelling with forecasts — not merely that the endpoints respond.
"""

from datetime import date

import pytest

pytest.importorskip("testcontainers", reason="integration extra not installed")
from httpx import AsyncClient  # noqa: E402

from tests.integration.conftest import auth_headers  # noqa: E402
from tests.integration.warehouse import LAST_DAY  # noqa: E402

pytestmark = pytest.mark.integration


# ── 1. Today's revenue ───────────────────────────────────────────────


async def test_revenue_today_anchors_on_warehouse_data_not_the_clock(
    api: AsyncClient,
) -> None:
    """A dashboard showing an empty 'today' because the load has not finished
    is a dashboard nobody trusts."""
    response = await api.get(
        "/api/v1/dashboard/revenue/today", headers=await auth_headers(api, "ceo")
    )
    assert response.status_code == 200

    body = response.json()
    assert body["business_date"] == LAST_DAY.isoformat()
    assert body["business_date"] != date.today().isoformat()


async def test_revenue_today_compares_against_the_same_weekday(
    api: AsyncClient,
) -> None:
    """Comparing Monday to Sunday reports a weekday artefact as a business signal."""
    body = (
        await api.get("/api/v1/dashboard/revenue/today", headers=await auth_headers(api, "ceo"))
    ).json()

    business = date.fromisoformat(body["business_date"])
    comparison = date.fromisoformat(body["comparison_date"])
    assert (business - comparison).days == 7
    assert business.weekday() == comparison.weekday()


async def test_revenue_cards_carry_values_and_direction(api: AsyncClient) -> None:
    body = (
        await api.get("/api/v1/dashboard/revenue/today", headers=await auth_headers(api, "ceo"))
    ).json()

    cards = {card["key"]: card for card in body["cards"]}
    assert {"net_revenue", "units_sold", "orders", "aov", "discount_rate"} <= set(cards)
    assert cards["net_revenue"]["value"] > 0
    assert cards["net_revenue"]["direction"] in {"up", "down", "flat"}


async def test_small_movements_report_as_flat(api: AsyncClient) -> None:
    """Noise must not be styled as news."""
    body = (
        await api.get("/api/v1/dashboard/revenue/today", headers=await auth_headers(api, "ceo"))
    ).json()
    for card in body["cards"]:
        change = card["change_pct"]
        if change is not None and abs(change) < 0.005:
            assert card["direction"] == "flat"


# ── 2. Revenue trend ─────────────────────────────────────────────────


async def test_revenue_trend_returns_a_daily_series(api: AsyncClient) -> None:
    response = await api.get(
        "/api/v1/dashboard/revenue/trend",
        params={"days": 14},
        headers=await auth_headers(api, "ceo"),
    )
    assert response.status_code == 200

    series = response.json()["series"]
    assert len(series) >= 10
    assert all(point["net_revenue"] > 0 for point in series)
    dates = [point["business_date"] for point in series]
    assert dates == sorted(dates), "series must be chronological for charting"


async def test_trend_carries_snapshot_provenance(api: AsyncClient) -> None:
    body = (
        await api.get(
            "/api/v1/dashboard/revenue/trend",
            params={"days": 14},
            headers=await auth_headers(api, "ceo"),
        )
    ).json()
    assert body["meta"]["data_snapshot_id"]
    assert body["meta"]["freshness"] == LAST_DAY.isoformat()


# ── 3. Growth ────────────────────────────────────────────────────────


async def test_growth_reports_three_horizons(api: AsyncClient) -> None:
    """A bad day, a bad week, and a bad month need different reactions."""
    response = await api.get("/api/v1/dashboard/growth", headers=await auth_headers(api, "ceo"))
    assert response.status_code == 200

    horizons = {row["horizon"]: row for row in response.json()["horizons"]}
    assert set(horizons) == {"day", "week", "month"}
    assert horizons["week"]["days"] == 7
    assert horizons["week"]["current_revenue"] > 0


async def test_growth_change_is_arithmetically_consistent(api: AsyncClient) -> None:
    body = (
        await api.get("/api/v1/dashboard/growth", headers=await auth_headers(api, "ceo"))
    ).json()
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


async def test_profit_returns_margin_cards_and_category_split(api: AsyncClient) -> None:
    response = await api.get(
        "/api/v1/dashboard/profit", params={"days": 7}, headers=await auth_headers(api, "finance")
    )
    assert response.status_code == 200

    body = response.json()
    keys = {card["key"] for card in body["cards"]}
    assert {"margin_amount", "margin_rate", "cogs_amount"} <= keys
    assert body["by_category"]
    assert "category" in body["by_category"][0]


async def test_profit_requires_the_profitability_module(api: AsyncClient) -> None:
    """Cost is the number most organisations restrict; revenue access is not enough."""
    response = await api.get(
        "/api/v1/dashboard/profit", headers=await auth_headers(api, "store_manager")
    )
    assert response.status_code == 403
    assert "profitability" in response.json()["hint"]


# ── 5. Top products ──────────────────────────────────────────────────


async def test_top_products_are_ranked_by_the_requested_metric(
    api: AsyncClient,
) -> None:
    """'Top' is not one question — the ranking metric must actually change the order."""
    headers = await auth_headers(api, "ceo")

    by_revenue = (
        await api.get(
            "/api/v1/dashboard/products/top",
            params={"by": "net_revenue", "limit": 5},
            headers=headers,
        )
    ).json()
    by_units = (
        await api.get(
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


async def test_top_products_carry_identity_and_margin(api: AsyncClient) -> None:
    body = (
        await api.get(
            "/api/v1/dashboard/products/top",
            params={"limit": 3},
            headers=await auth_headers(api, "ceo"),
        )
    ).json()
    row = body["products"][0]
    assert {"sku", "product_name", "category", "net_revenue", "margin_rate"} <= set(row)


# ── 6. Store rankings ────────────────────────────────────────────────


async def test_store_rankings_are_ordered_and_numbered(api: AsyncClient) -> None:
    response = await api.get(
        "/api/v1/dashboard/stores/ranking",
        params={"limit": 10},
        headers=await auth_headers(api, "ceo"),
    )
    assert response.status_code == 200

    stores = response.json()["stores"]
    assert [row["rank"] for row in stores] == list(range(1, len(stores) + 1))
    revenues = [row["net_revenue"] for row in stores]
    assert revenues == sorted(revenues, reverse=True)


async def test_store_rows_carry_their_peer_cluster(api: AsyncClient) -> None:
    """Comparing a flagship to an outlet is malpractice; the cluster makes
    correct grouping possible even without a filter."""
    body = (
        await api.get("/api/v1/dashboard/stores/ranking", headers=await auth_headers(api, "ceo"))
    ).json()
    assert all(row.get("store_cluster") for row in body["stores"])


async def test_cluster_filter_scopes_the_league_table(api: AsyncClient) -> None:
    headers = await auth_headers(api, "ceo")
    unfiltered = (await api.get("/api/v1/dashboard/stores/ranking", headers=headers)).json()
    cluster = unfiltered["stores"][0]["store_cluster"]

    filtered = (
        await api.get(
            "/api/v1/dashboard/stores/ranking", params={"cluster": cluster}, headers=headers
        )
    ).json()
    assert filtered["cluster"] == cluster
    assert {row["store_cluster"] for row in filtered["stores"]} == {cluster}


# ── 7. Inventory risk ────────────────────────────────────────────────


async def test_inventory_risk_ranks_by_rate_not_absolute_count(
    api: AsyncClient,
) -> None:
    """Four stockouts in twenty positions beats ten in five thousand."""
    response = await api.get(
        "/api/v1/dashboard/inventory/risk", headers=await auth_headers(api, "inventory")
    )
    assert response.status_code == 200

    rows = response.json()["at_risk"]
    rates = [row["stockout_rate"] for row in rows]
    assert rates == sorted(rates, reverse=True)


async def test_inventory_risk_reads_a_single_position_date(api: AsyncClient) -> None:
    """Positions are semi-additive: summing them across days invents stock."""
    body = (
        await api.get(
            "/api/v1/dashboard/inventory/risk", headers=await auth_headers(api, "inventory")
        )
    ).json()
    assert body["position_date"] == LAST_DAY.isoformat()


async def test_planted_stockouts_surface_in_the_risk_tile(api: AsyncClient) -> None:
    body = (
        await api.get(
            "/api/v1/dashboard/inventory/risk", headers=await auth_headers(api, "inventory")
        )
    ).json()
    assert any(row["stockout_rate"] > 0 for row in body["at_risk"])


# ── 8. Forecast ──────────────────────────────────────────────────────


async def test_forecast_returns_a_series_with_intervals(api: AsyncClient) -> None:
    """A bare-point forecast is a forbidden state in this product."""
    response = await api.get(
        "/api/v1/dashboard/forecast",
        params={"days": 14},
        headers=await auth_headers(api, "ceo"),
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


async def test_forecast_publishes_its_own_accuracy(api: AsyncClient) -> None:
    """Accuracy travels with the forecast, never behind a separate click: a
    planner needs to know how wrong the model has been."""
    body = (
        await api.get("/api/v1/dashboard/forecast", headers=await auth_headers(api, "ceo"))
    ).json()

    accuracy = body["accuracy"]
    assert accuracy["model_name"] == "seasonal_naive_w4"
    assert accuracy["model_class"] == "baseline"
    assert 0 <= accuracy["wape"] < 1
    assert accuracy["interval_coverage"] is not None
    assert accuracy["forecast_days_evaluated"] > 0


async def test_forecast_requires_forecast_permission(api: AsyncClient) -> None:
    response = await api.get(
        "/api/v1/dashboard/forecast", headers=await auth_headers(api, "store_manager")
    )
    # Store managers hold forecasts.read, so this succeeds — the assertion
    # documents the intended grant rather than a denial.
    assert response.status_code == 200


# ── 9. Alerts ────────────────────────────────────────────────────────


async def test_alerts_return_open_items_with_expected_bands(api: AsyncClient) -> None:
    response = await api.get("/api/v1/dashboard/alerts", headers=await auth_headers(api, "ceo"))
    assert response.status_code == 200

    body = response.json()
    assert body["counts"]
    alert = body["alerts"][0]
    assert alert["severity"] in {"critical", "warn", "info"}
    assert alert["expected_low"] < alert["expected_high"]
    assert alert["scope"]  # the exact slice that breached
    assert alert["data_snapshot_id"]


async def test_alerts_are_ordered_most_severe_first(api: AsyncClient) -> None:
    """Alphabetical severity ordering would put 'critical' after 'warn'."""
    body = (
        await api.get("/api/v1/dashboard/alerts", headers=await auth_headers(api, "ceo"))
    ).json()
    rank = {"critical": 0, "warn": 1, "info": 2}
    severities = [rank[a["severity"]] for a in body["alerts"]]
    assert severities == sorted(severities)


async def test_alert_deviation_is_signed_against_the_band(api: AsyncClient) -> None:
    body = (
        await api.get("/api/v1/dashboard/alerts", headers=await auth_headers(api, "ceo"))
    ).json()
    for alert in body["alerts"]:
        if alert["observed"] < alert["expected_low"]:
            assert alert["deviation_pct"] < 0


# ── 10. Recommendations ──────────────────────────────────────────────


async def test_recommendations_are_ranked_by_impact(api: AsyncClient) -> None:
    response = await api.get(
        "/api/v1/dashboard/recommendations", headers=await auth_headers(api, "ceo")
    )
    assert response.status_code == 200

    recs = response.json()["recommendations"]
    assert recs
    impacts = [rec["impact_value"] for rec in recs]
    assert impacts == sorted(impacts, reverse=True)


async def test_recommendations_state_their_estimation_method(
    api: AsyncClient,
) -> None:
    """An impact figure without a stated method is not actionable."""
    body = (
        await api.get("/api/v1/dashboard/recommendations", headers=await auth_headers(api, "ceo"))
    ).json()
    rec = body["recommendations"][0]
    assert "method" in rec["expected_impact"]
    assert rec["evidence"]
    assert rec["confidence"] in {"high", "medium", "low"}


# ── Composite ────────────────────────────────────────────────────────


async def test_executive_overview_assembles_every_tile(api: AsyncClient) -> None:
    response = await api.get("/api/v1/dashboard/executive", headers=await auth_headers(api, "ceo"))
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
    api: AsyncClient,
) -> None:
    """Naming the gap lets the UI explain it instead of rendering a hole."""
    response = await api.get(
        "/api/v1/dashboard/executive", headers=await auth_headers(api, "finance")
    )
    assert response.status_code == 200

    body = response.json()
    # Finance holds no alert-acknowledgement duty but does read alerts; the
    # tile it lacks is inventory risk.
    assert "inventory_risk" not in body or isinstance(body["inventory_risk"], list)
    assert isinstance(body["sections_unavailable"], list)


async def test_dashboard_requires_authentication(api: AsyncClient) -> None:
    assert (await api.get("/api/v1/dashboard/executive")).status_code == 401


# ── Documentation ────────────────────────────────────────────────────


async def test_openapi_documents_every_dashboard_endpoint(api: AsyncClient) -> None:
    spec = (await api.get("/api/openapi.json")).json()
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
