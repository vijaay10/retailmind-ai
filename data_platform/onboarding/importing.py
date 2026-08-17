"""Land a validated upload into the tenant's existing ingestion pipeline.

Detection, mapping and validation already worked and produced a report. This
module is the step that was missing: taking the *confirmed* result of that
report and putting it through the pipeline the platform already runs, so a new
company's data ends up where the demo tenant's data ends up.

**It reuses the pipeline rather than becoming a second one.** The work here is
deliberately small — partition the mapped rows by business date, write them
into the source inbox under the filename convention `CsvFileConnector` already
discovers, and hand the window to `IngestionPipeline`. Everything that makes
ingestion trustworthy (schema conformance, rejects, the reject-rate threshold,
quarantine, the bronze landing, the manifest, and the transactional load with
its conservation check) then applies unchanged, because it is the same code
path. A separate "import" that re-implemented any of that would be a second
ingestion architecture with a second set of bugs.

**Idempotency is inherited, not invented.** `WarehouseLoader.load_window`
already deletes the business-date range it is about to write and re-inserts it
inside one transaction. Importing the same file twice therefore *replaces* the
partitions it covers instead of duplicating them, and importing a different
date range leaves earlier partitions alone. `ImportResult.rows_replaced`
surfaces that so a repeat import is visible rather than silent.

**Tenant isolation is a path property.** Every tenant gets its own inbox, its
own bronze lake and its own DuckDB file, derived from the tenant slug by
`tenant_paths`. Two tenants uploading the identical file touch no shared
location, which is the same reason `resolve_warehouse_path` keys off the slug
on the read side.
"""

import csv
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from ingestion.core.config import EtlSettings
from ingestion.core.duck import connect
from ingestion.domain.schema import SourceSchema
from ingestion.domain.window import Window

#: What the unit segment of an uploaded file's name is set to.
#:
#: `CsvFileConnector` parses `{source}_{unit}_{YYYYMMDD}.csv`, where the unit
#: is normally a store identifier because the generated feeds arrive one file
#: per store per day. An upload is one file covering everything, so it lands
#: as a single unit per partition and the connector is told to expect one.
UPLOAD_UNIT = "upload"


class ImportRefusedError(RuntimeError):
    """The import was rejected before anything was written.

    Raised only for conditions that make writing wrong rather than merely
    imperfect — no rows, no usable business date, blocking validation errors,
    or a tenant that does not match. Row-level defects are not this: those go
    through the pipeline's existing reject and quarantine behaviour, which
    reports them precisely instead of failing the whole upload.
    """


@dataclass(frozen=True, slots=True)
class ImportResult:
    """What actually happened, in terms an operator can act on."""

    tenant_slug: str
    source: str
    table: str
    partitions: tuple[str, ...]
    files_written: int
    rows_submitted: int
    rows_read: int
    rows_loaded: int
    rows_rejected: int
    rows_replaced: int
    quarantined: tuple[str, ...]
    warehouse_path: Path
    imported_at: datetime
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def dataset(self) -> str:
        return f"{self.source}.{self.table}"

    @property
    def succeeded(self) -> bool:
        """Nothing was quarantined.

        Deliberately not `rows_loaded > 0`. Re-uploading a file the pipeline
        has already ingested is a *success with nothing to do*: the connector
        matches its checksum against the committed manifest and skips the
        partition entirely. Treating a zero-row outcome as failure would make
        the safest possible re-import look like a broken one.
        """
        return not self.quarantined

    @property
    def unchanged(self) -> bool:
        """The pipeline recognised this content and had nothing to do."""
        return self.rows_read == 0 and not self.quarantined

    @property
    def replaced_existing(self) -> bool:
        """A previous import covered some of these dates and was replaced."""
        return self.rows_replaced > 0


def tenant_paths(tenant_slug: str, root: Path) -> EtlSettings:
    """Ingestion settings scoped to one tenant.

    The inbox and the bronze lake are per-tenant for the same reason the
    warehouse is: two companies uploading a file called `sales.csv` on the same
    day must not meet. The warehouse filename matches
    `semantic.tenancy.resolve_warehouse_path`'s slug convention exactly, so the
    file written here is the file the API reads.
    """
    if not tenant_slug or "/" in tenant_slug or tenant_slug != tenant_slug.strip():
        raise ImportRefusedError(f"unusable tenant slug: {tenant_slug!r}")
    base = root / "tenants" / tenant_slug
    return EtlSettings(
        landing_root=base / "lake",
        inbox_root=base / "inbox",
        warehouse_path=root / f"{tenant_slug}.duckdb",
    )


def _business_date(value: object, column_formats: Sequence[str]) -> date | None:
    """The business date a row belongs to, or None if it has none."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        pass
    for fmt in column_formats:
        try:
            return datetime.strptime(text, fmt).date()  # noqa: DTZ007 — date only
        except ValueError:
            continue
    return None


def partition_rows(
    rows: Iterable[Mapping[str, object]], schema: SourceSchema
) -> dict[date, list[Mapping[str, object]]]:
    """Group mapped rows by the business date the schema declares.

    Partitioning by the schema's own `event_time_column` rather than by upload
    time is what makes a re-upload replace the right days: the pipeline keys
    everything on business date, so a file re-sent tomorrow still lands on the
    dates its rows describe.
    """
    formats: Sequence[str] = ()
    for column in schema.columns:
        if column.name == schema.event_time_column:
            formats = column.date_formats
            break

    grouped: dict[date, list[Mapping[str, object]]] = {}
    for row in rows:
        day = _business_date(row.get(schema.event_time_column), formats)
        if day is None:
            continue
        grouped.setdefault(day, []).append(row)
    return grouped


def import_mapped_rows(
    rows: Sequence[Mapping[str, object]],
    schema: SourceSchema,
    *,
    tenant_slug: str,
    settings: EtlSettings,
) -> ImportResult:
    """Put confirmed, mapped rows through the pipeline. Returns what happened.

    `rows` must already carry canonical field names — the caller has run
    detection and mapping and the user has confirmed the result. Validation is
    re-run by the pipeline itself against the same schema, so a file that
    passed the report and was edited before confirming does not slip through.
    """
    if not rows:
        raise ImportRefusedError("nothing to import: the file produced no rows")

    grouped = partition_rows(rows, schema)
    if not grouped:
        raise ImportRefusedError(
            f"no row carried a usable {schema.event_time_column} value, so there is "
            "no business date to file this data under"
        )

    undated = len(rows) - sum(len(v) for v in grouped.values())
    warnings: list[str] = []
    if undated:
        warnings.append(
            f"{undated:,} row(s) had no usable {schema.event_time_column} and were not imported"
        )

    # Canonical column order, so every partition file has identical headers
    # regardless of which keys a given row happened to carry.
    columns = [c.name for c in schema.columns]

    inbox = settings.inbox_dir(schema.source)
    inbox.mkdir(parents=True, exist_ok=True)
    for day, day_rows in sorted(grouped.items()):
        target = inbox / f"{schema.source}_{UPLOAD_UNIT}_{day:%Y%m%d}.csv"
        with target.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            for row in day_rows:
                writer.writerow({c: row.get(c, "") for c in columns})

    days = sorted(grouped)
    window = Window(days[0], days[-1] + timedelta(days=1))

    # Imported lazily: the connector pulls in the whole ingestion stack, and
    # callers that only wanted to partition rows should not pay for it.
    from ingestion.connectors.csv_files import CsvFileConnector
    from ingestion.pipeline import IngestionPipeline

    settings.warehouse_path.parent.mkdir(parents=True, exist_ok=True)
    connection = connect(settings.warehouse_path)
    try:
        connector = CsvFileConnector(
            schema=schema,
            settings=settings,
            connection=connection,
            # One uploaded file per partition — see UPLOAD_UNIT.
            expected_units=1,
        )
        summary = IngestionPipeline(
            connector=connector, settings=settings, connection=connection
        ).run(window)
    finally:
        connection.close()

    replaced = sum(o.load.rows_deleted for o in summary.outcomes if o.load)

    return ImportResult(
        tenant_slug=tenant_slug,
        source=schema.source,
        table=schema.table,
        partitions=tuple(f"{d:%Y-%m-%d}" for d in days),
        files_written=len(days),
        rows_submitted=len(rows),
        rows_read=summary.rows_read,
        rows_loaded=summary.rows_loaded,
        rows_rejected=summary.rows_rejected,
        rows_replaced=replaced,
        quarantined=tuple(str(q) for q in summary.quarantined),
        warehouse_path=settings.warehouse_path,
        imported_at=datetime.now(UTC),
        warnings=tuple(warnings),
    )
