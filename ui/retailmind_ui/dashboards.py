"""Composed dashboards: several panels as one Plotly figure.

The workspaces render charts as separate figures, which is right for a screen —
each one can load, fail, or be caveated independently. This module does the
other thing: it assembles panels into a **single figure** with one layout, one
hover convention, and one export. That matters in exactly three places — a
briefing that must survive as a PNG in someone's deck, a report page where the
panels have to line up, and any surface where two panels genuinely share an
axis and should pan together.

Three rules hold here that do not apply to a lone chart:

**A missing panel is drawn, not dropped.** If a grid silently reflows because
one query came back empty, every remaining panel changes position and size, and
a reader comparing this week's export against last week's is comparing two
different layouts. Empty panels keep their cell and say why they are empty.

**Panels only share an axis when they share a meaning.** `shared_xaxes` on a
grid whose rows are dated series and category ranks would link a time axis to a
category axis — the panels would pan together into nonsense.

**Domain traces need their cell declared.** Treemaps, sankeys, choropleths and
polar traces cannot live in a cartesian cell; `make_subplots` needs the type up
front. Getting it wrong renders an empty white square rather than an error,
which is the failure mode this module exists to have solved once.
"""

from typing import Any

import plotly.graph_objects as go
from plotly.subplots import make_subplots

from retailmind_ui.design import INK, SEMANTIC

#: Panel titles sit above their cell in the same muted key as a section note.
_TITLE_FONT = {"size": 12, "color": INK["muted"]}

_AXIS = {
    "showgrid": True,
    "gridcolor": "rgba(148,163,184,0.10)",
    "zeroline": False,
    "linecolor": "rgba(148,163,184,0.18)",
    "tickfont": {"size": 10, "color": INK["muted"]},
}


def _shell(figure: go.Figure, *, height: int, title: str = "") -> go.Figure:
    """Apply the design system to a composed figure."""
    figure.update_layout(
        height=height,
        margin={"l": 10, "r": 10, "t": 56 if title else 34, "b": 10},
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font={
            "family": '-apple-system, BlinkMacSystemFont, Inter, "Segoe UI", sans-serif',
            "size": 11,
            "color": INK["text"],
        },
        hoverlabel={
            "bgcolor": INK["raised"],
            "bordercolor": "rgba(148,163,184,0.3)",
            "font": {"size": 11, "color": INK["text"]},
        },
        showlegend=False,
        dragmode=False,
        title=(
            {
                "text": title,
                "font": {"size": 14, "color": INK["text"]},
                "x": 0.005,
                "y": 0.98,
            }
            if title
            else None
        ),
    )
    figure.update_xaxes(**_AXIS)
    figure.update_yaxes(**_AXIS)
    for annotation in figure.layout.annotations or ():
        if annotation.text and annotation.font.size is None:
            annotation.font = _TITLE_FONT
    return figure


def _blank(figure: go.Figure, *, row: int, column: int, reason: str) -> None:
    """Keep an empty panel's cell, and say why it is empty.

    A grid that reflows around a failed query changes the position and size of
    every other panel, and two exports of "the same" dashboard stop being
    comparable.
    """
    figure.add_annotation(
        row=row,
        col=column,
        text=f"<i>{reason}</i>",
        showarrow=False,
        font={"size": 11, "color": INK["faint"]},
        xref="x domain",
        yref="y domain",
        x=0.5,
        y=0.5,
    )
    figure.update_xaxes(visible=False, row=row, col=column)
    figure.update_yaxes(visible=False, row=row, col=column)


def _rows(data: list[dict[str, Any]], *keys: str) -> bool:
    return bool(data) and all(key in data[0] for key in keys)


# ── Revenue ──────────────────────────────────────────────────────────


def revenue_dashboard(
    *,
    trend: list[dict[str, Any]],
    breakdown: list[dict[str, Any]],
    matrix: list[dict[str, Any]],
    dimension: str = "region",
    measure: str = "net_revenue",
    height: int = 620,
    title: str = "",
) -> go.Figure:
    """Trend, composition, and the two-dimension matrix as one figure.

    The trend spans the full width because it is the only panel with a time
    axis; putting a dated series beside a category ranking in the same row
    invites the reader to scan across them as though the x axes agreed.
    """
    figure = make_subplots(
        rows=2,
        cols=2,
        specs=[[{"colspan": 2}, None], [{"type": "xy"}, {"type": "xy"}]],
        row_heights=[0.42, 0.58],
        vertical_spacing=0.14,
        horizontal_spacing=0.09,
        subplot_titles=(
            f"{measure.replace('_', ' ').title()} over the period",
            f"By {dimension}",
            f"{dimension.title()} × category",
        ),
    )

    if _rows(trend, "business_date", measure):
        figure.add_trace(
            go.Scatter(
                x=[row["business_date"] for row in trend],
                y=[row[measure] for row in trend],
                mode="lines",
                line={"color": SEMANTIC["accent"], "width": 2, "shape": "spline"},
                fill="tozeroy",
                fillcolor="rgba(99,102,241,0.13)",
                hovertemplate="%{x}<br><b>%{y:,.0f}</b><extra></extra>",
            ),
            row=1,
            col=1,
        )
    else:
        _blank(figure, row=1, column=1, reason="no daily rows in this window")

    if _rows(breakdown, dimension, measure):
        ranked = sorted(breakdown, key=lambda row: float(row[measure] or 0))[-10:]
        figure.add_trace(
            go.Bar(
                x=[row[measure] for row in ranked],
                y=[str(row[dimension]) for row in ranked],
                orientation="h",
                marker={"color": SEMANTIC["accent"]},
                hovertemplate="%{y}<br><b>%{x:,.0f}</b><extra></extra>",
            ),
            row=2,
            col=1,
        )
    else:
        _blank(figure, row=2, column=1, reason=f"no {dimension} rows")

    if _rows(matrix, dimension, "category", measure):
        regions = list(dict.fromkeys(str(row[dimension]) for row in matrix))
        categories = list(dict.fromkeys(str(row["category"]) for row in matrix))
        lookup = {
            (str(row[dimension]), str(row["category"])): float(row[measure] or 0) for row in matrix
        }
        figure.add_trace(
            go.Heatmap(
                # Absent combinations stay None rather than zero: a category a
                # region does not carry is not a category performing at zero.
                z=[
                    [lookup.get((region, category)) for category in categories]
                    for region in regions
                ],
                x=categories,
                y=regions,
                colorscale=[[0, "rgba(99,102,241,0.08)"], [1, SEMANTIC["accent"]]],
                showscale=False,
                xgap=2,
                ygap=2,
                hovertemplate="%{y} · %{x}<br><b>%{z:,.0f}</b><extra></extra>",
            ),
            row=2,
            col=2,
        )
    else:
        _blank(figure, row=2, column=2, reason="no two-dimension breakdown")

    figure.update_layout(bargap=0.4)
    return _shell(figure, height=height, title=title)


# ── Outlook ──────────────────────────────────────────────────────────


def outlook_dashboard(
    *,
    actuals: list[dict[str, Any]],
    forecast: list[dict[str, Any]],
    effects: list[dict[str, Any]],
    height: int = 560,
    title: str = "",
) -> go.Figure:
    """Recent actuals, the forecast with its interval, and what drives it.

    The two rows **do** share a time axis here — actuals and forecast are the
    same series either side of today — so they are linked and pan together.
    The effects panel does not, and sits on its own axis beneath.
    """
    figure = make_subplots(
        rows=2,
        cols=1,
        row_heights=[0.62, 0.38],
        vertical_spacing=0.16,
        subplot_titles=("Actual and forecast, with prediction interval", "Named effects"),
    )

    if _rows(forecast, "business_date", "forecast"):
        dates = [row["business_date"] for row in forecast]
        if all("forecast_upper" in row and "forecast_lower" in row for row in forecast):
            figure.add_trace(
                go.Scatter(
                    x=[*dates, *dates[::-1]],
                    y=[row["forecast_upper"] for row in forecast]
                    + [row["forecast_lower"] for row in reversed(forecast)],
                    fill="toself",
                    fillcolor="rgba(34,211,238,0.13)",
                    line={"color": "rgba(0,0,0,0)"},
                    hoverinfo="skip",
                ),
                row=1,
                col=1,
            )
        figure.add_trace(
            go.Scatter(
                x=dates,
                y=[row["forecast"] for row in forecast],
                mode="lines",
                line={"color": SEMANTIC["ai"], "width": 2, "dash": "dot"},
                hovertemplate="%{x}<br>forecast <b>%{y:,.0f}</b><extra></extra>",
            ),
            row=1,
            col=1,
        )
    else:
        _blank(figure, row=1, column=1, reason="nothing published for this target")

    if _rows(actuals, "business_date", "net_revenue"):
        figure.add_trace(
            go.Scatter(
                x=[row["business_date"] for row in actuals],
                y=[row["net_revenue"] for row in actuals],
                mode="lines",
                line={"color": INK["text"], "width": 2},
                hovertemplate="%{x}<br>actual <b>%{y:,.0f}</b><extra></extra>",
            ),
            row=1,
            col=1,
        )

    if _rows(effects, "feature", "effect"):
        ranked = sorted(effects, key=lambda row: abs(float(row["effect"] or 0)))[-8:]
        figure.add_trace(
            go.Bar(
                x=[row["effect"] for row in ranked],
                y=[str(row["feature"]) for row in ranked],
                orientation="h",
                marker={
                    "color": [
                        SEMANTIC["critical"]
                        if float(row["effect"] or 0) < 0
                        else SEMANTIC["positive"]
                        for row in ranked
                    ]
                },
                hovertemplate="%{y}<br><b>%{x:,.0f}</b><extra></extra>",
            ),
            row=2,
            col=1,
        )
    else:
        _blank(figure, row=2, column=1, reason="this model publishes no per-feature explanation")

    figure.update_layout(bargap=0.42)
    return _shell(figure, height=height, title=title)


# ── Estate ───────────────────────────────────────────────────────────


def estate_dashboard(
    *,
    regions: list[dict[str, Any]],
    stores: list[dict[str, Any]],
    measure: str = "net_revenue",
    height: int = 560,
    title: str = "",
) -> go.Figure:
    """The map beside the league table, in one figure.

    The choropleth needs a `geo` cell rather than a cartesian one. Declaring it
    wrong renders a blank white square with no error, which is the specific
    trap this module was written to remove.
    """
    figure = make_subplots(
        rows=1,
        cols=2,
        column_widths=[0.58, 0.42],
        specs=[[{"type": "choropleth"}, {"type": "xy"}]],
        horizontal_spacing=0.06,
        subplot_titles=("Regional totals across their states", "Stores ranked"),
    )

    from retailmind_ui import geo  # noqa: PLC0415 — avoids a cycle at import time

    if _rows(regions, "region", measure):
        codes: list[str] = []
        values: list[float] = []
        names: list[str] = []
        for row in regions:
            for code in geo.states_for(str(row["region"])):
                codes.append(code)
                values.append(float(row[measure] or 0))
                names.append(str(row["region"]))
        figure.add_trace(
            go.Choropleth(
                locations=codes,
                z=values,
                text=names,
                locationmode="USA-states",
                colorscale=[[0, "rgba(99,102,241,0.15)"], [1, SEMANTIC["accent"]]],
                showscale=False,
                marker={"line": {"color": "rgba(7,9,13,0.85)", "width": 0.5}},
                hovertemplate=(
                    "%{text} region<br><b>%{z:,.0f}</b><br>"
                    "<i>regional total, not state-level</i><extra></extra>"
                ),
            ),
            row=1,
            col=1,
        )

    if _rows(stores, "store_name", measure):
        ranked = sorted(stores, key=lambda row: float(row[measure] or 0))[-12:]
        figure.add_trace(
            go.Bar(
                x=[row[measure] for row in ranked],
                y=[str(row["store_name"]) for row in ranked],
                orientation="h",
                marker={"color": SEMANTIC["accent"]},
                hovertemplate="%{y}<br><b>%{x:,.0f}</b><extra></extra>",
            ),
            row=1,
            col=2,
        )
    else:
        _blank(figure, row=1, column=2, reason="no stores in this window")

    figure.update_geos(
        scope="usa",
        bgcolor="rgba(0,0,0,0)",
        lakecolor="rgba(0,0,0,0)",
        landcolor="rgba(148,163,184,0.05)",
        subunitcolor="rgba(148,163,184,0.15)",
    )
    figure.update_layout(bargap=0.4)
    return _shell(figure, height=height, title=title)


# ── Inventory ────────────────────────────────────────────────────────


def inventory_dashboard(
    *,
    ageing: list[dict[str, Any]],
    excess: list[dict[str, Any]],
    risk: list[dict[str, Any]],
    height: int = 560,
    title: str = "",
) -> go.Figure:
    """Ageing, excess composition, and availability risk in one figure.

    All three are point-in-time reads of the same snapshot, which is why they
    belong together and why none of them carries a time axis: summing stock
    across days counts the same unit repeatedly.
    """
    figure = make_subplots(
        rows=2,
        cols=2,
        specs=[[{"type": "xy"}, {"type": "domain"}], [{"colspan": 2, "type": "xy"}, None]],
        row_heights=[0.5, 0.5],
        vertical_spacing=0.15,
        horizontal_spacing=0.08,
        subplot_titles=(
            "Inventory value by age",
            "Excess by category",
            "Stockout rate by region and category",
        ),
    )

    if _rows(ageing, "aging_bucket", "inventory_value"):
        figure.add_trace(
            go.Bar(
                x=[str(row["aging_bucket"]) for row in ageing],
                y=[row["inventory_value"] for row in ageing],
                marker={"color": SEMANTIC["capital"]},
                hovertemplate="%{x}<br><b>%{y:,.0f}</b><extra></extra>",
            ),
            row=1,
            col=1,
        )
    else:
        _blank(figure, row=1, column=1, reason="no ageing buckets")

    positive = [row for row in excess if float(row.get("excess_value") or 0) > 0]
    if _rows(positive, "category", "excess_value"):
        figure.add_trace(
            go.Treemap(
                labels=[str(row["category"]) for row in positive],
                parents=[""] * len(positive),
                values=[float(row["excess_value"]) for row in positive],
                marker={
                    "colorscale": [[0, "rgba(167,139,250,0.3)"], [1, SEMANTIC["capital"]]],
                    "line": {"width": 1, "color": "rgba(7,9,13,0.9)"},
                },
                textinfo="label+value",
                hovertemplate="%{label}<br><b>%{value:,.0f}</b><extra></extra>",
            ),
            row=1,
            col=2,
        )

    if _rows(risk, "region", "category", "stockout_rate"):
        regions = list(dict.fromkeys(str(row["region"]) for row in risk))
        categories = list(dict.fromkeys(str(row["category"]) for row in risk))
        lookup = {
            (str(row["region"]), str(row["category"])): float(row["stockout_rate"] or 0)
            for row in risk
        }
        figure.add_trace(
            go.Heatmap(
                z=[[lookup.get((region, item)) for item in categories] for region in regions],
                x=categories,
                y=regions,
                colorscale=[[0, "rgba(16,185,129,0.18)"], [1, SEMANTIC["critical"]]],
                showscale=False,
                xgap=2,
                ygap=2,
                hovertemplate="%{y} · %{x}<br><b>%{z:.1%}</b><extra></extra>",
            ),
            row=2,
            col=1,
        )
    else:
        _blank(figure, row=2, column=1, reason="no region × category risk rows")

    figure.update_layout(bargap=0.45)
    return _shell(figure, height=height, title=title)
