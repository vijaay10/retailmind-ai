"""Analytics endpoints against a real warehouse.

Builds the full chain once — generate CSVs → ingest → dbt build — then drives
the HTTP API against it. That is the only way to prove the things that matter:
that the numbers reconcile, that module permissions actually gate access, and
that ratio metrics are recomputed at the grain the caller asked for.
"""

import pytest

pytest.importorskip("testcontainers", reason="integration extra not installed")
from httpx import AsyncClient  # noqa: E402

from tests.integration.warehouse import DEMO_PASSWORD, LAST_DAY, USERS  # noqa: E402

pytestmark = pytest.mark.integration

BUSINESS_DAY = LAST_DAY

# One user per role: the analytics matrix is verified against real sign-ins.


async def _token(api: AsyncClient, role: str) -> str:
    response = await api.post(
        "/api/v1/auth/login", json={"email": USERS[role], "password": DEMO_PASSWORD}
    )
    assert response.status_code == 200, response.text
    return str(response.json()["access_token"])


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ── Catalog ──────────────────────────────────────────────────────────


async def test_catalog_lists_only_domains_the_caller_may_query(api: AsyncClient) -> None:
    """A catalogue advertising inaccessible modules produces dead ends in the UI."""
    ceo = await api.get("/api/v1/analytics/catalog", headers=_auth(await _token(api, "ceo")))
    marketing = await api.get(
        "/api/v1/analytics/catalog", headers=_auth(await _token(api, "marketing"))
    )

    ceo_domains = {entry["domain"] for entry in ceo.json()}
    marketing_domains = {entry["domain"] for entry in marketing.json()}

    assert {"revenue", "profitability", "inventory", "marketing"} <= ceo_domains
    assert "marketing" in marketing_domains
    assert "profitability" not in marketing_domains  # module separation


async def test_catalog_declares_additivity_for_every_metric(api: AsyncClient) -> None:
    response = await api.get("/api/v1/analytics/catalog", headers=_auth(await _token(api, "ceo")))
    for entry in response.json():
        for metric in entry["metrics"]:
            assert metric["additivity"] in {"full", "semi", "non"}


# ── Revenue ──────────────────────────────────────────────────────────


async def test_revenue_summary_returns_totals_with_provenance(api: AsyncClient) -> None:
    response = await api.get(
        "/api/v1/analytics/revenue/summary",
        params={"start_date": "2026-07-01", "end_date": "2026-07-31"},
        headers=_auth(await _token(api, "ceo")),
    )
    assert response.status_code == 200

    body = response.json()
    assert body["totals"]["net_revenue"] > 0
    assert body["totals"]["units_sold"] > 0
    # Provenance is structural: a number without it cannot be defended.
    assert body["meta"]["data_snapshot_id"]
    assert body["meta"]["freshness"] == "2026-07-21"
    assert body["meta"]["cache"] == "miss"


async def test_revenue_breakdown_by_category(api: AsyncClient) -> None:
    response = await api.get(
        "/api/v1/analytics/revenue/breakdown",
        params={"metrics": "net_revenue,units_sold", "dimensions": "category"},
        headers=_auth(await _token(api, "ceo")),
    )
    assert response.status_code == 200

    body = response.json()
    assert body["data"]
    assert {"category", "net_revenue", "units_sold"} <= set(body["data"][0])
    # Default ordering is by the first metric, descending.
    revenues = [row["net_revenue"] for row in body["data"]]
    assert revenues == sorted(revenues, reverse=True)


async def test_breakdown_totals_match_the_summary(api: AsyncClient) -> None:
    """Conservation across grains: a breakdown that does not sum to its own
    summary means one of them is lying."""
    token = _auth(await _token(api, "ceo"))
    params = {"start_date": "2026-07-01", "end_date": "2026-07-31"}

    summary = await api.get("/api/v1/analytics/revenue/summary", params=params, headers=token)
    breakdown = await api.get(
        "/api/v1/analytics/revenue/breakdown",
        params={**params, "metrics": "net_revenue", "dimensions": "category"},
        headers=token,
    )

    total = summary.json()["totals"]["net_revenue"]
    summed = sum(row["net_revenue"] for row in breakdown.json()["data"])
    assert summed == pytest.approx(total, rel=1e-6)


async def test_ratio_metrics_are_recomputed_at_the_requested_grain(
    api: AsyncClient,
) -> None:
    """AOV must equal revenue ÷ orders *for each row*, not an average of a
    finer grain's AOVs."""
    response = await api.get(
        "/api/v1/analytics/revenue/breakdown",
        params={"metrics": "net_revenue,orders,aov", "dimensions": "region"},
        headers=_auth(await _token(api, "ceo")),
    )
    for row in response.json()["data"]:
        assert row["aov"] == pytest.approx(row["net_revenue"] / row["orders"], rel=1e-6)


async def test_revenue_trend_returns_a_daily_series(api: AsyncClient) -> None:
    response = await api.get(
        "/api/v1/analytics/revenue/trend",
        params={"metrics": "net_revenue", "start_date": "2026-07-01", "end_date": "2026-07-31"},
        headers=_auth(await _token(api, "ceo")),
    )
    assert response.status_code == 200

    series = response.json()["series"]
    assert series
    # Assert the shape, not a hard-coded first date: that expectation only
    # held while the fixture generated exactly one day, and a test that
    # encodes its fixture's size stops testing the endpoint.
    dates = [row["business_date"] for row in series]
    assert dates == sorted(dates), "a daily series must come back in order"
    assert len(set(dates)) == len(dates), "one row per day"
    assert BUSINESS_DAY.isoformat() in dates
    assert series[0]["values"]["net_revenue"] > 0


# ── Store, customer, inventory, marketing, profitability ─────────────


async def test_store_breakdown_carries_the_peer_cluster(api: AsyncClient) -> None:
    """Ranking is only meaningful within a cluster (Analytics §3)."""
    response = await api.get(
        "/api/v1/analytics/store/breakdown",
        params={"metrics": "net_revenue,aov", "dimensions": "store,store_cluster"},
        headers=_auth(await _token(api, "ceo")),
    )
    assert response.status_code == 200
    assert {"store_id", "store_cluster"} <= set(response.json()["data"][0])


async def test_customer_segments_are_returned(api: AsyncClient) -> None:
    response = await api.get(
        "/api/v1/analytics/customer/breakdown",
        params={"metrics": "customers,segment_value", "dimensions": "segment"},
        headers=_auth(await _token(api, "ceo")),
    )
    assert response.status_code == 200

    segments = {row["rfm_segment"] for row in response.json()["data"]}
    assert segments  # RFM produced named segments
    assert all(isinstance(name, str) for name in segments)


async def test_customer_domain_has_no_trend(api: AsyncClient) -> None:
    """Segments are point-in-time; a date axis would be fiction."""
    response = await api.get(
        "/api/v1/analytics/customer/trend",
        params={"metrics": "customers"},
        headers=_auth(await _token(api, "ceo")),
    )
    assert response.status_code == 404
    assert "time grain" in response.json()["detail"]


async def test_inventory_stockout_rate_reflects_planted_stockouts(
    api: AsyncClient,
) -> None:
    """The generator starves Outerwear in the low-numbered stores; the
    endpoint must see it."""
    response = await api.get(
        "/api/v1/analytics/inventory/breakdown",
        params={
            "metrics": "stockout_rate,on_hand_units,stockout_positions",
            "dimensions": "category",
            "sort_by": "stockout_rate",
        },
        headers=_auth(await _token(api, "inventory")),
    )
    assert response.status_code == 200

    rows = response.json()["data"]
    assert rows
    worst = rows[0]
    assert worst["category"] == "Outerwear"
    assert worst["stockout_rate"] > 0


async def test_marketing_breakdown_reports_subsidy_not_invented_lift(
    api: AsyncClient,
) -> None:
    """Honest lift needs a forecast counterfactual, so this endpoint reports
    only what was measured."""
    response = await api.get(
        "/api/v1/analytics/marketing/breakdown",
        params={"metrics": "promo_revenue,subsidy_amount,effective_depth", "dimensions": "promo"},
        headers=_auth(await _token(api, "marketing")),
    )
    assert response.status_code == 200

    body = response.json()
    assert body["data"]
    assert "lift" not in str(body).lower()
    assert body["data"][0]["subsidy_amount"] >= 0


async def test_profitability_margin_identity_holds(api: AsyncClient) -> None:
    response = await api.get(
        "/api/v1/analytics/profitability/breakdown",
        params={"metrics": "net_revenue,cogs_amount,margin_amount", "dimensions": "category"},
        headers=_auth(await _token(api, "finance")),
    )
    for row in response.json()["data"]:
        assert row["margin_amount"] == pytest.approx(
            row["net_revenue"] - row["cogs_amount"], rel=1e-6
        )


# ── Authorization: the module matrix ─────────────────────────────────


@pytest.mark.parametrize(
    ("role", "allowed", "denied"),
    [
        ("marketing", "marketing", "profitability"),
        ("finance", "profitability", "marketing"),
        ("inventory", "inventory", "marketing"),
        ("store_manager", "store", "profitability"),
        ("admin", "revenue", "customer"),
    ],
)
async def test_module_permissions_gate_each_domain(
    api: AsyncClient, role: str, allowed: str, denied: str
) -> None:
    """Module visibility is what separates the functional roles."""
    token = _auth(await _token(api, role))

    ok = await api.get(f"/api/v1/analytics/{allowed}/summary", headers=token)
    assert ok.status_code == 200

    forbidden = await api.get(f"/api/v1/analytics/{denied}/summary", headers=token)
    assert forbidden.status_code == 403
    assert forbidden.json()["type"].endswith("/forbidden")
    assert denied in (forbidden.json().get("hint") or "")


async def test_analytics_requires_authentication(api: AsyncClient) -> None:
    response = await api.get("/api/v1/analytics/revenue/summary")
    assert response.status_code == 401


# ── Validation and errors ────────────────────────────────────────────


async def test_unknown_metric_returns_a_helpful_422(api: AsyncClient) -> None:
    response = await api.get(
        "/api/v1/analytics/revenue/breakdown",
        params={"metrics": "revenu", "dimensions": "category"},
        headers=_auth(await _token(api, "ceo")),
    )
    assert response.status_code == 422
    body = response.json()
    assert "unknown metric" in body["detail"]
    assert "net_revenue" in body["hint"]


async def test_injection_attempt_is_rejected_by_the_registry(api: AsyncClient) -> None:
    """Caller input is matched against the registry before compilation, so a
    payload never becomes SQL syntax."""
    response = await api.get(
        "/api/v1/analytics/revenue/breakdown",
        params={
            "metrics": "net_revenue); DROP TABLE fct_sales;--",
            "dimensions": "category",
        },
        headers=_auth(await _token(api, "ceo")),
    )
    assert response.status_code == 422

    # And the warehouse is intact.
    still_works = await api.get(
        "/api/v1/analytics/revenue/summary", headers=_auth(await _token(api, "ceo"))
    )
    assert still_works.status_code == 200


async def test_unknown_domain_returns_404(api: AsyncClient) -> None:
    response = await api.get(
        "/api/v1/analytics/suppliers/summary", headers=_auth(await _token(api, "ceo"))
    )
    assert response.status_code == 404


async def test_inverted_period_is_rejected(api: AsyncClient) -> None:
    response = await api.get(
        "/api/v1/analytics/revenue/summary",
        params={"start_date": "2026-07-31", "end_date": "2026-07-01"},
        headers=_auth(await _token(api, "ceo")),
    )
    assert response.status_code == 422


# ── Pagination ───────────────────────────────────────────────────────


async def test_pagination_returns_disjoint_pages(api: AsyncClient) -> None:
    token = _auth(await _token(api, "ceo"))
    params = {"metrics": "net_revenue", "dimensions": "category,region"}

    first = await api.get(
        "/api/v1/analytics/revenue/breakdown",
        params={**params, "limit": 3, "offset": 0},
        headers=token,
    )
    second = await api.get(
        "/api/v1/analytics/revenue/breakdown",
        params={**params, "limit": 3, "offset": 3},
        headers=token,
    )

    page_one = {(r["category"], r["region"]) for r in first.json()["data"]}
    page_two = {(r["category"], r["region"]) for r in second.json()["data"]}
    assert len(page_one) == 3
    assert not page_one & page_two, "pages must not repeat rows"


async def test_page_size_is_capped(api: AsyncClient) -> None:
    response = await api.get(
        "/api/v1/analytics/revenue/breakdown",
        params={"metrics": "net_revenue", "dimensions": "category", "limit": 10_000},
        headers=_auth(await _token(api, "ceo")),
    )
    assert response.status_code == 422  # above the documented ceiling


# ── Filters ──────────────────────────────────────────────────────────


async def test_filter_narrows_the_result(api: AsyncClient) -> None:
    token = _auth(await _token(api, "ceo"))

    unfiltered = await api.get("/api/v1/analytics/revenue/summary", headers=token)
    filtered = await api.get(
        "/api/v1/analytics/revenue/summary", params={"filter": "region:West"}, headers=token
    )

    assert filtered.status_code == 200
    assert filtered.json()["totals"]["net_revenue"] < unfiltered.json()["totals"]["net_revenue"]


async def test_filter_value_is_bound_not_interpolated(api: AsyncClient) -> None:
    """A quote in a filter value must be data, not syntax."""
    response = await api.get(
        "/api/v1/analytics/revenue/summary",
        params={"filter": "region:West' OR '1'='1"},
        headers=_auth(await _token(api, "ceo")),
    )
    assert response.status_code == 200
    # The literal matched nothing, which is exactly right.
    assert not response.json()["totals"].get("net_revenue")


# ── Documentation ────────────────────────────────────────────────────


async def test_openapi_documents_every_analytics_endpoint(api: AsyncClient) -> None:
    spec = (await api.get("/api/openapi.json")).json()
    for path in (
        "/api/v1/analytics/catalog",
        "/api/v1/analytics/{domain}/summary",
        "/api/v1/analytics/{domain}/breakdown",
        "/api/v1/analytics/{domain}/trend",
    ):
        assert path in spec["paths"]

    breakdown = spec["paths"]["/api/v1/analytics/{domain}/breakdown"]["get"]
    assert "403" in breakdown["responses"]
    assert "422" in breakdown["responses"]
    assert breakdown["description"]
