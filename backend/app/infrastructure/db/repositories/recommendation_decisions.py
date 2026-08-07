"""The decision ledger for computed recommendations.

The engine recomputes its proposals on every request, so nothing about a
proposal is stored until a human acts on one. This repository is where that
act lands — and it is the only place in the recommendation path that writes.

Deciding twice on the same subject **overwrites** rather than appending. A
card that could render as both accepted and dismissed is a card nobody can
act on, and the audit ledger already keeps the history of who changed their
mind.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.db.models import RecommendationDecision

log = structlog.get_logger(__name__)

#: How far back the console shows decisions. Long enough to cover a planning
#: cycle, short enough that a decision from last quarter does not silently
#: suppress a card whose underlying position has changed completely.
DECISION_WINDOW_DAYS = 90


@dataclass(frozen=True, slots=True)
class Decision:
    """One recorded decision, flattened for display."""

    decision_key: str
    action: str
    category: str
    subject: str
    action_text: str
    expected_profit: float | None
    estimate_basis: str | None
    reason_code: str | None
    note: str | None
    decided_by: uuid.UUID
    decided_at: datetime

    def as_dict(self) -> dict[str, Any]:
        return {
            "decision_key": self.decision_key,
            "action": self.action,
            "category": self.category,
            "subject": self.subject,
            "action_text": self.action_text,
            "expected_profit": (
                float(self.expected_profit) if self.expected_profit is not None else None
            ),
            "estimate_basis": self.estimate_basis,
            "reason_code": self.reason_code,
            "note": self.note,
            "decided_at": self.decided_at.isoformat(),
        }


class RecommendationDecisionRepository:
    """Reads and writes what humans decided about proposed actions."""

    def __init__(self, session: AsyncSession, tenant_id: uuid.UUID) -> None:
        self._session = session
        self._tenant_id = tenant_id

    async def record(
        self,
        *,
        decision_key: str,
        action: str,
        category: str,
        subject: str,
        action_text: str,
        expected_profit: float | None,
        estimate_basis: str | None,
        reason_code: str | None,
        note: str | None,
        decided_by: uuid.UUID,
    ) -> Decision:
        """Record a decision, replacing any earlier one for the same subject.

        An upsert rather than a read-then-write: two managers clicking accept
        on the same card in the same second would otherwise race, and one of
        them would get a unique-violation traceback for having agreed.
        """
        statement = (
            insert(RecommendationDecision)
            .values(
                tenant_id=self._tenant_id,
                decision_key=decision_key,
                action=action,
                category=category,
                subject=subject,
                action_text=action_text,
                expected_profit=expected_profit,
                estimate_basis=estimate_basis,
                reason_code=reason_code,
                note=note,
                decided_by=decided_by,
            )
            .on_conflict_do_update(
                index_elements=["tenant_id", "decision_key"],
                set_={
                    "action": action,
                    "action_text": action_text,
                    "expected_profit": expected_profit,
                    "estimate_basis": estimate_basis,
                    "reason_code": reason_code,
                    "note": note,
                    "decided_by": decided_by,
                    "decided_at": datetime.now(tz=UTC),
                },
            )
            .returning(RecommendationDecision)
        )
        row = (await self._session.execute(statement)).scalar_one()
        await self._session.commit()

        log.info(
            "recommendation.decided",
            decision_key=decision_key,
            action=action,
            category=category,
        )
        return _to_decision(row)

    async def current(self, keys: list[str] | None = None) -> dict[str, Decision]:
        """Decisions in force, keyed by ``decision_key``."""
        cutoff = datetime.now(tz=UTC) - timedelta(days=DECISION_WINDOW_DAYS)
        statement = select(RecommendationDecision).where(
            RecommendationDecision.tenant_id == self._tenant_id,
            RecommendationDecision.decided_at >= cutoff,
        )
        if keys:
            statement = statement.where(RecommendationDecision.decision_key.in_(keys))

        rows = (await self._session.execute(statement)).scalars()
        return {row.decision_key: _to_decision(row) for row in rows}

    async def recent(self, *, limit: int = 50) -> list[Decision]:
        """The decision log, newest first — what this team has been doing."""
        statement = (
            select(RecommendationDecision)
            .where(RecommendationDecision.tenant_id == self._tenant_id)
            .order_by(RecommendationDecision.decided_at.desc())
            .limit(limit)
        )
        return [_to_decision(row) for row in (await self._session.execute(statement)).scalars()]


def _to_decision(row: RecommendationDecision) -> Decision:
    return Decision(
        decision_key=row.decision_key,
        action=row.action,
        category=row.category,
        subject=row.subject,
        action_text=row.action_text,
        expected_profit=float(row.expected_profit) if row.expected_profit is not None else None,
        estimate_basis=row.estimate_basis,
        reason_code=row.reason_code,
        note=row.note,
        decided_by=row.decided_by,
        decided_at=row.decided_at,
    )
