# Testing

What each layer proves, why it lives where it does, and what the suite refuses
to do.

## The ladder

| Suite | Runs against | Needs Docker | Wall clock |
|---|---|---|---|
| `backend/tests/unit` | pure functions and domain rules | no | seconds |
| `data_platform/tests/unit` | schema, conform SQL, pipeline logic | no | seconds |
| `ml/tests` | models, metrics, leakage | no | seconds |
| `ui/tests` | workspaces through Streamlit's `AppTest` | no | seconds |
| `data_platform/tests/integration` | dbt models and their contracts | no | ~90s |
| `backend/tests/integration` | the HTTP API over Postgres + a built warehouse | yes | ~14 min |

`make test` runs the fast ladder. `make test-integration` runs the rest.

## Why the integration suites share two warehouses

Each API suite used to build its own: generate CSVs, run the ingestion
pipeline, then `dbt seed`, `snapshot` and `build`. Identical work, about three
minutes, ten times over — half an hour to prove things that take seconds to
check once the warehouse exists.

A thirty-minute integration job is a job that never runs on a pull request, and
an integration suite that only runs on someone's laptop is decoration. So the
builds are session-scoped and cached by *shape*:

* **`ESTATE`** — 63 days, 10 stores. Nine of the ten API suites use it. Enough
  for a league table, a region decomposition, and a three-week window against a
  six-week baseline.
* **`DEEP_HISTORY`** — 140 days, 3 stores. Forecasting needs folds, not
  breadth; a wide estate over that history is four times the rows and buys
  nothing the backtest uses.

Sharing is safe because these suites **read**. That is not left to good
intentions — `backend/tests/unit/test_fixture_contract.py` asserts it: no suite
may build its own warehouse, keep a private copy of the user map, or open a
DuckDB connection without `read_only=True`. A violation fails in seconds rather
than in a twenty-minute job.

The Postgres side is *not* shared that way. Decisions, notifications and auth
events are written by tests, and those go through `migrated_db`.

## Mock data is generated, not fixtured

There are no JSON fixtures of "example sales". The suites run the same
generators the demo stack uses (`data_platform/ingestion/generators`), which
produce a coherent retailer: stores with clusters, SKUs with categories,
weather that correlates with regions, deliveries that sometimes run late.

That matters because the platform's claims are about *relationships*. A
root-cause test needs a decline that genuinely lands in one segment; a
retention test needs cohorts that genuinely decay. Hand-written fixture rows
would let a decomposition pass while being arithmetically wrong.

Some shocks are **planted deliberately** — severe weather in the Northeast, a
carrier degrading in the West — so the investigation suite can assert that the
engine finds the thing that was actually put there, rather than that it found
something.

## Coverage

Measured across every package, combined from the unit and integration jobs, and
gated in two places:

* **`backend/app/domain` and `backend/app/services` at 85%.** This is where a
  wrong number comes from.
* **The whole tree at 75%.** The API and infrastructure layers are thin and are
  covered end to end by the integration suites; holding them to the same bar
  would buy tests of framework glue.

Coverage is combined rather than measured per job, because a service covered
only by integration tests would otherwise report as untested and invite someone
to "fix" it with a unit test that mocks the thing under test.

## What the suite deliberately does not do

**No mocked database in the integration suites.** A mock proves the mocks agree
with each other. The failures worth catching — a GROUP BY Postgres rejects, a
`jsonb` column that will not take an `Infinity`, a migration that works on an
empty database and not a populated one — are invisible to a fake.

**No snapshot tests of rendered output.** They fail on every intentional change
and pass on every wrong number.

**No assertions on figures the platform computes.** A test that hard-codes
`net_revenue == 2_505_729.88` is a test of the generator's seed. The suites
assert *properties*: that ratios recompute at each grain, that a decomposition's
parts relate to its whole, that confidence never exceeds the ceiling its
evidence class allows.
