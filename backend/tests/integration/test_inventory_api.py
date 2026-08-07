"""Inventory intelligence endpoints against a real warehouse.

Six weeks of sales, stock positions, and purchase orders through the actual
pipeline, then dbt. The suite asserts the *invariants* — OTIF cannot exceed
either of its halves, a reorder point must equal its own arithmetic, on-order
stock must be netted off, overstock and dead stock must be disjoint — rather
than that the endpoints return 200. A replenishment system that responds
quickly with quantities nobody can reproduce is worse than none.
"""

import pytest

pytest.importorskip("testcontainers", reason="integration extra not installed")
from httpx import AsyncClient  # noqa: E402

from tests.integration.conftest import auth_headers  # noqa: E402

pytestmark = pytest.mark.integration


AGING_BUCKETS = {"0-30", "31-60", "61-90", "91-180", "180+", "unknown"}


async def _get(api: AsyncClient, path: str, role: str = "inventory", **params: object) -> dict:
    response = await api.get(
        f"/api/v1/inventory{path}", headers=await auth_headers(api, role), params=params
    )
    assert response.status_code == 200, response.text
    return response.json()


# ── ABC classification ───────────────────────────────────────────────


async def test_abc_classes_partition_the_assortment(api: AsyncClient) -> None:
    body = await _get(api, "/abc")
    rows = body["data"]
    assert rows

    classes = {row["abc_class"] for row in rows}
    assert classes <= {"A", "B", "C"}
    assert "A" in classes, "a classification with no A items has not classified anything"


async def test_abc_shares_are_shares_of_the_result_not_the_table(api: AsyncClient) -> None:
    """Shares must add to one, or the denominator is the wrong population."""
    rows = (await _get(api, "/abc"))["data"]
    assert sum(row["revenue_share"] for row in rows) == pytest.approx(1.0, abs=1e-3)
    assert rows[-1]["cumulative_share"] == pytest.approx(1.0, abs=1e-3)


async def test_abc_cumulative_share_is_monotonic(api: AsyncClient) -> None:
    rows = (await _get(api, "/abc"))["data"]
    running = [row["cumulative_share"] for row in rows]
    assert running == sorted(running), "cumulative share must never decrease"


async def test_a_items_are_planned_to_a_higher_service_level(api: AsyncClient) -> None:
    """The classification is only worth having if it changes the plan."""
    by_class = {row["abc_class"]: row for row in (await _get(api, "/abc"))["data"]}
    if "A" in by_class and "C" in by_class:
        assert by_class["A"]["avg_service_level"] > by_class["C"]["avg_service_level"]


async def test_a_items_carry_most_of_the_revenue(api: AsyncClient) -> None:
    by_class = {row["abc_class"]: row for row in (await _get(api, "/abc"))["data"]}
    if "A" in by_class and "C" in by_class:
        assert by_class["A"]["revenue"] > by_class["C"]["revenue"]


# ── Stockout prediction ──────────────────────────────────────────────


async def test_stockout_queue_is_ordered_by_urgency(api: AsyncClient) -> None:
    rows = (await _get(api, "/stockout-risk", limit=30))["data"]
    assert rows

    horizons = [
        row["soonest_stockout_days"] for row in rows if row["soonest_stockout_days"] is not None
    ]
    assert horizons == sorted(horizons), "the most urgent position must come first"


async def test_cover_days_agree_with_stock_over_demand(api: AsyncClient) -> None:
    """Cover is recomputed at grain, not averaged — so it must reconcile."""
    rows = (await _get(api, "/stockout-risk", by="region"))["data"]
    for row in rows:
        if row["daily_demand"]:
            assert row["cover_days"] == pytest.approx(
                row["on_hand_units"] / row["daily_demand"], rel=1e-3
            )


async def test_at_risk_is_a_subset_of_positions(api: AsyncClient) -> None:
    body = await _get(api, "/stockout-risk", by="region")
    for row in body["data"]:
        assert row["at_risk_positions"] <= row["positions"]
        assert row["stockout_positions"] <= row["positions"]


# ── Overstock and aging ──────────────────────────────────────────────


async def test_overstock_and_dead_stock_are_disjoint(api: AsyncClient) -> None:
    """Both are held stock, but they are different decisions.

    Overstock has demand and will clear; dead stock has none and will not.
    A position counted in both would mean the definitions overlap and the
    recommended action is ambiguous.
    """
    for row in (await _get(api, "/overstock"))["data"]:
        assert row["overstocked_positions"] + row["dead_stock_positions"] <= row["positions"]


async def test_excess_value_accompanies_excess_units(api: AsyncClient) -> None:
    for row in (await _get(api, "/overstock"))["data"]:
        if row["excess_units"]:
            assert row["excess_value"] > 0, "excess units with no value means costing is broken"


async def test_aging_uses_the_warehouse_buckets(api: AsyncClient) -> None:
    body = await _get(api, "/aging")
    rows = body["data"]
    assert rows
    assert {row["aging_bucket"] for row in rows} <= AGING_BUCKETS


async def test_aging_value_shares_sum_to_one(api: AsyncClient) -> None:
    rows = (await _get(api, "/aging"))["data"]
    assert sum(row["value_share"] for row in rows) == pytest.approx(1.0, abs=1e-3)


# ── Lead time ────────────────────────────────────────────────────────


async def test_p90_lead_time_is_not_below_the_mean(api: AsyncClient) -> None:
    """If it were, the percentile is computed over the wrong population."""
    for row in (await _get(api, "/lead-time"))["data"]:
        assert row["p90_lead_time_days"] >= row["avg_lead_time_days"]


async def test_lead_time_cov_is_spread_over_mean(api: AsyncClient) -> None:
    for row in (await _get(api, "/lead-time"))["data"]:
        if row["avg_lead_time_days"]:
            assert row["worst_lead_time_cov"] == pytest.approx(
                row["worst_lead_time_stddev"] / row["avg_lead_time_days"], abs=0.02
            )


async def test_suppliers_differ_in_reliability(api: AsyncClient) -> None:
    """A scorecard where every vendor scores the same measures nothing."""
    rows = (await _get(api, "/lead-time"))["data"]
    assert len({row["avg_lead_time_days"] for row in rows}) > 1


# ── Supplier risk ────────────────────────────────────────────────────


async def test_otif_never_exceeds_either_half(api: AsyncClient) -> None:
    """OTIF is on-time AND in-full, so it is bounded by both.

    This is the invariant that catches a scorecard computing OTIF over a
    different denominator from its components — the failure that makes a
    supplier review argue about arithmetic instead of performance.
    """
    for row in (await _get(api, "/supplier-risk"))["data"]:
        assert row["otif_rate"] <= row["on_time_rate"] + 1e-9
        assert row["otif_rate"] <= row["in_full_rate"] + 1e-9


async def test_open_lines_are_excluded_from_the_denominator(api: AsyncClient) -> None:
    """An order still in transit has not failed to arrive on time."""
    for row in (await _get(api, "/supplier-risk"))["data"]:
        assert row["closed_lines"] + row["open_lines"] == row["po_lines"]
        assert row["closed_lines"] < row["po_lines"], (
            "no open lines means the fixture is degenerate"
        )


async def test_rates_are_bounded(api: AsyncClient) -> None:
    for row in (await _get(api, "/supplier-risk"))["data"]:
        for key in ("otif_rate", "on_time_rate", "in_full_rate", "fill_rate"):
            assert 0.0 <= row[key] <= 1.0, f"{key} out of range: {row[key]}"


async def test_evidence_floor_is_reported_not_hidden(api: AsyncClient) -> None:
    body = await _get(api, "/supplier-risk")
    assert body["evidence_floor"] == 20
    for row in body["data"]:
        assert row["meets_evidence_floor"] == (row["closed_lines"] >= body["evidence_floor"])


async def test_risk_band_reflects_otif(api: AsyncClient) -> None:
    """The band must be derivable from the numbers shown beside it."""
    for row in (await _get(api, "/supplier-risk"))["data"]:
        if row["meets_evidence_floor"] and row["risk_band"] == "low":
            assert row["otif_rate"] >= 0.93


# ── Reorder suggestions ──────────────────────────────────────────────


async def test_reorder_point_equals_its_own_arithmetic(api: AsyncClient) -> None:
    """Lead-time demand plus safety stock. A buyer must be able to check it."""
    for row in (await _get(api, "/reorder", limit=25))["data"]:
        expected = row["daily_demand"] * row["lead_time_days"] + row["safety_stock"]
        assert row["reorder_point"] == pytest.approx(expected, rel=0.02)


async def test_on_order_stock_is_netted_off(api: AsyncClient) -> None:
    """Double-ordering in-transit stock is how automated replenishment
    destroys a working-capital budget while every line looks reasonable."""
    for row in (await _get(api, "/reorder", limit=25))["data"]:
        expected = row["order_up_to_level"] - (row["on_hand_units"] + row["on_order_units"])
        assert row["suggested_order_qty"] == pytest.approx(max(expected, 0), abs=1.0)


async def test_in_transit_stock_is_counted_once(api: AsyncClient) -> None:
    """The position feed and the purchasing feed both report in-transit stock.

    Summing them under-orders: the mirror of double-ordering, and far harder
    to spot, because the symptom is a stockout weeks later rather than a
    suspicious invoice today. Every row must attribute its on-order figure to
    exactly one source.
    """
    for row in (await _get(api, "/reorder", due_only=False, limit=100))["data"]:
        assert row["on_order_source"] in {"purchase_orders", "position_feed"}


async def test_suggested_quantities_are_never_negative(api: AsyncClient) -> None:
    for row in (await _get(api, "/reorder", due_only=False, limit=100))["data"]:
        assert row["suggested_order_qty"] >= 0


async def test_safety_stock_rises_with_service_level(api: AsyncClient) -> None:
    """A items are planned to a higher service level, so they buffer more."""
    rows = (await _get(api, "/reorder", by="abc_class", due_only=False))["data"]
    by_class = {row["abc_class"]: row for row in rows}
    if "A" in by_class and "C" in by_class:
        a_per_position = by_class["A"]["safety_stock"] / by_class["A"]["positions"]
        c_per_position = by_class["C"]["safety_stock"] / by_class["C"]["positions"]
        assert a_per_position > c_per_position


async def test_due_only_filters_to_actionable_lines(api: AsyncClient) -> None:
    due = await _get(api, "/reorder", due_only=True, limit=200)
    every = await _get(api, "/reorder", due_only=False, limit=200)
    assert len(due["data"]) < len(every["data"])
    assert all(row["below_reorder_point"] > 0 for row in due["data"])


async def test_reorder_states_its_method(api: AsyncClient) -> None:
    """A recommendation whose derivation is unstated will be overridden."""
    assert "newsvendor" in (await _get(api, "/reorder"))["method"]


# ── Warehouse health ─────────────────────────────────────────────────


async def test_health_scores_are_bounded_and_componentised(api: AsyncClient) -> None:
    body = await _get(api, "/warehouse-health")
    assert body["data"]
    for row in body["data"]:
        assert 0 <= row["health_score"] <= 100
        for component in (
            "availability_score",
            "replenishment_score",
            "capital_efficiency_score",
            "assortment_score",
            "freshness_score",
        ):
            assert 0 <= row[component] <= 100, f"{component} out of range"


async def test_weakest_region_is_the_lowest_scoring_one(api: AsyncClient) -> None:
    body = await _get(api, "/warehouse-health")
    scores = [row["health_score"] for row in body["data"]]
    assert scores == sorted(scores), "worst-first ordering is the point of the surface"
    assert body["weakest"] == body["data"][0]["region"]


async def test_network_score_is_position_weighted(api: AsyncClient) -> None:
    """An unweighted mean lets one healthy outpost mask four struggling metros."""
    body = await _get(api, "/warehouse-health")
    rows = body["data"]
    weighted = sum(row["health_score"] * row["positions"] for row in rows) / sum(
        row["positions"] for row in rows
    )
    assert body["network_health_score"] == pytest.approx(weighted, abs=0.1)


async def test_stockout_rate_is_recomputed_from_counts(api: AsyncClient) -> None:
    for row in (await _get(api, "/warehouse-health"))["data"]:
        assert row["stockout_rate"] == pytest.approx(
            row["stockout_positions"] / row["positions"], rel=1e-3
        )


# ── Authorization ────────────────────────────────────────────────────


async def test_marketing_cannot_read_inventory(api: AsyncClient) -> None:
    """Module permissions are enforced per surface, not per role check."""
    for path in ("/abc", "/reorder", "/supplier-risk", "/warehouse-health"):
        response = await api.get(
            f"/api/v1/inventory{path}", headers=await auth_headers(api, "marketing")
        )
        assert response.status_code == 403, path
        assert response.headers["content-type"].startswith("application/problem+json")


async def test_anonymous_access_is_rejected(api: AsyncClient) -> None:
    response = await api.get("/api/v1/inventory/abc")
    assert response.status_code == 401


async def test_ceo_sees_everything(api: AsyncClient) -> None:
    assert (await _get(api, "/warehouse-health", role="ceo"))["data"]


# ── Governance ───────────────────────────────────────────────────────


async def test_unknown_grouping_is_refused_by_the_registry(api: AsyncClient) -> None:
    """The registry is the security boundary: unlisted names never reach SQL."""
    response = await api.get(
        "/api/v1/inventory/abc",
        headers=await auth_headers(api),
        params={"by": "sku; drop table dim_product"},
    )
    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/problem+json")


async def test_responses_carry_their_provenance(api: AsyncClient) -> None:
    meta = (await _get(api, "/warehouse-health"))["meta"]
    assert meta["data_snapshot_id"]
    assert meta["row_count"] is not None
