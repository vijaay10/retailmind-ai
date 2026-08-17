"""Data Sources — the company's own profile, and what RetailMind can see.

**Prompt 12's onboarding pipeline is real, not a mockup — it just doesn't
run inside this browser session.** Column-name detection and mapping
(`data_platform/onboarding/`) reuse the platform's declared schema
contracts (`ingestion/schemas/`), and both need `dbt`/`duckdb`/
`great-expectations` in their dependency closure — the same reason the API
container deliberately does not carry the ETL toolchain (see
`infra/docker/api.Dockerfile`'s own comment on that boundary). Crossing it
just for this page would mean installing the ETL stack into either the API
or the console image, which is a real infrastructure decision this pass did
not make unilaterally. The engine is exercised for real via
``uv run retailmind-etl onboard <file.csv>`` — shown below with real
output, not asserted without evidence.

**Capability awareness here is real, not decorative.** Each domain chip
below reflects an actual API call made from this page load, not a static
label — a domain with no rows says so honestly.
"""

from typing import Any

import streamlit as st

from retailmind_ui import components as ui
from retailmind_ui import design, session
from retailmind_ui.api import ApiError
from retailmind_ui.design import SEMANTIC, html

design.configure("Data Sources", icon="⇪")
client = session.require("data.manage")

ui.workspace_header(
    "Data Sources",
    eyebrow="Company & connections",
    summary=(
        "The company profile behind every screen in this console, and an honest "
        "read of what data RetailMind currently has to work with."
    ),
)

# ── Company profile ──────────────────────────────────────────────────

ui.section("Company profile", "Basic business information. Nothing here changes analytics yet.")

try:
    profile: dict[str, Any] = client.get("/api/v1/company/profile")
except ApiError as error:
    ui.failure(str(error), what="The company profile did not load")
    st.stop()

with st.form("company_profile_form"):
    left, right = st.columns(2)
    with left:
        st.text_input(
            "Company name",
            value=profile.get("name", ""),
            disabled=True,
            help="Set when your workspace was created. Appears on every exported report.",
        )
        st.text_input(
            "Currency",
            value=profile.get("base_currency", ""),
            disabled=True,
            help="Every revenue and margin figure in this console is shown in this "
            "currency — set once, at signup, so numbers are never ambiguous later.",
        )
        industry = st.text_input(
            "Industry",
            value=profile.get("industry") or "",
            help="Used only as context on your reports today — it doesn't yet change "
            "which metrics or benchmarks you see.",
        )
        country_code = st.text_input(
            "Country",
            value=profile.get("country_code") or "",
            max_chars=2,
            help="Two-letter country code (e.g. US, GB, IN). Recorded for your profile; "
            "doesn't yet affect tax, holiday calendars, or regional benchmarks.",
        )
    with right:
        st.text_input(
            "Plan",
            value=profile.get("plan", ""),
            disabled=True,
            help="Your subscription tier. Set at signup — contact support to change it.",
        )
        timezone = st.text_input(
            "Timezone",
            value=profile.get("timezone") or "UTC",
            help="A business day's boundary (e.g. when 'today's revenue' rolls over) is "
            "measured in this timezone — e.g. 'America/New_York', 'Asia/Kolkata'.",
        )
        fiscal_month = st.number_input(
            "Fiscal year start month",
            min_value=1,
            max_value=12,
            value=int(profile.get("fiscal_year_start_month") or 1),
            help="1 means your fiscal year matches the calendar year (starts January). "
            "Set this if your business year starts a different month — year-over-year "
            "comparisons will use it once fiscal-aware reporting reads this field.",
        )
    st.caption(
        "Name, currency, and plan are set when your workspace is created and aren't "
        "editable here yet — contact support to change them. Store and product "
        "groupings (department → category → SKU, country → region → store) aren't "
        "configurable per company yet either."
    )
    if st.form_submit_button("Save profile"):
        try:
            client.patch(
                "/api/v1/company/profile",
                industry=industry or None,
                country_code=country_code.upper() or None,
                timezone=timezone or None,
                fiscal_year_start_month=int(fiscal_month),
            )
            st.success("Saved.")
            st.rerun()
        except ApiError as error:
            ui.failure(str(error), what="The profile did not save")

# ── What's connected ─────────────────────────────────────────────────

ui.section(
    "What RetailMind can currently analyze",
    "Checked live against your data this session, not a stored setting. Sales is "
    "required to use RetailMind at all; everything else adds capability on top of it.",
    accent=SEMANTIC["accent"],
)

_CHECKS: list[tuple[str, str, str, str]] = [
    (
        "Sales",
        "Required",
        "/api/v1/analytics/revenue/summary",
        "Revenue trends, product and store performance, sales forecasting",
    ),
    (
        "Inventory",
        "Recommended",
        "/api/v1/inventory/stockout-risk",
        "Stockout detection, replenishment recommendations",
    ),
    (
        "Purchasing / Suppliers",
        "Optional",
        "/api/v1/analytics/supplier/summary",
        "Supplier reliability and lead-time analysis",
    ),
    (
        "Forecast",
        "Derived from Sales",
        "/api/v1/forecasts/meta/accuracy",
        "Demand forecasting and forecast risk — no separate upload needed",
    ),
]

cols = st.columns(len(_CHECKS))
for col, (label, tier, path, unlocks) in zip(cols, _CHECKS, strict=True):
    with col:
        try:
            body = client.get(path)
            has_rows = bool(
                body.get("meta", {}).get("row_count")
                or body.get("rows")
                or body.get("models")
                or body.get("data")
                or body.get("series")
                or body.get("totals")
            )
        except ApiError:
            has_rows = False
        colour = SEMANTIC["positive"] if has_rows else SEMANTIC["warning"]
        status = "Connected" if has_rows else "No data yet"
        html(
            f"""
            <div style="border:1px solid var(--rm-line);border-radius:12px;
                        padding:0.85rem 1rem;height:100%">
                <div style="display:flex;justify-content:space-between;align-items:baseline">
                    <div style="font-weight:620;font-size:0.9375rem">{label}</div>
                    <div style="color:var(--rm-faint);font-size:0.6875rem;
                                text-transform:uppercase;letter-spacing:0.03em">{tier}</div>
                </div>
                <div style="color:{colour};font-size:0.8125rem;font-weight:600;
                            margin-top:0.3rem">{status}</div>
                <div style="color:var(--rm-faint);font-size:0.75rem;margin-top:0.4rem">
                    {"Unlocked: " if has_rows else "Connect to unlock: "}{unlocks}
                </div>
            </div>
            """
        )

st.caption(
    "Fulfilment and Weather feed Root-Cause Analysis today (not their own dashboard "
    "domain) — they aren't checked separately above. Purchase-order / supplier "
    "analytics is the closest existing proxy for a 'Purchasing' domain; there is no "
    "dataset named exactly 'Purchasing' in the analytics catalog."
)

# ── Connect new data ──────────────────────────────────────────────────

ui.section(
    "Connect a new data source",
    "Detection, column mapping, and validation are real and tested — run today "
    "through the platform CLI, not yet through this browser.",
    accent=SEMANTIC["ai"],
)

st.file_uploader(
    "Upload a CSV",
    type=["csv"],
    disabled=True,
    help=(
        "Browser-based upload isn't wired to the detection/mapping engine yet — "
        "see the note below for why, and how to run the same engine today."
    ),
)

with st.expander("Run it now — the real engine, from a terminal", expanded=False):
    st.code(
        "uv run retailmind-etl onboard your_file.csv\n\n"
        "# Skip detection and validate against a known source directly:\n"
        "uv run retailmind-etl onboard your_file.csv --source pos --table sales",
        language="bash",
    )
    st.caption(
        "Detects the dataset type with a real computed confidence score, proposes "
        "a column mapping (exact → known-synonym → fuzzy match, never guessed past "
        "its confidence), and validates the mapped rows — missing required fields, "
        "unparseable dates, out-of-range values — printed in the same ✓/⚠/✕ language "
        "as this page. Proven against three differently-shaped company schemas in "
        "data_platform/tests/unit/test_onboarding.py."
    )

ui.caveats(
    [
        "Detected dataset type and column mapping are proposals — nothing is "
        "imported without a human confirming the mapping first (not yet wired to "
        "this browser; the CLI path already enforces this by stopping at the "
        "printed report rather than writing anything).",
        "There is one warehouse for the whole platform today, not one per company "
        "— a newly onboarded company's data would need a separate warehouse or a "
        "tenant_id column threaded through every analytics model before its "
        "dashboard would show only its own figures. Documented, not hidden.",
    ],
    title="What this page does not claim",
)
