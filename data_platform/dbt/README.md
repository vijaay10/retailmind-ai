# RetailMind Warehouse

The dimensional layer: `raw.pos__sales` (written by the ingestion pipeline) →
a Kimball star → aggregation marts → the semantic views the application reads.

## Layers

```
raw.pos__sales                    ← ingestion pipeline (conformed, business-dated)
        │
   staging/  stg_pos__sales       ← view: derive net revenue, expose natural keys
        │
   marts/core/                    ← the star (gold)
        ├── dim_date              4-5-4 retail calendar, Type 0
        ├── dim_product           SCD2, from snap_product
        ├── dim_store             SCD2, from snap_store
        ├── dim_channel           Type 0
        └── fct_sales             order-line grain, incremental
        │
   marts/metrics/                 ← pre-aggregations ("materialized views")
        ├── mart_sales_daily      date × category × region × channel
        └── mart_kpi_daily        one wide row per business date
        │
   marts/semantic/  v_*           ← the only layer the application may read
```

## Running it

```bash
cp profiles.yml.example profiles.yml     # once
uv run dbt seed --profiles-dir .         # masters + channel map
uv run dbt snapshot --profiles-dir .     # SCD2 history
uv run dbt build --profiles-dir .        # models + all tests
```

Or from the repo root: `make warehouse`.

Requires data in `raw.pos__sales` — run the ingestion pipeline first
(`make etl-demo`).

## Decisions worth knowing

**Materialization.** dbt tables *are* the materialized views here. Neither
DuckDB nor Snowflake offers a maintained MV that fits these shapes, and a plain
view would re-scan the fact on every dashboard load. A table rebuilt by the
same DAG that publishes the fact gets the same freshness with the advantage of
being tested and version-controlled — which no engine MV is.

**Indexes are never unique.** Analytical stores declare constraints
informationally and enforce them in the test layer (DB design §11). A unique
index would also fight `delete+insert` on incremental models, where the insert
can land before the delete. Grain is guaranteed by the `unique` and
`unique_combination` tests, which fail the build rather than the 2 a.m. load.

What indexes *do* earn their keep: equality lookups (one SKU, one store, one
order) — the shape of drill-down and evidence-link queries. Range scans on
dates are served by the columnar layout's zone maps, so indexing them would be
pure overhead. On Snowflake these become clustering keys instead, which is why
the choice lives in `macros/indexes.sql` rather than inline SQL.

**Threads = 1 on DuckDB.** It is an embedded single-writer engine: parallel
model builds race on the create/rename swap and fail with missing
`__dbt_backup` tables. The Snowflake profile raises this — concurrency belongs
where the engine actually separates compute from storage.

**As-was attribution is the default.** Facts join the dimension version valid
at transaction time, so a sale made while a SKU sat in Outerwear stays in
Outerwear after it is recategorized. Reporting *as-is* is a deliberate opt-in
through `v_dim_*_current`, never the accident of a naive `is_current` join.

**First SCD2 version is backdated to 1900.** `dbt_valid_from` records when a
snapshot first *observed* a row, not when the attribute became true. Without
backdating, every fact older than the first snapshot run fails the as-was
predicate and lands on the UNKNOWN member — the classic failure of a warehouse
built on top of history it did not watch accumulate.

**Surrogate keys are deterministic hashes, not sequences.** A rebuild
reproduces identical keys, which is what lets a backfill be byte-identical
(DB design §9). The macro shifts right one bit because DuckDB's `hash()`
returns UINT64, which overflows a signed BIGINT — and the shift also
guarantees non-negative keys, so a generated key can never collide with the
reserved `-1` (unknown) and `-2` (not applicable) members.

## Testing

`dbt build` runs 60 tests. Two kinds matter differently:

* **Generic tests** (uniqueness, not-null, referential integrity, ranges,
  accepted values) — the schema-level safety net, declared in `schema.yml`.
* **Singular tests** in `tests/` — the semantic ones. The most valuable is
  `assert_revenue_reconciles_*`: conservation-of-money checks catch join
  fanout, dedup overreach, and window slips, whole classes of bug that per-row
  validation structurally cannot see.

`data_platform/tests/unit/test_warehouse.py` adds 24 behavioural tests that
build the warehouse from generated CSVs and interrogate the result — fiscal
calendar against published NRF dates, as-was attribution surviving a dimension
change, ratios recomputed rather than summed.

Generic tests are defined locally in `macros/generic_tests.sql` rather than
pulled from dbt_utils: fifty lines of SQL is cheaper than a package dependency
that needs `dbt deps` and network on every fresh clone.

## Extending it

Adding a fact or dimension:

1. Declare the source in `models/staging/sources.yml`.
2. Add a staging model — conform only; no business math.
3. Add the dimension (with `scd2` macros if it has history) or the fact
   (stitching keys with `scd2_valid_at`).
4. Declare grain and relationships in `schema.yml` — an untested grain is an
   unenforced one.
5. Add a reconciliation test if the model carries money.
6. Expose it through a `v_*` semantic view; the application reads nothing else.
