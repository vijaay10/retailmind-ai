"""Executive dashboard endpoints (UX spec §S1, Analytics §10).

Ten tiles, ten endpoints, plus one composite. The split is deliberate: tiles
refresh on different cadences and fail independently, so a slow inventory
query must not blank the revenue headline. The composite exists because the
*first* paint should be one round trip rather than eight.

Every endpoint anchors on the latest **business date in the warehouse**, not
on the wall clock. A dashboard that shows an empty "today" because the nightly
load has not finished is a dashboard nobody trusts.

Access follows the analytics module permissions, so the same URL returns a
full scorecard for a CEO and a 403 for a store manager reaching for margin.
"""

from dataclasses import asdict
from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Query

from app.api.deps import DashboardServiceDep, PrincipalDep
from app.domain.auth.permissions import Permission
from app.domain.shared.errors import AuthorizationError
from app.schemas.dashboard import (
    AlertCard,
    AlertsResponse,
    ExecutiveOverviewResponse,
    ForecastAccuracy,
    ForecastResponse,
    GrowthResponse,
    GrowthRow,
    InventoryRiskResponse,
    MetricCardModel,
    ProfitResponse,
    RecommendationCard,
    RecommendationsResponse,
    RevenueTodayResponse,
    RevenueTrendResponse,
    SectionMeta,
    StoreRankingResponse,
    TopProductsResponse,
    TrendPointModel,
)
from app.services.dashboard.service import (
    DashboardSection,
    MetricCard,
)

router = APIRouter(prefix="/dashboard", tags=["executive dashboard"])

_FORBIDDEN = {
    "description": "The caller's role does not include the module this tile reads.",
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

AsOf = Annotated[
    date | None,
    Query(description="Pin the dashboard to a business date. Defaults to the latest loaded day."),
]


def _cards(cards: list[MetricCard]) -> list[MetricCardModel]:
    # asdict, not __dict__: MetricCard uses slots, which removes __dict__.
    return [MetricCardModel(**asdict(card)) for card in cards]


def _meta(section: DashboardSection) -> SectionMeta:
    return SectionMeta(**{k: v for k, v in section.meta.items() if k in SectionMeta.model_fields})


# ── 1. Today's revenue ───────────────────────────────────────────────


@router.get(
    "/revenue/today",
    response_model=RevenueTodayResponse,
    summary="Today's revenue headline",
    responses={403: _FORBIDDEN},
)
async def revenue_today(
    principal: PrincipalDep, service: DashboardServiceDep, as_of: AsOf = None
) -> RevenueTodayResponse:
    """Revenue, units, orders, AOV, and discount rate for the latest business day.

    Each card compares against the **same weekday one week earlier** rather
    than yesterday: a Monday compared to a Sunday is a weekday-mix artefact,
    not a business signal.

    Movements under 0.5% report as `flat`, so ordinary noise is not styled as
    news.
    """
    cards, context = await service.revenue_today(principal, as_of=as_of)
    return RevenueTodayResponse(
        business_date=date.fromisoformat(context["business_date"]),
        comparison_date=date.fromisoformat(context["comparison_date"]),
        comparison_basis=context["comparison_basis"],
        cards=_cards(cards),
    )


# ── 2. Revenue trend ─────────────────────────────────────────────────


@router.get(
    "/revenue/trend",
    response_model=RevenueTrendResponse,
    summary="Daily revenue trend",
    responses={403: _FORBIDDEN},
)
async def revenue_trend(
    principal: PrincipalDep,
    service: DashboardServiceDep,
    days: Annotated[int, Query(ge=7, le=365, description="Days of history.")] = 30,
    as_of: AsOf = None,
) -> RevenueTrendResponse:
    """Daily revenue, units, and orders for the trend chart.

    Returns one point per business day with data. Gaps are absent rather than
    zero-filled — a zero and a missing day mean different things, and a chart
    that draws them identically hides pipeline failures.
    """
    section = await service.revenue_trend(principal, days=days, as_of=as_of)
    return RevenueTrendResponse(
        days=days,
        series=[TrendPointModel(**row) for row in section.rows],
        meta=_meta(section),
    )


# ── 3. Growth ────────────────────────────────────────────────────────


@router.get(
    "/growth",
    response_model=GrowthResponse,
    summary="Growth across day, week, and month horizons",
    responses={403: _FORBIDDEN},
)
async def growth(
    principal: PrincipalDep, service: DashboardServiceDep, as_of: AsOf = None
) -> GrowthResponse:
    """Revenue growth over three horizons at once.

    Each horizon compares against the immediately preceding window of equal
    length. Showing all three is deliberate: a bad day, a bad week, and a bad
    month call for different reactions, and forcing the reader to pick a
    period before seeing the numbers buries that.
    """
    section = await service.growth(principal, as_of=as_of)
    return GrowthResponse(
        as_of=date.fromisoformat(section.meta["as_of"]),
        horizons=[GrowthRow(**row) for row in section.rows],
    )


# ── 4. Profit ────────────────────────────────────────────────────────


@router.get(
    "/profit",
    response_model=ProfitResponse,
    summary="Margin headline and category breakdown",
    responses={403: _FORBIDDEN},
)
async def profit(
    principal: PrincipalDep,
    service: DashboardServiceDep,
    days: Annotated[int, Query(ge=1, le=365)] = 7,
    as_of: AsOf = None,
) -> ProfitResponse:
    """Gross margin, margin rate, COGS, and markdown impact, split by category.

    Requires `analytics.profitability.read`. Revenue access alone is not
    enough: cost is the number most organisations restrict, and margin
    exposes it.
    """
    cards, breakdown = await service.profit(principal, days=days, as_of=as_of)
    return ProfitResponse(
        period_days=days,
        cards=_cards(cards),
        by_category=breakdown.rows,
        meta=_meta(breakdown),
    )


# ── 5. Top products ──────────────────────────────────────────────────


@router.get(
    "/products/top",
    response_model=TopProductsResponse,
    summary="Best-performing products",
    responses={403: _FORBIDDEN},
)
async def top_products(
    principal: PrincipalDep,
    service: DashboardServiceDep,
    days: Annotated[int, Query(ge=1, le=365)] = 7,
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
    by: Annotated[
        str, Query(description="net_revenue | units_sold | margin_amount | margin_rate")
    ] = "net_revenue",
    as_of: AsOf = None,
) -> TopProductsResponse:
    """Top SKUs over the period, with revenue, units, margin, and margin rate.

    The ranking metric is a parameter because "top" is not one question: a
    merchandiser ranks by revenue, a buyer by margin, a planner by units. Each
    ordering tells a different story about the same period, and defaulting to
    revenue while hiding the others would pick a side.
    """
    section = await service.top_products(principal, days=days, limit=limit, by=by, as_of=as_of)
    return TopProductsResponse(
        period_days=days, ranked_by=by, products=section.rows, meta=_meta(section)
    )


# ── 6. Store rankings ────────────────────────────────────────────────


@router.get(
    "/stores/ranking",
    response_model=StoreRankingResponse,
    summary="Store league table",
    responses={403: _FORBIDDEN},
)
async def store_rankings(
    principal: PrincipalDep,
    service: DashboardServiceDep,
    days: Annotated[int, Query(ge=1, le=365)] = 7,
    limit: Annotated[int, Query(ge=1, le=100)] = 10,
    cluster: Annotated[
        str | None, Query(description="Restrict to one peer group, e.g. 'flagship/20k+'.")
    ] = None,
    as_of: AsOf = None,
) -> StoreRankingResponse:
    """Stores ranked by revenue, with margin, units, and AOV.

    **Read this within a cluster.** Comparing a flagship against an outlet is
    analytical malpractice: they serve different catchments with different
    footprints. Every row carries `store_cluster` so an unfiltered response can
    still be grouped correctly, and `cluster` scopes the ranking properly.
    """
    section = await service.store_rankings(
        principal, days=days, limit=limit, cluster=cluster, as_of=as_of
    )
    return StoreRankingResponse(
        period_days=days, cluster=cluster, stores=section.rows, meta=_meta(section)
    )


# ── 7. Inventory risk ────────────────────────────────────────────────


@router.get(
    "/inventory/risk",
    response_model=InventoryRiskResponse,
    summary="Categories carrying availability risk",
    responses={403: _FORBIDDEN},
)
async def inventory_risk(
    principal: PrincipalDep,
    service: DashboardServiceDep,
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
    as_of: AsOf = None,
) -> InventoryRiskResponse:
    """Category-region cells ranked by stockout rate.

    Ranked by *rate*, not by absolute stockouts: four stockouts across twenty
    positions is a crisis, ten across five thousand is background noise, and
    absolute counts invert that.

    Read for a **single day**. Inventory positions are semi-additive — summing
    them across dates would invent stock that never simultaneously existed.
    """
    section = await service.inventory_risk(principal, limit=limit, as_of=as_of)
    return InventoryRiskResponse(
        position_date=date.fromisoformat(section.meta["position_date"]),
        at_risk=section.rows,
        meta=_meta(section),
    )


# ── 8. Forecast ──────────────────────────────────────────────────────


@router.get(
    "/forecast",
    response_model=ForecastResponse,
    summary="Revenue forecast with intervals and model accuracy",
    responses={403: _FORBIDDEN},
)
async def forecast(
    principal: PrincipalDep,
    service: DashboardServiceDep,
    days: Annotated[int, Query(ge=7, le=90, description="Horizon in days.")] = 14,
    as_of: AsOf = None,
) -> ForecastResponse:
    """Forecast revenue with prediction intervals, and the model's track record.

    Accuracy travels **with** the forecast rather than behind a separate click.
    A planner deciding how much to trust a number needs to know how wrong the
    model has been, so `accuracy` carries WAPE, bias, and interval coverage.

    Coverage materially below the nominal band means the intervals are
    miscalibrated — the endpoint reports that rather than hiding it, because a
    band nobody can trust is worse than no band.

    The current model is `seasonal_naive_w4`, the published **baseline**. It is
    the benchmark every future model must beat (PRD G4: ≥15% better WAPE), not
    a placeholder pretending to be more.
    """
    section, accuracy = await service.forecast(principal, days=days, as_of=as_of)
    return ForecastResponse(
        horizon_days=days,
        series=section.rows,
        accuracy=ForecastAccuracy(**accuracy),
        meta=_meta(section),
    )


# ── 9. Alerts ────────────────────────────────────────────────────────


@router.get(
    "/alerts",
    response_model=AlertsResponse,
    summary="Open alerts needing attention",
    responses={403: _FORBIDDEN},
)
async def alerts(
    principal: PrincipalDep,
    service: DashboardServiceDep,
    limit: Annotated[int, Query(ge=1, le=50)] = 5,
) -> AlertsResponse:
    """Open and acknowledged alerts, most severe first.

    Resolved alerts are excluded: this surface shows what still needs
    attention. Each alert carries the exact slice that breached, the expected
    band, and how far outside it the observation fell — enough to judge
    severity without opening anything.
    """
    cards = await service.alerts(principal, limit=limit)
    counts = await service.alert_counts(principal)
    return AlertsResponse(
        counts=counts,
        alerts=[
            AlertCard(
                id=str(alert.id),
                metric_key=alert.metric_key,
                metric_label=alert.metric_label,
                severity=alert.severity,
                status=alert.status,
                scope=alert.series_key,
                observed=alert.observed,
                expected_low=alert.expected_low,
                expected_high=alert.expected_high,
                deviation_pct=alert.deviation_pct,
                narration=alert.narration,
                detected_at=alert.detected_at,
                has_investigation=alert.has_investigation,
                data_snapshot_id=alert.data_snapshot_id,
            )
            for alert in cards
        ],
    )


# ── 10. Recommendations ──────────────────────────────────────────────


@router.get(
    "/recommendations",
    response_model=RecommendationsResponse,
    summary="Highest-value proposed actions",
    responses={403: _FORBIDDEN},
)
async def recommendations(
    principal: PrincipalDep,
    service: DashboardServiceDep,
    limit: Annotated[int, Query(ge=1, le=50)] = 5,
) -> RecommendationsResponse:
    """Proposed actions ranked by expected impact.

    Expired proposals are filtered out rather than greyed: acting on a stale
    reorder is worse than never seeing it, because the quantities were computed
    against a stock position that has since moved.

    Every recommendation carries its estimation method and evidence pointers —
    an impact figure without a stated method is not actionable.
    """
    proposals = await service.recommendations(principal, limit=limit)
    return RecommendationsResponse(
        recommendations=[
            RecommendationCard(
                id=str(rec.id),
                type=rec.type,
                subject=rec.subject,
                expected_impact=rec.expected_impact,
                impact_value=rec.impact_value,
                rationale=rec.rationale,
                confidence=rec.confidence,
                expires_at=rec.expires_at,
                evidence=rec.evidence,
            )
            for rec in proposals
        ]
    )


# ── Composite ────────────────────────────────────────────────────────


@router.get(
    "/executive",
    response_model=ExecutiveOverviewResponse,
    summary="The whole dashboard in one request",
    responses={403: _FORBIDDEN},
)
async def executive_overview(
    principal: PrincipalDep, service: DashboardServiceDep, as_of: AsOf = None
) -> ExecutiveOverviewResponse:
    """Assemble every tile the caller may see, in one round trip.

    Sections the caller's role excludes are **named** in
    `sections_unavailable` rather than silently omitted, so the UI can explain
    the gap ("Margin requires Finance access") instead of rendering a hole.

    Individual endpoints remain the right choice for refreshes: they fail
    independently, so a slow inventory query cannot blank the revenue
    headline.
    """
    unavailable: list[str] = []

    revenue_cards, context = await service.revenue_today(principal, as_of=as_of)
    business_date = date.fromisoformat(context["business_date"])
    revenue = RevenueTodayResponse(
        business_date=business_date,
        comparison_date=date.fromisoformat(context["comparison_date"]),
        comparison_basis=context["comparison_basis"],
        cards=_cards(revenue_cards),
    )

    growth_section = await service.growth(principal, as_of=as_of)
    growth_block = GrowthResponse(
        as_of=date.fromisoformat(growth_section.meta["as_of"]),
        horizons=[GrowthRow(**row) for row in growth_section.rows],
    )

    # Each optional section degrades on its own. A tile the caller cannot see
    # is a permissions fact, not an error — the composite must not 403 as a
    # whole because one tile is restricted.
    alerts_block = AlertsResponse(counts={}, alerts=[])
    try:
        alerts_block = await alerts(principal, service, limit=5)
    except AuthorizationError:
        unavailable.append("alerts")

    recs_block = RecommendationsResponse(recommendations=[])
    try:
        recs_block = await recommendations(principal, service, limit=5)
    except AuthorizationError:
        unavailable.append("recommendations")

    products: list[dict[str, Any]] = []
    try:
        products = (await service.top_products(principal, days=7, limit=5, as_of=as_of)).rows
    except AuthorizationError:
        unavailable.append("top_products")

    risk: list[dict[str, Any]] = []
    if principal.has(Permission.ANALYTICS_INVENTORY_READ):
        risk = (await service.inventory_risk(principal, limit=5, as_of=as_of)).rows
    else:
        unavailable.append("inventory_risk")

    return ExecutiveOverviewResponse(
        business_date=business_date,
        revenue=revenue,
        growth=growth_block,
        alerts=alerts_block,
        recommendations=recs_block,
        top_products=products,
        inventory_risk=risk,
        sections_unavailable=unavailable,
    )
