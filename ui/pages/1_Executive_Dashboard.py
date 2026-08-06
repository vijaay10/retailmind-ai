"""Executive dashboard — the whole business on one screen."""

from typing import Any

import streamlit as st

from retailmind_ui import components as ui
from retailmind_ui import session, theme
from retailmind_ui.api import ApiError
from retailmind_ui.formatting import day, number

theme.configure("Executive Dashboard")
client = session.require("analytics.revenue.read")

ui.page_header(
    "Executive Dashboard",
    "Every figure is served by the surface that owns it. Nothing here is recomputed.",
)

try:
    overview = client.get("/api/v1/dashboard/executive")
except ApiError as error:
    ui.error(str(error))
    st.stop()

# The endpoint names the sections a role could not see rather than omitting
# them, so a reader can tell "no alerts" from "no access to alerts".
withheld: list[str] = overview.get("sections_unavailable") or []

revenue: dict[str, Any] = overview.get("revenue") or {}
st.caption(
    f"Business date {day(overview.get('business_date'))} — the latest date in the warehouse, "
    f"not today's clock date. Compared with {revenue.get('comparison_basis', 'the prior period')}."
)

ui.kpi_row(ui.cards(revenue.get("cards") or []))

left, right = st.columns([3, 2])

with left:
    ui.section("Growth", "The same revenue, read over three horizons.")
    horizons = overview.get("growth", {}).get("horizons") or []
    ui.kpi_row(
        [
            {
                "label": str(row.get("horizon", "")).title(),
                "value": number(row.get("current_revenue"), "currency"),
                "delta": (
                    f"{float(row['change_pct']):+.1%}"
                    if row.get("change_pct") is not None
                    else None
                ),
                "help": f"Against the {row.get('days')} days before.",
            }
            for row in horizons
        ],
        columns=3,
    )

    ui.section("Top products")
    ui.table(overview.get("top_products") or [], empty_reason="No sales in the period.")

with right:
    ui.section("Open alerts")
    alerts = overview.get("alerts", {}).get("alerts") or []
    counts = overview.get("alerts", {}).get("counts") or {}
    if counts:
        st.caption(" · ".join(f"{count} {name}" for name, count in counts.items()))

    if alerts:
        for alert in alerts[:5]:
            with st.container(border=True):
                icon = ui.SEVERITY_STYLE.get(str(alert.get("severity")), "⬜")
                st.markdown(f"**{icon} {alert.get('metric_label', '')}**")
                scope = ", ".join(f"{k}={v}" for k, v in (alert.get("scope") or {}).items())
                st.caption(scope or "whole business")
                st.caption(
                    f"Observed {number(alert.get('observed'), 'currency')} against an expected "
                    f"{number(alert.get('expected_low'), 'currency')}–"
                    f"{number(alert.get('expected_high'), 'currency')}."
                )
                # Null when narration was unavailable — an absent explanation is
                # shown as absent rather than papered over with the raw numbers.
                if alert.get("narration"):
                    st.caption(alert["narration"])
    else:
        ui.empty("Nothing is outside its expected band today.", what="No open alerts")

ui.section("Inventory at risk")
ui.table(
    overview.get("inventory_risk") or [],
    empty_reason="No category-region is carrying a material stockout rate.",
)

ui.section("Recommended actions")
ui.action_cards(overview.get("recommendations", {}).get("recommendations") or [])

if withheld:
    ui.caveats(
        [f"{name} — not included in your role" for name in withheld],
        title="Sections your role cannot see",
    )
