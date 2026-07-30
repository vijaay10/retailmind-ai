"""Bronze landing — write data, then commit with the manifest (ETL design §3).

The commit protocol is the important part. Data files are written first, then
``manifest.json``. Downstream only ever reads partitions that have one, so a
crash mid-write leaves an unreferenced directory rather than a half-visible
partition. This buys atomicity on a plain filesystem or object store, without
needing transactional storage.

Rejected rows land beside the data in ``_rejects/``, keeping their original
text and a machine-readable reason. They are data to triage, never log lines
to grep (ETL §23).
"""

# ruff: noqa: S608 — SQL is composed from schema identifiers validated at
# load time (SourceSchema.validate) and from module constants; every value
# originating in data is bound as a parameter.

from pathlib import Path

import duckdb
import structlog

from ingestion.core.config import EtlSettings
from ingestion.domain.manifest import PartitionManifest, SourceFile
from ingestion.domain.schema import SourceSchema

log = structlog.get_logger(__name__)

DATA_FILE = "part-000.parquet"
REJECTS_FILE = "rejects.parquet"


class BronzeWriter:
    """Writes one partition and commits it."""

    def __init__(self, settings: EtlSettings, connection: duckdb.DuckDBPyConnection) -> None:
        self._settings = settings
        self._conn = connection

    def write_partition(
        self,
        *,
        schema: SourceSchema,
        partition: str,
        data_relation: str,
        rejects_relation: str | None,
        source_files: list[SourceFile],
        rows_read: int,
        connector_version: str,
        warnings: list[str] | None = None,
        watermark: str | None = None,
    ) -> PartitionManifest:
        """Persist a conformed batch and its rejects, then commit the manifest.

        Partition-overwrite semantics: re-running a window replaces its files
        rather than appending. That is the mechanical basis of idempotency —
        double-counting is not something the pipeline avoids by being careful,
        it is something it cannot express (FR-D03).
        """
        data_dir = self._settings.bronze_dir(schema.source, schema.table, partition)
        data_dir.mkdir(parents=True, exist_ok=True)
        data_path = data_dir / DATA_FILE

        self._conn.execute(
            f"COPY ({data_relation}) TO '{data_path}' (FORMAT PARQUET, COMPRESSION ZSTD)"
        )
        rows_landed = self._count(f"SELECT count(*) FROM read_parquet('{data_path}')")

        rows_rejected = 0
        if rejects_relation is not None:
            rejects_dir = self._settings.rejects_dir(schema.source, schema.table, partition)
            rejects_dir.mkdir(parents=True, exist_ok=True)
            rejects_path = rejects_dir / REJECTS_FILE
            self._conn.execute(
                f"COPY ({rejects_relation}) TO '{rejects_path}' (FORMAT PARQUET, COMPRESSION ZSTD)"
            )
            rows_rejected = self._count(f"SELECT count(*) FROM read_parquet('{rejects_path}')")

        manifest = PartitionManifest(
            source=schema.source,
            table=schema.table,
            partition=partition,
            schema_version=schema.version,
            schema_fingerprint=schema.fingerprint,
            connector_version=connector_version,
            rows_read=rows_read,
            rows_rejected=rows_rejected,
            rows_landed=rows_landed,
            source_files=source_files,
            warnings=warnings or [],
            watermark=watermark,
        )
        # Commit marker — written last, on purpose.
        manifest.write(data_dir)

        log.info(
            "etl.land.committed",
            source=schema.source,
            table=schema.table,
            partition=partition,
            rows_read=rows_read,
            rows_landed=rows_landed,
            rows_rejected=rows_rejected,
        )
        return manifest

    def _count(self, sql: str) -> int:
        row = self._conn.execute(sql).fetchone()
        return int(row[0]) if row else 0


def committed_partitions(
    settings: EtlSettings, schema: SourceSchema
) -> dict[str, PartitionManifest]:
    """Every committed partition for a table, keyed by ``dt`` label.

    Uncommitted directories (no manifest) are invisible here by design — the
    same rule the loader follows.
    """
    root = settings.landing_root / "bronze" / schema.source / schema.table
    if not root.exists():
        return {}

    found: dict[str, PartitionManifest] = {}
    for directory in sorted(root.glob("dt=*")):
        manifest = PartitionManifest.read(directory)
        if manifest is not None:
            found[directory.name.removeprefix("dt=")] = manifest
    return found


def partition_data_path(settings: EtlSettings, schema: SourceSchema, partition: str) -> Path:
    return settings.bronze_dir(schema.source, schema.table, partition) / DATA_FILE
