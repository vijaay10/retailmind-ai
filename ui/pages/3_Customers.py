"""Customers — segments, retention, and who is drifting away."""

from typing import Any

import streamlit as st

from retailmind_ui import components as ui
from retailmind_ui import session, theme
from retailmind_ui.api import ApiError

theme.configure("Customers")
client = session.require("analytics.customer.read")

ui.page_header(
    "Customers",
    "Population aggregates only. No endpoint here can project an individual customer.",
)


def load(path: str, **params: object) -> dict[str, Any]:
    try:
        return client.get(path, **params)
    except ApiError as error:
        ui.error(str(error))
        return {}


tabs = st.tabs(["Segments", "Lifetime value", "Retention", "Churn risk", "VIPs", "Journey"])

with tabs[0]:
    body = load("/api/v1/customers/segments")
    ui.table(body.get("data") or [], empty_reason="No segments above the reporting floor.")
    privacy = body.get("privacy") or {}
    if privacy.get("note"):
        ui.caveats([privacy["note"]], title="Privacy suppression")

with tabs[1]:
    body = load("/api/v1/customers/lifetime-value")
    ui.table(body.get("data") or [])
    ui.caveats(
        [
            "Historic lifetime value is arithmetic over what happened. The "
            "12-month projection extrapolates observed cadence — it carries a "
            "confidence grade because annualising a fortnight of history is a "
            "guess wearing a number's clothes.",
        ]
    )

with tabs[2]:
    body = load("/api/v1/customers/retention", limit=200)
    ui.table(body.get("data") or [])
    ui.caveats(
        [
            "Cohorts stop at the observation edge. A cohort acquired two weeks "
            "ago has no week-8 retention yet, and a zero there would draw a "
            "cliff that never happened.",
        ]
    )

with tabs[3]:
    body = load("/api/v1/customers/churn-risk")
    bands = body.get("bands") or []
    if bands:
        ui.kpi_row(
            [
                {
                    "label": "Value at risk",
                    "value": f"{float(body.get('total_value_at_risk') or 0):,.0f}",
                },
                {
                    "label": "VIP value at risk",
                    "value": f"{float(body.get('vip_value_at_risk') or 0):,.0f}",
                    "help": "Expensive to replace, and still reachable.",
                },
            ],
            columns=2,
        )
    ui.chart(bands, x="churn_risk_band", y=["value_at_risk"], kind="bar")
    ui.table(bands)
    ui.caveats(
        [
            "Risk is a band derived from how many of a customer's own purchase "
            "cycles have elapsed unfulfilled — not a probability. Calling it "
            "'68% likely to churn' would imply a calibration nobody measured.",
        ]
    )

with tabs[4]:
    body = load("/api/v1/customers/vip")
    ui.table(body.get("data") or [])

with tabs[5]:
    body = load("/api/v1/customers/journey")
    stages = body.get("stages") or []
    ui.chart(stages, x="lifecycle_stage", y=["customers"], kind="bar")
    ui.table(stages)
