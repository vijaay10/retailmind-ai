"""Planning, and the injection defence.

The security tests here are the point of the file. They do not check that
malicious input is *escaped* — nothing is escaped, because nothing is
interpolated. They check that a payload cannot become part of a statement at
all, because the only thing a question can produce is a set of registry keys,
and an attack string is not one.

The distinction matters under review. An escaping test passes right up until
someone finds an encoding the escaper missed. These tests would keep passing,
because there is no escaper: the attack tokens end up in a list of words the
planner could not resolve, and that list is reported to the user rather than
sent anywhere.
"""

from datetime import date

import pytest

from app.services.nlq.charts import choose
from app.services.nlq.contracts import ChartType, Intent, UnsupportedQuestionError
from app.services.nlq.explain import build
from app.services.nlq.planner import (
    MAX_QUESTION_LENGTH,
    DeterministicPlanner,
    normalise_question,
)
from app.services.nlq.vocabulary import RESTRICTED_DOMAINS, Vocabulary

AS_OF = date(2026, 7, 21)


@pytest.fixture
def planner() -> DeterministicPlanner:
    return DeterministicPlanner(Vocabulary())


# ── Injection cannot reach a statement ───────────────────────────────

INJECTION_PAYLOADS = [
    "Show top customers; DROP TABLE app_user; --",
    "revenue' UNION SELECT password FROM app_user --",
    "revenue by region; DELETE FROM fct_sales",
    "show me revenue FROM information_schema.tables",
    "revenue WHERE 1=1 OR 1=1",
    "customers'); TRUNCATE fct_sales; --",
    "revenue/**/UNION/**/SELECT/**/1",
    "revenue\\'; EXEC xp_cmdshell('rm -rf /'); --",
]


@pytest.mark.parametrize("payload", INJECTION_PAYLOADS)
def test_injection_payloads_never_reach_the_plan(
    planner: DeterministicPlanner, payload: str
) -> None:
    """The core guarantee, stated eight ways.

    Whatever the payload, the plan contains only registry keys. There is no
    field on a plan that could hold a fragment of SQL, so there is nothing for
    an attacker to aim at.
    """
    try:
        plan = planner.plan(payload, as_of=AS_OF)
    except UnsupportedQuestionError:
        return  # refusing outright is equally safe

    vocabulary = Vocabulary()
    assert plan.domain in vocabulary.domain_keys
    for metric in plan.metrics:
        assert metric in vocabulary.metrics_for(plan.domain)
    for dimension in plan.dimensions:
        assert dimension in vocabulary.dimensions_for(plan.domain)


@pytest.mark.parametrize("payload", INJECTION_PAYLOADS)
def test_attack_tokens_end_up_reported_not_executed(
    planner: DeterministicPlanner, payload: str
) -> None:
    """Unrecognised words are shown back to the user, not acted on."""
    try:
        plan = planner.plan(payload, as_of=AS_OF)
    except UnsupportedQuestionError:
        return

    dangerous = {"drop", "delete", "union", "truncate", "exec", "password", "xp_cmdshell"}

    # The dangerous tokens must not have been mistaken for anything actionable.
    assert not (dangerous & set(plan.metrics))
    assert not (dangerous & set(plan.dimensions))
    assert not (dangerous & set(plan.filters))
    assert plan.sort_by is None or plan.sort_by not in dangerous


def test_a_plan_has_no_field_capable_of_holding_sql(
    planner: DeterministicPlanner,
) -> None:
    """A structural guarantee, asserted so a future field cannot slip in.

    The defence rests on the plan being incapable of expressing a statement.
    A ``where_clause`` or ``sql`` field added later would quietly remove that
    property, and this test is what fails when someone tries.
    """
    plan = planner.plan("revenue by region", as_of=AS_OF)
    forbidden = {"sql", "query", "where_clause", "expression", "raw", "statement", "table"}
    assert not (forbidden & set(plan.as_dict()))


def test_an_unknown_metric_is_refused_rather_than_passed_through(
    planner: DeterministicPlanner,
) -> None:
    vocabulary = Vocabulary()
    assert not vocabulary.resolve_metric("password", domain_key="revenue").resolved
    assert not vocabulary.resolve_domain("app_user").resolved
    assert not vocabulary.resolve_dimension("email", domain_key="customer").resolved


def test_restricted_domains_are_unreachable_by_question() -> None:
    """Some domains lose their guardrails when queried directly.

    RCA slices without the grading, forecasts without the accuracy record.
    They stay reachable through their own endpoints, where the surrounding
    service supplies what a bare query would drop.
    """
    vocabulary = Vocabulary()
    for restricted in RESTRICTED_DOMAINS:
        assert restricted not in vocabulary.domain_keys


def test_an_overlong_question_is_refused() -> None:
    with pytest.raises(UnsupportedQuestionError, match="longer than"):
        normalise_question("revenue " * MAX_QUESTION_LENGTH)


def test_an_empty_question_is_refused() -> None:
    with pytest.raises(UnsupportedQuestionError, match="empty"):
        normalise_question("   ")


def test_control_characters_are_stripped() -> None:
    """A newline in a logged question is a forged log line."""
    assert "\n" not in normalise_question("revenue\nby\x00region")


# ── The three named questions ────────────────────────────────────────


def test_why_did_sales_decrease_is_a_diagnosis_not_a_query(
    planner: DeterministicPlanner,
) -> None:
    """No SELECT answers 'why'.

    Translating it into one produces a table that looks like an answer while
    omitting the entire question, and a reader will over-interpret it.
    """
    plan = planner.plan("Why did sales decrease?", as_of=AS_OF)
    assert plan.intent is Intent.DIAGNOSIS
    assert "root cause" in plan.interpretation


def test_show_top_customers_becomes_a_ranked_query(
    planner: DeterministicPlanner,
) -> None:
    plan = planner.plan("Show top customers.", as_of=AS_OF)
    assert plan.intent is Intent.METRIC_QUERY
    assert plan.domain == "customer"
    assert plan.descending
    assert plan.dimensions


def test_compare_stores_groups_by_store(planner: DeterministicPlanner) -> None:
    plan = planner.plan("Compare stores.", as_of=AS_OF)
    assert plan.intent is Intent.METRIC_QUERY
    assert plan.domain == "store"
    assert plan.dimensions == ("store",)


# ── Interpretation ───────────────────────────────────────────────────


def test_top_n_is_honoured(planner: DeterministicPlanner) -> None:
    assert planner.plan("top 5 stores", as_of=AS_OF).limit == 5


def test_worst_reverses_the_sort(planner: DeterministicPlanner) -> None:
    assert not planner.plan("worst 5 stores", as_of=AS_OF).descending


def test_a_period_phrase_sets_the_window_not_a_grouping(
    planner: DeterministicPlanner,
) -> None:
    """ "Last week" is when, not what-by.

    Before this was fixed, "revenue by region last week" grouped by region
    *and day* — twenty rows of daily detail in answer to a question about five
    regions.
    """
    plan = planner.plan("revenue by region last week", as_of=AS_OF)
    assert plan.dimensions == ("region",)
    assert plan.start_date == date(2026, 7, 15)


def test_by_day_is_still_a_grouping(planner: DeterministicPlanner) -> None:
    plan = planner.plan("revenue by day last month", as_of=AS_OF)
    assert plan.dimensions == ("business_date",)


def test_a_question_naming_no_measure_defaults_and_says_so(
    planner: DeterministicPlanner,
) -> None:
    """Refusing would be pedantic; guessing silently would be worse."""
    plan = planner.plan("compare stores", as_of=AS_OF)
    assert plan.metrics
    assert "no measure was named" in plan.interpretation
    assert plan.confidence < 0.75


def test_unrecognised_terms_are_reported(planner: DeterministicPlanner) -> None:
    """A question half-understood and answered anyway is how trust is lost."""
    plan = planner.plan("revenue by courier and region", as_of=AS_OF)
    assert "courier" in plan.unresolved


def test_a_question_about_nothing_known_is_refused(
    planner: DeterministicPlanner,
) -> None:
    with pytest.raises(UnsupportedQuestionError, match="what the question is about"):
        planner.plan("what is the weather on mars", as_of=AS_OF)


def test_advice_beats_forecast_when_both_are_mentioned(
    planner: DeterministicPlanner,
) -> None:
    """ "What should I do about the forecast" is a request for advice."""
    plan = planner.plan("what should we do about the forecast", as_of=AS_OF)
    assert plan.intent is Intent.RECOMMENDATION


# ── Charts describe what the data supports ───────────────────────────


def test_categories_get_bars_not_lines() -> None:
    """A line across categories implies an ordering they do not have."""
    rows = [{"region": r, "net_revenue": 100.0} for r in ("North", "South", "East")]
    chart = choose(dimensions=("region",), metrics=("net_revenue",), rows=rows)
    assert chart.type is ChartType.BAR
    assert "continuum" in chart.rationale


def test_dates_get_lines() -> None:
    rows = [{"business_date": f"2026-07-0{n}", "net_revenue": 10.0} for n in range(1, 6)]
    chart = choose(dimensions=("business_date",), metrics=("net_revenue",), rows=rows)
    assert chart.type is ChartType.LINE


def test_many_categories_go_horizontal_then_tabular() -> None:
    def rows(count: int) -> list[dict[str, object]]:
        return [{"store": f"S{n}", "net_revenue": float(n)} for n in range(count)]

    assert (
        choose(dimensions=("store",), metrics=("net_revenue",), rows=rows(12)).type
        is ChartType.HORIZONTAL_BAR
    )
    assert (
        choose(dimensions=("store",), metrics=("net_revenue",), rows=rows(80)).type
        is ChartType.TABLE
    )


def test_a_single_total_is_a_number_not_a_chart() -> None:
    chart = choose(dimensions=(), metrics=("net_revenue",), rows=[{"net_revenue": 5.0}])
    assert chart.type is ChartType.BIG_NUMBER


def test_an_empty_result_is_not_charted() -> None:
    assert choose(dimensions=("region",), metrics=("net_revenue",), rows=[]).type is ChartType.TABLE


def test_every_chart_explains_its_shape() -> None:
    rows = [{"region": "North", "net_revenue": 1.0}]
    assert choose(dimensions=("region",), metrics=("net_revenue",), rows=rows).rationale


# ── Explanations are arithmetic, not prose ───────────────────────────


def test_the_explanation_is_derived_from_the_rows(
    planner: DeterministicPlanner,
) -> None:
    plan = planner.plan("revenue by region", as_of=AS_OF)
    rows = [
        {"region": "North", "net_revenue": 600.0},
        {"region": "South", "net_revenue": 300.0},
        {"region": "East", "net_revenue": 100.0},
    ]
    explanation = build(plan, rows)

    assert "1,000" in explanation.summary
    assert any("60%" in detail for detail in explanation.details)


def test_the_explanation_states_the_period_it_covers(
    planner: DeterministicPlanner,
) -> None:
    plan = planner.plan("revenue by region", as_of=AS_OF)
    explanation = build(plan, [{"region": "North", "net_revenue": 1.0}])
    assert any("Period" in caveat for caveat in explanation.caveats)


def test_the_explanation_warns_the_total_is_capped(
    planner: DeterministicPlanner,
) -> None:
    """A total over the returned rows is not the estate total."""
    plan = planner.plan("revenue by region", as_of=AS_OF)
    explanation = build(plan, [{"region": "North", "net_revenue": 1.0}])
    assert any("not the estate total" in caveat for caveat in explanation.caveats)


def test_an_empty_result_is_not_reported_as_a_zero(
    planner: DeterministicPlanner,
) -> None:
    plan = planner.plan("revenue by region", as_of=AS_OF)
    explanation = build(plan, [])
    assert "No data" in explanation.summary
    assert any("not the same as a zero" in caveat for caveat in explanation.caveats)


def test_unresolved_terms_reach_the_caveats(planner: DeterministicPlanner) -> None:
    plan = planner.plan("revenue by courier", as_of=AS_OF)
    explanation = build(plan, [{"net_revenue": 1.0}])
    assert any("courier" in caveat for caveat in explanation.caveats)
