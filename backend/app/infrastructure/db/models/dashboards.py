"""User-owned analytical assets: saved queries, dashboards, tiles (DB.2).

Saved answers persist as *semantic queries* (re-executed on view), never cached
data — the US-08 contract.
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
    uuid_pk,
)


class SavedQuery(Base, TenantScopedMixin, TimestampMixin):
    __tablename__ = "saved_query"
    __table_args__ = (
        # Impact analysis: "which tiles use metric X" (DB GIN)
        Index(
            "ix_saved_query_semantic",
            "semantic_query",
            postgresql_using="gin",
            postgresql_ops={"semantic_query": "jsonb_path_ops"},
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("app_user.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str]
    semantic_query: Mapped[JSONDict] = mapped_column(
        comment="The governed query object — the durable artifact (US-08)"
    )
    chart_spec: Mapped[JSONDict | None]
    schedule: Mapped[str | None] = mapped_column(
        comment="Cron for scheduled refresh; NULL = ad hoc"
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        comment="Soft delete: product-visible restore matters here (DB conventions)"
    )


class Dashboard(Base, TenantScopedMixin, TimestampMixin):
    """System dashboards (owner NULL, read-only) and user dashboards share this table."""

    __tablename__ = "dashboard"
    __table_args__ = (UniqueConstraint("tenant_id", "slug"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    slug: Mapped[str]
    title: Mapped[str]
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("app_user.id", ondelete="CASCADE"),
        comment="NULL = system dashboard (executive, sales…) — not user-deletable",
    )
    layout_version: Mapped[int] = mapped_column(
        server_default=text("1"), comment="Optimistic concurrency token (Backend)"
    )

    tiles: Mapped[list["DashboardTile"]] = relationship(
        back_populates="dashboard", cascade="all, delete-orphan", order_by="DashboardTile.position"
    )


class DashboardTile(Base):
    __tablename__ = "dashboard_tile"
    __table_args__ = (Index("ix_dashboard_tile_board", "dashboard_id", "position"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    dashboard_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("dashboard.id", ondelete="CASCADE"))
    saved_query_id: Mapped[uuid.UUID | None] = mapped_column(
        # RESTRICT: warn the user about tile usage before deleting a query (DB R5)
        ForeignKey("saved_query.id", ondelete="RESTRICT"),
    )
    position: Mapped[int] = mapped_column(comment="Grid order within the dashboard")
    tile_spec: Mapped[JSONDict] = mapped_column(
        comment="Declarative {semantic_query|ref, chart_spec, thresholds} (ARCH)"
    )
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"))

    dashboard: Mapped[Dashboard] = relationship(back_populates="tiles")
