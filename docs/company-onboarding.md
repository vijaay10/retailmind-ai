# Company Onboarding — What RetailMind Actually Supports Today

This document describes the real, working state of multi-company
onboarding as of Prompt 12. It is written to the same standard as the rest
of this repository's documentation: if a capability isn't implemented, this
page says so rather than describing an aspiration as if it were shipped.

---

## The short version

RetailMind is one platform serving many retail companies (tenants). Each
company gets:

- Its own row in the `tenant` table, its own users, its own RBAC-scoped
  data at the application (OLTP) layer — **real and enforced today.**
- A real, tested engine that detects an uploaded file's dataset type,
  proposes a column-name mapping onto the platform's canonical schema, and
  validates the mapped data — **real and tested today, reachable via the
  ETL command line, not yet wired into the browser.**
- Its own analytics dashboard reading from its own data — **not real yet.**
  The warehouse today is one shared DuckDB file for the whole platform; see
  "The one real gap" below.

---

## Supported datasets

### Required to onboard at all

| Dataset | Canonical schema | Key fields |
|---|---|---|
| Sales | `ingestion/schemas/pos/sales.yml` | `order_id`, `line_no`, `sku`, `store_id`, `transaction_ts`, `quantity`, `gross_amount`, `unit_price`, `currency`, `channel` |
| Product master | `ingestion/schemas/product/master.yml` (new, Prompt 12) | `product_id`, `product_name`, `category`, `subcategory`, `brand` |
| Store master | `ingestion/schemas/store/master.yml` (new, Prompt 12) | `store_id`, `store_name`, `city`, `region`, `country` |

### Recommended

| Dataset | Canonical schema |
|---|---|
| Inventory | `ingestion/schemas/inventory/positions.yml` |
| Purchase orders | `ingestion/schemas/purchasing/orders.yml` |

### Optional

| Dataset | Canonical schema |
|---|---|
| Fulfilment | `ingestion/schemas/fulfilment/deliveries.yml` |
| Weather | `ingestion/schemas/weather/observations.yml` |

Returns, promotions, pricing, customer, and targets/budgets have **no
schema today** — onboarding one of these datasets isn't supported yet. Do
not tell a prospective customer otherwise.

**A note on field names:** this document's "canonical field" column names
(`order_id`, `sku`, `transaction_ts`, `gross_amount`, ...) are the
platform's real, existing, shipped contract — not the illustrative names
sketched in Prompt 12's brief (`transaction_id`, `product_id`,
`business_date`, `net_amount`). Renaming the real ETL pipeline's columns to
match a brief's illustrative examples would touch dbt models, quality
rules, and every downstream query that already depends on the current
names — out of proportion for an onboarding pass, and exactly the kind of
change this repository's own operating rules ask to be proposed and
approved separately, not folded into a feature commit.

---

## Supported upload formats

**CSV only, today.** The detection/mapping/validation engine
(`data_platform/onboarding/`) works on `list[dict]` rows and doesn't care
where they came from — XLSX/Parquet support is a parsing-layer addition
(swap `csv.DictReader` for `openpyxl`/`pyarrow`), not a redesign, but it
wasn't built this pass. Don't claim XLSX/Parquet support exists.

## How to onboard a file today

```bash
uv run retailmind-etl onboard your_file.csv

# Skip detection and validate directly against a known schema:
uv run retailmind-etl onboard your_file.csv --source pos --table sales
```

This prints a business-language report:

```
Dataset detection:
  pos.sales        confidence  86%
  inventory.positions    confidence  42%
  purchasing.orders       confidence  37%

→ Best match: pos.sales (86%)

Column mapping:
  transaction_id       → order_id             synonym match
  date                 → transaction_ts       synonym match
  sku                  → sku                  exact match
  ...

Validation:
  ✓ 3 records detected
  ✓ 100.0% valid records
```

(Real output, captured from an actual run against a synthetic file — see
`docs/prompt-12-productization-report.md` §12 for the full transcript,
including a run that correctly found and reported real defects.)

**Nothing is imported by this command.** It stops at the printed report.
Wiring the confirmed mapping into an actual ingestion run (landing →
staging → warehouse) for a *new* tenant is the one piece this pass didn't
build — see "The one real gap" below for why, and what it would take.

---

## Schema mapping

Detection and mapping (`data_platform/onboarding/detection.py`,
`mapping.py`) score an uploaded column against every declared canonical
schema using:

1. **Exact match** (case/punctuation-insensitive) — confidence 1.0.
2. **Synonym match** — a small table of common retail aliases (`sku` /
   `product_code` / `item_code` → the schema's product-identifier column,
   `qty` / `units` / `units_sold` → quantity, etc.) — confidence 0.9.
3. **Fuzzy match** — `difflib.SequenceMatcher` on the normalized names,
   accepted above a 0.72 similarity threshold — confidence equal to the
   ratio.
4. **No match** — the column is left unmapped rather than guessed at.

Dataset-type confidence is a weighted average across a schema's declared
columns: identity columns (business keys, event-time) and required columns
count more than optional descriptors, so a file matching only optional
fields never outscores one matching the columns that actually define the
grain. Full formula in `detection.py`'s docstring — it's inspectable, not a
black box.

**Nothing here guesses past its confidence.** An unmapped column stays
unmapped; a low-confidence dataset-type match is reported as low-confidence,
not silently accepted. A human reviewing the printed report decides what to
do with anything unmapped — there is no auto-apply path.

Proven against three differently-shaped company schemas
(`data_platform/tests/unit/test_onboarding.py`) — see the productization
report for the real confidence numbers each one scored.

---

## Validation

`data_platform/onboarding/validate.py` checks, on the *mapped* (renamed)
rows:

- Required columns aren't null (or one of the schema's declared sentinel
  null values).
- Date/timestamp columns parse and fall in a sane year range.
- Numeric (measure) columns are actually numeric, and within the schema's
  declared bounds when it has any.
- Business-key values aren't repeated (a warning, not an error — a
  repeated key legitimately happens under retry/replay, and the pipeline's
  real dedupe logic, not this report, decides what survives).

Output is the ✓/⚠/✕ business-language format, with a few example record
identifiers per issue — never the whole offending set, and never a silent
drop. Nothing is excluded or discarded by this command; it reports.

---

## Company configuration

`GET`/`PATCH /api/v1/company/profile` (gated by the `data.manage`
permission — already in the RBAC catalog, granted to the admin role, wired
to an endpoint for the first time this pass) — real, live, tested:

- Company name, plan, base currency (read-only here; set at tenant
  creation)
- Industry, country code, timezone, fiscal year start month
  (editable)

**Not built:** store/product hierarchy configuration
(department→category→subcategory, country→region→city→store). Nothing in
the analytics engine varies its hierarchy depth by company today — building
per-tenant-configurable dimension schemas is a materially larger change
than a profile field and wasn't attempted.

**Not built:** business-rule configuration (reorder thresholds,
alert thresholds, forecast horizon, risk tolerance). None of these are
tenant-configurable inputs to the forecasting/recommendation engines
today — they're constants in the service code. Exposing a settings screen
for values nothing reads would be exactly the "fake configuration switch"
Prompt 12 explicitly said not to build.

---

## Tenant isolation

**Real and verified at the application layer.** `TenantScopedMixin`
(`backend/app/infrastructure/db/models/base.py`) puts a non-null, indexed,
foreign-keyed `tenant_id` on every tenant-owned table. The authenticated
principal's `tenant_id` (from the JWT) is threaded through FastAPI's
dependency injection into every repository at construction time — a
handler cannot pass the wrong tenant because it never receives one to pass.
Proven end-to-end against a real, freshly created second tenant in
`backend/tests/integration/test_tenant_isolation.py`: a new company can be
created and signed into independently; its recommendation-outcome/
calibration data is reachable through its own token and invisible to the
existing demo tenant, and vice versa; an unauthenticated caller reaches
nothing.

**🟢 RESOLVED 2026-08-17 (Prompt 12.5): the analytics warehouse.** What
follows described a real gap between Prompt 12 (this section's original
writing) and Prompt 12.5 (the fix). Kept for the historical record rather
than deleted — the original text:

> There is exactly one DuckDB file for the entire platform
> (`RM_WAREHOUSE_DUCKDB_PATH`), with no `tenant_id` column in any dbt
> model. A brand-new tenant, with zero transactions of its own, still
> receives non-empty, live-computed recommendations and analytics from the
> demo tenant's shared warehouse.

Each tenant now has its own DuckDB file, resolved per-request from the
authenticated principal's tenant — physical isolation, not a filter, and no
dbt model changes were needed. Full detail:
`docs/multi-tenancy-architecture.md` and
`docs/prompt-12.5-tenant-isolation-report.md`. A tenant with no warehouse
provisioned gets an honest 503, never another tenant's data — proven in
`backend/tests/integration/test_tenant_isolation.py::test_an_unprovisioned_tenant_never_reads_the_demo_warehouse`
and `test_tenant_warehouse_isolation.py` (two real, distinctly-shaped
tenant warehouses, queried through the same running API, returning
independently correct figures).

**Still not built:** wiring a browser-driven "upload → detect → map →
validate → import" flow to actually *provision* a new tenant's warehouse
file (§ "Remaining limitations" below) — the isolation mechanism is real
and proven, but nothing yet calls it automatically when a real company
finishes onboarding through the UI.

---

## Progressive capabilities

The Data Sources workspace (`ui/workspaces/13_Data_Sources.py`) checks,
live, on every page load, whether Sales/Inventory/Purchasing/Forecast
analytics actually have rows behind them — real API calls, not a stored
flag — and shows "Connect to unlock: <capability>" for anything that
doesn't. This reflects the *demo tenant's* real data today (since the
warehouse is shared — see above); it does not yet reflect a genuinely
different state per company, because there is no per-company data for it
to reflect.

---

## Sample / demo data

The seeded Northwind Threads tenant is exactly that — a demo tenant,
created the same way any tenant is (a `Tenant` row + users), holding
synthetic data. Nothing in the tenant-creation path requires inheriting
its content: a new tenant created via `TenantRepository`/the seed pattern
starts with zero recommendations, zero alerts, zero seeded rows beyond
what its own onboarding adds. The shared-warehouse gap above means a new
tenant's *analytics* currently show the demo tenant's figures regardless —
stated plainly as the same limitation, not a separate one.

---

## Data freshness

Every analytics/dashboard/forecast response already carries `meta.freshness`
(a real, computed warehouse business-date) and `meta.data_snapshot_id`.
There is no per-source (POS vs. inventory vs. weather) freshness signal —
one batch, one date, every source. See `docs/prompt-11.7-production-ux-report.md`
§3 for the full accounting of what freshness metadata exists and why a
fabricated per-source timestamp grid was deliberately not built.

---

## Limitations, summarized

1. ~~The warehouse is not tenant-isolated.~~ **🟢 RESOLVED 2026-08-17,
   Prompt 12.5** — see the "Tenant isolation" section above.
2. No self-serve "create a company" UI flow — tenant creation is a
   repository-level operation today (used directly in tests), not exposed
   through an unauthenticated signup endpoint. Adding one is a real
   authentication-surface decision, not made unilaterally here.
3. Browser-based file upload isn't wired to the detection/mapping engine —
   it lives in `data_platform`, which depends on `dbt-core`/`duckdb`/
   `great-expectations` even for this lightweight use, and neither the API
   nor UI container installs that toolchain (a deliberate, pre-existing
   security boundary — see `infra/docker/api.Dockerfile`'s own comment).
   The engine is real and runs today via `retailmind-etl onboard`.
4. CSV only — no XLSX/Parquet parsing, no external connectors (Prompt 12
   explicitly said not to build these without an existing implementation).
5. No store/product hierarchy configuration, no business-rule
   configuration UI (nothing downstream reads either yet).
6. Returns, promotions, pricing, customer, and targets/budgets have no
   canonical schema.

## Future connector architecture

The connector interface (`ingestion/connectors/base.py`,
`ingestion/connectors/csv_files.py`) is already an abstraction — a new
connector (SFTP, a REST API pull, a cloud-storage watcher) implements the
same interface and can be added without touching `onboarding/`,
`quality/`, or the dbt layer, which all operate on `SourceSchema`-shaped
rows regardless of where they came from. No new connector was built this
pass, per Prompt 12's explicit instruction not to overbuild.
