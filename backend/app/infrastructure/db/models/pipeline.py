"""Pipeline observability mirrored into the app DB — product surfaces read these
(pipeline health screens, quarantine workflow, job status; DB).

Operator logs go to the log stack; these tables are *product state*.
"""

import uuid
from datetime import date, datetime

from sqlalchemy import BigInteger, ForeignKey, Index, text
from sqlalchemy.dialects.postgresql import DATERANGE, Range
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.db.models.base import (
    Base,
    JSONDict,
    TenantScopedMixin,
    enum_check,
    uuid_pk,
)
from app.infrastructure.db.models.enums import RunStatus


class PipelineRun(Base, TenantScopedMixin):
    """One connector execution window — mirror of Airflow state (DB.2)."""

    __tablename__ = "pipeline_run"
    __table_args__ = (
        Index("ix_pipeline_run_connector", "connector_id", text("started_at DESC")),
        # Backfill overlap detection (DB ix_run_window)
        Index("ix_pipeline_run_window", "connector_id", "window"),
        enum_check("status", RunStatus),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    connector_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("connector_config.id", ondelete="RESTRICT")
    )
    dag_run_id: Mapped[str] = mapped_column(comment="Airflow correlation key")
    window: Mapped[Range[date]] = mapped_column(DATERANGE, comment="Business-date window (ds)")
    rows_read: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    rows_rejected: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    rows_written: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    watermark_before: Mapped[str | None]
    watermark_after: Mapped[str | None]
    status: Mapped[str] = mapped_column(server_default=text("'pending'"))
    error_class: Mapped[str | None] = mapped_column(comment="ETL taxonomy key")
    started_at: Mapped[datetime]
    ended_at: Mapped[datetime | None]

    dq_results: Mapped[list["DqResult"]] = relationship(
        back_populates="pipeline_run", cascade="all, delete-orphan"
    )


class DqResult(Base):
    """One expectation outcome per run (feeds trust dashboards + DQ scoring,)."""

    __tablename__ = "dq_result"
    __table_args__ = (Index("ix_dq_result_run", "pipeline_run_id", "passed"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    pipeline_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("pipeline_run.id", ondelete="CASCADE")
    )
    suite: Mapped[str]
    rule_id: Mapped[str] = mapped_column(comment="Quality-rule catalog id, e.g. QR-VOL-002")
    expectation: Mapped[str]
    passed: Mapped[bool]
    blocking: Mapped[bool] = mapped_column(server_default=text("false"))
    observed: Mapped[JSONDict] = mapped_column(server_default=text("'{}'::jsonb"))
    at: Mapped[datetime] = mapped_column(server_default=text("now()"))

    pipeline_run: Mapped[PipelineRun] = relationship(back_populates="dq_results")


class QuarantineBatch(Base, TenantScopedMixin):
    """A quarantined partition awaiting fix + replay (US-09 workflow; DB)."""

    __tablename__ = "quarantine_batch"

    id: Mapped[uuid.UUID] = uuid_pk()
    pipeline_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("pipeline_run.id", ondelete="RESTRICT")
    )
    source_key: Mapped[str]
    partition: Mapped[str] = mapped_column(comment="e.g. bronze/pos/sales/dt=2026-07-22")
    failed_rules: Mapped[JSONDict] = mapped_column(comment="Rule ids + observed values")
    diff_uri: Mapped[str | None] = mapped_column(comment="S3 key of the diff report")
    replayed_at: Mapped[datetime | None]
    replayed_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("app_user.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"))


class JobRun(Base, TenantScopedMixin):
    """Async job lifecycle (reports, RCA, backfills) — powers /v1/jobs/{id} (Backend)."""

    __tablename__ = "job_run"
    __table_args__ = (
        Index("ix_job_run_status", "tenant_id", "status", text("created_at DESC")),
        enum_check("status", RunStatus),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    job_type: Mapped[str] = mapped_column(comment="rca_investigation | report_run | backfill …")
    params_digest: Mapped[str] = mapped_column(comment="Idempotency correlation")
    requested_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("app_user.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(server_default=text("'pending'"))
    attempts: Mapped[int] = mapped_column(server_default=text("0"))
    error_class: Mapped[str | None]
    result_ref: Mapped[JSONDict | None] = mapped_column(
        comment="Pointer to the produced artifact {resource_type, resource_id} | {artifact_uri}"
    )
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"))
    started_at: Mapped[datetime | None]
    ended_at: Mapped[datetime | None]
