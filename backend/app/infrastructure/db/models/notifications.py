"""Notification delivery ledger (DB §36; PRD §32 flow)."""

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Index, text
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.db.models.base import (
    Base,
    JSONDict,
    TenantScopedMixin,
    enum_check,
    uuid_pk,
)
from app.infrastructure.db.models.enums import DeliveryStatus, NotificationChannel


class Notification(Base, TenantScopedMixin):
    __tablename__ = "notification"
    __table_args__ = (
        # Unread-badge hot path: tiny partial index (DB §12)
        Index(
            "ix_notification_unread",
            "user_id",
            text("created_at DESC"),
            postgresql_where=text("read_at IS NULL"),
        ),
        # Delivery-worker batch pull: pending only (DB §13 ix_notif_fanout)
        Index(
            "ix_notification_fanout",
            "tenant_id",
            "channel",
            postgresql_where=text("delivery_status = 'pending'"),
        ),
        enum_check("channel", NotificationChannel),
        enum_check("delivery_status", DeliveryStatus),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("app_user.id", ondelete="CASCADE"), index=True
    )
    channel: Mapped[str]
    event_type: Mapped[str] = mapped_column(
        comment="alert.critical | report.ready | rec.proposed …"
    )
    payload: Mapped[JSONDict] = mapped_column(comment="Template variables + deep link")
    severity: Mapped[str | None]
    delivery_status: Mapped[str] = mapped_column(server_default=text("'pending'"))
    delivery_attempts: Mapped[JSONDict] = mapped_column(
        server_default=text("'[]'::jsonb"), comment="[{at, outcome, error_class}] retry trail"
    )
    read_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"))
