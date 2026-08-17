"""AI Investigation — take a movement apart.

**The question is never "what chart do you want".** It is "this moved, why".
The investigation runs the platform's root-cause engine across nine dimensions
and returns two kinds of answer that this workspace refuses to blend:

* **Where it landed** — arithmetic. A segment's contribution to a decline is
  subtraction over observed rows, and it is drawn in the measured tier colours.
* **Why it might have happened** — hypotheses. Weather coinciding with the drop
  is an association with a hard confidence ceiling, and each one carries the
  sentence naming what it does not establish.

The sweep itself is shown, not just its conclusions. "Nine dimensions examined,
two produced findings" is a result; hiding the seven that came back empty makes
the two look like the only places anyone looked.
"""

from datetime import timedelta
from typing import Any

import streamlit as st

from retailmind_ui import charts, design, session
from retailmind_ui import components as ui
from retailmind_ui.api import ApiError
from retailmind_ui.design import SEMANTIC, escape, html
from retailmind_ui.formatting import number, signed_rate

design.configure("AI Investigation", icon="◎")
client = session.require("insights.read")

OPENED = "rm_open_finding"
METRICS = ["net_revenue", "units_sold", "orders", "aov", "margin_amount"]

focus = session.focus()

ui.workspace_header(
    "Investigation",
    eyebrow="Root cause",
    summary=(
        "A movement decomposed into where it landed and why it might have happened, "
        "with every candidate graded by the kind of evidence behind it."
    ),
)

if focus.get("reason"):
    ui.breadcrumb(["Command Center", str(focus["reason"])])

# ── Subject ──────────────────────────────────────────────────────────

controls = st.columns([2, 2, 2, 1.4])
metric = controls[0].selectbox(
    "Metric",
    METRICS,
    index=METRICS.index(focus["metric"]) if focus.get("metric") in METRICS else 0,
)
end = controls[1].date_input("Period end", value=session.data_date())
window = controls[2].slider("Period length (days)", 3, 28, 7)
run = controls[3].button("Investigate", type="primary", width="stretch")

start = end - timedelta(days=window - 1)

if focus:
    session.clear_focus()

# The investigation runs on load rather than waiting for a click: a workspace
# that opens empty asks the reader to do work before it does any.
progress = st.empty()
with progress.container():
    ui.working(f"Sweeping nine dimensions for {metric} · {start:%d %b} → {end:%d %b}…")
    ui.skeleton(rows=2, height="64px")

try:
    result: dict[str, Any] = client.get(
        "/api/v1/rca/investigate",
        metric=metric,
        current_start=start.isoformat(),
        current_end=end.isoformat(),
    )
except ApiError as error:
    progress.empty()
    ui.workspace_error(error, what="The investigation did not run")
    st.stop()

progress.empty()

# ── The movement ─────────────────────────────────────────────────────

change = float(result.get("change") or 0)
relative = result.get("relative_change")
direction = "down" if change < 0 else "up"
tone = SEMANTIC["critical"] if change < 0 else SEMANTIC["positive"]

lede, windows = st.columns([1.15, 1.5])

with lede:
    ui.headline_card(
        eyebrow=f"{metric.replace('_', ' ')} · movement",
        value=number(change, "currency"),
        delta=signed_rate(relative),
        direction=direction,
        caption=(
            f"{number(result.get('baseline_value'), 'currency')} → "
            f"{number(result.get('current_value'), 'currency')}"
        ),
        accent=tone,
    )

with windows:
    ui.window_comparison(
        result.get("current") or {},
        result.get("baseline") or {},
        scale=(result.get("meta") or {}).get("baseline_scaled_by"),
    )

where = result.get("where") or []
why = result.get("why") or []
investigated = result.get("dimensions_investigated") or []
unavailable = result.get("dimensions_unavailable") or {}

# ── The sweep ────────────────────────────────────────────────────────

sweep, cover = st.columns([1.35, 1])

with sweep:
    ui.section("How this was investigated", "Every dimension swept, and what each returned.")

    found_dims = {str(item.get("dimension")) for item in [*where, *why]}
    steps: list[dict[str, Any]] = [
        {
            "title": "Compared periods",
            "detail": (
                f"{(result.get('current') or {}).get('days')} days against "
                f"{(result.get('baseline') or {}).get('days')} days, scaled per-day."
            ),
            "state": "done",
        }
    ]
    for dimension in investigated:
        hits = [item for item in [*where, *why] if item.get("dimension") == dimension]
        if dimension in unavailable:
            steps.append(
                {
                    "title": dimension,
                    "detail": str(unavailable[dimension]),
                    "state": "blocked",
                }
            )
        elif hits:
            steps.append(
                {
                    "title": dimension,
                    "detail": f"{len(hits)} finding(s) above the materiality floor",
                    "state": "found",
                }
            )
        else:
            steps.append(
                {
                    "title": dimension,
                    "detail": "swept, nothing above the floor",
                    "state": "empty",
                }
            )
    steps.append(
        {
            "title": "Ranked and graded",
            "detail": f"{len(found_dims)} dimension(s) produced candidates.",
            "state": "done",
        }
    )
    ui.timeline(steps)

with cover:
    ui.section("Coverage", "How much of the movement the findings account for.")
    ui.coverage(result.get("explained_share"), investigated, unavailable)

    figure = charts.confidence_strip([*where, *why])
    if figure is not None:
        st.plotly_chart(figure, width="stretch", config={"displayModeBar": False})
        st.caption(
            "Impact against confidence. A large impact at low confidence is a "
            "hypothesis worth testing, not an action worth taking — which is why "
            "these are two axes rather than one score."
        )

# ── Findings ─────────────────────────────────────────────────────────


def _open(item: dict[str, Any]) -> None:
    identity = f"{item.get('dimension')}::{item.get('subject')}"
    st.session_state[OPENED] = None if st.session_state.get(OPENED) == identity else identity
    st.rerun()


ui.section("Where the movement landed", "Two readings of the same decomposition.")
shape = st.radio(
    "Shape",
    ["Bridge", "Contribution"],
    horizontal=True,
    label_visibility="collapsed",
    key="rm_rca_shape",
)

if shape == "Bridge":
    # A bridge answers "how did we get from there to here", which is the
    # question a decomposition is usually opened to settle. It is the same
    # arithmetic as the diverging bars, arranged as a path rather than a
    # ranking.
    figure = charts.bridge(
        baseline=float(result.get("baseline_value") or 0),
        steps=where,
        label="subject",
        value="impact_amount",
    )
    if figure is not None:
        st.plotly_chart(figure, width="stretch", config={"displayModeBar": False})
        st.caption(
            "Baseline, then each slice's excess contribution, then the current period. "
            "The named steps do not have to close the gap — they are the slices that "
            "cleared the materiality floor, not a complete attribution, and the "
            "difference is exactly what remains unexplained."
        )
else:
    figure = charts.contribution(where, label="subject", value="impact_amount")
    if figure is not None:
        st.plotly_chart(figure, width="stretch", config={"displayModeBar": False})
        st.caption(
            "Diverging from zero: the most useful fact in a decomposition is that "
            "some slices moved against the trend, which a magnitude ranking hides."
        )

ui.findings_group(
    where,
    title="Contribution",
    subtitle="Arithmetic over observed rows — this is where the change showed up.",
    opened=st.session_state.get(OPENED),
    on_open=_open,
    empty_reason="No slice moved enough to clear the materiality floor.",
)

ui.findings_group(
    why,
    title="Candidate explanations",
    subtitle="Operational factors that coincide. None of these establish causation.",
    opened=st.session_state.get(OPENED),
    on_open=_open,
    empty_reason=(
        "No operational factor coincided with the movement above the reporting "
        "threshold. That is not the same as there being no cause."
    ),
)

# ── What to do about it ──────────────────────────────────────────────

linked = [item for item in where if item.get("recommendations")]
if linked:
    ui.section("Actions attached to these findings", accent=SEMANTIC["accent"])
    for item in linked[:3]:
        for suggestion in list(item.get("recommendations") or [])[:2]:
            html(
                f"""
                <div class="rm-linked">
                    <span class="rm-linked-sub">{escape(item.get("subject", ""))}</span>
                    <span>{escape(suggestion)}</span>
                </div>
                <style>
                .rm-linked {{
                    display: flex; gap: 0.7rem; align-items: baseline; padding: 0.5rem 0;
                    border-bottom: 1px solid var(--rm-line); font-size: 0.875rem;
                }}
                .rm-linked-sub {{
                    color: var(--rm-faint); font-size: 0.75rem; min-width: 120px;
                }}
                </style>
                """
            )
    if session.can("recommendations.read") and st.button("Open the decision queue"):
        session.open_workspace("Decision Center")

# ── Next steps ───────────────────────────────────────────────────────

ui.section(
    "What's next",
    "Investigation shows what happened. Forecast and recommendations show what to expect and do.",
    accent=SEMANTIC["ai"],
)

next_actions = st.columns(2)

with next_actions[0], st.container(border=True):
    html(
        f"""
            <div class="rm-next">
                <div class="rm-next-title">See the forecast</div>
                <div class="rm-next-body">
                    View predictions for {metric.replace("_", " ")} with confidence intervals,
                    accuracy metrics, and backtest scores.
                </div>
            </div>
            <style>
            .rm-next {{ padding: 0.3rem 0; }}
            .rm-next-title {{ font-weight: 620; font-size: 0.95rem; margin-bottom: 0.35rem; }}
            .rm-next-body {{ font-size: 0.8125rem; color: var(--rm-muted); line-height: 1.5; }}
            </style>
            """
    )
    if session.can("forecasts.read") and st.button(
        "Open Forecast workspace", key="nav_forecast", width="stretch"
    ):
        nav_reason = (
            f"From investigation: {metric.replace('_', ' ')} {direction} {signed_rate(relative)}"
        )
        session.open_workspace(
            "Forecast Intelligence",
            metric=metric,
            reason=nav_reason,
        )

with next_actions[1], st.container(border=True):
    html(
        """
            <div class="rm-next">
                <div class="rm-next-title">See recommendations</div>
                <div class="rm-next-body">
                    View ranked actions with expected profit, confidence, risk, and
                    what would make them wrong.
                </div>
            </div>
            """
    )
    if session.can("recommendations.read") and st.button(
        "Open Decision Center", key="nav_decisions", width="stretch"
    ):
        nav_reason = (
            f"From investigation: {metric.replace('_', ' ')} {direction} {signed_rate(relative)}"
        )
        session.open_workspace(
            "Decision Center",
            reason=nav_reason,
        )

ui.caveats(result.get("caveats") or [], title="How to read this investigation")
ui.confidence_legend()
