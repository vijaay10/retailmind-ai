"""Admin — who you are, what you may do, and what the platform knows."""

import streamlit as st

from retailmind_ui import components as ui
from retailmind_ui import session, theme
from retailmind_ui.api import ApiError

theme.configure("Admin")
client = session.require()

ui.page_header("Admin", "Your identity, your permissions, and the platform's own vocabulary.")

profile = session.user() or {}

ui.section("Signed-in user")
ui.kpi_row(
    [
        {"label": "Email", "value": profile.get("email", "—")},
        {"label": "Roles", "value": ", ".join(profile.get("roles") or []) or "—"},
        {"label": "Permissions", "value": str(len(session.permissions()))},
    ],
    columns=3,
)

tabs = st.tabs(["Permissions", "Areas", "Metric catalogue", "Session"])

with tabs[0]:
    granted = sorted(session.permissions())
    ui.table([{"permission": item} for item in granted], empty_reason="No permissions granted.")
    st.caption(
        "These are enforced by the API on every call. The console hides areas "
        "your role cannot use, but hiding is a courtesy — the endpoint refuses "
        "regardless of what the navigation shows."
    )

with tabs[1]:
    ui.table(
        [
            {
                "area": name,
                "requires": need,
                "available": "yes" if session.can(need) else "no",
            }
            for name, need in session.PAGE_PERMISSIONS.items()
        ]
    )

with tabs[2]:
    try:
        catalogue = client.get("/api/v1/nlq/catalogue")
    except ApiError as error:
        ui.error(str(error))
        catalogue = {}

    domains = catalogue.get("domains") or []
    st.caption(
        f"{len(domains)} domains. This is the complete set a natural-language "
        "question can reach — there is nothing outside it, which is the same "
        "fact that makes the query path injection-proof."
    )
    for domain in domains:
        with st.expander(f"{domain.get('label', domain.get('domain'))} · {domain.get('domain')}"):
            st.caption("Metrics: " + ", ".join(domain.get("metrics") or []))
            st.caption("Dimensions: " + ", ".join(domain.get("dimensions") or []))

with tabs[3]:
    st.caption(
        "Tokens are held in server-side session state and never written to "
        "browser storage, so a cross-site scripting bug cannot walk away with "
        "your session. The cost is that refreshing the page signs you out."
    )
    if st.button("Revoke all my sessions", type="secondary"):
        try:
            client.post("/api/v1/auth/sessions/revoke-all")
            session.sign_out()
            st.rerun()
        except ApiError as error:
            ui.error(str(error))
