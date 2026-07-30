"""Scenario simulator: definitions and runs (DB §39; PRD F-12).

Assumptions (elasticity source + CI) are columns, not prose — provenance is a
display requirement (US-06).
"""

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.db.models.base import (
    Base,
    JSONDict,
    TenantScopedMixin,
    TimestampMixin,
    uuid_pk,
)


class Scenario(Base, TenantScopedMixin, TimestampMixin):
    __tablename__ = "scenario"

    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("app_user.id", ondelete="CASCADE"))
    title: Mapped[str]
    levers: Mapped[JSONDict] = mapped_column(
        comment="e.g. {price_change_pct: -15, scope: {category: 'Outerwear', region: 'SW'}}"
    )
    source_recommendation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("recommendation.id", ondelete="SET NULL"),
        comment="Set when launched from a rec's [Simulate] bridge (Backend §29)",
    )

    runs: Mapped[list["ScenarioRun"]] = relationship(
        back_populates="scenario", cascade="all, delete-orphan"
    )


class ScenarioRun(Base):
    __tablename__ = "scenario_run"

    id: Mapped[uuid.UUID] = uuid_pk()
    scenario_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("scenario.id", ondelete="CASCADE"), index=True
    )
    assumptions: Mapped[JSONDict] = mapped_column(
        comment="Elasticity values with CI + pooling level + model version (M10 honesty contract)"
    )
    baseline_result: Mapped[JSONDict]
    scenario_result: Mapped[JSONDict]
    data_snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("data_snapshot.id", ondelete="RESTRICT")
    )
    ran_at: Mapped[datetime] = mapped_column(server_default=text("now()"))

    scenario: Mapped[Scenario] = relationship(back_populates="runs")
