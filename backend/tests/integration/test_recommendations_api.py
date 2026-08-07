"""Recommendation endpoints against a real warehouse.

The unit suite proves the arithmetic. This one proves the engine reaches the
warehouse through the governed registry and comes back with advice whose
numbers hold together — that no recommendation claims more certainty than its
basis allows, that the portfolio totals do not promise the same pounds twice,
and that a campaign is never aimed at customers who are not at risk.
"""

import pytest

pytest.importorskip("testcontainers", reason="integration extra not installed")
from httpx import AsyncClient  # noqa: E402

from tests.integration.conftest import auth_headers  # noqa: E402
from tests.integration.warehouse import LAST_DAY  # noqa: E402

pytestmark = pytest.mark.integration


BASIS_CEILINGS = {"measured": 0.90, "modelled": 0.70, "assumed": 0.45}


async def _get(api: AsyncClient, role: str = "ceo", **params: object) -> dict:
    payload: dict[str, object] = {"end_date": LAST_DAY.isoformat()}
    payload.update(params)
    response = await api.get(
        "/api/v1/recommendations", headers=await auth_headers(api, role), params=payload
    )
    assert response.status_code == 200, response.text
    return response.json()


# ── The engine produces usable advice ────────────────────────────────


async def test_recommendations_are_produced(api: AsyncClient) -> None:
    body = await _get(api)
    assert body["recommendations"], "no recommendations from a live warehouse"


async def test_several_categories_are_covered(api: AsyncClient) -> None:
    """A recommender that only ever talks about stock is an inventory report."""
    body = await _get(api)
    assert len(body["by_category"]) >= 2


async def test_a_single_category_can_be_requested(api: AsyncClient) -> None:
    body = await _get(api, categories=["inventory"])
    assert set(body["by_category"]) <= {"inventory"}


async def test_empty_categories_are_explained_not_omitted(api: AsyncClient) -> None:
    """A silently absent category looks identical to one that never ran."""
    body = await _get(api)
    for reason in body["categories_empty"].values():
        assert reason


# ── Numbers hold together ────────────────────────────────────────────


async def test_no_recommendation_claims_more_certainty_than_its_basis(
    api: AsyncClient,
) -> None:
    """The load-bearing rule: an estimate cannot outrun its weakest input."""
    for item in (await _get(api))["recommendations"]:
        basis = item["impact"]["basis"]
        assert item["confidence"] <= BASIS_CEILINGS[basis] + 1e-9, (
            f"{item['subject']} claims {item['confidence']} on a {basis} estimate"
        )
        assert item["confidence"] <= item["confidence_ceiling"]


async def test_the_portfolio_does_not_promise_the_same_pounds_twice(
    api: AsyncClient,
) -> None:
    body = await _get(api)
    assert body["net_profit_opportunity"] <= body["gross_profit_opportunity"] + 1e-6


async def test_capital_freed_is_reported_apart_from_profit(api: AsyncClient) -> None:
    """Clearing dead stock releases cash and books a loss.

    If the two were combined, every markdown would look like a profit
    opportunity — which is how a clearance programme gets approved on the
    strength of the thing that makes it expensive.
    """
    body = await _get(api)
    markdowns = [
        item
        for item in body["recommendations"]
        if item["category"] == "pricing" and item["impact"]["capital_freed"] > 0
    ]
    for item in markdowns:
        assert item["impact"]["profit"] < item["impact"]["capital_freed"]


async def test_recommendations_are_ranked_by_risk_adjusted_profit(
    api: AsyncClient,
) -> None:
    scores = [item["risk_adjusted_profit"] for item in (await _get(api))["recommendations"]]
    assert scores == sorted(scores, reverse=True)


# ── Every recommendation is defensible ───────────────────────────────


async def test_every_recommendation_states_its_method(api: AsyncClient) -> None:
    for item in (await _get(api))["recommendations"]:
        assert item["impact"]["method"], f"{item['subject']} gives a number with no derivation"


async def test_unmeasured_parameters_are_declared_as_placeholders(
    api: AsyncClient,
) -> None:
    """An assumption dressed as a measurement is the failure mode here."""
    for item in (await _get(api))["recommendations"]:
        for assumption in item["impact"]["assumptions"]:
            assert assumption["source"] in {"measured", "industry default", "placeholder"}
            if assumption["source"] != "measured":
                assert not assumption["is_evidenced"]


async def test_assumed_estimates_ship_a_sensitivity_range(api: AsyncClient) -> None:
    for item in (await _get(api))["recommendations"]:
        if item["impact"]["basis"] == "assumed":
            assert item["impact"]["pessimistic_profit"] is not None
            assert item["impact"]["optimistic_profit"] is not None


async def test_every_recommendation_carries_a_downside(api: AsyncClient) -> None:
    """A response reporting only upside is a sales pitch."""
    for item in (await _get(api))["recommendations"]:
        assert "downside_profit" in item["risk"]
        assert item["risk"]["principal_risk"]
        assert item["risk"]["band"] in {"low", "medium", "high"}


async def test_irreversible_actions_are_banded_high(api: AsyncClient) -> None:
    for item in (await _get(api))["recommendations"]:
        if item["risk"]["reversibility"] == "irreversible":
            assert item["risk"]["band"] == "high"


async def test_every_recommendation_names_its_disqualifier(api: AsyncClient) -> None:
    """The reader is usually the only one who can check it."""
    for item in (await _get(api))["recommendations"]:
        assert item["do_not_act_if"], f"{item['subject']} has no disqualifying condition"


async def test_every_recommendation_carries_evidence_and_an_owner(
    api: AsyncClient,
) -> None:
    for item in (await _get(api))["recommendations"]:
        assert item["evidence"]
        assert item["owner"]


# ── Restraint ────────────────────────────────────────────────────────


async def test_customers_who_are_not_at_risk_are_never_targeted(
    api: AsyncClient,
) -> None:
    """Spending retention budget on customers behaving normally."""
    for item in (await _get(api))["recommendations"]:
        if item["category"] in {"customer", "marketing"}:
            assert not any(band in item["subject"].lower() for band in ("none", "low", "unknown"))


async def test_the_response_admits_its_estimates_are_estimates(
    api: AsyncClient,
) -> None:
    body = await _get(api)
    assert any("estimate" in caveat.lower() for caveat in body["caveats"])


async def test_unmeasured_parameters_are_summarised_in_the_caveats(
    api: AsyncClient,
) -> None:
    body = await _get(api)
    if any(item["impact"]["rests_on_unmeasured_assumptions"] for item in body["recommendations"]):
        assert any("unmeasured" in caveat for caveat in body["caveats"])


async def test_the_limit_is_respected(api: AsyncClient) -> None:
    body = await _get(api, limit=3)
    assert len(body["recommendations"]) <= 3


# ── Authorization ────────────────────────────────────────────────────


async def test_a_role_without_access_is_refused(api: AsyncClient) -> None:
    response = await api.get("/api/v1/recommendations", headers=await auth_headers(api, "admin"))
    assert response.status_code == 403


async def test_anonymous_access_is_rejected(api: AsyncClient) -> None:
    response = await api.get("/api/v1/recommendations")
    assert response.status_code == 401


# ── The decision loop ────────────────────────────────────────────────


async def _first_key(api: AsyncClient, role: str = "inventory") -> str:
    """A key from the portfolio *that role can see*.

    Deliberately not the CEO's first card. Roles see different categories, and
    the API refuses a decision on a recommendation the acting role was never
    shown — which is the intended behaviour, so a test that mixes the two
    roles is testing its own mistake.
    """
    body = (await api.get("/api/v1/recommendations", headers=await auth_headers(api, role))).json()
    assert body["recommendations"], f"{role} sees no recommendations to decide on"
    return str(body["recommendations"][0]["decision_key"])


async def test_reading_a_recommendation_does_not_license_acting_on_it(
    api: AsyncClient,
) -> None:
    """The CEO role sees everything and acts on nothing.

    Seeing a proposal and committing the business to it are different
    privileges, and the role matrix separates them — the managers who own the
    consequences may act.
    """
    response = await api.post(
        "/api/v1/recommendations/decisions",
        headers=await auth_headers(api, "ceo"),
        json={"decision_key": await _first_key(api, "ceo"), "action": "accepted"},
    )
    assert response.status_code == 403
    assert "recommendations.act" in response.text


async def test_a_manager_can_accept_and_the_ledger_keeps_the_estimate(
    api: AsyncClient,
) -> None:
    """The number a decision was made against is snapshot, not referenced.

    Tomorrow's engine will quote a different figure; a ledger that showed the
    new one beside yesterday's approval would be rewriting what was approved.
    """
    key = await _first_key(api)
    response = await api.post(
        "/api/v1/recommendations/decisions",
        headers=await auth_headers(api, "inventory"),
        json={"decision_key": key, "action": "accepted", "note": "raised PO 4471"},
    )
    assert response.status_code == 200, response.text

    decision = response.json()["decision"]
    assert decision["action"] == "accepted"
    assert decision["action_text"]
    assert decision["estimate_basis"] in {"measured", "modelled", "assumed"}
    assert decision["note"] == "raised PO 4471"


async def test_the_card_stops_reading_as_pending_once_decided(
    api: AsyncClient,
) -> None:
    """The engine has no memory of its own. Without the annotation an accepted
    action reappears tomorrow as though nobody had looked at it."""
    key = await _first_key(api)
    await api.post(
        "/api/v1/recommendations/decisions",
        headers=await auth_headers(api, "inventory"),
        json={"decision_key": key, "action": "accepted"},
    )

    body = (await api.get("/api/v1/recommendations", headers=await auth_headers(api, "ceo"))).json()
    decided = [item for item in body["recommendations"] if item["decision_key"] == key]

    assert decided and decided[0]["decision"]["action"] == "accepted"
    assert body["decided_count"] >= 1


async def test_changing_your_mind_replaces_rather_than_appends(
    api: AsyncClient,
) -> None:
    """A card that renders as both accepted and dismissed is a card nobody can
    act on."""
    key = await _first_key(api)
    headers = await auth_headers(api, "inventory")

    await api.post(
        "/api/v1/recommendations/decisions",
        headers=headers,
        json={"decision_key": key, "action": "accepted"},
    )
    await api.post(
        "/api/v1/recommendations/decisions",
        headers=headers,
        json={"decision_key": key, "action": "dismissed", "reason_code": "already_planned"},
    )

    log = (
        await api.get("/api/v1/recommendations/decisions", headers=await auth_headers(api, "ceo"))
    ).json()
    matching = [item for item in log["decisions"] if item["decision_key"] == key]

    assert len(matching) == 1
    assert matching[0]["action"] == "dismissed"
    assert matching[0]["reason_code"] == "already_planned"


async def test_a_reason_on_an_acceptance_is_dropped_rather_than_stored(
    api: AsyncClient,
) -> None:
    """`reason_code` enumerates why something was *rejected*. Storing one
    against an acceptance would poison the only learning signal dismissals
    carry."""
    key = await _first_key(api)
    response = await api.post(
        "/api/v1/recommendations/decisions",
        headers=await auth_headers(api, "inventory"),
        json={"decision_key": key, "action": "accepted", "reason_code": "already_planned"},
    )
    assert response.json()["decision"]["reason_code"] is None


async def test_deciding_on_advice_the_platform_no_longer_gives_is_refused(
    api: AsyncClient,
) -> None:
    """Positions move. Recording approval for a withdrawn recommendation would
    put agreement in the ledger for something nobody is advising."""
    response = await api.post(
        "/api/v1/recommendations/decisions",
        headers=await auth_headers(api, "inventory"),
        json={"decision_key": "deadbeef" * 4, "action": "accepted"},
    )
    assert response.status_code == 404


async def test_the_client_cannot_write_its_own_numbers_into_the_ledger(
    api: AsyncClient,
) -> None:
    """The request carries a key and a verb. Everything else is re-derived,
    because the ledger is what everyone later reasons from."""
    response = await api.post(
        "/api/v1/recommendations/decisions",
        headers=await auth_headers(api, "inventory"),
        json={
            "decision_key": await _first_key(api),
            "action": "accepted",
            "expected_profit": 10_000_000,
            "action_text": "give everything away",
        },
    )
    assert response.status_code == 422


async def test_an_invented_verb_is_refused(api: AsyncClient) -> None:
    response = await api.post(
        "/api/v1/recommendations/decisions",
        headers=await auth_headers(api, "inventory"),
        json={"decision_key": await _first_key(api), "action": "snoozed"},
    )
    assert response.status_code == 422


async def test_the_log_totals_expectations_not_outcomes(api: AsyncClient) -> None:
    """Nothing in this platform measures what happened after somebody acted,
    and a total labelled as realised profit would be an invention."""
    key = await _first_key(api)
    await api.post(
        "/api/v1/recommendations/decisions",
        headers=await auth_headers(api, "inventory"),
        json={"decision_key": key, "action": "accepted"},
    )

    log = (
        await api.get("/api/v1/recommendations/decisions", headers=await auth_headers(api, "ceo"))
    ).json()
    assert log["accepted_profit"] > 0
    assert "realised" not in str(log).lower()


async def test_anonymous_callers_cannot_decide(api: AsyncClient) -> None:
    response = await api.post(
        "/api/v1/recommendations/decisions",
        json={"decision_key": "x" * 32, "action": "accepted"},
    )
    assert response.status_code == 401


async def test_a_role_that_cannot_read_a_domain_is_told_so_not_shown_all_clear(
    api: AsyncClient,
) -> None:
    """Marketing may act on recommendations but cannot read inventory analytics.

    Failing the whole request would leave them an empty screen; returning a
    partial portfolio with no explanation would tell them "nothing to do"
    about surfaces they were never shown. The category names its reason.
    """
    body = (
        await api.get("/api/v1/recommendations", headers=await auth_headers(api, "inventory"))
    ).json()

    assert body["recommendations"], "the manager should still see what they can read"

    blocked = [reason for reason in body["categories_empty"].values() if "cannot read" in reason]
    assert blocked, "a role missing a domain permission should be told which"
    assert all("your role" in reason for reason in blocked)
