"""Command Center — the day, read in thirty seconds.

**This screen does not open with charts.** The first thing on it is a sentence
about what happened, then the money, then the single action most worth taking,
then what is on fire. A chart appears only after all of that, because a chart
is a question and this screen exists to answer one.

Every element leads somewhere: the movement opens an investigation carrying its
subject, an alert opens the risk register, the action opens the decision it
belongs to. A briefing you cannot act from is a newsletter.
"""

from typing import Any

import streamlit as st

from retailmind_ui import charts, design, session
from retailmind_ui import components as ui
from retailmind_ui.api import ApiError
from retailmind_ui.design import SEMANTIC, escape, html
from retailmind_ui.formatting import compact, number, signed_rate

design.configure("Command Center", icon="◈")
client = session.require("analytics.revenue.read")

# ── Load ─────────────────────────────────────────────────────────────

overview: dict[str, Any] = {}
recommendations: dict[str, Any] = {}
errors: list[str] = []

placeholder = st.empty()
with placeholder.container():
    ui.skeleton(rows=2, height="76px")

try:
    overview = client.get("/api/v1/dashboard/executive")
except ApiError as error:
    errors.append(str(error))

if session.can("recommendations.read"):
    try:
        recommendations = client.get("/api/v1/recommendations", limit=6)
    except ApiError:
        recommendations = {}

placeholder.empty()

if errors:
    ui.workspace_header("Command Center", eyebrow="Today")
    ui.failure(errors[0], what="The day's figures did not load")
    st.stop()

revenue = overview.get("revenue") or {}
cards = revenue.get("cards") or []
growth = (overview.get("growth") or {}).get("horizons") or []
alerts = (overview.get("alerts") or {}).get("alerts") or []
alert_counts = (overview.get("alerts") or {}).get("counts") or {}
actions = recommendations.get("recommendations") or []

headline: dict[str, Any] = next((card for card in cards if card.get("key") == "net_revenue"), {})
week: dict[str, Any] = next((row for row in growth if row.get("horizon") == "week"), {})


def _greeting() -> str:
    """Time of day from the reader's clock, not the warehouse's.

    The greeting is the one thing on this screen that is about the person
    rather than the business, and getting it from the data date would wish a
    European CEO good morning at 6pm.
    """
    from datetime import datetime

    hour = datetime.now().hour
    if hour < 12:
        return "Good morning"
    return "Good afternoon" if hour < 18 else "Good evening"


# ── The read ─────────────────────────────────────────────────────────

html(
    f"""
    <div class="rm-cc-head">
        <div class="rm-eyebrow">{escape(session.data_date().strftime("%A · %d %B %Y"))}</div>
        <h1>{escape(_greeting())}, {escape(session.display_name())}</h1>
    </div>
    <style>
    .rm-cc-head h1 {{ font-size: 2.1rem; margin: 0.2rem 0 1.2rem; letter-spacing: -0.03em; }}
    </style>
    """
)

lede, side = st.columns([2.1, 1])

with lede:
    direction = str(headline.get("direction", "flat"))
    change = headline.get("change_pct")
    ui.headline_card(
        eyebrow="Net revenue · latest business day",
        value=number(headline.get("value"), "currency"),
        delta=signed_rate(change) if direction != "flat" else "flat",
        direction=direction,
        caption=str(revenue.get("comparison_basis", "")),
        accent=SEMANTIC["positive"] if direction == "up" else "",
    )

    # The AI read of the day, assembled from figures the platform published —
    # never a model's own recollection of the business.
    movement = float(week.get("change_pct") or 0)
    trend_word = "ahead of" if movement >= 0 else "behind"
    summary_bullets = [
        f"Week to date {number(week.get('current_revenue'), 'currency')}, "
        f"{signed_rate(week.get('change_pct'))} against the prior week.",
    ]
    if alert_counts:
        summary_bullets.append(
            ", ".join(f"{count} {name}" for name, count in alert_counts.items())
            + " alert(s) currently outside band."
        )
    if actions:
        summary_bullets.append(
            f"{len(actions)} proposed actions worth "
            f"{number(recommendations.get('net_profit_opportunity'), 'currency')} "
            "net of overlap."
        )

    ui.ai_summary(
        f"Revenue is running {trend_word} the prior week by "
        f"{abs(movement):.1%}. "
        + (
            "The largest open exposure is listed below."
            if alerts
            else "Nothing is currently outside its expected band."
        ),
        title="Today's read",
        bullets=summary_bullets,
        footnote=(
            "Assembled from the figures on this screen. No number here was "
            "generated — each is quoted from the surface that owns it."
        ),
        live=True,
    )

    if st.button("Investigate this movement", type="primary", width="content"):
        session.open_workspace(
            "AI Investigation",
            metric="net_revenue",
            reason="Opened from the Command Center",
        )

with side:
    ui.stat_row(
        [
            {
                "label": row.get("horizon", ""),
                "value": compact(row.get("current_revenue")),
                "delta": signed_rate(row.get("change_pct")),
                "direction": "up" if float(row.get("change_pct") or 0) >= 0 else "down",
                "note": f"vs prior {row.get('days')} days",
            }
            for row in growth
        ],
        columns=1,
    )

# ── Attention ────────────────────────────────────────────────────────

ui.section(
    "Needs attention",
    "Ranked by consequence, not by severity label — a warn on a large line "
    "outranks a critical on a small one.",
    accent=SEMANTIC["critical"],
)

if not alerts:
    ui.empty(
        "Nothing is outside its expected band on the latest business date.",
        what="No open alerts",
        icon="✓",
    )
else:
    for index, alert in enumerate(alerts[:4]):
        payload = {
            "payload": {
                "title": alert.get("metric_label", ""),
                "body": alert.get("narration") or "No narration was generated for this alert.",
                "observed": alert.get("observed"),
                "expected_low": alert.get("expected_low"),
                "expected_high": alert.get("expected_high"),
            },
            "severity": alert.get("severity"),
            "created_at": alert.get("detected_at"),
            "id": alert.get("id"),
        }
        ui.alert_card(
            payload,
            index=index,
            on_investigate=lambda item, alert=alert: session.open_workspace(
                "AI Investigation",
                metric=str(alert.get("metric_key", "net_revenue")),
                scope=alert.get("scope") or {},
                reason=f"Opened from alert · {alert.get('metric_label')}",
            ),
        )

# ── The one action ───────────────────────────────────────────────────

if actions:
    top = actions[0]
    ui.section(
        "Most valuable action available",
        "One, not a list. The rest are in the Decision Center.",
        accent=SEMANTIC["accent"],
    )
    ui.action_card(top, index=0, can_act=False, on_decide=None)
    if st.button("Open the decision queue", width="content"):
        session.open_workspace("Decision Center")

# ── Context, last ────────────────────────────────────────────────────

ui.section("Where revenue has been", "Context for the figures above, not the point of them.")

try:
    trend = client.get(
        "/api/v1/dashboard/revenue/trend",
        days=30,
    )
    series = trend.get("series") or []
except ApiError:
    series = []

figure = charts.trend(series, x="business_date", y="net_revenue", height=240)
if figure is not None:
    st.plotly_chart(figure, width="stretch", config={"displayModeBar": False})
    st.caption(
        "A line: dates have a natural order and equal spacing. "
        "The band beneath is a reading aid for direction, not a measure of area."
    )
else:
    ui.empty("The revenue trend endpoint returned no rows for the last 30 days.")

top_products = overview.get("top_products") or []
inventory_risk = overview.get("inventory_risk") or []

left, right = st.columns(2)
with left:
    ui.section("Top products", "By net revenue over the reporting window.")
    ui.table(top_products, empty_reason="No product sales in the window.", height=260)
with right:
    ui.section("Inventory at risk", "Category-region cells carrying availability risk.")
    ui.table(
        inventory_risk,
        empty_reason="No category-region is carrying material stockout risk.",
        height=260,
    )

withheld = overview.get("sections_unavailable") or []
if withheld:
    ui.caveats(
        [f"{name} — not included in your role" for name in withheld],
        title="Sections your role cannot see",
    )
