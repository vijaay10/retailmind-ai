"""CSV file connector — the upload/drop path.

Handles the messy realities of file-based retail feeds: per-store files that
arrive at different times, occasional re-drops, gzip, and headers that drift.

Two rules worth knowing:

* **Business date comes from the rows, not the filename.** Filenames are a
  hint; a cross-midnight file would otherwise mis-date every row it contains.
  The conform stage derives ``business_date`` from event time in the store's
  timezone and the gate then rejects anything outside the window.
* **Checksums decide re-drops.** An identical file is skipped; a changed one
  with the same name is a correction and re-lands its partition.
"""

# ruff: noqa: S608 — SQL is composed from schema identifiers validated at
# load time (SourceSchema.validate) and from module constants; every value
# originating in data is bound as a parameter.

import re
from dataclasses import dataclass
from pathlib import Path

import duckdb
import structlog

from ingestion.connectors.base import ExtractionPlan
from ingestion.core.config import EtlSettings
from ingestion.core.errors import SourceUnavailableError
from ingestion.domain.manifest import PartitionManifest, SourceFile, file_checksum
from ingestion.domain.schema import SourceSchema
from ingestion.domain.window import Window

log = structlog.get_logger(__name__)

#: ``{source}_{unit}_{yyyymmdd}[_seq].csv[.gz]`` — ``unit`` is a store or
#: region code, ``seq`` distinguishes split exports.
FILENAME_RE = re.compile(
    r"^(?P<source>[a-z0-9]+)_(?P<unit>[A-Za-z0-9-]+)_(?P<date>\d{8})(?:_(?P<seq>\d+))?"
    r"\.csv(?:\.gz)?$"
)


@dataclass(slots=True)
class CsvFileConnector:
    """Discovers dropped CSVs and exposes them as a DuckDB relation."""

    schema: SourceSchema
    settings: EtlSettings
    connection: duckdb.DuckDBPyConnection
    expected_units: int = 0
    """How many per-unit files a complete day should contain. Zero disables
    the completeness check (single-file sources)."""
    version: str = "1.0"

    @property
    def name(self) -> str:
        return f"{self.schema.source}.{self.schema.table}"

    def discover(self, window: Window) -> list[ExtractionPlan]:
        """Group inbox files by the business date in their filename."""
        inbox = self.settings.inbox_dir(self.schema.source)
        if not inbox.exists():
            raise SourceUnavailableError(
                f"inbox for '{self.schema.source}' does not exist", path=str(inbox)
            )

        by_partition: dict[str, list[Path]] = {}
        for path in sorted(inbox.iterdir()):
            if not path.is_file():
                continue
            match = FILENAME_RE.match(path.name)
            if match is None:
                log.warning("etl.discover.ignored_file", file=path.name, reason="name pattern")
                continue
            stamp = match.group("date")
            partition = f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:]}"
            by_partition.setdefault(partition, []).append(path)

        return [
            self._plan(partition, by_partition.get(partition, []))
            for partition in window.partitions
        ]

    def _plan(self, partition: str, paths: list[Path]) -> ExtractionPlan:
        if not paths:
            return ExtractionPlan(partition=partition, files_expected=self.expected_units)

        files = [
            SourceFile(name=p.name, bytes=p.stat().st_size, checksum=file_checksum(p))
            for p in paths
        ]

        warnings: list[str] = []
        if self.expected_units and len(files) < self.expected_units:
            warnings.append(
                f"{len(files)} of {self.expected_units} expected files arrived for {partition}"
            )

        # Skip work entirely when this exact content is already committed —
        # the file-level duplicate check (ETL).
        committed = PartitionManifest.read(
            self.settings.bronze_dir(self.schema.source, self.schema.table, partition)
        )
        if committed and committed.checksums == {f.checksum for f in files}:
            log.info(
                "etl.discover.unchanged",
                partition=partition,
                files=len(files),
                reason="checksums match committed manifest",
            )
            # Empty relation signals "already committed, nothing to do";
            # the orchestrator short-circuits before any SQL runs.
            return ExtractionPlan(
                partition=partition,
                files=files,
                relation="",
                observed_columns=list(self.schema.column_names),
                files_expected=self.expected_units,
                warnings=["partition unchanged; skipped"],
            )

        file_list = ", ".join(f"'{p}'" for p in paths)
        # all_varchar: nothing is coerced at read time. Types are applied by
        # the conform stage, which can route failures to rejects instead of
        # aborting the whole file on one bad cell.
        relation = (
            f"read_csv([{file_list}], all_varchar=true, header=true, "
            "ignore_errors=false, union_by_name=true)"
        )

        return ExtractionPlan(
            partition=partition,
            files=files,
            relation=relation,
            observed_columns=self._observed_columns(relation),
            files_expected=self.expected_units,
            warnings=warnings,
        )

    def _observed_columns(self, relation: str) -> list[str]:
        """Read the header without reading the data (drift input,)."""
        rows = self.connection.execute(f"SELECT * FROM {relation} LIMIT 0").description or []
        return [str(column[0]) for column in rows]
