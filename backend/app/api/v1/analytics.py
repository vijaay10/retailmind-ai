"""Analytics endpoints — six domains, one governed path (Backend design §13).

Every domain exposes the same three verbs, because every analytics question a
dashboard asks is one of them:

    GET /{domain}/summary   headline totals for a period
    GET /{domain}/breakdown totals split by one or more dimensions
    GET /{domain}/trend     a metric over time

Uniformity is the point. A client that can render one domain can render all
six, and a new domain (suppliers, operations) is a registry entry rather than
a new API surface.

Access is by **module permission**: `analytics.revenue.read`,
`analytics.inventory.read`, and so on. That is what separates a Marketing user
from a Finance user — they share the action verbs but see different modules.
"""

from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Query

from app.api.deps import AnalyticsServiceDep, PrincipalDep
from app.core.pagination import PageParams, page_params
from app.schemas.analytics import (
    AnalyticsResponse,
    DomainCatalogEntry,
    QueryMeta,
    SummaryResponse,
    TrendPoint,
    TrendResponse,
)
from app.services.analytics.service import AnalyticsAnswer

router = APIRouter(prefix="/analytics", tags=["analytics"])

_FORBIDDEN = {
    "description": "The caller's role does not include this analytics module.",
    "content": {
        "application/problem+json": {
            "example": {
                "type": "https://retailmind.ai/errors/forbidden",
                "title": "Permission denied",
                "status": 403,
                "detail": "You do not have permission to perform this action.",
                "hint": "Requires the 'analytics.profitability.read' permission.",
            }
        }
    },
}

_UNPROCESSABLE = {
    "description": "A metric, dimension, or filter outside the governed registry.",
    "content": {
        "application/problem+json": {
            "example": {
                "type": "https://retailmind.ai/errors/invalid-state",
                "title": "Invalid state",
                "status": 422,
                "detail": "unknown metric 'revenu' for revenue",
                "hint": "Available metrics: aov, asp, discount_amount, net_revenue, …",
            }
        }
    },
}

DomainPath = Annotated[
    str,
    Path(description="revenue | store | customer | inventory | marketing | profitability"),
]


def _meta(answer: AnalyticsAnswer) -> QueryMeta:
    return QueryMeta(**answer.result.meta)


def _csv(value: str | None) -> list[str]:
    """Parse a comma-separated query parameter.

    Repeated `?metrics=a&metrics=b` is the alternative; comma-separated wins
    here because these lists are short and the URLs stay readable in a
    dashboard's network tab.
    """
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _filters(raw: list[str] | None) -> dict[str, str]:
    """Parse ``dimension:value`` filter pairs.

    Values are never interpolated — they bind as query parameters — and the
    dimension is validated against the registry before compilation, so an
    unknown key fails with a 422 naming the alternatives.
    """
    parsed: dict[str, str] = {}
    for item in raw or []:
        key, separator, value = item.partition(":")
        if separator and key.strip() and value.strip():
            parsed[key.strip()] = value.strip()
    return parsed


# ── Catalog ──────────────────────────────────────────────────────────


@router.get(
    "/catalog",
    response_model=list[DomainCatalogEntry],
    summary="Metrics and dimensions available to you",
)
async def catalog(
    principal: PrincipalDep, service: AnalyticsServiceDep
) -> list[DomainCatalogEntry]:
    """List every analytics domain the caller may query, with its vocabulary.

    Filtered by permission, so the response is a menu of what will actually
    work rather than a catalogue of dead ends. Each metric declares its
    **additivity** — whether it may be summed, summed except across time, or
    must be recomputed at the requested grain.
    """
    return [DomainCatalogEntry(**entry) for entry in service.catalog(principal)]


# ── The three verbs, per domain ──────────────────────────────────────


@router.get(
    "/{domain}/summary",
    response_model=SummaryResponse,
    summary="Headline totals for a period",
    responses={403: _FORBIDDEN, 404: {"description": "Unknown analytics domain."}},
)
async def summary(
    domain: DomainPath,
    principal: PrincipalDep,
    service: AnalyticsServiceDep,
    start_date: Annotated[date | None, Query(description="Inclusive start business date.")] = None,
    end_date: Annotated[date | None, Query(description="Inclusive end business date.")] = None,
    filter: Annotated[  # noqa: A002 — the query-string name users expect
        list[str] | None, Query(description="Repeatable `dimension:value` filter.")
    ] = None,
) -> SummaryResponse:
    """Every metric in the domain, totalled over the period.

    Omitting the period means the last 30 days, not all history — an unbounded
    scan is a self-inflicted outage the day the table gets big.
    """
    answer = await service.summary(
        principal,
        domain_key=domain,
        start_date=start_date,
        end_date=end_date,
        filters=_filters(filter),
    )
    totals: dict[str, Any] = answer.rows[0] if answer.rows else {}
    return SummaryResponse(
        domain=answer.domain,
        period={
            "start_date": start_date.isoformat() if start_date else None,
            "end_date": end_date.isoformat() if end_date else None,
        },
        totals=totals,
        meta=_meta(answer),
    )


@router.get(
    "/{domain}/breakdown",
    response_model=AnalyticsResponse,
    summary="Totals split by one or more dimensions",
    responses={403: _FORBIDDEN, 422: _UNPROCESSABLE},
)
async def breakdown(
    domain: DomainPath,
    principal: PrincipalDep,
    service: AnalyticsServiceDep,
    page: Annotated[PageParams, Depends(page_params)],
    metrics: Annotated[str, Query(description="Comma-separated metric keys.")],
    dimensions: Annotated[str, Query(description="Comma-separated dimension keys.")],
    start_date: Annotated[date | None, Query()] = None,
    end_date: Annotated[date | None, Query()] = None,
    sort_by: Annotated[str | None, Query(description="Metric or dimension to sort by.")] = None,
    descending: Annotated[bool, Query()] = True,
    filter: Annotated[list[str] | None, Query(description="`dimension:value` pairs.")] = None,  # noqa: A002
) -> AnalyticsResponse:
    """Group a domain's metrics by dimensions — the workhorse endpoint.

    Ratio metrics (AOV, margin rate, stockout rate) are **recomputed at the
    requested grain**, never averaged from a finer one; averaging an average
    is the most common wrong number in retail reporting.

    Results are ordered with a deterministic tiebreaker so paging is stable
    when several rows share the sort value.
    """
    answer = await service.query(
        principal,
        domain_key=domain,
        metrics=_csv(metrics),
        dimensions=_csv(dimensions),
        start_date=start_date,
        end_date=end_date,
        filters=_filters(filter),
        sort_by=sort_by,
        descending=descending,
        limit=page.limit,
        offset=page.offset,
    )
    return AnalyticsResponse(
        domain=answer.domain,
        metrics=answer.metrics,
        dimensions=answer.dimensions,
        data=answer.rows,
        meta=_meta(answer),
    )


@router.get(
    "/{domain}/trend",
    response_model=TrendResponse,
    summary="Metrics over time",
    responses={403: _FORBIDDEN, 404: {"description": "Domain has no time grain."}},
)
async def trend(
    domain: DomainPath,
    principal: PrincipalDep,
    service: AnalyticsServiceDep,
    metrics: Annotated[str, Query(description="Comma-separated metric keys.")],
    start_date: Annotated[date | None, Query()] = None,
    end_date: Annotated[date | None, Query()] = None,
    filter: Annotated[list[str] | None, Query(description="`dimension:value` pairs.")] = None,  # noqa: A002
) -> TrendResponse:
    """A daily series per metric — the shape every chart needs.

    Returns 404 for the customer domain: RFM segments are point-in-time
    aggregates, not a time series, and inventing a date axis for them would
    produce a chart that means nothing.
    """
    answer = await service.trend(
        principal,
        domain_key=domain,
        metrics=_csv(metrics),
        start_date=start_date,
        end_date=end_date,
        filters=_filters(filter),
    )
    series = [
        TrendPoint(
            business_date=row["business_date"],
            values={key: row[key] for key in answer.metrics if key in row},
        )
        for row in answer.rows
        if row.get("business_date") is not None
    ]
    return TrendResponse(
        domain=answer.domain, metrics=answer.metrics, series=series, meta=_meta(answer)
    )
