# Prompt 11.7 — Production User Experience & Real-World Data Productization

**Date:** 2026-08-16
**Scope:** the Streamlit console (`ui/retailmind_ui`, `ui/workspaces/*.py`) only.
No backend architecture change, no fabricated data, no invented backend
functionality. `docs/prompt-11-final-release-audit.md`,
`docs/prompt-11.5-remediation-report.md`, and `docs/prompt-11.6-live-run-report.md`
are untouched historical records.

---

## 1. Current UI problems (Phase 1 audit findings)

A structured audit of all 12 workspace files and the `retailmind_ui` package
(components, session, design, formatting) found the console considerably
further along toward "production tool" than a first manual look suggested.
Specifically **not found**, anywhere in production code paths: hardcoded
persona names, hardcoded dates, hardcoded/static KPI values, or hardcoded
sample recommendations. The one dataset the console does depend on
(`backend/app/infrastructure/db/seeds/sample.py`) is real, seeded, and
already labeled as such rather than presented as live.

Two real problems were found and are the actual scope of this pass:

1. **Wording that implied fabrication where there was none.** The AI-summary
   card (`ui/retailmind_ui/components/cards.py`) tagged its narrative panels
   `"generated"` — accurate about the sentence, misleading about the number
   sitting next to it, since the number is never the model's. This is
   exactly the confusion Phase 5 describes.
2. **No explicit "this is not live" signal.** The Command Center header
   showed a bare date with no framing — accurate, but not self-explanatory
   to a reader who has never been told this is a batch warehouse, not a
   live feed.

Everything else the prompt asks to audit for — demo copy, placeholder text,
seeded-persona assumptions, static recommendations, fake labels — came back
clean. Full inventory (file:line references, freshness-field survey,
recommendation-lifecycle survey, RBAC survey) was produced during the audit
and is summarized in the sections below rather than repeated in full here.

---

## 2. Production UX changes made

| Change | File | Why |
|---|---|---|
| AI-narrative tag: `"generated"` → `"explained"` | `ui/retailmind_ui/components/cards.py` | The number above the tag is observed/computed, never the model's; only the sentence is. "Explained" says that; "generated" didn't. |
| Header reframed: `"<date>"` → `"Latest business data · <date>"` + caption `"Updated daily from the warehouse pipeline — not a live feed."` | `ui/workspaces/1_Command_Center.py` | Phase 2/15's explicit requirement: never let a historical batch date sit next to a real-time-feeling greeting without saying so. |
| New `data_health()` component: lists the real source feeds (POS, Inventory, Purchasing, Fulfilment, Weather, Forecast) against the one real freshness date, with an explicit note that the platform tracks one warehouse-wide refresh, not six independent per-source clocks | `ui/retailmind_ui/components/primitives.py` (new function), wired into `ui/workspaces/1_Command_Center.py` | Phase 3/4's "Data Health" requirement — implemented honestly rather than as a fabricated 6-row timestamp grid (see §3). |
| Top-opportunity section reworded to name the real count and total value (`"Highest-value action from N opportunities worth $X net"`) instead of a generic one-liner; button relabeled `"View all opportunities"` | `ui/workspaces/1_Command_Center.py` | Makes the number the headline does the work Phase 3's "Top Decisions" section asks for, using data already on the page. |

Everything else audited — Decision Center's lifecycle language, the
Investigation → Forecast/Decision-Center handoff, the Analyst's
plan-before-answer framing, RBAC gating, and the empty/error-state library —
was already compliant with this prompt's requirements (see §5–§9). No
changes were made to files that were already correct, per this prompt's own
"do not remove working functionality merely for visual changes" instruction.

---

## 3. Data freshness behavior

**What is actually available**, confirmed against the schemas and a running
API, not assumed:

- Every analytics/dashboard/forecast response carries `meta.freshness` (a
  **date**, `backend/app/schemas/analytics.py:26`, `backend/app/schemas/dashboard.py:40`)
  and `meta.data_snapshot_id`. This is one warehouse-wide batch date — the
  same value regardless of which domain endpoint is called — because the
  platform runs one daily dbt build (`daily_dbt_schedule`, 3am UTC), not a
  pipeline per source.
- A genuine per-run **timestamp** (`data_snapshot.published_at`) exists in
  Postgres (`backend/app/infrastructure/db/models/platform.py`) but is not
  exposed by any API endpoint today — only used internally. Exposing it
  would be new backend surface, which this prompt's scope excludes
  ("do not redesign the backend architecture").
- There is **no per-source** (POS vs. Inventory vs. Purchasing vs.
  Fulfilment vs. Weather) freshness signal anywhere in the system — one
  batch, one date, six sources feeding it.

**What was built:** `session.data_date()` (pre-existing, unchanged) already
resolves this one real date from a real API call and caches it per session
— this is the single source of truth every date control on every workspace
already defaults to. The new `data_health()` component surfaces it
explicitly on the Command Center, alongside the six real source names, with
an honest note that they share one refresh rather than six independent
ones. **No fake per-source timestamp was added.** Where Phase 3's suggested
structure asked for six distinct "last update" times, this implementation
gives the true shape of the answer instead: one real date, stated plainly,
rather than a fabricated grid that would look more precise than the
platform actually is.

Refresh-model honesty (Phase 15): the pipeline is **daily batch**, confirmed
via `daily_dbt_schedule` in the Dagster definitions (verified running,
Prompt 11.6). The UI says "Updated daily" — never "live," never
real-time-implying language — everywhere freshness is shown.

---

## 4. Real vs. demo data handling

The console holds no business logic and computes no figures — audited and
confirmed: every number in every workspace is read from an API response
field, never computed, interpolated, or invented client-side. The dataset
behind it is the seeded Northwind Threads demo tenant (real Postgres +
DuckDB rows, not fixtures), which is exactly what Prompt 11.6 verified live.
The console does not — and structurally cannot, given how thin it is —
pretend this is a different or larger dataset than it is. Where the
platform has no data for a query (e.g., no published forecast, an empty
decision ledger), the console shows the platform's own honest empty-state
message rather than a fallback value (pre-existing behavior, confirmed
still true — see §6).

---

## 5. Role-aware behavior (Phase 10)

Already fully real, not cosmetic — confirmed by reading `ui/retailmind_ui/session.py`:

- `WORKSPACES` maps every one of the 12 workspaces to a required permission.
- `visible_workspaces()` filters the sidebar to only what the authenticated
  role can open — a role with 5 of 12 permissions sees 5 sidebar entries,
  not 12 with 7 disabled.
- `require(permission)` calls `st.stop()` with an explicit message naming
  the missing permission if a workspace is reached directly — not just a
  hidden nav item, an enforced stop.
- The sidebar states this plainly: *"N of 12 workspaces open to your role.
  Hidden ones are refused by the API too."* — telling the reader the UI gate
  and the API gate are the same boundary, not a decorative one.
- Decision Center separately distinguishes `recommendations.read` (can see
  proposals) from `recommendations.act` (can accept/dismiss them), with an
  explicit caption for read-only roles: *"Your role can read this proposal
  but not act on it — acting needs `recommendations.act`. The API enforces
  that regardless of what is shown here."*

No changes were needed. This already satisfies "the user should not feel
that the application is showing them a demo of features they cannot
actually use."

---

## 6. Decision workflow (Phase 6)

Audited the real backend contract before touching anything:
`RecommendationStatus` (`backend/app/infrastructure/db/models/enums.py`) is
`PROPOSED / ACCEPTED / DISMISSED / EXPIRED` — there is no `DEFERRED`,
`EXECUTED`, or `OUTCOME_MEASURED` per-recommendation state, and
`DecisionRequest.action` accepts only `"accepted"` or `"dismissed"`. The
`/recommendations/decisions` endpoint's own docstring states plainly: *"What
this endpoint does not do is execute anything. No purchase order is raised,
no price changes."*

The existing Decision Center UI (`ui/retailmind_ui/components/cards.py`,
`action_card`) already matches this exactly:
- Only **Accept** and **Dismiss** buttons exist — no fabricated Approve/
  Reject/Defer/Execute controls.
- A decided item shows the real recorded action, who/when
  (`relative_time(decision.get("decided_at"))`), and the dismissal reason
  code — not an invented lifecycle stage.
- Every decision surface carries the caption *"Recording a decision does
  not execute it. No purchase order is raised..."* — the exact honest
  "action execution not configured" framing Phase 6 asks for, already
  present.

**No changes made** — this was already correct, and per this prompt's own
instruction ("do not invent backend functionality"), inventing the
prompt's suggested seven-state lifecycle would have been a regression, not
an improvement.

---

## 7. Investigation workflow (Phase 7)

Confirmed real context-carrying, not a generic navigation:
`session.open_workspace("AI Investigation", metric=..., scope=..., reason=...)`
is called from the Command Center's headline movement button and from every
alert card, passing the actual subject into the investigation rather than
opening a blank form. `2_AI_Investigation.py` already renders magnitude,
time comparison, affected scope, evidence, and — critically — calls
`does_not_establish()` (`ui/retailmind_ui/components/evidence.py`) to state
plainly when the RCA service cannot establish a cause, instead of
fabricating one. A "What's next" section carries the same subject forward
into the Forecast and Decision Center workspaces. No changes were needed.

---

## 8. Forecasting (Phase 8)

Read `ui/workspaces/9_Forecast_Intelligence.py` in full. Already framed
operationally: stat row leads with **Horizon**, **Total forecast**, WAPE,
MASE (with the trust-signal framing Phase 8 asks for — "below 1.0 beats
assuming the same weekday repeats" — not "the model says"); a model that
fails its own accuracy bar is flagged with *"This model has not earned
trust"* rather than hidden; the empty state
(*"No forecast has been published for this target. The training job has
either not run or found the series too short to fit."*) matches the
prompt's own suggested wording almost verbatim. "Model" appears only in
trust/accuracy contexts, never as marketing. No changes made.

One honest limitation, not fixed: the API's forecast responses carry only
the single shared `meta.freshness` date (§3) — there is no distinct
"forecast run timestamp" field to show, so a separate "Forecast generated:
HH:MM" line was not added; it would either duplicate the same date shown
elsewhere or have to be fabricated. Not done.

---

## 9. NLQ / Analyst (Phase 9)

`ui/workspaces/4_AI_Analyst.py` opens with **"Not a chatbot"** as its own
framing, and shows question → interpretation/plan → compiled SQL (with
bound `?` placeholders, never interpolated) → answer → evidence — the exact
chain Phase 9 asks for. Starter questions are real, clickable, and captioned
with which engine they route to (e.g., "routes to root-cause analysis") —
not shown as pre-baked fake answers. Unrecognized vocabulary is refused
explicitly rather than guessed at (confirmed live in Prompt 11.6: "Which
stores performed best?" → 422 naming exactly which terms weren't
understood). No changes made.

---

## 10. Empty / loading / error states (Phase 11)

`ui/retailmind_ui/components/primitives.py` already provides `empty()`,
`failure()`, `skeleton()`, and `working()` as the shared vocabulary every
workspace uses — `empty()` requires a reason argument (no bare "nothing
here"), `failure()` surfaces the API's own `detail`/`hint` rather than a
generic message, and `skeleton()` is shaped to the content it's replacing.
Confirmed via the automated suite: `test_every_workspace_survives_an_api_that_answers_nothing`
runs all 12 workspaces against an API that returns empty bodies for
everything and asserts none raise — real, automated proof, not a claim. No
changes made; no blank pages or fabricated fallback values found anywhere.

---

## 11. Workspaces tested (Phase 16)

Live browser click-through was attempted twice this pass; the
`claude-in-chrome` extension reported disconnected both times. Per this
session's own guidance not to loop on a failing browser tool, this was not
retried a third time. In its place: the Streamlit `AppTest` harness (real
script execution against a real Streamlit runtime, not a mock of the UI
layer) plus live, authenticated HTTP requests against the actual running
API (rebuilt with today's changes) were used to verify every workspace.

| Workspace | AppTest result | Live API backing | Verdict |
|---|---|---|---|
| 1 — Command Center | `test_command_center_loads_without_error` — 🔴 fails, but confirmed **pre-existing and unrelated** (fails identically with this pass's changes fully reverted — see §12); covered by the all-workspaces empty-API sweep, which passes | `/dashboard/executive`, `/dashboard/recommendations` → 200 | 🟡 PASS (real defect is a pre-existing test-index fragility, not a functional break) |
| 2 — AI Investigation | `test_investigation_loads_without_error` → PASS | `/rca/investigate` → 200 | 🟢 PASS |
| 3 — Decision Center | `test_decision_center_loads_without_error` → PASS | `/recommendations`, `/recommendations/decisions` → 200 | 🟢 PASS |
| 4 — AI Analyst | `test_ai_analyst_loads_without_error` → PASS | `/analyst/ask` → 200 (verified live, Prompt 11.6) | 🟢 PASS |
| 5 — Sales Intelligence | covered by workspace sweep → PASS | `/analytics/revenue/summary` → 200 | 🟢 PASS |
| 6 — Customer Intelligence | covered by workspace sweep → PASS | `/customers/segments` → 200 | 🟢 PASS |
| 7 — Inventory Intelligence | covered by workspace sweep → PASS | `/inventory/stockout-risk` → 200 | 🟢 PASS |
| 8 — Store Intelligence | covered by workspace sweep → PASS | `/dashboard/stores/ranking` → 200 | 🟢 PASS |
| 9 — Forecast Intelligence | `test_forecast_loads_without_error` → PASS | `/forecasts/revenue` → 200, honest empty state | 🟡 EMPTY STATE (no published forecast — real, not a defect) |
| 10 — Risk Center | `test_risk_center_loads_without_error` → PASS | `/notifications` → 200 | 🟢 PASS |
| 11 — Executive Briefing | `test_executive_briefing_loads_without_error` → PASS | `/dashboard/executive`, `/reports` → 200 | 🟢 PASS |
| 12 — Admin | `test_admin_loads_without_error` → PASS | `/auth/permissions` → 200 | 🟢 PASS |

No workspace crashed, under either the automated harness or live API
verification. **Recommended:** open http://localhost:8501 yourself for the
actual visual/interaction confirmation this pass could not perform.

---

## 12. Regression test results (Phase 17)

| Suite | Result |
|---|---:|
| `ruff check` (backend, data_platform, ml, ui) | 🟢 clean |
| `ruff format --check` | 🟢 328 files already formatted |
| `mypy backend/app` | 🟢 clean, 173 files |
| `mypy ui` | 🟢 clean, 35 files |
| import-linter | 🟢 clean — 162 files, 270 dependencies, 1 kept / 0 broken |
| `make test` (backend unit + data_platform + ml + ui, no Docker) | 🟡 984 passed, 1 failed |
| `backend/tests/integration` (full, Docker) | 🟢 328/328 passed (849.75s) — unchanged from Prompt 11.5's baseline, as expected since this pass touched only `ui/` |
| `data_platform/tests/unit` | 🟢 121/121 |
| `data_platform/tests/unit/test_dagster_orchestration.py` | 🟢 22/22 |
| `ml/tests` | 🟢 66/66 |

**The one `make test` failure**
(`ui/tests/test_workspaces.py::test_command_center_loads_without_error`,
asserting the greeting is `app.markdown[0]`) was verified — not assumed —
to be pre-existing and unrelated to this pass: re-run with every file this
pass touched stashed out of the working tree, it fails identically
(`assert 'Good' in '<style>...'`). This is the same failure Prompt 11.5's
report already documented as "1 pre-existing, unrelated, documented." Not
touched, per this prompt's explicit "do not weaken tests, do not delete
tests, do not change expected values merely to make tests pass" — and
per CLAUDE.md's standing rule against making a test pass by weakening it.

No test was modified, deleted, or had its expected values changed by this
pass. `ui/retailmind_ui/components/primitives.py` gained one new function
(`data_health`) and no existing function's signature or behavior changed.

---

## 13. Remaining UX limitations

Stated plainly, not smoothed over:

1. **Per-source data freshness does not exist as a real signal.** The
   platform runs one warehouse-wide daily batch; there is no independent
   timestamp per POS/Inventory/Purchasing/Fulfilment/Weather source. The
   new `data_health()` component says this honestly rather than fabricating
   six clocks. Building real per-source freshness would require new backend
   work (exposing `data_snapshot.published_at` or per-source ingestion
   metadata), which is out of this prompt's scope.
2. **No distinct "forecast run timestamp."** Forecast responses carry the
   same shared warehouse date as everything else, not a separate model-run
   clock. Not fabricated; not added.
3. **The recommendation lifecycle is two states plus an audit trail**
   (proposed → accepted/dismissed, with who/when/why recorded), not the
   seven-state lifecycle this prompt's brief sketched. This is the real
   backend contract, confirmed from the model and the API's own docstring
   — implementing the fuller lifecycle would mean inventing backend
   functionality that does not exist, explicitly disallowed by this
   prompt.
4. **Live browser click-through was not performed this pass** — the
   `claude-in-chrome` extension was disconnected both times it was tried.
   Verification instead used the automated `AppTest` harness (real
   Streamlit execution) plus live authenticated API calls against the
   running stack. Recommend a manual look at http://localhost:8501 to
   confirm the visual result matches this report's description.
5. Carried forward from Prompt 11.5/11.6, not reopened here: confidence-band
   calibration returns empty (no numeric confidence persisted), the
   recommendation decision ledger isn't bridged to the outcome-measurement
   tables, and no forecast has been published against the demo warehouse.

---

## Summary

The audit found the console already substantially built toward the
production posture this prompt describes — real RBAC enforcement, honest
empty/error states, real context-carrying between investigation and
action, an already-correct two-state decision lifecycle, and an
already-correct "not a chatbot" framing for the analyst. The concrete gap
was narrower than the prompt's full 18-phase brief implied: one misleading
word ("generated" → "explained"), one missing "this is historical, not
live" signal (now explicit in the header), and no dedicated data-freshness
surface (now added, honestly, without fabricating per-source timestamps
the platform does not actually track). Every change is additive, grounded
in real API data, and verified against the running stack.

Per this prompt's instruction: stopping here. Not proceeding to Prompt 12.
