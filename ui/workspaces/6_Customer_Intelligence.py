"""Customer Intelligence — the base as populations, never as people.

Every surface here is a population aggregate. The warehouse holds per-customer
rows so joins work, but no endpoint can project one, and groups below the
20-customer reporting floor are withheld *and counted* — so a reader knows the
picture is partial rather than quietly seeing a smaller world.
"""

from typing import Any

import streamlit as st

from retailmind_ui import charts, design, session
from retailmind_ui import components as ui
from retailmind_ui.api import ApiError
from retailmind_ui.design import SEMANTIC
from retailmind_ui.formatting import number

design.configure("Customer Intelligence", icon="●")
client = session.require("analytics.customer.read")

ui.workspace_header(
    "Customer Intelligence",
    eyebrow="Customer domain",
    summary="Segments, value, retention, and drift — as populations, never as individuals.",
)


def load(path: str, **params: object) -> dict[str, Any]:
    try:
        return client.get(path, **params)
    except ApiError as error:
        ui.workspace_error(error, what=f"{path.rsplit('/', 1)[-1]} did not load")
        return {}


churn = load("/api/v1/customers/churn-risk")
bands = churn.get("bands") or []

ui.stat_row(
    [
        {
            "label": "Value at risk",
            "value": number(churn.get("total_value_at_risk"), "currency"),
            "note": "medium and high bands only",
            "accent": SEMANTIC["critical"],
        },
        {
            "label": "VIP value at risk",
            "value": number(churn.get("vip_value_at_risk"), "currency"),
            "note": "expensive to replace, still reachable",
        },
        {
            "label": "Bands",
            "value": str(len(bands)),
            "note": "derived from cycles overdue, not a probability",
        },
    ],
    columns=3,
)

tabs = st.tabs(["Risk", "Segments", "Lifetime value", "Retention", "Journey", "VIPs"])

with tabs[0]:
    figure = charts.ranked_bars(
        bands, label="churn_risk_band", value="value_at_risk", colour=SEMANTIC["critical"]
    )
    if figure is not None:
        st.plotly_chart(figure, width="stretch", config={"displayModeBar": False})
    ui.table(bands, empty_reason="No risk bands returned.")
    ui.caveats(
        [
            "Risk is a band derived from how many of a customer's own purchase cycles "
            "have elapsed unfulfilled — not a probability. Calling it '68% likely to "
            "churn' would imply a calibration nobody measured.",
            "The headline counts medium and higher only. Customers in the 'none' band "
            "hold value, but it is not value at risk.",
        ]
    )

with tabs[1]:
    body = load("/api/v1/customers/segments")
    rows = body.get("data") or []
    figure = charts.ranked_bars(rows, label="rfm_segment", value="segment_value")
    if figure is not None:
        st.plotly_chart(figure, width="stretch", config={"displayModeBar": False})
    ui.table(rows, empty_reason="No segments above the reporting floor.")
    privacy = body.get("privacy") or {}
    if privacy.get("note"):
        ui.caveats([str(privacy["note"])], title="Privacy suppression")

with tabs[2]:
    body = load("/api/v1/customers/lifetime-value")
    ui.table(body.get("data") or [], empty_reason="No value rows.")
    ui.caveats(
        [
            "Historic lifetime value is arithmetic over what happened. The 12-month "
            "projection extrapolates observed cadence and carries a confidence grade — "
            "annualising a fortnight of history is a guess wearing a number's clothes.",
        ]
    )

with tabs[3]:
    body = load("/api/v1/customers/retention", limit=200)
    rows = body.get("data") or []
    figure = charts.cohort_heatmap(rows)
    if figure is not None:
        st.plotly_chart(figure, width="stretch", config={"displayModeBar": False})
        st.caption(
            "Deliberately triangular: cells stop at the observation edge. A cohort "
            "acquired two weeks ago has no week-8 retention, and a zero there would "
            "draw a cliff that never happened."
        )
    ui.table(rows[:60], height=300, empty_reason="No cohorts yet.")

with tabs[4]:
    body = load("/api/v1/customers/journey")
    stages = sorted(body.get("stages") or [], key=lambda row: float(row.get("stage_order") or 0))

    if not stages:
        ui.empty("No lifecycle stages returned.")
    else:
        # Deliberately not nested tabs. Plotly sizes a Sankey to its container
        # at first paint and does not redraw when a hidden tab is revealed, so
        # a flow diagram behind a second-level tab renders blank. Side by side
        # is also the better reading: the same progression, two questions.
        funnel_view, flow_view = st.columns([1, 1.35])

        with funnel_view:
            figure = charts.funnel(stages, stage="lifecycle_stage", value="reached_stage")
            if figure is not None:
                st.plotly_chart(figure, width="stretch", config={"displayModeBar": False})
                st.caption(
                    "Cumulative reach: how many customers ever got this far. A funnel is "
                    "only honest when each step is genuinely a subset of the one before "
                    "it, which is true of lifecycle progression and false of segments."
                )

        with flow_view:
            # A Sankey of the same progression. Every customer who reached a stage
            # either progressed or stalled there, so inflow equals outflow at each
            # node by construction — a Sankey whose flows do not reconcile looks
            # exactly as convincing as one that does, which is the trap.
            nodes: list[str] = [str(stage.get("lifecycle_stage")) for stage in stages]
            links: list[tuple[int, int, float]] = []
            for index in range(len(stages) - 1):
                reached = float(stages[index].get("reached_stage") or 0)
                progressed = float(stages[index + 1].get("reached_stage") or 0)
                stalled = max(reached - progressed, 0.0)
                if progressed > 0:
                    links.append((index, index + 1, progressed))
                if stalled > 0:
                    nodes.append(f"Stalled at {stages[index].get('lifecycle_stage')}")
                    links.append((index, len(nodes) - 1, stalled))

            figure = charts.sankey(nodes, links)
            if figure is not None:
                st.plotly_chart(figure, width="stretch", config={"displayModeBar": False})
                st.caption(
                    "Each stage splits into those who progressed and those who stopped "
                    "there. Widths reconcile: everyone who reached a stage appears in "
                    "exactly one outgoing flow."
                )
            else:
                ui.empty("Not enough stages to draw a flow.")

        ui.table(stages, empty_reason="No lifecycle stages returned.")
        st.caption(
            "Watch the New → Repeat step: a customer who never makes a second purchase "
            "never earns back their acquisition cost."
        )

with tabs[5]:
    body = load("/api/v1/customers/vip")
    ui.table(body.get("data") or [], empty_reason="No VIP cohort rows.")

ui.provenance(churn.get("meta") or {})
