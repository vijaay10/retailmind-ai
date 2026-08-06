"""The analyst's behaviour, and what makes it senior rather than junior.

The tests worth having are about restraint: refusing questions the platform
cannot answer, separating what the data shows from what follows from it, and
saying what was *not* checked. An assistant that always produces something
teaches its users that every reply is a guess — and these are the assertions
that stop this one drifting there.
"""

from datetime import date

import pytest

from app.services.analyst import comparison, glossary
from app.services.analyst.contracts import (
    AnalystAnswer,
    CannotAnswerError,
    Capability,
    Certainty,
    Conversation,
    FollowUp,
    Statement,
    Turn,
)
from app.services.analyst.service import BusinessAnalystService

AS_OF = date(2026, 7, 21)


@pytest.fixture
def analyst() -> BusinessAnalystService:
    """The classifier and context resolver need no engines behind them."""
    return BusinessAnalystService(analytics=None)  # type: ignore[arg-type]


# ── Classification ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("What does AOV mean?", Capability.EXPLAIN_KPI),
        ("Define net revenue", Capability.EXPLAIN_KPI),
        ("Why did revenue fall?", Capability.INVESTIGATE),
        ("What should we do about stock?", Capability.RECOMMEND),
        ("Summarise how we are doing", Capability.SUMMARISE),
        ("Compare this month against last", Capability.COMPARE),
        ("What is the forecast for next week?", Capability.EXPLAIN_FORECAST),
        ("How much should I trust the forecast?", Capability.EXPLAIN_FORECAST),
        ("Where could our measurement improve?", Capability.IMPROVE),
        ("Show top customers", Capability.ANSWER),
    ],
)
def test_questions_route_to_the_right_capability(
    analyst: BusinessAnalystService, question: str, expected: Capability
) -> None:
    assert analyst._classify(question.lower()) is expected


def test_a_definitional_question_beats_a_data_question(
    analyst: BusinessAnalystService,
) -> None:
    """ "What is AOV" wants a definition.

    Every other branch would happily answer it with this month's average order
    value — a confident reply to a question nobody asked.
    """
    assert analyst._classify("what does average order value mean") is Capability.EXPLAIN_KPI


def test_asking_what_to_do_about_a_forecast_is_advice_not_a_forecast(
    analyst: BusinessAnalystService,
) -> None:
    assert analyst._classify("what should we do about the forecast") is Capability.RECOMMEND


# ── Conversation memory ──────────────────────────────────────────────


def test_a_pronoun_resolves_against_the_previous_subject(
    analyst: BusinessAnalystService,
) -> None:
    """ "Why did that drop?" is meaningless alone and obvious in context."""
    history = Conversation(
        turns=(Turn(question="show revenue", capability=Capability.ANSWER, subject="net_revenue"),)
    )
    assert "net_revenue" in analyst._resolve("Why did that drop?", history)


def test_a_pronoun_with_no_history_is_left_alone(
    analyst: BusinessAnalystService,
) -> None:
    """Inventing a subject would answer confidently about something arbitrary."""
    assert analyst._resolve("Why did that drop?", Conversation()) == "Why did that drop?"


def test_a_question_without_a_pronoun_is_untouched(
    analyst: BusinessAnalystService,
) -> None:
    history = Conversation(
        turns=(Turn(question="q", capability=Capability.ANSWER, subject="net_revenue"),)
    )
    assert analyst._resolve("Show units by store", history) == "Show units by store"


def test_context_does_not_reach_back_indefinitely() -> None:
    """Beyond a couple of turns "that" is ambiguous to the human too."""
    turns = tuple(
        Turn(question=f"q{n}", capability=Capability.ANSWER, subject="" if n else "net_revenue")
        for n in range(6)
    )
    assert Conversation(turns=turns).recent_subject() == ""


def test_a_conversation_keeps_subjects_not_prose() -> None:
    """Carrying prose forward is how an assistant starts answering questions
    about its own earlier phrasing."""
    turn = Turn(question="q", capability=Capability.ANSWER, subject="net_revenue")
    assert set(turn.as_dict()) == {"question", "capability", "subject", "period_end", "period_days"}


# ── KPI glossary ─────────────────────────────────────────────────────


def test_a_metric_is_explained_from_the_registry_that_defines_it() -> None:
    """A separately maintained glossary describes last year's definition."""
    explanation = glossary.explain("net_revenue")
    assert explanation is not None
    assert explanation.definition
    assert explanation.computed_as


def test_a_metric_resolves_by_label_as_well_as_key() -> None:
    assert glossary.explain("Average Order Value") is not None


def test_a_ratio_warns_against_averaging_it() -> None:
    """The most common error in retail reporting, generated rather than remembered."""
    explanation = glossary.explain("aov")
    assert explanation is not None
    assert any(
        "ratio" in item.lower() or "average" in item.lower() for item in explanation.misreadings
    )


def test_a_semi_additive_metric_warns_against_summing_over_time() -> None:
    explanation = glossary.explain("on_hand_units")
    assert explanation is not None
    assert "never across dates" in explanation.how_to_read


def test_an_unknown_term_explains_nothing_rather_than_guessing() -> None:
    assert glossary.explain("frobnication") is None


# ── Period comparison ────────────────────────────────────────────────


def test_windows_of_different_length_are_normalised() -> None:
    """A 28-day period against a 31-day one differs by 10% before anything
    happened, and both numbers are individually correct."""
    current = comparison.Window(date(2026, 7, 1), date(2026, 7, 14))
    baseline = comparison.Window(date(2026, 6, 4), date(2026, 6, 31 - 1))

    result = comparison.compare(
        metric="net_revenue",
        current=current,
        baseline=baseline,
        current_value=1000.0,
        baseline_value=2000.0,
    )
    assert result.scale != 1.0
    assert result.baseline_scaled != result.baseline_value


def test_equal_windows_are_not_scaled() -> None:
    current, baseline = comparison.windows(AS_OF, 14)
    result = comparison.compare(
        metric="net_revenue",
        current=current,
        baseline=baseline,
        current_value=900.0,
        baseline_value=1000.0,
    )
    assert result.scale == pytest.approx(1.0)
    assert result.relative_change == pytest.approx(-0.1)


def test_a_trivial_move_is_described_as_flat() -> None:
    """Calling a 0.4% move a decline trains readers to discount the language."""
    current, baseline = comparison.windows(AS_OF, 14)
    result = comparison.compare(
        metric="net_revenue",
        current=current,
        baseline=baseline,
        current_value=1004.0,
        baseline_value=1000.0,
    )
    assert not result.is_material
    assert "broadly flat" in result.describe()


def test_a_comparison_splits_volume_from_rate() -> None:
    """Fewer transactions and smaller ones are different problems."""
    current, baseline = comparison.windows(AS_OF, 14)
    result = comparison.compare(
        metric="net_revenue",
        current=current,
        baseline=baseline,
        current_value=500.0,
        baseline_value=1000.0,
        current_count=50.0,
        baseline_count=100.0,
    )
    assert result.dominant == "volume"
    assert "transactions" in result.describe()


def test_period_windows_do_not_overlap() -> None:
    """Shared days mute the very change being measured."""
    current, baseline = comparison.windows(AS_OF, 28)
    assert baseline.end < current.start
    assert current.days == baseline.days == 28


# ── The senior-analyst contract ──────────────────────────────────────


def _answer(**overrides: object) -> AnalystAnswer:
    defaults: dict[str, object] = {
        "question": "q",
        "capability": Capability.ANSWER,
        "headline": "h",
    }
    defaults.update(overrides)
    return AnalystAnswer(**defaults)  # type: ignore[arg-type]


def test_facts_and_inferences_are_separate_fields() -> None:
    """Merging them is how a reader comes to treat a guess as a measurement."""
    answer = _answer(
        facts=(Statement("revenue fell 6%", Certainty.MEASURED),),
        inferences=(Statement("weather may explain it", Certainty.INFERRED),),
    )
    payload = answer.as_dict()

    assert payload["facts"] != payload["inferences"]
    assert payload["facts"][0]["certainty"] == "measured"
    assert payload["inferences"][0]["certainty"] == "inferred"


def test_a_statement_names_where_it_came_from() -> None:
    """So a reader can go and check it."""
    assert Statement("x", Certainty.MEASURED, "revenue domain").as_dict()["source"]


def test_a_follow_up_says_why_it_is_worth_asking() -> None:
    """A bare next question is a menu; the reason is the analysis."""
    follow_up = FollowUp("Why did it move?", "A level is a starting point.")
    assert follow_up.as_dict()["because"]


def test_cannot_answer_carries_a_reason_and_an_alternative() -> None:
    """ "I can't tell you that, and here is why" is the senior response."""
    error = CannotAnswerError(
        "no such metric", because="it is not in the registry", instead=("try the catalogue",)
    )
    assert error.because
    assert error.instead
