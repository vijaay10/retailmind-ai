"""Schedules for RetailMind data platform.

Defines when assets should be materialized automatically:
- Daily ingestion (2 AM UTC)
- Daily dbt build (3 AM UTC, after ingestion)
- Weekly forecast training (Monday 4 AM UTC)
"""

from dagster import (
    AssetSelection,
    DefaultScheduleStatus,
    ScheduleDefinition,
    define_asset_job,
)

# ──────────────────────────────────────────────────────────────────
# Asset Jobs (groups of assets that run together)
# ──────────────────────────────────────────────────────────────────

# Ingestion job: Bronze + RAW for all sources
ingestion_job = define_asset_job(
    name="daily_ingestion",
    description="Ingest all sources for one business date",
    selection=AssetSelection.groups("ingestion", "warehouse"),
    tags={
        "dagster/priority": "high",
        "job_type": "ingestion",
    },
)

# dbt job: Seeds → Snapshots → Models → Tests
dbt_job = define_asset_job(
    name="dbt_build",
    description="Build dimensional warehouse (seeds, snapshots, models, tests)",
    selection=AssetSelection.groups("dbt", "staging", "analytics", "semantic"),
    tags={
        "dagster/priority": "medium",
        "job_type": "transformation",
    },
)

# Forecast job: Train models → Publish predictions
forecast_job = define_asset_job(
    name="forecast_training",
    description="Train forecast models and publish predictions",
    selection=AssetSelection.groups("ml"),
    tags={
        "dagster/priority": "low",
        "job_type": "ml",
    },
)

# Full pipeline job: Ingestion → dbt → Forecast (for backfills)
full_pipeline_job = define_asset_job(
    name="full_pipeline",
    description="Run complete pipeline: ingestion → dbt → forecasting",
    selection=AssetSelection.all(),
    tags={
        "job_type": "full_pipeline",
    },
)

# ──────────────────────────────────────────────────────────────────
# Schedules
# ──────────────────────────────────────────────────────────────────

# Daily ingestion at 2 AM UTC
daily_ingestion_schedule = ScheduleDefinition(
    name="daily_ingestion_schedule",
    job=ingestion_job,
    cron_schedule="0 2 * * *",  # 2 AM UTC daily
    default_status=DefaultScheduleStatus.RUNNING,
    description="Ingest all sources daily at 2 AM UTC",
)

# Daily dbt build at 3 AM UTC (after ingestion)
daily_dbt_schedule = ScheduleDefinition(
    name="daily_dbt_schedule",
    job=dbt_job,
    cron_schedule="0 3 * * *",  # 3 AM UTC daily
    default_status=DefaultScheduleStatus.RUNNING,
    description="Build dimensional warehouse daily at 3 AM UTC",
)

# Weekly forecast training (Monday 4 AM UTC)
weekly_forecast_schedule = ScheduleDefinition(
    name="weekly_forecast_schedule",
    job=forecast_job,
    cron_schedule="0 4 * * 1",  # 4 AM UTC Monday
    default_status=DefaultScheduleStatus.RUNNING,
    description="Train forecast models weekly on Monday at 4 AM UTC",
)

# ──────────────────────────────────────────────────────────────────
# On-Demand Jobs (for manual runs)
# ──────────────────────────────────────────────────────────────────

# Backfill job: Run ingestion for a date range
backfill_ingestion_job = define_asset_job(
    name="backfill_ingestion",
    description="Backfill ingestion for a date range (partition-based)",
    selection=AssetSelection.groups("ingestion", "warehouse"),
    tags={
        "job_type": "backfill",
    },
)

# Rebuild warehouse: Re-run dbt from scratch
rebuild_warehouse_job = define_asset_job(
    name="rebuild_warehouse",
    description="Rebuild entire warehouse (drop + rebuild all dbt models)",
    selection=AssetSelection.groups("dbt", "staging", "analytics", "semantic"),
    tags={
        "job_type": "rebuild",
    },
)

# Quality replay: Re-run quality checks on existing partitions
quality_replay_job = define_asset_job(
    name="quality_replay",
    description="Re-run quality checks on existing bronze partitions",
    selection=AssetSelection.groups("ingestion"),
    config={
        "execution": {
            "config": {
                "multiprocess": {
                    "max_concurrent": 4,  # Limit parallelism for quality checks
                }
            }
        }
    },
    tags={
        "job_type": "quality_replay",
    },
)
