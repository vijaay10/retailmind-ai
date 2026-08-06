"""Shared rendering.

**The rule this package exists to enforce: caveats are rendered, never
collapsed.** The backend spends real effort qualifying every figure — an
evidence tier on each root-cause finding, a confidence ceiling on each
estimate, a note saying what an analyst did *not* check, a reason on every
empty section. A console that draws the number and files the qualification
behind a disclosure triangle destroys all of that, because nobody opens the
triangle. So caveats render inline, beneath the thing they qualify, in the
normal reading path.

The corollary is that an empty result is drawn too. "No stockouts" and "the
inventory service is unreachable" look identical when a table simply does not
appear, and they call for opposite responses.

Nothing here computes. Every figure is passed through as the API returned it;
the only transformation is formatting.
"""

from typing import Any

import pandas as pd
import streamlit as st

from retailmind_ui.formatting import day, label, number, truncate

#: Evidence tiers and estimate bases carry a hard confidence ceiling in the
#: API. Colour tracks that ceiling so a reader can see at a glance which
#: findings are arithmetic and which are hypotheses.
TIER_STYLE: dict[str, tuple[str, str]] = {
    "arithmetic": ("🟦", "exact decomposition"),
    "measured": ("🟦", "arithmetic over observed data"),
    "mechanical": ("🟩", "a mechanism that necessarily applies"),
    "modelled": ("🟨", "uses a forecast or documented model"),
    "statistical": ("🟨", "measured deviation, mechanism plausible"),
    "assumed": ("🟧", "rests on an unmeasured parameter"),
    "associative": ("🟧", "correlated, no mechanism established"),
    "inferred": ("🟧", "consistent with the data, not established by it"),
    "derived": ("🟩", "computed through a stated relationship"),
    "unknown": ("⬜", "the platform cannot answer this"),
}

RISK_STYLE = {"low": "🟢", "medium": "🟡", "high": "🔴"}
SEVERITY_STYLE = {"info": "🔵", "warn": "🟡", "critical": "🔴"}


# ── Page furniture ───────────────────────────────────────────────────


def page_header(title: str, subtitle: str = "") -> None:
    st.markdown(f"## {title}")
    if subtitle:
        st.caption(subtitle)


def section(title: str, help_text: str = "") -> None:
    st.markdown(f"#### {title}")
    if help_text:
        st.caption(help_text)


# ── The honesty primitives ───────────────────────────────────────────


def caveats(items: list[str] | tuple[str, ...], *, title: str = "Before acting on this") -> None:
    """Render qualifications inline, in the reading path.

    Not an expander. A caveat behind a click is a caveat nobody reads, and the
    API attaches them precisely because acting on the number without them is
    the failure mode.
    """
    entries = [item for item in items if item]
    if not entries:
        return
    st.markdown(f"**{title}**")
    for item in entries:
        st.caption(f"· {item}")


def empty(reason: str, *, what: str = "Nothing to show") -> None:
    """Draw an absence, with its reason.

    "Nothing found" and "this never ran" look identical when a table simply
    does not appear, and they mean opposite things.
    """
    st.info(f"**{what}.** {reason}" if reason else f"**{what}.**")


def tier_badge(tier: str) -> str:
    icon, meaning = TIER_STYLE.get(tier, ("⬜", tier))
    return f"{icon} {tier} — {meaning}"


def error(message: str) -> None:
    st.error(message)


# ── Metrics ──────────────────────────────────────────────────────────


def kpi_row(tiles: list[dict[str, Any]], *, columns: int = 4) -> None:
    """A row of headline figures.

    Wraps rather than shrinking past four across: on a narrow viewport
    Streamlit compresses columns until the numbers truncate, and a truncated
    figure is worse than a stacked one.
    """
    if not tiles:
        return
    per_row = min(columns, 4)
    for start in range(0, len(tiles), per_row):
        chunk = tiles[start : start + per_row]
        for slot, tile in zip(st.columns(len(chunk)), chunk, strict=True):
            with slot:
                st.metric(
                    label=tile.get("label", ""),
                    value=tile.get("value", "—"),
                    delta=tile.get("delta"),
                    help=tile.get("help"),
                )


def kpis_from_api(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Adapt the report API's KPI blocks to tiles.

    The API pre-formats each value. Using its string rather than reformatting
    the raw number keeps the console and the exported PDF showing the same
    figure to the same precision.
    """
    return [
        {
            "label": item.get("label", ""),
            "value": item.get("formatted") or number(item.get("value"), item.get("unit", "")),
            "delta": (f"{item['change']:+.1%}" if item.get("change") is not None else None),
            "help": (
                f"{item.get('comparison_label', 'Prior period')}: "
                f"{number(item.get('comparison'), item.get('unit', ''))}"
            ),
        }
        for item in items
    ]


def cards(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Adapt dashboard metric cards to tiles.

    ``direction`` is flat below a half-percent move, so the API has already
    decided what counts as news. Styling a 0.2% wobble as a rise would be the
    console overriding that judgement.
    """
    return [
        {
            "label": item.get("label", ""),
            "value": number(item.get("value"), str(item.get("unit", ""))),
            "delta": (
                f"{float(item['change_pct']):+.1%}"
                if item.get("change_pct") is not None and item.get("direction") != "flat"
                else None
            ),
            "help": f"Prior: {number(item.get('prior_value'), str(item.get('unit', '')))}",
        }
        for item in items
    ]


def series_rows(
    series: list[dict[str, Any]], *, index: str = "business_date"
) -> list[dict[str, Any]]:
    """Flatten a trend series — ``{date, values: {...}}`` — into chart rows."""
    return [{index: point.get(index), **(point.get("values") or {})} for point in series]


# ── Tables and charts ────────────────────────────────────────────────


def frame(rows: list[dict[str, Any]], *, columns: list[str] | None = None) -> pd.DataFrame:
    """Rows to a dataframe, with readable column names."""
    if not rows:
        return pd.DataFrame()
    data = pd.DataFrame(rows)
    if columns:
        data = data[[c for c in columns if c in data.columns]]
    return data.rename(columns={name: label(name) for name in data.columns})


def table(
    rows: list[dict[str, Any]],
    *,
    columns: list[str] | None = None,
    empty_reason: str = "",
    height: int | None = None,
) -> None:
    if not rows:
        empty(empty_reason or "The query returned no rows for this period.")
        return
    # `height=None` is rejected outright rather than meaning "auto", so an
    # unset height must be omitted from the call, not passed through.
    extra: dict[str, Any] = {"height": height} if height is not None else {}
    st.dataframe(
        frame(rows, columns=columns),
        width="stretch",
        hide_index=True,
        **extra,
    )


def chart(
    rows: list[dict[str, Any]],
    *,
    x: str,
    y: list[str],
    kind: str = "bar",
    rationale: str = "",
) -> None:
    """Draw a chart, and say why this shape.

    Shape is a claim about the data. A line across categories asserts an
    ordering they do not have, so the API returns the shape it believes the
    result supports and the reason travels with it.
    """
    if not rows:
        empty("Nothing to plot for this period.")
        return

    data = pd.DataFrame(rows)
    present = [column for column in y if column in data.columns]
    if x not in data.columns or not present:
        table(rows)
        return

    indexed = data.set_index(x)[present]
    if kind == "line":
        st.line_chart(indexed, height=300)
    elif kind == "horizontal_bar":
        st.bar_chart(indexed, height=340, horizontal=True)
    else:
        st.bar_chart(indexed, height=300)

    if rationale:
        st.caption(rationale)


# ── Domain renderers ─────────────────────────────────────────────────


def findings(items: list[dict[str, Any]], *, limit: int = 6) -> None:
    """Root-cause findings, with the evidence tier shown beside each.

    The tier is the point. "Northeast accounts for 72% of the decline" and
    "four severe-weather days coincided" are different kinds of claim, and a
    list that renders them identically invites the reader to treat the second
    as established.
    """
    if not items:
        empty("No driver cleared the materiality floor for this period.")
        return

    for item in items[:limit]:
        tier = str(item.get("evidence_tier", ""))
        icon, _ = TIER_STYLE.get(tier, ("⬜", tier))
        confidence = float(item.get("confidence") or 0)
        ceiling = float(item.get("confidence_ceiling") or 1)

        with st.container(border=True):
            st.markdown(f"**{icon} {item.get('headline', '')}**")
            columns = st.columns([2, 2, 3])
            columns[0].caption(f"Confidence {confidence:.0%} (max {ceiling:.0%} for this evidence)")
            columns[1].caption(f"Impact {float(item.get('impact_share') or 0):+.0%}")
            columns[2].caption(tier_badge(tier))
            if item.get("does_not_establish"):
                st.caption(f"⚠︎ Does not establish: {item['does_not_establish']}")


def recommendations(items: list[dict[str, Any]], *, limit: int = 8) -> None:
    """Recommended actions with their impact basis, risk, and disqualifier."""
    if not items:
        empty("Nothing clears the materiality floor right now.")
        return

    for item in items[:limit]:
        impact = item.get("impact", {})
        risk = item.get("risk", {})
        basis = str(impact.get("basis", ""))
        icon, meaning = TIER_STYLE.get(basis, ("⬜", basis))

        with st.container(border=True):
            st.markdown(f"**{item.get('action', '')}**")
            st.caption(item.get("rationale", ""))

            tiles = st.columns(4)
            tiles[0].metric("Profit", number(impact.get("profit"), "currency"))
            tiles[1].metric("Confidence", f"{float(item.get('confidence') or 0):.0%}")
            tiles[2].metric(
                "Risk", f"{RISK_STYLE.get(str(risk.get('band')), '')} {risk.get('band', '—')}"
            )
            tiles[3].metric("Owner", str(item.get("owner") or "—"))

            st.caption(f"{icon} Estimate basis: {basis} — {meaning}")
            if impact.get("rests_on_unmeasured_assumptions"):
                low, high = impact.get("pessimistic_profit"), impact.get("optimistic_profit")
                if low is not None and high is not None:
                    st.caption(
                        f"Range if the assumption is wrong: "
                        f"{number(low, 'currency')} to {number(high, 'currency')}"
                    )
            if risk.get("principal_risk"):
                st.caption(f"⚠︎ {risk['principal_risk']}")
            if item.get("do_not_act_if"):
                st.caption(f"🚫 Do not act if: {item['do_not_act_if']}")


def action_cards(items: list[dict[str, Any]], *, limit: int = 5) -> None:
    """The dashboard's own recommendation cards.

    A different shape from the recommendation engine's — these are the
    operational cards the warehouse emits — so it gets its own renderer rather
    than a translation layer that would have to invent the missing fields.
    """
    if not items:
        empty("No action is currently pending on today's positions.")
        return

    for item in items[:limit]:
        impact = item.get("expected_impact") or {}
        subject = ", ".join(f"{key}={value}" for key, value in (item.get("subject") or {}).items())

        with st.container(border=True):
            st.markdown(f"**{label(str(item.get('type', 'action')))} — {subject}**")
            if item.get("rationale"):
                st.caption(item["rationale"])
            columns = st.columns([2, 2, 3])
            columns[0].caption(f"Impact {number(item.get('impact_value'), 'currency')}")
            columns[1].caption(f"Confidence {item.get('confidence', '—')}")
            # An impact without its estimation method is not actionable — the
            # reader cannot tell a measurement from an assumption.
            if impact.get("method"):
                columns[2].caption(f"Method: {impact['method']}")
            if item.get("expires_at"):
                st.caption(f"Expires {day(item['expires_at'])}")


def statements(items: list[dict[str, Any]], *, heading: str) -> None:
    """Analyst facts or inferences, each with its certainty."""
    if not items:
        return
    st.markdown(f"**{heading}**")
    for item in items:
        icon, _ = TIER_STYLE.get(str(item.get("certainty", "")), ("·", ""))
        source = f" _({item['source']})_" if item.get("source") else ""
        st.markdown(f"{icon} {item.get('text', '')}{source}")


def notification_card(item: dict[str, Any]) -> None:
    payload = item.get("payload", {})
    severity = str(item.get("severity") or payload.get("severity") or "info")

    with st.container(border=True):
        st.markdown(f"**{SEVERITY_STYLE.get(severity, '⬜')} {payload.get('title', '')}**")
        st.caption(payload.get("body", ""))
        columns = st.columns([2, 2, 2])
        columns[0].caption(day(item.get("created_at")))
        columns[1].caption(f"Type: {item.get('event_type', '')}")
        if payload.get("deep_link"):
            columns[2].caption(f"→ {payload['deep_link']}")


def follow_ups(items: list[dict[str, Any]]) -> list[str]:
    """Render suggested next questions; return them for wiring to buttons."""
    if not items:
        return []
    st.markdown("**Worth asking next**")
    questions = []
    for item in items:
        st.caption(f"· **{item.get('question', '')}** — {item.get('because', '')}")
        questions.append(str(item.get("question", "")))
    return questions


def provenance(meta: dict[str, Any]) -> None:
    """Where the numbers came from, in small print at the foot of a page."""
    parts = []
    if meta.get("data_snapshot_id"):
        parts.append(f"snapshot `{truncate(str(meta['data_snapshot_id']), 40)}`")
    if meta.get("freshness"):
        parts.append(f"data to {day(meta['freshness'])}")
    if meta.get("cache"):
        parts.append(f"cache {meta['cache']}")
    if meta.get("elapsed_ms") is not None:
        parts.append(f"{float(meta['elapsed_ms']):.0f} ms")
    if parts:
        st.caption(" · ".join(parts))
