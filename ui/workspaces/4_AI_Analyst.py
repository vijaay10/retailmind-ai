"""AI Analyst — ask in English, see everything that happened next.

**Not a chatbot.** A chatbot returns prose and asks you to trust it. This
returns the whole chain: the question, how it was understood, the plan it
compiled, the SQL that plan produced, the rows, the chart with its shape
rationale, the business reading, and what the answer did not cover.

**The question never becomes SQL.** It is parsed into a plan over a closed
vocabulary — named metrics and dimensions from the governed registry — and the
plan is compiled to SQL by the platform. That is why the compiled query shown
below always carries `?` placeholders for its dates: values are bound, never
interpolated, and a question containing `; DROP TABLE` resolves to nothing the
vocabulary recognises rather than to a string that reaches a database.

**"Why" questions do not run a query at all.** They route to the root-cause
engine, because a query cannot answer why, and the routing is shown rather
than hidden behind a uniform answer format.
"""

from typing import Any

import streamlit as st

from retailmind_ui import charts, design, session
from retailmind_ui import components as ui
from retailmind_ui.api import ApiError
from retailmind_ui.design import SEMANTIC, escape, html
from retailmind_ui.formatting import truncate

design.configure("AI Analyst", icon="◇")
client = session.require("insights.read")

HISTORY = "rm_analyst_history"
TURNS = "rm_analyst_turns"

st.session_state.setdefault(HISTORY, [])
st.session_state.setdefault(TURNS, [])

ui.workspace_header(
    "AI Analyst",
    eyebrow="Conversation",
    summary=(
        "Ask about the business. Every answer shows how the question was understood, "
        "what was run, and what it did not check."
    ),
)


def ask(question: str) -> None:
    """Send a question, keeping the conversation and the refusal path separate."""
    try:
        answer = client.post(
            "/api/v1/analyst/ask",
            question=question,
            conversation=st.session_state[TURNS],
            # Without this the analyst reasons about today, and a warehouse
            # ending three weeks ago makes "revenue fell 100%" the honest
            # answer to a question nobody asked.
            as_of=session.data_date().isoformat(),
        )
    except ApiError as error:
        # A refusal is a real answer here: the analyst declines questions the
        # platform cannot answer rather than approximating them.
        st.session_state[HISTORY].append({"question": question, "refusal": str(error)})
        return

    st.session_state[HISTORY].append({"question": question, "answer": answer})
    st.session_state[TURNS] = (answer.get("conversation") or {}).get("turns") or []


def query(question: str) -> None:
    """Run the same question through the query path, to expose the plan and SQL."""
    try:
        st.session_state["rm_nlq"] = client.post("/api/v1/nlq/ask", question=question)
    except ApiError as error:
        st.session_state["rm_nlq"] = {"error": str(error), "question": question}


def render_plan(plan: dict[str, Any], *, compiled: str, routed: str) -> None:
    """The interpretation, the plan, and the query it compiled to."""
    confidence = float(plan.get("confidence") or 0)
    unresolved = plan.get("unresolved") or []
    window_text = f"{escape(plan.get('start_date', '—'))} → {escape(plan.get('end_date', '—'))}"

    html(
        f"""
        <div class="rm-plan">
            <div class="rm-plan-head">
                <span class="rm-eyebrow">Understood as</span>
                {ui.chip(routed or plan.get("intent", "query"), colour=SEMANTIC["ai"], filled=True)}
            </div>
            <div class="rm-plan-read">{escape(plan.get("interpretation", ""))}</div>
            <div class="rm-plan-grid">
                <div><span>domain</span>{escape(plan.get("domain", "—"))}</div>
                <div><span>metrics</span>{escape(", ".join(plan.get("metrics") or []) or "—")}</div>
                <div><span>grouped by</span>
                    {escape(", ".join(plan.get("dimensions") or []) or "—")}</div>
                <div><span>window</span>{window_text}</div>
            </div>
            <div style="margin-top:0.7rem;max-width:220px">
                {ui.confidence_meter(confidence)}
            </div>
        </div>
        <style>
        .rm-plan {{
            border: 1px solid {SEMANTIC["ai"]}33; border-radius: 14px;
            padding: 0.9rem 1.1rem; background: {SEMANTIC["ai"]}0a;
        }}
        .rm-plan-head {{ display:flex; gap:0.6rem; align-items:center; margin-bottom:0.5rem; }}
        .rm-plan-read {{ font-size: 0.875rem; }}
        .rm-plan-grid {{
            display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
            gap: 0.5rem; margin-top: 0.8rem; font-size: 0.8125rem;
        }}
        .rm-plan-grid span {{
            display: block; font-size: 0.68rem; letter-spacing: 0.08em;
            text-transform: uppercase; color: var(--rm-faint); margin-bottom: 0.1rem;
        }}
        </style>
        """
    )

    if unresolved:
        ui.caveats(
            [f"Not recognised: {', '.join(str(item) for item in unresolved)}"],
            title="Terms the vocabulary does not contain",
        )

    if compiled:
        st.code(compiled, language="sql")
        st.caption(
            "Compiled from the plan above — not from the question. The `?` placeholders "
            "are bound parameters: dates are passed to the driver, never pasted into the "
            "string. The table is a governed semantic view, and every column name came "
            "from the metric registry rather than from anything typed."
        )


def render(entry: dict[str, Any], *, index: int, last: bool) -> None:
    with st.chat_message("user"):
        st.markdown(f"**{entry['question']}**")

    with st.chat_message("assistant"):
        if entry.get("refusal"):
            html(
                f"""
                <div class="rm-refusal">
                    <span class="rm-refusal-tag">Cannot answer</span>
                    <span>{escape(entry["refusal"])}</span>
                </div>
                <style>
                .rm-refusal {{
                    display: flex; gap: 0.6rem; align-items: baseline;
                    border: 1px solid {SEMANTIC["warning"]}55; background: {SEMANTIC["warning"]}0f;
                    border-radius: 12px; padding: 0.7rem 0.95rem; font-size: 0.875rem;
                }}
                .rm-refusal-tag {{
                    color: {SEMANTIC["warning"]}; font-weight: 650; font-size: 0.7rem;
                    letter-spacing: 0.06em; text-transform: uppercase; white-space: nowrap;
                }}
                </style>
                """
            )
            st.caption(
                "Refusing is the feature. An answer assembled from whatever the "
                "vocabulary matched would be confidently about the wrong thing."
            )
            return

        answer = entry["answer"]
        ui.ai_summary(
            str(answer.get("headline", "")),
            title=f"Answered as · {answer.get('capability', '')}",
        )

        ui.statements(answer.get("facts") or [], heading="What the data shows")
        ui.statements(answer.get("inferences") or [], heading="What follows from it")
        ui.checked_and_not(answer.get("checked") or [], answer.get("not_checked") or [])

        rows = (answer.get("data") or {}).get("rows") or []
        if rows:
            ui.table(rows[:25], height=260)

        ui.caveats(answer.get("caveats") or [])

        follow_ups = answer.get("follow_ups") or []
        if follow_ups and last:
            html('<div class="rm-eyebrow" style="margin:0.8rem 0 0.4rem">Worth asking next</div>')
            for slot, item in zip(
                st.columns(min(3, len(follow_ups))), follow_ups[:3], strict=False
            ):
                question = str(item.get("question", ""))
                with slot:
                    if st.button(
                        truncate(question, 44),
                        key=f"fu_{index}_{question[:20]}",
                        width="stretch",
                        help=str(item.get("because", "")),
                    ):
                        ask(question)
                        st.rerun()


# ── Conversation ─────────────────────────────────────────────────────

history = st.session_state[HISTORY]

if not history:
    ui.section("Start anywhere", "Three questions that exercise different engines.")
    starters = [
        ("Why did revenue fall?", "routes to root-cause analysis"),
        ("Show revenue by region", "compiles a governed query"),
        ("What should we do next?", "reads the recommendation engine"),
    ]
    for slot, (question, note) in zip(st.columns(3), starters, strict=True):
        with slot, st.container(border=True):
            st.markdown(f"**{question}**")
            st.caption(note)
            if st.button("Ask", key=f"start_{question[:12]}", width="stretch"):
                ask(question)
                st.rerun()

for index, entry in enumerate(history):
    render(entry, index=index, last=index == len(history) - 1)

asked = st.chat_input("Ask about the business…")
if asked:
    ask(asked)
    query(asked)
    st.rerun()

# ── The query path ───────────────────────────────────────────────────

nlq = st.session_state.get("rm_nlq")
if nlq and not nlq.get("error"):
    ui.section(
        "How the last question was executed",
        "The plan, the compiled query, and the result it returned.",
        accent=SEMANTIC["ai"],
    )
    render_plan(
        nlq.get("plan") or {},
        compiled=str(nlq.get("compiled_sql") or ""),
        routed=str(nlq.get("routed_to") or ""),
    )

    rows = nlq.get("rows") or []
    chart_spec = nlq.get("chart") or {}
    if rows:
        left, right = st.columns([1.4, 1])
        with left:
            figure = None
            kind = str(chart_spec.get("type", ""))
            columns = nlq.get("columns") or []
            category = str(chart_spec.get("x") or (columns[0] if columns else ""))
            measures = [str(item) for item in (chart_spec.get("y") or [])]
            if measures:
                measure = measures[0]
                actual_category = category if category in rows[0] else str(columns[0])
                actual_measure = measure if measure in rows[0] else str(columns[-1])
                if kind == "line":
                    figure = charts.trend(rows, x=actual_category, y=actual_measure)
                else:
                    figure = charts.ranked_bars(rows, label=actual_category, value=actual_measure)
            if figure is not None:
                st.plotly_chart(figure, width="stretch", config={"displayModeBar": False})
                if chart_spec.get("rationale"):
                    st.caption(str(chart_spec["rationale"]))
        with right:
            ui.table(rows[:20], height=300)

    explanation = nlq.get("explanation") or {}
    if explanation.get("summary"):
        ui.ai_summary(
            str(explanation["summary"]),
            title="Business reading",
            bullets=[str(item) for item in (explanation.get("details") or [])],
        )
    ui.caveats(explanation.get("caveats") or [])
    ui.provenance(nlq.get("meta") or {}, extra=[f"{nlq.get('row_count', 0)} rows"])

with st.sidebar:
    ui.divider("0.5rem")
    if st.button("Clear conversation", width="stretch"):
        st.session_state[HISTORY] = []
        st.session_state[TURNS] = []
        st.session_state.pop("rm_nlq", None)
        st.rerun()
    st.caption(
        "Follow-ups resolve against earlier turns — only the subject and period "
        "carry forward, never the prose."
    )
