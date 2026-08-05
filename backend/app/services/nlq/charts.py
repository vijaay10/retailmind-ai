"""Choosing a chart from the shape of a result.

Chart type is inferred from the data's structure rather than asked for,
because the structure is what makes a shape honest or misleading. A line
across categories implies continuity between them — that Footwear sits
"between" Accessories and Outerwear — and there is no such ordering. So
categorical results get bars, time gets lines, and a single number gets a
number.

Every choice carries its reason, so a reader who disagrees can see what the
renderer assumed about their data.
"""

from typing import Any

from app.services.nlq.contracts import ChartSpec, ChartType

#: Dimension keys that represent time. A result grouped by one of these has a
#: natural ordering and can be drawn as a line.
TIME_DIMENSIONS = frozenset({"business_date", "position_date", "origin_date", "cohort_week"})

#: Above this many categories a vertical bar chart's labels collide and the
#: chart becomes a decorative band. Horizontal bars keep labels readable.
HORIZONTAL_THRESHOLD = 8

#: Beyond this, no chart helps. A table is the honest presentation.
CHART_LIMIT = 40


def choose(
    *, dimensions: tuple[str, ...], metrics: tuple[str, ...], rows: list[dict[str, Any]]
) -> ChartSpec:
    """Pick the chart shape the result actually supports."""
    if not rows or not metrics:
        return ChartSpec(
            type=ChartType.TABLE,
            rationale="Nothing to plot: the query returned no measures or no rows.",
        )

    if not dimensions:
        return ChartSpec(
            type=ChartType.BIG_NUMBER,
            y=(metrics[0],),
            title=_title(metrics[0], None),
            rationale=(
                "One row and no grouping, so the result is a single figure. A "
                "chart of one bar communicates less than the number itself."
            ),
        )

    time_dimension = next((key for key in dimensions if key in TIME_DIMENSIONS), None)
    if time_dimension:
        return ChartSpec(
            type=ChartType.LINE,
            x=time_dimension,
            y=metrics[:3],
            title=_title(metrics[0], time_dimension),
            rationale=(
                "Grouped by a date, which has a natural order, so a line "
                "correctly implies continuity between adjacent points."
            ),
        )

    if len(rows) > CHART_LIMIT:
        return ChartSpec(
            type=ChartType.TABLE,
            rationale=(
                f"{len(rows)} categories is past the point where any chart "
                "reads clearly; the table is the honest presentation."
            ),
        )

    if len(rows) > HORIZONTAL_THRESHOLD:
        return ChartSpec(
            type=ChartType.HORIZONTAL_BAR,
            x=dimensions[0],
            y=(metrics[0],),
            title=_title(metrics[0], dimensions[0]),
            rationale=(
                f"{len(rows)} categories: horizontal bars keep the labels "
                "readable where vertical ones would collide."
            ),
        )

    return ChartSpec(
        type=ChartType.BAR,
        x=dimensions[0],
        y=metrics[:2],
        title=_title(metrics[0], dimensions[0]),
        rationale=(
            "Categories have no inherent order, so bars are used rather than a "
            "line — a line would imply these categories sit on a continuum."
        ),
    )


def _title(metric: str, dimension: str | None) -> str:
    label = metric.replace("_", " ").title()
    if dimension is None:
        return label
    return f"{label} by {dimension.replace('_', ' ').title()}"
