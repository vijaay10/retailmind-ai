"""Sign-in, session state, and the permission gate.

**Tokens live in Streamlit's session state, which is server-side.** That is a
deliberate improvement on the browser-storage default: nothing token-shaped
ever reaches `localStorage`, so a cross-site scripting bug in a dependency
cannot walk away with a session. The cost is honest and worth stating — session
state does not survive a browser refresh, so a reload signs the user out. For
an internal console that is the right side of the trade.

**The UI hides what a role cannot use, and the API enforces it anyway.** Hiding
a page is a courtesy, not a control: every endpoint behind it re-checks the
caller's permissions. A console that treats its own navigation as the security
boundary is one where guessing a URL is an exploit.
"""

import contextlib
import os
from dataclasses import dataclass
from datetime import date
from typing import Any

import streamlit as st

from retailmind_ui.api import DEFAULT_BASE_URL as DEFAULT_API
from retailmind_ui.api import ApiClient, ApiError

SESSION_CLIENT = "rm_client"
SESSION_USER = "rm_user"
SESSION_ERROR = "rm_login_error"
SESSION_DATA_DATE = "rm_data_date"


@dataclass(frozen=True, slots=True)
class Area:
    """One page in the console, and what it takes to see it."""

    name: str
    file: str
    permission: str
    icon: str


#: The console's map. `app.py` builds navigation from this and nothing else:
#: Streamlit's default file-based sidebar lists every page in `pages/` to
#: everyone, including a visitor who has not signed in, who would be shown the
#: full shape of a product they cannot open. Declaring the navigation instead
#: turns that off entirely. The API still enforces the real rule — this only
#: decides which links exist.
AREAS: tuple[Area, ...] = (
    Area("Executive Dashboard", "pages/1_Executive_Dashboard.py", "analytics.revenue.read", "📊"),
    Area("Sales", "pages/2_Sales.py", "analytics.revenue.read", "💷"),
    Area("Customers", "pages/3_Customers.py", "analytics.customer.read", "👥"),
    Area("Inventory", "pages/4_Inventory.py", "analytics.inventory.read", "📦"),
    Area("Forecast", "pages/5_Forecast.py", "forecasts.read", "📈"),
    Area("Recommendations", "pages/6_Recommendations.py", "recommendations.read", "🎯"),
    Area("AI Analyst", "pages/7_AI_Analyst.py", "insights.read", "💬"),
    Area("Reports", "pages/8_Reports.py", "reports.read", "📄"),
    Area("Alerts", "pages/9_Alerts.py", "alerts.read", "🔔"),
    Area("Admin", "pages/10_Admin.py", "admin.users.read", "⚙️"),
)

#: The same map, read as "what does this area need" — used by the gate.
PAGE_PERMISSIONS: dict[str, str] = {area.name: area.permission for area in AREAS}


def api_base_url() -> str:
    """Where the API lives: environment first, secrets file if present.

    `st.secrets` *raises* when no secrets.toml exists rather than returning the
    default, so reading it unguarded breaks a fresh checkout on the first click
    — which is exactly the deployment least likely to have one.
    """
    with contextlib.suppress(Exception):
        configured = st.secrets.get("api_base_url")
        if configured:
            return str(configured)
    return os.environ.get("RM_API_BASE_URL", DEFAULT_API)


def client() -> ApiClient:
    """The API client for this session, created once."""
    if SESSION_CLIENT not in st.session_state:
        st.session_state[SESSION_CLIENT] = ApiClient(base_url=api_base_url())
    return st.session_state[SESSION_CLIENT]  # type: ignore[no-any-return]


def user() -> dict[str, Any] | None:
    return st.session_state.get(SESSION_USER)


def data_date() -> date:
    """The latest business date the warehouse holds — not today's clock date.

    Every date control in this console defaults to this rather than to today.
    The two are not the same: the warehouse lands a day behind at best, and in
    a demo environment it can be weeks behind. A period ending "today" then
    covers a stretch with no data at all, and the platform dutifully reports a
    100% collapse in revenue. Asking the API what it actually has is one cheap
    call, and it is the difference between a console that looks broken and one
    that says which day it is talking about.
    """
    if SESSION_DATA_DATE not in st.session_state:
        resolved = date.today()
        with contextlib.suppress(ApiError, ValueError, TypeError):
            meta = client().get("/api/v1/analytics/revenue/summary").get("meta") or {}
            if meta.get("freshness"):
                resolved = date.fromisoformat(str(meta["freshness"])[:10])
        st.session_state[SESSION_DATA_DATE] = resolved
    return st.session_state[SESSION_DATA_DATE]  # type: ignore[no-any-return]


def is_signed_in() -> bool:
    return user() is not None and client().tokens is not None


def permissions() -> set[str]:
    profile = user() or {}
    return set(profile.get("permissions") or [])


def can(permission: str) -> bool:
    return permission in permissions()


def sign_in(email: str, password: str) -> bool:
    api = client()
    try:
        api.login(email, password)
        st.session_state[SESSION_USER] = api.me()
        st.session_state.pop(SESSION_ERROR, None)
        return True
    except ApiError as error:
        # The message is the API's own. Inventing "invalid credentials" would
        # be wrong for a locked account or an unreachable backend, and those
        # need different responses from the person reading it.
        st.session_state[SESSION_ERROR] = str(error)
        return False


def sign_out() -> None:
    if SESSION_CLIENT in st.session_state:
        st.session_state[SESSION_CLIENT].logout()
    for key in (SESSION_CLIENT, SESSION_USER, SESSION_ERROR, SESSION_DATA_DATE):
        st.session_state.pop(key, None)


def chrome() -> None:
    """Identity and sign-out — drawn on every page.

    Streamlit runs *only* the current page's script, so anything the entry
    point draws exists nowhere else. A sidebar built in `app.py` alone leaves
    every other page with no way to sign out, which is how a console ends up
    with users closing the browser to log out.
    """
    profile = user() or {}
    with st.sidebar:
        st.markdown("### RetailMind AI")
        st.caption(profile.get("email", ""))
        st.caption(f"Signed in as {', '.join(profile.get('roles') or []) or 'no roles'}")
        st.divider()

        st.caption(
            f"{len(visible_pages())} of {len(AREAS)} areas available to your role. "
            "Hidden pages are also refused by the API."
        )
        if st.button("Sign out", width="stretch", key="rm_sign_out"):
            sign_out()
            st.rerun()


def require(permission: str | None = None) -> ApiClient:
    """Gate a page, and draw the chrome around it.

    ``st.stop()`` rather than a redirect: Streamlit runs the whole script top
    to bottom, so returning early would still execute everything below.
    """
    if not is_signed_in():
        st.warning("Sign in to continue.")
        st.stop()
    chrome()
    if permission and not can(permission):
        st.error(
            f"Your role does not include this area (needs `{permission}`). "
            "Ask an administrator if you think that is wrong."
        )
        st.stop()
    return client()


def visible_pages() -> list[str]:
    return [area.name for area in AREAS if can(area.permission)]
