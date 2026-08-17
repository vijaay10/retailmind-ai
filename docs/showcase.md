# RetailMind AI — Showcase

A retail decision-intelligence platform, built and verified end to end —
this document is the single-page pitch for anyone who lands here first:
recruiters, engineers, portfolio reviewers, and prospective users alike.

---

## Product description

RetailMind explains *why* a retail number moved, forecasts what's next,
and recommends what to do about it — grounded entirely in a company's own
data. Every figure on screen is computed by a deterministic analytical
engine over a governed semantic layer; an optional language model narrates
evidence that already exists, but never generates a business number
itself. It's built as one platform for many retail companies, not a
single-tenant demo: each company's analytics warehouse is a physically
separate file, proven by running two independently-built tenants side by
side and confirming neither can see the other's data (real attack tests,
not an architecture diagram's promise — see [Multi-Tenancy
Architecture](multi-tenancy-architecture.md)).

## Target customer

A mid-market retailer — multiple stores or a meaningful product catalog,
enough scale that "why did revenue move" isn't answerable by eyeballing a
spreadsheet, but without an in-house data team to build a custom warehouse
first. Someone who wants an answer that shows its work (evidence, not just
an assertion), and who would rather see "cause not established" than a
plausible-sounding guess.

## User journey

```
Sign in → Executive dashboard (what needs attention, ranked)
        → Investigate (why — with evidence, or an honest "not established")
        → Forecast (what's next, with an honest accuracy scoreboard)
        → Decision Center (ranked recommendations, accept/dismiss)
        → Outcome measurement (did it actually help?)
```

Full walkthrough with real, live-captured numbers: [Demo Script](demo-script.md).

## What data a company provides

| Tier | Datasets | Unlocks |
|---|---|---|
| **Required** | Sales | Revenue trends, product/store performance, sales forecasting |
| **Recommended** | Inventory, Purchase Orders | Stockout detection, replenishment recommendations, supplier analysis |
| **Optional** | Fulfilment, Weather | Delivery signal, weather-linked demand effects (via root-cause analysis) |

Nothing beyond Sales is required. A company that only connects Sales sees
sales-only capabilities — the platform says plainly what's unavailable
rather than showing an empty chart pretending it has data.

Column names don't need to match any fixed convention — a real detection
and mapping engine handles that (proven live against three genuinely
different company schemas; see [Company Onboarding](company-onboarding.md)).

## Screenshots

Not included in this document. The browser-automation tooling used
elsewhere in this project's development wasn't available in this session,
and this project doesn't ship a screenshot it hasn't actually looked at
(see [CLAUDE.md](../CLAUDE.md)'s standing rule against unverified claims).
The application is real and running — capture your own:

```bash
make demo
# http://localhost:8501 — priya@northwind.example / ChangeMe-Demo1!
```

The [Demo Script](demo-script.md) names the exact six screens worth
capturing, in order.

## Demo instructions

```bash
git clone <this repository>
cd retailmind-ai
make demo
```

- **UI:** http://localhost:8501
- **API docs:** http://localhost:8090/api/docs
- **Demo login:** `priya@northwind.example` / `ChangeMe-Demo1!` (CEO —
  full access; six more role-specific users share the password, see
  `backend/app/infrastructure/db/seeds/sample.py`)
- This is a published, intentional seed credential for a synthetic demo
  tenant — clearly labeled as such everywhere it appears, never a real
  secret.

Full detail, including a known local Docker-build gotcha and its
workaround: [README Quick Start](../README.md#quick-start).

## Architecture

```
Streamlit UI  →  FastAPI backend  →  Semantic layer (the ONLY path to the
(renders API      (Analytics, RCA,     warehouse — import-linter enforced)
 responses only)   Forecasting,               │
                    Recommendations,   ────────┴────────
                    NLQ/Analyst)       │                │
                                  Tenant A's file   Tenant B's file
                                  (DuckDB, real,    (DuckDB, real,
                                   physically        physically
                                   separate)         separate)
                                        ▲
                          Data platform: CSV → detection/mapping →
                          validation → ingestion → dbt (67 models) →
                          one warehouse file per tenant, orchestrated
                          by Dagster
```

Postgres (not pictured) holds OLTP state — users, roles, decisions — scoped
by `tenant_id` at the model level, a separate isolation boundary from the
warehouse above. Full diagram and detail: [Architecture](architecture.md),
[Multi-Tenancy Architecture](multi-tenancy-architecture.md).

## Security

- JWT authentication (RS256); RBAC with 6 roles enforced server-side on
  every request, not just hidden in the UI.
- Tenant isolation proven at two independent layers — OLTP `tenant_id`
  scoping and a physically separate warehouse file per tenant — with real
  cross-tenant attack tests, not just a diagram (see
  [Multi-Tenancy Architecture](multi-tenancy-architecture.md)).
- No secret of any kind exists in this repository or its git history —
  independently verified, not assumed (see the [Public Release
  Audit](prompt-14-public-release-audit.md)).
- Self-conducted security review: MEDIUM overall risk, 0 critical findings
  — see [Security Audit](security-audit.md) for the actual findings (a
  self-review, not third-party).

## Limitations

Stated plainly:

- **No self-serve company signup or browser-based file upload yet.**
  Creating a tenant and provisioning its warehouse is currently an
  operator step; the detection/mapping/validation engine underneath is
  real and works today via `retailmind-etl onboard <file>`. See
  [Company Onboarding](company-onboarding.md).
- **LLM narration defaults to a deterministic mock** — a real Anthropic
  key is opt-in; business numbers never come from the LLM regardless.
- **Backups are opt-in, not automatic**; **Prometheus alert rules
  evaluate but nothing currently receives their notifications** (no
  Alertmanager deployed).
- **One known, pre-existing test failure** (a UI test-harness fragility,
  not a product defect) — full current list: [Known Issues](known-issues.md).
- No screenshots or recorded video ship with this pass (see above) — the
  application itself is the evidence, and it's genuinely running.

## GitHub

Repository: *(add your repository URL here before publishing — this
document intentionally doesn't invent one)*. See the [README](../README.md)
for the full technical writeup, and `docs/prompt-15-github-release-preparation.md`
for what still needs a maintainer's action before the repository goes
public (a real security contact, a real CODEOWNERS handle, and a decision
about the changelog's history).

---

## Final status

# 🟡 SHOWCASE READY WITH LIMITATIONS

The end-to-end story this document promises is real: sign in, see a ranked
problem, investigate it with evidence (or an honest "not established"),
forecast against an honest accuracy bar, decide on a recommendation with
its assumptions individually marked evidenced-or-not, and measure whether
past decisions actually worked — all proven with real, live-captured
numbers from the actual running application, not fabricated for this
document. Multi-tenant isolation is proven with real attack tests, not
just described.

What keeps this from a clean 🟢: two real, named gaps. First, "a new
retailer joins and clicks through onboarding" is only true up to the point
of connecting data — creating a company and provisioning its warehouse is
a real, working engine reached from a terminal today, not yet a browser
form (the [Demo Script](demo-script.md) says so out loud rather than
routing around it). Second, no screenshots or recorded video were produced
in this pass — genuine tooling unavailability, not an oversight, and
clearly disclosed everywhere it matters rather than papered over with
placeholder images.

Neither gap is a fabrication risk — both are disclosed, both point to
exactly what's real underneath, and both are fixable by a human with
browser access following the scripts this pass produced. That is the
definition of "ready with limitations," not "not ready."
