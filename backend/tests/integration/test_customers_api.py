"""Customer intelligence endpoints against a real warehouse.

Six weeks of history with a heterogeneous customer base, so segments, cohorts,
and lifecycle stages have genuine spread. The suite asserts the honesty
contracts — privacy suppression, bands rather than probabilities, cohorts
truncated at the observation edge — not merely that the endpoints respond.
"""

from pathlib import Path

import pytest

pytest.importorskip("testcontainers", reason="integration extra not installed")
from httpx import AsyncClient  # noqa: E402

from tests.integration.conftest import auth_headers  # noqa: E402

pytestmark = pytest.mark.integration


# ── Segmentation ─────────────────────────────────────────────────────


async def test_segments_span_the_named_rfm_map(api: AsyncClient) -> None:
    """A base that lands in one segment means the scoring is broken."""
    response = await api.get("/api/v1/customers/segments", headers=await auth_headers(api, "ceo"))
    assert response.status_code == 200

    body = response.json()
    segments = {row["rfm_segment"] for row in body["data"]}
    assert len(segments) >= 4, f"expected spread across segments, got {segments}"
    assert segments <= {
        "Champions",
        "Loyal",
        "New",
        "Promising",
        "At Risk",
        "Needs Attention",
        "Hibernating",
        "Potential",
    }


async def test_segment_values_are_consistent_with_their_averages(
    api: AsyncClient,
) -> None:
    body = (
        await api.get("/api/v1/customers/segments", headers=await auth_headers(api, "ceo"))
    ).json()
    for row in body["data"]:
        expected = row["segment_value"] / row["customers"]
        assert row["avg_lifetime_value"] == pytest.approx(expected, rel=1e-3)


async def test_rfm_grid_returns_cells_on_both_axes(api: AsyncClient) -> None:
    response = await api.get("/api/v1/customers/rfm", headers=await auth_headers(api, "ceo"))
    assert response.status_code == 200

    rows = response.json()["data"]
    assert rows
    assert all(1 <= row["recency_score"] <= 5 for row in rows)
    assert all(1 <= row["frequency_score"] <= 5 for row in rows)
    # A grid collapsed onto one axis is not a grid.
    assert len({row["recency_score"] for row in rows}) > 1
    assert len({row["frequency_score"] for row in rows}) > 1


# ── Lifetime value ───────────────────────────────────────────────────


async def test_lifetime_value_reports_historic_and_projected(
    api: AsyncClient,
) -> None:
    response = await api.get(
        "/api/v1/customers/lifetime-value", headers=await auth_headers(api, "ceo")
    )
    assert response.status_code == 200

    row = response.json()["data"][0]
    assert row["avg_lifetime_value"] > 0
    assert row["avg_predicted_clv_12m"] > 0
    assert 0 <= row["share_of_total_value"] <= 1


async def test_predicted_clv_carries_a_confidence_grade(estate_warehouse: Path) -> None:
    """Publishing a projection without its grade is how a two-week customer
    ends up in a five-year revenue plan.

    Read straight from the warehouse rather than through the API: the grade is
    a column the dbt model must produce, and an endpoint that happened not to
    select it would hide a missing one.
    """
    from ingestion.core.duck import connect  # noqa: PLC0415

    conn = connect(estate_warehouse, read_only=True)
    grades = {
        row[0]
        for row in conn.execute(
            "SELECT DISTINCT clv_confidence FROM analytics_analytics.dim_customer "
            "WHERE customer_key <> -1"
        ).fetchall()
    }
    conn.close()
    assert grades <= {"low", "medium", "high"}
    assert grades, "every customer must carry a CLV confidence grade"


# ── Retention ────────────────────────────────────────────────────────


async def test_retention_returns_cohorts_with_decaying_curves(
    api: AsyncClient,
) -> None:
    response = await api.get("/api/v1/customers/retention", headers=await auth_headers(api, "ceo"))
    assert response.status_code == 200

    body = response.json()
    assert body["cohorts"]
    assert body["data"]

    # Week zero is definitionally the whole cohort.
    week_zero = [row for row in body["data"] if row["weeks_since_acquisition"] == 0]
    assert week_zero
    assert all(row["retention_rate"] == 1.0 for row in week_zero)

    # And retention must actually fall away — a flat 100% curve means the
    # population is homogeneous and the analytics are degenerate.
    later = [row for row in body["data"] if row["weeks_since_acquisition"] >= 2]
    assert later
    assert min(row["retention_rate"] for row in later) < 0.95


async def test_cohorts_stop_at_the_observation_edge(api: AsyncClient) -> None:
    """A cohort acquired two weeks ago has no week-8 retention yet; emitting a
    zero there would draw a cliff that does not exist."""
    body = (
        await api.get("/api/v1/customers/retention", headers=await auth_headers(api, "ceo"))
    ).json()

    by_cohort: dict[str, int] = {}
    for row in body["data"]:
        cohort = str(row["cohort_week"])
        by_cohort[cohort] = max(by_cohort.get(cohort, 0), row["weeks_since_acquisition"])

    # The most recent cohort must have a shorter observable span than the oldest.
    oldest, newest = min(by_cohort), max(by_cohort)
    assert by_cohort[newest] < by_cohort[oldest]


async def test_cumulative_value_per_customer_is_monotonic(api: AsyncClient) -> None:
    """The payback curve accumulates; it can flatten but never fall."""
    body = (
        await api.get("/api/v1/customers/retention", headers=await auth_headers(api, "ceo"))
    ).json()

    first_cohort = body["cohorts"][0]
    curve = sorted(
        (row for row in body["data"] if str(row["cohort_week"]) == first_cohort),
        key=lambda r: r["weeks_since_acquisition"],
    )
    values = [row["cumulative_value_per_customer"] for row in curve]
    assert values == sorted(values)


# ── Repeat purchase and journey ──────────────────────────────────────


async def test_repeat_purchase_reports_cadence_per_stage(api: AsyncClient) -> None:
    response = await api.get(
        "/api/v1/customers/repeat-purchase", headers=await auth_headers(api, "ceo")
    )
    assert response.status_code == 200

    rows = response.json()["data"]
    assert rows
    cadences = [
        row["avg_days_between_orders"] for row in rows if row["avg_days_between_orders"] is not None
    ]
    assert cadences, "cadence is the denominator churn risk needs"
    assert all(value > 0 for value in cadences)


async def test_journey_funnel_narrows_through_the_stages(api: AsyncClient) -> None:
    """A funnel that widens is not a funnel."""
    response = await api.get("/api/v1/customers/journey", headers=await auth_headers(api, "ceo"))
    assert response.status_code == 200

    stages = sorted(response.json()["stages"], key=lambda s: s["stage_order"])
    assert [s["lifecycle_stage"] for s in stages] == ["New", "Repeat", "Established", "Loyal"]

    reach = [s["reached_stage"] for s in stages]
    assert reach == sorted(reach, reverse=True)


async def test_journey_conversion_rates_are_shares_not_counts(
    api: AsyncClient,
) -> None:
    stages = sorted(
        (await api.get("/api/v1/customers/journey", headers=await auth_headers(api, "ceo"))).json()[
            "stages"
        ],
        key=lambda s: s["stage_order"],
    )
    for stage in stages[1:]:
        assert 0 < stage["conversion_from_previous"] <= 1


async def test_lapsing_customers_stay_in_their_stage(api: AsyncClient) -> None:
    """A lapsing Loyal customer is still Loyal — collapsing risk into the stage
    label would hide exactly the customer worth saving."""
    stages = (
        await api.get("/api/v1/customers/journey", headers=await auth_headers(api, "ceo"))
    ).json()["stages"]

    loyal = next(s for s in stages if s["lifecycle_stage"] == "Loyal")
    assert loyal["at_risk_rate"] > 0, "some Loyal customers should be drifting"
    assert loyal["customers"] > 0


# ── Churn risk ───────────────────────────────────────────────────────


async def test_churn_risk_reports_bands_not_probabilities(api: AsyncClient) -> None:
    """Calling a heuristic ratio '68% likely to churn' implies a calibration
    nobody has measured."""
    response = await api.get("/api/v1/customers/churn-risk", headers=await auth_headers(api, "ceo"))
    assert response.status_code == 200

    body = response.json()
    bands = {row["churn_risk_band"] for row in body["bands"]}
    assert bands <= {"none", "low", "medium", "high", "unknown"}
    assert "probability" not in str(body).lower()


async def test_churn_risk_is_ranked_by_value_not_headcount(
    api: AsyncClient,
) -> None:
    """Sorting retention effort by number of customers is how a team spends a
    quarter saving people worth less than the campaign."""
    body = (
        await api.get("/api/v1/customers/churn-risk", headers=await auth_headers(api, "ceo"))
    ).json()
    values = [row["value_at_risk"] for row in body["bands"]]
    assert values == sorted(values, reverse=True)


async def test_the_headline_counts_only_customers_actually_at_risk(
    api: AsyncClient,
) -> None:
    """The earlier version of this test asserted the total equalled the sum of
    *every* band — which is how the bug survived.

    `value_at_risk` is the lifetime value held by the customers in a band, so
    summing all bands puts the `none` band into a figure labelled "value at
    risk". On the demo tenant that is 44% of the headline: a retention brief
    built on it would size the opportunity at nearly twice what exists.
    """
    body = (
        await api.get("/api/v1/customers/churn-risk", headers=await auth_headers(api, "ceo"))
    ).json()

    elevated = [row for row in body["bands"] if row["churn_risk_band"] in {"medium", "high"}]
    assert body["total_value_at_risk"] == pytest.approx(
        sum(row["value_at_risk"] for row in elevated), rel=1e-6
    )

    quiet = [row for row in body["bands"] if row["churn_risk_band"] in {"none", "low", "unknown"}]
    if quiet:
        assert body["total_value_at_risk"] < sum(row["value_at_risk"] for row in body["bands"])


async def test_the_headline_means_the_same_thing_under_every_grouping(
    api: AsyncClient,
) -> None:
    """`by` chooses how the rows are displayed. A headline that changed with it
    would be a number nobody could quote without also quoting a toggle."""
    headers = await auth_headers(api, "ceo")
    totals = set()
    for by in ("risk_band", "segment", "stage"):
        response = await api.get("/api/v1/customers/churn-risk", params={"by": by}, headers=headers)
        assert response.status_code == 200
        totals.add(round(response.json()["total_value_at_risk"], 2))

    assert len(totals) == 1


async def test_churn_risk_can_be_grouped_by_segment(api: AsyncClient) -> None:
    response = await api.get(
        "/api/v1/customers/churn-risk",
        params={"by": "segment"},
        headers=await auth_headers(api, "ceo"),
    )
    assert response.status_code == 200
    assert response.json()["grouped_by"] == "segment"
    assert "rfm_segment" in response.json()["bands"][0]


# ── VIP ──────────────────────────────────────────────────────────────


async def test_vip_reports_concentration_not_a_name_list(api: AsyncClient) -> None:
    """What a merchant needs from 'VIP' is the shape of the group, not names."""
    response = await api.get("/api/v1/customers/vip", headers=await auth_headers(api, "ceo"))
    assert response.status_code == 200

    body = response.json()
    assert body["data"]
    serialized = str(body)
    assert "customer_id" not in serialized
    assert "CU-" not in serialized


async def test_vips_are_a_minority_holding_disproportionate_value(
    api: AsyncClient,
) -> None:
    """Top decile by value *and* repeat — the concentration is the point."""
    body = (await api.get("/api/v1/customers/vip", headers=await auth_headers(api, "ceo"))).json()

    share_of_value = sum(row["share_of_total_value"] for row in body["data"])
    assert 0 < share_of_value < 1
    # A tenth of customers holding a tenth of value would mean VIP detection
    # found nothing worth acting on.
    assert share_of_value > 0.15


async def test_vip_rows_carry_their_risk_band(api: AsyncClient) -> None:
    """A VIP drifting into risk is the highest-value retention target there is."""
    body = (await api.get("/api/v1/customers/vip", headers=await auth_headers(api, "ceo"))).json()
    assert all("churn_risk_band" in row for row in body["data"])


# ── Privacy ──────────────────────────────────────────────────────────


async def test_every_response_declares_the_privacy_floor(api: AsyncClient) -> None:
    headers = await auth_headers(api, "ceo")
    for path in (
        "/api/v1/customers/segments",
        "/api/v1/customers/rfm",
        "/api/v1/customers/lifetime-value",
        "/api/v1/customers/retention",
        "/api/v1/customers/repeat-purchase",
        "/api/v1/customers/journey",
        "/api/v1/customers/churn-risk",
        "/api/v1/customers/vip",
    ):
        body = (await api.get(path, headers=headers)).json()
        assert body["privacy"]["floor"] == 20, path


async def test_groups_below_the_floor_are_withheld_and_counted(
    api: AsyncClient,
) -> None:
    """Suppression must be reported: a caller seeing six of eight segments
    should know two exist and why they are missing."""
    body = (await api.get("/api/v1/customers/rfm", headers=await auth_headers(api, "ceo"))).json()

    privacy = body["privacy"]
    assert privacy["suppressed_groups"] >= 0
    for row in body["data"]:
        assert row["customers"] >= 20
    if privacy["suppressed_groups"]:
        assert privacy["note"] and "floor" in privacy["note"]
        assert privacy["suppressed_customers"] > 0


async def test_no_endpoint_exposes_an_individual_customer(
    api: AsyncClient,
) -> None:
    """The structural guarantee: the product analyses cohorts, not people."""
    headers = await auth_headers(api, "ceo")
    for path in (
        "/api/v1/customers/segments",
        "/api/v1/customers/rfm",
        "/api/v1/customers/churn-risk",
        "/api/v1/customers/vip",
        "/api/v1/customers/journey",
    ):
        serialized = str((await api.get(path, headers=headers)).json())
        assert "customer_id" not in serialized, path
        assert "CU-0" not in serialized, path


# ── Authorization ────────────────────────────────────────────────────


async def test_customer_module_gates_every_surface(api: AsyncClient) -> None:
    """Finance reads money, not customers."""
    headers = await auth_headers(api, "finance")
    for path in (
        "/api/v1/customers/segments",
        "/api/v1/customers/churn-risk",
        "/api/v1/customers/vip",
    ):
        response = await api.get(path, headers=headers)
        assert response.status_code == 403, path
        assert "analytics.customer.read" in response.json()["hint"]


async def test_marketing_may_read_customer_intelligence(api: AsyncClient) -> None:
    response = await api.get(
        "/api/v1/customers/segments", headers=await auth_headers(api, "marketing")
    )
    assert response.status_code == 200


async def test_customer_endpoints_require_authentication(api: AsyncClient) -> None:
    assert (await api.get("/api/v1/customers/segments")).status_code == 401


# ── Documentation ────────────────────────────────────────────────────


async def test_openapi_documents_every_customer_endpoint(api: AsyncClient) -> None:
    spec = (await api.get("/api/openapi.json")).json()
    for path in (
        "/api/v1/customers/segments",
        "/api/v1/customers/rfm",
        "/api/v1/customers/lifetime-value",
        "/api/v1/customers/retention",
        "/api/v1/customers/repeat-purchase",
        "/api/v1/customers/journey",
        "/api/v1/customers/churn-risk",
        "/api/v1/customers/vip",
    ):
        assert path in spec["paths"], f"{path} is undocumented"
        operation = spec["paths"][path]["get"]
        assert operation["summary"]
        assert operation["description"], f"{path} has no description"
        assert "403" in operation["responses"]
