"""RetailMind operator console — entry point, sign-in, and navigation.

**Navigation is declared, not discovered.** Streamlit's default behaviour is to
publish every file in `pages/` as its own sidebar entry and its own URL, for
everyone — including a visitor who has not signed in. That leaks the shape of
the product to anyone who loads the page, and shows a store manager six areas
their role will refuse. Declaring the pages here instead turns that off: a
signed-out visitor's console contains exactly one page, and a signed-in user's
contains the areas their permissions actually cover.

That is a courtesy, not a control. Every page still gates itself through
`session.require`, and every endpoint behind it re-checks the caller. A console
whose navigation *is* its security boundary is one where guessing a URL is an
exploit.
"""

import streamlit as st

from retailmind_ui import session, theme


def sign_in_form() -> None:
    st.markdown("# RetailMind AI")
    st.caption("Retail decision intelligence — operator console")

    left, _ = st.columns([1, 1])
    with left, st.form("sign_in", border=True):
        st.markdown("#### Sign in")
        email = st.text_input("Email", autocomplete="username")
        password = st.text_input("Password", type="password", autocomplete="current-password")

        if st.form_submit_button("Sign in", width="stretch") and session.sign_in(email, password):
            st.rerun()

        error = st.session_state.get(session.SESSION_ERROR)
        if error:
            st.error(error)

    st.caption(
        "Sessions are held server-side and are not written to browser storage, "
        "so refreshing the page signs you out."
    )


def landing() -> None:
    session.chrome()

    st.markdown("# RetailMind AI")
    st.caption("Pick an area from the sidebar, or ask the analyst a question.")

    available = session.visible_pages()
    if not available:
        st.warning("Your role has no analytics areas enabled. An administrator can grant them.")
        return

    st.markdown("#### Available to you")
    for start in range(0, len(available), 3):
        for slot, name in zip(st.columns(3), available[start : start + 3], strict=False):
            with slot, st.container(border=True):
                st.markdown(f"**{name}**")
                st.caption(f"Requires `{session.PAGE_PERMISSIONS[name]}`")


theme.configure("Console")

if session.is_signed_in():
    pages = [
        st.Page(landing, title="Home", icon="🏠", default=True),
        *[
            st.Page(area.file, title=area.name, icon=area.icon)
            for area in session.AREAS
            if session.can(area.permission)
        ],
    ]
else:
    pages = [st.Page(sign_in_form, title="Sign in", icon="🔐")]

st.navigation(pages).run()
