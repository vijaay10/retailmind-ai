"""Ingestion assets for RetailMind data platform.

These assets wrap the existing CLI-based ingestion pipeline without
rewriting it. Each asset represents a data artifact (bronze partition
or warehouse table) that is materialized by calling the CLI.

Asset lineage:
    Sources (external files)
    ↓
    Bronze Partitions (parquet + manifest)
    ↓
    RAW Tables (warehouse)
"""

import subprocess

from dagster import (
    AssetExecutionContext,
    AssetIn,
    MetadataValue,
    Output,
    asset,
)

from orchestration.dagster.partitions import daily_partition
from orchestration.dagster.resources import CliExecutor, DuckDBWarehouse

# Source definitions (5 sources in RetailMind)
SOURCES = [
    ("pos", "sales"),
    ("inventory", "positions"),
    ("purchasing", "orders"),
    ("weather", "observations"),
    ("fulfilment", "deliveries"),
]


def make_bronze_asset(source: str, table: str):
    """Factory function to create a bronze partition asset.

    Bronze assets represent parquet partitions written to the landing zone
    by the ingestion CLI. They are materialized by calling:
        retailmind-etl run --source {source} --table {table} --day {date}

    Quality gates are enforced by the CLI. If quality fails, the CLI
    exits with code 1 and the partition is quarantined (not written).
    """

    @asset(
        name=f"bronze_{source}_{table}",
        key_prefix=["bronze", source],
        partitions_def=daily_partition,
        description=f"Bronze partition for {source}.{table} (conformed, quality-checked)",
        compute_kind="ingestion",
        group_name="ingestion",
    )
    def bronze_partition(
        context: AssetExecutionContext,
        cli: CliExecutor,
    ) -> Output:
        """Materialize bronze partition for one business date.

        Calls the existing ingestion CLI which:
        1. Discovers source files for the partition
        2. Extracts and conforms data
        3. Runs quality gate (blocks if failed)
        4. Writes bronze parquet + manifest
        5. Records audit ledger entry

        Returns:
            Output with partition metadata (rows, quality status)

        Raises:
            Exception if quality gate fails (partition quarantined)
        """
        business_date = context.partition_key
        context.log.info(f"Materializing bronze partition: {source}.{table} for {business_date}")

        try:
            result = cli.run_ingestion(
                source=source,
                table=table,
                business_date=business_date,
            )

            # Parse output for metadata
            output = result.stdout
            rows_read = _extract_metric(output, "rows_read")
            rows_landed = _extract_metric(output, "rows_landed")
            rows_rejected = _extract_metric(output, "rows_rejected")

            # Check for quality gate status
            quality_passed = result.returncode == 0

            return Output(
                value=None,  # Asset is the parquet file on disk, not in-memory
                metadata={
                    "business_date": MetadataValue.text(business_date),
                    "rows_read": MetadataValue.int(rows_read),
                    "rows_landed": MetadataValue.int(rows_landed),
                    "rows_rejected": MetadataValue.int(rows_rejected),
                    "quality_passed": MetadataValue.bool(quality_passed),
                    "reject_rate": MetadataValue.float(
                        rows_rejected / rows_read if rows_read > 0 else 0.0
                    ),
                    "partition_path": MetadataValue.path(
                        f".local/lake/bronze/{source}/{table}/dt={business_date}"
                    ),
                },
            )

        except subprocess.CalledProcessError as error:
            # Quality gate failed - partition quarantined
            context.log.error(
                f"Quality gate failed for {source}.{table} {business_date}: {error.stderr}"
            )
            raise

    return bronze_partition


def make_raw_table_asset(source: str, table: str):
    """Factory function to create a RAW warehouse table asset.

    RAW assets represent tables in the warehouse RAW schema. They are
    materialized implicitly by the bronze asset (the CLI writes both
    bronze parquet and loads the warehouse in one transaction).

    This asset exists for dependency tracking - downstream dbt assets
    depend on RAW tables, not bronze partitions.
    """

    @asset(
        name=f"raw_{source}_{table}",
        key_prefix=["raw", source],
        partitions_def=daily_partition,
        description=f"RAW warehouse table for {source}.{table}",
        compute_kind="warehouse_load",
        group_name="warehouse",
        ins={
            "bronze": AssetIn(
                key=["bronze", source, f"bronze_{source}_{table}"],
            ),
        },
    )
    def raw_table(
        context: AssetExecutionContext,
        bronze,  # noqa: ARG001 - dependency only, not used
        warehouse: DuckDBWarehouse,
    ) -> Output:
        """Verify RAW table partition was loaded.

        The bronze asset already loaded this partition into the warehouse.
        This asset just verifies the load succeeded and reports metadata.

        Returns:
            Output with table metadata (row count, data freshness)
        """
        business_date = context.partition_key
        raw_table_name = f"{source}__{table}"

        # Query warehouse to verify partition exists. raw_table_name is a
        # module-internal identifier; business_date is a Dagster-managed
        # daily partition key, not external input.
        query = f"""
            SELECT COUNT(*) as row_count
            FROM raw.{raw_table_name}
            WHERE business_date = '{business_date}'
        """  # noqa: S608

        try:
            result = warehouse.execute_query(query)
            row_count = result[0][0] if result else 0

            return Output(
                value=None,
                metadata={
                    "business_date": MetadataValue.text(business_date),
                    "table": MetadataValue.text(f"raw.{raw_table_name}"),
                    "row_count": MetadataValue.int(row_count),
                    "partition_loaded": MetadataValue.bool(row_count > 0),
                },
            )

        except Exception as error:
            context.log.error(
                f"Failed to verify RAW table {raw_table_name} for {business_date}: {error}"
            )
            raise

    return raw_table


def _extract_metric(output: str, metric_name: str) -> int:
    """Extract a numeric metric from CLI output.

    The CLI logs structured output like:
        rows_read=1000
        rows_landed=998
        rows_rejected=2

    This parses those lines and extracts the values.
    """
    for line in output.split("\n"):
        if f"{metric_name}=" in line:
            try:
                return int(line.split("=")[1].strip())
            except (IndexError, ValueError):
                pass
    return 0


# Generate all bronze and RAW assets
bronze_assets = [make_bronze_asset(source, table) for source, table in SOURCES]
raw_assets = [make_raw_table_asset(source, table) for source, table in SOURCES]
