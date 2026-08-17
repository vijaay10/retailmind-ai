"""Forecast Intelligence — what is expected, and whether to believe it.

**The interval comes first.** A point forecast is the convenient part of a
model's output and the interval is the honest one, so the band is drawn filled
and the line dotted over it. A chart that draws the line boldly and the
interval as two faint edges invites planning against a number the model never
promised.

**A model that does not beat a seasonal-naive baseline is flagged, not
hidden.** MASE at or above 1.0 means the forecast carries no more information
than a calendar — that is a fact about the model, and replenishment planned on
it is planned on nothing.
"""

from typing import Any

import streamlit as st

from retailmind_ui import charts, dashboards, design, session
from retailmind_ui import components as ui
from retailmind_ui.api import ApiError
from retailmind_ui.design import SEMANTIC
from retailmind_ui.formatting import number

design.configure("Forecast Intelligence", icon="◐")
client = session.require("forecasts.read")

TARGETS = ["revenue", "sales", "demand", "inventory", "profit"]

ui.workspace_header(
    "Forecast Intelligence",
    eyebrow="Outlook",
    summary="Published forecasts, their intervals, and the measured accuracy behind them.",
)

target = st.selectbox("Target", TARGETS)

try:
    body: dict[str, Any] = client.get(f"/api/v1/forecasts/{target}", limit=200)
    accuracy: dict[str, Any] = client.get("/api/v1/forecasts/meta/accuracy")
except ApiError as error:
    ui.workspace_error(error, what="The forecast surface did not respond")
    st.stop()

rows = body.get("data") or []

if not rows:
    ui.empty(
        "No forecast has been published for this target. The training job has either "
        "not run or found the series too short to fit.",
        what="Nothing forecast",
    )
    ui.caveats(body.get("caveats") or [])
    st.stop()

mase = next((row.get("model_mase") for row in rows if row.get("model_mase") is not None), None)
wape = next((row.get("model_wape") for row in rows if row.get("model_wape") is not None), None)
total = sum(float(row.get("forecast") or 0) for row in rows)

ui.stat_row(
    [
        {"label": "Horizon", "value": f"{body.get('horizon_days', 0)} days"},
        {"label": "Total forecast", "value": number(total, "currency")},
        {
            "label": "WAPE",
            "value": number(wape, "rate") if wape is not None else "—",
            "note": "volume-weighted out-of-sample error",
        },
        {
            "label": "MASE",
            "value": number(mase, "ratio") if mase is not None else "—",
            "note": "below 1.0 beats assuming the same weekday repeats",
            "accent": (
                SEMANTIC["critical"]
                if mase is not None and float(mase) >= 1.0
                else SEMANTIC["positive"]
            ),
        },
    ]
)

if mase is not None and float(mase) >= 1.0:
    ui.caveats(
        [
            f"This model's MASE is {float(mase):.2f}. It does not beat a seasonal-naive "
            "baseline, so the forecast carries no more information than a calendar. "
            "Replenishment planned on it is planned on a number with no demonstrated skill.",
        ],
        title="This model has not earned trust",
        tone=SEMANTIC["critical"],
    )

ui.section("Forecast with prediction interval")
figure = charts.forecast_band(rows, height=340)
if figure is not None:
    st.plotly_chart(figure, width="stretch", config={"displayModeBar": False})
    st.caption(
        "The band is empirical — built from how wrong this model has actually been at "
        "each horizon — and widened so its coverage holds in small samples."
    )

left, right = st.columns([1.2, 1])

with left:
    ui.section("What is pushing it", "Named effects, from the model's own explanation.")
    try:
        explain = client.get(f"/api/v1/forecasts/{target}/explain")
        effects = explain.get("data") or []
    except ApiError:
        effects = []

    if effects:
        figure = charts.evidence_effects(effects, label="feature", value="effect")
        if figure is not None:
            st.plotly_chart(figure, width="stretch", config={"displayModeBar": False})
        st.caption(
            "Effects are the model's own decomposition against its baseline level — "
            "a weekday profile, a trend term. They explain the shape, not the cause."
        )
    else:
        ui.empty("This model publishes no per-feature explanation.", what="No decomposition")

with right:
    ui.section("Accuracy scoreboard", "Every model that has been scored.")
    ui.table(accuracy.get("models") or [], empty_reason="No model has been scored yet.", height=260)
    if accuracy.get("best_model"):
        st.caption(f"Best on record: {accuracy['best_model']}")

ui.section(
    "Composed outlook",
    "Actual and forecast share a time axis and pan together; the effects panel "
    "has its own, because it is not a series.",
)
try:
    recent = client.get("/api/v1/dashboard/revenue/trend", days=30).get("series") or []
except ApiError:
    recent = []

st.plotly_chart(
    dashboards.outlook_dashboard(
        actuals=recent,
        forecast=rows,
        effects=effects,
        title=f"{target.title()} outlook · {body.get('horizon_days', 0)} days",
    ),
    width="stretch",
    config={"displayModeBar": False},
)

ui.section("Forecast rows")
ui.table(rows, height=320)

ui.caveats(accuracy.get("caveats") or [], title="How accuracy was measured")
ui.caveats(body.get("caveats") or [])
