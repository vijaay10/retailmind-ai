"""Executive dashboard DTOs.

Every response carries provenance (`meta`) and, where a number is compared,
the basis of that comparison. A dashboard tile that shows "-12.4%" without
saying what it is 12.4% *of* is a tile that starts arguments.
"""

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ResponseModel(BaseModel):
    model_config = ConfigDict(from_attributes=False)


class MetricCardModel(ResponseModel):
    """One scorecard tile."""

    key: str
    label: str
    value: float | None
    unit: str = Field(description="currency | units | count | rate")
    prior_value: float | None = None
    change_pct: float | None = Field(
        default=None, description="Signed fraction, e.g. -0.124 for a 12.4% decline."
    )
    direction: str = Field(
        default="flat",
        description="up | down | flat — flat below a 0.5% move, so noise is not styled as news.",
    )


class SectionMeta(ResponseModel):
    """Where a section's numbers came from."""

    row_count: int | None = None
    data_snapshot_id: str | None = None
    freshness: str | None = None
    cache: str | None = None
    elapsed_ms: float | None = None


class RevenueTodayResponse(ResponseModel):
    business_date: date = Field(
        description="Latest business date in the warehouse, not today's clock date."
    )
    comparison_date: date
    comparison_basis: str
    cards: list[MetricCardModel]


class TrendPointModel(ResponseModel):
    business_date: date
    net_revenue: float | None = None
    units_sold: float | None = None
    orders: float | None = None


class RevenueTrendResponse(ResponseModel):
    days: int
    series: list[TrendPointModel]
    meta: SectionMeta


class GrowthRow(ResponseModel):
    horizon: str = Field(description="day | week | month")
    days: int
    current_revenue: float
    prior_revenue: float
    change_amount: float
    change_pct: float | None


class GrowthResponse(ResponseModel):
    as_of: date
    horizons: list[GrowthRow]


class ProfitResponse(ResponseModel):
    period_days: int
    cards: list[MetricCardModel]
    by_category: list[dict[str, Any]]
    meta: SectionMeta


class TopProductsResponse(ResponseModel):
    period_days: int
    ranked_by: str
    products: list[dict[str, Any]]
    meta: SectionMeta


class StoreRankingResponse(ResponseModel):
    period_days: int
    cluster: str | None = Field(
        default=None, description="Peer group the ranking was scoped to, if any."
    )
    stores: list[dict[str, Any]]
    meta: SectionMeta


class InventoryRiskResponse(ResponseModel):
    position_date: date = Field(
        description="Positions are a daily snapshot; risk is read for one day, never summed."
    )
    at_risk: list[dict[str, Any]]
    meta: SectionMeta


class ForecastAccuracy(ResponseModel):
    """The model's public record. Travels with every forecast, by design."""

    model_config = ConfigDict(protected_namespaces=())

    model_name: str | None = None
    model_class: str | None = Field(
        default=None, description="baseline | statistical | ml — what kind of model produced this."
    )
    wape: float | None = Field(
        default=None,
        description=(
            "Weighted absolute percentage error — volume-weighted, so a quiet day "
            "with a tiny denominator cannot dominate the headline."
        ),
    )
    bias: float | None = Field(
        default=None,
        description=(
            "Signed error share. A model wrong in one direction is worse than one "
            "wrong randomly, because inventory decisions compound the bias."
        ),
    )
    interval_coverage: float | None = Field(
        default=None,
        description=(
            "Share of actuals falling inside the interval. Materially below the "
            "nominal band means the intervals are miscalibrated."
        ),
    )
    forecast_days_evaluated: int | None = None


class ForecastResponse(ResponseModel):
    horizon_days: int
    series: list[dict[str, Any]]
    accuracy: ForecastAccuracy
    meta: SectionMeta


class AlertCard(ResponseModel):
    id: str
    metric_key: str
    metric_label: str
    severity: str = Field(description="critical | warn | info")
    status: str
    scope: dict[str, Any] = Field(description="The exact slice that breached, e.g. {region: SW}.")
    observed: float
    expected_low: float
    expected_high: float
    deviation_pct: float | None = Field(
        default=None, description="Signed distance outside the expected band."
    )
    narration: str | None = Field(
        default=None, description="AI-generated one-liner; null when narration was unavailable."
    )
    detected_at: datetime
    has_investigation: bool = Field(
        description="True when a root-cause investigation is attached and ready to open."
    )
    data_snapshot_id: str


class AlertsResponse(ResponseModel):
    counts: dict[str, int] = Field(description="Open alerts by severity, for the header badge.")
    alerts: list[AlertCard]


class RecommendationCard(ResponseModel):
    id: str
    type: str = Field(description="reorder | markdown | promo | assortment")
    subject: dict[str, Any]
    expected_impact: dict[str, Any] = Field(
        description="Includes the estimation method — an impact without one is not actionable."
    )
    impact_value: float
    rationale: str | None
    confidence: str = Field(description="high | medium | low, from a deterministic rubric.")
    expires_at: datetime
    evidence: dict[str, Any] = Field(
        description="Pointers to the queries and snapshot behind the recommendation."
    )


class RecommendationsResponse(ResponseModel):
    recommendations: list[RecommendationCard]


class ExecutiveOverviewResponse(ResponseModel):
    """The whole dashboard in one round trip.

    Individual endpoints exist for tiles that refresh independently; this one
    exists because the first paint should not be eight sequential requests.
    """

    business_date: date
    revenue: RevenueTodayResponse
    growth: GrowthResponse
    alerts: AlertsResponse
    recommendations: RecommendationsResponse
    top_products: list[dict[str, Any]]
    inventory_risk: list[dict[str, Any]]
    sections_unavailable: list[str] = Field(
        default_factory=list,
        description=(
            "Sections omitted because the caller's role does not include them. "
            "Named rather than silently dropped, so the UI can explain the gap."
        ),
    )
