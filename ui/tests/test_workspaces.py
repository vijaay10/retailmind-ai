"""What the workspaces must never do.

Not smoke tests. Each one pins a way a competent-looking console misleads the
person reading it: a dropped qualification, a blank panel standing in for a
failed call, a headline a later screen contradicts, an action recorded that
nobody can trace, navigation mistaken for a security boundary.
"""

from typing import Any

import pytest

from retailmind_ui.api import ApiError

FORECAST = "/api/v1/forecasts/revenue"
ACCURACY = "/api/v1/forecasts/meta/accuracy"
RECOMMENDATIONS = "/api/v1/recommendations"
DECISIONS = "/api/v1/recommendations/decisions"
RCA = "/api/v1/rca/investigate"

ALL_WORKSPACES = [
    "1_Command_Center.py",
    "2_AI_Investigation.py",
    "3_Decision_Center.py",
    "4_AI_Analyst.py",
    "5_Sales_Intelligence.py",
    "6_Customer_Intelligence.py",
    "7_Inventory_Intelligence.py",
    "8_Store_Intelligence.py",
    "9_Forecast_Intelligence.py",
    "10_Risk_Center.py",
    "11_Executive_Briefing.py",
    "12_Admin.py",
    "13_Data_Sources.py",
]

FORECAST_BODY: dict[str, Any] = {
    "horizon_days": 14,
    "data": [
        {
            "business_date": "2026-07-22",
            "forecast": 101_000.0,
            "forecast_lower": 88_000.0,
            "forecast_upper": 114_000.0,
            "model_mase": 0.74,
            "model_wape": 0.11,
        }
    ],
    "caveats": [
        "Bands are calibrated on 42 days of history and widen with horizon.",
        "A promotion not present in history is not represented here.",
    ],
}

ACTION: dict[str, Any] = {
    "category": "inventory",
    "subject": "BS-1037@S2016",
    "decision_key": "abc123def456abc123def456abc12345",
    "action": "Order 122 units of BS-1037 for S2016",
    "rationale": "Cover is 2.0 days against an 8-day lead time.",
    "confidence": 0.72,
    "confidence_ceiling": 0.90,
    "do_not_act_if": "the line is being discontinued",
    "impact": {
        "profit": 42_350.0,
        "basis": "modelled",
        "rests_on_unmeasured_assumptions": True,
        "pessimistic_profit": -1_200.0,
        "optimistic_profit": 68_000.0,
    },
    "risk": {"band": "low", "principal_risk": "Carrying charge if unnecessary."},
    "decision": None,
}


# ── The gate ─────────────────────────────────────────────────────────


def test_a_signed_out_visitor_gets_no_figures(workspace: Any, texts: Any) -> None:
    app = workspace("9_Forecast_Intelligence.py", user=None)
    assert "Sign in" in texts(app.warning)
    assert not app.dataframe


def test_a_role_without_the_permission_is_told_which_one(
    workspace: Any, texts: Any, ceo: dict[str, Any]
) -> None:
    """ "Access denied" leaves a user unable to ask for the right thing."""
    app = workspace(
        "9_Forecast_Intelligence.py", user={**ceo, "permissions": ["analytics.revenue.read"]}
    )
    assert "forecasts.read" in texts(app.error)


def test_hiding_a_workspace_is_never_the_only_control(workspace: Any, ceo: dict[str, Any]) -> None:
    """A user who lacks the permission but reaches the page anyway is stopped
    here too — the navigation only decides which links exist."""
    app = workspace("10_Risk_Center.py", user={**ceo, "permissions": []})
    assert app.error
    assert not app.dataframe


@pytest.mark.parametrize("name", ALL_WORKSPACES)
def test_every_workspace_survives_an_api_that_answers_nothing(
    workspace: Any, texts: Any, name: str, manager: dict[str, Any]
) -> None:
    """Empty bodies everywhere. A workspace may show nothing; it may not crash,
    because the state it is least tested in is the one a new deployment is in.
    """
    app = workspace(
        name,
        user={
            **manager,
            "permissions": [*manager["permissions"], "admin.users", "data.manage"],
        },
    )
    assert not app.exception, texts(app.exception)


# ── Qualifications survive to the screen ─────────────────────────────


def test_caveats_render_inline_rather_than_behind_a_click(workspace: Any, markup: Any) -> None:
    """The single property this console exists to preserve.

    The API attaches caveats precisely because acting on a figure without them
    is the failure mode. A disclosure triangle is where a caveat goes to be
    ignored.
    """
    app = workspace(
        "9_Forecast_Intelligence.py",
        responses={FORECAST: FORECAST_BODY, ACCURACY: {"models": []}},
    )
    rendered = markup(app)
    assert "widen with horizon" in rendered
    assert "not represented here" in rendered
    assert not app.expander


def test_a_model_that_does_not_beat_a_baseline_says_so(workspace: Any, markup: Any) -> None:
    """MASE ≥ 1 means the forecast carries no more information than a calendar."""
    weak = {**FORECAST_BODY, "data": [{**FORECAST_BODY["data"][0], "model_mase": 1.31}]}
    app = workspace(
        "9_Forecast_Intelligence.py", responses={FORECAST: weak, ACCURACY: {"models": []}}
    )
    rendered = markup(app)
    assert "1.31" in rendered
    assert "seasonal-naive" in rendered


def test_a_healthy_model_is_not_second_guessed(workspace: Any, markup: Any) -> None:
    app = workspace(
        "9_Forecast_Intelligence.py",
        responses={FORECAST: FORECAST_BODY, ACCURACY: {"models": []}},
    )
    assert "has not earned trust" not in markup(app)


def test_confidence_is_rendered_against_its_ceiling(workspace: Any, markup: Any) -> None:
    """62% alone reads as a probability. Beside its ceiling it reads as what it
    is: the most this class of evidence could ever support."""
    app = workspace(
        "2_AI_Investigation.py",
        responses={
            RCA: {
                "metric": "net_revenue",
                "change": -162_966.0,
                "relative_change": -0.061,
                "current": {"start": "2026-07-15", "end": "2026-07-21", "days": 7},
                "baseline": {"start": "2026-06-24", "end": "2026-07-14", "days": 21},
                "where": [
                    {
                        "dimension": "segment",
                        "subject": "Loyal",
                        "headline": "Loyal accounts for +106% of the change",
                        "evidence_tier": "arithmetic",
                        "confidence": 0.54,
                        "confidence_ceiling": 0.95,
                        "impact_amount": -173_297.0,
                        "claim_type": "accounts_for",
                    }
                ],
                "why": [],
                "dimensions_investigated": ["segment"],
                "dimensions_unavailable": {},
                "caveats": [],
            }
        },
    )
    assert "of 95% max" in markup(app)


def test_an_associative_finding_carries_what_it_does_not_establish(
    workspace: Any, markup: Any
) -> None:
    """Without this sentence, "four severe-weather days coincided" is read as
    "weather caused it"."""
    app = workspace(
        "2_AI_Investigation.py",
        responses={
            RCA: {
                "change": -1000.0,
                "current": {"days": 7},
                "baseline": {"days": 21},
                "where": [],
                "why": [
                    {
                        "dimension": "weather",
                        "subject": "Northeast",
                        "headline": "4 severe-weather days",
                        "evidence_tier": "associative",
                        "confidence": 0.36,
                        "confidence_ceiling": 0.45,
                        "impact_amount": -146_754.0,
                        "does_not_establish": "That weather caused the shortfall.",
                    }
                ],
                "dimensions_investigated": ["weather"],
                "dimensions_unavailable": {},
                "caveats": [],
            }
        },
    )
    rendered = markup(app)
    assert "Does not establish" in rendered
    assert "That weather caused the shortfall." in rendered


def test_a_swept_dimension_that_found_nothing_is_still_shown(workspace: Any, markup: Any) -> None:
    """Hiding the seven dimensions that came back empty makes the two that did
    look like the only places anyone looked."""
    app = workspace(
        "2_AI_Investigation.py",
        responses={
            RCA: {
                "change": -1000.0,
                "current": {"days": 7},
                "baseline": {"days": 21},
                "where": [],
                "why": [],
                "dimensions_investigated": ["region", "promotion", "returns"],
                "dimensions_unavailable": {},
                "caveats": [],
            }
        },
    )
    assert "swept, nothing above the floor" in markup(app)


# ── Absence is drawn, not omitted ────────────────────────────────────


def test_an_empty_result_gives_its_reason(workspace: Any, markup: Any) -> None:
    app = workspace(
        "9_Forecast_Intelligence.py",
        responses={FORECAST: {"data": [], "caveats": []}, ACCURACY: {"models": []}},
    )
    assert "not run" in markup(app)


def test_a_failed_call_reports_the_outage_instead_of_a_blank_screen(
    workspace: Any, markup: Any
) -> None:
    """A command centre showing zero revenue because the API is unreachable is
    worse than one showing nothing at all."""
    app = workspace(
        "1_Command_Center.py",
        responses={
            "/api/v1/dashboard/executive": ApiError(
                status=0, title="Cannot reach the API", detail="connection refused"
            )
        },
    )
    rendered = markup(app)
    assert "connection refused" in rendered
    assert not app.dataframe


def test_an_unprovisioned_tenant_sees_a_calm_setup_state_not_an_outage(
    workspace: Any, markup: Any
) -> None:
    """Prompt 13: a brand-new tenant's warehouse not being provisioned yet
    (a real 503 `dependency-unavailable`, Prompt 12.5's own honest failure
    mode) must not read the same as the product being broken. Same
    ``ApiError`` type as the outage test above — only the status differs —
    proving the distinction is in `workspace_error`, not in a special-cased
    workspace."""
    app = workspace(
        "1_Command_Center.py",
        responses={
            "/api/v1/dashboard/executive": ApiError(
                status=503,
                title="Unavailable",
                detail="warehouse is temporarily unavailable.",
            )
        },
    )
    rendered = markup(app)
    assert "still being set up" in rendered
    assert "warehouse is temporarily unavailable" not in rendered
    assert not app.dataframe


# ── Numbers keep the meaning the API gave them ───────────────────────


def test_the_decision_queue_totals_net_and_explains_the_gap(workspace: Any, markup: Any) -> None:
    """The gross figure double-promises actions that chase the same pounds."""
    app = workspace(
        "3_Decision_Center.py",
        responses={
            RECOMMENDATIONS: {
                "count": 3,
                "gross_profit_opportunity": 180_000,
                "net_profit_opportunity": 120_000,
                "capital_freed": 90_000,
                "recommendations": [],
                "caveats": [],
            },
            DECISIONS: {"decisions": [], "count": 0, "accepted_profit": 0},
        },
    )
    rendered = markup(app)
    assert "120,000" in rendered
    assert "180,000" in rendered
    assert "net of overlap" in rendered


def test_capital_freed_is_never_folded_into_profit(workspace: Any, markup: Any) -> None:
    """Working capital released is not earnings, and a console that adds them
    reports a profit improvement no ledger will show."""
    app = workspace(
        "3_Decision_Center.py",
        responses={
            RECOMMENDATIONS: {
                "count": 1,
                "gross_profit_opportunity": 100,
                "net_profit_opportunity": 100,
                "capital_freed": 5_000,
                "recommendations": [],
            },
            DECISIONS: {"decisions": [], "count": 0, "accepted_profit": 0},
        },
    )
    assert "never added to profit" in markup(app)


def test_an_estimate_whose_range_crosses_zero_says_to_test_instead(
    workspace: Any, markup: Any
) -> None:
    """A sensitivity range spanning zero means the action might lose money.
    Printing only the point estimate hides exactly that."""
    app = workspace(
        "3_Decision_Center.py",
        responses={
            RECOMMENDATIONS: {
                "count": 1,
                "recommendations": [ACTION],
                "gross_profit_opportunity": 42_350,
                "net_profit_opportunity": 42_350,
                "capital_freed": 0,
            },
            DECISIONS: {"decisions": [], "count": 0, "accepted_profit": 0},
        },
    )
    rendered = markup(app)
    assert "range crosses zero" in rendered
    assert "a test beats a rollout" in rendered


def test_the_disqualifier_renders_above_the_decision(
    workspace: Any, markup: Any, manager: dict[str, Any]
) -> None:
    """`do_not_act_if` names the condition under which the advice is wrong. The
    platform cannot see it and the reader usually can — which is useless if it
    appears after the button."""
    app = workspace(
        "3_Decision_Center.py",
        user=manager,
        responses={
            RECOMMENDATIONS: {"count": 1, "recommendations": [ACTION]},
            DECISIONS: {"decisions": [], "count": 0, "accepted_profit": 0},
        },
    )
    rendered = markup(app)
    assert "Do not act if" in rendered
    assert "being discontinued" in rendered


# ── The decision loop ────────────────────────────────────────────────


def test_a_role_that_cannot_act_is_shown_why_not(workspace: Any, texts: Any) -> None:
    app = workspace(
        "3_Decision_Center.py",
        responses={
            RECOMMENDATIONS: {"count": 1, "recommendations": [ACTION]},
            DECISIONS: {"decisions": [], "count": 0, "accepted_profit": 0},
        },
    )
    assert "recommendations.act" in texts(app.caption)
    assert not [button for button in app.button if button.label == "Accept"]


def test_a_role_that_can_act_gets_the_buttons(workspace: Any, manager: dict[str, Any]) -> None:
    app = workspace(
        "3_Decision_Center.py",
        user=manager,
        responses={
            RECOMMENDATIONS: {"count": 1, "recommendations": [ACTION]},
            DECISIONS: {"decisions": [], "count": 0, "accepted_profit": 0},
        },
    )
    assert [button for button in app.button if button.label == "Accept"]


def test_accepting_sends_only_a_key_and_a_verb(
    workspace: Any, api: Any, manager: dict[str, Any]
) -> None:
    """The ledger is what everyone later reasons from. A client that could send
    its own action text and expected profit could write fiction into it."""
    client = api(
        {
            RECOMMENDATIONS: {"count": 1, "recommendations": [ACTION]},
            DECISIONS: {"decisions": [], "count": 0, "accepted_profit": 0},
        }
    )
    app = workspace("3_Decision_Center.py", user=manager, client=client)
    [button for button in app.button if button.label == "Accept"][0].click().run()

    sent = [call for call in client.posted if call["path"] == DECISIONS]
    assert sent, "accepting should post a decision"
    assert set(sent[0]) <= {"path", "decision_key", "action", "reason_code", "note"}
    assert sent[0]["action"] == "accepted"
    assert "expected_profit" not in sent[0]


def test_an_already_decided_action_does_not_read_as_pending(
    workspace: Any, markup: Any, manager: dict[str, Any]
) -> None:
    """The engine has no memory of its own: without the annotation an accepted
    action reappears tomorrow as though nobody had looked at it."""
    decided = {
        **ACTION,
        "decision": {
            "action": "accepted",
            "decided_at": "2026-08-06T09:00:00+00:00",
            "note": "raised PO 4471",
        },
    }
    app = workspace(
        "3_Decision_Center.py",
        user=manager,
        state={"rm_show": "All"},
        responses={
            RECOMMENDATIONS: {"count": 1, "recommendations": [decided], "decided_count": 1},
            DECISIONS: {"decisions": [], "count": 0, "accepted_profit": 0},
        },
    )
    rendered = markup(app)
    assert "Accepted" in rendered
    assert "raised PO 4471" in rendered


def test_the_ledger_never_calls_an_expectation_a_result(
    workspace: Any, texts: Any, markup: Any
) -> None:
    """Nothing in this platform measures what happened after somebody acted."""
    app = workspace(
        "3_Decision_Center.py",
        responses={
            RECOMMENDATIONS: {"count": 0, "recommendations": []},
            DECISIONS: {
                "decisions": [
                    {
                        "decision_key": "k",
                        "action": "accepted",
                        "category": "inventory",
                        "action_text": "Order 122 units",
                        "expected_profit": 42_350.0,
                        "decided_at": "2026-08-06T09:00:00+00:00",
                    }
                ],
                "count": 1,
                "accepted_profit": 42_350.0,
            },
        },
    )
    combined = markup(app) + texts(app.caption)
    assert "expected" in combined.lower()
    assert "realised profit" not in combined.lower()


# ── The analyst ──────────────────────────────────────────────────────


def test_a_refusal_is_shown_as_an_answer_not_swallowed(workspace: Any, markup: Any) -> None:
    app = workspace(
        "4_AI_Analyst.py",
        responses={
            "/api/v1/analyst/ask": ApiError(
                status=422,
                title="Cannot answer",
                detail="No measure of advertising spend exists in this platform.",
            )
        },
    )
    app.chat_input[0].set_value("What is the ROI on our TikTok campaign?").run()
    rendered = markup(app)
    assert "Cannot answer" in rendered
    assert "advertising spend" in rendered


def test_what_the_analyst_did_not_check_is_shown_beside_what_it_did(
    workspace: Any, markup: Any
) -> None:
    app = workspace(
        "4_AI_Analyst.py",
        responses={
            "/api/v1/analyst/ask": {
                "capability": "investigate",
                "headline": "Revenue fell 8.1%.",
                "facts": [{"text": "Northeast accounts for 72%.", "certainty": "measured"}],
                "inferences": [],
                "checked": ["region", "category"],
                "not_checked": ["returns", "shipping"],
                "caveats": [],
                "follow_ups": [],
                "conversation": {"turns": []},
            },
            "/api/v1/nlq/ask": {},
        },
    )
    app.chat_input[0].set_value("Why did revenue fall?").run()
    rendered = markup(app)
    assert "Not checked" in rendered
    assert "returns" in rendered


def test_the_compiled_query_is_shown_with_its_bound_parameters(workspace: Any, texts: Any) -> None:
    """The placeholders are the proof: dates are bound, never interpolated."""
    app = workspace(
        "4_AI_Analyst.py",
        responses={
            "/api/v1/analyst/ask": {
                "capability": "answer",
                "headline": "ok",
                "facts": [],
                "inferences": [],
                "checked": [],
                "not_checked": [],
                "caveats": [],
                "follow_ups": [],
                "conversation": {"turns": []},
            },
            "/api/v1/nlq/ask": {
                "plan": {
                    "domain": "revenue",
                    "metrics": ["net_revenue"],
                    "dimensions": ["region"],
                    "confidence": 0.85,
                    "interpretation": "Reading this as net_revenue by region.",
                    "unresolved": [],
                },
                "compiled_sql": (
                    "SELECT region, sum(net_revenue) AS net_revenue\n"
                    "FROM analytics_semantic.v_mart_sales_daily\n"
                    "WHERE business_date >= ? AND business_date <= ?"
                ),
                "rows": [{"region": "Midwest", "net_revenue": 1_089_743}],
                "columns": ["region", "net_revenue"],
                "chart": {"type": "bar", "x": "region", "y": ["net_revenue"]},
                "explanation": {"summary": "5 regions returned."},
                "routed_to": "",
                "meta": {},
                "row_count": 1,
            },
        },
    )
    app.chat_input[0].set_value("show revenue by region").run()

    code = texts(app.code)
    assert "business_date >= ?" in code
    assert "analytics_semantic" in code
    assert "bound parameters" in texts(app.caption)


def test_an_unresolved_term_is_named_rather_than_guessed(workspace: Any, markup: Any) -> None:
    app = workspace(
        "4_AI_Analyst.py",
        responses={
            "/api/v1/analyst/ask": {
                "capability": "answer",
                "headline": "ok",
                "facts": [],
                "inferences": [],
                "checked": [],
                "not_checked": [],
                "caveats": [],
                "follow_ups": [],
                "conversation": {"turns": []},
            },
            "/api/v1/nlq/ask": {
                "plan": {"domain": "revenue", "confidence": 0.4, "unresolved": ["tiktok"]},
                "compiled_sql": "",
                "rows": [],
                "explanation": {},
                "meta": {},
            },
        },
    )
    app.chat_input[0].set_value("tiktok revenue").run()
    assert "tiktok" in markup(app)


# ── Drill-down ───────────────────────────────────────────────────────


def test_a_drill_path_descends_and_comes_back(workspace: Any) -> None:
    """A drill-down you can only descend is a trap: the most common thing an
    analyst does after drilling is compare against the branch beside it."""
    import streamlit as st  # noqa: PLC0415

    from retailmind_ui.components.drilldown import DrillPath  # noqa: PLC0415

    # AppTest gives the script run context these session-state reads need.
    app = workspace(
        "5_Sales_Intelligence.py",
        responses={
            "/api/v1/analytics/revenue/summary": {"totals": {}, "meta": {}},
            "/api/v1/analytics/revenue/breakdown": {"data": [], "meta": {}},
            "/api/v1/analytics/revenue/trend": {"series": []},
            "/api/v1/dashboard/profit": {"cards": []},
        },
    )
    assert not app.exception

    # The path object itself is pure state: exercised directly, not through
    # the widget, because the invariant is about the cursor and not the chrome.
    st.session_state.clear()
    path = DrillPath("unit", ["region", "category", "department"])
    assert path.current_level == "region"

    path.descend("Midwest")
    assert path.filters == [("region", "Midwest")]
    assert path.current_level == "category"
    assert path.as_params == {"region": "Midwest"}

    path.descend("Outerwear")
    assert path.current_level == "department"

    path.ascend(1)
    assert path.filters == [("region", "Midwest")]
    assert path.current_level == "category"

    path.reset()
    assert path.filters == []
    assert path.current_level == "region"


def test_the_bottom_of_a_hierarchy_is_a_state_not_an_error(workspace: Any) -> None:
    import streamlit as st  # noqa: PLC0415

    from retailmind_ui.components.drilldown import DrillPath  # noqa: PLC0415

    workspace(
        "5_Sales_Intelligence.py",
        responses={
            "/api/v1/analytics/revenue/summary": {"totals": {}, "meta": {}},
            "/api/v1/analytics/revenue/breakdown": {"data": [], "meta": {}},
            "/api/v1/analytics/revenue/trend": {"series": []},
            "/api/v1/dashboard/profit": {"cards": []},
        },
    )
    st.session_state.clear()
    path = DrillPath("unit_bottom", ["region"])
    path.descend("Midwest")
    assert path.current_level is None


def test_the_drill_filter_is_a_dimension_name_and_a_value(workspace: Any) -> None:
    """Nothing typed or clicked becomes part of a query string: the path is a
    registry dimension plus a value, and the API resolves or refuses it."""
    import streamlit as st  # noqa: PLC0415

    from retailmind_ui.components.drilldown import DrillPath  # noqa: PLC0415

    workspace(
        "5_Sales_Intelligence.py",
        responses={
            "/api/v1/analytics/revenue/summary": {"totals": {}, "meta": {}},
            "/api/v1/analytics/revenue/breakdown": {"data": [], "meta": {}},
            "/api/v1/analytics/revenue/trend": {"series": []},
            "/api/v1/dashboard/profit": {"cards": []},
        },
    )
    st.session_state.clear()
    path = DrillPath("unit_params", ["region", "category"])
    path.descend("Midwest'; DROP TABLE fct_sales--")

    name, value = path.filters[0]
    assert name in ("region", "category")
    assert value == "Midwest'; DROP TABLE fct_sales--"
    assert path.as_params == {"region": "Midwest'; DROP TABLE fct_sales--"}


# ── Smoke tests ──────────────────────────────────────────────────────


def test_command_center_loads_without_error(workspace: Any) -> None:
    """Verify Command Center workspace loads with minimal valid data."""
    app = workspace(
        "1_Command_Center.py",
        responses={
            "/api/v1/dashboard/executive": {
                "revenue": {
                    "cards": [
                        {
                            "key": "net_revenue",
                            "value": 100_000,
                            "direction": "up",
                            "change_pct": 0.05,
                        }
                    ],
                    "comparison_basis": "vs prior day",
                },
                "growth": {
                    "horizons": [
                        {
                            "horizon": "week",
                            "current_revenue": 700_000,
                            "change_pct": 0.03,
                            "days": 7,
                        }
                    ]
                },
                "alerts": {"alerts": [], "counts": {}},
                "top_products": [],
                "inventory_risk": [],
                "sections_unavailable": [],
            },
            "/api/v1/recommendations": {
                "recommendations": [],
                "count": 0,
                "by_category": {},
                "gross_profit_opportunity": 0,
                "net_profit_opportunity": 0,
                "capital_freed": 0,
                "categories_requested": [],
                "caveats": [],
                "meta": {},
            },
            "/api/v1/dashboard/revenue/trend": {"series": []},
        },
    )
    assert not app.exception

    # The greeting, wherever it lands among the rendered blocks.
    #
    # Not `app.markdown[0]`: `design.configure()` emits the global stylesheet
    # as the first `st.markdown` call, so indexing by position asserted
    # against a `<style>` block and failed for a reason that had nothing to do
    # with the greeting. Matching the greeting text itself is also stricter
    # than the old `"Good" in ...`, which any block containing that word
    # anywhere would have satisfied.
    rendered = [block.value for block in app.markdown]
    assert any(
        greeting in value
        for value in rendered
        for greeting in ("Good morning", "Good afternoon", "Good evening")
    ), "the Command Center greeting did not render"


def test_investigation_loads_without_error(workspace: Any) -> None:
    """Verify AI Investigation workspace loads with minimal valid data."""
    app = workspace(
        "2_AI_Investigation.py",
        responses={
            "/api/v1/rca/investigate": {
                "change": -5000,
                "relative_change": -0.05,
                "baseline_value": 100_000,
                "current_value": 95_000,
                "current": {"start": "2026-08-07", "end": "2026-08-13", "days": 7},
                "baseline": {"start": "2026-07-31", "end": "2026-08-06", "days": 7},
                "where": [],
                "why": [],
                "dimensions_investigated": ["region", "category", "channel"],
                "dimensions_unavailable": {},
                "explained_share": 0,
                "caveats": [],
                "meta": {},
            }
        },
    )
    assert not app.exception


def test_decision_center_loads_without_error(workspace: Any) -> None:
    """Verify Decision Center workspace loads with minimal valid data."""
    app = workspace(
        "3_Decision_Center.py",
        responses={
            "/api/v1/recommendations": {
                "recommendations": [],
                "count": 0,
                "by_category": {},
                "gross_profit_opportunity": 0,
                "net_profit_opportunity": 0,
                "capital_freed": 0,
                "categories_requested": [],
                "decided_count": 0,
                "categories_empty": {},
                "caveats": [],
                "meta": {},
            },
            "/api/v1/recommendations/decisions": {
                "decisions": [],
                "count": 0,
                "accepted_profit": 0,
            },
            "/api/v1/recommendations/calibration": {
                "total_measured_outcomes": 0,
                "total_pending_outcomes": 0,
                "total_failed_outcomes": 0,
                "overall_metrics": {
                    "sample_size": 0,
                    "is_statistically_significant": False,
                },
                "generator_performance": [],
                "best_performing_generators": [],
                "needs_calibration": [],
                "confidence_calibration": [],
                "horizon_breakdown": {},
                "limitations": [],
            },
        },
    )
    assert not app.exception


def test_ai_analyst_loads_without_error(workspace: Any) -> None:
    """Verify AI Analyst workspace loads with minimal valid data."""
    app = workspace(
        "4_AI_Analyst.py",
        responses={
            "/api/v1/analyst/ask": {
                "question": "What is net revenue?",
                "capability": "explain_kpi",
                "headline": "Net revenue is gross sales minus returns and discounts.",
                "facts": [],
                "inferences": [],
                "checked": [],
                "not_checked": [],
                "caveats": [],
                "follow_ups": [],
                "data": {},
                "meta": {},
            }
        },
        state={"rm_analyst_question": "What is net revenue?"},
    )
    assert not app.exception


def test_forecast_loads_without_error(workspace: Any) -> None:
    """Verify Forecast workspace loads with minimal valid data."""
    app = workspace(
        "9_Forecast_Intelligence.py",
        responses={
            FORECAST: FORECAST_BODY,
            ACCURACY: {
                "summary": {
                    "horizon_days": 14,
                    "mase": 0.74,
                    "wape": 0.11,
                    "beats_naive": True,
                    "sample_days": 42,
                }
            },
        },
    )
    assert not app.exception


def test_executive_briefing_loads_without_error(workspace: Any) -> None:
    """Verify Executive Briefing workspace loads with minimal valid data."""
    app = workspace(
        "11_Executive_Briefing.py",
        responses={
            "/api/v1/reports": {
                "title": "Retail Performance Review",
                "period_label": "28 days to 2026-08-13",
                "generated_at": "2026-08-14T10:00:00",
                "sections": [],
                "caveats": [],
                "meta": {},
            }
        },
    )
    assert not app.exception


def test_risk_center_loads_without_error(workspace: Any) -> None:
    """Verify Risk Center workspace loads with minimal valid data."""
    app = workspace(
        "10_Risk_Center.py",
        responses={
            "/api/v1/notifications": {
                "notifications": [],
                "unread_count": 0,
                "total_count": 0,
            },
            "/api/v1/inventory/supplier-risk": {"data": []},
            "/api/v1/inventory/stockout-risk": {"data": []},
        },
    )
    assert not app.exception


def test_admin_loads_without_error(workspace: Any) -> None:
    """Verify Admin workspace loads with minimal valid data."""
    app = workspace(
        "12_Admin.py",
        responses={
            "/api/v1/users": {"users": [], "count": 0},
            "/api/v1/roles": {"roles": [], "count": 0},
        },
    )
    assert not app.exception
