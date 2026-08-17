"""Decision Center — the queue of things worth doing, and what was decided.

**Accepting writes to a ledger.** It does not raise a purchase order or change
a price, and every card says so. What it records is the judgement and the
number that judgement was made against — which is the precondition for ever
answering "were we right?", and the reason this is a workspace rather than a
list of suggestions.

**Two totals, deliberately.** The gross figure adds every estimate; the net one
counts overlapping actions once, because reordering a line and fixing the
supplier that made it late chase the same pounds. Plans built on the gross
number miss by the difference.

**Capital freed is never added to profit.** Clearing dead stock releases cash
*and* books a loss. Combining them is how a clearance programme gets approved
on the strength of the thing that makes it expensive.
"""

from typing import Any

import streamlit as st

from retailmind_ui import components as ui
from retailmind_ui import design, session
from retailmind_ui.api import ApiError
from retailmind_ui.design import SEMANTIC, escape, html
from retailmind_ui.formatting import number, relative_time

design.configure("Decision Center", icon="◆")
client = session.require("recommendations.read")

CATEGORIES = ["inventory", "pricing", "promotion", "store", "marketing", "customer", "supplier"]
can_act = session.can("recommendations.act")

ui.workspace_header(
    "Decision Center",
    eyebrow="Proposed actions",
    summary=(
        "Ranked by expected profit weighted by confidence, less the downside weighted "
        "by the chance the reasoning does not hold."
    ),
    meta=[
        "acting requires recommendations.act" if not can_act else "you can act on these",
        "decisions persist",
    ],
)

# ── Controls ─────────────────────────────────────────────────────────

filters = st.columns([3, 1.4, 1.4])
chosen = filters[0].multiselect("Categories", CATEGORIES, placeholder="All seven categories")
show = filters[1].selectbox("Show", ["Pending", "All", "Decided"], key="rm_show")
limit = filters[2].slider("Limit", 5, 40, 20)

try:
    body: dict[str, Any] = client.get(
        "/api/v1/recommendations", categories=chosen or None, limit=limit
    )
except ApiError as error:
    ui.workspace_error(error, what="The recommendation engine did not respond")
    st.stop()

items: list[dict[str, Any]] = body.get("recommendations") or []

if show == "Pending":
    visible = [item for item in items if not item.get("decision")]
elif show == "Decided":
    visible = [item for item in items if item.get("decision")]
else:
    visible = items

# ── Portfolio ────────────────────────────────────────────────────────

gross = float(body.get("gross_profit_opportunity") or 0)
net = float(body.get("net_profit_opportunity") or 0)

ui.stat_row(
    [
        {
            "label": "Actions proposed",
            "value": str(body.get("count", 0)),
            "note": f"{body.get('decided_count', 0)} already decided",
        },
        {
            "label": "Profit opportunity",
            "value": number(net, "currency"),
            "note": "net of overlap — plan against this",
            "accent": SEMANTIC["positive"],
        },
        {
            "label": "If counted separately",
            "value": number(gross, "currency"),
            "note": "gross; double-promises overlapping actions",
        },
        {
            "label": "Capital freed",
            "value": number(body.get("capital_freed"), "currency"),
            "note": "working capital, never added to profit",
            "accent": SEMANTIC["capital"],
        },
    ]
)

if gross > net * 1.05 and net:
    st.caption(
        f"The {number(gross - net, 'currency')} gap between those two totals is overlap: "
        "several of these actions chase the same pounds."
    )

# ── Deciding ─────────────────────────────────────────────────────────


def decide(key: str, action: str, reason: str | None, note: str | None) -> None:
    """Record a decision and clear the transient UI state around it."""
    try:
        client.post(
            "/api/v1/recommendations/decisions",
            decision_key=key,
            action=action,
            reason_code=reason,
            note=note,
        )
    except ApiError as error:
        st.session_state["rm_decision_error"] = str(error)
    else:
        st.session_state.pop("rm_decision_error", None)
    for prefix in ("rm_dismissing_", "rm_reopen_"):
        st.session_state.pop(f"{prefix}{key}", None)
    st.rerun()


if st.session_state.get("rm_decision_error"):
    ui.failure(st.session_state["rm_decision_error"], what="That decision was not recorded")

ui.section(
    f"{len(visible)} action(s)",
    "Each card shows what the estimate rests on, what it risks, and what would make it wrong.",
    accent=SEMANTIC["accent"],
)

if not visible:
    if items:
        ui.empty(
            "Every proposed action already carries a decision. Switch the filter to "
            "'All' to review them.",
            what="Queue clear",
            icon="✓",
        )
    else:
        ui.empty(
            "Nothing clears the materiality floor in the selected categories.",
            what="No actions proposed",
        )
else:
    for index, item in enumerate(visible):
        ui.action_card(item, index=index, can_act=can_act, on_decide=decide)

# ── What the engine could not offer ──────────────────────────────────

empty_categories = body.get("categories_empty") or {}
if empty_categories:
    blocked = {name: reason for name, reason in empty_categories.items() if "cannot read" in reason}
    quiet = {name: reason for name, reason in empty_categories.items() if name not in blocked}

    if quiet:
        ui.caveats(
            [f"{name} — {reason}" for name, reason in quiet.items()],
            title="Categories with nothing to propose",
            tone=SEMANTIC["ai"],
        )
    if blocked:
        ui.caveats(
            [f"{name} — {reason}" for name, reason in blocked.items()],
            title="Categories your role cannot see",
        )

ui.caveats(body.get("caveats") or [])

# ── The ledger ───────────────────────────────────────────────────────

ui.section("Decision log", "What this team has decided, newest first.")

try:
    log = client.get("/api/v1/recommendations/decisions", limit=25)
except ApiError as error:
    ui.workspace_error(error, what="The decision log did not load")
    log = {}

entries = log.get("decisions") or []
if not entries:
    ui.empty(
        "Nobody has accepted or dismissed a proposal yet. Decisions recorded here "
        "survive the engine recomputing tomorrow.",
        what="No decisions recorded",
    )
else:
    accepted_total = float(log.get("accepted_profit") or 0)
    html(
        f"""
        <div class="rm-ledger-head">
            <span>{len(entries)} decision(s)</span>
            <span class="rm-ledger-total">
                {escape(number(accepted_total, "currency"))} expected across accepted actions
            </span>
        </div>
        <style>
        .rm-ledger-head {{
            display: flex; justify-content: space-between; flex-wrap: wrap; gap: 0.5rem;
            font-size: 0.8125rem; color: var(--rm-muted); margin-bottom: 0.6rem;
        }}
        .rm-ledger-total {{ font-variant-numeric: tabular-nums; }}
        </style>
        """
    )

    for entry in entries:
        action = str(entry.get("action"))
        reason_text = f"· {escape(entry.get('reason_code'))} " if entry.get("reason_code") else ""
        note_text = f"· {escape(entry.get('note'))}" if entry.get("note") else ""
        tone = SEMANTIC["positive"] if action == "accepted" else SEMANTIC["capital"]
        html(
            f"""
            <div class="rm-ledger-row">
                <span class="rm-dot" style="background:{tone}"></span>
                <div class="rm-ledger-body">
                    <div>{escape(entry.get("action_text", ""))}</div>
                    <div class="rm-ledger-meta">
                        {escape(action)} · {escape(relative_time(entry.get("decided_at")))}
                        · {escape(entry.get("category", ""))}
                        {reason_text}{note_text}
                    </div>
                </div>
                <span class="rm-ledger-amount">
                    {escape(number(entry.get("expected_profit"), "currency"))}
                </span>
            </div>
            <style>
            .rm-ledger-row {{
                display: flex; gap: 0.7rem; align-items: flex-start;
                padding: 0.6rem 0; border-bottom: 1px solid var(--rm-line);
            }}
            .rm-ledger-row .rm-dot {{ margin-top: 0.45rem; }}
            .rm-ledger-body {{ flex: 1; font-size: 0.875rem; }}
            .rm-ledger-meta {{ font-size: 0.72rem; color: var(--rm-faint); margin-top: 0.2rem; }}
            .rm-ledger-amount {{
                font-variant-numeric: tabular-nums; font-size: 0.875rem;
                color: var(--rm-muted); white-space: nowrap;
            }}
            </style>
            """
        )

    st.caption(
        "Amounts are what each action was expected to be worth **when it was decided**, "
        "not what it earned. Nothing in this platform measures the outcome yet — "
        "recording the expectation is what makes that measurable later."
    )

# ── Recommendation Learning ──────────────────────────────────────────

ui.section(
    "Recommendation Learning",
    "How reliable are our recommendations in practice? Based on measured outcomes.",
    accent=SEMANTIC["ai"],
)

try:
    calibration_data = client.get("/api/v1/recommendations/calibration")
except ApiError:
    ui.empty(
        "Calibration data is not available yet. Measurements will appear here after "
        "recommendations are accepted and their outcomes measured.",
        what="No calibration data",
        icon="📊",
    )
    st.stop()

measured_count = calibration_data.get("total_measured_outcomes", 0)
overall = calibration_data.get("overall_metrics", {})
limitations = calibration_data.get("limitations", [])

if measured_count == 0:
    ui.empty(
        "No measured outcomes yet. Calibration metrics will appear after decisions "
        "are measured (H+1, H+7, H+14, H+30 days).",
        what="Awaiting measurements",
        icon="⏱",
    )
else:
    is_significant = overall.get("is_statistically_significant", False)

    # Build stats
    stats = [
        {
            "label": "Measured outcomes",
            "value": str(measured_count),
            "note": "with outcomes measured" if is_significant else "insufficient for reliability",
            "accent": SEMANTIC["positive"] if is_significant else SEMANTIC["muted"],
        }
    ]

    if is_significant:
        # Direction accuracy
        direction_acc = overall.get("direction_accuracy")
        if direction_acc is not None:
            stats.append(
                {
                    "label": "Direction accuracy",
                    "value": f"{direction_acc * 100:.0f}%",
                    "note": "moved in expected direction",
                    "accent": SEMANTIC["positive"] if direction_acc > 0.75 else SEMANTIC["capital"],
                }
            )

        # Mean realization
        mean_real = overall.get("mean_realization_ratio")
        if mean_real is not None:
            stats.append(
                {
                    "label": "Mean realization",
                    "value": f"{mean_real * 100:.0f}%",
                    "note": "of expected impact realized",
                    "accent": (
                        SEMANTIC["positive"] if 0.85 <= mean_real <= 1.15 else SEMANTIC["capital"]
                    ),
                }
            )

        # Success rate
        success_rate = overall.get("success_rate")
        if success_rate is not None:
            stats.append(
                {
                    "label": "Success rate",
                    "value": f"{success_rate * 100:.0f}%",
                    "note": "achieved ≥70% of expected",
                    "accent": SEMANTIC["positive"] if success_rate > 0.70 else SEMANTIC["capital"],
                }
            )

    ui.stat_row(stats)

    # Best performing generators
    best_performing = calibration_data.get("best_performing_generators", [])
    if best_performing and is_significant:
        st.caption(f"**Best performing**: {', '.join(best_performing)}")

    # Needs calibration warnings
    needs_calibration = calibration_data.get("needs_calibration", [])
    if needs_calibration and is_significant:
        st.warning(f"⚠️ Systematic bias detected: {', '.join(needs_calibration)}")

    # Limitations
    if limitations:
        with st.expander("Known limitations"):
            for limitation in limitations:
                st.caption(f"• {limitation}")

    # Note about observational nature
    st.caption(
        "These metrics are **observational only**. The system learns from outcomes but "
        "does not automatically change recommendations, confidence scores, or estimator logic."
    )
