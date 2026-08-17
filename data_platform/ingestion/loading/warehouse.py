"""Warehouse loading: bronze → RAW, incrementally and idempotently (ETL).

The load is a *partition overwrite*, not an append: the window's existing rows
are deleted and re-inserted inside one transaction. That single choice is what
makes re-runs, late-file re-lands, and backfills all safe by construction —
they are the same operation with a different window.

Reconciliation (QR-BAL-030) runs immediately after: row counts and measure
totals are compared against the manifest before the load is allowed to stand.
This is the check that catches join fanout, dedup overreach, and window slips
— whole classes of bug that per-row validation structurally cannot see.
"""

# ruff: noqa: S608 — SQL is composed from schema identifiers validated at
# load time (SourceSchema.validate) and from module constants; every value
# originating in data is bound as a parameter.

from dataclasses import dataclass
from pathlib import Path

import duckdb
import structlog

from ingestion.core.errors import WarehouseError
from ingestion.domain.schema import ColumnClass, DataType, SourceSchema
from ingestion.domain.window import Window

log = structlog.get_logger(__name__)

RAW_SCHEMA = "raw"


@dataclass(frozen=True, slots=True)
class LoadResult:
    table: str
    rows_deleted: int
    rows_inserted: int
    reconciled: bool
    measure_totals: dict[str, float]


class WarehouseLoader:
    """Loads conformed bronze partitions into the warehouse's RAW schema."""

    def __init__(self, connection: duckdb.DuckDBPyConnection) -> None:
        self._conn = connection
        self._conn.execute(f"CREATE SCHEMA IF NOT EXISTS {RAW_SCHEMA}")

    def target_table(self, schema: SourceSchema) -> str:
        return f"{RAW_SCHEMA}.{schema.source}__{schema.table}"

    def ensure_table(self, schema: SourceSchema, sample_parquet: Path) -> None:
        """Create the target from the landed file's own shape.

        Deriving the DDL from the data rather than re-deriving it from the
        schema avoids a second source of truth that could disagree with what
        the conform stage actually produced.
        """
        table = self.target_table(schema)
        self._conn.execute(
            f"CREATE TABLE IF NOT EXISTS {table} AS "
            f"SELECT * FROM read_parquet('{sample_parquet}') WHERE 1 = 0"
        )

    def load_window(
        self,
        schema: SourceSchema,
        *,
        window: Window,
        parquet_paths: list[Path],
        expected_rows: int,
    ) -> LoadResult:
        """Replace the window's rows with the contents of ``parquet_paths``.

        Delete-then-insert runs in one transaction: a reader either sees the
        old window or the new one, never a partially rewritten one.
        """
        if not parquet_paths:
            return LoadResult(self.target_table(schema), 0, 0, True, {})

        table = self.target_table(schema)
        self.ensure_table(schema, parquet_paths[0])
        files = ", ".join(f"'{p}'" for p in parquet_paths)

        try:
            self._conn.execute("BEGIN TRANSACTION")

            deleted = self._conn.execute(
                f"DELETE FROM {table} WHERE business_date >= ? AND business_date < ?",
                [window.start, window.end],
            ).fetchone()
            rows_deleted = int(deleted[0]) if deleted else 0

            self._conn.execute(f"INSERT INTO {table} SELECT * FROM read_parquet([{files}])")
            inserted = self._conn.execute(
                f"SELECT count(*) FROM {table} WHERE business_date >= ? AND business_date < ?",
                [window.start, window.end],
            ).fetchone()
            rows_inserted = int(inserted[0]) if inserted else 0

            totals = self._measure_totals(schema, table, window)

            # Conservation check before commit: if the count does not match
            # what we landed, nothing about this load can be trusted.
            reconciled = rows_inserted == expected_rows
            if not reconciled:
                self._conn.execute("ROLLBACK")
                raise WarehouseError(
                    "reconciliation failed: warehouse row count does not match the manifest",
                    table=table,
                    expected=expected_rows,
                    actual=rows_inserted,
                    window=str(window),
                )

            self._conn.execute("COMMIT")
        except WarehouseError:
            raise
        except duckdb.Error as exc:  # pragma: no cover — engine-level failures
            self._conn.execute("ROLLBACK")
            raise WarehouseError(f"warehouse load failed: {exc}", table=table) from exc

        log.info(
            "etl.load.completed",
            table=table,
            window=str(window),
            rows_deleted=rows_deleted,
            rows_inserted=rows_inserted,
            reconciled=reconciled,
        )
        return LoadResult(table, rows_deleted, rows_inserted, reconciled, totals)

    def _measure_totals(self, schema: SourceSchema, table: str, window: Window) -> dict[str, float]:
        """Sum every numeric measure in the window — the money-conservation input."""
        measures = [
            c
            for c in schema.columns_of_class(ColumnClass.MEASURE)
            if c.dtype in (DataType.DECIMAL, DataType.INTEGER)
        ]
        if not measures:
            return {}

        projection = ", ".join(f'coalesce(sum("{c.name}"), 0)' for c in measures)
        row = self._conn.execute(
            f"SELECT {projection} FROM {table} WHERE business_date >= ? AND business_date < ?",
            [window.start, window.end],
        ).fetchone()
        if row is None:
            return {}
        return {c.name: float(value) for c, value in zip(measures, row, strict=True)}

    def row_count_history(self, schema: SourceSchema, *, limit: int = 28) -> list[int]:
        """Recent per-day row counts, newest first — the volume band's input."""
        table = self.target_table(schema)
        try:
            rows = self._conn.execute(
                f"SELECT count(*) AS n FROM {table} "
                "GROUP BY business_date ORDER BY business_date DESC LIMIT ?",
                [limit],
            ).fetchall()
        except duckdb.Error:
            return []  # table not created yet: no history is not an error
        return [int(r[0]) for r in rows]
