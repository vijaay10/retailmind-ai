"""Risk Center — everything outside its band, and what detection withheld.

**Suppression is shown, not hidden.** A detection sweep that fires every hour
on an unchanged condition produces twenty-four identical emails a day, and the
recipient's response is a mail rule — after which the one alert that mattered
is filed with the rest. So the sweep suppresses duplicates, caps volume per
kind, and reports every reason. An operator asking "why didn't I hear about
this?" gets an answer.

**Running the sweep again is safe.** It decides from what has already been
sent, not from when it last ran.
"""

from typing import Any

import streamlit as st

from retailmind_ui import components as ui
from retailmind_ui import design, session
from retailmind_ui.api import ApiError
from retailmind_ui.design import SEMANTIC

design.configure("Risk Center", icon="▲")
client = session.require("alerts.read")

ui.workspace_header(
    "Risk Center",
    eyebrow="Detection",
    summary="Open exposures, the detection sweep, and everything it deliberately did not send.",
)

controls = st.columns([2, 2, 4])
unread_only = controls[0].toggle("Unread only", value=False)
sweep_now = controls[1].button("Run detection now", width="stretch", type="primary")

if sweep_now:
    with st.spinner("Sweeping six detectors…"):
        try:
            result: dict[str, Any] = client.post("/api/v1/notifications/sweep")
        except ApiError as error:
            ui.failure(str(error), what="The sweep did not complete")
            result = {}

    if result:
        ui.stat_row(
            [
                {"label": "Detected", "value": str(result.get("detected", 0))},
                {
                    "label": "Notified",
                    "value": str(result.get("notified", 0)),
                    "accent": SEMANTIC["positive"],
                },
                {
                    "label": "Suppressed",
                    "value": str(result.get("suppressed", 0)),
                    "note": "already sent, muted, or capped",
                },
                {"label": "Deliveries", "value": str(result.get("deliveries", 0))},
            ]
        )

        reasons = result.get("suppression_reasons") or {}
        if reasons:
            ui.caveats(
                [f"{count} × {reason}" for reason, count in reasons.items()],
                title="Why some alerts were not sent",
                tone=SEMANTIC["ai"],
            )

        failed = result.get("detectors_failed") or {}
        if failed:
            ui.caveats(
                [f"{name}: {error}" for name, error in failed.items()],
                title="Detectors that failed — the rest still ran",
            )

        by_kind = result.get("by_kind") or {}
        if by_kind:
            st.caption(
                "Notified by kind · "
                + " · ".join(f"{kind}: {count}" for kind, count in by_kind.items())
            )

        st.caption(
            "Safe to run again — the sweep decides from what has already been sent, "
            "not from when it last ran."
        )

# ── Inbox ────────────────────────────────────────────────────────────

try:
    inbox: dict[str, Any] = client.get("/api/v1/notifications", unread_only=unread_only, limit=50)
except ApiError as error:
    ui.workspace_error(error, what="The inbox did not load")
    st.stop()

items = inbox.get("notifications") or []

ui.section(
    f"Inbox · {inbox.get('unread_count', 0)} unread",
    "Newest first. Each carries the observed value and the band it left.",
    accent=SEMANTIC["critical"],
)

if not items:
    ui.empty(
        "Either nothing has been detected, or everything current has already been "
        "sent and is inside its quiet window.",
        what="Nothing in your inbox",
        icon="✓",
    )
else:
    unread_ids = [item["id"] for item in items if not item.get("read")]
    if unread_ids and st.button(f"Mark {len(unread_ids)} as read"):
        try:
            client.post("/api/v1/notifications/read", notification_ids=unread_ids)
            st.rerun()
        except ApiError as error:
            ui.failure(str(error), what="Marking as read failed")

    for index, item in enumerate(items):
        ui.alert_card(
            item,
            index=index,
            on_investigate=(
                lambda payload: session.open_workspace(
                    "AI Investigation",
                    metric="net_revenue",
                    reason=f"Opened from alert · {(payload.get('payload') or {}).get('title', '')}",
                )
            )
            if session.can("insights.read")
            else None,
        )

# ── Standing exposures ───────────────────────────────────────────────

if session.can("analytics.inventory.read"):
    ui.section(
        "Standing exposures",
        "Conditions that are not alerts because they are not new — but are still true.",
    )
    try:
        supplier = client.get("/api/v1/inventory/supplier-risk")
        stockout = client.get("/api/v1/inventory/stockout-risk", limit=10)
    except ApiError:
        supplier, stockout = {}, {}

    left, right = st.columns(2)
    with left:
        st.caption("Suppliers below the on-time threshold")
        ui.table(
            (supplier.get("data") or [])[:8],
            height=240,
            empty_reason="No supplier is below the threshold.",
        )
    with right:
        st.caption("Positions closest to running out")
        ui.table(
            (stockout.get("data") or [])[:8],
            height=240,
            empty_reason="Nothing is projected to run out inside its lead time.",
        )

st.caption(
    "Alert thresholds are bands around expected values, not targets. A metric "
    "inside its band is not necessarily healthy — it is behaving as the platform "
    "predicted it would."
)
