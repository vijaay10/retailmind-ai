"""Admin — identity, permissions, vocabulary, and session control.

The most useful screen in the product for understanding what it *is*: the
complete list of what can be asked, the permission each area needs, and the
gap between what this console hides and what the API refuses.
"""

from typing import Any

import streamlit as st

from retailmind_ui import components as ui
from retailmind_ui import design, session
from retailmind_ui.api import ApiError

design.configure("Admin", icon="⚙")
client = session.require("admin.users")

ui.workspace_header(
    "Admin",
    eyebrow="Platform",
    summary="Who you are, what you may do, and the vocabulary the platform speaks.",
)

profile: dict[str, Any] = session.user() or {}

ui.stat_row(
    [
        {"label": "Signed in", "value": str(profile.get("email", "—"))},
        {"label": "Roles", "value": ", ".join(profile.get("roles") or []) or "—"},
        {"label": "Permissions", "value": str(len(session.permissions()))},
    ],
    columns=3,
)

tabs = st.tabs(["Permissions", "Workspaces", "Metric vocabulary", "Session"])

with tabs[0]:
    granted = sorted(session.permissions())
    ui.table(
        [{"permission": item} for item in granted],
        height=340,
        empty_reason="No permissions granted.",
    )
    st.caption(
        "These are enforced by the API on every call. The console hides areas your "
        "role cannot use, but hiding is a courtesy — the endpoint refuses regardless "
        "of what the navigation shows."
    )

with tabs[1]:
    try:
        catalogue_roles = client.get("/api/v1/auth/permissions")
    except ApiError:
        catalogue_roles = {}

    rows = catalogue_roles if isinstance(catalogue_roles, list) else catalogue_roles.get("data", [])
    issued: set[str] = set()
    for role in rows or []:
        issued.update(role.get("permissions") or [])

    ui.table(
        [
            {
                "workspace": item.name,
                "group": item.group,
                "requires": item.permission,
                "available": "yes" if session.can(item.permission) else "no",
                "purpose": item.purpose,
            }
            for item in session.WORKSPACES
        ],
        height=420,
    )

    # A workspace gated on a permission the API never issues is unreachable by
    # everyone — including the administrator looking for it — and nothing else
    # in the system would ever say so. Checking the console's own map against
    # the live catalogue is how that surfaces.
    if issued:
        unknown = [
            f"{item.name} requires `{item.permission}`, which no role grants"
            for item in session.WORKSPACES
            if item.permission not in issued
        ]
        if unknown:
            ui.caveats(unknown, title="Misconfigured workspace gates")
        else:
            st.caption(
                f"All {len(session.WORKSPACES)} workspace gates match permissions the "
                "API actually issues."
            )

with tabs[2]:
    try:
        catalogue = client.get("/api/v1/nlq/catalogue")
    except ApiError as error:
        ui.failure(str(error), what="The catalogue did not load")
        catalogue = {}

    domains = catalogue.get("domains") or []
    st.caption(
        f"{len(domains)} domains. This is the complete set a natural-language question "
        "can reach — there is nothing outside it, which is the same fact that makes "
        "the query path injection-proof."
    )
    for domain in domains:
        with st.container(border=True):
            st.markdown(f"**{domain.get('label', domain.get('domain'))}**")
            st.caption(f"`{domain.get('domain')}`")
            st.caption("Metrics · " + ", ".join(domain.get("metrics") or []))
            st.caption("Dimensions · " + ", ".join(domain.get("dimensions") or []))

with tabs[3]:
    st.caption(
        "Tokens are held in server-side session state and never written to browser "
        "storage, so a cross-site scripting bug cannot walk away with your session. "
        "The cost is that refreshing the page signs you out."
    )
    if st.button("Revoke all my sessions", type="secondary"):
        try:
            client.post("/api/v1/auth/sessions/revoke-all")
            session.sign_out()
            st.rerun()
        except ApiError as error:
            ui.failure(str(error), what="Revocation failed")
