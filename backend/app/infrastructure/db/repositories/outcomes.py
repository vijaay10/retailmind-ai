"""Repository for recommendation outcome measurement records.

Provides read access to measured outcomes for calibration analysis. Write
operations happen through the outcome measurement service (when implemented).
"""

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.infrastructure.db.models.enums import OutcomeStatus
from app.infrastructure.db.models.recommendations import RecommendationOutcome


class OutcomeRepository:
    """Read repository for recommendation outcomes — calibration data source."""

    def __init__(self, session: AsyncSession, tenant_id: uuid.UUID) -> None:
        self._session = session
        self._tenant_id = tenant_id

    async def find_measured(
        self,
        *,
        category: str | None = None,
        generator: str | None = None,
        horizon_days: int | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Query measured outcomes and convert to dict format for calculator.

        Args:
            category: Filter by recommendation category (inventory, pricing, etc.)
            generator: Alias for category (same meaning in this context)
            horizon_days: Filter by measurement horizon (1, 7, 14, 30)
            limit: Maximum number of outcomes to return

        Returns:
            List of outcome dicts with fields expected by calibration calculator:
            - realized_impact
            - expected_impact
            - realization_ratio
            - absolute_error
            - direction_correct
            - confidence (from recommendation)
            - category
            - baseline_method
            - estimate_basis (from recommendation)
            - horizon_days
        """
        # Join to recommendation to get category and other metadata.
        # `.join()` alone only affects the SQL WHERE/filter clause — it does
        # not populate `outcome.recommendation` on the returned ORM objects,
        # so accessing it below (building the result dicts) would lazy-load
        # per row. Under AsyncSession that raises MissingGreenlet rather than
        # quietly issuing a sync query — never hit before because every
        # caller that tried to write real data through this join failed on
        # an earlier bug first (see Prompt 11.5). `selectinload` batches the
        # relationship into one extra query instead.
        stmt = (
            select(RecommendationOutcome)
            .join(RecommendationOutcome.recommendation)
            .options(selectinload(RecommendationOutcome.recommendation))
            .where(
                RecommendationOutcome.recommendation.has(tenant_id=self._tenant_id),
                RecommendationOutcome.status == OutcomeStatus.MEASURED,
            )
        )

        # Apply filters. `category` (inventory | pricing | promotion | store |
        # marketing | customer | supplier — the generator vocabulary this API
        # documents) — not `type` (reorder | markdown | promo | assortment,
        # the kind of action). The two are disjoint; filtering on `type`
        # could never match a generator name for any real caller.
        filter_category = category or generator
        if filter_category:
            stmt = stmt.where(RecommendationOutcome.recommendation.has(category=filter_category))

        if horizon_days is not None:
            stmt = stmt.where(RecommendationOutcome.window_days == horizon_days)

        if limit:
            stmt = stmt.limit(limit)

        result = await self._session.execute(stmt)
        outcomes = result.scalars().all()

        # Convert to dict format expected by calculator
        return [
            {
                "realized_impact": float(outcome.realized_impact)
                if outcome.realized_impact is not None
                else 0.0,
                "expected_impact": float(outcome.expected_impact)
                if outcome.expected_impact is not None
                else 0.0,
                "realization_ratio": (
                    float(outcome.realization_ratio)
                    if outcome.realization_ratio is not None
                    else None
                ),
                "absolute_error": float(outcome.absolute_error)
                if outcome.absolute_error is not None
                else 0.0,
                "direction_correct": outcome.direction_correct or False,
                "confidence": (
                    outcome.recommendation.confidence if outcome.recommendation else "unknown"
                ),
                "category": outcome.recommendation.category
                if outcome.recommendation and outcome.recommendation.category
                else "unknown",
                "baseline_method": outcome.baseline_method or "unknown",
                "estimate_basis": (
                    outcome.recommendation.expected_impact.get("method")
                    if outcome.recommendation
                    and outcome.recommendation.expected_impact
                    and isinstance(outcome.recommendation.expected_impact, dict)
                    else "unknown"
                ),
                "horizon_days": outcome.window_days or 0,
            }
            for outcome in outcomes
        ]

    async def count_by_status(self, status: OutcomeStatus) -> int:
        """Count outcomes by status."""
        stmt = (
            select(RecommendationOutcome)
            .join(RecommendationOutcome.recommendation)
            .where(
                RecommendationOutcome.recommendation.has(tenant_id=self._tenant_id),
                RecommendationOutcome.status == status,
            )
        )
        result = await self._session.execute(stmt)
        return len(result.scalars().all())
