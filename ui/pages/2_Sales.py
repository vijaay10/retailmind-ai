"""Sales — revenue, margin, and where it came from."""

from datetime import timedelta

import streamlit as st

from retailmind_ui import components as ui
from retailmind_ui import session, theme
from retailmind_ui.api import ApiError
from retailmind_ui.formatting import number

theme.configure("Sales")
client = session.require("analytics.revenue.read")

ui.page_header("Sales", "Revenue and margin, cut by the dimensions the registry declares.")

#: Metric and dimension names, never expressions. The API resolves a name
#: through its registry or refuses it; nothing typed here reaches a query.
HEADLINES = (
    ("net_revenue", "Net revenue", "currency"),
    ("units_sold", "Units", ""),
    ("orders", "Orders", ""),
    ("aov", "Average order value", "currency"),
)

controls = st.columns([2, 2, 2, 2])
end = controls[0].date_input("Period end", value=session.data_date())
days = controls[1].slider("Days", 7, 180, 28)
dimension = controls[2].selectbox("Break down by", ["region", "category", "department", "store"])
metric = controls[3].selectbox("Metric", ["net_revenue", "units_sold", "orders", "aov"])

start = end - timedelta(days=days - 1)
window = {"start_date": start.isoformat(), "end_date": end.isoformat()}

try:
    summary = client.get("/api/v1/analytics/revenue/summary", **window)
    breakdown = client.get(
        "/api/v1/analytics/revenue/breakdown",
        metrics=metric,
        dimensions=dimension,
        page_size=25,
        **window,
    )
    trend = client.get("/api/v1/analytics/revenue/trend", metrics="net_revenue", **window)
except ApiError as error:
    ui.error(str(error))
    st.stop()

totals = summary.get("totals") or {}
ui.kpi_row(
    [{"label": name, "value": number(totals.get(key), unit)} for key, name, unit in HEADLINES]
)

ui.section("Trend")
ui.chart(
    ui.series_rows(trend.get("series") or []),
    x="business_date",
    y=["net_revenue"],
    kind="line",
    rationale="A line: dates have a natural order, and the gaps between them are equal.",
)

ui.section(f"By {dimension}")
rows = breakdown.get("data") or []
ui.chart(
    rows,
    x=dimension,
    y=[metric],
    kind="bar",
    rationale="Bars, not a line — these categories have no inherent order.",
)
ui.table(rows, empty_reason=f"No {dimension} recorded any activity in this window.")

caveats = [
    "Periods compare like for like only when they are the same length; "
    "day-of-week composition is not adjusted for.",
]
if (breakdown.get("meta") or {}).get("truncated"):
    caveats.append(
        "This breakdown was truncated at 25 rows, so the chart is the top of "
        "the distribution rather than all of it."
    )
if metric == "aov":
    # AOV is a ratio: the average of per-region averages is not the overall
    # average, and the API recomputes it at each grain for exactly that reason.
    caveats.append(
        "Average order value is a ratio, recomputed at each grain. The values "
        "below do not sum to the headline figure, and averaging them would not "
        "reproduce it either."
    )

ui.caveats(caveats)
ui.provenance(breakdown.get("meta") or {})
