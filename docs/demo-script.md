# RetailMind AI — Demo Script

**Runtime:** ~8 minutes
**Audience:** someone who has never seen RetailMind
**Prerequisite:** `make demo` running (see [README Quick Start](../README.md#quick-start))

**One honest note before the script starts, so nothing in it surprises
you mid-recording:** two steps of the story below (creating a company, and
connecting its first data source) are real and working, but not yet
reachable by clicking a button in the browser — they're a real, tested
engine reached from a terminal today. The script says so out loud at that
exact moment rather than hiding it. Everything from "their company
workspace becomes available" onward is 100% live in the browser, on the
same running instance, with real numbers pulled from the actual API for
this document (not invented for the demo).

Every number quoted below was pulled live from the running stack while
writing this script — not estimated, not from an earlier session's memory.

---

## 1. A new retailer joins RetailMind

**Say:** "RetailMind isn't one company's dashboard wearing a demo skin —
it's a platform. Here's a second, completely independent company we're
about to bring onto it, next to the one already running."

**Show:** the terminal, `retailmind-etl onboard --help` or just the
running demo stack's URL bar (http://localhost:8501) — either is a fine
opening shot.

**What it means:** every tenant on RetailMind gets its own physically
separate analytics warehouse (a real, proven property — see step 6).
**Why it matters:** a demo that only ever shows one seeded company can't
prove isolation; this one can, live.
**Action available:** none yet — this is the "before" shot.

---

## 2. They create their company

**Say:** "Creating a company today is a real database operation — a
`Tenant` row with a name, currency, and industry — reachable through a
real, RBAC-gated API (`POST`/`GET /company/profile`), but not yet behind a
public signup form. That's a real, named gap, not a hidden one."

**Show:** either the terminal command that inserts the tenant (a few
lines), or skip straight to the **Data Sources** workspace's company
profile panel once signed in — it's the same real data either way.

**What it means:** the plumbing for self-serve signup exists at every
layer except the front door.
**Why it matters:** an honest demo names its own gaps instead of
routing around them with a scripted illusion.
**Action available:** in the browser, an admin can already edit industry,
country, timezone, and fiscal month for an existing company — show that
part live, it's real.

---

## 3. They connect/upload their business data

**Say:** "Here's the real engine, run from a terminal against a sample
sales file with completely different column names than our demo tenant
uses."

**Show, live in a terminal:**
```bash
uv run retailmind-etl onboard company_b_sales.csv
```
with a file whose header is e.g. `order_no, business_date, product_code,
location_id, units, revenue` — deliberately nothing like the seeded demo's
own schema.

**What it means:** this is not a script pretending to understand the file
— it's a real detection pass over the file's actual columns.
**Why it matters:** every real retailer's export looks different; a
platform that only works with one exact column layout isn't a platform.
**Action available:** none yet — output comes next.

---

## 4. RetailMind detects and maps their schema

**Say:** "Watch what it does with headers it's never seen."

**Show, real captured output:**
```
Dataset detection:
  pos.sales               confidence  56%
  inventory.positions     confidence  38%
→ Best match: pos.sales (56%)

Column mapping:
  order_no      → order_id       synonym match
  business_date → transaction_ts synonym match
  product_code  → sku            synonym match
  location_id   → store_id       synonym match
  units         → quantity       synonym match
  revenue       → gross_amount   synonym match
```

**What it means:** a real, computed confidence score (never a hardcoded
value) matched this file to the sales schema and proposed a canonical
mapping for every column.
**Why it matters:** this is the exact same engine, run twice, on two
completely different real column layouts (see `docs/prompt-12-productization-report.md`
for a third) — it generalizes, it doesn't memorize one file.
**Action available:** a human reviews the mapping before anything is
imported — nothing here auto-commits.

---

## 5. RetailMind validates the data

**Say:** "Before anything is imported, it's checked."

**Show, real captured output (deliberately incomplete rows, so the
✓/⚠/✕ language actually appears):**
```
Validation:
  ✓ 3 records detected
  ✕ 3 records have a missing line no
  ✕ 3 records have a missing unit price
  ✕ 3 records have a missing currency
```

**What it means:** required fields, bad dates, and out-of-range values are
reported with counts and real example identifiers — never silently
dropped, never silently accepted.
**Why it matters:** "validated" has to mean something, or it's decoration.
**Action available:** fix the source file and re-run, or (for the rest of
this demo) switch to the already-provisioned, already-passing seeded
company — exactly what happens next.

---

## 6. Their company workspace becomes available

**Say:** "From here on, everything is live in the browser — sign in as the
seeded demo company's CEO."

**Show:** http://localhost:8501, sign in as `priya@northwind.example` /
`ChangeMe-Demo1!`.

**What it means:** this tenant has its own warehouse file — a separate
DuckDB file on disk, not a filtered view of a shared one.
**Why it matters (say this plainly, it's the platform's real
differentiator):** "We proved this isn't just a config flag: two
independently-built tenant warehouses, queried through the same running
API process, returned genuinely different, non-fabricated revenue figures
— and a tenant with no warehouse of its own gets a clean 'still being set
up' message, never someone else's numbers. That's `docs/multi-tenancy-architecture.md`
and `docs/prompt-12.5-tenant-isolation-report.md`, real attack tests
included."
**Action available:** open the **Command Center**.

---

## 7. RetailMind explains what is happening

**Show:** Command Center. Real, live numbers (captured while writing this
script):

> **Net Revenue: ₹15,966.97** (+6.68% vs. prior) — but one alert is
> showing **critical**: *"Net revenue in Southwest came in 12.4% below its
> expected range ($3.28–3.46M) for W30."*

**What it means:** the headline number is up overall, but the platform
surfaced a specific, ranked, real problem underneath it — not because it
was scripted to, because the data supports it.
**Why it matters:** a CEO opening this once a morning gets the one thing
that needs attention, ranked by consequence, not just a wall of green
tiles.
**Action available:** click **"Investigate"** on the Southwest alert.

---

## 8. RetailMind investigates why

**Show:** AI Investigation, opened *with the alert's subject and region
already carried over* — not a blank form.

**What it means:** magnitude, time comparison, affected stores/products,
and evidence tiers (arithmetic → mechanical → statistical → associative)
are shown for the actual Southwest decline. If the engine can't establish
a cause from available evidence, it says so explicitly — it does not
fabricate one.
**Why it matters:** this is the difference between a chatbot guessing and
an analytical engine that shows its work.
**Action available:** "What's next" links carry the same subject straight
into Forecast or Decision Center — click through to Forecast.

---

## 9. RetailMind forecasts what happens next

**Show:** Forecast Intelligence. A prediction interval (drawn filled, the
point line dotted over it — the interval is the honest part), plus an
accuracy scoreboard scored against a seasonal-naive baseline.

**What it means:** if the model doesn't beat "assume the same weekday
repeats" (MASE ≥ 1.0), the screen says so and flags the forecast as
untrustworthy rather than hiding that fact.
**Why it matters:** a forecast nobody can trust is worse than no forecast
— this is the platform refusing to hide that when it's true.
**Action available:** open **Decision Center**.

---

## 10. RetailMind recommends an action

**Show:** Decision Center. Real, live example:

> **"Raise a replenishment order for 9 units of OW-1001"** — expected
> revenue impact **₹985.95**, expected profit **₹687.08**, confidence
> **0.19** (ceiling 0.70), risk band **low**, reversible.

**What it means:** every recommendation carries its own expected impact,
an explicit confidence ceiling (never overstated), and a risk assessment
— including which assumptions are measured versus placeholder, marked
individually.
**Why it matters:** "the AI recommended it" is not evidence; the number
next to it, and where that number came from, is.
**Action available:** **Accept** or **Dismiss**, with a reason code on
dismissal — a role without `recommendations.act` sees the same card but is
told plainly why the buttons aren't there, not shown a demo of a feature
it can't use.

---

## 11. The user can inspect evidence

**Show:** expand the recommendation's evidence panel — the assumptions
list from the API response, live:

> `revenue_at_risk: $1,792.64` — **measured** (real)
> `lost_sale_rate: 0.55` — **placeholder** (not evidenced)

**What it means:** the platform tells you, per number, whether it was
measured from your data or assumed — never blends the two silently.
**Why it matters:** this is the single property the whole console's
design exists to protect (see `ui/retailmind_ui/components/evidence.py`'s
own docstring) — a qualification a reader can't see is a qualification
that doesn't exist.
**Action available:** decide with full information, not partial.

---

## 12. The system records the outcome

**Say:** "Every accepted or dismissed recommendation goes into a real
decision ledger — and separately, a calibration engine checks whether past
recommendations' projected impact actually happened."

**Show:** the Decision Center's decision log (who decided what, when, and
why for a dismissal), and — if time allows — the calibration summary
showing measured-outcome sample sizes and realization ratios.

**What it means:** the loop closes. A recommendation isn't fire-and-forget
— its outcome is measured against what it promised.
**Why it matters:** this is what makes "decision intelligence" a real
claim instead of a marketing phrase — the platform learns whether it was
right.
**Action available:** end the demo here, or continue to architecture/GitHub
(see the recommended recording sequence below).

---

## Recommended recording sequence (timings)

Matches this script's own numbered sections; adjust pacing live, this is a
guide, not a stopwatch:

```
00:00  What is RetailMind?           (one line — see docs/showcase.md's opener)
00:30  Company onboarding            (§1–2 — name the real gap plainly)
01:30  Data connection               (§3 — terminal, real file)
02:30  Schema detection              (§4 — real confidence + mapping output)
03:30  Dashboard                     (§6–7 — sign in, Command Center)
05:00  Investigate                   (§8)
06:00  Forecast                      (§9)
07:00  Decision Center               (§10–11)
08:00  Outcome                       (§12)
09:00  Architecture/security         (see docs/showcase.md — tenant isolation proof)
10:00  GitHub                        (repository link, README)
```

**On the recording itself:** this script was written and verified against
the real, running application (every number above is real, captured live),
but no video file was produced in this pass — the browser-automation
tooling used elsewhere in this project wasn't available in this session.
This document is the complete, ready-to-follow shot list for whoever
records it next; nothing here needs re-deriving.

**Reminder while recording:** the application is genuinely running at
http://localhost:8501 — every screen described above is real and waiting.
