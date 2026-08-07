"""The investigation surface: timeline, causes, drill-down, coverage.

An investigation is not a chart with a title. It is a claim about a movement,
decomposed into where the change landed and why it might have happened, with
each candidate graded by the kind of evidence behind it. These components exist
to keep those two categories visually separate all the way down the page:

* **Where** — arithmetic. A region's contribution to a decline is subtraction,
  and it is drawn in the tier colours for measured claims.
* **Why** — hypotheses. Weather coinciding with a drop is an association with a
  hard confidence ceiling, and every one of them carries the sentence saying
  what it does not establish.

The timeline renders the investigation as it was actually performed — windows
compared, dimensions swept, what was found and what was unavailable — because
"the platform looked at nine dimensions and eight had nothing" is itself a
finding, and a reader who cannot see the sweep cannot judge the conclusion.
"""

from typing import Any

import streamlit as st

from retailmind_ui.components.evidence import (
    confidence_meter,
    does_not_establish,
    evidence_panel,
)
from retailmind_ui.components.primitives import chip, empty
from retailmind_ui.design import (
    INK,
    SEMANTIC,
    escape,
    html,
    tier_colour,
    tier_meaning,
)
from retailmind_ui.formatting import day, number

# ── Timeline ─────────────────────────────────────────────────────────


def timeline(steps: list[dict[str, Any]]) -> None:
    """The investigation as a sequence of steps.

    Each step names what was examined and what came back. Steps that found
    nothing are kept rather than dropped: a dimension swept and cleared is
    evidence, and hiding it makes the remaining findings look like the only
    places anyone looked.
    """
    if not steps:
        return

    nodes = []
    for step in steps:
        state = str(step.get("state", "done"))
        colour = {
            "done": SEMANTIC["positive"],
            "found": SEMANTIC["warning"],
            "empty": INK["faint"],
            "blocked": SEMANTIC["critical"],
            "running": SEMANTIC["ai"],
        }.get(state, INK["faint"])
        detail = (
            f'<div class="rm-tl-detail">{escape(step.get("detail"))}</div>'
            if step.get("detail")
            else ""
        )
        nodes.append(
            f"""
            <div class="rm-node" style="color:{colour}">
                <div class="rm-tl-title">{escape(step.get("title", ""))}</div>
                {detail}
            </div>
            """
        )

    html(
        f"""
        <div class="rm-rail">{"".join(nodes)}</div>
        <style>
        .rm-tl-title {{ font-size: 0.875rem; font-weight: 600; color: var(--rm-text); }}
        .rm-tl-detail {{
            font-size: 0.78rem; color: var(--rm-muted);
            margin-top: 0.2rem; line-height: 1.5;
        }}
        </style>
        """
    )


def window_comparison(
    current: dict[str, Any], baseline: dict[str, Any], *, scale: float | None = None
) -> None:
    """The two periods being compared, and how they were made comparable.

    Unequal windows are the most common way a comparison lies. The API scales
    the baseline per-day and reports the factor; showing it here means a reader
    who sees "7 days vs 21 days" knows the arithmetic already accounts for it.
    """
    note = ""
    if scale and abs(scale - 1.0) > 0.01:
        note = (
            f"Windows differ in length, so the baseline is scaled by {scale:.3g} "
            "to compare per-day. Day-of-week mix is not corrected for."
        )

    html(
        f"""
        <div class="rm-windows">
            <div class="rm-window">
                <div class="rm-eyebrow">Period</div>
                <div class="rm-window-range">
                    {escape(day(current.get("start")))} → {escape(day(current.get("end")))}
                </div>
                <div class="rm-window-days">{escape(current.get("days"))} days</div>
            </div>
            <div class="rm-window-vs">vs</div>
            <div class="rm-window">
                <div class="rm-eyebrow">Baseline</div>
                <div class="rm-window-range">
                    {escape(day(baseline.get("start")))} → {escape(day(baseline.get("end")))}
                </div>
                <div class="rm-window-days">{escape(baseline.get("days"))} days</div>
            </div>
        </div>
        {f'<div class="rm-window-note">{escape(note)}</div>' if note else ""}
        <style>
        .rm-windows {{ display: flex; gap: 1rem; align-items: center; flex-wrap: wrap; }}
        .rm-window {{
            border: 1px solid var(--rm-line); border-radius: 12px;
            padding: 0.65rem 0.9rem; background: rgba(255,255,255,0.015); flex: 1; min-width: 180px;
        }}
        .rm-window-range {{
            font-size: 0.875rem; font-weight: 600; margin-top: 0.2rem;
            font-variant-numeric: tabular-nums;
        }}
        .rm-window-days {{ font-size: 0.72rem; color: var(--rm-faint); margin-top: 0.1rem; }}
        .rm-window-vs {{ color: var(--rm-faint); font-size: 0.8rem; }}
        .rm-window-note {{
            font-size: 0.75rem; color: var(--rm-muted); margin-top: 0.55rem;
        }}
        </style>
        """
    )


# ── Findings ─────────────────────────────────────────────────────────


def finding_row(
    item: dict[str, Any],
    *,
    index: int,
    expanded: bool = False,
    on_open: Any = None,
) -> None:
    """One candidate cause, at the weight its evidence supports.

    Impact and confidence sit side by side rather than combined into a single
    score. They answer different questions — how much is at stake, and how much
    the platform trusts the attribution — and a large impact at low confidence
    is a thing to test, not a thing to do.
    """
    tier = str(item.get("evidence_tier", ""))
    colour = tier_colour(tier)
    confidence = float(item.get("confidence") or 0)
    impact = float(item.get("impact_amount") or 0)
    share = item.get("impact_share")

    with st.container(border=True):
        left, right = st.columns([3.2, 1.5])
        with left:
            html(
                f"""
                <div style="display:flex;gap:0.4rem;flex-wrap:wrap;margin-bottom:0.45rem">
                    {chip(str(item.get("dimension", "")), colour=INK["faint"])}
                    {chip(tier, colour=colour, filled=True)}
                    {chip(str(item.get("claim_type", "")).replace("_", " "), colour=INK["faint"])}
                </div>
                <div class="rm-find-title">{escape(item.get("headline", ""))}</div>
                <div class="rm-find-meaning">{escape(tier_meaning(tier))}</div>
                <style>
                .rm-find-title {{ font-size: 0.95rem; font-weight: 600; line-height: 1.4; }}
                .rm-find-meaning {{
                    font-size: 0.75rem; color: var(--rm-faint); margin-top: 0.25rem;
                }}
                </style>
                """
            )
        with right:
            tone = SEMANTIC["critical"] if impact < 0 else SEMANTIC["positive"]
            share_text = f"{float(share):+.0%} of the move" if share is not None else ""
            html(
                f"""
                <div style="text-align:right">
                    <div class="rm-find-impact" style="color:{tone}">
                        {escape(number(impact, "currency"))}
                    </div>
                    <div class="rm-find-share">{escape(share_text)}</div>
                    <div style="margin-top:0.5rem">
                        {confidence_meter(confidence, item.get("confidence_ceiling"), compact=True)}
                    </div>
                </div>
                <style>
                .rm-find-impact {{
                    font-size: 1.15rem; font-weight: 640;
                    font-variant-numeric: tabular-nums; letter-spacing: -0.02em;
                }}
                .rm-find-share {{ font-size: 0.72rem; color: var(--rm-faint); }}
                </style>
                """
            )

        if item.get("does_not_establish"):
            does_not_establish(str(item["does_not_establish"]))

        if on_open is not None:
            key = f"open_{item.get('dimension')}_{item.get('subject')}_{index}"
            if st.button(
                "Close evidence" if expanded else "Open evidence",
                key=key,
                width="content",
            ):
                on_open(item)

        if expanded and item.get("evidence"):
            evidence_panel(list(item["evidence"]), title="Measurements behind this")


def findings_group(
    items: list[dict[str, Any]],
    *,
    title: str,
    subtitle: str,
    opened: str | None = None,
    on_open: Any = None,
    limit: int = 6,
    empty_reason: str = "",
) -> None:
    """A ranked group of findings — the *where* list or the *why* list."""
    html(
        f"""
        <div class="rm-group-head">
            <span class="rm-group-title">{escape(title)}</span>
            <span class="rm-group-sub">{escape(subtitle)}</span>
        </div>
        <style>
        .rm-group-head {{
            display: flex; gap: 0.7rem; align-items: baseline;
            margin: 1.1rem 0 0.65rem; flex-wrap: wrap;
        }}
        .rm-group-title {{ font-size: 1rem; font-weight: 620; }}
        .rm-group-sub {{ font-size: 0.78rem; color: var(--rm-muted); }}
        </style>
        """
    )

    if not items:
        empty(empty_reason or "Nothing cleared the materiality floor for this period.")
        return

    for index, item in enumerate(items[:limit]):
        identity = f"{item.get('dimension')}::{item.get('subject')}"
        finding_row(item, index=index, expanded=opened == identity, on_open=on_open)


def coverage(
    explained_share: float | None, investigated: list[str], unavailable: dict[str, str]
) -> None:
    """How much of the movement the investigation actually accounts for.

    Coverage above 100% is normal and is not a bug: slices overlap, and one
    region can account for more than the whole net change when another moved
    the other way. Shown rather than clamped, because clamping would hide the
    fact that the decomposition is not a partition.
    """
    swept = ", ".join(investigated) if investigated else "none"
    blocked = (
        " ".join(
            f'<div class="rm-cov-blocked">{escape(name)} — {escape(reason)}</div>'
            for name, reason in unavailable.items()
        )
        if unavailable
        else ""
    )

    share_text = f"{float(explained_share):.0%}" if explained_share is not None else "—"
    over = explained_share is not None and float(explained_share) > 1.05

    html(
        f"""
        <div class="rm-cov">
            <div>
                <div class="rm-eyebrow">Explained</div>
                <div class="rm-cov-value">{escape(share_text)}</div>
            </div>
            <div class="rm-cov-body">
                <div>Swept: {escape(swept)}</div>
                {
            '<div class="rm-cov-note">Above 100% because slices overlap — one region '
            "can account for more than the net change when another moved the other "
            "way. This is a decomposition, not a partition.</div>"
            if over
            else ""
        }
                {blocked}
            </div>
        </div>
        <style>
        .rm-cov {{
            display: flex; gap: 1.2rem; align-items: flex-start;
            border: 1px solid var(--rm-line); border-radius: 14px;
            padding: 0.9rem 1.1rem; background: rgba(255,255,255,0.015);
        }}
        .rm-cov-value {{
            font-size: 1.6rem; font-weight: 640; letter-spacing: -0.03em;
            font-variant-numeric: tabular-nums;
        }}
        .rm-cov-body {{ font-size: 0.8125rem; color: var(--rm-muted); line-height: 1.55; }}
        .rm-cov-note {{ color: var(--rm-faint); font-size: 0.75rem; margin-top: 0.35rem; }}
        .rm-cov-blocked {{ color: {SEMANTIC["warning"]}; font-size: 0.75rem; margin-top: 0.3rem; }}
        </style>
        """
    )


# ── Navigation ───────────────────────────────────────────────────────


def breadcrumb(trail: list[str]) -> None:
    """Where the reader is in a drill-down."""
    if not trail:
        return
    crumbs = '<span class="rm-crumb-sep">›</span>'.join(
        f'<span class="rm-crumb">{escape(item)}</span>' for item in trail
    )
    html(
        f"""
        <div class="rm-crumbs">{crumbs}</div>
        <style>
        .rm-crumbs {{
            display: flex; align-items: center; gap: 0.4rem;
            font-size: 0.78rem; color: var(--rm-muted); margin-bottom: 0.7rem;
            flex-wrap: wrap;
        }}
        .rm-crumb:last-child {{ color: var(--rm-text); font-weight: 600; }}
        .rm-crumb-sep {{ color: var(--rm-faint); margin: 0 0.35rem; }}
        </style>
        """
    )


def decision_tree(branches: list[dict[str, Any]]) -> None:
    """The reasoning path: question → answer → next question.

    A tree rather than a list because the second question depends on the first
    answer, and rendering them as siblings implies the platform asked all of
    them independently.
    """
    if not branches:
        return

    nodes = []
    for depth, branch in enumerate(branches):
        colour = tier_colour(str(branch.get("certainty", "measured")))
        nodes.append(
            f"""
            <div class="rm-branch" style="margin-left:{depth * 1.1:.2f}rem;color:{colour}">
                <div class="rm-branch-q">{escape(branch.get("question", ""))}</div>
                <div class="rm-branch-a">{escape(branch.get("answer", ""))}</div>
            </div>
            """
        )

    html(
        f"""
        <div class="rm-tree">{"".join(nodes)}</div>
        <style>
        .rm-tree {{ margin: 0.5rem 0; }}
        .rm-branch {{
            border-left: 2px solid currentColor; padding: 0.15rem 0 0.6rem 0.8rem;
        }}
        .rm-branch-q {{
            font-size: 0.72rem; letter-spacing: 0.06em; text-transform: uppercase;
            font-weight: 650;
        }}
        .rm-branch-a {{ font-size: 0.875rem; color: var(--rm-text); margin-top: 0.2rem; }}
        </style>
        """
    )
