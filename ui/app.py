"""RetailMind — entry point, sign-in, and navigation.

**Navigation is declared, not discovered.** Streamlit's default is to publish
every file in a pages directory as its own sidebar entry and its own URL, for
everyone — including a visitor who has not signed in. Declaring the workspaces
here turns that off: signed out, the console contains exactly one page; signed
in, it contains the workspaces that role's permissions actually cover.

That is a courtesy, not a control. Every workspace gates itself through
`session.require`, and every endpoint behind it re-checks the caller.
"""

import streamlit as st

from retailmind_ui import design, session
from retailmind_ui.design import SEMANTIC, html


def sign_in() -> None:
    """The door. Deliberately quiet: one job, no navigation, no product tour."""
    left, right = st.columns([1.05, 1])

    with left:
        html(
            f"""
            <div class="rm-auth">
                <div class="rm-mark">◈</div>
                <h1>RetailMind</h1>
                <p class="rm-auth-lede">
                    Retail decision intelligence. Every figure arrives with the
                    evidence behind it and the confidence it has earned.
                </p>
                <div class="rm-auth-points">
                    <div><span style="color:{SEMANTIC["accent"]}">◆</span>
                        Investigations that decompose a movement rather than describe it</div>
                    <div><span style="color:{SEMANTIC["ai"]}">◇</span>
                        Answers that state what they did not check</div>
                    <div><span style="color:{SEMANTIC["positive"]}">●</span>
                        Recommendations priced net of what they risk</div>
                </div>
            </div>
            <style>
            .rm-auth {{ padding: 3.5rem 0 0; max-width: 44ch; }}
            .rm-mark {{
                font-size: 1.6rem; width: 44px; height: 44px; border-radius: 13px;
                display: grid; place-items: center; margin-bottom: 1.1rem;
                background: linear-gradient(140deg, {SEMANTIC["accent"]}, {SEMANTIC["ai"]});
                color: #06080C;
            }}
            .rm-auth h1 {{ font-size: 2.4rem; letter-spacing: -0.035em; margin: 0 0 0.6rem; }}
            .rm-auth-lede {{ color: var(--rm-muted); font-size: 1rem; line-height: 1.6; }}
            .rm-auth-points {{
                margin-top: 1.8rem; display: flex; flex-direction: column; gap: 0.7rem;
                font-size: 0.875rem; color: var(--rm-muted);
            }}
            .rm-auth-points span {{ margin-right: 0.55rem; }}
            </style>
            """
        )

    with right:
        st.write("")
        with st.container(border=True), st.form("sign_in", border=False):
            st.markdown("#### Sign in")
            email = st.text_input("Email", autocomplete="username", placeholder="you@company.com")
            password = st.text_input("Password", type="password", autocomplete="current-password")
            submitted = st.form_submit_button("Continue", width="stretch", type="primary")
            if submitted and session.sign_in(email, password):
                st.rerun()

            error = st.session_state.get(session.SESSION_ERROR)
            if error:
                st.error(error)

        st.caption(
            "Sessions are held server-side and never written to browser storage, "
            "so refreshing the page signs you out."
        )


def navigation() -> None:
    """The workspace switcher, grouped by the job each one does."""
    available = session.visible_workspaces()

    with st.sidebar:
        html(
            f"""
            <div class="rm-brand">
                <span class="rm-brand-mark">◈</span>
                <span class="rm-brand-name">RetailMind</span>
            </div>
            <style>
            .rm-brand {{ display:flex; align-items:center; gap:0.55rem; margin-bottom:1.1rem; }}
            .rm-brand-mark {{
                width: 26px; height: 26px; border-radius: 8px; display: grid;
                place-items: center; font-size: 0.9rem; color: #06080C;
                background: linear-gradient(140deg, {SEMANTIC["accent"]}, {SEMANTIC["ai"]});
            }}
            .rm-brand-name {{ font-weight: 650; letter-spacing: -0.02em; }}
            </style>
            """
        )

        # A jump box rather than a modal palette: Streamlit cannot capture a
        # global key chord without an iframe hack that breaks as often as it
        # works, and a shortcut hint that does nothing is worse than none.
        query = st.text_input(
            "Jump to",
            placeholder="Jump to workspace…",
            label_visibility="collapsed",
            key="rm_jump",
        )
        if query:
            matches = [
                item
                for item in available
                if query.lower() in item.name.lower() or query.lower() in item.purpose.lower()
            ]
            for item in matches[:5]:
                if st.button(f"{item.icon}  {item.name}", key=f"jump_{item.name}", width="stretch"):
                    st.session_state["rm_jump"] = ""
                    st.switch_page(item.file)
            if not matches:
                st.caption("No workspace matches that.")


def landing() -> None:
    """Signed in but not yet anywhere — send them to the Command Center."""
    session.chrome()
    st.switch_page(session.WORKSPACES[0].file)


design.configure("Console")

if session.is_signed_in():
    navigation()
    # No icons on the nav entries: `st.Page` validates them as emoji, and the
    # console's marks are geometric glyphs. Text-only reads better here anyway
    # — the workspace names are the navigation.
    pages = [
        st.Page(item.file, title=item.name, default=index == 0)
        for index, item in enumerate(session.visible_workspaces())
    ] or [st.Page(landing, title="No access")]
else:
    pages = [st.Page(sign_in, title="Sign in")]

st.navigation(pages).run()
