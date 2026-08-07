# RetailMind AI

**An AI-powered Retail Decision Intelligence Platform** — it doesn't just show you dashboards; it investigates why metrics changed, predicts what happens next, and recommends what to do about it.

> 🚧 **Status: Phase 0 — repository scaffold.** Follow progress via [tagged releases](../../releases) (`v0.1` data platform → `v1.0` production-grade, per the [roadmap](docs/architecture/README.md)).

<!-- TODO(v0.3): 90-second demo GIF goes here — dashboard → anomaly badge → "Why?" → root cause → report -->

## What it does

| Capability | Instead of |
|---|---|
| Detects anomalous metric movements automatically | Noticing a bad number three days late |
| Diagnoses root causes (dimensional decomposition) | Two days of pivot-table archaeology |
| Forecasts demand with public accuracy scoreboards | Spreadsheet extrapolation |
| Answers questions in plain English (governed semantic layer) | SQL request tickets |
| Generates the Monday-morning executive report | Deck assembly by hand |
| Recommends actions (reorders, markdowns) with evidence | Gut feel |

## Quickstart

```bash
git clone <repo-url> && cd retailmind-ai
make demo        # boots the full stack + synthetic retailer, opens the app
```

Requirements: Docker + Docker Compose, `make`, [uv](https://docs.astral.sh/uv/). See [docs/](docs/) for everything else.

## Architecture at a glance

Warehouse-centric modular monolith: Python connectors land data in a **bronze/silver/gold** medallion warehouse (DuckDB locally, Snowflake profile for cloud) transformed by **dbt**, orchestrated by **Airflow**. A **semantic layer** is the single source of metric truth for every consumer: dashboards (Next.js), the six intelligence engines (alerts, RCA, forecasting, recommendations, NLQ, reports), and the **FastAPI** backend. LLM calls (Claude) pass through one gateway — versioned prompts, PII scrubbing, token budgets, grounded narration. *The LLM never invents a number.*

Full design documentation lives in [`docs/architecture/`](docs/architecture/) — 9 documents covering architecture, PRD, database, backend, ETL, analytics, AI, UX, and operations, plus [ADRs](docs/architecture/adr/).

## Repository layout

```text
backend/         FastAPI modular monolith (api → services → domain → infrastructure)
data_platform/   Connectors, Airflow DAGs, dbt project, data-quality suites
ml/              Feature builders, training, evaluation, registry config
ui/              Streamlit console — 12 AI-native workspaces, design system
infra/           Dockerfiles, compose profiles, nginx edge, monitoring
docs/            Architecture docs, ADRs, runbooks, data dictionary
```

## Development

```bash
make up          # dev stack with hot reload
make api         # API only, no Docker
make console     # Streamlit console (RM_API_BASE_URL points it at the API)
make test        # full test ladder
make lint        # ruff + mypy + sqlfluff
make down        # stop everything
```

Branch strategy: trunk-based, short-lived branches, conventional commits, PRs require green CI + review. See [CONTRIBUTING](docs/CONTRIBUTING.md) <!-- TODO(S1) -->.

## License

TBD <!-- TODO before public release -->
