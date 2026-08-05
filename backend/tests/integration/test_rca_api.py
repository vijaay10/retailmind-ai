"""Root cause analysis against a warehouse containing known incidents.

**This suite grades the engine against ground truth.** The synthetic estate
carries two deliberately planted incidents, declared once in
``ingestion.generators.shocks`` and expressed consistently across the POS,
weather, and carrier feeds:

* a severe-weather week in the **Northeast** that suppresses transactions, and
* a **West** carrier degrading, visible only in the shipping feed.

Testing an RCA engine any other way is close to worthless. On data with no
causal structure it will rank the largest region first, look plausible, and
nobody discovers it is a ranking of size until it is asked about a real
incident. So these tests assert recovery of a cause that was genuinely put
there — and, just as importantly, that the engine does *not* claim more than
its evidence supports about it.
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
HISTORY_DAYS = 63
DEMO_PASSWORD = "ChangeMe-Demo1!"  # noqa: S105 — seeded demo credential

USERS = {"ceo": "priya@northwind.example", "admin": "sam@northwind.example"}

#: The current window is the last week, which is where the weather incident
#: sits. The baseline is the three weeks before it.
CURRENT_START = LAST_DAY - timedelta(days=6)
BASELINE_END = CURRENT_START - timedelta(days=1)
BASELINE_START = BASELINE_END - timedelta(days=20)


@pytest.fixture(scope="module")
def rca_warehouse(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    """Build an estate containing the planted incidents."""
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

    root = tmp_path_factory.mktemp("rca_wh")
    settings = EtlSettings(
        landing_root=root / "lake",
        inbox_root=root / "inbox",
        warehouse_path=root / "wh.duckdb",
        reject_rate_threshold=0.10,
    )

    # Ten stores so every region has two: a region with one store cannot be
    # distinguished from that store, and the peer statistics need peers.
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
async def client(migrated_db: dict[str, str], rca_warehouse: Path) -> AsyncIterator[AsyncClient]:
    os.environ["RM_WAREHOUSE_DUCKDB_PATH"] = str(rca_warehouse)
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


async def _investigate(client: AsyncClient, role: str = "ceo", **overrides: object) -> dict:
    params: dict[str, object] = {
        "current_start": CURRENT_START.isoformat(),
        "current_end": LAST_DAY.isoformat(),
        "baseline_start": BASELINE_START.isoformat(),
        "baseline_end": BASELINE_END.isoformat(),
    }
    params.update(overrides)
    response = await client.get(
        "/api/v1/rca/investigate", headers=await _auth(client, role), params=params
    )
    assert response.status_code == 200, response.text
    return response.json()


def _subjects(findings: list[dict], dimension: str) -> list[str]:
    return [f["subject"] for f in findings if f["dimension"] == dimension]


# ── Ground truth recovery ────────────────────────────────────────────


async def test_the_drop_itself_is_detected(client: AsyncClient) -> None:
    body = await _investigate(client)
    assert body["relative_change"] is not None
    assert body["relative_change"] < 0, "the planted incidents should show a decline"


async def test_the_affected_region_is_identified(client: AsyncClient) -> None:
    """The headline assertion of the whole engine.

    A severe-weather week was planted in the Northeast. If the engine cannot
    name it, nothing else it reports is worth reading.
    """
    body = await _investigate(client)
    assert "Northeast" in _subjects(body["where"], "region")


async def test_the_affected_region_leads_its_own_dimension(client: AsyncClient) -> None:
    body = await _investigate(client)
    regions = _subjects(body["where"], "region")
    assert regions and regions[0] == "Northeast", f"expected Northeast first, got {regions}"


async def test_regions_that_held_up_are_not_reported_as_causes(client: AsyncClient) -> None:
    """The inverted-filter regression, pinned.

    Excess share is already normalised by the total change, so a slice falling
    during a decline is *positive*. An earlier version multiplied by the sign
    of the change as well, which flipped the test and ranked precisely the
    regions that had grown — reporting the healthy parts of the estate as the
    cause of its decline.
    """
    body = await _investigate(client)
    for finding in body["where"]:
        assert finding["impact_share"] > 0, (
            f"{finding['subject']} moved against the decline and is not a cause of it"
        )


async def test_the_weather_incident_is_surfaced(client: AsyncClient) -> None:
    body = await _investigate(client)
    assert "Northeast" in _subjects(body["why"], "weather")


async def test_the_carrier_incident_is_surfaced(client: AsyncClient) -> None:
    """The harder case: the symptom is in sales and the cause is in shipping."""
    body = await _investigate(client)
    shipping = [f for f in body["why"] if f["dimension"] == "shipping"]
    assert shipping, "the planted carrier degradation was not detected"


# ── The engine does not overclaim ────────────────────────────────────


async def test_weather_never_exceeds_its_evidence_ceiling(client: AsyncClient) -> None:
    """Even a perfectly correlated storm stays an association.

    This is the design's load-bearing rule. Weather is the most predictive
    signal in the estate and the least actionable, and an engine that lets
    correlation strength promote it into a mechanism will send an operator to
    fix the sky.
    """
    body = await _investigate(client)
    for finding in body["why"]:
        if finding["dimension"] == "weather":
            assert finding["evidence_tier"] == "associative"
            assert finding["confidence"] <= 0.45
            assert finding["claim_type"] == "coincides_with"


async def test_no_finding_exceeds_its_own_ceiling(client: AsyncClient) -> None:
    body = await _investigate(client)
    for finding in body["findings"]:
        assert finding["confidence"] <= finding["confidence_ceiling"]


async def test_arithmetic_findings_claim_contribution_not_causation(
    client: AsyncClient,
) -> None:
    body = await _investigate(client)
    for finding in body["where"]:
        assert finding["claim_type"] == "accounts_for"
        assert finding["does_not_establish"]


async def test_every_finding_states_what_it_cannot_establish(client: AsyncClient) -> None:
    body = await _investigate(client)
    assert body["findings"]
    for finding in body["findings"]:
        assert finding["does_not_establish"], f"{finding['subject']} claims without limit"


async def test_the_response_admits_it_is_observational(client: AsyncClient) -> None:
    body = await _investigate(client)
    assert any("observational" in caveat.lower() for caveat in body["caveats"])


async def test_segment_findings_carry_the_as_of_caveat(client: AsyncClient) -> None:
    """RFM segments are assigned now and applied to the past."""
    body = await _investigate(client)
    if any(f["dimension"] == "segment" for f in body["where"]):
        assert any("as of today" in caveat for caveat in body["caveats"])


# ── Structure ────────────────────────────────────────────────────────


async def test_where_and_why_are_kept_apart(client: AsyncClient) -> None:
    """A subtraction and a correlation must not read as the same statement."""
    body = await _investigate(client)
    assert all(f["evidence_tier"] == "arithmetic" for f in body["where"])
    assert all(f["evidence_tier"] != "arithmetic" for f in body["why"])
    assert len(body["where"]) + len(body["why"]) == len(body["findings"])


async def test_explained_share_does_not_sum_across_cuts(client: AsyncClient) -> None:
    """Region and segment describe the same pounds; adding them counts twice.

    An earlier version summed every arithmetic finding and reported 298%
    explained, with every individual number correct.
    """
    body = await _investigate(client)
    naive_total = sum(abs(f["impact_share"]) for f in body["where"])
    assert body["explained_share"] <= naive_total + 1e-6
    if len({f["dimension"] for f in body["where"]}) > 1:
        assert body["explained_share"] < naive_total


async def test_no_single_dimension_fills_the_briefing(client: AsyncClient) -> None:
    """Five variations on one cut is one finding restated five times."""
    body = await _investigate(client)
    counts: dict[str, int] = {}
    for finding in body["findings"]:
        counts[finding["dimension"]] = counts.get(finding["dimension"], 0) + 1
    assert max(counts.values()) <= 2


async def test_findings_are_ranked_by_stake_weighted_confidence(client: AsyncClient) -> None:
    body = await _investigate(client)
    scores = [f["confidence"] * abs(f["impact_share"]) for f in body["findings"]]
    assert scores == sorted(scores, reverse=True)


async def test_every_finding_carries_checkable_evidence(client: AsyncClient) -> None:
    body = await _investigate(client)
    for finding in body["findings"]:
        assert finding["evidence"], f"{finding['subject']} asserts without evidence"
        for item in finding["evidence"]:
            assert item["label"]


async def test_recommendations_state_their_assumptions(client: AsyncClient) -> None:
    """A recommendation whose assumption is false is worse than none."""
    body = await _investigate(client)
    for finding in body["findings"]:
        for recommendation in finding["recommendations"]:
            assert recommendation["assumes"], f"{recommendation['action']} assumes nothing?"
            assert recommendation["rationale"]


async def test_revenue_changes_are_split_into_volume_and_rate(client: AsyncClient) -> None:
    """Fewer baskets and smaller baskets are different problems."""
    body = await _investigate(client)
    labels = {item["label"] for f in body["where"] for item in f["evidence"]}
    assert "Volume effect" in labels
    assert "Rate effect" in labels


async def test_the_planted_weather_drop_reads_as_a_volume_effect(client: AsyncClient) -> None:
    """The incident suppresses transactions, not basket value.

    A storm keeps people at home; it does not make the ones who came spend
    less. If the engine reported this as a rate effect it would send a
    merchant to review pricing during a snowstorm.
    """
    body = await _investigate(client)
    northeast = next(
        (f for f in body["where"] if f["dimension"] == "region" and f["subject"] == "Northeast"),
        None,
    )
    assert northeast is not None
    assert "volume" in northeast["headline"]


# ── Restraint ────────────────────────────────────────────────────────


async def test_a_flat_period_produces_no_causes(client: AsyncClient) -> None:
    """An engine that always finds three causes finds three causes for noise.

    Comparing a quiet week against the week before it should return an empty
    briefing and say why, rather than manufacturing explanations for ordinary
    variation.
    """
    quiet_end = BASELINE_END
    quiet_start = quiet_end - timedelta(days=6)
    body = await _investigate(
        client,
        current_start=quiet_start.isoformat(),
        current_end=quiet_end.isoformat(),
        baseline_start=(quiet_start - timedelta(days=7)).isoformat(),
        baseline_end=(quiet_start - timedelta(days=1)).isoformat(),
    )
    if abs(body["relative_change"]) < 0.02:
        assert body["findings"] == []
        assert any("floor for investigation" in caveat for caveat in body["caveats"])


async def test_overlapping_windows_are_refused(client: AsyncClient) -> None:
    """Shared days mute the very change being investigated."""
    response = await client.get(
        "/api/v1/rca/investigate",
        headers=await _auth(client),
        params={
            "current_start": CURRENT_START.isoformat(),
            "current_end": LAST_DAY.isoformat(),
            "baseline_start": BASELINE_START.isoformat(),
            "baseline_end": LAST_DAY.isoformat(),
        },
    )
    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/problem+json")


async def test_unavailable_dimensions_are_named_not_omitted(client: AsyncClient) -> None:
    """A silently missing dimension looks identical to one that found nothing."""
    body = await _investigate(client, dimensions=["weather"])
    assert body["dimensions_investigated"] == ["weather"]


# ── Authorization ────────────────────────────────────────────────────


async def test_a_role_without_rca_access_is_refused(client: AsyncClient) -> None:
    response = await client.get("/api/v1/rca/investigate", headers=await _auth(client, "admin"))
    assert response.status_code == 403


async def test_anonymous_access_is_rejected(client: AsyncClient) -> None:
    response = await client.get("/api/v1/rca/investigate")
    assert response.status_code == 401
