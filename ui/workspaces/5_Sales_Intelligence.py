"""Sales Intelligence — revenue and margin, cut by the governed dimensions.

Every metric and dimension offered here is a **name** the metric registry
recognises. Nothing typed on this screen becomes part of a query; the console
sends names, the API resolves them or refuses. That is what makes the whole
surface injection-proof rather than carefully escaped.
"""

from datetime import timedelta
from typing import Any

import streamlit as st

from retailmind_ui import charts, dashboards, design, session
from retailmind_ui import components as ui
from retailmind_ui.api import ApiError
from retailmind_ui.components import drilldown
from retailmind_ui.design import SEMANTIC
from retailmind_ui.formatting import number, signed_rate

design.configure("Sales Intelligence", icon="▲")
client = session.require("analytics.revenue.read")

HEADLINES = (
    ("net_revenue", "Net revenue", "currency"),
    ("units_sold", "Units", ""),
    ("orders", "Orders", ""),
    ("aov", "Average order value", "currency"),
)

ui.workspace_header(
    "Sales Intelligence",
    eyebrow="Revenue domain",
    summary="Totals, trend, and composition over one governed window.",
)

controls = st.columns([2, 2, 2, 2])
end = controls[0].date_input("Period end", value=session.data_date())
days = controls[1].slider("Days", 7, 180, 28)
dimension = controls[2].selectbox("Break down by", ["region", "category", "department", "store"])
metric = controls[3].selectbox("Measure", ["net_revenue", "units_sold", "orders", "aov"])

start = end - timedelta(days=days - 1)
window = {"start_date": start.isoformat(), "end_date": end.isoformat()}

try:
    summary: dict[str, Any] = client.get("/api/v1/analytics/revenue/summary", **window)
    breakdown: dict[str, Any] = client.get(
        "/api/v1/analytics/revenue/breakdown",
        metrics=metric,
        dimensions=dimension,
        page_size=25,
        **window,
    )
    trend: dict[str, Any] = client.get(
        "/api/v1/analytics/revenue/trend", metrics="net_revenue", **window
    )
    profit: dict[str, Any] = client.get("/api/v1/dashboard/profit", period_days=days)
except ApiError as error:
    ui.failure(str(error), what="The revenue domain did not respond")
    st.stop()

totals = summary.get("totals") or {}
ui.stat_row(
    [{"label": name, "value": number(totals.get(key), unit)} for key, name, unit in HEADLINES]
)

# Margin sits beside revenue because a revenue number read alone is how a
# discount-driven quarter gets celebrated.
margin_cards = profit.get("cards") or []
if margin_cards:
    ui.stat_row(
        [
            {
                "label": card.get("label", ""),
                "value": number(card.get("value"), str(card.get("unit", ""))),
                "delta": signed_rate(card.get("change_pct")),
                "direction": str(card.get("direction", "")),
            }
            for card in margin_cards
        ]
    )

ui.section("Trend", f"{start:%d %b} → {end:%d %b}")
series = [
    {"business_date": point.get("business_date"), **(point.get("values") or {})}
    for point in (trend.get("series") or [])
]
figure = charts.trend(series, x="business_date", y="net_revenue", height=300)
if figure is not None:
    st.plotly_chart(figure, width="stretch", config={"displayModeBar": False})
    st.caption("A line: dates have a natural order and the gaps between them are equal.")
else:
    ui.empty("No daily rows in this window.")

rows = breakdown.get("data") or []

left, right = st.columns([1.3, 1])

with left:
    ui.section(f"By {dimension}", "Bars, not a line — these categories have no inherent order.")
    bars = charts.ranked_bars(rows, label=dimension, value=metric, colour=SEMANTIC["accent"])
    if bars is not None:
        st.plotly_chart(bars, width="stretch", config={"displayModeBar": False})
    else:
        ui.empty(f"No {dimension} recorded activity in this window.")

with right:
    ui.section("Rows", "The same figures, exactly as returned.")
    ui.table(rows, height=320, empty_reason=f"No {dimension} rows.")

# ── Composition ──────────────────────────────────────────────────────

ui.section(
    "Composition",
    "Where the total actually sits. Area is the honest encoding for share of a whole.",
)

try:
    nested = (
        client.get(
            "/api/v1/analytics/revenue/breakdown",
            metrics="net_revenue",
            dimensions="region,category",
            page_size=200,
            **window,
        ).get("data")
        or []
    )
except ApiError:
    nested = []

composition, matrix = st.columns([1.2, 1])

with composition:
    figure = charts.treemap(nested, path=["region", "category"], value="net_revenue")
    if figure is not None:
        st.plotly_chart(figure, width="stretch", config={"displayModeBar": False})
        st.caption(
            "Region, then category within it. A treemap needs an additive measure — "
            "net revenue sums, so parents are genuinely the sum of their children. "
            "Average order value would not, and is deliberately not offered here."
        )
    else:
        ui.empty("The two-dimension breakdown returned nothing to nest.")

with matrix:
    figure = charts.matrix_heatmap(
        nested, row_key="region", column_key="category", value="net_revenue"
    )
    if figure is not None:
        st.plotly_chart(figure, width="stretch", config={"displayModeBar": False})
        st.caption(
            "The same figures as a matrix. Blank cells are combinations with no rows "
            "at all — not zero revenue, which would colour as an underperformer."
        )

# ── One figure ───────────────────────────────────────────────────────

ui.section(
    "Composed view",
    "The same three panels as a single figure — one layout, one export, "
    "for a deck or a report page.",
)
st.plotly_chart(
    dashboards.revenue_dashboard(
        trend=series,
        breakdown=rows,
        matrix=nested,
        dimension=dimension,
        measure=metric,
        title=f"{metric.replace('_', ' ').title()} · {start:%d %b} → {end:%d %b}",
    ),
    width="stretch",
    config={"displayModeBar": False},
)
st.caption(
    "Panels that came back empty keep their cell and say so. A grid that "
    "reflowed around a failed query would change the position and size of "
    "every other panel, and two exports of the same dashboard would stop "
    "being comparable."
)

# ── Drill-down ───────────────────────────────────────────────────────

ui.section(
    "Drill-down",
    "Click a bar to descend. Each level is a governed dimension, not a query.",
    accent=SEMANTIC["ai"],
)

path = drilldown.DrillPath("sales", ["region", "category", "department"])
drilldown.breadcrumb_controls(path, root="Estate")

level = path.current_level
if level is not None:
    try:
        drill_rows = (
            client.get(
                "/api/v1/analytics/revenue/breakdown",
                metrics=metric,
                dimensions=level,
                filter=[f"{name}:{value}" for name, value in path.filters] or None,
                page_size=25,
                **window,
            ).get("data")
            or []
        )
    except ApiError as error:
        ui.failure(str(error), what="That branch did not load")
        drill_rows = []

    drilldown.drill_chart(drill_rows, path=path, value=metric)
else:
    drilldown.drill_chart([], path=path, value=metric)

caveats = [
    "Periods compare like for like only when they are the same length; "
    "day-of-week composition is not adjusted for.",
]
if (breakdown.get("meta") or {}).get("truncated"):
    caveats.append(
        "This breakdown was truncated at 25 rows — the chart is the top of the "
        "distribution rather than all of it."
    )
if metric == "aov":
    caveats.append(
        "Average order value is a ratio, recomputed at each grain. These values do not "
        "sum to the headline, and averaging them would not reproduce it either."
    )

ui.caveats(caveats)
ui.provenance(breakdown.get("meta") or {})
