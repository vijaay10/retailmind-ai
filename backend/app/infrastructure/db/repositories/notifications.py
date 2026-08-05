"""Notification persistence: the in-app inbox and the suppression history.

The ledger doubles as the suppression memory. Rather than keeping a separate
"last sent" table that can drift out of step with what was actually delivered,
the fingerprint is written into the notification payload and read back to
decide what is a duplicate. One source of truth means the system cannot
believe it sent something it did not.
"""

import uuid
from datetime import datetime
from typing import Any

import structlog
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.db.models.notifications import Notification

log = structlog.get_logger(__name__)

#: How far back the suppression memory reaches. Longer than the widest
#: re-notification window so nothing falls out of memory while still quiet,
#: and short enough that the lookup stays cheap.
SUPPRESSION_LOOKBACK_DAYS = 7

#: Inbox page size. An inbox is read, not exported.
DEFAULT_PAGE = 50


class NotificationRepository:
    """Writes the ledger and serves the inbox."""

    def __init__(self, session: AsyncSession, tenant_id: uuid.UUID) -> None:
        self._session = session
        self._tenant_id = tenant_id

    async def record(
        self, *, user_id: str, channel: str, event_type: str, payload: dict[str, Any]
    ) -> None:
        """Queue one notification.

        Written as `pending`: the row is the record that a notification is
        *owed*, and the delivery worker moves it to sent or failed. Writing
        `sent` here would mean an in-app badge appearing for a message the
        delivery step never managed to render.
        """
        self._session.add(
            Notification(
                tenant_id=self._tenant_id,
                user_id=uuid.UUID(user_id),
                channel=channel,
                event_type=event_type,
                payload=payload,
                severity=str(payload.get("severity") or "info"),
            )
        )
        await self._session.flush()

    async def last_notified(self) -> dict[str, datetime]:
        """When each alert fingerprint was last sent.

        Read from the ledger itself rather than a parallel table, so the
        suppression decision is grounded in what was actually written.
        """
        cutoff = func.now() - func.make_interval(0, 0, 0, SUPPRESSION_LOOKBACK_DAYS)
        statement = (
            select(
                Notification.payload["fingerprint"].astext.label("fingerprint"),
                func.max(Notification.created_at).label("last_sent"),
            )
            .where(
                Notification.tenant_id == self._tenant_id,
                Notification.created_at >= cutoff,
                Notification.payload["fingerprint"].astext.is_not(None),
            )
            .group_by(Notification.payload["fingerprint"].astext)
        )
        rows = (await self._session.execute(statement)).all()
        return {row.fingerprint: row.last_sent for row in rows if row.fingerprint}

    async def inbox(
        self, user_id: uuid.UUID, *, unread_only: bool = False, limit: int = DEFAULT_PAGE
    ) -> list[Notification]:
        statement = (
            select(Notification)
            .where(
                Notification.tenant_id == self._tenant_id,
                Notification.user_id == user_id,
                Notification.channel == "in_app",
            )
            .order_by(Notification.created_at.desc())
            .limit(limit)
        )
        if unread_only:
            statement = statement.where(Notification.read_at.is_(None))
        return list((await self._session.execute(statement)).scalars())

    async def unread_count(self, user_id: uuid.UUID) -> int:
        statement = select(func.count()).where(
            Notification.tenant_id == self._tenant_id,
            Notification.user_id == user_id,
            Notification.channel == "in_app",
            Notification.read_at.is_(None),
        )
        return int((await self._session.execute(statement)).scalar_one())

    async def mark_read(self, user_id: uuid.UUID, notification_ids: list[uuid.UUID]) -> int:
        """Mark notifications read, scoped to their owner.

        The user filter is not redundant with the id filter: without it, a
        caller who guesses an identifier could mark somebody else's inbox read.
        """
        if not notification_ids:
            return 0
        statement = (
            update(Notification)
            .where(
                Notification.tenant_id == self._tenant_id,
                Notification.user_id == user_id,
                Notification.id.in_(notification_ids),
                Notification.read_at.is_(None),
            )
            .values(read_at=func.now())
        )
        # `returning` rather than rowcount: the typed Result API does not
        # expose a row count for an UPDATE, and counting the returned ids is
        # exact rather than driver-dependent.
        marked = (await self._session.execute(statement.returning(Notification.id))).scalars()
        return len(list(marked))
