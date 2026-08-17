"""Analytics repository — translates domain requests into governed queries.

Sits between services and the semantic client. Services
express intent in registry terms ("net revenue by region for this period");
this layer resolves those names to columns and expressions, applies the cache,
and hands back rows.

Every method takes the analytics *domain* explicitly rather than reading
ambient state, so a query can never accidentally read a relation the caller
was not entitled to.
"""

from dataclasses import dataclass
from datetime import date
from typing import Any

import structlog

from app.domain.shared.errors import ValidationDomainError
from app.infrastructure.cache.redis_cache import AnalyticsCache
from app.infrastructure.semantic.client import (
    QueryResult,
    SemanticLayerClient,
    SemanticQuery,
)
from app.services.analytics.registry import Domain

log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class AnalyticsRequest:
    """A resolved analytics question, in registry vocabulary."""

    domain: Domain
    metrics: list[str]
    dimensions: list[str]
    start_date: date | None = None
    end_date: date | None = None
    filters: dict[str, str] | None = None
    sort_by: str | None = None
    descending: bool = True
    limit: int = 100
    offset: int = 0

    def validate(self) -> None:
        """Reject anything outside the registry before it reaches SQL.

        This is where caller input stops being caller input: past this point
        only registry-declared names exist, so the compiler is working with a
        closed vocabulary.
        """
        if not self.metrics:
            raise ValidationDomainError("at least one metric is required")

        for key in self.metrics:
            if self.domain.metric(key) is None:
                available = ", ".join(sorted(self.domain.metrics))
                raise ValidationDomainError(
                    f"unknown metric '{key}' for {self.domain.key}",
                    hint=f"Available metrics: {available}",
                )

        for key in self.dimensions:
            if self.domain.dimension(key) is None:
                available = ", ".join(sorted(self.domain.dimensions))
                raise ValidationDomainError(
                    f"unknown dimension '{key}' for {self.domain.key}",
                    hint=f"Available dimensions: {available}",
                )

        for key in self.filters or {}:
            if self.domain.dimension(key) is None:
                raise ValidationDomainError(f"cannot filter on unknown dimension '{key}'")

        if self.sort_by and self.sort_by not in {*self.metrics, *self.dimensions}:
            raise ValidationDomainError(
                f"cannot sort by '{self.sort_by}'",
                hint="Sort only by a metric or dimension included in the request.",
            )

        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValidationDomainError("start_date must not be after end_date")


class AnalyticsRepository:
    """Executes analytics requests, with caching."""

    def __init__(self, client: SemanticLayerClient, cache: AnalyticsCache) -> None:
        self._client = client
        self._cache = cache

    async def run(self, request: AnalyticsRequest, *, tenant_id: str) -> QueryResult:
        """Compile, cache-check, execute, and cache."""
        request.validate()
        query = self._build(request)

        snapshot_id, freshness = self._client.snapshot()
        cache_key = self._cache.key(
            query_fingerprint=query.fingerprint(),
            snapshot_id=snapshot_id,
            tenant_id=tenant_id,
        )

        cached = await self._cache.get(cache_key)
        if cached is not None:
            return QueryResult(
                rows=cached["rows"],
                columns=cached["columns"],
                row_count=len(cached["rows"]),
                compiled_sql=cached["compiled_sql"],
                snapshot_id=snapshot_id,
                freshness=freshness,
                cache="hit",
                truncated=cached.get("truncated", False),
            )

        result = self._client.execute(query)
        await self._cache.set(
            cache_key,
            {
                "rows": result.rows,
                "columns": result.columns,
                "compiled_sql": result.compiled_sql,
                "truncated": result.truncated,
            },
        )
        return result

    def _build(self, request: AnalyticsRequest) -> SemanticQuery:
        """Resolve registry names to a compiled query object."""
        domain = request.domain

        group_columns = [domain.dimensions[key].column for key in request.dimensions]
        aggregates = {key: domain.metrics[key].expression for key in request.metrics}

        filters: list[tuple[str, str, Any]] = []
        if domain.date_column and request.start_date:
            filters.append((domain.date_column, "gte", request.start_date))
        if domain.date_column and request.end_date:
            filters.append((domain.date_column, "lte", request.end_date))
        for key, value in (request.filters or {}).items():
            filters.append((domain.dimensions[key].column, "eq", value))

        order_by: list[tuple[str, bool]] = []
        if request.sort_by:
            # Sorting by an alias is safe: it was validated against the request's
            # own metric/dimension list, so it always exists in the projection.
            column = (
                request.sort_by
                if request.sort_by in request.metrics
                else domain.dimensions[request.sort_by].column
            )
            order_by.append((column, request.descending))
        elif request.metrics:
            order_by.append((request.metrics[0], True))

        # Every grouping column becomes a tiebreaker, in order. Two reasons:
        # pagination stays stable when rows share the primary sort value
        # (Backend), and multi-dimensional results arrive fully ordered —
        # a retention curve sorted by cohort but not by week is a scatter of
        # points the client has to re-sort before it can draw a line.
        sorted_columns = {column for column, _ in order_by}
        for column in group_columns:
            if column not in sorted_columns:
                order_by.append((column, False))

        return SemanticQuery(
            relation=domain.relation,
            select=group_columns,
            aggregates=aggregates,
            filters=filters,
            group_by=group_columns,
            order_by=order_by,
            limit=request.limit,
            offset=request.offset,
        )
