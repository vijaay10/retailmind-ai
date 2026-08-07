"""Enterprise charting on one shared Plotly template.

**A chart shape is a claim.** A line across regions asserts an ordering they do
not have; an area under a forecast asserts the space beneath it is meaningful.
The API already returns the shape it believes its result supports, along with
the reason, and every builder here takes that reason and renders it beneath the
figure. Where the console picks a shape itself, the builder's docstring says
what the shape asserts.

**Charts are deliberately quiet.** No gridline is darker than the hairline, no
series is thicker than 2px, and nothing is filled at more than 18% opacity. In
a product where insight text does the arguing, a chart that shouts is a chart
competing with the sentence that explains it.

**Nothing here computes.** Builders take the API's rows and plot them. The one
exception is arithmetic that is purely visual — cumulative offsets in a
waterfall, which are positions on a canvas rather than published figures.
"""

from typing import Any

import pandas as pd
import plotly.graph_objects as go

from retailmind_ui import geo
from retailmind_ui.design import INK, SEMANTIC, tier_colour

#: Categorical series colours, ordered so the first three are distinguishable
#: for the most common forms of colour blindness.
SERIES = ["#6366F1", "#22D3EE", "#F59E0B", "#10B981", "#A78BFA", "#F43F5E", "#38BDF8"]

_AXIS = {
    "showgrid": True,
    "gridcolor": "rgba(148,163,184,0.10)",
    "zeroline": False,
    "linecolor": "rgba(148,163,184,0.20)",
    "tickfont": {"size": 11, "color": INK["muted"]},
    "title": {"font": {"size": 11, "color": INK["faint"]}},
}


def _layout(height: int, **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "height": height,
        "margin": {"l": 8, "r": 8, "t": 24, "b": 8},
        "paper_bgcolor": "rgba(0,0,0,0)",
        "plot_bgcolor": "rgba(0,0,0,0)",
        "font": {
            "family": '-apple-system, BlinkMacSystemFont, Inter, "Segoe UI", sans-serif',
            "size": 12,
            "color": INK["text"],
        },
        "xaxis": dict(_AXIS),
        "yaxis": dict(_AXIS),
        "hoverlabel": {
            "bgcolor": INK["raised"],
            "bordercolor": "rgba(148,163,184,0.3)",
            "font": {"size": 12, "color": INK["text"]},
        },
        "legend": {
            "orientation": "h",
            "yanchor": "bottom",
            "y": 1.02,
            "x": 0,
            "font": {"size": 11, "color": INK["muted"]},
            "bgcolor": "rgba(0,0,0,0)",
        },
        "showlegend": False,
        "dragmode": False,
    }
    base.update(overrides)
    return base


def _frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
    return pd.DataFrame(rows) if rows else pd.DataFrame()


# ── Trend ────────────────────────────────────────────────────────────


def trend(
    rows: list[dict[str, Any]],
    *,
    x: str,
    y: str,
    height: int = 260,
    colour: str = SEMANTIC["accent"],
    comparison: str | None = None,
) -> go.Figure | None:
    """A metric over time.

    A line, because dates have a natural order and equal spacing. The soft fill
    beneath is a reading aid for direction, not a claim that the area means
    anything — which is why it stops at 14% opacity and carries no axis.
    """
    data = _frame(rows)
    if data.empty or x not in data or y not in data:
        return None

    figure = go.Figure()
    if comparison and comparison in data:
        figure.add_trace(
            go.Scatter(
                x=data[x],
                y=data[comparison],
                name="Prior period",
                mode="lines",
                line={"color": INK["faint"], "width": 1.4, "dash": "dot"},
                hovertemplate="prior: %{y:,.0f}<extra></extra>",
            )
        )

    figure.add_trace(
        go.Scatter(
            x=data[x],
            y=data[y],
            name=y,
            mode="lines",
            line={"color": colour, "width": 2, "shape": "spline", "smoothing": 0.4},
            fill="tozeroy",
            fillcolor=_alpha(colour, 0.14),
            hovertemplate="%{x}<br><b>%{y:,.0f}</b><extra></extra>",
        )
    )
    figure.update_layout(**_layout(height, showlegend=comparison is not None))
    return figure


def forecast_band(
    rows: list[dict[str, Any]],
    *,
    x: str = "business_date",
    point: str = "forecast",
    lower: str = "forecast_lower",
    upper: str = "forecast_upper",
    actual: str | None = None,
    height: int = 320,
) -> go.Figure | None:
    """A forecast with its prediction interval.

    The band is drawn *before* the line and filled, because the interval is the
    honest part of a forecast and the point estimate is the convenient one. A
    chart that draws the line boldly and the interval as two faint edges invites
    planning against a number the model never promised.
    """
    data = _frame(rows)
    if data.empty or point not in data:
        return None

    figure = go.Figure()

    if lower in data and upper in data:
        figure.add_trace(
            go.Scatter(
                x=list(data[x]) + list(data[x])[::-1],
                y=list(data[upper]) + list(data[lower])[::-1],
                fill="toself",
                fillcolor=_alpha(SEMANTIC["ai"], 0.13),
                line={"color": "rgba(0,0,0,0)"},
                name="Prediction interval",
                hoverinfo="skip",
            )
        )

    if actual and actual in data:
        figure.add_trace(
            go.Scatter(
                x=data[x],
                y=data[actual],
                name="Actual",
                mode="lines",
                line={"color": INK["text"], "width": 2},
                hovertemplate="actual %{y:,.0f}<extra></extra>",
            )
        )

    figure.add_trace(
        go.Scatter(
            x=data[x],
            y=data[point],
            name="Forecast",
            mode="lines",
            line={"color": SEMANTIC["ai"], "width": 2, "dash": "dot" if actual else "solid"},
            hovertemplate="%{x}<br>forecast <b>%{y:,.0f}</b><extra></extra>",
        )
    )

    figure.update_layout(**_layout(height, showlegend=True))
    return figure


# ── Composition ──────────────────────────────────────────────────────


def ranked_bars(
    rows: list[dict[str, Any]],
    *,
    label: str,
    value: str,
    height: int = 300,
    colour: str = SEMANTIC["accent"],
    limit: int = 12,
) -> go.Figure | None:
    """Categories ranked by one measure.

    Horizontal bars: category names are words, and words read left-to-right.
    Rotating them 45° under a vertical bar chart is a layout decision that
    costs the reader legibility to save the chart width.
    """
    data = _frame(rows)
    if data.empty or label not in data or value not in data:
        return None

    data = data.nlargest(limit, value).sort_values(value)
    figure = go.Figure(
        go.Bar(
            x=data[value],
            y=data[label].astype(str),
            orientation="h",
            marker={"color": colour, "line": {"width": 0}},
            hovertemplate="%{y}<br><b>%{x:,.0f}</b><extra></extra>",
        )
    )
    figure.update_layout(**_layout(height))
    figure.update_layout(bargap=0.45)
    return figure


def contribution(
    rows: list[dict[str, Any]],
    *,
    label: str = "subject",
    value: str = "impact_amount",
    height: int = 300,
    limit: int = 8,
) -> go.Figure | None:
    """Who moved the number, and in which direction.

    Diverging bars from a zero line rather than a ranked list, because the
    single most useful fact in a decomposition is that some slices moved
    *against* the trend — and a chart sorted by magnitude alone hides it.
    """
    data = _frame(rows)
    if data.empty or value not in data or label not in data:
        return None

    data = data.reindex(data[value].abs().sort_values().index).tail(limit)
    colours = [
        SEMANTIC["critical"] if float(item) < 0 else SEMANTIC["positive"] for item in data[value]
    ]

    figure = go.Figure(
        go.Bar(
            x=data[value],
            y=data[label].astype(str),
            orientation="h",
            marker={"color": colours, "line": {"width": 0}},
            hovertemplate="%{y}<br><b>%{x:,.0f}</b><extra></extra>",
        )
    )
    figure.update_layout(**_layout(height))
    figure.add_vline(x=0, line={"color": "rgba(148,163,184,0.35)", "width": 1})
    figure.update_layout(bargap=0.45)
    return figure


def waterfall(
    steps: list[tuple[str, float]],
    *,
    start: float,
    height: int = 300,
    start_label: str = "Baseline",
    end_label: str = "Current",
) -> go.Figure:
    """A movement decomposed into named steps.

    The cumulative offsets here are the one piece of arithmetic in this module.
    They are positions on a canvas — where a bar floats — not published
    figures, and they are computed from values the API already returned.
    """
    labels = [start_label, *[name for name, _ in steps], end_label]
    values = [start, *[amount for _, amount in steps], 0.0]
    measures = ["absolute", *["relative"] * len(steps), "total"]

    figure = go.Figure(
        go.Waterfall(
            orientation="v",
            measure=measures,
            x=labels,
            y=values,
            connector={"line": {"color": "rgba(148,163,184,0.25)", "width": 1}},
            increasing={"marker": {"color": SEMANTIC["positive"]}},
            decreasing={"marker": {"color": SEMANTIC["critical"]}},
            totals={"marker": {"color": SEMANTIC["accent"]}},
            hovertemplate="%{x}<br><b>%{y:,.0f}</b><extra></extra>",
        )
    )
    figure.update_layout(**_layout(height))
    return figure


def cohort_heatmap(
    rows: list[dict[str, Any]],
    *,
    row_key: str = "cohort_week",
    column_key: str = "weeks_since_acquisition",
    value: str = "retention_rate",
    height: int = 360,
) -> go.Figure | None:
    """The retention triangle.

    Deliberately triangular: cells stop at the observation edge rather than
    being filled with zeros. A cohort acquired two weeks ago has no week-8
    retention, and a zero there draws a cliff that never happened.
    """
    data = _frame(rows)
    if data.empty or value not in data:
        return None

    grid = data.pivot_table(index=row_key, columns=column_key, values=value, aggfunc="mean")
    figure = go.Figure(
        go.Heatmap(
            z=grid.to_numpy(),
            x=[str(item) for item in grid.columns],
            y=[str(item) for item in grid.index],
            colorscale=[[0, "rgba(99,102,241,0.05)"], [1, SEMANTIC["accent"]]],
            hovertemplate="cohort %{y} · week %{x}<br><b>%{z:.0%}</b><extra></extra>",
            showscale=False,
            xgap=2,
            ygap=2,
        )
    )
    figure.update_layout(**_layout(height))
    return figure


def scatter_quadrant(
    rows: list[dict[str, Any]],
    *,
    x: str,
    y: str,
    label: str,
    size: str | None = None,
    height: int = 380,
) -> go.Figure | None:
    """Two measures against each other, for finding the outlier.

    Quadrant lines sit at the medians rather than at zero: "below average" is
    the comparison a store manager is actually making, and a fixed origin puts
    every store in the same corner.
    """
    data = _frame(rows)
    if data.empty or x not in data or y not in data or label not in data:
        return None

    sizes = None
    if size and size in data:
        magnitude = data[size].abs()
        largest = float(magnitude.max()) or 1.0
        sizes = (magnitude / largest * 26 + 8).tolist()

    # Label the outliers only. Twenty labels over twenty clustered points is a
    # grey smear that hides the very thing the chart is for — and the points
    # worth naming are exactly the ones far from the medians.
    mid_x, mid_y = float(data[x].median()), float(data[y].median())
    spread_x = float((data[x] - mid_x).abs().median()) or 1.0
    spread_y = float((data[y] - mid_y).abs().median()) or 1.0
    labels = [
        str(name) if abs(float(px) - mid_x) > spread_x or abs(float(py) - mid_y) > spread_y else ""
        for name, px, py in zip(data[label], data[x], data[y], strict=True)
    ]

    figure = go.Figure(
        go.Scatter(
            x=data[x],
            y=data[y],
            mode="markers+text",
            text=labels,
            hovertext=data[label].astype(str),
            textposition="top center",
            textfont={"size": 10, "color": INK["muted"]},
            marker={
                "size": sizes or 12,
                "color": SEMANTIC["accent"],
                "opacity": 0.75,
                "line": {"width": 1, "color": "rgba(255,255,255,0.35)"},
            },
            hovertemplate="%{hovertext}<br>"
            + f"{x}: "
            + "%{x:,.2f}<br>"
            + f"{y}: "
            + "%{y:,.2f}<extra></extra>",
        )
    )
    figure.add_vline(
        x=float(data[x].median()),
        line={"color": "rgba(148,163,184,0.25)", "width": 1, "dash": "dash"},
    )
    figure.add_hline(
        y=float(data[y].median()),
        line={"color": "rgba(148,163,184,0.25)", "width": 1, "dash": "dash"},
    )
    figure.update_layout(**_layout(height))
    return figure


def evidence_effects(
    rows: list[dict[str, Any]],
    *,
    label: str = "feature",
    value: str = "effect",
    height: int = 280,
    limit: int = 10,
) -> go.Figure | None:
    """What pushed a forecast up or down, by named effect."""
    return contribution(rows, label=label, value=value, height=height, limit=limit)


def confidence_strip(findings: list[dict[str, Any]], *, height: int = 220) -> go.Figure | None:
    """Candidate causes plotted as impact against confidence.

    The two axes are the whole judgement: a large impact at low confidence is a
    hypothesis worth testing, and a small one at high confidence is a fact not
    worth acting on. Ranking them in a list forces those two into one order.
    """
    data = _frame(findings)
    if data.empty or "confidence" not in data:
        return None

    figure = go.Figure(
        go.Scatter(
            x=data["confidence"],
            y=data.get("impact_amount", 0),
            mode="markers",
            text=data.get("headline", ""),
            marker={
                "size": 13,
                "color": [tier_colour(str(tier)) for tier in data.get("evidence_tier", [])],
                "line": {"width": 1, "color": "rgba(255,255,255,0.3)"},
            },
            hovertemplate="%{text}<br>confidence %{x:.0%}<br>impact %{y:,.0f}<extra></extra>",
        )
    )
    layout = _layout(height)
    layout["xaxis"] = {**_AXIS, "tickformat": ".0%", "title": {"text": "confidence"}}
    layout["yaxis"] = {**_AXIS, "title": {"text": "impact"}}
    figure.update_layout(**layout)
    return figure


def _alpha(colour: str, alpha: float) -> str:
    """Hex to rgba, for fills that must sit under a line of the same hue."""
    value = colour.lstrip("#")
    red, green, blue = (int(value[index : index + 2], 16) for index in (0, 2, 4))
    return f"rgba({red}, {green}, {blue}, {alpha})"


# ── Composition and hierarchy ────────────────────────────────────────


def treemap(
    rows: list[dict[str, Any]],
    *,
    path: list[str],
    value: str,
    height: int = 400,
    colour: str | None = None,
) -> go.Figure | None:
    """Composition, nested.

    Area is the honest encoding for "share of a whole" — better than a pie
    because rectangles are comparable, better than stacked bars because the
    hierarchy stays legible. It requires the measure to be **additive**: a
    treemap of average order value would draw boxes whose parents are not the
    sum of their children, which is a picture of nothing.

    Nodes are addressed by their full path rather than by label. "Outerwear"
    appears under two regions, and identifying nodes by label alone makes
    Plotly merge them into one box with the wrong parent — a failure that
    renders as an empty chart rather than as an error.
    """
    data = _frame(rows)
    levels = [name for name in path if name in data]
    if data.empty or value not in data or not levels:
        return None

    # Negative values have no area. Excluding them is the only honest choice —
    # a negative rectangle would either vanish or render as a positive one.
    data = data[data[value] > 0]
    if data.empty:
        return None

    ids: list[str] = []
    labels: list[str] = []
    parents: list[str] = []
    values: list[float] = []

    # Parents carry zero and inherit their children's total, so the rectangles
    # nest exactly rather than approximately.
    for depth in range(len(levels) - 1):
        columns = levels[: depth + 1]
        for node in dict.fromkeys(data[columns].astype(str).agg(" / ".join, axis=1)):
            ids.append(node)
            labels.append(node.rsplit(" / ", 1)[-1])
            parents.append(node.rsplit(" / ", 1)[0] if depth else "")
            values.append(0.0)

    for _, row in data.iterrows():
        node = " / ".join(str(row[name]) for name in levels)
        ids.append(node)
        labels.append(str(row[levels[-1]]))
        parents.append(node.rsplit(" / ", 1)[0] if len(levels) > 1 else "")
        values.append(float(row[value]))

    figure = go.Figure(
        go.Treemap(
            ids=ids,
            labels=labels,
            parents=parents,
            values=values,
            branchvalues="remainder",
            marker={
                "colorscale": [[0, "rgba(99,102,241,0.28)"], [1, colour or SEMANTIC["accent"]]],
                "line": {"width": 1, "color": "rgba(7,9,13,0.9)"},
                "pad": {"t": 26, "l": 3, "r": 3, "b": 3},
                "cornerradius": 4,
            },
            # Plotly's own click-to-zoom: a treemap descends into a branch on
            # click, and the path bar is how a reader gets back out.
            pathbar={"visible": True, "thickness": 22, "textfont": {"size": 11}},
            root={"color": "rgba(148,163,184,0.06)"},
            textinfo="label+value",
            textfont={"size": 12},
            hovertemplate=(
                "%{id}<br><b>%{value:,.0f}</b><br>%{percentRoot:.1%} of total<extra></extra>"
            ),
        )
    )
    figure.update_layout(**_layout(height))
    return figure


def matrix_heatmap(
    rows: list[dict[str, Any]],
    *,
    row_key: str,
    column_key: str,
    value: str,
    height: int = 380,
    diverging: bool = False,
) -> go.Figure | None:
    """One measure across two dimensions.

    Cells with no rows stay **blank rather than zero**. A category a region
    does not stock has no revenue, which is not the same as revenue of zero,
    and colouring it at the bottom of the scale invents an underperformer.
    """
    data = _frame(rows)
    if data.empty or not {row_key, column_key, value} <= set(data.columns):
        return None

    grid = data.pivot_table(index=row_key, columns=column_key, values=value, aggfunc="sum")
    if grid.empty:
        return None

    scale = (
        [[0, SEMANTIC["critical"]], [0.5, "rgba(148,163,184,0.12)"], [1, SEMANTIC["positive"]]]
        if diverging
        else [[0, "rgba(99,102,241,0.06)"], [1, SEMANTIC["accent"]]]
    )

    figure = go.Figure(
        go.Heatmap(
            z=grid.to_numpy(),
            x=[str(item) for item in grid.columns],
            y=[str(item) for item in grid.index],
            colorscale=scale,
            zmid=0 if diverging else None,
            xgap=2,
            ygap=2,
            showscale=True,
            colorbar={
                "thickness": 8,
                "outlinewidth": 0,
                "tickfont": {"size": 10, "color": INK["muted"]},
            },
            hovertemplate="%{y} · %{x}<br><b>%{z:,.0f}</b><extra></extra>",
        )
    )
    figure.update_layout(**_layout(height))
    return figure


# ── Flow ─────────────────────────────────────────────────────────────


def funnel(
    rows: list[dict[str, Any]],
    *,
    stage: str,
    value: str,
    height: int = 320,
) -> go.Figure | None:
    """A sequence where each step is a subset of the one before it.

    Only valid when that is actually true. Plotting stages that are *states*
    rather than a progression — segments, risk bands — produces a shape that
    implies leakage between things nobody moves between.
    """
    data = _frame(rows)
    if data.empty or stage not in data or value not in data:
        return None

    figure = go.Figure(
        go.Funnel(
            y=data[stage].astype(str),
            x=data[value],
            marker={
                "color": [SERIES[index % len(SERIES)] for index in range(len(data))],
                "line": {"width": 1, "color": "rgba(7,9,13,0.8)"},
            },
            connector={"line": {"color": "rgba(148,163,184,0.25)", "width": 1}},
            textinfo="value+percent initial",
            textfont={"size": 11},
            hovertemplate="%{y}<br><b>%{x:,.0f}</b><extra></extra>",
        )
    )
    figure.update_layout(**_layout(height))
    return figure


def sankey(
    nodes: list[str],
    links: list[tuple[int, int, float]],
    *,
    height: int = 380,
    labels: list[str] | None = None,
) -> go.Figure | None:
    """Where quantity moves between named states.

    Link width is the flow. Sankeys mislead in one specific way: a diagram
    whose outflows do not sum to its inflows *looks* balanced regardless, so
    callers must pass flows that genuinely reconcile — this builder cannot
    check it for them, and the workspaces that use it say what the flow is.
    """
    if not nodes or not links:
        return None

    figure = go.Figure(
        go.Sankey(
            arrangement="snap",
            node={
                "label": labels or nodes,
                "pad": 18,
                "thickness": 14,
                "line": {"width": 0},
                "color": [SERIES[index % len(SERIES)] for index in range(len(nodes))],
                "hovertemplate": "%{label}<br>%{value:,.0f}<extra></extra>",
            },
            link={
                "source": [item[0] for item in links],
                "target": [item[1] for item in links],
                "value": [item[2] for item in links],
                "color": "rgba(99,102,241,0.18)",
                "hovertemplate": "%{source.label} → %{target.label}<br>"
                "<b>%{value:,.0f}</b><extra></extra>",
            },
        )
    )
    layout = _layout(height)
    layout.pop("xaxis", None)
    layout.pop("yaxis", None)
    figure.update_layout(**layout)
    figure.update_layout(font={"color": INK["text"], "size": 11})
    return figure


# ── Geography ────────────────────────────────────────────────────────


def region_choropleth(
    rows: list[dict[str, Any]],
    *,
    region_key: str = "region",
    value: str = "net_revenue",
    height: int = 400,
) -> go.Figure | None:
    """Regional values painted across the states each region contains.

    **This is not state-level data.** Every state in a region carries the
    region's figure, and the hover text says so on every cell. The alternative
    — leaving the map blank because the warehouse holds regions rather than
    states — hides a real signal; the danger is only in letting the fill read
    as per-state measurement, which is what the labelling prevents.
    """
    data = _frame(rows)
    if data.empty or region_key not in data or value not in data:
        return None

    states: list[str] = []
    values: list[float] = []
    names: list[str] = []
    for _, row in data.iterrows():
        region = str(row[region_key])
        for code in geo.states_for(region):
            states.append(code)
            values.append(float(row[value]))
            names.append(region)

    if not states:
        return None

    figure = go.Figure(
        go.Choropleth(
            locations=states,
            z=values,
            locationmode="USA-states",
            text=names,
            colorscale=[[0, "rgba(99,102,241,0.15)"], [1, SEMANTIC["accent"]]],
            marker={"line": {"color": "rgba(7,9,13,0.85)", "width": 0.6}},
            colorbar={
                "thickness": 8,
                "outlinewidth": 0,
                "tickfont": {"size": 10, "color": INK["muted"]},
            },
            hovertemplate="%{text} region<br><b>%{z:,.0f}</b><br>"
            "<i>regional total, not state-level</i><extra></extra>",
        )
    )
    figure.update_layout(**_layout(height))
    figure.update_geos(
        scope="usa",
        bgcolor="rgba(0,0,0,0)",
        lakecolor="rgba(0,0,0,0)",
        landcolor="rgba(148,163,184,0.05)",
        subunitcolor="rgba(148,163,184,0.15)",
    )
    return figure


def city_bubbles(
    rows: list[dict[str, Any]],
    *,
    city_key: str = "city",
    value: str = "net_revenue",
    height: int = 420,
) -> tuple[go.Figure | None, list[str]]:
    """Store cities sized by a measure.

    Returns the figure **and the cities it could not place**, which the caller
    is expected to render. A map quietly missing three stores under-reports a
    region and gives the reader no way to notice.
    """
    data = _frame(rows)
    if data.empty or city_key not in data or value not in data:
        return None, []

    coordinates, missing = geo.locate([str(item) for item in data[city_key]])
    plotted = data[data[city_key].astype(str).isin(coordinates)]
    if plotted.empty:
        return None, missing

    magnitude = plotted[value].abs()
    largest = float(magnitude.max()) or 1.0

    figure = go.Figure(
        go.Scattergeo(
            lon=[coordinates[str(city)][1] for city in plotted[city_key]],
            lat=[coordinates[str(city)][0] for city in plotted[city_key]],
            text=plotted[city_key].astype(str),
            customdata=plotted[value],
            mode="markers",
            marker={
                "size": (magnitude / largest * 34 + 8).tolist(),
                "color": plotted[value],
                "colorscale": [[0, "rgba(34,211,238,0.55)"], [1, SEMANTIC["accent"]]],
                "line": {"width": 1, "color": "rgba(255,255,255,0.4)"},
                "opacity": 0.85,
            },
            hovertemplate="%{text}<br><b>%{customdata:,.0f}</b><extra></extra>",
        )
    )
    figure.update_layout(**_layout(height))
    figure.update_geos(
        scope="usa",
        bgcolor="rgba(0,0,0,0)",
        lakecolor="rgba(0,0,0,0)",
        landcolor="rgba(148,163,184,0.05)",
        subunitcolor="rgba(148,163,184,0.18)",
        countrycolor="rgba(148,163,184,0.25)",
    )
    return figure, missing


# ── Comparison ───────────────────────────────────────────────────────


def bubble(
    rows: list[dict[str, Any]],
    *,
    x: str,
    y: str,
    size: str,
    label: str,
    colour_by: str | None = None,
    height: int = 420,
) -> go.Figure | None:
    """Three measures at once: two positions and an area.

    Area, not radius — Plotly's ``sizemode="area"`` is set for that reason. A
    bubble scaled by radius exaggerates large values by the square, which is
    how a chart makes one store look four times the size of another that is
    twice as large.
    """
    data = _frame(rows)
    if data.empty or not {x, y, size, label} <= set(data.columns):
        return None

    magnitude = data[size].abs()
    largest = float(magnitude.max()) or 1.0

    if colour_by and colour_by in data:
        groups = list(dict.fromkeys(data[colour_by].astype(str)))
        colours = [SERIES[groups.index(str(item)) % len(SERIES)] for item in data[colour_by]]
    else:
        colours = [SEMANTIC["accent"]] * len(data)

    figure = go.Figure(
        go.Scatter(
            x=data[x],
            y=data[y],
            mode="markers",
            text=data[label].astype(str),
            customdata=data[size],
            marker={
                "size": magnitude,
                "sizemode": "area",
                "sizeref": 2.0 * largest / (46.0**2),
                "sizemin": 5,
                "color": colours,
                "opacity": 0.8,
                "line": {"width": 1, "color": "rgba(255,255,255,0.35)"},
            },
            hovertemplate=(
                "%{text}<br>" + f"{x}: " + "%{x:,.2f}<br>" + f"{y}: " + "%{y:,.2f}<br>"
                f"{size}: " + "%{customdata:,.0f}<extra></extra>"
            ),
        )
    )
    layout = _layout(height)
    layout["xaxis"] = {**_AXIS, "title": {"text": x}}
    layout["yaxis"] = {**_AXIS, "title": {"text": y}}
    figure.update_layout(**layout)
    return figure


def radar(
    rows: list[dict[str, Any]],
    *,
    label: str,
    axes: list[str],
    height: int = 400,
    limit: int = 4,
) -> tuple[go.Figure | None, bool]:
    """A profile across several measures at once.

    Returns the figure and whether the axes were **normalised**, which they
    almost always are: on-time rate lives in 0–1 and lead time in days, and
    drawing them on one radius without rescaling produces a shape determined
    by units rather than by performance.

    Normalisation is min–max *within the rows shown*, so positions are
    relative to this comparison set and to nothing else. The caller renders
    that caveat — a radar read as absolute is a radar read wrongly.
    """
    data = _frame(rows)
    present = [name for name in axes if name in data]
    if data.empty or label not in data or len(present) < 3:
        return None, False

    subject = data.head(limit)
    normalised = False
    scaled = subject.copy()
    for name in present:
        column = subject[name].astype(float)
        span = float(column.max() - column.min())
        if span > 0:
            scaled[name] = (column - column.min()) / span
            normalised = True
        else:
            scaled[name] = 0.5

    figure = go.Figure()
    for index, (_, row) in enumerate(scaled.iterrows()):
        colour = SERIES[index % len(SERIES)]
        figure.add_trace(
            go.Scatterpolar(
                r=[float(row[name]) for name in [*present, present[0]]],
                theta=[*present, present[0]],
                name=str(row[label]),
                mode="lines",
                line={"color": colour, "width": 2},
                fill="toself",
                fillcolor=_alpha(colour, 0.12),
                hovertemplate="%{theta}: %{r:.2f}<extra>%{fullData.name}</extra>",
            )
        )

    layout = _layout(height, showlegend=True)
    layout.pop("xaxis", None)
    layout.pop("yaxis", None)
    figure.update_layout(**layout)
    figure.update_polars(
        bgcolor="rgba(255,255,255,0.015)",
        radialaxis={
            "visible": True,
            "range": [0, 1],
            "gridcolor": "rgba(148,163,184,0.14)",
            "tickfont": {"size": 9, "color": INK["faint"]},
        },
        angularaxis={
            "gridcolor": "rgba(148,163,184,0.14)",
            "tickfont": {"size": 10, "color": INK["muted"]},
        },
    )
    return figure, normalised


def bridge(
    *,
    baseline: float,
    steps: list[dict[str, Any]],
    label: str = "subject",
    value: str = "impact_amount",
    height: int = 340,
    limit: int = 6,
) -> go.Figure | None:
    """A period-over-period movement as named contributions.

    Everything named is drawn, and whatever the named steps do not account for
    becomes an explicit "unexplained" bar rather than being absorbed into the
    total. A bridge that silently balances is a bridge that claims complete
    attribution it does not have.
    """
    if not steps:
        return None

    ranked = sorted(steps, key=lambda item: -abs(float(item.get(value) or 0)))[:limit]
    named = [(str(item.get(label, "")), float(item.get(value) or 0)) for item in ranked]
    return waterfall(named, start=baseline, height=height)
