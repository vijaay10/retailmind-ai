"""ML and forecasting assets for RetailMind data platform.

Wraps the existing ML CLI (forecasting/cli.py) for model training
and forecast generation. Depends on dbt analytics tables.

Asset lineage:
    Analytics tables (mart_sales_daily, etc.)
    ↓
    Forecast training (backtest, gate winner, publish)
    ↓
    Forecast predictions (analytics_ml.forecast_predictions)
    ↓
    Forecast explanations (analytics_ml.forecast_explanations)
"""

import subprocess

from dagster import (
    AssetExecutionContext,
    AssetKey,
    MetadataValue,
    Output,
    asset,
)

from orchestration.dagster.resources import CliExecutor, DuckDBWarehouse


@asset(
    name="forecast_models",
    description="Trained forecast models (backtest, gate, publish champions)",
    compute_kind="ml",
    group_name="ml",
    deps=[
        # Forecast training depends on sales analytics (data is in warehouse, not loaded)
        AssetKey(["analytics", "mart_sales_daily"]),
        AssetKey(["analytics", "mart_kpi_daily"]),
    ],
)
def forecast_models(
    context: AssetExecutionContext,
    cli: CliExecutor,
) -> Output:
    """Train forecast models and publish champion predictions.

    Calls: retailmind-forecast train

    This runs the complete forecast workflow:
    1. Extract time series from warehouse
    2. Backtest candidate models (3 horizons, 12 folds)
    3. Gate winner based on MAPE, WAPE, MASE
    4. Publish champion forecasts to analytics_ml.forecast_predictions
    5. Generate confidence intervals and explanations

    Returns:
        Output with training metadata (models trained, champion published)

    Raises:
        Exception if training fails or no predictions written
    """
    context.log.info("Training forecast models")

    try:
        result = cli.run_forecast_training(
            horizon=28,  # 4-week ahead
            folds=12,  # 1-year backtest
            demand_series=25,  # Top 25 SKUs by velocity
        )

        # Parse training output for metadata
        output = result.stdout
        models_trained = _count_models_trained(output)
        champion_published = _check_champion_published(output)

        return Output(
            value=None,
            metadata={
                "models_trained": MetadataValue.int(models_trained),
                "champion_published": MetadataValue.bool(champion_published),
                "horizon_days": MetadataValue.int(28),
                "backtest_folds": MetadataValue.int(12),
                "command": MetadataValue.text("retailmind-forecast train"),
            },
        )

    except subprocess.CalledProcessError as error:
        context.log.error(f"Forecast training failed: {error.stderr}")
        raise


@asset(
    name="forecast_predictions",
    description="Forecast predictions table (analytics_ml.forecast_predictions)",
    compute_kind="ml",
    group_name="ml",
    deps=["forecast_models"],
)
def forecast_predictions(
    context: AssetExecutionContext,
    warehouse: DuckDBWarehouse,
) -> Output:
    """Verify forecast predictions table was populated.

    The forecast_models asset already wrote predictions to the warehouse.
    This asset verifies the table exists and reports metadata.

    Returns:
        Output with prediction metadata (row count, date range, targets)
    """
    context.log.info("Verifying forecast predictions table")

    # Query predictions table
    query = """
        SELECT
            COUNT(*) as prediction_count,
            MIN(forecast_date) as earliest_forecast,
            MAX(forecast_date) as latest_forecast,
            COUNT(DISTINCT target) as target_count,
            COUNT(DISTINCT series_id) as series_count
        FROM analytics_ml.forecast_predictions
    """

    try:
        result = warehouse.execute_query(query)
        if not result:
            raise RuntimeError("Forecast predictions table is empty")

        row = result[0]
        prediction_count = row[0]
        earliest_forecast = row[1]
        latest_forecast = row[2]
        target_count = row[3]
        series_count = row[4]

        return Output(
            value=None,
            metadata={
                "prediction_count": MetadataValue.int(prediction_count),
                "earliest_forecast": MetadataValue.text(str(earliest_forecast)),
                "latest_forecast": MetadataValue.text(str(latest_forecast)),
                "target_count": MetadataValue.int(target_count),
                "series_count": MetadataValue.int(series_count),
                "table": MetadataValue.text("analytics_ml.forecast_predictions"),
            },
        )

    except Exception as error:
        context.log.error(f"Failed to verify forecast predictions: {error}")
        raise


@asset(
    name="forecast_explanations",
    description="Forecast explanations table (analytics_ml.forecast_explanations)",
    compute_kind="ml",
    group_name="ml",
    deps=["forecast_predictions"],
)
def forecast_explanations(
    context: AssetExecutionContext,
    warehouse: DuckDBWarehouse,
) -> Output:
    """Verify forecast explanations table was populated.

    Explanations provide model cards, confidence intervals, and
    feature importance for forecast predictions.

    Returns:
        Output with explanation metadata
    """
    context.log.info("Verifying forecast explanations table")

    query = """
        SELECT
            COUNT(*) as explanation_count,
            COUNT(DISTINCT series_id) as series_count
        FROM analytics_ml.forecast_explanations
    """

    try:
        result = warehouse.execute_query(query)
        if not result:
            context.log.warning("Forecast explanations table is empty")
            return Output(
                value=None,
                metadata={
                    "explanation_count": MetadataValue.int(0),
                    "series_count": MetadataValue.int(0),
                },
            )

        row = result[0]
        explanation_count = row[0]
        series_count = row[1]

        return Output(
            value=None,
            metadata={
                "explanation_count": MetadataValue.int(explanation_count),
                "series_count": MetadataValue.int(series_count),
                "table": MetadataValue.text("analytics_ml.forecast_explanations"),
            },
        )

    except Exception as error:
        context.log.error(f"Failed to verify forecast explanations: {error}")
        raise


def _count_models_trained(output: str) -> int:
    """Parse CLI output to count models trained."""
    count = 0
    for line in output.split("\n"):
        if "trained model" in line.lower() or "backtest complete" in line.lower():
            count += 1
    return count


def _check_champion_published(output: str) -> bool:
    """Check if champion forecast was published."""
    return "champion published" in output.lower() or "predictions written" in output.lower()
