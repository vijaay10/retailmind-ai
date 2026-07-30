"""Pipeline orchestrator — the stage chain, end to end (ETL design §1).

    discover → conform → validate → land → load → reconcile → record

One partition at a time, one transaction per load, one manifest per commit.
A quarantined partition stops *its own source* and leaves the rest of the
night's work alone: the gate protects the warehouse without turning one bad
feed into a global outage.

The orchestrator holds no data itself. Everything moves as DuckDB relations
until it lands as Parquet, which is what keeps memory flat regardless of
batch size.
"""

# ruff: noqa: S608 — SQL is composed from schema identifiers validated at
# load time (SourceSchema.validate) and from module constants; every value
# originating in data is bound as a parameter.

import uuid
from dataclasses import dataclass, field
from datetime import date

import duckdb
import structlog

from ingestion.audit.ledger import AuditLedger
from ingestion.connectors.base import Connector, ExtractionPlan
from ingestion.core.config import EtlSettings
from ingestion.core.errors import QuarantineError
from ingestion.core.logging import run_context
from ingestion.core.retry import with_retries
from ingestion.domain.manifest import PartitionManifest
from ingestion.domain.schema import detect_drift
from ingestion.domain.window import Window
from ingestion.landing.writer import BronzeWriter, partition_data_path
from ingestion.loading.warehouse import LoadResult, WarehouseLoader
from ingestion.transform.currency import build_fx_lookup_sql
from ingestion.transform.sql import build_conform_sql, build_rejects_sql
from quality.gate import BatchStats, GateVerdict, QualityGate

log = structlog.get_logger(__name__)


@dataclass(slots=True)
class PartitionOutcome:
    partition: str
    status: str
    """``loaded`` | ``skipped`` | ``quarantined`` | ``empty``"""
    manifest: PartitionManifest | None = None
    verdict: GateVerdict | None = None
    load: LoadResult | None = None
    failed_rules: list[str] = field(default_factory=list)


@dataclass(slots=True)
class RunSummary:
    source: str
    table: str
    window: Window
    outcomes: list[PartitionOutcome] = field(default_factory=list)

    @property
    def rows_read(self) -> int:
        return sum(o.manifest.rows_read for o in self.outcomes if o.manifest)

    @property
    def rows_rejected(self) -> int:
        return sum(o.manifest.rows_rejected for o in self.outcomes if o.manifest)

    @property
    def rows_loaded(self) -> int:
        return sum(o.load.rows_inserted for o in self.outcomes if o.load)

    @property
    def quarantined(self) -> list[PartitionOutcome]:
        return [o for o in self.outcomes if o.status == "quarantined"]

    @property
    def status(self) -> str:
        if self.quarantined:
            return "quarantined"
        return "succeeded"


class IngestionPipeline:
    """Runs one connector over one window."""

    def __init__(
        self,
        *,
        connector: Connector,
        settings: EtlSettings,
        connection: duckdb.DuckDBPyConnection,
        ledger: AuditLedger | None = None,
        tenant_id: uuid.UUID | None = None,
        connector_id: uuid.UUID | None = None,
    ) -> None:
        self._connector = connector
        self._schema = connector.schema
        self._settings = settings
        self._conn = connection
        self._writer = BronzeWriter(settings, connection)
        self._loader = WarehouseLoader(connection)
        self._gate = QualityGate(
            self._schema,
            reject_rate_threshold=settings.reject_rate_threshold,
            volume_tolerance=settings.volume_band_tolerance,
        )
        self._ledger = ledger
        self._tenant_id = tenant_id
        self._connector_id = connector_id

    def run(self, window: Window, *, dag_run_id: str = "manual") -> RunSummary:
        """Process every partition in the window."""
        summary = RunSummary(source=self._schema.source, table=self._schema.table, window=window)

        with run_context(
            source=self._schema.source,
            table=self._schema.table,
            window=str(window),
            dag_run_id=dag_run_id,
        ):
            log.info("etl.run.started", partitions=len(window.partitions))

            run_id = self._open_ledger_run(window, dag_run_id)
            plans = with_retries(
                lambda: self._connector.discover(window),
                name=f"discover:{self._connector.name}",
                max_attempts=self._settings.max_retries,
                base_seconds=self._settings.retry_base_seconds,
            )

            for plan in plans:
                summary.outcomes.append(self._process_partition(plan, run_id))

            self._close_ledger_run(run_id, summary)
            log.info(
                "etl.run.finished",
                status=summary.status,
                rows_read=summary.rows_read,
                rows_loaded=summary.rows_loaded,
                rows_rejected=summary.rows_rejected,
                quarantined=len(summary.quarantined),
            )

        return summary

    # ── Stages ───────────────────────────────────────────────────────

    def _process_partition(
        self, plan: ExtractionPlan, run_id: uuid.UUID | None
    ) -> PartitionOutcome:
        if plan.is_empty:
            log.info("etl.partition.empty", partition=plan.partition)
            return PartitionOutcome(partition=plan.partition, status="empty")

        if not plan.relation:
            # Discovery found the exact content already committed.
            return PartitionOutcome(partition=plan.partition, status="skipped")

        with run_context(partition=plan.partition):
            expected_dates = {date.fromisoformat(plan.partition)}
            drift = detect_drift(self._schema, plan.observed_columns)

            # Schema first, before a single row is read. The conform query is
            # generated *from* the declared schema, so running it against a
            # file missing a declared column would fail with a binder error
            # rather than the diagnosis an operator needs. Cheapest check
            # first is not only an optimization here — it is what keeps the
            # failure legible.
            if any(finding.blocking for finding in drift):
                verdict = self._gate.evaluate(
                    BatchStats(rows_read=0, rows_rejected=0, rows_landed=0),
                    drift=drift,
                    expected_dates=expected_dates,
                )
                if self._ledger and run_id:
                    self._ledger.record_quality(run_id, verdict)
                return self._quarantine(plan, verdict, run_id)

            conformed = self._conform(plan)
            rejects_sql = build_rejects_sql(self._schema, source_relation=plan.relation)
            stats = self._measure(plan, conformed, rejects_sql)

            verdict = self._gate.evaluate(
                stats,
                drift=drift,
                expected_dates=expected_dates,
                volume_history=self._loader.row_count_history(self._schema),
            )
            if self._ledger and run_id:
                self._ledger.record_quality(run_id, verdict)

            if not verdict.passed:
                return self._quarantine(plan, verdict, run_id)

            manifest = self._writer.write_partition(
                schema=self._schema,
                partition=plan.partition,
                data_relation=conformed,
                rejects_relation=rejects_sql,
                source_files=plan.files,
                rows_read=stats.rows_read,
                connector_version=self._connector.version,
                warnings=plan.warnings + [r.rule.id for r in verdict.warnings],
                watermark=plan.partition,
            )

            load = self._loader.load_window(
                self._schema,
                window=Window.for_day(date.fromisoformat(plan.partition)),
                parquet_paths=[partition_data_path(self._settings, self._schema, plan.partition)],
                expected_rows=manifest.rows_landed,
            )

            return PartitionOutcome(
                partition=plan.partition,
                status="loaded",
                manifest=manifest,
                verdict=verdict,
                load=load,
            )

    def _conform(self, plan: ExtractionPlan) -> str:
        """Compile parse → validate → dedupe → standardize, then apply FX.

        Returns a SQL string, not data: nothing is materialized until the
        landing stage writes Parquet.
        """
        conformed = build_conform_sql(self._schema, source_relation=plan.relation)
        if not any(c.is_money for c in self._schema.columns):
            return conformed

        self._ensure_fx_table()
        return build_fx_lookup_sql(
            self._schema,
            source_relation=f"({conformed})",
            fx_relation="fx_rates",
            base_currency=self._settings.base_currency,
            carry_forward_days=self._settings.fx_carry_forward_days,
        )

    def _measure(self, plan: ExtractionPlan, conformed_sql: str, rejects_sql: str) -> BatchStats:
        """Collect the counts the gate judges, in as few passes as possible."""
        rows_read = self._scalar(f"SELECT count(*) FROM {plan.relation}")
        landed = self._conn.execute(
            f"""
            SELECT count(*),
                   coalesce(sum(_duplicates_collapsed), 0),
                   coalesce(sum(CASE WHEN _fx_missing THEN 1 ELSE 0 END), 0)
            FROM ({conformed_sql})
            """
            if "_fx_missing" in conformed_sql
            else f"""
            SELECT count(*), coalesce(sum(_duplicates_collapsed), 0), 0
            FROM ({conformed_sql})
            """
        ).fetchone()
        rows_landed, duplicates, fx_missing = (int(v) for v in (landed or (0, 0, 0)))

        business_dates = {
            row[0]
            for row in self._conn.execute(
                f"SELECT DISTINCT business_date FROM ({conformed_sql})"
            ).fetchall()
            if row[0] is not None
        }

        return BatchStats(
            rows_read=rows_read,
            rows_rejected=self._scalar(f"SELECT count(*) FROM ({rejects_sql})"),
            rows_landed=rows_landed,
            business_dates=business_dates,
            duplicates_collapsed=duplicates,
            fx_missing_rows=fx_missing,
            files_expected=plan.files_expected or len(plan.files),
            files_arrived=len(plan.files),
        )

    def _quarantine(
        self, plan: ExtractionPlan, verdict: GateVerdict, run_id: uuid.UUID | None
    ) -> PartitionOutcome:
        """Hold the batch at the boundary; leave published data untouched.

        Nothing is written to bronze or the warehouse: downstream keeps
        serving yesterday's snapshot, correctly marked stale, rather than
        being handed a batch we already know is wrong.
        """
        failed = verdict.failed_rule_ids
        log.error(
            "etl.partition.quarantined",
            partition=plan.partition,
            failed_rules=failed,
        )
        if self._ledger and run_id and self._tenant_id:
            self._ledger.record_quarantine(
                run_id=run_id,
                tenant_id=self._tenant_id,
                source_key=self._schema.source,
                partition=plan.partition,
                failed_rules=failed,
            )
        return PartitionOutcome(
            partition=plan.partition,
            status="quarantined",
            verdict=verdict,
            failed_rules=failed,
        )

    # ── Helpers ──────────────────────────────────────────────────────

    def _ensure_fx_table(self) -> None:
        """Guarantee an fx_rates relation exists so the join is well-formed.

        An empty table means every non-base row trips ``_fx_missing`` and the
        gate blocks the batch — the loud failure the design demands, rather
        than a silent 1:1 conversion.
        """
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS fx_rates (
                rate_date DATE,
                currency VARCHAR,
                base_currency VARCHAR,
                rate DOUBLE
            )
            """
        )

    def _scalar(self, sql: str) -> int:
        row = self._conn.execute(sql).fetchone()
        return int(row[0]) if row else 0

    def _open_ledger_run(self, window: Window, dag_run_id: str) -> uuid.UUID | None:
        if not (self._ledger and self._tenant_id and self._connector_id):
            return None
        return self._ledger.start_run(
            tenant_id=self._tenant_id,
            connector_id=self._connector_id,
            dag_run_id=dag_run_id,
            window=window,
        )

    def _close_ledger_run(self, run_id: uuid.UUID | None, summary: RunSummary) -> None:
        if not (self._ledger and run_id):
            return
        self._ledger.finish_run(
            run_id,
            status=summary.status,
            rows_read=summary.rows_read,
            rows_rejected=summary.rows_rejected,
            rows_written=summary.rows_loaded,
            watermark_after=summary.window.partitions[-1] if summary.outcomes else None,
            error_class="validation_failed" if summary.quarantined else None,
        )


def raise_if_quarantined(summary: RunSummary) -> None:
    """Convert a quarantine into the DAG-failing exception Airflow expects.

    Kept separate from ``run`` so programmatic callers can inspect the summary
    and decide, while the scheduler gets a clean failure signal.
    """
    if summary.quarantined:
        failed = sorted({rule for o in summary.quarantined for rule in o.failed_rules})
        raise QuarantineError(
            f"{len(summary.quarantined)} partition(s) quarantined",
            failed_rules=failed,
            source=summary.source,
            table=summary.table,
        )
