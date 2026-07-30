"""Audit ledger — pipeline runs and quality results in the app database.

These rows are *product state*, not operator logs: the pipeline-health screen,
the quarantine workflow, and the report's data-trust appendix all read them
(ETL §19–20, DB §36). Operator logs go to the log stack; these go to tables.

Deliberately written with parameterized SQL against the shared tables rather
than by importing the backend's ORM models. The two packages ship
independently, and a compile-time dependency between them would make every
backend model change a data-platform release.

Writes are best-effort by design: a ledger outage must not fail a data load
that otherwise succeeded. The load is the product; the ledger is its receipt,
and a missing receipt is recoverable from the manifest.
"""

# ruff: noqa: S608 — SQL is composed from schema identifiers validated at
# load time (SourceSchema.validate) and from module constants; every value
# originating in data is bound as a parameter.

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

import structlog

from ingestion.domain.window import Window
from quality.gate import GateVerdict

log = structlog.get_logger(__name__)

try:  # pragma: no cover — optional dependency; the pipeline runs without it
    import psycopg

    _PSYCOPG_AVAILABLE = True
except ImportError:  # pragma: no cover
    _PSYCOPG_AVAILABLE = False


class AuditLedger:
    """Records pipeline runs, quality results, and quarantines."""

    def __init__(self, dsn: str | None, *, enabled: bool = True) -> None:
        self._dsn = dsn
        self._enabled = enabled and bool(dsn) and _PSYCOPG_AVAILABLE
        if enabled and dsn and not _PSYCOPG_AVAILABLE:  # pragma: no cover
            log.warning("etl.audit.disabled", reason="psycopg not installed")

    @contextmanager
    def _cursor(self) -> Iterator[Any]:
        """Yield a cursor, or None when the ledger is disabled/unreachable."""
        if not self._enabled or self._dsn is None:
            yield None
            return
        try:
            with psycopg.connect(self._dsn, connect_timeout=5) as conn, conn.cursor() as cur:
                yield cur
        except Exception as exc:  # noqa: BLE001 — never fail a load over its receipt
            log.warning("etl.audit.write_failed", error=str(exc))
            yield None

    def start_run(
        self,
        *,
        tenant_id: uuid.UUID,
        connector_id: uuid.UUID,
        dag_run_id: str,
        window: Window,
    ) -> uuid.UUID:
        """Open a ``pipeline_run`` row and return its id."""
        run_id = uuid.uuid4()
        with self._cursor() as cur:
            if cur is not None:
                cur.execute(
                    """
                    INSERT INTO pipeline_run (
                        id, tenant_id, connector_id, dag_run_id, window,
                        status, started_at
                    ) VALUES (%s, %s, %s, %s, daterange(%s, %s, '[)'), 'running', %s)
                    """,
                    (
                        run_id,
                        tenant_id,
                        connector_id,
                        dag_run_id,
                        window.start,
                        window.end,
                        datetime.now(tz=UTC),
                    ),
                )
        return run_id

    def finish_run(
        self,
        run_id: uuid.UUID,
        *,
        status: str,
        rows_read: int,
        rows_rejected: int,
        rows_written: int,
        watermark_before: str | None = None,
        watermark_after: str | None = None,
        error_class: str | None = None,
    ) -> None:
        with self._cursor() as cur:
            if cur is not None:
                cur.execute(
                    """
                    UPDATE pipeline_run
                       SET status = %s, rows_read = %s, rows_rejected = %s,
                           rows_written = %s, watermark_before = %s,
                           watermark_after = %s, error_class = %s, ended_at = %s
                     WHERE id = %s
                    """,
                    (
                        status,
                        rows_read,
                        rows_rejected,
                        rows_written,
                        watermark_before,
                        watermark_after,
                        error_class,
                        datetime.now(tz=UTC),
                        run_id,
                    ),
                )

    def record_quality(self, run_id: uuid.UUID, verdict: GateVerdict) -> None:
        """Persist every rule outcome — passes included.

        Storing passes is what makes the data-trust screen show a *score*
        rather than only a list of complaints.
        """
        with self._cursor() as cur:
            if cur is None:
                return
            for result in verdict.results:
                cur.execute(
                    """
                    INSERT INTO dq_result (
                        id, pipeline_run_id, suite, rule_id, expectation,
                        passed, blocking, observed
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                    """,
                    (
                        uuid.uuid4(),
                        run_id,
                        result.rule.layer,
                        result.rule.id,
                        result.rule.description,
                        result.passed,
                        result.rule.severity.value == "blocking",
                        _json(result.observed),
                    ),
                )

    def record_quarantine(
        self,
        *,
        run_id: uuid.UUID,
        tenant_id: uuid.UUID,
        source_key: str,
        partition: str,
        failed_rules: list[str],
        diff_uri: str | None = None,
    ) -> None:
        with self._cursor() as cur:
            if cur is not None:
                cur.execute(
                    """
                    INSERT INTO quarantine_batch (
                        id, tenant_id, pipeline_run_id, source_key, partition,
                        failed_rules, diff_uri
                    ) VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s)
                    """,
                    (
                        uuid.uuid4(),
                        tenant_id,
                        run_id,
                        source_key,
                        partition,
                        _json({"rule_ids": failed_rules}),
                        diff_uri,
                    ),
                )


def _json(value: Any) -> str:
    import json

    return json.dumps(value, default=str)
