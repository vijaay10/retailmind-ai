"""Dagster definitions for RetailMind data platform.

This module defines all Dagster assets, jobs, schedules, sensors, and
resources for the RetailMind data pipeline.

Entry point for Dagster:
    dagster dev -f orchestration/dagster/__init__.py
"""

from pathlib import Path

from dagster_dbt import DbtCliResource

from dagster import Definitions

from .assets.dbt_assets import dbt_seeds, dbt_snapshots, retailmind_dbt_assets
from .assets.ingestion import bronze_assets, raw_assets
from .assets.ml import forecast_explanations, forecast_models, forecast_predictions
from .checks import (
    forecast_predictions_coverage,
    freshness_checks,
    mart_sales_daily_freshness,
    row_count_checks,
)
from .resources import AuditLedger, CliExecutor, DuckDBWarehouse
from .schedules import (
    backfill_ingestion_job,
    daily_dbt_schedule,
    daily_ingestion_schedule,
    dbt_job,
    forecast_job,
    full_pipeline_job,
    ingestion_job,
    quality_replay_job,
    rebuild_warehouse_job,
    weekly_forecast_schedule,
)
from .sensors import failed_partition_retry_sensor, quality_quarantine_alert_sensor

# ──────────────────────────────────────────────────────────────────
# Resource Configuration
# ──────────────────────────────────────────────────────────────────

# Default paths (override via env vars or launchpad)
DATA_PLATFORM_DIR = Path(__file__).parents[2]
REPO_ROOT = DATA_PLATFORM_DIR.parent  # Repository root (CLI expects to run from here)
DBT_PROJECT_DIR = DATA_PLATFORM_DIR / "dbt"
DBT_PROFILES_DIR = DBT_PROJECT_DIR  # profiles.yml is in dbt directory
WAREHOUSE_PATH = REPO_ROOT / ".local" / "retailmind.duckdb"

resources = {
    "warehouse": DuckDBWarehouse(database_path=str(WAREHOUSE_PATH)),
    "cli": CliExecutor(working_directory=str(REPO_ROOT)),
    "dbt": DbtCliResource(
        project_dir=str(DBT_PROJECT_DIR),
        profiles_dir=str(DBT_PROFILES_DIR),
    ),
    "audit_ledger": AuditLedger(
        postgres_dsn="postgresql://localhost/retailmind",
        enabled=False,  # Enable when Postgres is available
    ),
}

# ──────────────────────────────────────────────────────────────────
# Dagster Definitions
# ──────────────────────────────────────────────────────────────────

defs = Definitions(
    # Assets
    assets=[
        # Ingestion assets (bronze + RAW)
        *bronze_assets,
        *raw_assets,
        # dbt assets (staging + analytics + semantic)
        retailmind_dbt_assets,
        dbt_seeds,
        dbt_snapshots,
        # ML assets (forecast training + predictions)
        forecast_models,
        forecast_predictions,
        forecast_explanations,
    ],
    # Asset checks (data quality)
    asset_checks=[
        *row_count_checks,
        *freshness_checks,
        mart_sales_daily_freshness,
        forecast_predictions_coverage,
    ],
    # Jobs (collections of assets)
    jobs=[
        ingestion_job,
        dbt_job,
        forecast_job,
        full_pipeline_job,
        backfill_ingestion_job,
        rebuild_warehouse_job,
        quality_replay_job,
    ],
    # Schedules (automated runs)
    schedules=[
        daily_ingestion_schedule,
        daily_dbt_schedule,
        weekly_forecast_schedule,
    ],
    # Sensors (event-driven)
    sensors=[
        failed_partition_retry_sensor,
        quality_quarantine_alert_sensor,
    ],
    # Resources (external systems)
    resources=resources,
)
