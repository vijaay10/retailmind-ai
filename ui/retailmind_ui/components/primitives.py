"""Surfaces, headers, statistics, and states.

The building blocks every workspace composes from. Three of them carry weight
beyond layout:

``empty`` **always takes a reason.** "No stockouts" and "the inventory service
is unreachable" render identically when a panel simply fails to appear, and
they call for opposite responses.

``skeleton`` **holds the exact shape of what is loading.** A spinner tells a
reader to wait; a skeleton tells them what they are waiting for, and stops the
layout jumping when it arrives.

``failure`` **shows the API's own words.** This platform's errors carry a
``detail`` and usually a ``hint`` that names the fix. Replacing that with
"Something went wrong" throws away the only actionable part.
"""

from typing import Any

import pandas as pd
import streamlit as st

from retailmind_ui.design import (
    INK,
    SEMANTIC,
    confidence_colour,
    escape,
    html,
)
from retailmind_ui.formatting import label as humanise
from retailmind_ui.formatting import number

# ── Page furniture ───────────────────────────────────────────────────


def workspace_header(
    title: str,
    *,
    eyebrow: str = "",
    summary: str = "",
    meta: list[str] | None = None,
) -> None:
    """The top of a workspace: what this is, and what it is for."""
    parts = ['<div class="rm-ws-head">']
    if eyebrow:
        parts.append(f'<div class="rm-eyebrow">{escape(eyebrow)}</div>')
    parts.append(f"<h1>{escape(title)}</h1>")
    if summary:
        parts.append(f'<p class="rm-ws-sub">{escape(summary)}</p>')
    if meta:
        chips = "".join(
            f'<span class="rm-meta-chip">{escape(item)}</span>' for item in meta if item
        )
        parts.append(f'<div class="rm-meta-row">{chips}</div>')
    parts.append("</div>")

    html(
        "".join(parts)
        + """
        <style>
        .rm-ws-head { margin-bottom: 1.4rem; }
        .rm-ws-head h1 { margin: 0.15rem 0 0.3rem; }
        .rm-ws-sub { color: var(--rm-muted); margin: 0; max-width: 72ch; }
        .rm-meta-row { display: flex; flex-wrap: wrap; gap: 0.4rem; margin-top: 0.7rem; }
        .rm-meta-chip {
            font-size: 0.72rem; color: var(--rm-faint);
            border: 1px solid var(--rm-line); border-radius: 999px;
            padding: 0.12rem 0.6rem; font-variant-numeric: tabular-nums;
        }
        </style>
        """
    )


def section(title: str, note: str = "", *, accent: str = "") -> None:
    """A titled band within a workspace."""
    bar = accent or INK["line_strong"]
    html(
        f"""
        <div class="rm-section">
            <span class="rm-section-bar" style="background:{bar}"></span>
            <div>
                <div class="rm-section-title">{escape(title)}</div>
                {f'<div class="rm-section-note">{escape(note)}</div>' if note else ""}
            </div>
        </div>
        <style>
        .rm-section {{ display: flex; gap: 0.7rem; margin: 1.5rem 0 0.85rem; }}
        .rm-section-bar {{ width: 3px; border-radius: 2px; flex: none; }}
        .rm-section-title {{ font-size: 1.02rem; font-weight: 620; letter-spacing: -0.015em; }}
        .rm-section-note {{ font-size: 0.8125rem; color: var(--rm-muted); margin-top: 0.15rem; }}
        </style>
        """
    )


def panel_open(*, glass: bool = False, padding: str = "1.15rem 1.25rem") -> None:
    """Open a raw design-system panel. Pair with :func:`panel_close`."""
    css = "rm-panel rm-glass" if glass else "rm-panel"
    html(f'<div class="{css}" style="padding:{padding}">')


def panel_close() -> None:
    html("</div>")


def divider(space: str = "1.25rem") -> None:
    html(f'<div style="height:1px;background:var(--rm-line);margin:{space} 0"></div>')


# ── Statistics ───────────────────────────────────────────────────────


def stat_row(items: list[dict[str, Any]], *, columns: int = 4) -> None:
    """A row of figures.

    Wraps rather than compressing past four across. Streamlit's columns divide
    a fixed width, and a currency figure that clips is a wrong number.
    """
    if not items:
        return
    per_row = max(1, min(columns, 4))
    for start in range(0, len(items), per_row):
        chunk = items[start : start + per_row]
        for slot, item in zip(st.columns(len(chunk)), chunk, strict=True):
            with slot:
                stat(**item)


def stat(
    *,
    label: str,
    value: str,
    delta: str | None = None,
    direction: str = "",
    note: str = "",
    accent: str = "",
) -> None:
    """One figure, its movement, and what qualifies it.

    Direction is taken from the API rather than inferred from the sign: the
    executive surface calls a move "flat" below half a percent, and a console
    that colours a 0.2% wobble green is overriding a judgement the platform
    already made.
    """
    tone = {"up": SEMANTIC["positive"], "down": SEMANTIC["critical"]}.get(direction, INK["muted"])
    arrow = {"up": "↑", "down": "↓"}.get(direction, "")
    colour = accent or INK["text"]

    html(
        f"""
        <div class="rm-stat">
            <div class="rm-eyebrow">{escape(label)}</div>
            <div class="rm-stat-value" style="color:{colour}">{escape(value)}</div>
            {
            f'<div class="rm-stat-delta" style="color:{tone}">{arrow} {escape(delta)}</div>'
            if delta
            else ""
        }
            {f'<div class="rm-stat-note">{escape(note)}</div>' if note else ""}
        </div>
        <style>
        .rm-stat {{
            background: linear-gradient(180deg, rgba(255,255,255,0.035), rgba(255,255,255,0.01));
            border: 1px solid var(--rm-line);
            border-radius: 12px;
            padding: 0.85rem 1rem;
            height: 100%;
        }}
        .rm-stat-value {{
            font-size: 1.6rem; font-weight: 620; letter-spacing: -0.025em;
            font-variant-numeric: tabular-nums; margin-top: 0.3rem; line-height: 1.15;
        }}
        .rm-stat-delta {{ font-size: 0.8125rem; font-weight: 600; margin-top: 0.2rem; }}
        .rm-stat-note {{ font-size: 0.75rem; color: var(--rm-faint); margin-top: 0.35rem; }}
        </style>
        """
    )


def data_health(sources: list[str], *, as_of: str, cadence: str = "Updated daily") -> None:
    """What feeds this screen, and how fresh it is — honestly.

    The platform runs one daily warehouse batch, not an independently
    tracked pipeline per source: there is exactly one real freshness date
    (``as_of``, the same value every screen's date controls default to),
    not six. Listing the sources without inventing six different clocks for
    them is the whole point — a reader asking "how complete is this?"
    deserves the true shape of the answer, not a fabricated one that merely
    looks more granular.
    """
    chips = "".join(f'<span class="rm-health-chip">{escape(src)}</span>' for src in sources)
    html(
        f"""
        <div class="rm-health">
            <div class="rm-health-row">{chips}</div>
            <div class="rm-health-note">
                {escape(cadence)} · data through {escape(as_of)}. One warehouse refresh feeds
                every source above — there is no independent per-source timestamp to show.
            </div>
        </div>
        <style>
        .rm-health {{ margin: 0.7rem 0 0.3rem; }}
        .rm-health-row {{ display: flex; flex-wrap: wrap; gap: 0.4rem; margin-bottom: 0.4rem; }}
        .rm-health-chip {{
            font-size: 0.72rem; color: var(--rm-muted);
            border: 1px solid var(--rm-line); border-radius: 999px;
            padding: 0.15rem 0.65rem;
        }}
        .rm-health-note {{ font-size: 0.75rem; color: var(--rm-faint); }}
        </style>
        """
    )


def chip(text: str, *, colour: str = "", filled: bool = False) -> str:
    """An inline label. Returns markup for composition into a larger fragment."""
    tone = colour or INK["muted"]
    background = f"background:{tone}1f;" if filled else ""
    return f'<span class="rm-chip" style="color:{tone};{background}">{escape(text)}</span>'


def meter(value: float, *, colour: str = "", width: str = "100%") -> str:
    """A proportion bar, as markup."""
    tone = colour or confidence_colour(value)
    pct = max(0.0, min(1.0, float(value))) * 100
    return (
        f'<div class="rm-meter" style="width:{width}">'
        f'<span style="width:{pct:.1f}%;background:{tone}"></span></div>'
    )


# ── States ───────────────────────────────────────────────────────────


def empty(reason: str, *, what: str = "Nothing to show", icon: str = "○") -> None:
    """Draw an absence, with its reason.

    The reason is required rather than optional: an empty panel with no
    explanation is indistinguishable from a broken one.
    """
    html(
        f"""
        <div class="rm-empty">
            <div class="rm-empty-icon">{escape(icon)}</div>
            <div>
                <div class="rm-empty-title">{escape(what)}</div>
                <div class="rm-empty-reason">{escape(reason)}</div>
            </div>
        </div>
        <style>
        .rm-empty {{
            display: flex; gap: 0.85rem; align-items: flex-start;
            border: 1px dashed var(--rm-line-strong);
            border-radius: 14px; padding: 1.1rem 1.25rem;
            background: rgba(255,255,255,0.012);
        }}
        .rm-empty-icon {{ color: var(--rm-faint); font-size: 1.1rem; line-height: 1.4; }}
        .rm-empty-title {{ font-weight: 600; font-size: 0.9375rem; }}
        .rm-empty-reason {{ color: var(--rm-muted); font-size: 0.8125rem; margin-top: 0.2rem; }}
        </style>
        """
    )


def failure(message: str, *, what: str = "This did not load") -> None:
    """An error, in the API's own words."""
    html(
        f"""
        <div class="rm-fail">
            <div class="rm-fail-title">{escape(what)}</div>
            <div class="rm-fail-detail">{escape(message)}</div>
        </div>
        <style>
        .rm-fail {{
            border: 1px solid {SEMANTIC["critical"]}55;
            background: {SEMANTIC["critical"]}0f;
            border-radius: 14px; padding: 0.95rem 1.15rem;
        }}
        .rm-fail-title {{ font-weight: 620; color: {SEMANTIC["critical"]}; font-size: 0.875rem; }}
        .rm-fail-detail {{ color: var(--rm-text); font-size: 0.8125rem; margin-top: 0.3rem; }}
        </style>
        """
    )


def workspace_error(error: Exception, *, what: str = "This did not load") -> None:
    """A failed API call, read correctly for who's asking.

    Every workspace used to route every ``ApiError`` through the same red
    "did not load" panel — accurate for a genuine outage, actively
    misleading for a brand-new tenant whose warehouse simply hasn't been
    provisioned yet (a 503 `dependency-unavailable`, Prompt 12.5's
    per-tenant isolation fix's own honest failure mode for that case). The
    first reads as "the product is broken"; the second is an expected,
    temporary, first-day state. Import-time check on
    ``error.is_dependency_unavailable`` rather than a second exception type,
    so every existing ``except ApiError`` call site gets the distinction by
    changing one function name, not by learning a new one.
    """
    if getattr(error, "is_dependency_unavailable", False):
        html(
            f"""
            <div class="rm-pending">
                <div class="rm-pending-icon">◐</div>
                <div>
                    <div class="rm-pending-title">Your workspace is still being set up</div>
                    <div class="rm-pending-detail">
                        This screen needs your data to be connected first. Once a data
                        source finishes processing, it appears here automatically —
                        nothing to do on this screen in the meantime.
                    </div>
                </div>
            </div>
            <style>
            .rm-pending {{
                display: flex; gap: 0.85rem; align-items: flex-start;
                border: 1px dashed {SEMANTIC["ai"]}55;
                background: {SEMANTIC["ai"]}0c;
                border-radius: 14px; padding: 1.1rem 1.25rem;
            }}
            .rm-pending-icon {{ color: {SEMANTIC["ai"]}; font-size: 1.1rem; line-height: 1.4; }}
            .rm-pending-title {{ font-weight: 600; font-size: 0.9375rem; }}
            .rm-pending-detail {{
                color: var(--rm-muted); font-size: 0.8125rem; margin-top: 0.25rem;
                max-width: 60ch;
            }}
            </style>
            """
        )
        return
    failure(str(error), what=what)


def skeleton(*, rows: int = 3, height: str = "58px") -> None:
    """A loading placeholder in the shape of what is coming.

    Shaped rather than generic so the layout does not jump when the data
    lands, and so the reader knows what is being fetched.
    """
    blocks = "".join(
        f'<div class="rm-skeleton" style="height:{height};margin-bottom:0.55rem"></div>'
        for _ in range(rows)
    )
    html(f"<div>{blocks}</div>")


def working(message: str) -> None:
    """A live progress line for work that takes seconds, not milliseconds."""
    html(
        f"""
        <div class="rm-working">
            <span class="rm-dot rm-live" style="background:{SEMANTIC["ai"]}"></span>
            <span>{escape(message)}</span>
        </div>
        <style>
        .rm-working {{
            display: flex; align-items: center; gap: 0.55rem;
            color: var(--rm-muted); font-size: 0.8125rem;
            padding: 0.5rem 0;
        }}
        </style>
        """
    )


# ── Tables ───────────────────────────────────────────────────────────


def frame(rows: list[dict[str, Any]], *, columns: list[str] | None = None) -> pd.DataFrame:
    """Rows to a dataframe with readable column names."""
    if not rows:
        return pd.DataFrame()
    data = pd.DataFrame(rows)
    if columns:
        data = data[[name for name in columns if name in data.columns]]
    return data.rename(columns={name: humanise(name) for name in data.columns})


def table(
    rows: list[dict[str, Any]],
    *,
    columns: list[str] | None = None,
    empty_reason: str = "",
    height: int | None = None,
    config: dict[str, Any] | None = None,
) -> None:
    """A read-only grid.

    Native rather than a component: `st.dataframe` inherits the theme, sorts
    and searches without a round trip, and stays inspectable by the test
    harness. The dense analyst grids that genuinely need pinning and
    multi-sort use :func:`analyst_grid` instead.
    """
    if not rows:
        empty(empty_reason or "The query returned no rows for this period.")
        return

    extra: dict[str, Any] = {}
    if height is not None:
        extra["height"] = height
    if config:
        extra["column_config"] = {humanise(key): value for key, value in config.items()}

    st.dataframe(frame(rows, columns=columns), width="stretch", hide_index=True, **extra)


def analyst_grid(
    rows: list[dict[str, Any]],
    *,
    columns: list[str] | None = None,
    empty_reason: str = "",
    height: int = 420,
    pinned: str | None = None,
) -> None:
    """A dense grid for surfaces analysts actually work in.

    AgGrid earns its weight exactly twice in this console — the store league
    table and the SKU-level inventory lists — where column filtering, multi-
    sort and a pinned identifier are how the work is done. Everywhere else the
    native grid is better: themed, testable, no extra payload.

    Degrades to the native grid if the component is unavailable, because a
    missing optional dependency should cost polish, not the data.
    """
    if not rows:
        empty(empty_reason or "No rows for this selection.")
        return

    try:
        from st_aggrid import AgGrid, GridOptionsBuilder  # noqa: PLC0415
    except ImportError:
        table(rows, columns=columns, height=height)
        return

    data = frame(rows, columns=columns)
    builder = GridOptionsBuilder.from_dataframe(data)
    builder.configure_default_column(
        filter=True, sortable=True, resizable=True, wrapHeaderText=True, autoHeaderHeight=True
    )
    if pinned and humanise(pinned) in data.columns:
        builder.configure_column(humanise(pinned), pinned="left")

    AgGrid(
        data,
        gridOptions=builder.build(),
        height=height,
        # The "streamlit" theme follows the app's own light/dark setting. The
        # named AgGrid themes are light-only, and a white grid dropped into a
        # dark console is the single most obvious way an embedded component
        # announces that it was bolted on.
        theme="streamlit",
        fit_columns_on_grid_load=True,
        allow_unsafe_jscode=False,
        custom_css={
            ".ag-root-wrapper": {
                "border": "1px solid rgba(148,163,184,0.14)",
                "border-radius": "12px",
                "background": "transparent",
            },
            ".ag-header": {"background": "rgba(255,255,255,0.03)", "border": "none"},
            ".ag-row": {"background": "transparent", "border-color": "rgba(148,163,184,0.08)"},
            ".ag-cell": {"font-variant-numeric": "tabular-nums"},
        },
    )


def bar_column(rows: list[dict[str, Any]], column: str) -> dict[str, Any]:
    """Column config that draws a magnitude bar in a numeric column."""
    values = [float(row.get(column) or 0) for row in rows] or [0.0]
    return {
        column: st.column_config.ProgressColumn(
            humanise(column),
            min_value=min(0.0, min(values)),
            max_value=max(values) or 1.0,
            format="%.0f",
        )
    }


def money(value: Any) -> str:
    return number(value, "currency")
