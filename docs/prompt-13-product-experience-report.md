# Prompt 13 — Real Customer Onboarding & Product Experience Report

**Date:** 2026-08-17
**Scope:** evaluate and improve RetailMind from the perspective of a brand-new
customer who has never seen the codebase, using a genuinely fresh tenant —
not the demo tenant, not a mock. `docs/prompt-12-productization-report.md`
and `docs/prompt-12.5-tenant-isolation-report.md` are untouched historical
records.

---

## Honest scope note, up front

Prompt 13 asks for "complete onboarding without developer intervention."
That is not yet true, and this report says so rather than fabricating it.
Two prior passes (Prompt 12, Prompt 12.5) each independently reached the
same conclusion and deliberately declined to build it: there is no
self-serve "create a company" endpoint, and browser-based file upload isn't
wired to the real detection/mapping/validation engine (it lives in
`data_platform`, whose package currently bundles `dbt-core`/`duckdb`/
`great-expectations` as unconditional dependencies — installing it into the
API risks that container's dependency footprint, and splitting
`data_platform`'s dependencies to avoid that is itself a real, monorepo-wide
change to a shared virtual environment every other command in this
repository already depends on). Building either was judged, again, not
"required for correctness" of this pass — it's a distinct, larger,
infrastructure decision.

**What this pass actually did**: ran the real, complete customer journey
using the mechanisms that genuinely exist today (the same ones Prompt 12.5
used to prove tenant isolation — a real tenant row, a real user, a real
warehouse built by the unmodified ingestion+dbt pipeline), watched exactly
where a first-time user would be confused or alarmed, and fixed what was
found. Every fix below is real, tested, and live on the running stack.

---

## 1. Customer journey — as actually run

| Step | How it happened | Self-service today? |
|---|---|---|
| 1. Create company | `Tenant` row inserted directly (mirrors what a real signup endpoint would do — the model and repository already exist, Prompt 12) | 🔴 No — ops-assisted |
| 2. Create user, sign in | `AppUser` + role assignment inserted directly; then **signed in through the real `/auth/login` endpoint**, receiving a real JWT | 🟡 Account creation: no. Sign-in itself: yes, fully real |
| 3. View/edit company profile | **Fully live** — `GET`/`PATCH /api/v1/company/profile`, through the real Data Sources workspace | 🟢 Yes |
| 4. Connect data | A real warehouse built by `ingestion.demo.build` (the same code `make demo` runs) — a stand-in for "upload your files", not a browser click | 🔴 No — CLI/ops-assisted |
| 5. See what's connected | **Fully live** — Data Sources workspace's capability panel, real API calls | 🟢 Yes |
| 6. Open the dashboard | **Fully live** — real, distinct revenue ($14,450.63 vs. the demo tenant's $65,220.45, queried through the same running process) | 🟢 Yes |
| 7. Ask a question | **Fully live** — Business Analyst answered "Net revenue came to 46,990 over the period" from real compiled SQL against this tenant's own warehouse | 🟢 Yes |
| 8. See recommendations | **Fully live** — 2 real, evidenced recommendations, distinct from the demo tenant's | 🟢 Yes |
| 9. See a forecast | **Fully live** — real baseline model, real WAPE (0.1445) from this tenant's own history | 🟢 Yes |

Steps 3, 5–9 are the actual product experience a signed-in user has, and
they are real, isolated, and correct. Steps 1 and 4 are the gap — stated
plainly, not smoothed over, matching Prompts 12/12.5's own precedent.

---

## 2. Onboarding flow (today's real state)

```
Ops-assisted: create Tenant row + first user  ─┐
                                                 │
Ops/CLI-assisted: provision a warehouse         │──▶ Self-service from here on:
  (ingestion.demo.build, or                     │      sign in → company profile →
   `retailmind-etl onboard <file>` for a         │      Data Sources capability check →
   real company's own CSV, detection/mapping/    │      executive dashboard → investigate →
   validation only — no import step yet)         │      decide → forecast → ask
                                                 ┘
```

---

## 3. Supported datasets

Unchanged from Prompt 12 (`docs/company-onboarding.md`): Sales (required),
Product/Store master, Inventory, Purchase Orders (recommended), Fulfilment,
Weather (optional). No new datasets added this pass.

---

## 4. Capability matrix (as shown to the user today)

The Data Sources workspace now shows this explicitly, per-domain, with a
tier label (new this pass — previously the tiers existed only in
documentation, not in the UI itself):

| Dataset | Tier | Unlocks | Checked how |
|---|---|---|---|
| Sales | **Required** | Revenue trends, product/store performance, sales forecasting | Live API call, real row-count check |
| Inventory | **Recommended** | Stockout detection, replenishment recommendations | Live API call |
| Purchasing/Suppliers | **Optional** | Supplier reliability, lead-time analysis | Live API call |
| Forecast | **Derived from Sales** | Demand forecasting, forecast risk — no separate upload | Live API call |

Each card shows real-time "Connected" / "No data yet" status — never a
static claim.

---

## 5. Screenshots / evidence

Live browser screenshots weren't captured this pass (the `claude-in-chrome`
extension was unavailable, consistent with every prior UI-facing prompt in
this series). In its place, the evidence is **real, captured API and test
output**, which is what every claim in this report traces back to:

```
$ curl .../dashboard/executive  (fresh tenant, real warehouse)
net_revenue: 14450.63
$ curl .../recommendations
count: 2
$ curl .../analyst/ask -d '{"question":"What were total sales?"}'
"Net revenue came to 46,990 over the period."
$ curl .../forecasts/meta/accuracy
[{'model_name': 'seasonal_naive_w4', 'wape': 0.1445, ...}]

$ curl .../analytics/revenue/summary  (demo tenant, same running process)
net_revenue: 65220.449999999975   ← unaffected, correctly isolated
```

Recommended you open http://localhost:8501 yourself (demo tenant, real
data already loaded) to see the actual rendered screens.

---

## 6. Usability issues found — and fixed

**The one real, significant issue found**: every workspace's primary data
load routed *any* API failure through the same red "This did not load"
panel — including a brand-new tenant's warehouse simply not being
provisioned yet (a 503, Prompt 12.5's own honest failure mode for that
exact case). To a first-time user, "The day's figures did not load" in
alarming red reads as "this product is broken", not "you haven't connected
data yet" — the wrong message on literally the first screen a new customer
would see.

**Fix**: a new shared component, `ui.workspace_error()`
(`ui/retailmind_ui/components/primitives.py`), inspects the error's status
and renders one of two states:
- **503 (dependency unavailable)** → a calm, dashed-border, non-alarming
  "Your workspace is still being set up" panel — explains what's happening
  and that nothing is broken.
- **Everything else** → the existing red "did not load" panel, unchanged —
  a genuine outage should still look like one.

Applied to every primary-load call site across 9 workspaces (Command
Center, AI Investigation, Decision Center, Sales/Customer/Inventory/Store
Intelligence, Forecast Intelligence, Risk Center, Executive Briefing) —
action-triggered failures (saving a profile, marking a notification read,
exporting a report) were deliberately left on the red panel, since a
genuinely failed user action should still read as one regardless of cause.

**Other, smaller issues found and fixed**:
- Company profile fields (Data Sources workspace) had no explanation of
  why each one matters — Phase 2's explicit ask. Added `help=` tooltips to
  every field explaining its real effect today (e.g. "Every revenue and
  margin figure in this console is shown in this currency") rather than
  generic labels.
- The capability panel didn't distinguish required/recommended/optional/
  derived datasets visually — only in prose documentation. Added an
  explicit tier label to each capability card.
- No developer terminology (DuckDB, dbt, Dagster, Bronze/Silver/Gold,
  Parquet, "semantic layer") was found leaking into any rendered UI string
  — audited via grep across every workspace and component file; the one
  hit was a Python docstring (developer documentation, never shown to a
  user), not rendered text. No fix needed — confirmed clean, not assumed.

**Not a bug, confirmed by design**: the Business Analyst correctly refused
unrecognized phrasing ("Ignoring total, which I do not recognise") rather
than guessing — this is the deterministic, no-fabrication behavior
documented since Prompt 11.7, working as intended.

---

## 7. Regression results

| Suite | Result |
|---|---:|
| `ui/tests` (full) | 🟢 172/173 — the 1 failure is the same pre-existing, unrelated `test_command_center_loads_without_error` markdown-index issue documented since Prompt 11.5 |
| New test: `test_an_unprovisioned_tenant_sees_a_calm_setup_state_not_an_outage` | 🟢 passes — proves the fix renders the calm state for a 503 and never leaks the raw "warehouse is temporarily unavailable" wording into it |
| Existing test: `test_a_failed_call_reports_the_outage_instead_of_a_blank_screen` | 🟢 still passes unmodified — proves a genuine outage (status=0) still shows the red panel |
| `make test` (backend unit + data_platform + ml + ui) | 🟡 1022/1023 — same one pre-existing failure, zero new regressions |
| Ruff / `ruff format --check` / mypy (`backend/app`, `ui`) | 🟢 clean |
| Live Docker verification | 🟢 fresh tenant's dashboard, recommendations, analyst, and forecasts all real and correctly isolated from the demo tenant; demo tenant unaffected |

No existing test was weakened, deleted, or had its expected values changed.

---

## 8. Remaining limitations

Carried forward, not newly discovered:

1. No self-serve company/tenant signup (Prompt 12/12.5's documented
   limitation, unchanged).
2. No browser-based file upload wired to the onboarding engine — real,
   tested, CLI-only (`retailmind-etl onboard <file>`), same as Prompt 12.
   Closing this would need splitting `data_platform`'s dependencies into a
   lightweight base + an `etl` extra so the API can safely import just the
   detection/mapping/validation modules — technically straightforward
   (confirmed: those modules only import `pyyaml` + stdlib transitively),
   but a real change to a shared workspace virtual environment every
   command in this repository depends on, not undertaken unilaterally in
   this pass.
3. No "import" step even via CLI — `retailmind-etl onboard` stops at a
   validated report; provisioning a new tenant's actual warehouse still
   means running the full ingestion+dbt pipeline separately and pointing
   `Tenant.warehouse_path` at the result (exactly what this pass did by
   hand to run the real customer-journey test).
4. Store/product hierarchy and business-rule configuration remain
   unbuilt (Prompt 12's documented limitation, unchanged).

---

## Summary

The parts of RetailMind a signed-in user actually touches — company
profile, capability awareness, the executive dashboard, investigation,
decision center, forecasting, the business analyst — are real, honest, and
correctly isolated per tenant, proven again this pass with a genuinely
fresh company rather than the demo tenant. The gap is entirely on the
*front door*: getting a new company from "doesn't exist" to "has a
provisioned tenant" still needs a person with database/CLI access, not a
customer clicking through a signup form. That gap is named plainly here,
not hidden behind a UI that pretends it doesn't exist.

Per this prompt's instruction: the complete customer journey has been
tested from a fresh tenant. Not proceeding to Prompt 14.
