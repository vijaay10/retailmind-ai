"""Asset checks for RetailMind data platform.

Dagster asset checks verify data quality and freshness without
blocking materialization. They run after assets materialize and
surface quality issues in the UI.

The existing quality gate (in ingestion CLI) is BLOCKING and runs
before bronze partitions are written. These checks are ADVISORY and
run after to provide observability.
"""

from dagster import (
    AssetCheckExecutionContext,
    AssetCheckResult,
    AssetCheckSeverity,
    asset_check,
)

from .resources import DuckDBWarehouse


def make_row_count_check(source: str, table: str):
    """Factory for row count checks on RAW tables.

    Verifies that each partition has > 0 rows (not empty).
    This catches issues where files arrived but were all rejected.
    """

    @asset_check(
        asset=f"raw_{source}_{table}",
        name=f"raw_{source}_{table}_has_rows",
        description=f"Verify {source}.{table} partition has rows",
    )
    def row_count_check(
        context: AssetCheckExecutionContext,
        warehouse: DuckDBWarehouse,
    ) -> AssetCheckResult:
        """Check that partition has > 0 rows."""
        business_date = context.partition_key
        raw_table_name = f"{source}__{table}"

        # raw_table_name is a module-internal identifier; business_date is a
        # Dagster-managed daily partition key (YYYY-MM-DD), not external
        # input. DuckDBWarehouse.execute_query takes a plain string, so this
        # is interpolated rather than bound.
        query = f"""
            SELECT COUNT(*) as row_count
            FROM raw.{raw_table_name}
            WHERE business_date = '{business_date}'
        """  # noqa: S608

        try:
            result = warehouse.execute_query(query)
            row_count = result[0][0] if result else 0

            passed = row_count > 0

            return AssetCheckResult(
                passed=passed,
                severity=AssetCheckSeverity.ERROR,
                metadata={
                    "row_count": row_count,
                    "business_date": business_date,
                    "table": f"raw.{raw_table_name}",
                },
                description=f"Partition has {row_count} rows"
                if passed
                else "Partition is empty (0 rows)",
            )

        except Exception as error:
            return AssetCheckResult(
                passed=False,
                severity=AssetCheckSeverity.ERROR,
                description=f"Failed to check row count: {error}",
            )

    return row_count_check


def make_freshness_check(source: str, table: str):
    """Factory for data freshness checks.

    Verifies that the latest partition is recent (within 2 days of today).
    This catches issues where the pipeline is running but not producing new data.
    """

    @asset_check(
        asset=f"raw_{source}_{table}",
        name=f"raw_{source}_{table}_is_fresh",
        description=f"Verify {source}.{table} has recent data",
    )
    def freshness_check(
        context: AssetCheckExecutionContext,
        warehouse: DuckDBWarehouse,
    ) -> AssetCheckResult:
        """Check that latest partition is recent."""
        raw_table_name = f"{source}__{table}"

        query = f"""
            SELECT MAX(business_date) as latest_partition
            FROM raw.{raw_table_name}
        """  # noqa: S608 — raw_table_name is a module-internal identifier, not external input

        try:
            result = warehouse.execute_query(query)
            if not result or result[0][0] is None:
                return AssetCheckResult(
                    passed=False,
                    severity=AssetCheckSeverity.ERROR,
                    description="Table is empty (no partitions)",
                )

            latest_partition = result[0][0]
            # Calculate days behind (using SQL DATE arithmetic)
            age_query = f"SELECT CURRENT_DATE - DATE '{latest_partition}' as days_behind"
            age_result = warehouse.execute_query(age_query)
            days_behind = age_result[0][0] if age_result else 999

            # Fresh if within 2 days
            passed = days_behind <= 2

            return AssetCheckResult(
                passed=passed,
                severity=AssetCheckSeverity.WARN if days_behind <= 7 else AssetCheckSeverity.ERROR,
                metadata={
                    "latest_partition": str(latest_partition),
                    "days_behind": days_behind,
                    "table": f"raw.{raw_table_name}",
                },
                description=f"Latest partition is {days_behind} day(s) old"
                if passed
                else f"Data is stale ({days_behind} days old)",
            )

        except Exception as error:
            return AssetCheckResult(
                passed=False,
                severity=AssetCheckSeverity.ERROR,
                description=f"Failed to check freshness: {error}",
            )

    return freshness_check


# Generate checks for all sources
SOURCES = [
    ("pos", "sales"),
    ("inventory", "positions"),
    ("purchasing", "orders"),
    ("weather", "observations"),
    ("fulfilment", "deliveries"),
]

row_count_checks = [make_row_count_check(source, table) for source, table in SOURCES]
freshness_checks = [make_freshness_check(source, table) for source, table in SOURCES]


# Analytics-level checks


@asset_check(
    asset="mart_sales_daily",
    name="mart_sales_daily_has_recent_data",
    description="Verify sales analytics table has recent data",
)
def mart_sales_daily_freshness(
    context: AssetCheckExecutionContext,
    warehouse: DuckDBWarehouse,
) -> AssetCheckResult:
    """Check that mart_sales_daily has recent data."""
    query = """
        SELECT MAX(business_date) as latest_date
        FROM analytics.mart_sales_daily
    """

    try:
        result = warehouse.execute_query(query)
        if not result or result[0][0] is None:
            return AssetCheckResult(
                passed=False,
                severity=AssetCheckSeverity.ERROR,
                description="mart_sales_daily is empty",
            )

        latest_date = result[0][0]
        age_query = f"SELECT CURRENT_DATE - DATE '{latest_date}' as days_behind"
        age_result = warehouse.execute_query(age_query)
        days_behind = age_result[0][0] if age_result else 999

        passed = days_behind <= 2

        return AssetCheckResult(
            passed=passed,
            severity=AssetCheckSeverity.WARN if days_behind <= 7 else AssetCheckSeverity.ERROR,
            metadata={
                "latest_date": str(latest_date),
                "days_behind": days_behind,
            },
            description=f"Latest data is {days_behind} day(s) old",
        )

    except Exception as error:
        return AssetCheckResult(
            passed=False,
            severity=AssetCheckSeverity.ERROR,
            description=f"Failed to check freshness: {error}",
        )


@asset_check(
    asset="forecast_predictions",
    name="forecast_predictions_coverage",
    description="Verify forecast covers all required targets",
)
def forecast_predictions_coverage(
    context: AssetCheckExecutionContext,
    warehouse: DuckDBWarehouse,
) -> AssetCheckResult:
    """Check that forecasts cover all targets (revenue, units)."""
    query = """
        SELECT
            COUNT(DISTINCT target) as target_count,
            array_agg(DISTINCT target) as targets
        FROM analytics_ml.forecast_predictions
    """

    try:
        result = warehouse.execute_query(query)
        if not result:
            return AssetCheckResult(
                passed=False,
                severity=AssetCheckSeverity.ERROR,
                description="No forecast predictions found",
            )

        target_count = result[0][0]
        # DuckDB returns array as list
        # For now just check count
        expected_targets = 2  # revenue, units

        passed = target_count >= expected_targets

        return AssetCheckResult(
            passed=passed,
            severity=AssetCheckSeverity.ERROR,
            metadata={
                "target_count": target_count,
                "expected_targets": expected_targets,
            },
            description=f"Forecast covers {target_count} targets"
            if passed
            else f"Forecast missing targets (found {target_count}, expected {expected_targets})",
        )

    except Exception as error:
        return AssetCheckResult(
            passed=False,
            severity=AssetCheckSeverity.ERROR,
            description=f"Failed to check forecast coverage: {error}",
        )
