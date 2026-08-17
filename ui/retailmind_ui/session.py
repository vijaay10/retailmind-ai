"""Sign-in, session state, the workspace registry, and the permission gate.

**Tokens live in Streamlit's session state, which is server-side.** Nothing
token-shaped ever reaches `localStorage`, so a cross-site scripting bug in a
dependency cannot walk away with a session. The cost is stated rather than
hidden: session state does not survive a browser refresh, so a reload signs the
user out. For an internal console that is the right side of the trade.

**The console hides what a role cannot use, and the API enforces it anyway.**
Hiding a workspace is a courtesy, not a control: every endpoint behind it
re-checks the caller. A console whose navigation *is* its security boundary is
one where guessing a URL is an exploit.

**Workspaces, not pages.** A page is a place charts live. A workspace is a
place a question gets answered — it owns a job (understand the day, investigate
a movement, decide what to do) and pulls whatever surfaces that job needs. The
registry below is the single source for what exists, what it takes to enter,
and how it is reached.
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

#: Cross-workspace handoff. The Command Center opens an investigation, the
#: Risk Center opens one from an alert, and the analyst answers with a finding
#: — all of them write here and switch workspace, so an investigation carries
#: its subject instead of restarting from a blank form.
SESSION_FOCUS = "rm_focus"


@dataclass(frozen=True, slots=True)
class Workspace:
    """One place a job gets done."""

    name: str
    file: str
    permission: str
    icon: str
    group: str
    purpose: str


#: The console's map. Navigation is declared here and nowhere else: Streamlit's
#: default file-based sidebar publishes every file to everyone, including a
#: visitor who has not signed in, who would be shown the whole shape of a
#: product they cannot open.
WORKSPACES: tuple[Workspace, ...] = (
    Workspace(
        "Command Center",
        "workspaces/1_Command_Center.py",
        "analytics.revenue.read",
        "◈",
        "Today",
        "What happened, what it means, and the single thing most worth doing about it.",
    ),
    Workspace(
        "AI Investigation",
        "workspaces/2_AI_Investigation.py",
        "insights.read",
        "◎",
        "Today",
        "Take a movement apart: where it landed, why it happened, how much is explained.",
    ),
    Workspace(
        "Decision Center",
        "workspaces/3_Decision_Center.py",
        "recommendations.read",
        "◆",
        "Today",
        "Every proposed action with what it is worth, what it risks, and a decision that persists.",
    ),
    Workspace(
        "AI Analyst",
        "workspaces/4_AI_Analyst.py",
        "insights.read",
        "◇",
        "Today",
        "Ask in English. See the plan, the query, the answer, and what it does not cover.",
    ),
    Workspace(
        "Sales Intelligence",
        "workspaces/5_Sales_Intelligence.py",
        "analytics.revenue.read",
        "▲",
        "Domains",
        "Revenue and margin, cut by the dimensions the metric registry governs.",
    ),
    Workspace(
        "Customer Intelligence",
        "workspaces/6_Customer_Intelligence.py",
        "analytics.customer.read",
        "●",
        "Domains",
        "Segments, lifetime value, retention cohorts, and who is drifting away.",
    ),
    Workspace(
        "Inventory Intelligence",
        "workspaces/7_Inventory_Intelligence.py",
        "analytics.inventory.read",
        "■",
        "Domains",
        "Availability, excess, ageing, and the supply behind both.",
    ),
    Workspace(
        "Store Intelligence",
        "workspaces/8_Store_Intelligence.py",
        "analytics.revenue.read",
        "▣",
        "Domains",
        "The estate as a league table, and the outliers worth a visit.",
    ),
    Workspace(
        "Forecast Intelligence",
        "workspaces/9_Forecast_Intelligence.py",
        "forecasts.read",
        "◐",
        "Domains",
        "What is expected, how wide the interval is, and whether the model has earned trust.",
    ),
    Workspace(
        "Risk Center",
        "workspaces/10_Risk_Center.py",
        "alerts.read",
        "▲",
        "Oversight",
        "Everything currently outside its expected band, and what detection deliberately withheld.",
    ),
    Workspace(
        "Executive Briefing",
        "workspaces/11_Executive_Briefing.py",
        "reports.read",
        "▤",
        "Oversight",
        "The morning read: summary, KPIs, risks, outlook, actions — exportable as it stands.",
    ),
    Workspace(
        "Admin",
        "workspaces/12_Admin.py",
        "admin.users",
        "⚙",
        "Oversight",
        "Identity, permissions, the metric vocabulary, and session control.",
    ),
    Workspace(
        "Data Sources",
        "workspaces/13_Data_Sources.py",
        "data.manage",
        "⇪",
        "Oversight",
        "Company profile, what data is connected, and what that unlocks.",
    ),
)

#: The same map read as "what does this take", used by the gate.
PAGE_PERMISSIONS: dict[str, str] = {item.name: item.permission for item in WORKSPACES}


# ── Configuration ────────────────────────────────────────────────────


def api_base_url() -> str:
    """Where the API lives: a secrets file if present, otherwise the environment.

    `st.secrets` *raises* when no secrets.toml exists rather than returning the
    default, so reading it unguarded breaks a fresh checkout on the first
    click — exactly the deployment least likely to have one.
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


# ── Identity ─────────────────────────────────────────────────────────


def user() -> dict[str, Any] | None:
    return st.session_state.get(SESSION_USER)


def is_signed_in() -> bool:
    return user() is not None and client().tokens is not None


def permissions() -> set[str]:
    profile = user() or {}
    return set(profile.get("permissions") or [])


def can(permission: str) -> bool:
    return permission in permissions()


def display_name() -> str:
    profile = user() or {}
    name = str(profile.get("display_name") or "").strip()
    return name or str(profile.get("email", "")).split("@")[0].title()


def primary_role() -> str:
    roles = (user() or {}).get("roles") or []
    return str(roles[0]).replace("_", " ").title() if roles else "No role"


def sign_in(email: str, password: str) -> bool:
    api = client()
    try:
        api.login(email, password)
        st.session_state[SESSION_USER] = api.me()
        st.session_state.pop(SESSION_ERROR, None)
        return True
    except ApiError as error:
        # The API's own message. Inventing "invalid credentials" would be wrong
        # for a locked account and wrong again for an unreachable backend, and
        # those need different responses from the person reading it.
        st.session_state[SESSION_ERROR] = str(error)
        return False


def sign_out() -> None:
    if SESSION_CLIENT in st.session_state:
        st.session_state[SESSION_CLIENT].logout()
    for key in (SESSION_CLIENT, SESSION_USER, SESSION_ERROR, SESSION_DATA_DATE, SESSION_FOCUS):
        st.session_state.pop(key, None)


# ── Data currency ────────────────────────────────────────────────────


def data_date() -> date:
    """The latest business date the warehouse holds — not today's clock date.

    Every date control defaults to this. The two are not the same: the
    warehouse lands a day behind at best, and in a demo environment it can be
    weeks behind. A period ending "today" then covers days with no data at all,
    and the platform dutifully reports a 100% collapse in revenue.
    """
    if SESSION_DATA_DATE not in st.session_state:
        resolved = date.today()
        with contextlib.suppress(ApiError, ValueError, TypeError):
            meta = client().get("/api/v1/analytics/revenue/summary").get("meta") or {}
            if meta.get("freshness"):
                resolved = date.fromisoformat(str(meta["freshness"])[:10])
        st.session_state[SESSION_DATA_DATE] = resolved
    return st.session_state[SESSION_DATA_DATE]  # type: ignore[no-any-return]


# ── Workspace handoff ────────────────────────────────────────────────


def set_focus(**payload: Any) -> None:
    """Hand a subject to another workspace before switching to it."""
    st.session_state[SESSION_FOCUS] = payload


def focus() -> dict[str, Any]:
    return dict(st.session_state.get(SESSION_FOCUS) or {})


def clear_focus() -> None:
    st.session_state.pop(SESSION_FOCUS, None)


def open_workspace(name: str, **payload: Any) -> None:
    """Carry a subject into another workspace and go there."""
    if payload:
        set_focus(**payload)
    target = next((item for item in WORKSPACES if item.name == name), None)
    if target is not None:
        st.switch_page(target.file)


# ── The gate ─────────────────────────────────────────────────────────


def visible_workspaces() -> list[Workspace]:
    return [item for item in WORKSPACES if can(item.permission)]


def visible_pages() -> list[str]:
    return [item.name for item in visible_workspaces()]


def require(permission: str | None = None) -> ApiClient:
    """Gate a workspace and draw the chrome around it.

    ``st.stop()`` rather than a redirect: Streamlit runs the whole script top
    to bottom, so returning early would still execute everything below.
    """
    if not is_signed_in():
        st.warning("Sign in to continue.")
        st.stop()

    chrome()

    if permission and not can(permission):
        st.error(
            f"Your role does not include this workspace (needs `{permission}`). "
            "Ask an administrator if you think that is wrong."
        )
        st.stop()
    return client()


def chrome() -> None:
    """Identity, data currency, and sign-out — on every workspace.

    Streamlit runs *only* the current page's script, so anything the entry
    point draws exists nowhere else. A sidebar built in `app.py` alone leaves
    every other workspace with no way to sign out.
    """
    from retailmind_ui.design import SEMANTIC, escape, html

    with st.sidebar:
        html(
            f"""
            <div class="rm-id">
                <div class="rm-id-avatar">{escape(display_name()[:1].upper())}</div>
                <div>
                    <div class="rm-id-name">{escape(display_name())}</div>
                    <div class="rm-id-role">{escape(primary_role())}</div>
                </div>
            </div>
            <style>
            .rm-id {{ display:flex; gap:0.6rem; align-items:center; margin-bottom:0.9rem; }}
            .rm-id-avatar {{
                width: 32px; height: 32px; border-radius: 9px; flex: none;
                display: grid; place-items: center; font-weight: 650; font-size: 0.85rem;
                background: linear-gradient(140deg, {SEMANTIC["accent"]}, {SEMANTIC["ai"]});
                color: #06080C;
            }}
            .rm-id-name {{ font-size: 0.875rem; font-weight: 620; }}
            .rm-id-role {{ font-size: 0.72rem; color: var(--rm-faint); }}
            </style>
            """
        )

        st.caption(f"Data through {data_date():%d %b %Y}")
        st.caption(
            f"{len(visible_workspaces())} of {len(WORKSPACES)} workspaces open to your role. "
            "Hidden ones are refused by the API too."
        )

        if st.button("Sign out", width="stretch", key="rm_sign_out"):
            sign_out()
            st.rerun()
