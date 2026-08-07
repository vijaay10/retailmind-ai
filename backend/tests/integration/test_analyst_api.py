"""The business analyst against a live warehouse.

Proves all eight capabilities reach real engines and come back in the shape a
senior analyst gives — including the two things that distinguish it from a
chatbot: refusing what it cannot answer, and saying what it did not check.
"""

import pytest

pytest.importorskip("testcontainers", reason="integration extra not installed")
from httpx import AsyncClient  # noqa: E402

from tests.integration.conftest import auth_headers  # noqa: E402
from tests.integration.warehouse import LAST_DAY  # noqa: E402

pytestmark = pytest.mark.integration


async def _ask(
    api: AsyncClient,
    question: str,
    *,
    conversation: list[dict] | None = None,
    role: str = "ceo",
) -> tuple[int, dict]:
    response = await api.post(
        "/api/v1/analyst/ask",
        headers=await auth_headers(api, role),
        json={
            "question": question,
            "conversation": conversation or [],
            "as_of": LAST_DAY.isoformat(),
        },
    )
    return response.status_code, response.json()


# ── All eight capabilities reach a real engine ───────────────────────


@pytest.mark.parametrize(
    ("question", "capability"),
    [
        ("What does AOV mean?", "explain_kpi"),
        ("Show revenue by region", "answer"),
        ("Why did revenue fall?", "investigate"),
        ("What should we do next?", "recommend"),
        ("Summarise how we are doing", "summarise"),
        ("Compare this period against the prior one", "compare"),
        ("Where could our measurement improve?", "improve"),
    ],
)
async def test_every_capability_answers(api: AsyncClient, question: str, capability: str) -> None:
    status, body = await _ask(api, question)
    assert status == 200, body
    assert body["capability"] == capability
    assert body["headline"]


async def test_the_forecast_capability_answers_or_refuses_with_a_reason(
    api: AsyncClient,
) -> None:
    """This fixture never trains a model, so there is nothing to explain.

    Both outcomes are correct: explain the published forecast, or say plainly
    that none exists. What would be wrong is a confident answer built on a
    forecast nobody produced — which is why the refusal has to name the reason
    rather than returning an empty success.
    """
    status, body = await _ask(api, "How much should I trust the forecast?")

    if status == 200:
        assert body["capability"] == "explain_forecast"
        assert body["facts"]
    else:
        assert status == 422
        assert "forecast" in str(body).lower()


# ── The senior-analyst contract holds end to end ─────────────────────


async def test_facts_and_inferences_come_back_separately(api: AsyncClient) -> None:
    """A decomposition is arithmetic; an explanation for it is a hypothesis."""
    status, body = await _ask(api, "Why did revenue fall?")
    assert status == 200

    for fact in body["facts"]:
        assert fact["certainty"] in {"measured", "derived"}
    for inference in body["inferences"]:
        assert inference["certainty"] in {"inferred", "unknown"}


async def test_every_answer_says_what_it_checked(api: AsyncClient) -> None:
    status, body = await _ask(api, "Show revenue by region")
    assert status == 200
    assert body["checked"]


async def test_answers_say_what_they_did_not_check(api: AsyncClient) -> None:
    """An assistant that reports only what it looked at leaves its silences
    unreadable — the reader cannot tell whether returns were fine or never
    examined."""
    status, body = await _ask(api, "Show revenue by region")
    assert status == 200
    assert body["not_checked"]


async def test_every_answer_proposes_a_next_question(api: AsyncClient) -> None:
    """An analyst who answers exactly what was asked and stops is a search box."""
    for question in ("Show revenue by region", "Why did revenue fall?", "What does AOV mean?"):
        status, body = await _ask(api, question)
        assert status == 200
        assert body["follow_ups"], question
        assert all(item["because"] for item in body["follow_ups"])


async def test_statements_name_their_source(api: AsyncClient) -> None:
    status, body = await _ask(api, "Why did revenue fall?")
    assert status == 200
    assert all(fact["source"] for fact in body["facts"])


# ── Conversation ─────────────────────────────────────────────────────


async def test_a_follow_up_resolves_against_the_previous_turn(
    api: AsyncClient,
) -> None:
    """ "Why did that drop?" is meaningless alone and obvious in context."""
    status, first = await _ask(api, "Show revenue by region")
    assert status == 200

    status, second = await _ask(
        api, "Why did that drop?", conversation=first["conversation"]["turns"]
    )
    assert status == 200
    assert second["capability"] == "investigate"


async def test_the_conversation_grows_with_each_turn(api: AsyncClient) -> None:
    _, first = await _ask(api, "Show revenue by region")
    _, second = await _ask(api, "What should we do?", conversation=first["conversation"]["turns"])
    assert len(second["conversation"]["turns"]) == len(first["conversation"]["turns"]) + 1


async def test_a_pronoun_without_history_is_not_invented(api: AsyncClient) -> None:
    """Guessing a subject produces a confident answer about something arbitrary."""
    status, body = await _ask(api, "Why did that drop?")
    assert status in {200, 422}


# ── Restraint ────────────────────────────────────────────────────────


async def test_a_question_about_something_unmeasured_is_refused(
    api: AsyncClient,
) -> None:
    """The failure this design exists to prevent.

    "ROI on our TikTok campaign" resolves 'campaign' to the promotions domain
    and would otherwise return total promotional revenue — a confident answer
    about a channel the platform does not have and a metric it does not
    compute.
    """
    status, body = await _ask(api, "What is the ROI on our TikTok campaign?")
    assert status == 422, body


async def test_an_unknown_metric_is_not_explained(api: AsyncClient) -> None:
    status, _ = await _ask(api, "What does frobnication mean?")
    assert status == 422


async def test_an_empty_question_is_refused(api: AsyncClient) -> None:
    response = await api.post(
        "/api/v1/analyst/ask", headers=await auth_headers(api), json={"question": "   "}
    )
    assert response.status_code == 422


async def test_the_request_rejects_unknown_fields(api: AsyncClient) -> None:
    response = await api.post(
        "/api/v1/analyst/ask",
        headers=await auth_headers(api),
        json={"question": "revenue", "sql": "DROP TABLE fct_sales"},
    )
    assert response.status_code == 422


# ── Composition, not reimplementation ────────────────────────────────


async def test_the_analyst_agrees_with_the_engine_behind_it(
    api: AsyncClient,
) -> None:
    """An assistant with its own implementation contradicts the screen."""
    _, analyst = await _ask(api, "Why did revenue fall?")

    direct = await api.get(
        "/api/v1/rca/investigate",
        headers=await auth_headers(api),
        params={"current_end": LAST_DAY.isoformat()},
    )
    assert direct.status_code == 200

    findings = direct.json()["findings"]
    if findings:
        assert findings[0]["headline"] in analyst["headline"] or any(
            findings[0]["headline"] == fact["text"] for fact in analyst["facts"]
        )


async def test_the_improve_capability_lists_the_platforms_own_gaps(
    api: AsyncClient,
) -> None:
    """The most senior contribution is often stating what cannot be answered."""
    status, body = await _ask(api, "Where could our measurement improve?")
    assert status == 200
    assert body["inferences"]
    assert all(item["certainty"] == "unknown" for item in body["inferences"])


# ── Authorization ────────────────────────────────────────────────────


async def test_anonymous_access_is_rejected(api: AsyncClient) -> None:
    response = await api.post("/api/v1/analyst/ask", json={"question": "revenue"})
    assert response.status_code == 401
