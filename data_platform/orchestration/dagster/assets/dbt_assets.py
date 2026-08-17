"""dbt assets for RetailMind data platform.

Uses dagster-dbt to automatically create assets for all 67 dbt models
with correct dependency inference from the dbt DAG.

Asset lineage (from dbt):
    RAW tables (sources)
    ↓
    Staging views (stg_*)
    ↓
    Core tables (dim_*, fct_*)
    ↓
    Metrics tables (mart_*)
    ↓
    Semantic views (v_*)
"""

from pathlib import Path

from dagster import AssetExecutionContext, Output, asset
from dagster_dbt import (
    DagsterDbtTranslator,
    DbtCliResource,
    dbt_assets,
)

from orchestration.dagster.resources import CliExecutor

# Path to dbt project
DBT_PROJECT_DIR = Path(__file__).parents[3] / "dbt"
DBT_PROFILES_DIR = Path(__file__).parents[3]


class RetailMindDbtTranslator(DagsterDbtTranslator):
    """Custom translator for RetailMind dbt assets.

    Maps dbt models to Dagster asset keys with appropriate prefixes:
    - staging models → ["staging", "stg_*"]
    - core models → ["analytics", "dim_*"|"fct_*"]
    - metrics models → ["analytics", "mart_*"]
    - semantic models → ["semantic", "v_*"]
    """

    def get_asset_key(self, dbt_resource_props):
        """Map dbt model to Dagster asset key."""
        resource_type = dbt_resource_props["resource_type"]
        name = dbt_resource_props["name"]

        if resource_type == "source":
            # RAW tables from ingestion
            source_name = dbt_resource_props["source_name"]
            return ["raw", source_name, name]

        # Models - use schema as prefix
        schema = dbt_resource_props.get("schema", "analytics")

        if schema == "staging":
            return ["staging", name]
        elif schema == "semantic":
            return ["semantic", name]
        else:
            # analytics schema (core + metrics)
            return ["analytics", name]

    def get_group_name(self, dbt_resource_props):
        """Organize assets into groups by schema."""
        schema = dbt_resource_props.get("schema", "analytics")
        if schema == "staging":
            return "staging"
        elif schema == "semantic":
            return "semantic"
        else:
            return "analytics"


@dbt_assets(
    manifest=DBT_PROJECT_DIR / "target" / "manifest.json",
    dagster_dbt_translator=RetailMindDbtTranslator(),
    name="retailmind_dbt",
)
def retailmind_dbt_assets(
    context: AssetExecutionContext,
    dbt: DbtCliResource,
):
    """Materialize all dbt assets.

    This single @dbt_assets decorator creates 67 individual Dagster assets
    (one per dbt model) with automatic dependency inference from dbt's DAG.

    The dbt CLI runs:
        dbt build --select {selected_models}

    Which executes models + tests in dependency order.

    Args:
        context: Dagster execution context
        dbt: dbt CLI resource

    Yields:
        AssetMaterialization events for each dbt model
    """
    yield from dbt.cli(["build"], context=context).stream()


# Helper assets for dbt operations that don't produce models


@asset(
    name="dbt_seeds",
    description="Load reference data (calendar, holidays, channel maps)",
    compute_kind="dbt",
    group_name="dbt",
)
def dbt_seeds(
    context: AssetExecutionContext,
    cli: CliExecutor,
) -> Output:
    """Load dbt seeds (reference data).

    Seeds are CSV files in dbt/seeds/ that are loaded as tables:
    - calendar_454.csv
    - holiday_calendar.csv
    - channel_map.csv

    These are prerequisites for dimensional models.
    """
    context.log.info("Loading dbt seeds")

    result = cli.run_dbt("seed")

    return Output(
        value=None,
        metadata={
            "command": "dbt seed",
            "exit_code": result.returncode,
        },
    )


@asset(
    name="dbt_snapshots",
    description="Run SCD2 snapshots for slowly-changing dimensions",
    compute_kind="dbt",
    group_name="dbt",
    deps=["dbt_seeds"],
)
def dbt_snapshots(
    context: AssetExecutionContext,
    cli: CliExecutor,
) -> Output:
    """Run dbt snapshots (SCD Type 2).

    Snapshots track history for dimensions that change over time
    (e.g., product attributes, supplier ratings).
    """
    context.log.info("Running dbt snapshots")

    result = cli.run_dbt("snapshot")

    return Output(
        value=None,
        metadata={
            "command": "dbt snapshot",
            "exit_code": result.returncode,
        },
    )
