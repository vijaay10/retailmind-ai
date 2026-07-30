"""Alert lifecycle: alert, alert_event, alert_mute (DB §12.2, §13, ARCH §29).

The alert row is a *record of detection* — reproducible via its snapshot pin.
Lifecycle transitions are decided by the domain state machine (Backend §3);
these tables only persist its outcomes.
"""

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Index, Numeric, text
from sqlalchemy.dialects.postgresql import JSONB, TSTZRANGE, ExcludeConstraint, Range
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.db.models.base import (
    Base,
    JSONDict,
    TenantScopedMixin,
    enum_check,
    uuid_pk,
)
from app.infrastructure.db.models.enums import AlertStatus, Severity


class Alert(Base, TenantScopedMixin):
    __tablename__ = "alert"
    __table_args__ = (
        # Inbox hot path: open criticals newest-first, index-only (DB §13 ix_alert_inbox)
        Index(
            "ix_alert_inbox",
            "tenant_id",
            "status",
            "severity",
            text("detected_at DESC"),
        ),
        # Flap suppression: recent alerts for a rule (DB §13 ix_alert_series)
        Index("ix_alert_series", "tenant_id", "rule_id", text("detected_at DESC")),
        # Tile-badge lookup by dimension slice (DB §12 GIN)
        Index(
            "ix_alert_series_key",
            "series_key",
            postgresql_using="gin",
            postgresql_ops={"series_key": "jsonb_path_ops"},
        ),
        enum_check("severity", Severity),
        enum_check("status", AlertStatus),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    rule_id: Mapped[uuid.UUID] = mapped_column(
        # RESTRICT: alerts are records of fact; rules soft-retire instead (DB §21 R3)
        ForeignKey("alert_rule.id", ondelete="RESTRICT"),
    )
    series_key: Mapped[JSONDict] = mapped_column(
        comment='Exact slice pinned, e.g. {"metric":"net_revenue","region":"SW"}; schema v2'
    )
    observed: Mapped[float] = mapped_column(Numeric(18, 4))
    expected_low: Mapped[float] = mapped_column(Numeric(18, 4))
    expected_high: Mapped[float] = mapped_column(Numeric(18, 4))
    severity: Mapped[str]
    status: Mapped[str] = mapped_column(server_default=text("'open'"))
    detector_scores: Mapped[JSONDict] = mapped_column(
        server_default=text("'{}'::jsonb"),
        comment="Per-detector votes/scores — detector-level explainability (AI §8)",
    )
    narration: Mapped[str | None] = mapped_column(
        comment="One-sentence grounded narration; NULL under T2 fallback (AI §0.2)"
    )
    detected_at: Mapped[datetime]
    acked_at: Mapped[datetime | None]
    acked_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("app_user.id", ondelete="SET NULL")
    )
    resolved_at: Mapped[datetime | None]
    data_snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("data_snapshot.id", ondelete="RESTRICT"),
        comment="Reproducibility pin (DB §21 R10) — undeletable while referenced",
    )
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"))

    events: Mapped[list["AlertEvent"]] = relationship(
        back_populates="alert", cascade="all, delete-orphan"
    )


class AlertEvent(Base):
    """Lifecycle audit trail per alert (opened/acked/resolved/muted, with actor + note)."""

    __tablename__ = "alert_event"
    __table_args__ = (Index("ix_alert_event_alert", "alert_id", "at"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    alert_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("alert.id", ondelete="CASCADE"))
    action: Mapped[str] = mapped_column(comment="opened | acked | resolved | muted | restated")
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("app_user.id", ondelete="SET NULL"), comment="NULL = system action"
    )
    note: Mapped[str | None]
    at: Mapped[datetime] = mapped_column(server_default=text("now()"))

    alert: Mapped[Alert] = relationship(back_populates="events")


class AlertMute(Base, TenantScopedMixin):
    """Scoped, time-boxed mutes. Overlap per series is impossible by constraint (DB §11).

    ``series_hash`` is the deterministic digest of the muted series_key slice;
    the GiST exclusion constraint (btree_gist extension) rejects overlapping
    windows for the same series — 'mute forever' cannot be expressed (UX S10).
    """

    __tablename__ = "alert_mute"
    __table_args__ = (
        ExcludeConstraint(
            ("series_hash", "="),
            ("during", "&&"),
            using="gist",
            name="alert_mute_no_overlap",
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    series_hash: Mapped[str] = mapped_column(comment="SHA-256 of canonicalized series_key")
    series_key: Mapped[JSONDict] = mapped_column(JSONB)
    during: Mapped[Range[datetime]] = mapped_column(TSTZRANGE)
    reason: Mapped[str | None]
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("app_user.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"))
