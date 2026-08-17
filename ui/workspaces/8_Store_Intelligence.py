"""Store Intelligence — the estate as a league table, and its outliers.

**A league table without a peer group is a map of catchment quality.** Ranking
every store on raw revenue tells you which ones sit in busy places. The
comparison that means something is a store against stores like it, which is why
the cluster filter is here and why the quadrant plots against the estate median
rather than against zero.
"""

from datetime import timedelta
from typing import Any

import streamlit as st

from retailmind_ui import charts, dashboards, design, session
from retailmind_ui import components as ui
from retailmind_ui.api import ApiError
from retailmind_ui.design import SEMANTIC
from retailmind_ui.formatting import number

design.configure("Store Intelligence", icon="▣")
client = session.require("analytics.revenue.read")

ui.workspace_header(
    "Store Intelligence",
    eyebrow="Estate",
    summary="Where each store sits against its peers, and which gaps are worth a visit.",
)

controls = st.columns([2, 2, 4])
days = controls[0].slider("Window (days)", 7, 90, 28)
limit = controls[1].slider("Stores", 5, 50, 20)

try:
    ranking: dict[str, Any] = client.get(
        "/api/v1/dashboard/stores/ranking",
        days=days,
        limit=limit,
        as_of=session.data_date().isoformat(),
    )
except ApiError as error:
    ui.workspace_error(error, what="The store ranking did not load")
    st.stop()

stores = ranking.get("stores") or []

if not stores:
    ui.empty(
        "No store traded in this window, or the ranking surface returned nothing.",
        what="No stores to rank",
    )
    st.stop()

revenues = [float(row.get("net_revenue") or 0) for row in stores]
ui.stat_row(
    [
        {"label": "Stores ranked", "value": str(len(stores))},
        {
            "label": "Top store",
            "value": str(stores[0].get("store_name", "—")),
            "note": str(stores[0].get("store_cluster") or ""),
        },
        {
            "label": "Estate revenue",
            "value": number(sum(revenues), "currency"),
            "note": f"over {days} days",
        },
        {
            "label": "Spread top to bottom",
            "value": number(max(revenues) - min(revenues), "currency"),
            "note": "before any peer-group adjustment",
        },
    ]
)

# ── Geography ────────────────────────────────────────────────────────

ui.section("Where the estate is", "Two views: regional totals, and the stores themselves.")

geography, cities = st.tabs(["Regions", "Store cities"])

with geography:
    try:
        by_region = (
            client.get(
                "/api/v1/analytics/store/breakdown",
                metrics="net_revenue",
                dimensions="region",
                start_date=(session.data_date() - timedelta(days=days - 1)).isoformat(),
                end_date=session.data_date().isoformat(),
                page_size=20,
            ).get("data")
            or []
        )
    except ApiError:
        by_region = []

    figure = charts.region_choropleth(by_region, region_key="region", value="net_revenue")
    if figure is not None:
        st.plotly_chart(figure, width="stretch", config={"displayModeBar": False})
        ui.caveats(
            [
                "Every state in a census region carries that region's total. This is "
                "regional data drawn on a state map, not state-level measurement — the "
                "warehouse holds regions, and inventing a split between them would be "
                "worse than colouring them alike.",
            ],
            title="What this map is and is not",
        )
    else:
        ui.empty("The store domain returned no regional rows to map.")

with cities:
    try:
        by_city = (
            client.get(
                "/api/v1/analytics/store/breakdown",
                metrics="net_revenue,units_sold",
                dimensions="city",
                start_date=(session.data_date() - timedelta(days=days - 1)).isoformat(),
                end_date=session.data_date().isoformat(),
                page_size=100,
            ).get("data")
            or []
        )
    except ApiError:
        by_city = []

    figure, unplaced = charts.city_bubbles(by_city, city_key="city", value="net_revenue")
    if figure is not None:
        st.plotly_chart(figure, width="stretch", config={"displayModeBar": False})
        st.caption("Bubble area is net revenue over the window. Area, not radius.")
    else:
        ui.empty("No store city in this window could be placed.")

    if unplaced:
        ui.caveats(
            [
                f"{len(unplaced)} city not in the console's gazetteer and therefore not "
                f"plotted: {', '.join(sorted(unplaced)[:8])}. Their revenue is still in "
                "every total on this page — the map is short, the numbers are not."
            ],
            title="Cities missing from the map",
        )

ui.section("Estate at a glance", "Map and league table as one figure, for export.")
st.plotly_chart(
    dashboards.estate_dashboard(
        regions=by_region,
        stores=stores,
        title=f"Estate · {days} days to {session.data_date():%d %b %Y}",
    ),
    width="stretch",
    config={"displayModeBar": False},
)

ui.section("League table", "Sortable and filterable — this is the surface managers work in.")
ui.analyst_grid(stores, pinned="store_name", height=420, empty_reason="No stores returned.")

ui.section("Ranked", "By net revenue in the window.")
figure = charts.ranked_bars(
    stores, label="store_name", value="net_revenue", colour=SEMANTIC["accent"], limit=12
)
if figure is not None:
    st.plotly_chart(figure, width="stretch", config={"displayModeBar": False})

# ── Three measures at once ───────────────────────────────────────────

ui.section(
    "Volume, ticket, and scale together",
    "Revenue against basket size, sized by units — the third measure a scatter cannot carry.",
)
figure = charts.bubble(
    stores,
    x="net_revenue",
    y="aov",
    size="units_sold",
    label="store_name",
    colour_by="region" if any("region" in row for row in stores) else None,
    height=420,
)
if figure is not None:
    st.plotly_chart(figure, width="stretch", config={"displayModeBar": False})
    st.caption(
        "Colour is region. Bubble area is units sold — scaled by area rather than "
        "radius, because radius scaling exaggerates large values by the square."
    )

# ── Profile ──────────────────────────────────────────────────────────

ui.section(
    "Store profiles",
    "The shape of a store across four measures at once, for the top few.",
)

profile_stores = st.multiselect(
    "Compare",
    [str(row.get("store_name")) for row in stores],
    default=[str(row.get("store_name")) for row in stores[:3]],
    max_selections=4,
)
chosen_rows = [row for row in stores if str(row.get("store_name")) in profile_stores]

figure, normalised = charts.radar(
    chosen_rows,
    label="store_name",
    axes=["net_revenue", "margin_amount", "units_sold", "aov"],
    height=420,
)
if figure is not None:
    st.plotly_chart(figure, width="stretch", config={"displayModeBar": False})
    if normalised:
        ui.caveats(
            [
                "The axes are rescaled 0–1 *within the stores selected here*. Revenue in "
                "pounds and basket size in pounds-per-order do not share a radius, and "
                "drawing them raw would produce a shape decided by units. Change the "
                "selection and every shape changes — positions are relative to this "
                "comparison, never absolute.",
            ],
            title="How to read this radar",
        )
else:
    ui.empty("Select at least one store, and the ranking must carry all four measures.")

ui.caveats(
    [
        "Ranking on raw revenue ranks catchments as much as stores. Where a peer "
        "cluster is available the API scopes the comparison to it; without one, read "
        "this as a description of size, not of performance.",
        f"The window is {days} days ending {session.data_date():%d %b %Y}. "
        "Day-of-week composition is not adjusted for.",
    ]
)
ui.provenance(ranking.get("meta") or {})
