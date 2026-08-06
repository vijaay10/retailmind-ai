"""Recommendations — what to do, what it is worth, and what it risks."""

import streamlit as st

from retailmind_ui import components as ui
from retailmind_ui import session, theme
from retailmind_ui.api import ApiError
from retailmind_ui.formatting import number

theme.configure("Recommendations")
client = session.require("recommendations.read")

ui.page_header(
    "Recommendations",
    "Ranked by expected profit net of what each action risks — not by headline value.",
)

categories = st.multiselect(
    "Categories",
    ["inventory", "pricing", "promotion", "store", "marketing", "customer", "supplier"],
)

try:
    body = client.get("/api/v1/recommendations", categories=categories or None, limit=20)
except ApiError as error:
    ui.error(str(error))
    st.stop()

ui.kpi_row(
    [
        {"label": "Actions", "value": str(body.get("count", 0))},
        {
            "label": "Profit opportunity",
            "value": number(body.get("net_profit_opportunity"), "currency"),
            "help": "Net of overlapping actions, which chase the same pounds.",
        },
        {
            "label": "If counted separately",
            "value": number(body.get("gross_profit_opportunity"), "currency"),
            "help": "The gross figure double-promises overlapping actions.",
        },
        {
            "label": "Capital freed",
            "value": number(body.get("capital_freed"), "currency"),
            "help": "Working capital. Deliberately not added to profit.",
        },
    ]
)

gross = float(body.get("gross_profit_opportunity") or 0)
net = float(body.get("net_profit_opportunity") or 0)
if gross > net * 1.05 and net:
    st.info(
        f"The gap between {gross:,.0f} and {net:,.0f} is overlap: several of these "
        "actions chase the same pounds. Plan against the net figure."
    )

ui.recommendations(body.get("recommendations") or [])

empty_categories = body.get("categories_empty") or {}
if empty_categories:
    ui.caveats(
        [f"{name}: {reason}" for name, reason in empty_categories.items()],
        title="Categories that produced nothing",
    )

ui.caveats(body.get("caveats") or [])
