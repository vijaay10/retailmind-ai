"""What the chart builders must never draw.

A chart shape is a claim. These tests pin the claims each builder is allowed
to make, and — more importantly — the ones it must refuse to make when the
data cannot support them.
"""

from typing import Any

import pytest

from retailmind_ui import charts, geo

NESTED = [
    {"region": "Midwest", "category": "Outerwear", "net_revenue": 540_000.0},
    {"region": "Southeast", "category": "Outerwear", "net_revenue": 480_000.0},
    {"region": "West", "category": "Denim", "net_revenue": 390_000.0},
]


# ── Every builder degrades rather than raising ───────────────────────


@pytest.mark.parametrize(
    ("name", "kwargs"),
    [
        ("trend", {"x": "business_date", "y": "net_revenue"}),
        ("ranked_bars", {"label": "region", "value": "net_revenue"}),
        ("contribution", {"label": "subject", "value": "impact_amount"}),
        ("treemap", {"path": ["region"], "value": "net_revenue"}),
        ("matrix_heatmap", {"row_key": "a", "column_key": "b", "value": "c"}),
        ("funnel", {"stage": "stage", "value": "customers"}),
        ("region_choropleth", {}),
        ("bubble", {"x": "a", "y": "b", "size": "c", "label": "d"}),
        ("scatter_quadrant", {"x": "a", "y": "b", "label": "c"}),
        ("cohort_heatmap", {}),
        ("forecast_band", {}),
    ],
)
def test_a_builder_given_nothing_returns_nothing(name: str, kwargs: dict[str, Any]) -> None:
    """A workspace calls these with whatever the API returned, including an
    empty list. Raising would take a screen down over a quiet day."""
    assert getattr(charts, name)([], **kwargs) is None


@pytest.mark.parametrize(
    ("name", "kwargs"),
    [
        ("ranked_bars", {"label": "missing", "value": "net_revenue"}),
        ("treemap", {"path": ["missing"], "value": "net_revenue"}),
        ("bubble", {"x": "net_revenue", "y": "missing", "size": "net_revenue", "label": "region"}),
        ("scatter_quadrant", {"x": "net_revenue", "y": "missing", "label": "region"}),
    ],
)
def test_a_missing_column_draws_nothing_rather_than_crashing(
    name: str, kwargs: dict[str, Any]
) -> None:
    """API row shapes drift. A column that is not there must produce an empty
    panel with a reason, not a traceback in the middle of a dashboard."""
    assert getattr(charts, name)(NESTED, **kwargs) is None


# ── Composition ──────────────────────────────────────────────────────


def test_a_treemap_addresses_nodes_by_path_not_by_label() -> None:
    """ "Outerwear" exists under two regions. Identifying nodes by label alone
    makes Plotly merge them into one box under the wrong parent — and the
    failure renders as an empty chart rather than an error."""
    figure = charts.treemap(NESTED, path=["region", "category"], value="net_revenue")
    assert figure is not None

    trace = figure.data[0]
    assert "Midwest / Outerwear" in trace.ids
    assert "Southeast / Outerwear" in trace.ids
    assert len(set(trace.ids)) == len(trace.ids)


def test_treemap_parents_carry_no_value_of_their_own() -> None:
    """Parents inherit their children's total. Giving a parent its own value
    on top would double-count it into the root."""
    figure = charts.treemap(NESTED, path=["region", "category"], value="net_revenue")
    trace = figure.data[0]  # type: ignore[union-attr]
    parents = [
        value for node, value in zip(trace.ids, trace.values, strict=True) if "/" not in node
    ]
    assert set(parents) == {0.0}


def test_a_treemap_excludes_negatives_rather_than_drawing_them() -> None:
    """Area cannot be negative. A negative rectangle either vanishes or, worse,
    renders identically to a positive one."""
    figure = charts.treemap(
        [{"category": "A", "v": 100.0}, {"category": "B", "v": -50.0}],
        path=["category"],
        value="v",
    )
    assert figure is not None
    assert list(figure.data[0].labels) == ["A"]


def test_a_heatmap_leaves_absent_combinations_blank() -> None:
    """A category a region does not stock has no revenue, which is not revenue
    of zero — and colouring it at the bottom of the scale invents an
    underperformer."""
    import math

    figure = charts.matrix_heatmap(
        NESTED, row_key="region", column_key="category", value="net_revenue"
    )
    assert figure is not None
    grid = figure.data[0].z
    assert any(math.isnan(cell) for row in grid for cell in row)


# ── Geography ────────────────────────────────────────────────────────


def test_a_region_map_paints_every_state_in_the_region() -> None:
    figure = charts.region_choropleth(
        [{"region": "Northeast", "net_revenue": 1000.0}], value="net_revenue"
    )
    assert figure is not None
    assert set(figure.data[0].locations) == set(geo.states_for("Northeast"))


def test_the_region_map_says_it_is_not_state_level() -> None:
    """A regional figure painted across states reads as per-state measurement
    unless the chart says otherwise on every cell."""
    figure = charts.region_choropleth([{"region": "West", "net_revenue": 1.0}], value="net_revenue")
    assert "not state-level" in figure.data[0].hovertemplate  # type: ignore[union-attr]


def test_a_city_the_gazetteer_does_not_know_is_reported_not_dropped() -> None:
    """A map quietly missing three stores under-reports a region and gives the
    reader no way to notice."""
    figure, missing = charts.city_bubbles(
        [{"city": "Miami", "net_revenue": 5.0}, {"city": "Atlantis", "net_revenue": 1.0}]
    )
    assert figure is not None
    assert missing == ["Atlantis"]
    assert len(figure.data[0].lat) == 1


def test_every_estate_city_resolves() -> None:
    """The gazetteer must cover the estate it ships with, or the demo map lies
    on first run."""
    _, missing = geo.locate(
        ["Miami", "Chicago", "New York", "Seattle", "Phoenix", "St Louis", "Salt Lake City"]
    )
    assert missing == []


# ── Comparison ───────────────────────────────────────────────────────


def test_bubbles_are_scaled_by_area_not_radius() -> None:
    """Radius scaling exaggerates large values by the square, which is how one
    store comes to look four times another that is twice its size."""
    figure = charts.bubble(
        [{"a": 1.0, "b": 2.0, "c": 10.0, "d": "x"}, {"a": 2.0, "b": 1.0, "c": 40.0, "d": "y"}],
        x="a",
        y="b",
        size="c",
        label="d",
    )
    assert figure is not None
    assert figure.data[0].marker.sizemode == "area"


def test_a_radar_reports_that_it_normalised() -> None:
    """On-time rate lives in 0–1 and lead time in days. Drawing them on one
    radius without rescaling produces a shape decided by units — so the caller
    has to be told, and the caller renders the caveat."""
    figure, normalised = charts.radar(
        [{"n": "S1", "a": 1.0, "b": 500.0, "c": 3.0}, {"n": "S2", "a": 4.0, "b": 2.0, "c": 1.0}],
        label="n",
        axes=["a", "b", "c"],
    )
    assert figure is not None
    assert normalised is True
    assert all(0.0 <= value <= 1.0 for trace in figure.data for value in trace.r)


def test_a_radar_needs_three_axes_to_be_a_shape() -> None:
    """Two axes is a line pretending to be a profile."""
    figure, _ = charts.radar([{"n": "S1", "a": 1.0, "b": 2.0}], label="n", axes=["a", "b"])
    assert figure is None


def test_a_funnel_keeps_the_order_it_was_given() -> None:
    """Sorting a funnel by size would invent a progression that is not the
    one the business has."""
    stages = [
        {"stage": "New", "customers": 9388},
        {"stage": "Repeat", "customers": 6203},
        {"stage": "Loyal", "customers": 3802},
    ]
    figure = charts.funnel(stages, stage="stage", value="customers")
    assert list(figure.data[0].y) == ["New", "Repeat", "Loyal"]  # type: ignore[union-attr]


def test_a_sankey_needs_both_nodes_and_links() -> None:
    assert charts.sankey([], []) is None
    assert charts.sankey(["A", "B"], []) is None
    assert charts.sankey(["A", "B"], [(0, 1, 5.0)]) is not None


def test_a_bridge_ranks_by_magnitude_and_keeps_direction() -> None:
    """A bridge is read left to right as a path. Ordering by absolute size puts
    the biggest movement first without hiding which way it went."""
    figure = charts.bridge(
        baseline=1000.0,
        steps=[
            {"subject": "small", "impact_amount": -10.0},
            {"subject": "large", "impact_amount": -500.0},
            {"subject": "gain", "impact_amount": 200.0},
        ],
    )
    assert figure is not None
    labels = list(figure.data[0].x)
    assert labels[1] == "large"
    assert figure.data[0].y[1] == -500.0


# ── Composed dashboards ──────────────────────────────────────────────

TREND = [{"business_date": f"2026-07-{d:02d}", "net_revenue": 100.0 * d} for d in range(1, 8)]
BREAKDOWN = [{"region": "Midwest", "net_revenue": 500.0}, {"region": "West", "net_revenue": 300.0}]
MATRIX = [
    {"region": "Midwest", "category": "Denim", "net_revenue": 200.0},
    {"region": "West", "category": "Denim", "net_revenue": 100.0},
]


def test_a_composed_dashboard_keeps_empty_panels_in_place() -> None:
    """A grid that reflows around a failed query changes the position and size
    of every other panel, and two exports of "the same" dashboard stop being
    comparable week to week."""
    from retailmind_ui import dashboards  # noqa: PLC0415

    full = dashboards.revenue_dashboard(trend=TREND, breakdown=BREAKDOWN, matrix=MATRIX)
    empty = dashboards.revenue_dashboard(trend=[], breakdown=[], matrix=[])

    assert len(full.data) == 3
    assert len(empty.data) == 0

    # Same grid either way: three panel titles, plus a stated reason per panel.
    reasons = [note.text for note in empty.layout.annotations if note.text.startswith("<i>")]
    assert len(reasons) == 3
    assert all(reason.strip("<i>/") for reason in reasons)


def test_an_empty_panel_says_why_rather_than_going_blank() -> None:
    from retailmind_ui import dashboards  # noqa: PLC0415

    figure = dashboards.revenue_dashboard(trend=TREND, breakdown=[], matrix=MATRIX)
    reasons = " ".join(
        note.text for note in figure.layout.annotations if note.text.startswith("<i>")
    )
    assert "no region rows" in reasons


def test_domain_traces_get_a_cell_that_can_hold_them() -> None:
    """A choropleth in a cartesian cell renders as a blank white square with no
    error — the specific failure the composed module exists to have solved."""
    from retailmind_ui import dashboards  # noqa: PLC0415

    estate = dashboards.estate_dashboard(
        regions=[{"region": "West", "net_revenue": 5.0}],
        stores=[{"store_name": "A", "net_revenue": 1.0}],
    )
    assert [trace.type for trace in estate.data] == ["choropleth", "bar"]

    inventory = dashboards.inventory_dashboard(
        ageing=[{"aging_bucket": "0-30", "inventory_value": 5.0}],
        excess=[{"category": "Denim", "excess_value": 9.0}],
        risk=[{"region": "W", "category": "D", "stockout_rate": 0.2}],
    )
    assert "treemap" in [trace.type for trace in inventory.data]


def test_a_composed_heatmap_still_leaves_absent_cells_blank() -> None:
    """The rule does not weaken because the panel is inside a grid."""
    from retailmind_ui import dashboards  # noqa: PLC0415

    figure = dashboards.revenue_dashboard(
        trend=TREND,
        breakdown=BREAKDOWN,
        matrix=[
            {"region": "Midwest", "category": "Denim", "net_revenue": 200.0},
            {"region": "West", "category": "Footwear", "net_revenue": 100.0},
        ],
    )
    heatmap = next(trace for trace in figure.data if trace.type == "heatmap")
    assert any(cell is None for row in heatmap.z for cell in row)


def test_only_panels_that_share_a_meaning_share_an_axis() -> None:
    """Linking a dated series to a category ranking makes them pan together
    into nonsense."""
    from retailmind_ui import dashboards  # noqa: PLC0415

    revenue = dashboards.revenue_dashboard(trend=TREND, breakdown=BREAKDOWN, matrix=MATRIX)
    trend_axis = revenue.data[0].xaxis
    bars_axis = revenue.data[1].xaxis
    assert trend_axis != bars_axis

    outlook = dashboards.outlook_dashboard(
        actuals=TREND,
        forecast=[
            {
                "business_date": "2026-07-22",
                "forecast": 1.0,
                "forecast_lower": 0.5,
                "forecast_upper": 1.5,
            }
        ],
        effects=[{"feature": "weekday", "effect": -5.0}],
    )
    # Actuals and forecast are the same series either side of today.
    series_axes = {trace.xaxis for trace in outlook.data if trace.type == "scatter"}
    assert len(series_axes) == 1
