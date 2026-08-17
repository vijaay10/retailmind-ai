"""Action cards, alert cards, and AI summary cards.

The three objects a reader actually acts on.

**An action card is a decision surface, not a summary.** It shows what the
action is worth, what it rests on, what it risks, what would make it wrong, and
— once someone acts — who decided and when. The accept button writes to a
ledger through `recommendations.act`; it does not raise a purchase order, and
the card says so rather than implying an execution it cannot perform.

**An alert card leads with the money**, not the metric name. "Northeast revenue
outside band" is a fact; "£146k below the expected floor, four severe-weather
days in the window" is a reason to open it.

**An AI summary card marks itself as generated.** Everything cyan in this
product came from a model reading the platform's own numbers. A reader must
never have to guess whether a sentence was written by an analyst or assembled
from a template.
"""

from typing import Any

import streamlit as st

from retailmind_ui.components.evidence import (
    basis_chip,
    confidence_meter,
    disqualifier,
)
from retailmind_ui.components.primitives import chip
from retailmind_ui.design import (
    INK,
    SEMANTIC,
    escape,
    html,
    risk_colour,
    severity_colour,
)
from retailmind_ui.formatting import day, number, relative_time

# ── Action cards ─────────────────────────────────────────────────────


def action_card(
    item: dict[str, Any],
    *,
    index: int,
    can_act: bool,
    on_decide: Any = None,
) -> None:
    """One proposed action, with everything needed to accept or refuse it.

    The disqualifier (`do_not_act_if`) is rendered above the buttons on
    purpose. It names the condition under which the advice is wrong — a line
    being discontinued, a store under refit — and the platform cannot see any
    of those while the reader usually can. Putting it after the decision is
    where a caveat goes to be ignored.
    """
    impact = item.get("impact") or {}
    risk = item.get("risk") or {}
    decision = item.get("decision")
    basis = str(impact.get("basis", ""))
    confidence = float(item.get("confidence") or 0)
    profit = float(impact.get("profit") or 0)

    with st.container(border=True):
        header, figure = st.columns([3, 1.15])

        with header:
            badges = " ".join(
                [
                    chip(str(item.get("category", "")), colour=SEMANTIC["accent"], filled=True),
                    chip(
                        f"{risk.get('band', 'unknown')} risk",
                        colour=risk_colour(str(risk.get("band", ""))),
                    ),
                    chip(str(item.get("urgency") or "planned"), colour=INK["faint"]),
                ]
            )
            html(
                f"""
                <div style="display:flex;gap:0.35rem;flex-wrap:wrap;margin-bottom:0.5rem">
                    {badges}
                </div>
                <div class="rm-act-title">{escape(item.get("action", ""))}</div>
                <div class="rm-act-sub">{escape(item.get("subject", ""))}</div>
                <div class="rm-act-why">{escape(item.get("rationale", ""))}</div>
                <style>
                .rm-act-title {{
                    font-size: 1.02rem; font-weight: 620;
                    letter-spacing: -0.015em; line-height: 1.35;
                }}
                .rm-act-sub {{ font-size: 0.75rem; color: var(--rm-faint); margin-top: 0.15rem; }}
                .rm-act-why {{
                    font-size: 0.8125rem; color: var(--rm-muted);
                    margin-top: 0.5rem; line-height: 1.5;
                }}
                </style>
                """
            )

        with figure:
            tone = SEMANTIC["positive"] if profit >= 0 else SEMANTIC["critical"]
            html(
                f"""
                <div class="rm-act-figure">
                    <div class="rm-eyebrow">Expected profit</div>
                    <div class="rm-act-amount" style="color:{tone}">
                        {escape(number(profit, "currency"))}
                    </div>
                    <div style="margin-top:0.5rem">{basis_chip(basis)}</div>
                    <div style="margin-top:0.6rem">
                        {confidence_meter(confidence, item.get("confidence_ceiling"))}
                    </div>
                </div>
                <style>
                .rm-act-figure {{ text-align: right; }}
                .rm-act-amount {{
                    font-size: 1.45rem; font-weight: 640;
                    letter-spacing: -0.03em; font-variant-numeric: tabular-nums;
                }}
                .rm-act-figure .rm-conf {{ margin-left: auto; }}
                </style>
                """
            )

        # Sensitivity, where the estimate turns on something unmeasured.
        if impact.get("rests_on_unmeasured_assumptions"):
            low, high = impact.get("pessimistic_profit"), impact.get("optimistic_profit")
            if low is not None and high is not None:
                crosses_zero = float(low) <= 0 <= float(high)
                html(
                    f"""
                    <div class="rm-range">
                        <span class="rm-range-label">If the assumption is wrong</span>
                        <span class="rm-range-value">
                            {escape(number(low, "currency"))} to {escape(number(high, "currency"))}
                        </span>
                        {
                        '<span class="rm-range-flag">range crosses zero — '
                        "a test beats a rollout</span>"
                        if crosses_zero
                        else ""
                    }
                    </div>
                    <style>
                    .rm-range {{
                        display: flex; gap: 0.6rem; flex-wrap: wrap; align-items: baseline;
                        margin-top: 0.75rem; padding-top: 0.7rem;
                        border-top: 1px solid var(--rm-line); font-size: 0.8125rem;
                    }}
                    .rm-range-label {{ color: var(--rm-faint); }}
                    .rm-range-value {{ font-variant-numeric: tabular-nums; font-weight: 600; }}
                    .rm-range-flag {{ color: {SEMANTIC["warning"]}; }}
                    </style>
                    """
                )

        if risk.get("principal_risk"):
            st.caption(f"Principal risk · {risk['principal_risk']}")
        if item.get("do_not_act_if"):
            disqualifier(str(item["do_not_act_if"]))

        _decision_controls(
            item,
            index=index,
            can_act=can_act,
            decision=decision,
            on_decide=on_decide,
        )


def _decision_controls(
    item: dict[str, Any],
    *,
    index: int,
    can_act: bool,
    decision: dict[str, Any] | None,
    on_decide: Any,
) -> None:
    """Accept, dismiss, or show what was already decided."""
    key = str(item.get("decision_key", index))

    if decision:
        action = str(decision.get("action"))
        tone = SEMANTIC["positive"] if action == "accepted" else INK["faint"]
        when = relative_time(decision.get("decided_at"))
        reason = decision.get("reason_code")
        note = decision.get("note")
        html(
            f"""
            <div class="rm-decided" style="border-color:{tone}55;background:{tone}0f">
                <span style="color:{tone};font-weight:650">{escape(action.title())}</span>
                <span style="color:var(--rm-muted)">{escape(when)}</span>
                {f'<span style="color:var(--rm-faint)">· {escape(reason)}</span>' if reason else ""}
                {f'<span style="color:var(--rm-faint)">· {escape(note)}</span>' if note else ""}
            </div>
            <style>
            .rm-decided {{
                display: flex; gap: 0.5rem; flex-wrap: wrap; align-items: baseline;
                border: 1px solid; border-radius: 10px;
                padding: 0.45rem 0.75rem; margin-top: 0.8rem; font-size: 0.8125rem;
            }}
            </style>
            """
        )
        if (
            can_act
            and on_decide is not None
            and st.button("Change decision", key=f"undo_{key}_{index}", width="content")
        ):
            st.session_state[f"rm_reopen_{key}"] = True
            st.rerun()
        if not st.session_state.get(f"rm_reopen_{key}"):
            return

    if not can_act:
        st.caption(
            "Your role can read this proposal but not act on it — acting needs "
            "`recommendations.act`. The API enforces that regardless of what is shown here."
        )
        return

    accept, dismiss, reason_slot = st.columns([1, 1, 2])
    with accept:
        if st.button("Accept", key=f"acc_{key}_{index}", type="primary", width="stretch"):
            on_decide(key, "accepted", None, None)
    with dismiss:
        if st.button("Dismiss", key=f"dis_{key}_{index}", width="stretch"):
            st.session_state[f"rm_dismissing_{key}"] = True
            st.rerun()

    if st.session_state.get(f"rm_dismissing_{key}"):
        with reason_slot:
            # Dismissals carry an enumerated reason because that is the only
            # learning signal the loop produces: an action refused for a
            # supplier constraint is a different fact from one refused because
            # the reasoning was wrong.
            reason = st.selectbox(
                "Why?",
                ["already_planned", "supplier_constraint", "disagree_forecast", "other"],
                key=f"rsn_{key}_{index}",
                label_visibility="collapsed",
            )
            if st.button("Confirm dismissal", key=f"cfm_{key}_{index}", width="stretch"):
                on_decide(key, "dismissed", reason, None)

    st.caption(
        "Recording a decision does not execute it. No purchase order is raised and "
        "no price changes — the ledger captures the judgement and the number it was "
        "made against, which is what makes “were we right?” answerable later."
    )


# ── Alerts ───────────────────────────────────────────────────────────


def alert_card(item: dict[str, Any], *, on_investigate: Any = None, index: int = 0) -> None:
    """One alert, led by its consequence."""
    payload = item.get("payload") or item
    severity = str(item.get("severity") or payload.get("severity") or "info")
    colour = severity_colour(severity)
    observed = payload.get("observed")
    low, high = payload.get("expected_low"), payload.get("expected_high")

    band = ""
    if low is not None or high is not None:
        floor = number(low, "currency") if low is not None else "—"
        cap = number(high, "currency") if high is not None else "no upper bound"
        band = f"expected {floor} to {cap}"

    observed_text = (
        f"<span>observed {escape(number(observed, 'currency'))}</span>"
        if observed is not None
        else ""
    )
    when = escape(day(item.get("created_at") or payload.get("detected_for")))

    with st.container(border=True):
        html(
            f"""
            <div class="rm-alert-head">
                <span class="rm-dot" style="background:{colour}"></span>
                <div>
                    <div class="rm-alert-title">{escape(payload.get("title", ""))}</div>
                    <div class="rm-alert-body">{escape(payload.get("body", ""))}</div>
                </div>
            </div>
            <div class="rm-alert-meta">
                {observed_text}
                {f"<span>{escape(band)}</span>" if band else ""}
                <span>{when}</span>
                <span style="color:{colour}">{escape(severity)}</span>
            </div>
            <style>
            .rm-alert-head {{ display: flex; gap: 0.7rem; align-items: flex-start; }}
            .rm-alert-head .rm-dot {{ margin-top: 0.45rem; }}
            .rm-alert-title {{ font-weight: 620; font-size: 0.95rem; line-height: 1.35; }}
            .rm-alert-body {{
                font-size: 0.8125rem; color: var(--rm-muted); margin-top: 0.25rem;
            }}
            .rm-alert-meta {{
                display: flex; flex-wrap: wrap; gap: 0.85rem; margin-top: 0.6rem;
                font-size: 0.72rem; color: var(--rm-faint);
                font-variant-numeric: tabular-nums;
            }}
            </style>
            """
        )
        if on_investigate is not None and st.button(
            "Investigate", key=f"inv_{item.get('id', index)}_{index}", width="content"
        ):
            on_investigate(item)


# ── AI narration ─────────────────────────────────────────────────────


def ai_summary(
    text: str,
    *,
    title: str = "AI summary",
    bullets: list[str] | None = None,
    footnote: str = "",
    live: bool = False,
) -> None:
    """Machine-written narrative, marked as such.

    Cyan and the pulse dot mean "a model wrote this from the platform's own
    numbers". The distinction matters more here than anywhere else in the
    product: a reader who cannot tell narrated prose from a measured figure
    will eventually quote one as the other. The tag reads "explained" rather
    than "generated" — the number above it is observed or computed, never
    the model's; only the sentence describing it is the model's writing,
    grounded in the figures the platform already published.
    """
    lines = "".join(f"<li>{escape(item)}</li>" for item in bullets) if bullets else ""
    html(
        f"""
        <div class="rm-ai">
            <div class="rm-ai-head">
                <span class="rm-dot {"rm-live" if live else ""}"
                      style="background:{SEMANTIC["ai"]}"></span>
                <span class="rm-ai-title">{escape(title)}</span>
                <span class="rm-ai-tag">explained</span>
            </div>
            <div class="rm-ai-body">{escape(text)}</div>
            {f"<ul class='rm-ai-list'>{lines}</ul>" if lines else ""}
            {f'<div class="rm-ai-foot">{escape(footnote)}</div>' if footnote else ""}
        </div>
        <style>
        .rm-ai {{
            border: 1px solid {SEMANTIC["ai"]}33;
            background: linear-gradient(180deg, {SEMANTIC["ai"]}0f, rgba(255,255,255,0.012));
            border-radius: 16px; padding: 1.05rem 1.2rem;
        }}
        .rm-ai-head {{ display: flex; align-items: center; gap: 0.5rem; margin-bottom: 0.6rem; }}
        .rm-ai-title {{
            font-size: 0.72rem; letter-spacing: 0.1em; text-transform: uppercase;
            font-weight: 650; color: {SEMANTIC["ai"]};
        }}
        .rm-ai-tag {{
            font-size: 0.65rem; color: var(--rm-faint);
            border: 1px solid var(--rm-line); border-radius: 999px; padding: 0.05rem 0.45rem;
        }}
        .rm-ai-body {{ font-size: 0.95rem; line-height: 1.6; }}
        .rm-ai-list {{ margin: 0.6rem 0 0; padding-left: 1.1rem; }}
        .rm-ai-list li {{
            font-size: 0.8125rem; color: var(--rm-muted);
            line-height: 1.55; margin-bottom: 0.25rem;
        }}
        .rm-ai-foot {{
            font-size: 0.72rem; color: var(--rm-faint);
            margin-top: 0.7rem; padding-top: 0.6rem; border-top: 1px solid var(--rm-line);
        }}
        </style>
        """
    )


def headline_card(
    *,
    eyebrow: str,
    value: str,
    caption: str = "",
    delta: str = "",
    direction: str = "",
    accent: str = "",
) -> None:
    """The one number a workspace is about."""
    tone = accent or INK["text"]
    move_colour = {"up": SEMANTIC["positive"], "down": SEMANTIC["critical"]}.get(
        direction, INK["muted"]
    )
    arrow = {"up": "↑", "down": "↓"}.get(direction, "")

    html(
        f"""
        <div class="rm-headline">
            <div class="rm-eyebrow">{escape(eyebrow)}</div>
            <div class="rm-display" style="color:{tone}">{escape(value)}</div>
            <div class="rm-headline-meta">
                {
            f'<span style="color:{move_colour};font-weight:640">{arrow} {escape(delta)}</span>'
            if delta
            else ""
        }
                {f"<span>{escape(caption)}</span>" if caption else ""}
            </div>
        </div>
        <style>
        .rm-headline-meta {{
            display: flex; gap: 0.6rem; align-items: baseline;
            margin-top: 0.35rem; font-size: 0.8125rem; color: var(--rm-muted);
        }}
        </style>
        """
    )
