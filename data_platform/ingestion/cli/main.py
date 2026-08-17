"""Operator CLI: run, backfill, replay, inspect.

Backfill is deliberately *the same code path* as a nightly run with a
different window — there is no separate backfill logic to rot, which is the
whole payoff of making windows the unit of work.
"""

# ruff: noqa: S608 — SQL is composed from schema identifiers validated at
# load time (SourceSchema.validate) and from module constants; every value
# originating in data is bound as a parameter.

import shutil
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
from ingestion.generators import fulfilment, inventory_files, purchase_orders, weather
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
    weather_rows = delivery_rows = 0
    for offset in range(days):
        current = business_date - timedelta(days=days - 1 - offset)
        # history_start/end let the POS generator apply the shared incident
        # calendar: the same storm that suppresses transactions here shows up
        # as a severe flag in the weather feed.
        batch = generate_day(
            settings.inbox_dir("pos"),
            current,
            stores=stores,
            seed=seed + offset,
            history_start=business_date - timedelta(days=days - 1),
            history_end=business_date,
        )
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
        observations = weather.generate_day(
            settings.inbox_dir("weather"),
            current,
            seed=seed + 41 + offset,
            history_end=business_date,
        )
        deliveries = fulfilment.generate_day(
            settings.inbox_dir("fulfilment"),
            current,
            stores=stores,
            seed=seed + 55 + offset,
            history_end=business_date,
        )
        sales_rows += batch.rows
        sales_files += len(batch.files)
        position_rows += positions.rows
        order_rows += orders.rows
        weather_rows += observations.rows
        delivery_rows += deliveries.rows

    first_day = business_date - timedelta(days=days - 1)
    span = business_date.isoformat() if days == 1 else f"{first_day} → {business_date}"
    typer.echo(f"generated {span} ({days} day(s) x {stores} stores)")
    typer.echo(f"  pos.sales:           {sales_rows:,} rows in {sales_files:,} files")
    typer.echo(f"  inventory.positions: {position_rows:,} rows")
    typer.echo(f"  purchasing.orders:   {order_rows:,} rows")
    typer.echo(f"  weather.observations:{weather_rows:,} rows")
    typer.echo(f"  fulfilment.deliveries: {delivery_rows:,} rows")


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


@app.command("demo-warehouse")
def demo_warehouse(
    out: Annotated[Path, typer.Option(help="Where to write the warehouse")] = Path(
        ".local/demo/retailmind.duckdb"
    ),
    days: Annotated[int, typer.Option(help="Days of history to generate")] = 0,
    stores: Annotated[int, typer.Option(help="Stores in the estate")] = 0,
) -> None:
    """Build a demo warehouse from nothing: generate, ingest, dbt.

    This is what `make demo` runs. Zero for --days or --stores means "use the
    tuned demo shape", which is sized against a stopwatch — see
    `ingestion.demo.DEMO`.

    Idempotent in the way that matters for a demo: it builds into a scratch
    tree and moves the finished file into place, so an interrupted run leaves
    the previous warehouse intact rather than a half-built one that fails at
    query time instead of build time.
    """
    from ingestion.demo import DEMO, Shape, build

    shape = Shape(
        days=days or DEMO.days,
        stores=stores or DEMO.stores,
        lines_per_store=DEMO.lines_per_store,
        skus_per_store=DEMO.skus_per_store,
    )
    out = out.resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    scratch = out.parent / f".build-{shape.slug}"
    if scratch.exists():
        shutil.rmtree(scratch)
    scratch.mkdir(parents=True)

    typer.echo(f"building demo warehouse: {shape.days} days, {shape.stores} stores")
    # Built under its final name, not renamed into place. DuckDB derives the
    # catalog name from the file name and dbt compiles it into every view, so a
    # rename turns a working warehouse into `Catalog "wh" does not exist` on
    # the first query the API makes.
    built = build(shape, scratch, filename=out.name)
    built.replace(out)
    shutil.rmtree(scratch, ignore_errors=True)

    verify(out)
    typer.echo(f"→ {out} ({out.stat().st_size / 1e6:.0f} MB)")


@app.command("onboard")
def onboard(
    path: Annotated[Path, typer.Argument(help="A CSV file from a company being onboarded")],
    source: Annotated[
        str | None,
        typer.Option(help="Skip detection and validate against a known source (e.g. 'pos')"),
    ] = None,
    table: Annotated[str | None, typer.Option(help="Paired with --source")] = None,
    tenant: Annotated[
        str | None,
        typer.Option(help="Tenant slug to import into. Required with --confirm-import"),
    ] = None,
    confirm_import: Annotated[
        bool,
        typer.Option(
            "--confirm-import",
            help="Import after validating. Without this the command only reports.",
        ),
    ] = False,
    root: Annotated[Path, typer.Option(help="Where tenant lakes and warehouses live")] = Path(
        ".local"
    ),
) -> None:
    """Detect, map, and validate an arbitrary uploaded file — Prompt 12.

    The real entry point for "a new company uploads their data": no code
    change, no new schema file, for any source whose shape resembles one of
    the declared canonical schemas under `ingestion/schemas/`. Prints a
    business-language report — the same three-step pipeline
    (`onboarding.detect_dataset_type` → `suggest_column_mapping` →
    `validate_mapped_dataset`) the onboarding UI's "Data Sources" workspace
    describes, run for real here rather than only described there.
    """
    import csv

    from onboarding import detect_dataset_type, suggest_column_mapping, validate_mapped_dataset
    from onboarding.matching import load_known_schemas

    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        typer.echo(f"{path}: no rows found")
        raise typer.Exit(code=1)
    columns = list(rows[0].keys())

    if source and table:
        schema = _load(source, table)
        typer.echo(f"Validating against {source}.{table} (skipping detection, as requested)\n")
    else:
        typer.echo(f"Detected columns: {', '.join(columns)}\n")
        results = detect_dataset_type(columns, rows[:20])
        typer.echo("Dataset detection:")
        for result in results[:3]:
            pct = f"{result.confidence:.0%}"
            typer.echo(f"  {result.source}.{result.table:<12} confidence {pct:>4}")
        if not results or results[0].confidence < 0.4:
            typer.echo(
                "\nNo declared schema matches with reasonable confidence — "
                "this file's dataset type could not be determined."
            )
            raise typer.Exit(code=1)
        best = results[0]
        typer.echo(f"\n→ Best match: {best.source}.{best.table} ({best.confidence:.0%})\n")
        schema = next(
            s for s in load_known_schemas() if s.source == best.source and s.table == best.table
        )

    typer.echo("Column mapping:")
    mapping = suggest_column_mapping(columns, schema)
    for suggestion in mapping:
        target = suggestion.canonical_field or "(unmapped)"
        typer.echo(f"  {suggestion.source_column:<20} → {target:<20} {suggestion.reason}")

    rename = {m.source_column: m.canonical_field for m in mapping if m.canonical_field}
    mapped_rows = [
        {rename.get(key, key): value for key, value in row.items() if key in rename} for row in rows
    ]

    typer.echo("\nValidation:")
    report = validate_mapped_dataset(mapped_rows, schema)
    typer.echo(f"  ✓ {report.total_records:,} records detected")
    typer.echo(f"  ✓ {report.valid_pct:.1f}% valid records")
    for issue in report.issues:
        mark = "✕" if issue.severity == "error" else "⚠"
        typer.echo(f"  {mark} {issue.message}")

    # What counts as "blocking" defers to the pipeline's own policy rather
    # than inventing a stricter one here. Ingestion already rejects bad rows
    # and quarantines the batch only when the reject rate exceeds
    # `reject_rate_threshold`; a gate that refused any file with a single bad
    # row would be a second, harsher data-quality rule sitting in front of the
    # real one, and would reject files the platform is designed to handle.
    reject_rate = 1.0 - (report.valid_pct / 100.0)
    threshold = EtlSettings().reject_rate_threshold
    structural = report.valid_records == 0
    over_threshold = reject_rate > threshold
    blocking = structural or over_threshold

    # Step 4 of the customer journey: the report is shown, and importing is a
    # separate, explicit decision. Reporting and importing in one step would
    # mean a file is in the warehouse before anyone has read why it should not
    # be.
    if not confirm_import:
        typer.echo("")
        if structural:
            typer.echo("Fix these issues before importing — no record is currently usable.")
        elif over_threshold:
            typer.echo(
                f"Fix these issues before importing — {reject_rate:.1%} of records have "
                f"errors, above the {threshold:.1%} the pipeline accepts, so the whole "
                "batch would be quarantined rather than loaded."
            )
        else:
            typer.echo(
                f"Ready to import. {report.valid_records:,} of {report.total_records:,} "
                f"records are usable; the rest will be rejected and reported."
            )
            typer.echo("  Re-run with:  --tenant <slug> --confirm-import")
        return

    if tenant is None:
        typer.echo("\n--confirm-import needs --tenant <slug>: data belongs to a company.", err=True)
        raise typer.Exit(code=2)
    if blocking:
        reason = (
            "no record is usable"
            if structural
            else f"{reject_rate:.1%} of records have errors, above the {threshold:.1%} limit"
        )
        typer.echo(f"\nRefusing to import: {reason}. Nothing was written.", err=True)
        raise typer.Exit(code=1)

    from onboarding.importing import ImportRefusedError, import_mapped_rows, tenant_paths

    typer.echo(f"\nImporting into '{tenant}'...")
    try:
        result = import_mapped_rows(
            mapped_rows, schema, tenant_slug=tenant, settings=tenant_paths(tenant, root)
        )
    except ImportRefusedError as exc:
        typer.echo(f"  Import refused: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo("\nImport result:")
    if result.unchanged:
        typer.echo("  Status             Already imported — this exact file is already in place")
    elif result.replaced_existing:
        typer.echo("  Status             Imported, replacing a previous upload for these dates")
    else:
        typer.echo("  Status             Imported")
    typer.echo(f"  Dataset            {result.dataset}")
    typer.echo(f"  Company            {result.tenant_slug}")
    typer.echo(f"  Dates covered      {result.partitions[0]} to {result.partitions[-1]}")
    typer.echo(f"  Rows accepted      {result.rows_loaded:,}")
    typer.echo(f"  Rows rejected      {result.rows_rejected:,}")
    if result.replaced_existing:
        typer.echo(f"  Rows replaced      {result.rows_replaced:,} (re-import of the same dates)")
    for warning in result.warnings:
        typer.echo(f"  ⚠ {warning}")
    if result.quarantined:
        typer.echo(f"  ✕ Quarantined      {', '.join(result.quarantined)}")
    typer.echo(f"  Imported at        {result.imported_at:%Y-%m-%d %H:%M:%S} UTC")
    typer.echo(f"  Stored in          {result.warehouse_path}")

    if not result.succeeded:
        raise typer.Exit(code=1)

    typer.echo(
        "\nNext: build the analytics layer for this company —\n"
        f"  RM_WAREHOUSE_DUCKDB_PATH={result.warehouse_path} \\\n"
        "    uv run dbt build --profiles-dir . --project-dir data_platform/dbt"
    )


def verify(path: Path) -> None:
    """Query the finished warehouse the way the API will.

    Opened read-only from its final location, through a semantic view, because
    that is the combination that broke: the build succeeded, the file was
    valid, and every query still failed. A build that reports success without
    being readable is worse than one that fails.
    """
    import duckdb

    try:
        conn = duckdb.connect(str(path), read_only=True)
    except duckdb.Error as exc:  # pragma: no cover - surfaced to the operator
        raise typer.Exit(1) from exc
    try:
        rows = conn.execute("SELECT count(*) FROM analytics_semantic.v_mart_sales_daily").fetchone()
    except duckdb.Error as exc:
        typer.echo(f"warehouse built but is not queryable: {exc}", err=True)
        raise typer.Exit(1) from exc
    finally:
        conn.close()

    if not rows or not rows[0]:
        typer.echo("warehouse built but the sales mart is empty", err=True)
        raise typer.Exit(1)
    typer.echo(f"verified: {rows[0]:,} rows in the daily sales mart")


if __name__ == "__main__":  # pragma: no cover
    app()
