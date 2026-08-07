"""Confidence, evidence, and the qualifications attached to a number.

**This module is the reason the console exists in this shape.** The API grades
every claim it makes — an evidence tier on each finding, a hard confidence
ceiling implied by that tier, an estimate basis on each impact, a note saying
what was *not* checked. A console that renders the number and drops the grade
is strictly worse than one that shows nothing, because it produces confident
decisions from tentative inputs.

So: caveats render inline, in the reading path, never behind a disclosure
triangle. Confidence renders beside its ceiling, so "62%" reads as "62% of a
possible 70%" rather than as a probability. Evidence tiers carry a colour that
is used for nothing else in the product.
"""

from typing import Any

import streamlit as st

from retailmind_ui.components.primitives import chip, meter
from retailmind_ui.design import (
    INK,
    SEMANTIC,
    confidence_colour,
    escape,
    html,
    tier_colour,
    tier_meaning,
)
from retailmind_ui.formatting import day, number, truncate

# ── Confidence ───────────────────────────────────────────────────────


def confidence_meter(
    value: float,
    ceiling: float | None = None,
    *,
    tier: str = "",
    compact: bool = False,
) -> str:
    """Confidence, its ceiling, and where the ceiling came from.

    The ceiling is drawn as a notch on the track. Without it, 62% reads as a
    probability; with it, the reader sees that this class of evidence could
    never have exceeded 70% however clean the data was.
    """
    pct = max(0.0, min(1.0, float(value or 0)))
    tone = confidence_colour(pct)
    cap = max(0.0, min(1.0, float(ceiling))) if ceiling is not None else None

    notch = ""
    if cap is not None and cap < 0.999:
        notch = (
            f'<span class="rm-cap" style="left:{cap * 100:.1f}%" '
            f'title="ceiling for this evidence"></span>'
        )

    caption = f"{pct:.0%}"
    if cap is not None:
        caption += f' <span style="color:var(--rm-faint)">of {cap:.0%} max</span>'
    if tier and not compact:
        caption += f' <span style="color:{tier_colour(tier)}">· {escape(tier)}</span>'

    return f"""
    <div class="rm-conf">
        <div class="rm-conf-track">
            <span style="width:{pct * 100:.1f}%;background:{tone}"></span>
            {notch}
        </div>
        <div class="rm-conf-caption">{caption}</div>
    </div>
    <style>
    .rm-conf {{ min-width: 120px; }}
    .rm-conf-track {{
        position: relative; height: 5px; border-radius: 999px;
        background: rgba(148,163,184,0.16); overflow: visible;
    }}
    .rm-conf-track > span {{ display:block; height:100%; border-radius:999px; }}
    .rm-cap {{
        position: absolute; top: -3px; width: 2px; height: 11px;
        background: var(--rm-text); opacity: 0.55; border-radius: 1px;
    }}
    .rm-conf-caption {{
        font-size: 0.72rem; color: var(--rm-muted);
        margin-top: 0.3rem; font-variant-numeric: tabular-nums;
    }}
    </style>
    """


def tier_chip(tier: str, *, with_meaning: bool = True) -> str:
    """An evidence tier as a labelled chip."""
    text = f"{tier} — {tier_meaning(tier)}" if with_meaning else str(tier)
    return chip(text, colour=tier_colour(tier))


def basis_chip(basis: str) -> str:
    """An estimate basis: what the pound figure rests on."""
    return chip(f"{basis} estimate", colour=tier_colour(basis))


# ── Evidence ─────────────────────────────────────────────────────────


def evidence_panel(items: list[dict[str, Any]], *, title: str = "Evidence") -> None:
    """The measurements behind a claim, with their own units and notes.

    Rendered as rows rather than a table because each line carries a note that
    explains what the number does and does not include — and a note squeezed
    into a table cell is a note nobody reads.
    """
    if not items:
        return

    rows = []
    for item in items:
        unit = str(item.get("unit", ""))
        value = number(item.get("value"), unit if unit != "z" else "ratio")
        change = item.get("relative_change")
        movement = (
            f'<span class="rm-ev-move" style="color:'
            f'{SEMANTIC["critical"] if float(change) < 0 else SEMANTIC["positive"]}">'
            f"{float(change):+.1%}</span>"
            if change is not None
            else ""
        )
        baseline = (
            f'<span class="rm-ev-base">from {number(item.get("baseline"), unit)}</span>'
            if item.get("baseline") is not None
            else ""
        )
        note = (
            f'<div class="rm-ev-note">{escape(item.get("note"))}</div>' if item.get("note") else ""
        )
        rows.append(
            f"""
            <div class="rm-ev-row">
                <div class="rm-ev-label">{escape(item.get("label", ""))}</div>
                <div class="rm-ev-value">{escape(value)} {movement} {baseline}</div>
                {note}
            </div>
            """
        )

    html(
        f"""
        <div class="rm-ev">
            <div class="rm-eyebrow" style="margin-bottom:0.6rem">{escape(title)}</div>
            {"".join(rows)}
        </div>
        <style>
        .rm-ev {{
            border: 1px solid var(--rm-line); border-radius: 14px;
            padding: 0.95rem 1.1rem; background: rgba(255,255,255,0.015);
        }}
        .rm-ev-row {{ padding: 0.5rem 0; border-bottom: 1px solid var(--rm-line); }}
        .rm-ev-row:last-child {{ border-bottom: none; padding-bottom: 0; }}
        .rm-ev-label {{ font-size: 0.8125rem; color: var(--rm-muted); }}
        .rm-ev-value {{
            font-size: 0.9375rem; font-weight: 600;
            font-variant-numeric: tabular-nums; margin-top: 0.1rem;
        }}
        .rm-ev-move {{ font-size: 0.8125rem; margin-left: 0.35rem; }}
        .rm-ev-base {{ font-size: 0.75rem; color: var(--rm-faint); margin-left: 0.35rem; }}
        .rm-ev-note {{ font-size: 0.72rem; color: var(--rm-faint); margin-top: 0.25rem; }}
        </style>
        """
    )


# ── Qualifications ───────────────────────────────────────────────────


def caveats(
    items: list[str] | tuple[str, ...],
    *,
    title: str = "Before acting on this",
    tone: str = "",
) -> None:
    """Render qualifications inline, in the reading path.

    **Not an expander.** A caveat behind a click is a caveat nobody reads, and
    the API attaches them precisely because acting on the number without them
    is the failure mode. This is the single rule the whole component library
    exists to enforce.
    """
    entries = [item for item in items if item]
    if not entries:
        return

    colour = tone or SEMANTIC["warning"]
    lines = "".join(f"<li>{escape(item)}</li>" for item in entries)
    html(
        f"""
        <div class="rm-caveats" style="border-left-color:{colour}">
            <div class="rm-caveat-title" style="color:{colour}">{escape(title)}</div>
            <ul>{lines}</ul>
        </div>
        <style>
        .rm-caveats {{
            border-left: 2px solid; border-radius: 0 12px 12px 0;
            background: rgba(255,255,255,0.02);
            padding: 0.8rem 1rem 0.85rem 1rem; margin: 0.9rem 0;
        }}
        .rm-caveat-title {{
            font-size: 0.72rem; font-weight: 650;
            letter-spacing: 0.08em; text-transform: uppercase;
        }}
        .rm-caveats ul {{ margin: 0.45rem 0 0; padding-left: 1.05rem; }}
        .rm-caveats li {{
            font-size: 0.8125rem; color: var(--rm-muted);
            line-height: 1.5; margin-bottom: 0.3rem;
        }}
        .rm-caveats li:last-child {{ margin-bottom: 0; }}
        </style>
        """
    )


def does_not_establish(text: str) -> None:
    """What a finding explicitly does *not* prove.

    The API returns this on associative findings, and it is the sentence that
    stops "four severe-weather days coincided" from being read as "weather
    caused it". It is rendered at the same weight as the finding itself.
    """
    if not text:
        return
    html(
        f"""
        <div class="rm-dne">
            <span class="rm-dne-tag">Does not establish</span>
            <span>{escape(text)}</span>
        </div>
        <style>
        .rm-dne {{
            display: flex; gap: 0.55rem; align-items: baseline;
            font-size: 0.8125rem; color: var(--rm-muted);
            border: 1px solid {SEMANTIC["capital"]}44;
            background: {SEMANTIC["capital"]}0d;
            border-radius: 10px; padding: 0.55rem 0.8rem; margin-top: 0.6rem;
        }}
        .rm-dne-tag {{
            color: {SEMANTIC["capital"]}; font-weight: 650; font-size: 0.7rem;
            letter-spacing: 0.06em; text-transform: uppercase; white-space: nowrap;
        }}
        </style>
        """
    )


def disqualifier(text: str) -> None:
    """The condition under which an action would be wrong.

    Deliberately not :func:`does_not_establish`, which is a claim about
    evidence. This is a claim about applicability: the platform cannot see
    that a line is being discontinued or a store is under refit, and the
    reader usually can. It renders above the decision, because a caveat placed
    after the button is a caveat placed after the decision.
    """
    if not text:
        return
    html(
        f"""
        <div class="rm-disq">
            <span class="rm-disq-tag">Do not act if</span>
            <span>{escape(text)}</span>
        </div>
        <style>
        .rm-disq {{
            display: flex; gap: 0.55rem; align-items: baseline;
            font-size: 0.8125rem; color: var(--rm-text);
            border: 1px solid {SEMANTIC["warning"]}55;
            background: {SEMANTIC["warning"]}0f;
            border-radius: 10px; padding: 0.55rem 0.8rem; margin-top: 0.6rem;
        }}
        .rm-disq-tag {{
            color: {SEMANTIC["warning"]}; font-weight: 650; font-size: 0.7rem;
            letter-spacing: 0.06em; text-transform: uppercase; white-space: nowrap;
        }}
        </style>
        """
    )


def checked_and_not(checked: list[str], not_checked: list[str]) -> None:
    """What an answer looked at, and what it did not.

    The second list is the more useful of the two. An assistant that reports
    only what it examined leaves its silences unreadable — the reader cannot
    tell whether returns were fine or were never opened.
    """
    if not checked and not_checked == []:
        return

    def render(items: list[str], colour: str, label: str) -> str:
        if not items:
            return ""
        chips = "".join(chip(item, colour=colour) + " " for item in items)
        return (
            f'<div class="rm-cnc-row"><span class="rm-cnc-label">{label}</span>'
            f"<span>{chips}</span></div>"
        )

    html(
        f"""
        <div class="rm-cnc">
            {render(checked, SEMANTIC["positive"], "Checked")}
            {render(not_checked, INK["faint"], "Not checked")}
        </div>
        <style>
        .rm-cnc {{ margin: 0.7rem 0; display: flex; flex-direction: column; gap: 0.4rem; }}
        .rm-cnc-row {{ display: flex; gap: 0.6rem; align-items: baseline; flex-wrap: wrap; }}
        .rm-cnc-label {{
            font-size: 0.7rem; letter-spacing: 0.08em; text-transform: uppercase;
            color: var(--rm-faint); font-weight: 650; min-width: 76px;
        }}
        </style>
        """
    )


def statements(items: list[dict[str, Any]], *, heading: str) -> None:
    """Facts or inferences, each carrying its certainty.

    Facts and inferences are rendered in separate calls by design: a
    decomposition is arithmetic and an explanation for it is a hypothesis, and
    a list that interleaves them invites the reader to promote the second.
    """
    if not items:
        return

    rows = []
    for item in items:
        certainty = str(item.get("certainty", ""))
        colour = tier_colour(certainty)
        source = (
            f'<span class="rm-stmt-src">{escape(item.get("source"))}</span>'
            if item.get("source")
            else ""
        )
        rows.append(
            f"""
            <div class="rm-stmt">
                <span class="rm-dot" style="background:{colour};margin-top:0.5rem"></span>
                <div>
                    <div>{escape(item.get("text", ""))}</div>
                    <div class="rm-stmt-meta">
                        <span style="color:{colour}">{escape(certainty)}</span> {source}
                    </div>
                </div>
            </div>
            """
        )

    html(
        f"""
        <div class="rm-stmts">
            <div class="rm-eyebrow" style="margin-bottom:0.5rem">{escape(heading)}</div>
            {"".join(rows)}
        </div>
        <style>
        .rm-stmt {{ display: flex; gap: 0.65rem; padding: 0.4rem 0; align-items: flex-start; }}
        .rm-stmt-meta {{ font-size: 0.72rem; color: var(--rm-faint); margin-top: 0.15rem; }}
        .rm-stmt-src {{ margin-left: 0.4rem; font-style: italic; }}
        </style>
        """
    )


def provenance(meta: dict[str, Any], *, extra: list[str] | None = None) -> None:
    """Where the numbers came from, in small print at the foot of a workspace."""
    parts: list[str] = []
    if meta.get("freshness"):
        parts.append(f"data to {day(meta['freshness'])}")
    if meta.get("data_snapshot_id"):
        parts.append(f"snapshot {truncate(str(meta['data_snapshot_id']), 28)}")
    if meta.get("cache"):
        parts.append(f"cache {meta['cache']}")
    if meta.get("elapsed_ms") is not None:
        parts.append(f"{float(meta['elapsed_ms']):.0f} ms")
    parts.extend(extra or [])

    if not parts:
        return
    st.caption(" · ".join(parts))


def confidence_legend() -> None:
    """One legend, once, for readers meeting the grading for the first time."""
    tiers = ["measured", "mechanical", "modelled", "assumed", "unknown"]
    chips = " ".join(tier_chip(tier) for tier in tiers)
    html(f'<div style="display:flex;flex-wrap:wrap;gap:0.4rem;margin:0.5rem 0">{chips}</div>')


def meter_row(label: str, value: float, *, colour: str = "") -> None:
    """A labelled proportion — used for coverage, share, and health scores."""
    html(
        f"""
        <div style="margin:0.35rem 0">
            <div style="display:flex;justify-content:space-between;font-size:0.78rem">
                <span style="color:var(--rm-muted)">{escape(label)}</span>
                <span style="font-variant-numeric:tabular-nums">{value:.0%}</span>
            </div>
            <div style="margin-top:0.25rem">{meter(value, colour=colour)}</div>
        </div>
        """
    )
