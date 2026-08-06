"""AI Analyst — a conversation with the platform."""

from typing import Any

import streamlit as st

from retailmind_ui import components as ui
from retailmind_ui import session, theme
from retailmind_ui.api import ApiError
from retailmind_ui.formatting import truncate

theme.configure("AI Analyst")
client = session.require("insights.read")

HISTORY = "rm_analyst_history"
TURNS = "rm_analyst_turns"

ui.page_header(
    "AI Analyst",
    "Answers questions, explains KPIs, investigates movements, and says what it did not check.",
)

st.session_state.setdefault(HISTORY, [])
st.session_state.setdefault(TURNS, [])


def ask(question: str) -> None:
    try:
        answer = client.post(
            "/api/v1/analyst/ask",
            question=question,
            conversation=st.session_state[TURNS],
            # Without this the analyst reasons about today, and a warehouse
            # that ends last month makes "revenue fell 100%" the honest answer
            # to a question nobody asked.
            as_of=session.data_date().isoformat(),
        )
    except ApiError as error:
        # A refusal is a real answer here: the analyst declines questions the
        # platform cannot answer rather than approximating them.
        st.session_state[HISTORY].append({"question": question, "refusal": str(error)})
        return

    st.session_state[HISTORY].append({"question": question, "answer": answer})
    st.session_state[TURNS] = (answer.get("conversation") or {}).get("turns") or []


def render(entry: dict[str, Any], *, index: int, last: bool) -> None:
    with st.chat_message("user"):
        st.markdown(entry["question"])

    with st.chat_message("assistant"):
        if entry.get("refusal"):
            st.warning(entry["refusal"])
            return

        answer = entry["answer"]
        st.markdown(f"**{answer.get('headline', '')}**")
        st.caption(f"Answered as: {answer.get('capability', '')}")

        ui.statements(answer.get("facts") or [], heading="What the data shows")
        ui.statements(answer.get("inferences") or [], heading="What follows from it")

        checked = answer.get("checked") or []
        not_checked = answer.get("not_checked") or []
        if checked:
            st.caption("Checked: " + "; ".join(checked))
        if not_checked:
            # The most useful thing a senior analyst says.
            st.caption("Not checked: " + "; ".join(not_checked))

        rows = (answer.get("data") or {}).get("rows") or []
        if rows:
            ui.table(rows[:25])

        ui.caveats(answer.get("caveats") or [])

        questions = ui.follow_ups(answer.get("follow_ups") or [])
        # Only the newest turn gets buttons. Offering them on an older answer
        # would append its follow-up to the end of a conversation that has
        # since moved on, and the analyst would resolve it against the wrong
        # subject.
        if questions and last:
            for slot, question in zip(st.columns(len(questions)), questions, strict=True):
                if slot.button(truncate(question, 42), key=f"fu_{index}_{question[:24]}"):
                    ask(question)
                    st.rerun()


history = st.session_state[HISTORY]
for index, entry in enumerate(history):
    render(entry, index=index, last=index == len(history) - 1)

if not history:
    st.caption("Try: “Why did revenue fall?” · “What does AOV mean?” · “What should we do next?”")

question = st.chat_input("Ask about the business…")
if question:
    ask(question)
    st.rerun()

with st.sidebar:
    st.divider()
    if st.button("Clear conversation", width="stretch"):
        st.session_state[HISTORY] = []
        st.session_state[TURNS] = []
        st.rerun()
    st.caption(
        "Follow-ups resolve against earlier turns — only their subject and "
        "period are carried forward, never the prose."
    )
