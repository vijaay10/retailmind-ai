"""Click-through drill-down over the governed dimensions.

**The drill path is a list of dimension names, not a query.** Descending
appends a *filter value* to the path; the console then asks the API for the
next dimension with that filter applied. Nothing typed or clicked becomes part
of a query string — the registry resolves the dimension name or refuses it,
which is what keeps an interactive surface as injection-proof as a static one.

**Why the bar chart is not the click target.** Streamlit's Plotly selection
channel emits point selections for box and lasso gestures, and — on bar traces
— not for a plain click. Wiring the drill to a drag-select would mean an
interaction that works when you happen to drag and silently does nothing when
you click, which is worse than a visible control. So the descent is an explicit
control, and the treemap beside it provides genuine click-through: Plotly zooms
a treemap branch client-side, with its own path bar for coming back.

**Every level is reachable backwards.** The breadcrumb is not decoration: a
drill-down you can only descend is a trap, and the most common thing an
analyst does after drilling is compare their branch against the one beside it.

**A level with one child is still shown.** Auto-skipping "helpfully" through
single-child levels hides that the level exists, and the reader draws
conclusions about a hierarchy they were never shown.
"""

from typing import Any

import streamlit as st

from retailmind_ui import charts
from retailmind_ui.components.primitives import empty
from retailmind_ui.design import SEMANTIC, escape, html


class DrillPath:
    """The path a reader has descended, held in session state.

    Keyed per surface so two drill-downs on one screen do not share a cursor.
    """

    def __init__(self, key: str, levels: list[str]) -> None:
        self.key = f"rm_drill_{key}"
        self.levels = levels
        st.session_state.setdefault(self.key, [])

    @property
    def filters(self) -> list[tuple[str, str]]:
        return list(st.session_state[self.key])

    @property
    def depth(self) -> int:
        return len(self.filters)

    @property
    def current_level(self) -> str | None:
        """The dimension being shown now, or ``None`` at the bottom."""
        return self.levels[self.depth] if self.depth < len(self.levels) else None

    @property
    def as_params(self) -> dict[str, str]:
        """Filters in the API's ``dimension:value`` form."""
        return {name: value for name, value in self.filters}

    def descend(self, value: str) -> None:
        level = self.current_level
        if level is None:
            return
        st.session_state[self.key] = [*self.filters, (level, value)]

    def ascend(self, depth: int) -> None:
        st.session_state[self.key] = self.filters[:depth]

    def reset(self) -> None:
        st.session_state[self.key] = []


def breadcrumb_controls(path: DrillPath, *, root: str = "All") -> None:
    """The trail, with every level clickable."""
    columns = st.columns([1, *([1] * path.depth), 6][: path.depth + 2])

    with columns[0]:
        if st.button(root, key=f"{path.key}_root", width="content", disabled=path.depth == 0):
            path.reset()
            st.rerun()

    for index, (level, value) in enumerate(path.filters):
        with columns[index + 1]:
            if st.button(
                f"{value}",
                key=f"{path.key}_crumb_{index}",
                width="content",
                help=f"back to {level}",
                disabled=index == path.depth - 1,
            ):
                path.ascend(index + 1)
                st.rerun()


def drill_chart(
    rows: list[dict[str, Any]],
    *,
    path: DrillPath,
    value: str,
    height: int = 340,
    empty_reason: str = "",
) -> None:
    """The current level: magnitudes, and a control that descends.

    Two shapes of the same rows. The bars rank them; the treemap nests them and
    zooms on click, which is the one genuinely client-side drill Plotly gives
    for free.
    """
    level = path.current_level
    if level is None:
        empty(
            "This is the deepest level configured for this surface. Step back up "
            "to compare against a neighbouring branch.",
            what="Bottom of the hierarchy",
        )
        return

    if not rows:
        empty(empty_reason or f"No {level} rows under this selection.")
        return

    ranked, nested = st.columns([1.15, 1])

    with ranked:
        figure = charts.ranked_bars(
            rows, label=level, value=value, height=height, colour=SEMANTIC["accent"], limit=15
        )
        if figure is not None:
            st.plotly_chart(figure, width="stretch", config={"displayModeBar": False})
        else:
            empty(f"The response carries no `{level}` column to draw.")

    with nested:
        figure = charts.treemap(rows, path=[level], value=value, height=height)
        if figure is not None:
            st.plotly_chart(figure, width="stretch", config={"displayModeBar": False})
            st.caption("Click a tile to zoom; the path bar above it steps back out.")

    descend_control(rows, path=path, level=level)


def descend_control(rows: list[dict[str, Any]], *, path: DrillPath, level: str) -> None:
    """The explicit descent: pick a value, go one level deeper."""
    remaining = path.levels[path.depth + 1 :]
    if not remaining:
        st.caption(
            f"`{level}` is the deepest level on this surface. "
            "Step back up to compare a neighbouring branch."
        )
        return

    values = [str(row.get(level)) for row in rows if row.get(level) is not None]
    if not values:
        return

    picker, action = st.columns([3, 1])
    with picker:
        chosen = st.selectbox(
            f"Drill into {remaining[0]}",
            values,
            key=f"{path.key}_pick_{path.depth}",
            label_visibility="collapsed",
        )
    with action:
        if st.button(f"Open {remaining[0]}", key=f"{path.key}_go_{path.depth}", width="stretch"):
            path.descend(str(chosen))
            st.rerun()

    html(
        f"""
        <div class="rm-drill-hint">
            Next level: <strong>{escape(remaining[0])}</strong>
            {escape("· then " + " · ".join(remaining[1:])) if len(remaining) > 1 else ""}
        </div>
        <style>
        .rm-drill-hint {{ font-size: 0.75rem; color: var(--rm-faint); margin-top: 0.2rem; }}
        .rm-drill-hint strong {{ color: var(--rm-muted); font-weight: 600; }}
        </style>
        """
    )
