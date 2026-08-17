"""Inventory Intelligence — a point-in-time position, never a series.

Stock is a **snapshot**: on-hand units on a given date. Summing snapshots across
days counts the same unit repeatedly, which is why nothing on this screen adds
inventory over time, and why the aging and ABC surfaces read as distributions
rather than trends.
"""

from typing import Any

import streamlit as st

from retailmind_ui import charts, design, session
from retailmind_ui import components as ui
from retailmind_ui.api import ApiError
from retailmind_ui.design import SEMANTIC
from retailmind_ui.formatting import number

design.configure("Inventory Intelligence", icon="■")
client = session.require("analytics.inventory.read")

ui.workspace_header(
    "Inventory Intelligence",
    eyebrow="Inventory domain",
    summary="Availability, excess, ageing, and the supply behind both.",
)


def load(path: str, **params: object) -> dict[str, Any]:
    try:
        return client.get(path, **params)
    except ApiError as error:
        ui.workspace_error(error, what=f"{path.rsplit('/', 1)[-1]} did not load")
        return {}


health = load("/api/v1/inventory/warehouse-health")
stockout = load("/api/v1/inventory/stockout-risk", limit=50)
overstock = load("/api/v1/inventory/overstock")

ui.stat_row(
    [
        {
            "label": "Network health",
            "value": number(health.get("network_health_score"), "", 1),
            "note": "position-weighted composite — a ranking device, not a diagnosis",
        },
        {
            "label": "Weakest region",
            "value": str(health.get("weakest") or "—"),
        },
        {
            "label": "At risk",
            "value": str(stockout.get("at_risk_positions", 0)),
            "note": "will run out before replenishment could arrive",
            "accent": SEMANTIC["warning"],
        },
        {
            "label": "Excess value",
            "value": number(overstock.get("excess_value"), "currency"),
            "note": f"{overstock.get('dead_stock_positions', 0)} dead-stock positions",
            "accent": SEMANTIC["capital"],
        },
    ]
)

tabs = st.tabs(["Availability", "Reorder", "Excess", "Ageing", "Suppliers", "ABC", "Network"])

with tabs[0]:
    rows = stockout.get("data") or []
    ui.analyst_grid(
        rows,
        pinned="sku",
        height=380,
        empty_reason="No positions are projected to run out before replenishment.",
    )
    ui.caveats(
        [
            "Days until stockout is a projection, not a forecast: it assumes demand "
            "continues as observed and says nothing about a promotion next week.",
        ]
    )

with tabs[1]:
    body = load("/api/v1/inventory/reorder", limit=50)
    ui.stat_row(
        [
            {"label": "Lines due", "value": str(body.get("lines_due", 0))},
            {"label": "Units to order", "value": number(body.get("total_order_qty"), "", 0)},
            {
                "label": "Revenue at risk",
                "value": number(body.get("revenue_at_risk"), "currency"),
            },
        ],
        columns=3,
    )
    ui.analyst_grid(body.get("data") or [], pinned="sku", empty_reason="Nothing due.")
    if body.get("method"):
        ui.caveats([str(body["method"])], title="How the quantities were derived")

with tabs[2]:
    excess_rows = overstock.get("data") or []
    figure = charts.treemap(
        excess_rows,
        path=["category", "sku"] if any("sku" in row for row in excess_rows) else ["category"],
        value="excess_value",
        colour=SEMANTIC["capital"],
    )
    if figure is not None:
        st.plotly_chart(figure, width="stretch", config={"displayModeBar": False})
        st.caption(
            "Area is capital sitting still. Positions with no excess are absent rather "
            "than drawn at zero size — a treemap cannot render a zero, and a negative "
            "excess is not a thing."
        )
    ui.table(excess_rows, height=340, empty_reason="No overstocked positions.")
    ui.caveats(
        [
            "Excess value is capital, not loss. Clearing it releases cash and books a "
            "markdown; the two are reported separately for that reason.",
        ]
    )

with tabs[3]:
    body = load("/api/v1/inventory/aging")
    rows = body.get("data") or []
    figure = charts.ranked_bars(
        rows, label="aging_bucket", value="inventory_value", colour=SEMANTIC["capital"]
    )
    if figure is not None:
        st.plotly_chart(figure, width="stretch", config={"displayModeBar": False})
    ui.table(rows, empty_reason="No ageing buckets returned.")

with tabs[4]:
    body = load("/api/v1/inventory/supplier-risk")
    rows = body.get("data") or []
    figure = charts.scatter_quadrant(
        rows,
        x="otif_rate",
        y="avg_lead_time_days",
        label="supplier_name",
        size="ordered_value",
        height=360,
    )
    if figure is not None:
        st.plotly_chart(figure, width="stretch", config={"displayModeBar": False})
        st.caption(
            "On-time delivery against lead time, sized by value ordered. Quadrant "
            "lines sit at the medians — 'worse than our other suppliers' is the "
            "comparison being made, and a fixed origin puts everyone in one corner."
        )
    ui.table(rows, empty_reason="No supplier rows.")
    if body.get("below_evidence_floor"):
        ui.caveats(
            [
                f"{body['below_evidence_floor']} supplier(s) have fewer than "
                f"{body.get('evidence_floor', 20)} received lines. Their rates are "
                "arithmetic, not evidence — shown rather than hidden, because an "
                "unmeasured vendor is not the same as one with no orders.",
            ]
        )

with tabs[5]:
    body = load("/api/v1/inventory/abc")
    rows = body.get("data") or []
    figure = charts.ranked_bars(rows, label="abc_class", value="revenue")
    if figure is not None:
        st.plotly_chart(figure, width="stretch", config={"displayModeBar": False})
    ui.table(rows, empty_reason="No ABC classes returned.")
    ui.caveats(
        [
            "Classes are cut on cumulative revenue share within category. Classifying "
            "across the whole assortment starves every smaller category of service "
            "level until it dies.",
        ]
    )

with tabs[6]:
    rows = health.get("data") or []

    try:
        risk_matrix = (
            client.get(
                "/api/v1/analytics/inventory/breakdown",
                metrics="stockout_rate",
                dimensions="region,category",
                page_size=200,
            ).get("data")
            or []
        )
    except ApiError:
        risk_matrix = []

    figure = charts.matrix_heatmap(
        risk_matrix, row_key="region", column_key="category", value="stockout_rate"
    )
    if figure is not None:
        st.plotly_chart(figure, width="stretch", config={"displayModeBar": False})
        st.caption(
            "Stockout rate by region and category. A blank cell is a combination the "
            "estate does not carry, not a cell with perfect availability."
        )

    figure = charts.ranked_bars(
        rows, label="region", value="health_score", colour=SEMANTIC["positive"]
    )
    if figure is not None:
        st.plotly_chart(figure, width="stretch", config={"displayModeBar": False})
    ui.table(rows, empty_reason="No regions returned.")
    ui.caveats(
        [
            "The composite ranks regions for attention. Its five components say what "
            "to fix — a region scoring 74 on collapsed availability needs a different "
            "response from one scoring 74 on trapped capital.",
        ]
    )

ui.provenance(health.get("meta") or {})
