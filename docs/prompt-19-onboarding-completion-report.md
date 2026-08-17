# Prompt 19 — Customer Onboarding Completion

Every number below came from an actual run on 2026-08-17, not from a plan.

## 1. Previous onboarding flow

```
upload CSV → detect dataset → map columns → validate → print report → STOP
```

Real and working, but terminal. `docs/company-onboarding.md` said so itself:
*"Nothing is imported by this command."*

## 2. Identified gap

Nothing carried a confirmed mapping into the pipeline. A new company could be
told their file was valid and still have no data in the platform. The ingestion
pipeline, the bronze lake, the warehouse and per-tenant isolation all existed —
the connection between "this file is good" and "run it" did not.

## 3. Implementation performed

**`data_platform/onboarding/importing.py`** (new, ~200 lines). It partitions
mapped rows by the schema's own `event_time_column`, writes one CSV per business
date into the tenant's inbox under the `{source}_{unit}_{YYYYMMDD}.csv` name
`CsvFileConnector` already discovers, and hands the window to
`IngestionPipeline`.

**Deliberately thin.** Conformance, rejects, the reject-rate threshold,
quarantine, the bronze landing, the manifest and the transactional load with its
conservation check are all *unchanged existing code*. Nothing was
re-implemented, so there is no second ingestion architecture to keep in sync.

**`retailmind-etl onboard --tenant <slug> --confirm-import`** — the
confirmation gate. Without the flag the command reports and exits, exactly as
before.

**No analytical or business logic was changed.** No dbt model, no metric, no
API, no schema.

## 4. New customer journey

```
upload → detect → map → validate → report
       → CONFIRM (--confirm-import)
       → tenant inbox → IngestionPipeline → bronze → tenant DuckDB (raw)
       → dbt build → dimensional + semantic layers → analytics
```

## 5. Supported datasets

Unchanged — no dataset was invented.

| Dataset | Status |
|---|---|
| Sales, Product master, Store master | **Required** |
| Inventory, Purchase orders | Recommended |
| Fulfilment, Weather | Optional |
| Returns, promotions, pricing, customer, targets | **Not supported — no schema exists** |

## 6. Import behaviour

| Case | Behaviour | Status |
|---|---|---|
| First import | Rows land, partitions created | 🟢 |
| Repeated import (identical file) | Checksums match the committed manifest; partitions skipped; **no duplication** | 🟢 |
| Repeated import (corrected file) | Covered dates are deleted and re-inserted in one transaction | 🟢 |
| Invalid rows | Rejected by the existing pipeline and reported | 🟢 |
| Partially valid file | Under the reject threshold: bad rows dropped, good rows land. Over it: partition **quarantined, not loaded** | 🟢 |
| Missing required dataset | Import of the datasets supplied still succeeds; dbt models depending on absent sources error — see §11 | 🟡 |
| Unsupported dataset | Detection reports no confident match and exits non-zero | 🟢 |
| Schema mismatch | Required columns unmapped → every row invalid → refused before writing | 🟢 |
| Tenant mismatch | Empty/whitespace/path-bearing slug refused; `--confirm-import` without `--tenant` exits 2 | 🟢 |
| Silent overwrite | Impossible: replacement is per business date and `rows_replaced` is reported | 🟢 |

The gate defers to the pipeline's own `reject_rate_threshold` rather than
inventing a stricter rule. Observed, with 5.3% bad rows against the 0.5%
default:

> *Fix these issues before importing — 5.3% of records have errors, above the
> 0.5% the pipeline accepts, so the whole batch would be quarantined rather
> than loaded.*

## 7. Duplicate handling — 🟢 PASS

Existing mechanism, reused, not replaced. Two layers:

1. **Checksum skip** — `etl.discover.unchanged … reason='checksums match committed manifest'`
2. **Partition replacement** — `DELETE … WHERE business_date … ; INSERT` in one transaction

Measured: the same file imported **three times** left **484 rows**, not 1,452.
The second run reports *"Already imported — this exact file is already in
place"* and exits 0.

A defect was found and fixed here: `ImportResult.succeeded` originally required
`rows_loaded > 0`, so the safest possible re-import exited 1 and looked broken.

## 8. Tenant isolation — 🟢 PASS (data layer)

Two tenants imported the identical file:

| | demo-retailer | rival-retail |
|---|---|---|
| Warehouse | `demo-retailer.duckdb` | `rival-retail.duckdb` |
| `raw.pos__sales` | 484 | 484 |
| Inbox / bronze | `tenants/demo-retailer/` | `tenants/rival-retail/` |

Byte-scan of tenant A's warehouse for `rival-retail`: **not present**.

The filename convention matches `semantic.tenancy.resolve_warehouse_path`
(`<root>/<slug>.duckdb`) exactly, so what the importer writes is what the API
reads. A test asserts that equality, because a divergence would present as
missing data rather than as a path bug.

Isolation is verified at the file and data layer, not by UI hiding.

## 9. Tests — 🟢 PASS

`data_platform/tests/unit/test_onboarding_import.py`, **15 new tests, all
passing**: partitioning by event time, undated rows, per-tenant path
separation, the read-side filename contract, unusable slugs, empty uploads,
refusal-writes-nothing, import reaching the warehouse, duplicate import,
corrected re-import, two-tenant isolation, quarantine over threshold, canonical
headers.

Two failed on first run. Both were **my test assumptions**, not product bugs: a
missing `.fetchall()`, and an assumption that a batch with one bad row in six
would load — it is correctly quarantined, since that is 16% against a 0.5%
threshold. The test now asserts the real behaviour rather than the behaviour I
expected.

Full regression: **1,037 passed, 1 failed** — up from 1,022 passed, and the
single failure is the pre-existing, documented `test_command_center_loads_without_error`.
ruff clean, mypy clean over 178 files, import-linter contract kept, docs
integrity check passed. **No test was weakened or skipped.**

## 10. End-to-end evidence

Scenario: *Demo Retailer*, 14 days, 3 stores, with the retailer's own column
names (`transaction_id`, `transaction_date`, `revenue`).

| Stage | Evidence |
|---|---|
| Upload | `sales.csv`, 526 rows |
| Detection | `pos.sales` at **86%** confidence |
| Mapping | `transaction_id → order_id`, `transaction_date → transaction_ts`, `revenue → gross_amount` (synonym matches) |
| Validation (as uploaded) | 94.7% valid — **refused**, above threshold |
| Validation (corrected) | 484 rows, **100% valid** → *"Ready to import"* |
| Confirm | `--tenant demo-retailer --confirm-import` |
| Import | **484 accepted, 0 rejected**, 14 partitions, 2026-07-08 → 2026-07-21 |
| Warehouse | `raw.pos__sales` = **484**, 14 distinct business dates |
| dbt | **PASS=116**, ERROR=13, SKIP=23 |
| Analytics | `fct_sales` 484 · `dim_product` 41 · `dim_store` 121 · `v_mart_sales_daily` 286 |
| Real output | 2026-07-21 revenue **8,249.56**, units 87 · 07-20 revenue **9,253.83**, units 100 |

The 13 dbt errors are **source-level tests against raw tables this tenant never
uploaded** (`fulfilment__deliveries`, `inventory__positions`, …) —
`Catalog Error: Table … does not exist`. A sales-only tenant gets sales
analytics; the rest wait for the rest of the data.

Forecasting and recommendations were **⚪ not tested end-to-end for this
tenant** — both consume marts that require deeper history and the inventory
feed. Not claimed as verified.

## 11. Remaining limitations

| Limitation | Status |
|---|---|
| Onboarding is CLI, not browser. The Data Sources workspace still does not perform the import — the engine lives in `data_platform`, which the API and UI containers deliberately do not install | 🟡 |
| Building the analytics layer after import is a separate `dbt build`, not automatic | 🟡 |
| A tenant missing optional sources gets dbt source-test errors (13 of 152) | 🟡 |
| Company creation is still an operator step, not self-serve | 🟡 |
| CSV only | 🟡 |
| Forecasting / recommendations from a freshly imported tenant | ⚪ Not tested |
| Returns, promotions, pricing, customer, targets | 🔴 No schema — unchanged |

---

## Answers

| # | Question | Answer |
|---|---|---|
| 1 | Can a new tenant upload supported CSV files? | 🟢 Yes — CLI |
| 2 | Can RetailMind detect/map them? | 🟢 Yes — 86% detection, synonym mapping verified |
| 3 | Can the user review validation? | 🟢 Yes — business-language report |
| 4 | Can the user explicitly confirm import? | 🟢 Yes — `--confirm-import`, default is report-only |
| 5 | Does validated data enter the existing pipeline? | 🟢 Yes — same `IngestionPipeline`, no second path |
| 6 | Does the data reach the warehouse? | 🟢 Yes — 484 rows in the tenant's own DuckDB |
| 7 | Can analytics consume it? | 🟢 Yes — 116 dbt models, real revenue figures |
| 8 | Can forecasting consume it? | ⚪ Not tested for a newly imported tenant |
| 9 | Can recommendations consume it? | ⚪ Not tested for a newly imported tenant |
| 10 | Is tenant isolation verified? | 🟢 Yes — at the data layer, two tenants, identical file |
| 11 | Is duplicate import safe? | 🟢 Yes — three imports, 484 rows, not 1,452 |
| 12 | Usable without developer intervention? | 🟡 Usable by an **operator** with terminal access; not by a business user in a browser |

## Verdict

🟡 **CUSTOMER ONBOARDING COMPLETE WITH LIMITATIONS**

The gap this prompt targeted is closed: validated data now enters the pipeline,
reaches the tenant's warehouse, and becomes real analytics, with isolation and
duplicate safety verified by measurement.

It is amber, not green, because the journey is **operator-usable, not
self-serve**. The import runs from a terminal, the analytics build is a second
command, and two downstream consumers were not tested for a freshly onboarded
tenant. This is **not** "fully automated onboarding" and should not be
described as such.
