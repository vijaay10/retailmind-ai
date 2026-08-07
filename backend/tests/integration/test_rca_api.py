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

from datetime import timedelta

import pytest

pytest.importorskip("testcontainers", reason="integration extra not installed")
from httpx import AsyncClient  # noqa: E402

from tests.integration.conftest import auth_headers  # noqa: E402
from tests.integration.warehouse import LAST_DAY  # noqa: E402

pytestmark = pytest.mark.integration


#: The current window is the last week, which is where the weather incident
#: sits. The baseline is the three weeks before it.
CURRENT_START = LAST_DAY - timedelta(days=6)
BASELINE_END = CURRENT_START - timedelta(days=1)
BASELINE_START = BASELINE_END - timedelta(days=20)


async def _investigate(api: AsyncClient, role: str = "ceo", **overrides: object) -> dict:
    params: dict[str, object] = {
        "current_start": CURRENT_START.isoformat(),
        "current_end": LAST_DAY.isoformat(),
        "baseline_start": BASELINE_START.isoformat(),
        "baseline_end": BASELINE_END.isoformat(),
    }
    params.update(overrides)
    response = await api.get(
        "/api/v1/rca/investigate", headers=await auth_headers(api, role), params=params
    )
    assert response.status_code == 200, response.text
    return response.json()


def _subjects(findings: list[dict], dimension: str) -> list[str]:
    return [f["subject"] for f in findings if f["dimension"] == dimension]


# ── Ground truth recovery ────────────────────────────────────────────


async def test_the_drop_itself_is_detected(api: AsyncClient) -> None:
    body = await _investigate(api)
    assert body["relative_change"] is not None
    assert body["relative_change"] < 0, "the planted incidents should show a decline"


async def test_the_affected_region_is_identified(api: AsyncClient) -> None:
    """The headline assertion of the whole engine.

    A severe-weather week was planted in the Northeast. If the engine cannot
    name it, nothing else it reports is worth reading.
    """
    body = await _investigate(api)
    assert "Northeast" in _subjects(body["where"], "region")


async def test_the_affected_region_leads_its_own_dimension(api: AsyncClient) -> None:
    body = await _investigate(api)
    regions = _subjects(body["where"], "region")
    assert regions and regions[0] == "Northeast", f"expected Northeast first, got {regions}"


async def test_regions_that_held_up_are_not_reported_as_causes(api: AsyncClient) -> None:
    """The inverted-filter regression, pinned.

    Excess share is already normalised by the total change, so a slice falling
    during a decline is *positive*. An earlier version multiplied by the sign
    of the change as well, which flipped the test and ranked precisely the
    regions that had grown — reporting the healthy parts of the estate as the
    cause of its decline.
    """
    body = await _investigate(api)
    for finding in body["where"]:
        assert finding["impact_share"] > 0, (
            f"{finding['subject']} moved against the decline and is not a cause of it"
        )


async def test_the_weather_incident_is_surfaced(api: AsyncClient) -> None:
    body = await _investigate(api)
    assert "Northeast" in _subjects(body["why"], "weather")


async def test_the_carrier_incident_is_surfaced(api: AsyncClient) -> None:
    """The harder case: the symptom is in sales and the cause is in shipping."""
    body = await _investigate(api)
    shipping = [f for f in body["why"] if f["dimension"] == "shipping"]
    assert shipping, "the planted carrier degradation was not detected"


# ── The engine does not overclaim ────────────────────────────────────


async def test_weather_never_exceeds_its_evidence_ceiling(api: AsyncClient) -> None:
    """Even a perfectly correlated storm stays an association.

    This is the design's load-bearing rule. Weather is the most predictive
    signal in the estate and the least actionable, and an engine that lets
    correlation strength promote it into a mechanism will send an operator to
    fix the sky.
    """
    body = await _investigate(api)
    for finding in body["why"]:
        if finding["dimension"] == "weather":
            assert finding["evidence_tier"] == "associative"
            assert finding["confidence"] <= 0.45
            assert finding["claim_type"] == "coincides_with"


async def test_no_finding_exceeds_its_own_ceiling(api: AsyncClient) -> None:
    body = await _investigate(api)
    for finding in body["findings"]:
        assert finding["confidence"] <= finding["confidence_ceiling"]


async def test_arithmetic_findings_claim_contribution_not_causation(
    api: AsyncClient,
) -> None:
    body = await _investigate(api)
    for finding in body["where"]:
        assert finding["claim_type"] == "accounts_for"
        assert finding["does_not_establish"]


async def test_every_finding_states_what_it_cannot_establish(api: AsyncClient) -> None:
    body = await _investigate(api)
    assert body["findings"]
    for finding in body["findings"]:
        assert finding["does_not_establish"], f"{finding['subject']} claims without limit"


async def test_the_response_admits_it_is_observational(api: AsyncClient) -> None:
    body = await _investigate(api)
    assert any("observational" in caveat.lower() for caveat in body["caveats"])


async def test_segment_findings_carry_the_as_of_caveat(api: AsyncClient) -> None:
    """RFM segments are assigned now and applied to the past."""
    body = await _investigate(api)
    if any(f["dimension"] == "segment" for f in body["where"]):
        assert any("as of today" in caveat for caveat in body["caveats"])


# ── Structure ────────────────────────────────────────────────────────


async def test_where_and_why_are_kept_apart(api: AsyncClient) -> None:
    """A subtraction and a correlation must not read as the same statement."""
    body = await _investigate(api)
    assert all(f["evidence_tier"] == "arithmetic" for f in body["where"])
    assert all(f["evidence_tier"] != "arithmetic" for f in body["why"])
    assert len(body["where"]) + len(body["why"]) == len(body["findings"])


async def test_explained_share_does_not_sum_across_cuts(api: AsyncClient) -> None:
    """Region and segment describe the same pounds; adding them counts twice.

    An earlier version summed every arithmetic finding and reported 298%
    explained, with every individual number correct.
    """
    body = await _investigate(api)
    naive_total = sum(abs(f["impact_share"]) for f in body["where"])
    assert body["explained_share"] <= naive_total + 1e-6
    if len({f["dimension"] for f in body["where"]}) > 1:
        assert body["explained_share"] < naive_total


async def test_no_single_dimension_fills_the_briefing(api: AsyncClient) -> None:
    """Five variations on one cut is one finding restated five times."""
    body = await _investigate(api)
    counts: dict[str, int] = {}
    for finding in body["findings"]:
        counts[finding["dimension"]] = counts.get(finding["dimension"], 0) + 1
    assert max(counts.values()) <= 2


async def test_findings_are_ranked_by_stake_weighted_confidence(api: AsyncClient) -> None:
    body = await _investigate(api)
    scores = [f["confidence"] * abs(f["impact_share"]) for f in body["findings"]]
    assert scores == sorted(scores, reverse=True)


async def test_every_finding_carries_checkable_evidence(api: AsyncClient) -> None:
    body = await _investigate(api)
    for finding in body["findings"]:
        assert finding["evidence"], f"{finding['subject']} asserts without evidence"
        for item in finding["evidence"]:
            assert item["label"]


async def test_recommendations_state_their_assumptions(api: AsyncClient) -> None:
    """A recommendation whose assumption is false is worse than none."""
    body = await _investigate(api)
    for finding in body["findings"]:
        for recommendation in finding["recommendations"]:
            assert recommendation["assumes"], f"{recommendation['action']} assumes nothing?"
            assert recommendation["rationale"]


async def test_revenue_changes_are_split_into_volume_and_rate(api: AsyncClient) -> None:
    """Fewer baskets and smaller baskets are different problems."""
    body = await _investigate(api)
    labels = {item["label"] for f in body["where"] for item in f["evidence"]}
    assert "Volume effect" in labels
    assert "Rate effect" in labels


async def test_the_planted_weather_drop_reads_as_a_volume_effect(api: AsyncClient) -> None:
    """The incident suppresses transactions, not basket value.

    A storm keeps people at home; it does not make the ones who came spend
    less. If the engine reported this as a rate effect it would send a
    merchant to review pricing during a snowstorm.
    """
    body = await _investigate(api)
    northeast = next(
        (f for f in body["where"] if f["dimension"] == "region" and f["subject"] == "Northeast"),
        None,
    )
    assert northeast is not None
    assert "volume" in northeast["headline"]


# ── Restraint ────────────────────────────────────────────────────────


async def test_a_flat_period_produces_no_causes(api: AsyncClient) -> None:
    """An engine that always finds three causes finds three causes for noise.

    Comparing a quiet week against the week before it should return an empty
    briefing and say why, rather than manufacturing explanations for ordinary
    variation.
    """
    quiet_end = BASELINE_END
    quiet_start = quiet_end - timedelta(days=6)
    body = await _investigate(
        api,
        current_start=quiet_start.isoformat(),
        current_end=quiet_end.isoformat(),
        baseline_start=(quiet_start - timedelta(days=7)).isoformat(),
        baseline_end=(quiet_start - timedelta(days=1)).isoformat(),
    )
    if abs(body["relative_change"]) < 0.02:
        assert body["findings"] == []
        assert any("floor for investigation" in caveat for caveat in body["caveats"])


async def test_overlapping_windows_are_refused(api: AsyncClient) -> None:
    """Shared days mute the very change being investigated."""
    response = await api.get(
        "/api/v1/rca/investigate",
        headers=await auth_headers(api),
        params={
            "current_start": CURRENT_START.isoformat(),
            "current_end": LAST_DAY.isoformat(),
            "baseline_start": BASELINE_START.isoformat(),
            "baseline_end": LAST_DAY.isoformat(),
        },
    )
    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/problem+json")


async def test_unavailable_dimensions_are_named_not_omitted(api: AsyncClient) -> None:
    """A silently missing dimension looks identical to one that found nothing."""
    body = await _investigate(api, dimensions=["weather"])
    assert body["dimensions_investigated"] == ["weather"]


# ── Authorization ────────────────────────────────────────────────────


async def test_a_role_without_rca_access_is_refused(api: AsyncClient) -> None:
    response = await api.get("/api/v1/rca/investigate", headers=await auth_headers(api, "admin"))
    assert response.status_code == 403


async def test_anonymous_access_is_rejected(api: AsyncClient) -> None:
    response = await api.get("/api/v1/rca/investigate")
    assert response.status_code == 401
