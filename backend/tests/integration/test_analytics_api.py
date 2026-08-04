"""Analytics endpoints against a real warehouse.

Builds the full chain once — generate CSVs → ingest → dbt build — then drives
the HTTP API against it. That is the only way to prove the things that matter:
that the numbers reconcile, that module permissions actually gate access, and
that ratio metrics are recomputed at the grain the caller asked for.
"""

import os
import subprocess
from collections.abc import AsyncIterator, Iterator
from datetime import date
from pathlib import Path

import pytest

pytest.importorskip("testcontainers", reason="integration extra not installed")
from httpx import ASGITransport, AsyncClient  # noqa: E402

pytestmark = pytest.mark.integration

REPO = Path(__file__).resolve().parents[3]
DBT_DIR = REPO / "data_platform" / "dbt"
BUSINESS_DAY = date(2026, 7, 21)
DEMO_PASSWORD = "ChangeMe-Demo1!"  # noqa: S105 — seeded demo credential

# One user per role: the analytics matrix is verified against real sign-ins.
USERS = {
    "ceo": "priya@northwind.example",
    "marketing": "marcus@northwind.example",
    "inventory": "aisha@northwind.example",
    "finance": "yusuf@northwind.example",
    "store_manager": "lena@northwind.example",
    "admin": "sam@northwind.example",
}


@pytest.fixture(scope="module")
def analytics_warehouse(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    """Generate, ingest, and build a throwaway warehouse."""
    import sys

    sys.path.insert(0, str(REPO / "data_platform"))

    from ingestion.connectors.csv_files import CsvFileConnector
    from ingestion.core.config import EtlSettings
    from ingestion.core.duck import connect
    from ingestion.domain.schema import SourceSchema
    from ingestion.domain.window import Window
    from ingestion.generators import inventory_files, pos_files, purchase_orders
    from ingestion.pipeline import IngestionPipeline

    root = tmp_path_factory.mktemp("analytics_wh")
    settings = EtlSettings(
        landing_root=root / "lake",
        inbox_root=root / "inbox",
        warehouse_path=root / "wh.duckdb",
        reject_rate_threshold=0.10,
    )

    pos_files.generate_day(settings.inbox_dir("pos"), BUSINESS_DAY, stores=8, lines_per_store=30)
    inventory_files.generate_day(
        settings.inbox_dir("inventory"), BUSINESS_DAY, stores=8, skus_per_store=20
    )
    purchase_orders.generate_day(
        settings.inbox_dir("purchasing"), BUSINESS_DAY, stores=8, lines=40, as_of=BUSINESS_DAY
    )

    schema_root = REPO / "data_platform" / "ingestion" / "schemas"
    conn = connect(settings.warehouse_path)
    # Purchasing arrives as one file for the whole estate rather than one per
    # store, so its completeness check counts a single unit.
    for source, table, units in (
        ("pos", "sales", 8),
        ("inventory", "positions", 8),
        ("purchasing", "orders", 1),
    ):
        schema = SourceSchema.from_yaml(schema_root / source / f"{table}.yml")
        connector = CsvFileConnector(
            schema=schema, settings=settings, connection=conn, expected_units=units
        )
        summary = IngestionPipeline(connector=connector, settings=settings, connection=conn).run(
            Window.for_day(BUSINESS_DAY)
        )
        assert summary.status == "succeeded", f"{source} ingestion failed"
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
    migrated_db: dict[str, str], analytics_warehouse: Path
) -> AsyncIterator[AsyncClient]:
    """App wired to the throwaway warehouse; no Redis, so cache always misses."""
    os.environ["RM_WAREHOUSE_DUCKDB_PATH"] = str(analytics_warehouse)
    os.environ.pop("RM_REDIS_CACHE_URL", None)

    from app.main import create_app

    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http
    await app.state.engine.dispose()


async def _token(client: AsyncClient, role: str) -> str:
    response = await client.post(
        "/api/v1/auth/login", json={"email": USERS[role], "password": DEMO_PASSWORD}
    )
    assert response.status_code == 200, response.text
    return str(response.json()["access_token"])


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ── Catalog ──────────────────────────────────────────────────────────


async def test_catalog_lists_only_domains_the_caller_may_query(client: AsyncClient) -> None:
    """A catalogue advertising inaccessible modules produces dead ends in the UI."""
    ceo = await client.get("/api/v1/analytics/catalog", headers=_auth(await _token(client, "ceo")))
    marketing = await client.get(
        "/api/v1/analytics/catalog", headers=_auth(await _token(client, "marketing"))
    )

    ceo_domains = {entry["domain"] for entry in ceo.json()}
    marketing_domains = {entry["domain"] for entry in marketing.json()}

    assert {"revenue", "profitability", "inventory", "marketing"} <= ceo_domains
    assert "marketing" in marketing_domains
    assert "profitability" not in marketing_domains  # module separation


async def test_catalog_declares_additivity_for_every_metric(client: AsyncClient) -> None:
    response = await client.get(
        "/api/v1/analytics/catalog", headers=_auth(await _token(client, "ceo"))
    )
    for entry in response.json():
        for metric in entry["metrics"]:
            assert metric["additivity"] in {"full", "semi", "non"}


# ── Revenue ──────────────────────────────────────────────────────────


async def test_revenue_summary_returns_totals_with_provenance(client: AsyncClient) -> None:
    response = await client.get(
        "/api/v1/analytics/revenue/summary",
        params={"start_date": "2026-07-01", "end_date": "2026-07-31"},
        headers=_auth(await _token(client, "ceo")),
    )
    assert response.status_code == 200

    body = response.json()
    assert body["totals"]["net_revenue"] > 0
    assert body["totals"]["units_sold"] > 0
    # Provenance is structural: a number without it cannot be defended.
    assert body["meta"]["data_snapshot_id"]
    assert body["meta"]["freshness"] == "2026-07-21"
    assert body["meta"]["cache"] == "miss"


async def test_revenue_breakdown_by_category(client: AsyncClient) -> None:
    response = await client.get(
        "/api/v1/analytics/revenue/breakdown",
        params={"metrics": "net_revenue,units_sold", "dimensions": "category"},
        headers=_auth(await _token(client, "ceo")),
    )
    assert response.status_code == 200

    body = response.json()
    assert body["data"]
    assert {"category", "net_revenue", "units_sold"} <= set(body["data"][0])
    # Default ordering is by the first metric, descending.
    revenues = [row["net_revenue"] for row in body["data"]]
    assert revenues == sorted(revenues, reverse=True)


async def test_breakdown_totals_match_the_summary(client: AsyncClient) -> None:
    """Conservation across grains: a breakdown that does not sum to its own
    summary means one of them is lying."""
    token = _auth(await _token(client, "ceo"))
    params = {"start_date": "2026-07-01", "end_date": "2026-07-31"}

    summary = await client.get("/api/v1/analytics/revenue/summary", params=params, headers=token)
    breakdown = await client.get(
        "/api/v1/analytics/revenue/breakdown",
        params={**params, "metrics": "net_revenue", "dimensions": "category"},
        headers=token,
    )

    total = summary.json()["totals"]["net_revenue"]
    summed = sum(row["net_revenue"] for row in breakdown.json()["data"])
    assert summed == pytest.approx(total, rel=1e-6)


async def test_ratio_metrics_are_recomputed_at_the_requested_grain(
    client: AsyncClient,
) -> None:
    """AOV must equal revenue ÷ orders *for each row*, not an average of a
    finer grain's AOVs."""
    response = await client.get(
        "/api/v1/analytics/revenue/breakdown",
        params={"metrics": "net_revenue,orders,aov", "dimensions": "region"},
        headers=_auth(await _token(client, "ceo")),
    )
    for row in response.json()["data"]:
        assert row["aov"] == pytest.approx(row["net_revenue"] / row["orders"], rel=1e-6)


async def test_revenue_trend_returns_a_daily_series(client: AsyncClient) -> None:
    response = await client.get(
        "/api/v1/analytics/revenue/trend",
        params={"metrics": "net_revenue", "start_date": "2026-07-01", "end_date": "2026-07-31"},
        headers=_auth(await _token(client, "ceo")),
    )
    assert response.status_code == 200

    series = response.json()["series"]
    assert series
    assert series[0]["business_date"] == "2026-07-21"
    assert series[0]["values"]["net_revenue"] > 0


# ── Store, customer, inventory, marketing, profitability ─────────────


async def test_store_breakdown_carries_the_peer_cluster(client: AsyncClient) -> None:
    """Ranking is only meaningful within a cluster (Analytics §3)."""
    response = await client.get(
        "/api/v1/analytics/store/breakdown",
        params={"metrics": "net_revenue,aov", "dimensions": "store,store_cluster"},
        headers=_auth(await _token(client, "ceo")),
    )
    assert response.status_code == 200
    assert {"store_id", "store_cluster"} <= set(response.json()["data"][0])


async def test_customer_segments_are_returned(client: AsyncClient) -> None:
    response = await client.get(
        "/api/v1/analytics/customer/breakdown",
        params={"metrics": "customers,segment_value", "dimensions": "segment"},
        headers=_auth(await _token(client, "ceo")),
    )
    assert response.status_code == 200

    segments = {row["rfm_segment"] for row in response.json()["data"]}
    assert segments  # RFM produced named segments
    assert all(isinstance(name, str) for name in segments)


async def test_customer_domain_has_no_trend(client: AsyncClient) -> None:
    """Segments are point-in-time; a date axis would be fiction."""
    response = await client.get(
        "/api/v1/analytics/customer/trend",
        params={"metrics": "customers"},
        headers=_auth(await _token(client, "ceo")),
    )
    assert response.status_code == 404
    assert "time grain" in response.json()["detail"]


async def test_inventory_stockout_rate_reflects_planted_stockouts(
    client: AsyncClient,
) -> None:
    """The generator starves Outerwear in the low-numbered stores; the
    endpoint must see it."""
    response = await client.get(
        "/api/v1/analytics/inventory/breakdown",
        params={
            "metrics": "stockout_rate,on_hand_units,stockout_positions",
            "dimensions": "category",
            "sort_by": "stockout_rate",
        },
        headers=_auth(await _token(client, "inventory")),
    )
    assert response.status_code == 200

    rows = response.json()["data"]
    assert rows
    worst = rows[0]
    assert worst["category"] == "Outerwear"
    assert worst["stockout_rate"] > 0


async def test_marketing_breakdown_reports_subsidy_not_invented_lift(
    client: AsyncClient,
) -> None:
    """Honest lift needs a forecast counterfactual, so this endpoint reports
    only what was measured."""
    response = await client.get(
        "/api/v1/analytics/marketing/breakdown",
        params={"metrics": "promo_revenue,subsidy_amount,effective_depth", "dimensions": "promo"},
        headers=_auth(await _token(client, "marketing")),
    )
    assert response.status_code == 200

    body = response.json()
    assert body["data"]
    assert "lift" not in str(body).lower()
    assert body["data"][0]["subsidy_amount"] >= 0


async def test_profitability_margin_identity_holds(client: AsyncClient) -> None:
    response = await client.get(
        "/api/v1/analytics/profitability/breakdown",
        params={"metrics": "net_revenue,cogs_amount,margin_amount", "dimensions": "category"},
        headers=_auth(await _token(client, "finance")),
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
    client: AsyncClient, role: str, allowed: str, denied: str
) -> None:
    """Module visibility is what separates the functional roles."""
    token = _auth(await _token(client, role))

    ok = await client.get(f"/api/v1/analytics/{allowed}/summary", headers=token)
    assert ok.status_code == 200

    forbidden = await client.get(f"/api/v1/analytics/{denied}/summary", headers=token)
    assert forbidden.status_code == 403
    assert forbidden.json()["type"].endswith("/forbidden")
    assert denied in (forbidden.json().get("hint") or "")


async def test_analytics_requires_authentication(client: AsyncClient) -> None:
    response = await client.get("/api/v1/analytics/revenue/summary")
    assert response.status_code == 401


# ── Validation and errors ────────────────────────────────────────────


async def test_unknown_metric_returns_a_helpful_422(client: AsyncClient) -> None:
    response = await client.get(
        "/api/v1/analytics/revenue/breakdown",
        params={"metrics": "revenu", "dimensions": "category"},
        headers=_auth(await _token(client, "ceo")),
    )
    assert response.status_code == 422
    body = response.json()
    assert "unknown metric" in body["detail"]
    assert "net_revenue" in body["hint"]


async def test_injection_attempt_is_rejected_by_the_registry(client: AsyncClient) -> None:
    """Caller input is matched against the registry before compilation, so a
    payload never becomes SQL syntax."""
    response = await client.get(
        "/api/v1/analytics/revenue/breakdown",
        params={
            "metrics": "net_revenue); DROP TABLE fct_sales;--",
            "dimensions": "category",
        },
        headers=_auth(await _token(client, "ceo")),
    )
    assert response.status_code == 422

    # And the warehouse is intact.
    still_works = await client.get(
        "/api/v1/analytics/revenue/summary", headers=_auth(await _token(client, "ceo"))
    )
    assert still_works.status_code == 200


async def test_unknown_domain_returns_404(client: AsyncClient) -> None:
    response = await client.get(
        "/api/v1/analytics/suppliers/summary", headers=_auth(await _token(client, "ceo"))
    )
    assert response.status_code == 404


async def test_inverted_period_is_rejected(client: AsyncClient) -> None:
    response = await client.get(
        "/api/v1/analytics/revenue/summary",
        params={"start_date": "2026-07-31", "end_date": "2026-07-01"},
        headers=_auth(await _token(client, "ceo")),
    )
    assert response.status_code == 422


# ── Pagination ───────────────────────────────────────────────────────


async def test_pagination_returns_disjoint_pages(client: AsyncClient) -> None:
    token = _auth(await _token(client, "ceo"))
    params = {"metrics": "net_revenue", "dimensions": "category,region"}

    first = await client.get(
        "/api/v1/analytics/revenue/breakdown",
        params={**params, "limit": 3, "offset": 0},
        headers=token,
    )
    second = await client.get(
        "/api/v1/analytics/revenue/breakdown",
        params={**params, "limit": 3, "offset": 3},
        headers=token,
    )

    page_one = {(r["category"], r["region"]) for r in first.json()["data"]}
    page_two = {(r["category"], r["region"]) for r in second.json()["data"]}
    assert len(page_one) == 3
    assert not page_one & page_two, "pages must not repeat rows"


async def test_page_size_is_capped(client: AsyncClient) -> None:
    response = await client.get(
        "/api/v1/analytics/revenue/breakdown",
        params={"metrics": "net_revenue", "dimensions": "category", "limit": 10_000},
        headers=_auth(await _token(client, "ceo")),
    )
    assert response.status_code == 422  # above the documented ceiling


# ── Filters ──────────────────────────────────────────────────────────


async def test_filter_narrows_the_result(client: AsyncClient) -> None:
    token = _auth(await _token(client, "ceo"))

    unfiltered = await client.get("/api/v1/analytics/revenue/summary", headers=token)
    filtered = await client.get(
        "/api/v1/analytics/revenue/summary", params={"filter": "region:West"}, headers=token
    )

    assert filtered.status_code == 200
    assert filtered.json()["totals"]["net_revenue"] < unfiltered.json()["totals"]["net_revenue"]


async def test_filter_value_is_bound_not_interpolated(client: AsyncClient) -> None:
    """A quote in a filter value must be data, not syntax."""
    response = await client.get(
        "/api/v1/analytics/revenue/summary",
        params={"filter": "region:West' OR '1'='1"},
        headers=_auth(await _token(client, "ceo")),
    )
    assert response.status_code == 200
    # The literal matched nothing, which is exactly right.
    assert not response.json()["totals"].get("net_revenue")


# ── Documentation ────────────────────────────────────────────────────


async def test_openapi_documents_every_analytics_endpoint(client: AsyncClient) -> None:
    spec = (await client.get("/api/openapi.json")).json()
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
