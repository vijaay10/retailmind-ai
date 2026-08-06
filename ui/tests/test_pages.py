"""What the pages must never do.

These are not smoke tests. Each one pins a way a competent-looking console
misleads the person reading it: dropping a qualification, drawing an empty
table for a failed call, showing a headline number that a later screen
contradicts, or trusting its own navigation as a security boundary.
"""

from datetime import date
from typing import Any

import pytest

from retailmind_ui.api import ApiError

FORECAST = "/api/v1/forecasts/revenue"
ACCURACY = "/api/v1/forecasts/meta/accuracy"

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


# ── The permission gate ──────────────────────────────────────────────


def test_a_signed_out_visitor_gets_no_figures(page: Any, texts: Any) -> None:
    app = page("5_Forecast.py", user=None)
    assert "Sign in" in texts(app.warning)
    assert not app.metric


def test_a_role_without_the_permission_is_told_which_one(
    page: Any, texts: Any, ceo: dict[str, Any]
) -> None:
    """ "Access denied" leaves a user unable to ask for the right thing."""
    app = page("5_Forecast.py", user={**ceo, "permissions": ["analytics.revenue.read"]})
    assert "forecasts.read" in texts(app.error)
    assert not app.metric


def test_hiding_a_page_is_never_the_only_control(page: Any, ceo: dict[str, Any]) -> None:
    """A user who lacks the permission but reaches the URL anyway is stopped
    here too — the navigation only decides whether a link is drawn."""
    app = page("9_Alerts.py", user={**ceo, "permissions": []})
    assert app.error
    assert not app.dataframe


# ── Caveats survive to the screen ────────────────────────────────────


def test_caveats_render_inline_rather_than_behind_a_click(page: Any, texts: Any) -> None:
    """The single property this frontend exists to preserve.

    The API attaches caveats precisely because acting on the figure without
    them is the failure mode. A disclosure triangle is where a caveat goes to
    be ignored, so they must appear in the ordinary reading path.
    """
    app = page("5_Forecast.py", responses={FORECAST: FORECAST_BODY, ACCURACY: {"models": []}})

    rendered = texts(app.caption)
    assert "widen with horizon" in rendered
    assert "not represented here" in rendered
    assert not app.expander


def test_a_model_that_does_not_beat_a_baseline_says_so(page: Any, texts: Any) -> None:
    """MASE ≥ 1 means the forecast carries no more information than a
    calendar. Rendering it as an ordinary number invites replenishment
    planning on a series with no demonstrated skill."""
    weak = {
        **FORECAST_BODY,
        "data": [{**FORECAST_BODY["data"][0], "model_mase": 1.31}],
    }
    app = page("5_Forecast.py", responses={FORECAST: weak, ACCURACY: {"models": []}})

    warning = texts(app.warning)
    assert "1.31" in warning
    assert "seasonal-naive" in warning


def test_a_healthy_model_is_not_second_guessed(page: Any, texts: Any) -> None:
    app = page("5_Forecast.py", responses={FORECAST: FORECAST_BODY, ACCURACY: {"models": []}})
    assert "seasonal-naive" not in texts(app.warning)


# ── Absence is drawn, not omitted ────────────────────────────────────


def test_an_empty_result_gives_its_reason(page: Any, texts: Any) -> None:
    """ "No forecast" and "the training job never ran" look identical when a
    chart simply fails to appear, and they call for opposite responses."""
    app = page(
        "5_Forecast.py",
        responses={FORECAST: {"data": [], "caveats": []}, ACCURACY: {"models": []}},
    )
    assert "not run" in texts(app.info)


def test_a_failed_call_reports_the_outage_instead_of_drawing_a_blank_page(
    page: Any, texts: Any
) -> None:
    """A dashboard showing zero revenue because the API is unreachable is
    worse than one showing nothing at all."""
    app = page(
        "1_Executive_Dashboard.py",
        responses={
            "/api/v1/dashboard/executive": ApiError(
                status=0, title="Cannot reach the API", detail="connection refused"
            )
        },
    )
    assert "connection refused" in texts(app.error)
    assert not app.metric


# ── Numbers keep the meaning the API gave them ───────────────────────


def test_the_recommendation_total_is_the_net_and_the_overlap_is_explained(
    page: Any, texts: Any
) -> None:
    """The gross figure double-promises actions that chase the same pounds.
    Both are shown, and the difference is named rather than left to be found."""
    app = page(
        "6_Recommendations.py",
        responses={
            "/api/v1/recommendations": {
                "count": 3,
                "gross_profit_opportunity": 180_000,
                "net_profit_opportunity": 120_000,
                "capital_freed": 90_000,
                "recommendations": [],
                "caveats": [],
            }
        },
    )
    values = texts(app.metric)
    assert "120,000" in values
    assert "180,000" in values
    assert "overlap" in texts(app.info)


def test_capital_freed_is_never_folded_into_profit(page: Any) -> None:
    """Working capital released is not earnings, and a console that adds them
    reports a profit improvement that will not appear in any ledger."""
    app = page(
        "6_Recommendations.py",
        responses={
            "/api/v1/recommendations": {
                "count": 1,
                "gross_profit_opportunity": 100,
                "net_profit_opportunity": 100,
                "capital_freed": 5_000,
                "recommendations": [],
            }
        },
    )
    tiles = {tile.label: tile.value for tile in app.metric}
    assert tiles["Profit opportunity"] == "100"
    assert tiles["Capital freed"] == "5,000"


# ── The analyst ──────────────────────────────────────────────────────


def test_a_refusal_is_shown_as_an_answer_not_swallowed(page: Any, texts: Any) -> None:
    """The analyst refuses questions the platform cannot answer. A console
    that renders that as a generic failure teaches users the tool is flaky,
    when it was being careful."""
    app = page(
        "7_AI_Analyst.py",
        responses={
            "/api/v1/analyst/ask": ApiError(
                status=422,
                title="Cannot answer",
                detail="No measure of advertising spend exists in this platform.",
            )
        },
    )
    app.chat_input[0].set_value("What is the ROI on our TikTok campaign?").run()
    assert "advertising spend" in texts(app.warning)


def test_what_the_analyst_did_not_check_is_shown_beside_what_it_did(page: Any, texts: Any) -> None:
    """An answer that reports only what it examined leaves its silences
    unreadable — the reader cannot tell whether returns were fine or ignored."""
    app = page(
        "7_AI_Analyst.py",
        responses={
            "/api/v1/analyst/ask": {
                "capability": "investigate",
                "headline": "Revenue fell 8.1% against the prior period.",
                "facts": [{"text": "Northeast accounts for 72%.", "certainty": "measured"}],
                "inferences": [],
                "checked": ["region", "category"],
                "not_checked": ["returns", "shipping"],
                "caveats": [],
                "follow_ups": [],
                "conversation": {"turns": []},
            }
        },
    )
    app.chat_input[0].set_value("Why did revenue fall?").run()

    captions = texts(app.caption)
    assert "Not checked" in captions
    assert "returns" in captions


# ── Structure ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "name",
    [
        "1_Executive_Dashboard.py",
        "2_Sales.py",
        "3_Customers.py",
        "4_Inventory.py",
        "5_Forecast.py",
        "6_Recommendations.py",
        "7_AI_Analyst.py",
        "8_Reports.py",
        "9_Alerts.py",
        "10_Admin.py",
    ],
)
def test_every_page_survives_an_api_that_answers_nothing(page: Any, name: str, texts: Any) -> None:
    """Empty bodies everywhere. A page may show nothing; it may not crash,
    because the state it is least tested in is the one a new deployment is in.
    """
    app = page(name)
    assert not app.exception, texts(app.exception)


# ── Dates come from the data, not the clock ──────────────────────────


def test_period_controls_default_to_the_warehouse_date_not_today(page: Any) -> None:
    """A period ending "today" covers days the warehouse does not have, and
    the platform then reports a 100% collapse in revenue — a broken-looking
    console produced entirely by asking the wrong question."""
    app = page(
        "2_Sales.py",
        responses={
            "/api/v1/analytics/revenue/summary": {
                "totals": {"net_revenue": 1_000.0},
                "meta": {"freshness": "2026-07-21"},
            },
            "/api/v1/analytics/revenue/breakdown": {"data": [], "meta": {}},
            "/api/v1/analytics/revenue/trend": {"series": []},
        },
    )
    assert app.date_input[0].value == date(2026, 7, 21)


def test_the_analyst_is_asked_about_the_data_s_latest_day(page: Any) -> None:
    app = page(
        "7_AI_Analyst.py",
        responses={
            "/api/v1/analytics/revenue/summary": {"meta": {"freshness": "2026-07-21"}},
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
        },
    )
    app.chat_input[0].set_value("Show revenue by region").run()
    assert app.session_state["rm_data_date"] == date(2026, 7, 21)
