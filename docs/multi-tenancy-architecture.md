# Multi-Tenancy Architecture

**Status:** the warehouse/analytics isolation gap Prompt 12 identified was
closed in Prompt 12.5 (2026-08-17). This document describes the resulting
architecture — what boundary exists, where, and why — plus what still isn't
built.

---

## The boundary, stated once

**A tenant's analytical data lives in a file no other tenant's request can
open.** Not a row filtered out by a `WHERE` clause — a file that is never
part of the query in the first place. This is the strongest available
isolation model for an embedded, single-writer analytical database like
DuckDB, and it is deliberately stronger than the alternative (a shared file
with a `tenant_id` column) that a forgotten filter could silently defeat.

Everything below is either this boundary, or something that already existed
above it (OLTP/RBAC) and was verified rather than rebuilt.

---

## Data flow, end to end

```
Authenticated user (JWT: tenant_id)
        │
        ▼
Principal (backend/app/domain/auth/entities.py)
        │
        ├──▶ OLTP repositories (Postgres) ── tenant_id column, injected at
        │     construction (TenantScopedMixin) — unchanged by this pass,
        │     already correct (Prompt 12 audit + tests)
        │
        └──▶ get_tenant_semantic_client (backend/app/api/deps.py)
                    │
                    ▼
             TenantRepository.get(principal.tenant_id)  — one Postgres read
                    │
                    ▼
             resolve_warehouse_path(tenant, WarehouseSettings)
                    │              (backend/app/infrastructure/semantic/tenancy.py)
                    ▼
             tenant.warehouse_path  OR  {RM_WAREHOUSE_ROOT}/{tenant.slug}.duckdb
                    │
                    ▼
             TenantWarehouseRegistry.client_for(tenant)  — cached per tenant id
                    │
                    ▼
             SemanticLayerClient(that_path)  — opens a fresh DuckDB
                    │                           connection PER QUERY (unchanged
                    │                           behavior, just a different path)
                    ▼
        AnalyticsService / AnalyticsRepository
                    │
        ┌───────────┼────────────┬─────────────┬──────────────┐
        ▼           ▼            ▼             ▼              ▼
   Dashboard   Recommendations Forecasting   RCA         Reports /
   (executive)   (live engine)   (all reads)  (analytics-  Notifications
                                                driven)
                    │
                    ▼
             API response (JSON)
                    │
                    ▼
             Streamlit console (HTTP only — no direct warehouse access,
             unchanged; the boundary was never here and still isn't)
```

**One dependency, one fix.** Every box in the fan-out under
`AnalyticsService` (dashboard, recommendations, forecasting, RCA, reports,
notifications, customer/inventory intelligence) constructs on top of
`AnalyticsServiceDep` and nothing else touches the warehouse — confirmed by
grepping for `SemanticLayerClient(` and `app.state.semantic_client` across
`backend/app/` before and after this pass. Fixing `get_tenant_semantic_client`
once fixed all of them. This is deliberate: the import-linter contract
already enforced "nothing outside the semantic client may query the
warehouse" (`SemanticLayerClient`'s own docstring, ARCH ADR-3); this pass
made the *client itself* tenant-aware rather than adding a check at every
call site.

---

## Architecture decision (Phase 2)

Three options were evaluated against the actual, current implementation:

| Option | What it means here | Verdict |
|---|---|---|
| **A. Shared warehouse + `tenant_id` column** | Add `tenant_id` to every fact/dim table, filter every dbt model and every semantic-layer query | **Rejected.** Requires threading a correct `WHERE tenant_id = ?` through 67+ dbt models and every hand-written query in `SemanticLayerClient._compile`, with zero tolerance for a missed filter — a single omission is a full cross-tenant breach. This is exactly the failure mode Prompt 12.5 names explicitly ("do not assume a tenant_id column automatically guarantees isolation"). Also the largest, riskiest change of the three. |
| **B. Separate schema per tenant, one DuckDB file** | `CREATE SCHEMA tenant_x`; dbt's `generate_schema_name` macro produces per-tenant schema names in one catalog | **Rejected, but the closest runner-up.** Real isolation (a query naming schema A's relation cannot see schema B's), still one file to operate/back up. Rejected mainly because it requires a custom dbt schema-generation macro and changes to how every model is *addressed* (the semantic layer's `_semantic_schema`/`_core_schema` become per-tenant strings resolved the same way `warehouse_path` now is) — a real but strictly larger change than Option C for an equivalent isolation guarantee, since DuckDB gives that guarantee for free at the file level already. |
| **C. Separate DuckDB file per tenant** (chosen) | Each tenant's entire warehouse — seeds, staging, marts, semantic views — lives in its own file, built by the exact same, unmodified `ingestion.demo.build` / dbt project | **Chosen.** Physical, not logical, isolation. Requires **zero dbt model changes** (Phase 5) — the existing 67+ models were not touched. Requires **zero new dimension columns** (Phase 4) — `product_id`/`store_id` collisions across tenants are structurally impossible, because two tenants' dimension tables are never in the same file to collide in. The entire fix is one dependency-resolution function (`resolve_warehouse_path`) plus a `Tenant.warehouse_path` column. |

**Operational tradeoffs of C, stated honestly:**
- Backup/restore is per-tenant-file, not per-database — simpler for a
  single tenant's data, more files to manage in aggregate. Not automated by
  this pass (no backup tooling exists for the demo warehouse today either —
  `docs/known-issues.md`).
- Provisioning a new tenant's warehouse means running the ingestion+dbt
  pipeline once per tenant, not once for the platform. Real cost, paid once
  at onboarding, not per query.
- Cross-tenant analytics (e.g., a platform-wide usage dashboard) are not
  possible without a separate aggregation step reading every tenant's file
  — not needed today, not built.
- DuckDB's per-query connection-open cost (already the existing design,
  documented in `SemanticLayerClient.execute`'s own docstring: "opens a
  read-only connection per call... connection setup is microseconds")
  applies per tenant file identically to how it applied to the one shared
  file before. No new performance concern introduced (Phase 16 below).

---

## Tenant → warehouse resolution

`backend/app/infrastructure/semantic/tenancy.py`:

```python
def resolve_warehouse_path(tenant: Tenant, settings: WarehouseSettings) -> str:
    if tenant.warehouse_path:
        return tenant.warehouse_path
    return str(Path(settings.root) / f"{tenant.slug}.duckdb")
```

- `Tenant.warehouse_path` (nullable, migration `0008_tenant_warehouse_isolation`)
  — an explicit override. The demo tenant (`northwind-threads`) carries one,
  backfilled at migration time from whatever `RM_WAREHOUSE_DUCKDB_PATH`
  resolves to in that environment — not a hardcoded path, so it stays
  correct across local dev, `make demo`, and prod without code changes.
- Every other tenant resolves deterministically from its own immutable
  `slug` (already "a URL-safe immutable business key" per the `Tenant`
  model's own pre-existing docstring — it was already the right handle for
  this).
- `TenantWarehouseRegistry` (`app.state.warehouse_registry`) caches one
  `SemanticLayerClient` per tenant id for the life of the process — avoids
  re-resolving the path and re-constructing the (cheap, but not free)
  client object on every request, and keeps each tenant's snapshot-id
  memoization (`SemanticLayerClient._snapshot_cache`) alive across requests,
  exactly as it worked for the single pre-Prompt-12.5 client.
- A tenant with no file at its resolved path gets the platform's existing,
  honest `DependencyError("warehouse", retryable=True)` → HTTP 503
  `"warehouse is temporarily unavailable"` — no new error path was needed;
  DuckDB's file-not-found already routed through this handler.

---

## DBT architecture

**Unchanged.** All 67+ models keep their existing SQL, existing tests,
existing schema. Tenant identity is never threaded through a dbt model
because it never needs to be — a model only ever sees one tenant's rows,
because it only ever runs against one tenant's file. "Cross-tenant leakage
tests" at the dbt level (Prompt 12.5, Phase 5) would test a condition that
cannot occur under this architecture (there is no second tenant's rows in
the same `dbt build` invocation to leak from) — the leakage test that
actually matters is the one built: two real, separately-built warehouses,
queried through the same running API, asserting the returned figures are
independently correct (`test_tenant_warehouse_isolation.py`).

---

## API / dependency-injection changes

| File | Change |
|---|---|
| `backend/app/main.py` | Replaced the single `app.state.semantic_client` (built once from one global path) with `app.state.warehouse_registry` (a `TenantWarehouseRegistry`) and `app.state.warehouse_settings`. |
| `backend/app/api/deps.py` | New `get_tenant_semantic_client` / `TenantSemanticClientDep` — resolves the caller's own tenant's client. `get_analytics_service` now depends on it instead of reading `app.state` directly. |
| `backend/app/api/deps.py` — `get_dashboard_service` | **A real, separate bug found and fixed**: this dependency read `request.app.state.semantic_client` *directly*, bypassing `AnalyticsServiceDep`'s resolution entirely — the exact "trusted at one call site, quietly skipped at another" failure Prompt 12.5 was written to catch. Now takes `TenantSemanticClientDep` like everything else. |
| `backend/app/workers/container.py` | **A second real, separate bug**: `build_analytics()` read `RM_WAREHOUSE_DUCKDB_PATH` from the OS environment unconditionally — the scheduled notification sweep for *every* tenant read the *same* file regardless of which tenant's sweep was running. Now takes the tenant object and calls the same `resolve_warehouse_path`. `build_notification_service` became `async` to look up the tenant row first. |

**Every other analytics-touching dependency** (customers, inventory,
forecasting, RCA, recommendations, reports, notifications) was already
composed on top of `AnalyticsServiceDep` and required no individual change
— confirmed by reading each one, not assumed.

---

## Cache isolation (Phase 9)

**Already correct before this pass — verified, not built.**
`AnalyticsCache.key()` (`backend/app/infrastructure/cache/redis_cache.py`)
was already `rm:{env}:sem:{tenant_id}:{snapshot_id}:{query_fingerprint}` —
tenant-scoped by construction, with the tenant id supplied by
`AnalyticsService`'s caller from `principal.tenant_id` at query time (not
from the now-fixed connection-resolution layer, a genuinely separate
mechanism). This meant that even before this pass, one tenant could never
be served *another tenant's cached response* — the bug was that a cache
miss fell through to a query against the wrong (shared) file, which then
got cached correctly per-tenant-key but containing the wrong content. Fixing
the connection layer fixes what gets cached; the cache layer itself needed
no change. No other Redis usage exists in the recommendation/forecasting/RCA
services — confirmed by grep; they all reach the warehouse exclusively
through `AnalyticsService`.

---

## ML / recommendations (Phase 10)

`RecommendationService` and `ForecastingService` both take exactly one
constructor dependency: `AnalyticsService`. There is no separate ML/
recommendation table or cache to isolate independently — "the live
analytical engine" *is* a read through the same tenant-resolved semantic
client everything else uses. (The separate, Prompt-11.5-documented
`Recommendation`/`RecommendationOutcome` Postgres tables — the batch-engine,
accept/dismiss ledger — were already tenant-scoped via `TenantScopedMixin`
and proven isolated in `test_tenant_isolation.py`; unrelated to this pass's
fix, unchanged by it.)

---

## UI (Phase 11)

**No Streamlit change was made for isolation, deliberately — there was
nothing to fix there.** The console has never held a database connection or
a `WHERE tenant_id` clause of its own; it talks to the API exclusively over
HTTP and renders whatever the API returns (verified repeatedly across
Prompts 11.6/11.7/12's audits). Once the API returns the correct tenant's
data, the console correctly displays it — proven by the same live Docker
verification used throughout this pass (the demo tenant's dashboard,
served through the now-tenant-resolved backend, is unchanged and correct).

---

## Threat model

**In scope, defended:**
- A tenant's JWT is used to query another tenant's analytics, dashboard,
  recommendations, forecasts, or semantic-layer data → fails closed (503 if
  unprovisioned) or returns only the caller's own data (if provisioned).
  Physically enforced — there is no code path from one tenant's `Principal`
  to another tenant's DuckDB file.
- A cache-poisoning attempt via shared Redis keys → keys are tenant-scoped;
  a collision would require an id collision, not a logic bug.
- Two tenants using the identical business id (`product_id`, `store_id`)
  for genuinely different entities → structurally safe; there is no shared
  table for the collision to occur in.

**Out of scope, not defended (stated plainly):**
- **Filesystem-level access control** — this design assumes the process
  running the API is the only thing that can read `.local/tenants/*.duckdb`
  files; it does not add OS-level file permissions beyond what already
  protects `.local/demo/retailmind.duckdb`. A compromised API process (or
  host) can still read every tenant's file — the boundary is "a correct
  request cannot reach the wrong file," not "the files are individually
  encrypted or access-controlled beyond the filesystem."
- **A malicious or buggy migration/backfill script** running with elevated
  privileges could still set any tenant's `warehouse_path` to any other
  tenant's file. `warehouse_path` is only writable via `TenantRepository`,
  which is only reachable through authenticated, RBAC-gated endpoints
  today (`/company/profile` does not expose this field) — no endpoint lets
  a tenant set its own `warehouse_path`, by design.
- **Resource exhaustion / noisy-neighbor** — one tenant's expensive query
  does not affect another's DuckDB connection (separate files, separate
  connections), but all tenants still share the same host's CPU/disk I/O.
  No per-tenant resource quota exists.

---

## Demo tenant vs. customer tenants

The demo tenant (`northwind-threads`) is a tenant like any other under this
architecture — its isolation is the same mechanism, not a special case in
code. It is special only in that its `warehouse_path` is an explicit
override (backfilled once, at migration time) rather than the per-slug
default, because its real data predates this column and lives at whatever
path `RM_WAREHOUSE_DUCKDB_PATH` already configured. Nothing about how it's
queried differs from a real customer tenant once resolution happens.

---

## Known limitations

1. **No self-serve tenant/warehouse provisioning.** `Tenant.warehouse_path`
   is set directly at the repository/database level in tests and would need
   to be set by a real onboarding flow once file upload → ingestion is
   wired end-to-end (`docs/company-onboarding.md`'s own documented next
   step). Nothing in this pass built that trigger.
2. **No per-tenant resource quota or noisy-neighbor protection.**
3. **No filesystem-level encryption or OS-level per-tenant access control**
   beyond what already protected the single shared file.
4. **No automated backup/restore**, per-tenant or otherwise — a pre-existing
   gap (`docs/known-issues.md`), not worsened or fixed by this pass.
5. Schema-per-tenant (Option B) or a fully managed multi-tenant warehouse
   service remain reasonable future evolutions if operational complexity
   (many small files) becomes the binding constraint — not needed at this
   scale.

See `docs/prompt-12.5-tenant-isolation-report.md` for full test evidence,
performance observations, and the final release gate.
