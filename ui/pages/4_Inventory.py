"""Inventory — availability, excess, and the supply behind both."""

from typing import Any

import streamlit as st

from retailmind_ui import components as ui
from retailmind_ui import session, theme
from retailmind_ui.api import ApiError
from retailmind_ui.formatting import number

theme.configure("Inventory")
client = session.require("analytics.inventory.read")

ui.page_header("Inventory", "A point-in-time picture: the current stock position, not a series.")


def load(path: str, **params: object) -> dict[str, Any]:
    try:
        return client.get(path, **params)
    except ApiError as error:
        ui.error(str(error))
        return {}


health = load("/api/v1/inventory/warehouse-health")
rows = health.get("data") or []
if rows:
    ui.kpi_row(
        [
            {
                "label": "Network health",
                "value": number(health.get("network_health_score"), "", 1),
                "help": "Position-weighted composite. A ranking device, not a diagnosis.",
            },
            {"label": "Weakest region", "value": str(health.get("weakest") or "—")},
            {"label": "Regions", "value": str(len(rows))},
        ],
        columns=3,
    )

tabs = st.tabs(["Health", "Stockout risk", "Reorder", "Overstock", "Aging", "Suppliers", "ABC"])

with tabs[0]:
    ui.chart(
        rows,
        x="region",
        y=["health_score"],
        kind="bar",
        rationale="Bars: regions have no inherent order.",
    )
    ui.table(rows)
    ui.caveats(
        [
            "The composite score ranks regions for attention. The five "
            "components say what to fix — a region scoring 74 on collapsed "
            "availability needs a different response from one scoring 74 on "
            "trapped capital.",
        ]
    )

with tabs[1]:
    body = load("/api/v1/inventory/stockout-risk", limit=50)
    ui.kpi_row(
        [
            {
                "label": "At risk",
                "value": str(body.get("at_risk_positions", 0)),
                "help": "Will run out before a replenishment could physically arrive.",
            },
            {"label": "Already out", "value": str(body.get("stockout_positions", 0))},
        ],
        columns=2,
    )
    ui.table(body.get("data") or [], empty_reason="No positions at risk.")
    ui.caveats(
        [
            "Days until stockout is a projection, not a forecast: it assumes "
            "demand continues as observed and says nothing about a promotion "
            "next week.",
        ]
    )

with tabs[2]:
    body = load("/api/v1/inventory/reorder", limit=50)
    ui.kpi_row(
        [
            {"label": "Lines due", "value": str(body.get("lines_due", 0))},
            {"label": "Units to order", "value": number(body.get("total_order_qty"), "", 0)},
            {"label": "Revenue at risk", "value": number(body.get("revenue_at_risk"), "currency")},
        ],
        columns=3,
    )
    ui.table(body.get("data") or [])
    if body.get("method"):
        ui.caveats([f"Method: {body['method']}"], title="How the quantities were derived")

with tabs[3]:
    body = load("/api/v1/inventory/overstock")
    ui.kpi_row(
        [
            {"label": "Excess value", "value": number(body.get("excess_value"), "currency")},
            {"label": "Overstocked", "value": str(body.get("overstocked_positions", 0))},
            {
                "label": "Dead stock",
                "value": str(body.get("dead_stock_positions", 0)),
                "help": "No demand at all. Will not clear without markdown.",
            },
        ],
        columns=3,
    )
    ui.table(body.get("data") or [])

with tabs[4]:
    body = load("/api/v1/inventory/aging")
    ui.chart(body.get("data") or [], x="aging_bucket", y=["inventory_value"], kind="bar")
    ui.table(body.get("data") or [])

with tabs[5]:
    body = load("/api/v1/inventory/supplier-risk")
    ui.table(body.get("data") or [])
    if body.get("below_evidence_floor"):
        ui.caveats(
            [
                f"{body['below_evidence_floor']} supplier(s) have fewer than "
                f"{body.get('evidence_floor', 20)} received lines. Their rates are "
                "arithmetic, not evidence — shown rather than hidden, because an "
                "unmeasured vendor is not the same as one with no orders.",
            ]
        )

with tabs[6]:
    body = load("/api/v1/inventory/abc")
    ui.chart(body.get("data") or [], x="abc_class", y=["revenue"], kind="bar")
    ui.table(body.get("data") or [])
    ui.caveats(
        [
            "Classes are cut on cumulative revenue share within category. "
            "Classifying across the whole assortment starves every smaller "
            "category of service level until it dies.",
        ]
    )
