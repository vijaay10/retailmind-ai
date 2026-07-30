"""Analytics DTOs (Backend design §15).

Responses always carry a `meta` block: row count, the warehouse snapshot the
numbers came from, freshness, and whether the answer was cached. A number
without provenance is a number nobody can defend, so provenance is structural
here rather than optional.
"""

from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ResponseModel(BaseModel):
    model_config = ConfigDict(from_attributes=False)


class QueryMeta(ResponseModel):
    """Where these numbers came from."""

    row_count: int
    data_snapshot_id: str | None = Field(
        default=None, description="Warehouse publish these rows were computed from."
    )
    freshness: str | None = Field(default=None, description="Latest business date in the data.")
    cache: str = Field(description="hit | miss — whether the answer was served from cache.")
    elapsed_ms: float
    truncated: bool = Field(
        default=False,
        description="True when the result hit the row cap; narrow the period or add filters.",
    )


class AnalyticsResponse(ResponseModel):
    """A dimensional result set."""

    domain: str
    metrics: list[str]
    dimensions: list[str]
    data: list[dict[str, Any]]
    meta: QueryMeta


class SummaryResponse(ResponseModel):
    """Headline totals for a domain, one row."""

    domain: str
    period: dict[str, str | None]
    totals: dict[str, Any]
    meta: QueryMeta


class TrendPoint(ResponseModel):
    business_date: date
    values: dict[str, Any]


class TrendResponse(ResponseModel):
    domain: str
    metrics: list[str]
    series: list[TrendPoint]
    meta: QueryMeta


class MetricDescriptor(ResponseModel):
    key: str
    label: str
    unit: str
    additivity: str = Field(
        description=(
            "full — sums across every dimension; semi — sums across dimensions "
            "but not time (inventory positions); non — never summed (ratios and "
            "distinct counts), recomputed at the requested grain."
        )
    )
    description: str


class DimensionDescriptor(ResponseModel):
    key: str
    label: str


class DomainCatalogEntry(ResponseModel):
    domain: str
    label: str
    has_time_grain: bool
    metrics: list[MetricDescriptor]
    dimensions: list[DimensionDescriptor]
