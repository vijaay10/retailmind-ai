"""LLM usage tracking and cost accounting.

Records every LLM request to the llm_request_log table for audit trail,
debugging, and cost control. Provides budget checking and cost reporting
per tenant.
"""

import uuid
from datetime import datetime, timedelta

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.db.models.ai import LlmRequestLog
from app.infrastructure.llm.models import LLMResponse

log = structlog.get_logger(__name__)


class LlmUsageRepository:
    """Tracks LLM usage and enforces budget limits."""

    def __init__(self, session: AsyncSession, tenant_id: uuid.UUID) -> None:
        self._session = session
        self._tenant_id = tenant_id

    async def record_request(
        self,
        response: LLMResponse,
        user_id: uuid.UUID | None = None,
    ) -> None:
        """Record an LLM request to the audit log.

        Args:
            response: LLMResponse containing usage data
            user_id: Optional user who initiated the request
        """
        self._session.add(
            LlmRequestLog(
                request_id=response.request_id,
                tenant_id=self._tenant_id,
                user_id=user_id,
                model_id=response.model_id,
                prompt_version=response.prompt_version,
                tokens_in=response.tokens_in,
                tokens_out=response.tokens_out,
                estimated_cost_usd=response.estimated_cost_usd,
                latency_ms=response.latency_ms,
                status=response.status,
                error=response.error,
            )
        )
        await self._session.flush()

        log.info(
            "llm_usage_recorded",
            request_id=response.request_id,
            tenant_id=str(self._tenant_id),
            tokens_total=response.tokens_in + response.tokens_out,
            cost_usd=response.estimated_cost_usd,
            status=response.status,
        )

    async def get_daily_token_usage(self, date: datetime | None = None) -> int:
        """Get total tokens used today by this tenant.

        Args:
            date: Date to check (defaults to today)

        Returns:
            Total tokens (input + output) used
        """
        if date is None:
            date = datetime.utcnow()

        # Start of day in UTC
        start_of_day = date.replace(hour=0, minute=0, second=0, microsecond=0)
        end_of_day = start_of_day + timedelta(days=1)

        statement = select(
            func.coalesce(func.sum(LlmRequestLog.tokens_in + LlmRequestLog.tokens_out), 0)
        ).where(
            LlmRequestLog.tenant_id == self._tenant_id,
            LlmRequestLog.created_at >= start_of_day,
            LlmRequestLog.created_at < end_of_day,
            LlmRequestLog.status == "success",  # Only count successful requests
        )

        result = await self._session.execute(statement)
        return int(result.scalar_one())

    async def get_daily_cost(self, date: datetime | None = None) -> float:
        """Get total cost incurred today by this tenant.

        Args:
            date: Date to check (defaults to today)

        Returns:
            Total cost in USD
        """
        if date is None:
            date = datetime.utcnow()

        start_of_day = date.replace(hour=0, minute=0, second=0, microsecond=0)
        end_of_day = start_of_day + timedelta(days=1)

        statement = select(func.coalesce(func.sum(LlmRequestLog.estimated_cost_usd), 0.0)).where(
            LlmRequestLog.tenant_id == self._tenant_id,
            LlmRequestLog.created_at >= start_of_day,
            LlmRequestLog.created_at < end_of_day,
            LlmRequestLog.status == "success",
        )

        result = await self._session.execute(statement)
        return float(result.scalar_one())

    async def check_budget_limit(
        self, daily_token_budget: int, max_cost_per_request: float
    ) -> tuple[bool, str | None]:
        """Check if tenant is within budget limits.

        Args:
            daily_token_budget: Maximum tokens per day
            max_cost_per_request: Maximum cost per request in USD

        Returns:
            Tuple of (within_budget, reason_if_exceeded)
        """
        daily_usage = await self.get_daily_token_usage()

        if daily_usage >= daily_token_budget:
            return (
                False,
                f"Daily token budget exceeded: {daily_usage}/{daily_token_budget}",
            )

        # max_cost_per_request is checked before making the request,
        # not after, so we just return True here
        return (True, None)

    async def get_usage_summary(
        self, start_date: datetime, end_date: datetime
    ) -> dict[str, int | float]:
        """Get usage summary for a date range.

        Args:
            start_date: Start of range (inclusive)
            end_date: End of range (exclusive)

        Returns:
            Summary dict with total_requests, total_tokens, total_cost, etc.
        """
        statement = select(
            func.count(LlmRequestLog.id).label("total_requests"),
            func.sum(LlmRequestLog.tokens_in + LlmRequestLog.tokens_out).label("total_tokens"),
            func.sum(LlmRequestLog.estimated_cost_usd).label("total_cost"),
            func.avg(LlmRequestLog.latency_ms).label("avg_latency_ms"),
        ).where(
            LlmRequestLog.tenant_id == self._tenant_id,
            LlmRequestLog.created_at >= start_date,
            LlmRequestLog.created_at < end_date,
        )

        result = await self._session.execute(statement)
        row = result.one()

        return {
            "total_requests": row.total_requests or 0,
            "total_tokens": row.total_tokens or 0,
            "total_cost_usd": float(row.total_cost or 0.0),
            "avg_latency_ms": float(row.avg_latency_ms or 0.0),
        }

    async def get_error_count(self, start_date: datetime, end_date: datetime) -> dict[str, int]:
        """Get error counts by status for a date range.

        Args:
            start_date: Start of range (inclusive)
            end_date: End of range (exclusive)

        Returns:
            Dict mapping status to count
        """
        statement = (
            select(LlmRequestLog.status, func.count(LlmRequestLog.id).label("count"))
            .where(
                LlmRequestLog.tenant_id == self._tenant_id,
                LlmRequestLog.created_at >= start_date,
                LlmRequestLog.created_at < end_date,
                LlmRequestLog.status != "success",
            )
            .group_by(LlmRequestLog.status)
        )

        result = await self._session.execute(statement)
        return {row.status: int(row.count) for row in result.all()}  # type: ignore[call-overload]

    async def get_recent_failures(self, limit: int = 10) -> list[LlmRequestLog]:
        """Get recent failed requests for debugging.

        Args:
            limit: Maximum number of failures to return

        Returns:
            List of failed LLM request logs
        """
        statement = (
            select(LlmRequestLog)
            .where(
                LlmRequestLog.tenant_id == self._tenant_id,
                LlmRequestLog.status != "success",
            )
            .order_by(LlmRequestLog.created_at.desc())
            .limit(limit)
        )

        result = await self._session.execute(statement)
        return list(result.scalars().all())
