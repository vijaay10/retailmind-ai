"""Alerts — the inbox, and what detection deliberately withheld."""

import streamlit as st

from retailmind_ui import components as ui
from retailmind_ui import session, theme
from retailmind_ui.api import ApiError

theme.configure("Alerts")
client = session.require("alerts.read")

ui.page_header(
    "Alerts",
    "Detection suppresses what it has already told you. The reasons are shown, not hidden.",
)

controls = st.columns([2, 2, 4])
unread_only = controls[0].toggle("Unread only", value=False)
run_sweep = controls[1].button("Run detection now", width="stretch")

if run_sweep:
    try:
        result = client.post("/api/v1/notifications/sweep")
    except ApiError as error:
        ui.error(str(error))
        result = {}

    if result:
        ui.kpi_row(
            [
                {"label": "Detected", "value": str(result.get("detected", 0))},
                {"label": "Notified", "value": str(result.get("notified", 0))},
                {
                    "label": "Suppressed",
                    "value": str(result.get("suppressed", 0)),
                    "help": "Already sent, muted, or capped. Reasons below.",
                },
                {"label": "Deliveries", "value": str(result.get("deliveries", 0))},
            ]
        )
        reasons = result.get("suppression_reasons") or {}
        if reasons:
            ui.caveats(
                [f"{count} × {reason}" for reason, count in reasons.items()],
                title="Why some alerts were not sent",
            )
        failed = result.get("detectors_failed") or {}
        if failed:
            ui.caveats(
                [f"{name}: {error}" for name, error in failed.items()],
                title="Detectors that failed (the rest still ran)",
            )
        st.caption(
            "Safe to run again — the sweep decides from what has already been "
            "sent, not from when it last ran."
        )

try:
    inbox = client.get("/api/v1/notifications", unread_only=unread_only, limit=50)
except ApiError as error:
    ui.error(str(error))
    st.stop()

items = inbox.get("notifications") or []
st.caption(f"{inbox.get('unread_count', 0)} unread")

if not items:
    ui.empty(
        "Either nothing has been detected, or everything current has already "
        "been sent and is inside its quiet window.",
        what="Nothing in your inbox",
    )
else:
    unread_ids = [item["id"] for item in items if not item.get("read")]
    if unread_ids and st.button(f"Mark {len(unread_ids)} as read"):
        try:
            client.post("/api/v1/notifications/read", notification_ids=unread_ids)
            st.rerun()
        except ApiError as error:
            ui.error(str(error))

    for item in items:
        ui.notification_card(item)
