# Prompt 12.5 — Multi-Tenant Warehouse & Analytics Isolation Remediation

**Date:** 2026-08-17
**Scope:** close the one real gap Prompt 12 identified and refused to paper
over — the analytics warehouse was shared across every tenant.
`docs/prompt-11-final-release-audit.md` through
`docs/prompt-12-productization-report.md` are untouched historical records;
`docs/company-onboarding.md` and `docs/known-issues.md` were updated in
place to mark the gap resolved, per this prompt's instruction, not rewritten.

---

## 1. Previous architecture

One `SemanticLayerClient` was constructed once, at process startup
(`backend/app/main.py`), from a single global `RM_WAREHOUSE_DUCKDB_PATH`,
and stored on `app.state.semantic_client`. Every request — regardless of
which tenant's JWT authenticated it — was handed the same object by
`app.api.deps.get_analytics_service`. `SemanticLayerClient` itself already
opened a fresh, read-only DuckDB connection per query (not a long-lived
handle), but always against the same file.

`AnalyticsCache` (Redis) was already tenant-scoped in its key format
(`rm:{env}:sem:{tenant_id}:{snapshot}:{query}`) — this predates this pass
and was verified, not built.

---

## 2. Problem discovered in Prompt 12

`test_the_shared_warehouse_is_a_real_unresolved_isolation_gap`
(`backend/tests/integration/test_tenant_isolation.py`, written in Prompt 12)
proved a brand-new tenant — zero transactions of its own — received the
demo tenant's live-computed recommendations from `GET /recommendations`,
because that endpoint reads the semantic layer and the semantic layer was
one shared file. Prompt 12's own report named this the single most
consequential finding of that pass and declined to fix it without
architectural approval.

---

## 3. Architecture decision

Three options evaluated (full writeup: `docs/multi-tenancy-architecture.md`
§"Architecture decision"):

| Option | Verdict |
|---|---|
| A. Shared warehouse + `tenant_id` column, filtered everywhere | **Rejected** — requires a correct filter in 67+ dbt models and every hand-written semantic query with zero tolerance for one missed clause; the exact anti-pattern this prompt named explicitly. |
| B. Separate schema per tenant, one file | Rejected as a valid but strictly larger change than C for an equivalent guarantee (needs a custom dbt schema-generation macro). |
| **C. Separate DuckDB file per tenant** | **Chosen.** Zero dbt model changes. Zero new dimension columns. Physical isolation: two tenants' rows are never in the same file to collide, join, or leak from. |

---

## 4. Implementation

| File | Change |
|---|---|
| `backend/app/infrastructure/db/models/auth.py` | `Tenant.warehouse_path: str \| None` — explicit override; NULL uses the per-slug convention. |
| `backend/app/infrastructure/db/migrations/versions/202608170900_tenant_warehouse_isolation.py` | Adds the column; backfills the demo tenant's row from `WarehouseSettings().duckdb_path` read at migrate time (environment-correct everywhere, not hardcoded). |
| `backend/app/core/config.py` | `WarehouseSettings.root` (`RM_WAREHOUSE_ROOT`, default `.local/tenants`) — where a tenant without an override resolves its file. |
| `backend/app/infrastructure/semantic/tenancy.py` (new) | `resolve_warehouse_path(tenant, settings)` (pure function) and `TenantWarehouseRegistry` (per-tenant `SemanticLayerClient` cache). |
| `backend/app/main.py` | `app.state.semantic_client` (singleton) → `app.state.warehouse_registry` (`TenantWarehouseRegistry`). |
| `backend/app/api/deps.py` | New `get_tenant_semantic_client`/`TenantSemanticClientDep`, resolved from `principal.tenant_id` via a real `TenantRepository.get()` lookup. `get_analytics_service` now depends on it. |
| `backend/app/api/deps.py` — `get_dashboard_service` | **Real bug #1, found during remediation**: read `app.state.semantic_client` directly, bypassing the analytics dependency's resolution entirely. Fixed to take `TenantSemanticClientDep`. |
| `backend/app/workers/container.py` | **Real bug #2**: `build_analytics()` read `RM_WAREHOUSE_DUCKDB_PATH` from the OS environment unconditionally — every tenant's scheduled notification sweep read the same file. Fixed to resolve per-tenant via the same `resolve_warehouse_path`; `build_notification_service` became `async` to fetch the tenant row first. |
| `infra/compose/compose.demo.yml` | **Real bug #3**: the `migrate` service's environment lacked `RM_WAREHOUSE_DUCKDB_PATH`, so the migration's environment-aware backfill (item above) resolved the *default* path, not the one `api`/`worker` actually mount — silently pointing the demo tenant at the wrong file. Added the same env var `api` already sets. |
| `backend/tests/integration/conftest.py` | Test harness fix: the demo tenant's `warehouse_path` now gets pointed at each test session's dynamically-built warehouse (`_point_demo_tenant_at`) before the app is created, since resolution no longer reads `RM_WAREHOUSE_DUCKDB_PATH` directly. |

All three "real bugs" above were found by testing the actual running system
(pytest + live Docker), not by inspection alone — each one is exactly the
"trusted at one call site, quietly skipped at another" failure mode this
prompt was written to catch, occurring at a *different* layer than the one
already fixed (dashboard dependency injection, worker container, compose
environment) rather than a second instance of the same code.

---

## 5. Data flow

See `docs/multi-tenancy-architecture.md` for the full diagram. Summary: JWT
→ `Principal.tenant_id` → `TenantRepository.get()` (one Postgres read) →
`resolve_warehouse_path()` → `TenantWarehouseRegistry.client_for()` (cached)
→ `SemanticLayerClient` (opens a DuckDB connection per query, unchanged
behavior) → every analytics-touching service (dashboard, recommendations,
forecasting, RCA, reports, notifications) — all composed on
`AnalyticsServiceDep`, all fixed by this one dependency change.

---

## 6. DBT changes

**None.** All 67+ models, their tests, and their schema are byte-for-byte
unchanged. Isolation is physical (separate files per tenant), not a filter
threaded through the transformation layer — see
`docs/multi-tenancy-architecture.md` §"DBT architecture" for why this makes
Prompt 12.5's literal ask for tenant-scoped dbt tests a non-issue rather
than a skipped requirement.

---

## 7. API changes

Summarized in §4's table. Net effect: `AnalyticsServiceDep` and everything
built on it (`CustomerServiceDep`, `InventoryServiceDep`,
`ForecastingServiceDep` via `ForecastServiceDep`, `RcaServiceDep`,
`RecommendationServiceDep`, `ReportServiceDep`, `NotificationServiceDep`,
`DashboardServiceDep`) now transitively resolve the caller's own tenant's
warehouse. Verified by grepping `backend/app/` for every remaining
`SemanticLayerClient(` construction and `app.state.` reference after the
fix — none bypass the new dependency.

---

## 8. Cache changes

**None needed — verified, not built.** `AnalyticsCache.key()` already
embedded `tenant_id`. The bug was never in the cache; it was that a cache
*miss* fell through to a query against the wrong (shared) file, which then
cached correctly-scoped but wrong-content results. Fixing the connection
layer fixed what gets cached.

---

## 9. ML / recommendation changes

**None needed.** `RecommendationService` and `ForecastingService` each take
exactly one warehouse-touching dependency, `AnalyticsService` — fixed once,
fixed everywhere for both. The separate, Postgres-native
`Recommendation`/`RecommendationOutcome` batch tables (Prompt 11.5's
calibration fix) were already tenant-scoped and are unrelated to this
pass's fix.

---

## 10. UI changes

**None.** The console has never held a database connection; it renders
whatever the API returns over HTTP. Verified live against the running demo
stack (§13) rather than assumed.

---

## 11. Security tests (regression)

| Suite | Result |
|---|---:|
| `backend/tests/unit/test_tenant_warehouse_resolution.py` (new) | 🟢 6/6 — pure resolution logic, no Docker needed |
| `backend/tests/unit` (full) | 🟢 645/645 |
| `backend/tests/integration/test_tenant_isolation.py` | 🟢 6/6 (updated to assert the fixed behavior — see §2/§12) |
| `backend/tests/integration/test_tenant_warehouse_isolation.py` (new) | 🟢 5/5 |
| `backend/tests/integration` (full) | 🟢 **339/339 passed** (972.91s) — Prompt 12's 334 baseline + this pass's 5 new `test_tenant_warehouse_isolation.py` tests, zero failures |
| `make test` (backend unit + data_platform + ml + ui) | 🟡 1021/1022 — the 1 failure is the same pre-existing, unrelated `test_command_center_loads_without_error` markdown-index fragility documented since Prompt 11.5, confirmed still unrelated |
| `data_platform/tests/unit` | 🟢 140/140 — untouched, no regressions |
| `data_platform/tests/unit/test_dagster_orchestration.py` | 🟢 22/22 |
| Ruff (whole monorepo) | 🟢 clean |
| mypy (`backend/app`) | 🟢 clean, 178 files |
| import-linter | 🟢 clean |
| `scripts/check_env.py` | 🟢 clean (77 variables, `RM_WAREHOUSE_ROOT` added to `.env.example`) |
| `scripts/check_ports.py` | 🟢 clean (compose.demo.yml env addition doesn't publish a new port) |
| `scripts/check_docs_integrity.py` | 🟢 clean |
| Alembic (`0008_tenant_warehouse_isolation`, head) | 🟢 single head; base→head→downgrade→head round trip on a disposable database |

---

## 12. Cross-tenant attack tests

Two real tenants (`Tenant A Retail`, `Tenant B Retail`), each with a real,
separately-built DuckDB warehouse (`ingestion.demo.build` — the same code
`make demo` runs — over `Shape(days=14, stores=2, ...)` and
`Shape(days=14, stores=6, ...)` respectively), served by the **same running
API process** as the demo tenant, in `test_tenant_warehouse_isolation.py`:

| Attack | Result |
|---|---|
| Tenant A token → `/analytics/revenue/summary` vs. Tenant B's own | 🟢 Genuinely different, real revenue totals (both computed from `dbt build`, not mocked) |
| Identical business id (`store_id`) present in both tenants' data | 🟢 Same id, different attributed revenue in each tenant's response — proves no cross-tenant join is possible, not just that none happened |
| Tenant A token → `/recommendations` immediately after Tenant B's request | 🟢 Tenant A's figure unchanged by having just served Tenant B — no shared mutable state |
| Tenant A vs. Tenant B → `/forecasts/meta/accuracy` | 🟢 Both non-empty (the standard dbt build publishes a `seasonal_naive_w4` baseline), independently scored — WAPE 0.228 vs. 0.103, real, different, from real different data |
| A brand-new, unprovisioned tenant → `/recommendations` | 🟢 503 `"warehouse is temporarily unavailable"` — fails closed, never falls through to the demo tenant's data (`test_an_unprovisioned_tenant_never_reads_the_demo_warehouse`, `test_tenant_isolation.py`) |
| Two new tenants existing → demo tenant's own `/analytics/revenue/summary` | 🟢 Unchanged, still real, non-zero — backward compatibility proven live, not assumed |

No cross-tenant data leak was found or is reported.

---

## 13. Runtime tests (Phase 13 — real execution, no mocks)

Against the fresh (non-`rmprod`) `retailmind` demo stack, rebuilt from the
current repository:

```
$ docker build -f infra/docker/api.Dockerfile -t retailmind-api:latest .    # success
$ docker run ... retailmind-api:latest migrate
Running upgrade 0007_tenant_company_profile -> 0008_tenant_warehouse_isolation
$ docker exec retailmind-postgres-1 psql ... "SELECT slug, warehouse_path FROM tenant WHERE slug='northwind-threads';"
       slug        |          warehouse_path
-------------------+-----------------------------------
 northwind-threads | /data/warehouse/retailmind.duckdb   ← correct, after
                                                            the compose.yml fix
$ docker compose ... up -d --force-recreate api worker beat
$ curl .../analytics/revenue/summary   (as the real seeded CEO user)
net_revenue: 65220.449999999975   ← identical to the pre-remediation figure,
                                     proving backward compatibility live
$ curl .../dashboard/executive → 200
$ curl .../recommendations → 200
```

The two-tenant, distinct-data proof itself (§12) ran through the dev/test
harness (`uv run pytest`) rather than by mounting additional tenant
warehouse files into the Docker stack — the harness runs the identical,
unmodified `ingestion.demo.build`/dbt pipeline `make demo` uses, so the
*mechanism* proven is the same; only the container boundary differs. Stated
as a scope choice, not hidden.

**The fail-closed half was additionally proven directly against the live
Docker container**, not just pytest: a genuinely new tenant + user was
inserted directly into the running `retailmind-postgres-1` (simulating an
onboarding step outside the test harness), signed in for a real JWT against
the running `retailmind-api-1`, and queried:

```
$ curl .../recommendations  (new, unprovisioned tenant's real token)
{"type":"https://retailmind.ai/errors/dependency-unavailable", "status":503,
 "detail":"warehouse is temporarily unavailable."}

$ curl .../analytics/revenue/summary  (demo tenant, same running container)
net_revenue: 65220.449999999975   ← unaffected by the new tenant's existence
```
Test tenant removed afterward; the demo tenant's data was never touched.

**Recorded, real:**
- Tenant A: `Shape(days=14, stores=2, lines_per_store=22, skus_per_store=6)`
- Tenant B: `Shape(days=14, stores=6, lines_per_store=24, skus_per_store=10)`
- Both quarantine-free `dbt build`s (an earlier, too-small shape attempt
  *was* correctly quarantined by the real quality gate's reject-rate
  threshold — evidence the gate is real, not decorative, encountered and
  fixed by enlarging the shape, not by loosening the threshold)
- Real revenue totals: two distinct, non-zero figures (exact values vary
  run to run — synthetic generator uses a date-offset seed, not a fixed
  constant — the *property* proven is inequality, not a specific number)
- Real WAPE scores from real dbt-published baselines: 0.228 (tenant A) vs.
  0.103 (tenant B) in the run captured above

---

## 14. Performance observations (Phase 16 — measured, not assumed)

20 sequential HTTP requests to `/analytics/revenue/summary` against the
live, rebuilt demo stack, post-remediation:

```
min=12.6ms  p50=15.1ms  p95=24.1ms  max=24.1ms
```

(Includes full process-spawn overhead of `curl` invoked from Python
subprocess per request — actual server-side time is lower.) The one added
cost versus the pre-remediation design is a single Postgres read
(`TenantRepository.get()`) per analytics-touching request; `SemanticLayerClient`
already opened a DuckDB connection per query before this pass, so no new
per-request DuckDB cost was introduced. `TenantWarehouseRegistry` caches the
resolved client object (not the connection) per tenant for the process's
lifetime, so the resolution itself only happens once per tenant per
process, not once per request.

No indexing or partitioning changes were made — not warranted at this
scale (Phase 16's own "do not prematurely optimize" instruction), and the
one new Postgres query (`SELECT ... FROM tenant WHERE id = ?`) already hits
`tenant.id`'s primary-key index.

---

## 15. Regression results

| Suite | Before this pass | After |
|---|---:|---:|
| `backend/tests/unit` | 631 (Prompt 12) | 🟢 645 |
| `backend/tests/integration` | 334 (Prompt 12) | 🟢 **339** |
| `data_platform/tests/unit` | 140 | 🟢 140, unchanged |
| `data_platform/tests/unit/test_dagster_orchestration.py` | 22 | 🟢 22, unchanged |
| `make test` (unit ladder, all 4 workspace members) | 1011/1012 (Prompt 12) | 🟡 1021/1022 — same single pre-existing, unrelated UI test failure |
| Ruff / `ruff format --check` / mypy / import-linter (whole monorepo) | clean | 🟢 clean |
| `scripts/check_env.py` / `check_ports.py` / `check_docs_integrity.py` | clean | 🟢 clean |

No existing test was weakened, deleted, or had its expected values loosened.
One Prompt-12 test was **updated to assert the corrected behavior** its own
docstring said it would eventually require updating to: the shared-warehouse
test (renamed
`test_an_unprovisioned_tenant_never_reads_the_demo_warehouse`) now asserts a
fail-closed 503 instead of documenting an open leak — a strengthening, not
a weakening, of what the test guarantees.

---

## 16. Remaining limitations

Carried forward from `docs/multi-tenancy-architecture.md`'s threat model,
not reopened or expanded here:

1. No self-serve tenant/warehouse provisioning trigger — `warehouse_path`
   is set at the repository level (proven in tests); a real onboarding flow
   would need to call the same mechanism once file upload → ingestion is
   wired end-to-end.
2. No per-tenant resource quota or noisy-neighbor protection — all tenants
   share host CPU/disk I/O.
3. No filesystem-level encryption or OS-level per-tenant access control
   beyond what already protected the single shared file.
4. No automated backup/restore, per-tenant or otherwise (pre-existing gap,
   unrelated to and not worsened by this pass).
5. The two-tenant runtime proof ran via the dev/test harness rather than
   inside the disposable Docker stack directly (§13) — the pipeline
   exercised is identical either way, but stated as a scope choice.

---

## Final release gate

1. **Can Tenant A see Tenant B's sales?** 🟢 NO — proven via distinct real
   revenue totals from separately-built warehouses.
2. **Can Tenant A see Tenant B's inventory?** 🟢 NO — same mechanism; not
   independently re-tested per domain since isolation is at the connection
   layer, not per-query (see §5 — every domain goes through the same fix).
3. **Can Tenant A see Tenant B's products?** 🟢 NO — proven via the
   identical-store-id test; the same physical-separation argument applies
   to every dimension, since there is no shared table for any of them.
4. **Can Tenant A see Tenant B's stores?** 🟢 NO — directly proven
   (`test_identical_store_ids_across_tenants_return_tenant_specific_data`).
5. **Can Tenant A see Tenant B's forecasts?** 🟢 NO — directly proven, real
   WAPE scores differ.
6. **Can Tenant A see Tenant B's recommendations?** 🟢 NO — directly proven,
   independence across interleaved requests.
7. **Can Tenant A access Tenant B's API resources?** 🟢 NO — company
   profile, recommendations, analytics all tested directly.
8. **Can Tenant A receive Tenant B's cached data?** 🟢 NO — cache key
   already tenant-scoped, verified by reading `AnalyticsCache.key()`
   directly, not assumed.
9. **Can Tenant A access Tenant B's semantic analytics?** 🟢 NO — the
   layer this whole pass fixed.
10. **Can the demo tenant remain isolated?** 🟢 YES — its explicit
    `warehouse_path` override keeps it on its own real file, proven
    unaffected by two new tenants existing, both in pytest and live Docker.
11. **Can two companies use identical business IDs safely?** 🟢 YES —
    directly proven, not merely architecturally implied.
12. **Can two companies have completely different schemas?** 🟢 YES —
    carried over from Prompt 12 (Company A/B/C column-name mapping),
    unaffected by this pass, still passing.
13. **Does the UI show only the authenticated tenant's data?** 🟢 YES —
    the UI has no independent data path; it inherits the API's now-correct
    isolation entirely, verified live against the running demo tenant.
14. **Was this proven using real execution rather than mocks?** 🟢 YES —
    real `dbt build`s, real HTTP requests, real Docker rebuild and restart,
    real measured latencies, and a real cross-tenant attack executed
    directly against the live Docker container (§13) in addition to pytest.
    No response in any test file is a mock or a hand-constructed fixture
    standing in for the warehouse.
15. **Do all regression tests pass?** 🟢 YES — backend unit 645/645,
    backend integration 339/339 (zero failures, up from Prompt 12's 334
    with 5 new tests added), data_platform unit 140/140, Dagster unit
    22/22, ruff/format/mypy/import-linter clean across the monorepo. The
    only known failure anywhere is the single pre-existing, unrelated
    `test_command_center_loads_without_error` UI test, documented since
    Prompt 11.5 and unrelated to tenancy.

**No cross-tenant data leakage was found that was not also fixed within
this pass.** Three real instances of the underlying bug were found (the
original shared client, the dashboard dependency bypass, the worker's
unconditional environment read) — all three fixed, all three proven fixed
by direct test.

Per this prompt's instruction: stopping here. Not proceeding to Prompt 13.
