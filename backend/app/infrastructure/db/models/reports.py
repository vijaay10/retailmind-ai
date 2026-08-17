"""Executive reports: schedules, runs, sections (ARCH; DB).

Sections persist their assembled *data payload* separately from the narrative,
so a report is re-narratable without re-querying — and ships tables-only when
narration fails (the T3 degradation contract).
"""

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Index, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.db.models.base import (
    Base,
    JSONDict,
    TenantScopedMixin,
    TimestampMixin,
    enum_check,
    uuid_pk,
)
from app.infrastructure.db.models.enums import RunStatus


class ReportSchedule(Base, TenantScopedMixin, TimestampMixin):
    __tablename__ = "report_schedule"

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str]
    template_key: Mapped[str] = mapped_column(comment="weekly_business_review | monthly_deep_dive")
    cron: Mapped[str] = mapped_column(comment="Tenant-local schedule")
    audience_register: Mapped[str] = mapped_column(
        server_default=text("'executive'"), comment="Narration voice (AI)"
    )
    sections_config: Mapped[JSONDict] = mapped_column(server_default=text("'{}'::jsonb"))
    channels: Mapped[JSONDict] = mapped_column(
        server_default=text("'[]'::jsonb"), comment="Delivery targets"
    )
    review_gated: Mapped[bool] = mapped_column(
        server_default=text("false"), comment="Hold for human approval before send (AI.3)"
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("app_user.id", ondelete="RESTRICT"))
    enabled: Mapped[bool] = mapped_column(server_default=text("true"))

    runs: Mapped[list["ReportRun"]] = relationship(back_populates="schedule")


class ReportRun(Base, TenantScopedMixin):
    __tablename__ = "report_run"
    __table_args__ = (
        Index("ix_report_run_schedule", "schedule_id", text("period_end DESC")),
        enum_check("status", RunStatus),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    schedule_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("report_schedule.id", ondelete="SET NULL"),
        comment="NULL = on-demand run",
    )
    period_start: Mapped[datetime]
    period_end: Mapped[datetime]
    status: Mapped[str] = mapped_column(server_default=text("'pending'"))
    artifact_uri: Mapped[str | None] = mapped_column(comment="S3 key of rendered HTML/PDF")
    narrative_status: Mapped[str] = mapped_column(
        server_default=text("'pending'"), comment="ok | unavailable — the T3 marker (PRD)"
    )
    data_snapshot_id: Mapped[str | None] = mapped_column(
        ForeignKey("data_snapshot.id", ondelete="RESTRICT"),
        comment="The as-of pin every deep link carries (ARCH)",
    )
    generated_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"))

    schedule: Mapped[ReportSchedule | None] = relationship(back_populates="runs")
    sections: Mapped[list["ReportSection"]] = relationship(
        back_populates="run", cascade="all, delete-orphan", order_by="ReportSection.position"
    )


class ReportSection(Base):
    __tablename__ = "report_section"
    __table_args__ = (UniqueConstraint("run_id", "section_key"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("report_run.id", ondelete="CASCADE"))
    section_key: Mapped[str] = mapped_column(comment="kpi_scorecard | what_changed | outlook …")
    position: Mapped[int]
    payload: Mapped[JSONDict] = mapped_column(
        comment="Assembled deterministic data (re-narratable)"
    )
    narrative: Mapped[str | None]
    grounding_result: Mapped[JSONDict | None] = mapped_column(
        comment="Numeral-validation outcome for this section's prose (AI.5)"
    )
    human_edited: Mapped[bool] = mapped_column(
        server_default=text("false"), comment="Review-queue edits render visually distinct (AI)"
    )
    prompt_version: Mapped[str | None]
    model_id: Mapped[str | None]

    run: Mapped[ReportRun] = relationship(back_populates="sections")
