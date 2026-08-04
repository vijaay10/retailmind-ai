"""Operator CLI: run, backfill, replay, inspect (ETL design §31).

Backfill is deliberately *the same code path* as a nightly run with a
different window — there is no separate backfill logic to rot, which is the
whole payoff of making windows the unit of work.
"""

# ruff: noqa: S608 — SQL is composed from schema identifiers validated at
# load time (SourceSchema.validate) and from module constants; every value
# originating in data is bound as a parameter.

import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Annotated

import structlog
import typer
from duckdb import DuckDBPyConnection

from ingestion.audit.ledger import AuditLedger
from ingestion.connectors.csv_files import CsvFileConnector
from ingestion.core.config import EtlSettings
from ingestion.core.duck import connect
from ingestion.core.logging import configure_logging
from ingestion.domain.schema import SourceSchema
from ingestion.domain.window import Window
from ingestion.generators import inventory_files, purchase_orders
from ingestion.generators.pos_files import generate_day
from ingestion.landing.writer import committed_partitions
from ingestion.pipeline import IngestionPipeline, RunSummary

app = typer.Typer(help="RetailMind ingestion pipeline", no_args_is_help=True)
log = structlog.get_logger(__name__)

SCHEMA_ROOT = Path(__file__).resolve().parents[1] / "schemas"


def _load(source: str, table: str) -> SourceSchema:
    return SourceSchema.from_yaml(SCHEMA_ROOT / source / f"{table}.yml")


def _build_pipeline(
    schema: SourceSchema,
    settings: EtlSettings,
    *,
    dsn: str | None,
    expected_units: int | None = None,
) -> tuple[IngestionPipeline, DuckDBPyConnection]:
    settings.warehouse_path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(settings.warehouse_path)
    connector = CsvFileConnector(
        schema=schema,
        settings=settings,
        connection=conn,
        # How many per-store files constitute a complete day is *tenant
        # configuration*, not part of the source's data contract — a chain that
        # opens stores changes this number without changing its schema. The
        # schema value is the default; the caller may override it.
        expected_units=(
            expected_units
            if expected_units is not None
            else int(schema.metadata.get("expected_units", 0))
        ),
    )
    pipeline = IngestionPipeline(
        connector=connector,
        settings=settings,
        connection=conn,
        ledger=AuditLedger(dsn) if dsn else None,
        tenant_id=uuid.UUID(int=0) if dsn else None,
        connector_id=uuid.UUID(int=0) if dsn else None,
    )
    return pipeline, conn


def _report(summary: RunSummary) -> None:
    typer.echo(f"\n{summary.source}.{summary.table} {summary.window} → {summary.status}")
    typer.echo(
        f"  read {summary.rows_read:,} · loaded {summary.rows_loaded:,} "
        f"· rejected {summary.rows_rejected:,}"
    )
    for outcome in summary.outcomes:
        marker = {"loaded": "✓", "quarantined": "✗", "skipped": "·", "empty": "·"}[outcome.status]
        detail = f" [{', '.join(outcome.failed_rules)}]" if outcome.failed_rules else ""
        typer.echo(f"  {marker} {outcome.partition} {outcome.status}{detail}")


@app.command()
def generate(
    day: Annotated[str | None, typer.Option(help="Business date; default yesterday")] = None,
    days: Annotated[int, typer.Option(help="Days of history ending at --day")] = 1,
    stores: Annotated[int, typer.Option(help="How many per-store files to write")] = 120,
    seed: Annotated[int, typer.Option(help="Deterministic generation seed")] = 7,
) -> None:
    """Write synthetic POS drops into the inbox (dev/demo only).

    Deterministic for a given seed, and deliberately imperfect: a couple of
    unusable rows and a re-sent line are planted so the reject and dedup paths
    are exercised on every run.
    """
    configure_logging("INFO", json_output=False)
    settings = EtlSettings()
    business_date = date.fromisoformat(day) if day else date.today() - timedelta(days=1)

    # Each day carries its own seed offset, so the series varies day to day
    # while staying reproducible for a given --seed. A flat series would make
    # trends, growth, and forecasts meaningless to demo against.
    sales_rows = sales_files = position_rows = order_rows = 0
    for offset in range(days):
        current = business_date - timedelta(days=days - 1 - offset)
        batch = generate_day(settings.inbox_dir("pos"), current, stores=stores, seed=seed + offset)
        positions = inventory_files.generate_day(
            settings.inbox_dir("inventory"), current, stores=stores, seed=seed + 600 + offset
        )
        orders = purchase_orders.generate_day(
            settings.inbox_dir("purchasing"),
            current,
            stores=stores,
            seed=seed + 900 + offset,
            # The horizon is the last day the warehouse knows about, so lines
            # that would arrive later stay legitimately open.
            as_of=business_date,
        )
        sales_rows += batch.rows
        sales_files += len(batch.files)
        position_rows += positions.rows
        order_rows += orders.rows

    first_day = business_date - timedelta(days=days - 1)
    span = business_date.isoformat() if days == 1 else f"{first_day} → {business_date}"
    typer.echo(f"generated {span} ({days} day(s) x {stores} stores)")
    typer.echo(f"  pos.sales:           {sales_rows:,} rows in {sales_files:,} files")
    typer.echo(f"  inventory.positions: {position_rows:,} rows")
    typer.echo(f"  purchasing.orders:   {order_rows:,} rows")


@app.command()
def run(
    source: Annotated[str, typer.Option(help="Source key, e.g. 'pos'")] = "pos",
    table: Annotated[str, typer.Option(help="Table key, e.g. 'sales'")] = "sales",
    day: Annotated[
        str | None, typer.Option(help="Business date (YYYY-MM-DD); default yesterday")
    ] = None,
    dsn: Annotated[str | None, typer.Option(help="Postgres DSN for the audit ledger")] = None,
    expected_stores: Annotated[
        int | None, typer.Option(help="Override the expected per-store file count")
    ] = None,
    verbose: Annotated[bool, typer.Option(help="Human-readable logs")] = False,
) -> None:
    """Ingest one business date."""
    configure_logging("DEBUG" if verbose else "INFO", json_output=not verbose)
    business_date = date.fromisoformat(day) if day else date.today() - timedelta(days=1)

    schema = _load(source, table)
    pipeline, conn = _build_pipeline(schema, EtlSettings(), dsn=dsn, expected_units=expected_stores)
    try:
        summary = pipeline.run(
            Window.for_day(business_date), dag_run_id=f"cli__{datetime.now():%Y%m%dT%H%M%S}"
        )
    finally:
        conn.close()

    _report(summary)
    if summary.quarantined:
        raise typer.Exit(code=1)


@app.command()
def backfill(
    start: Annotated[str, typer.Argument(help="First business date (inclusive)")],
    end: Annotated[str, typer.Argument(help="Last business date (exclusive)")],
    source: Annotated[str, typer.Option()] = "pos",
    table: Annotated[str, typer.Option()] = "sales",
    dsn: Annotated[str | None, typer.Option()] = None,
    expected_stores: Annotated[
        int | None, typer.Option(help="Override the expected per-store file count")
    ] = None,
) -> None:
    """Reprocess a historical window.

    Identical to a nightly run apart from the window: partitions are
    overwritten, so running this twice is safe and produces identical output.
    """
    configure_logging("INFO")
    window = Window(date.fromisoformat(start), date.fromisoformat(end))

    schema = _load(source, table)
    pipeline, conn = _build_pipeline(schema, EtlSettings(), dsn=dsn, expected_units=expected_stores)
    try:
        summary = pipeline.run(window, dag_run_id=f"backfill__{start}_{end}")
    finally:
        conn.close()

    _report(summary)
    if summary.quarantined:
        raise typer.Exit(code=1)


@app.command()
def status(
    source: Annotated[str, typer.Option()] = "pos",
    table: Annotated[str, typer.Option()] = "sales",
) -> None:
    """List committed partitions with their landing statistics."""
    settings = EtlSettings()
    schema = _load(source, table)
    manifests = committed_partitions(settings, schema)

    if not manifests:
        typer.echo(f"no committed partitions for {source}.{table}")
        return

    typer.echo(f"{'partition':<12} {'read':>10} {'landed':>10} {'rejected':>9}  schema")
    for partition, manifest in sorted(manifests.items()):
        typer.echo(
            f"{partition:<12} {manifest.rows_read:>10,} {manifest.rows_landed:>10,} "
            f"{manifest.rows_rejected:>9,}  v{manifest.schema_version}"
        )


@app.command("rejects")
def rejects_report(
    partition: Annotated[str, typer.Argument(help="Business date (YYYY-MM-DD)")],
    source: Annotated[str, typer.Option()] = "pos",
    table: Annotated[str, typer.Option()] = "sales",
) -> None:
    """Summarize why rows were rejected — the source-owner conversation."""
    settings = EtlSettings()
    schema = _load(source, table)
    path = settings.rejects_dir(source, table, partition) / "rejects.parquet"

    if not path.exists():
        typer.echo(f"no rejects recorded for {schema.source}.{schema.table} {partition}")
        return

    conn = connect()
    try:
        rows = conn.execute(
            f"SELECT _reject_reason, count(*) AS n FROM read_parquet('{path}') "
            "GROUP BY 1 ORDER BY n DESC"
        ).fetchall()
    finally:
        conn.close()

    typer.echo(f"rejects for {source}.{table} {partition}:")
    for reason, count in rows:
        typer.echo(f"  {count:>8,}  {reason}")


if __name__ == "__main__":  # pragma: no cover
    app()
