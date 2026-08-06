"""Forecast — what is expected, and how much to trust it."""

import streamlit as st

from retailmind_ui import components as ui
from retailmind_ui import session, theme
from retailmind_ui.api import ApiError
from retailmind_ui.formatting import number

theme.configure("Forecast")
client = session.require("forecasts.read")

ui.page_header(
    "Forecast",
    "Published forecasts, each shown with the measured accuracy of the model behind it.",
)

target = st.selectbox("Target", ["revenue", "sales", "demand", "inventory", "profit"])

try:
    body = client.get(f"/api/v1/forecasts/{target}", limit=200)
    accuracy = client.get("/api/v1/forecasts/meta/accuracy")
except ApiError as error:
    ui.error(str(error))
    st.stop()

rows = body.get("data") or []
if not rows:
    ui.empty(
        "No forecast has been published for this target. The training job has "
        "either not run or found the series too short to fit.",
        what="Nothing forecast",
    )
    ui.caveats(body.get("caveats") or [])
    st.stop()

mase = next((r.get("model_mase") for r in rows if r.get("model_mase") is not None), None)
wape = next((r.get("model_wape") for r in rows if r.get("model_wape") is not None), None)

ui.kpi_row(
    [
        {"label": "Horizon", "value": f"{body.get('horizon_days', 0)} days"},
        {
            "label": "Total forecast",
            "value": number(sum(float(r.get("forecast") or 0) for r in rows), "currency"),
        },
        {"label": "Model WAPE", "value": number(wape, "rate") if wape is not None else "—"},
        {
            "label": "MASE",
            "value": number(mase, "ratio") if mase is not None else "—",
            "help": "Below 1.0 beats assuming the same weekday repeats.",
        },
    ]
)

if mase is not None and float(mase) >= 1.0:
    st.warning(
        f"This model's MASE is {float(mase):.2f}. It does not beat a seasonal-naive "
        "baseline, so the forecast carries no more information than a calendar. "
        "Replenishment planned on it is planned on a number with no demonstrated skill."
    )

ui.section("Forecast with prediction interval")
ui.chart(
    rows,
    x="business_date",
    y=["forecast", "forecast_lower", "forecast_upper"],
    kind="line",
    rationale=(
        "Bands are empirical — built from how wrong this model has actually been at each "
        "horizon — and widened so their coverage holds in small samples."
    ),
)
ui.table(rows, height=320)

ui.section("Accuracy scoreboard")
models = accuracy.get("models") or []
ui.table(models, empty_reason="No model has been scored yet.")
ui.caveats(accuracy.get("caveats") or [])
ui.caveats(body.get("caveats") or [])
