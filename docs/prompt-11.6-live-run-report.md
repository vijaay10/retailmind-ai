# Prompt 11.6 — Full Project Launch & Interactive System Verification

**Date:** 2026-08-16
**Scope:** launch the CURRENT repository (the code Prompt 11.5 verified: three
release blockers resolved, 328/328 backend integration tests passing) as a
complete, running application — not another audit. `docs/prompt-11-final-release-audit.md`
and `docs/prompt-11.5-remediation-report.md` are untouched historical records.

---

## 1. Current git commit

`2fc3ab49a7646f3a21e5fbcf55afe39ad156c10b` (HEAD, unchanged — the verified
Prompt 10.5–11.5 fixes exist in the working tree, not yet committed; this is
what got built).

---

## 2. Build information

Built via the repository's own documented first-run path, `make demo`, which
builds images and the demo warehouse concurrently, then runs migrations and
seeding as services before bringing up the app.

| Image | Build result | Image ID |
|---|---|---|
| `retailmind-api:latest` | success (cache-identical to the filesystem-verified Prompt 11.5 `rmstage115-api` build — same source tree, same layer hashes) | `de14a1221323` |
| `retailmind-ui:latest` | success (same cache identity as `rmstage115-ui`) | `aedb0b8bce72` |
| `retailmind-worker:latest` | success | `936d28781a7a` |
| `retailmind-beat:latest` | success | `66d6d6e7f690` |
| `retailmind-migrate:latest` / `retailmind-seed:latest` | success (one-shot images, same api.Dockerfile, different command) | `e3f622024ca2` / `e422273507d3` |

**Filesystem-verified current code**, not assumed from a successful build:
```
$ docker run --rm retailmind-api:latest sh -c "grep -q RecommendationCategory app/infrastructure/db/models/enums.py"
Blocker-3 fix PRESENT
$ docker run --rm --entrypoint sh retailmind-ui:latest -c "grep -q infer_string /srv/ui/retailmind_ui/__init__.py"
Blocker-2 fix PRESENT
```

The stale `rmprod-*` stack (a 2026-08-07 build, 8–9 days old) was **not
touched, stopped, or rebuilt** — it kept running throughout, untouched,
on its own separate compose project and network.

---

## 3. Stack used

`make demo` — `docker compose -f infra/compose/compose.yml -f infra/compose/compose.demo.yml up -d --wait`,
compose project `retailmind` (a third, independent project alongside the
pre-existing `rmprod` and unrelated `opendsa` projects — no shared
containers, networks, or volumes).

This is the repository's documented "first-run path": the base stack
(Postgres, two Redis instances, MinIO, API, worker, beat, UI) plus the demo
overlay, which runs `migrate` and `seed` as one-shot services before the app
starts, and mounts an already-built demo warehouse (`.local/demo/retailmind.duckdb`,
built 2026-08-14, reused rather than regenerated — `demo-warehouse` skips
rebuilding when the file already exists, avoiding an unnecessary 2+ minute
regeneration).

`compose.prod.yml` (nginx edge, Prometheus, Grafana, backups, Docker
secrets) was deliberately **not** used for this launch: it's live production
configuration per this repository's own operating rules, requires secrets
(TLS certs, SMTP password, Grafana password, a real JWT private key) that
either don't exist locally or shouldn't be fabricated, and is exactly what
the `rmprod` stack already runs — reusing it would mean either touching
`rmprod` or standing up a second, redundant production-shaped deployment for
a manual walkthrough. `make demo` is the documented, safe, current-code path
for exactly this purpose.

**One prerequisite fix, made before starting:** the local `.env` (gitignored,
untracked, not application code) had `RM_AUTH_JWT_PRIVATE_KEY_FILE=infra/secrets/jwt_private_key`
set from earlier manual Docker testing in Prompt 11.5. That path is
host-relative and isn't mounted into any container (`api.Dockerfile` installs
`backend/` only), so leaving it active would have crashed the API container
on startup trying to read a file that doesn't exist inside it. Commented out
in `.env` with an explanation — this reverts to the documented dev fallback
(an ephemeral RSA keypair minted at process start), which is exactly what
`compose.demo.yml` already pins `RM_API_WORKERS=1` to make safe (single
process, so there's no multi-worker key-mismatch to avoid). No credential was
invented; this restored the project's own documented default.

---

## 4. Containers / services

| Service | Status | Restarts |
|---|---|---|
| `retailmind-postgres-1` | Up, healthy | 0 |
| `retailmind-redis-cache-1` | Up, healthy | 0 |
| `retailmind-redis-state-1` | Up, healthy | 0 |
| `retailmind-minio-1` | Up, healthy | 0 |
| `retailmind-api-1` | Up, healthy | 0 |
| `retailmind-worker-1` | Up, healthy | 0 |
| `retailmind-beat-1` | Up, healthy | 0 |
| `retailmind-ui-1` | Up, healthy | 0 |
| Dagster webserver (local process, not a compose service — this repository has no Dagster Docker image) | Up (pid recorded in this session) | n/a |

`migrate` and `seed` are one-shot services — both exited 0 and are not
expected to still be running.

---

## 5. Ports

| Service | Container port | Host port |
|---|---|---|
| API | 8000 | **8090** |
| UI (Streamlit) | 8501 | **8501** |
| Postgres | 5432 | not published (internal `app` network only) |
| Redis (both) | 6379 | not published (internal only) |
| MinIO | 9000 | not published (internal only) |
| Dagster webserver | — | **3001** (local process; 3000 was already occupied by the running `rmprod` Grafana, so `-p 3001` was used explicitly) |

---

## 6. URLs

| Access point | URL |
|---|---|
| Main UI | http://localhost:8501 |
| API | http://localhost:8090/api/v1/... |
| API docs (Swagger) | http://localhost:8090/api/docs |
| API OpenAPI schema | http://localhost:8090/api/openapi.json |
| Dagster UI | http://localhost:3001 |
| Grafana | not part of this stack (see §12) |
| Prometheus | not part of this stack (see §12) |
| Alertmanager | not part of this stack (see §12) |

---

## 7. Health status

| Component | Check | Result |
|---|---|---|
| API `/health` | `GET /health` | 🟢 `{"status":"ok","version":"local"}` |
| API `/ready` | `GET /ready` (real `SELECT 1` through the pool) | 🟢 `{"status":"ok"}` |
| Postgres | `SELECT version_num FROM alembic_version` | 🟢 `0006_recommendation_category` (head) |
| Redis cache | `redis-cli ping` | 🟢 `PONG` |
| Redis state | `redis-cli ping` | 🟢 `PONG` |
| Celery worker | `celery inspect ping` | 🟢 `celery@<container>: OK, pong` — 1 node online |
| UI root | `GET /` | 🟢 200 |
| API docs | `GET /api/docs` | 🟢 200 |
| Dagster webserver | `GET /` and a GraphQL `repositoriesOrError` query | 🟢 200; jobs and asset groups load (see §11) |

---

## 8. UI workspaces tested

**Live browser click-through was not performed this run** — the
`claude-in-chrome` extension reported disconnected in this session
(`tabs_context_mcp` returned "Browser extension is not connected"). Per this
prompt's own closing instruction ("I want to manually open the browser and
interact with the complete RetailMind AI system after you finish"), the
intended path is for you to do this directly rather than me automating it —
so instead of retrying browser automation, every workspace's **backing API**
was exercised directly with real authenticated HTTP requests, confirming
each one has working, real data behind it. This is not a substitute for
opening the pages yourself — it confirms the data path each page depends on
actually works, which you can then verify visually.

| Workspace | Backing endpoint(s) exercised | Result |
|---|---|---|
| 1 — Command Center | `/dashboard/executive`, `/dashboard/recommendations` | 🟢 200, real data (revenue cards, growth horizons, alerts, 1 seeded recommendation) |
| 2 — AI Investigation (RCA) | `/rca/investigate?question=...` | 🟢 200, real evidence-backed response |
| 3 — Decision Center | `/recommendations`, `/recommendations/decisions` | 🟢 200 (6862 bytes of real recommendation data; decisions ledger empty but valid) |
| 4 — AI Analyst | `/analyst/ask` | 🟢 see §9 — real query-planner pipeline exercised with multiple questions |
| 5 — Sales Intelligence | `/analytics/revenue/summary`, `/analytics/revenue/trend?metrics=net_revenue` | 🟢 200, real net_revenue figures from the warehouse |
| 6 — Customer Intelligence | `/customers/segments`, `/customers/rfm`, `/customers/churn-risk` | 🟢 200, real data |
| 7 — Inventory Intelligence | `/inventory/stockout-risk`, `/inventory/reorder` | 🟢 200, real data (7434 bytes stockout-risk) |
| 8 — Store Intelligence | `/dashboard/stores/ranking` (via executive dashboard tile) | 🟢 200 (part of `/dashboard/executive`) |
| 9 — Forecast Intelligence | `/forecasts/revenue`, `/forecasts/meta/accuracy` | 🟡 200, but returns an honest empty state — see §10 (real, not a bug) |
| 10 — Risk Center | `/notifications`, `/dashboard/alerts` (via executive) | 🟢 200 |
| 11 — Executive Briefing | `/dashboard/executive`, `/reports` | 🟢 200, real report list (7091 bytes) |
| 12 — Admin | `/auth/permissions` | 🟢 200, real 25-permission catalog for the CEO role (16 rows for the admin-specific permissions view, matching Prompt 11.5's live verification) |

Empty states: `/recommendations/decisions` (empty ledger — real, documented
Blocker-3 limitation), `/forecasts/revenue` (no published forecast — real,
confirmed against the warehouse directly, see §10) both returned structured,
non-crashing responses. No 500s anywhere in this pass.

---

## 9. API endpoints tested

24 distinct authenticated endpoints exercised across dashboard, analytics,
customers, inventory, forecasts, rca, recommendations, calibration,
notifications, reports, nlq, and auth — all real HTTP requests against the
running API and Postgres/DuckDB, not mocked. One request returned 422
(`/analytics/revenue/trend` without the required `metrics` query param) —
correctly rejected with a structured validation error, not a crash; re-run
with the parameter supplied returned 200. Full list and results in §8/§9's
surrounding sections above.

Authentication and RBAC: login as CEO (`priya@northwind.example`) and Admin
(`sam@northwind.example`) both succeeded with real JWTs; `/auth/me` and
`/auth/permissions` returned the correct role-scoped permission sets for
each.

---

## 10. AI workflow tested

Business Analyst (`/analyst/ask`), using the platform's own mock LLM
provider (confirmed via startup log line `llm_gateway_using_mock_provider` —
no real provider configured, no external API calls made) for narration, with
the query planner and evidence layer doing the real analytical work:

| Question | Result |
|---|---|
| "What were total sales?" | 🟢 Real answer: net revenue 65,220 over 2026-07-18 to 2026-08-16, with the actual compiled SQL (`SELECT sum(net_revenue) FROM analytics_semantic.v_mart_sales_daily WHERE ...`), evidence, caveats, and follow-up questions — the full documented pipeline (question → planner → governed SQL → evidence → narrator → response) exercised for real. |
| "What is net revenue by store?" | 🟢 Real 4-category breakdown returned. |
| "What does AOV mean?" | 🟢 `explain_kpi` capability answered correctly. |
| "Which stores performed best?" / "What products have inventory concerns?" | 🟡 Correctly refused with 422 — the deterministic term-matcher didn't recognize "performed" / "have, inventory, concerns" against its known metric vocabulary, and said so explicitly rather than fabricating an answer. This is the documented, no-LLM, templated-narration design (see project instructions) working as intended, not a bug — rephrasing to the platform's own vocabulary (e.g. "net revenue by store") succeeds. |
| "Explain the revenue forecast" | 🟡 Correctly refused: "No forecast has been published." Confirmed genuine, not an app bug — see §10 data check below. |

No fabricated metrics anywhere: every successful answer's numbers trace to a
real compiled SQL query against the real warehouse, visible in the response
payload itself (`data.compiled_sql`).

## 10b. Data + ML experience

Queried the mounted demo warehouse (`.local/demo/retailmind.duckdb`)
directly to confirm the data every workspace and the analyst draws from is
real, without regenerating it:

| Table | Rows |
|---|---|
| `fct_sales` / `stg_pos__sales` | 1,738 |
| `fct_inventory_daily` / `stg_inventory__positions` | 672 |
| `fct_purchase_orders` / `stg_purchasing__orders` | 448 |
| `stg_fulfilment__deliveries` | 336 |
| `stg_weather__observations` | 140 |
| `fct_forecast` | 21 |
| `mart_sales_daily` | 866 |
| `mart_inventory_daily` / `mart_inventory_health` | 112 / 24 |
| `mart_rca_weather_effect` | 4 |
| `analytics_ml.forecast_predictions` / `forecast_runs` / `forecast_explanations` | **0** |

The three empty `analytics_ml.*` tables are exactly why `/forecasts/revenue`
and "explain the revenue forecast" both correctly report no published
forecast — the ML training/publish job hasn't been run against this demo
warehouse. Real, consistent, not a crash or a fabricated fallback. Running
`make forecast` would populate these but was not done here, matching this
prompt's "do not regenerate large datasets unnecessarily" instruction and
"do not start a remediation cycle unless something prevents launch" — an
empty, honestly-reported forecast state doesn't prevent the application from
running.

---

## 11. Dagster UI tested

Started as a local process (this repository has no Dagster Docker image;
`make dagster` runs `dagster dev` directly) on port 3001 — 3000 was already
occupied by the running `rmprod` stack's Grafana container.

- Webserver responds: `GET http://localhost:3001/` → 🟢 200.
- Definitions load for real: a GraphQL query against the running webserver
  confirms all 8 jobs (`daily_ingestion`, `backfill_ingestion`, `dbt_build`,
  `forecast_training`, `full_pipeline`, `quality_replay`,
  `rebuild_warehouse`, `__ASSET_JOB`) and all 7 asset groups (`ingestion`,
  `dbt`, `staging`, `analytics`, `warehouse`, `ml`, `default`) are visible —
  including **`dbt_build`, the job Prompt 11 found broken and Prompt 11.5
  fixed and proved via direct execution.**
- **Not re-executed live here.** `dbt_build`'s real execution (77 assets,
  152/152 dbt checks, against the actual named job object) was already
  proven in Prompt 11.5 with full evidence. Triggering it again here would
  write into `.local/demo/retailmind.duckdb` — the same file the live API
  container has open right now — while you're about to interact with the
  running demo. Re-running it live risked mutating the demo's data
  mid-session for no new evidence; skipped deliberately, not because it
  doesn't work.
- 🟡 **One real, pre-existing bug surfaced in the daemon logs**, unrelated
  to any of the three fixed blockers: the `quality_quarantine_alert` sensor
  throws `AttributeError: 'SensorEvaluationContext' object has no attribute
  'sensor_runtime'` on every tick (`context.sensor_runtime` doesn't exist —
  `data_platform/orchestration/dagster/sensors.py:121`). This does not
  crash the webserver or block anything else — the UI, jobs, and asset
  groups all remained fully accessible — but the sensor itself is broken
  and would never actually alert on a quarantine. **Not fixed** — out of
  this prompt's scope ("do not start another remediation cycle unless
  something prevents the application from launching"; it didn't). Reported
  here as a genuine finding, not silently absorbed.

---

## 12. Monitoring tested

⚪ **Not part of this stack, by design — not a launch failure.** Prometheus,
Grafana, and Alertmanager are defined only in `infra/compose/compose.prod.yml`,
not in `compose.yml`/`compose.demo.yml`. Standing them up would mean either:
(a) reusing the already-running `rmprod` monitoring containers — out of
scope, that stack was explicitly left untouched, or (b) building a second,
independent `compose.prod.yml` deployment, which needs Docker secrets (TLS
certs, SMTP password, Grafana admin password, a real JWT signing key) this
session has no authorization to fabricate, and would functionally duplicate
`rmprod`. This matches this repository's own documented known issue: *"No
backups, no Alertmanager, no Grafana dashboards. The four Prometheus alert
rules evaluate and notify nobody."* Nothing here is newly broken by this
launch — it was never wired into the demo/dev path.

---

## 13. Errors encountered

**Real application failures:** none. Zero container restarts across all 8
services. `docker logs` on api/worker/beat/ui shows no exceptions,
tracebacks, crashes, segfaults, or connection failures — only structured
info-level request logs and one Starlette deprecation warning
(`HTTP_422_UNPROCESSABLE_ENTITY` → `_CONTENT`, pre-existing, cosmetic).

**Real, non-blocking bug found live:** the Dagster `quality_quarantine_alert`
sensor (§11) — genuine, reproducible, unrelated to the three Prompt 11.5
blockers, not fixed per this prompt's scope.

**Expected/by-design non-errors, not treated as failures:**
- `/analytics/revenue/trend` 422 without `metrics` — correct request
  validation.
- Analyst 422s for unrecognized vocabulary — correct refusal-over-fabrication
  behavior.
- `/forecasts/revenue` empty result and "No forecast has been published" —
  accurate reflection of empty `analytics_ml.*` tables, confirmed directly
  against the warehouse.
- `/recommendations/decisions` empty — the live analytical engine's
  accept/dismiss ledger genuinely has no entries yet (a documented,
  pre-existing Blocker-3 limitation, not reopened here).

---

## 14. Known limitations

Carried forward, not reopened or expanded:

1. Confidence-band calibration returns empty results — no numeric
   confidence score is persisted anywhere in the schema yet
   (Prompt 11.5, unchanged).
2. The live recommendation engine's decision ledger
   (`RecommendationDecision`) is not bridged to the tables calibration and
   the dashboard read from (Prompt 11.5, unchanged).
3. No forecast has been published against this demo warehouse — the
   `analytics_ml.*` tables are empty until `make forecast` is run (new
   observation this pass, not a defect — an un-run optional step).
4. The Dagster `quality_quarantine_alert` sensor throws on every tick
   (`sensor_runtime` attribute error) — real, found live this pass, not
   fixed (out of scope; does not block the webserver or any job).
5. No monitoring stack (Prometheus/Grafana/Alertmanager) in this demo
   launch — by design, matches this repository's own documented known
   issues; only available in `compose.prod.yml`, which is `rmprod`'s
   domain and was left untouched.
6. Live browser click-through of the 12 workspaces was not performed this
   pass (extension disconnected) — every workspace's backing API was
   verified instead (§8). Recommended you open http://localhost:8501
   yourself to see the actual rendered pages.

---

## 15. Instructions for manually running the system again

```bash
# Stop and remove this demo stack (deletes its data — Postgres/Redis/MinIO volumes):
make demo-down

# Bring it back up (reuses the already-built demo warehouse, fast):
make demo

# Stop Dagster's local dev server (no compose service — kill the process):
# (pid was 74919 → dagster-webserver child in this session; check `lsof -i :3001`
#  or `pkill -f "dagster dev"` if you don't have the pid handy)

# Regenerate the demo warehouse from scratch (only if you want fresh synthetic data):
make demo-rebuild

# Sign in:
#   http://localhost:8501
#   priya@northwind.example / ChangeMe-Demo1!  (CEO — lands on Command Center)
#   Six more role-specific users exist with the same password — see
#   backend/app/infrastructure/db/seeds/sample.py for the full list.
```

---

## Summary

The current, Prompt 11.5–verified codebase is running as a real, healthy,
multi-service application — not the stale `rmprod` stack, not a subset, not
a simulation. Every workspace's backing API was exercised with real
authenticated requests against real Postgres and DuckDB data; the AI
Business Analyst pipeline was exercised end-to-end with real questions,
real compiled SQL, and no fabricated numbers; Dagster's real job/asset graph
loaded including the previously-broken `dbt_build` job. Zero container
restarts, zero application-level errors in any log. One new, real,
non-blocking bug was found and disclosed (a Dagster sensor), and monitoring
was correctly left out rather than faked. The stack is left running per
this prompt's explicit instruction.

Per this prompt's instruction: stopping here. Not proceeding to Prompt 12.
