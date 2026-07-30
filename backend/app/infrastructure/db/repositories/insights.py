"""Read repositories for alerts and recommendations (Backend design §4).

Both are OLTP reads scoped to the caller's tenant. They are deliberately
read-only here: the alert and recommendation *lifecycles* (acknowledge,
accept, dismiss) belong to their own services, where the domain state machines
live. A dashboard reads; it does not decide.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.db.models import (
    Alert,
    AlertRule,
    MetricConfig,
    Recommendation,
)

#: Severity ordering for "most urgent first". Text sorting would put
#: 'critical' after 'warn' alphabetically, which is precisely backwards.
_SEVERITY_RANK = {"critical": 0, "warn": 1, "info": 2}


@dataclass(frozen=True, slots=True)
class AlertSummary:
    """One open alert, flattened for display."""

    id: uuid.UUID
    metric_key: str
    metric_label: str
    severity: str
    status: str
    series_key: dict[str, Any]
    observed: float
    expected_low: float
    expected_high: float
    narration: str | None
    detected_at: datetime
    has_investigation: bool
    data_snapshot_id: str

    @property
    def deviation_pct(self) -> float | None:
        """How far outside the expected band, as a share of the nearer bound.

        Signed: negative means below the band. This is the number that decides
        display ordering, so it is computed once here rather than in each
        consumer.
        """
        if self.observed < self.expected_low and self.expected_low:
            return round((self.observed - self.expected_low) / abs(self.expected_low), 4)
        if self.observed > self.expected_high and self.expected_high:
            return round((self.observed - self.expected_high) / abs(self.expected_high), 4)
        return None


@dataclass(frozen=True, slots=True)
class RecommendationSummary:
    id: uuid.UUID
    type: str
    subject: dict[str, Any]
    expected_impact: dict[str, Any]
    rationale: str | None
    confidence: str
    score: float
    status: str
    expires_at: datetime
    evidence: dict[str, Any]

    @property
    def impact_value(self) -> float:
        """Headline dollar impact, or zero when the estimate carries none."""
        raw = self.expected_impact.get("value_usd", 0)
        try:
            return float(raw)
        except (TypeError, ValueError):
            return 0.0


class AlertReadRepository:
    """Open-alert queries for dashboards and the insight feed."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def open_alerts(
        self, tenant_id: uuid.UUID, *, limit: int = 10, severities: list[str] | None = None
    ) -> list[AlertSummary]:
        """Open and acknowledged alerts, most severe and most recent first.

        Resolved alerts are excluded: a dashboard shows what still needs
        attention, and a resolved alert is history the Alert Center owns.
        """
        stmt: Select[Any] = (
            select(
                Alert.id,
                MetricConfig.metric_key,
                MetricConfig.display_name,
                Alert.severity,
                Alert.status,
                Alert.series_key,
                Alert.observed,
                Alert.expected_low,
                Alert.expected_high,
                Alert.narration,
                Alert.detected_at,
                Alert.data_snapshot_id,
            )
            .join(AlertRule, AlertRule.id == Alert.rule_id)
            .join(MetricConfig, MetricConfig.id == AlertRule.metric_config_id)
            .where(Alert.tenant_id == tenant_id, Alert.status != "resolved")
        )
        if severities:
            stmt = stmt.where(Alert.severity.in_(severities))

        rows = (await self._session.execute(stmt)).all()

        summaries = [
            AlertSummary(
                id=row.id,
                metric_key=row.metric_key,
                metric_label=row.display_name,
                severity=row.severity,
                status=row.status,
                series_key=row.series_key or {},
                observed=float(row.observed),
                expected_low=float(row.expected_low),
                expected_high=float(row.expected_high),
                narration=row.narration,
                detected_at=row.detected_at,
                # Wired when the RCA engine lands (v0.3); the column exists so
                # the dashboard contract does not change when it does.
                has_investigation=False,
                data_snapshot_id=row.data_snapshot_id,
            )
            for row in rows
        ]

        # Sorted in Python rather than SQL: severity is stored as text for
        # migration friendliness (DB §11), so ordering needs the rank map above.
        summaries.sort(
            key=lambda a: (_SEVERITY_RANK.get(a.severity, 9), -a.detected_at.timestamp())
        )
        return summaries[:limit]

    async def open_count_by_severity(self, tenant_id: uuid.UUID) -> dict[str, int]:
        """Counts for the header badge, without fetching the alerts themselves."""
        from sqlalchemy import func

        rows = (
            await self._session.execute(
                select(Alert.severity, func.count())
                .where(Alert.tenant_id == tenant_id, Alert.status != "resolved")
                .group_by(Alert.severity)
            )
        ).all()
        return {severity: int(count) for severity, count in rows}


class RecommendationReadRepository:
    """Proposed-recommendation queries for the decision panel."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def top_proposed(
        self, tenant_id: uuid.UUID, *, limit: int = 5
    ) -> list[RecommendationSummary]:
        """Highest-scoring unexpired proposals.

        Expired recommendations are filtered here rather than shown greyed
        out: acting on a stale reorder is worse than not seeing it, because
        the quantities were computed against a position that has since moved.
        """
        rows = (
            await self._session.scalars(
                select(Recommendation)
                .where(
                    Recommendation.tenant_id == tenant_id,
                    Recommendation.status == "proposed",
                    Recommendation.expires_at > datetime.now(tz=UTC),
                )
                .order_by(Recommendation.score.desc())
                .limit(limit)
            )
        ).all()

        return [
            RecommendationSummary(
                id=row.id,
                type=row.type,
                subject=row.subject,
                expected_impact=row.expected_impact,
                rationale=row.rationale,
                confidence=row.confidence,
                score=float(row.score),
                status=row.status,
                expires_at=row.expires_at,
                evidence=row.evidence,
            )
            for row in rows
        ]
