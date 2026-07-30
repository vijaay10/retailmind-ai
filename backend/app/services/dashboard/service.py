"""Executive dashboard service — composition, not new analytics.

The executive surface owns no metrics of its own (Analytics §10). Every number
here comes from the same governed registry the analytics endpoints read, which
is what guarantees the scorecard and a drill-down cannot disagree. What this
service adds is *selection*: which metrics, which period, which ordering, and
which comparison — the editorial decisions that turn a warehouse into a
ten-minute Monday read.

Two conventions run through it:

* **Plan-anchored comparison.** Every headline carries its prior-period
  context, because a number without a comparison is trivia.
* **Dollar-ranked attention.** Alerts and recommendations sort by impact, not
  recency, so the most expensive problem is the first thing read.
"""

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

import structlog

from app.domain.auth.entities import Principal
from app.domain.auth.permissions import Permission
from app.infrastructure.db.repositories.insights import (
    AlertReadRepository,
    AlertSummary,
    RecommendationReadRepository,
    RecommendationSummary,
)
from app.infrastructure.semantic.client import SemanticLayerClient, SemanticQuery
from app.services.analytics.service import AnalyticsService
from app.services.shared import authz

log = structlog.get_logger(__name__)

#: The comparison window for every headline. Seven days holds the weekday mix
#: constant, which day-over-day does not (Analytics §1, FR-A03).
COMPARISON_DAYS = 7


@dataclass(frozen=True, slots=True)
class MetricCard:
    """One scorecard tile: a value, its comparison, and its provenance."""

    key: str
    label: str
    value: float | None
    unit: str
    prior_value: float | None = None
    change_pct: float | None = None
    direction: str = "flat"
    """up | down | flat — computed here so every client renders it identically."""

    @classmethod
    def build(
        cls, *, key: str, label: str, unit: str, value: float | None, prior: float | None
    ) -> "MetricCard":
        change: float | None = None
        direction = "flat"
        if value is not None and prior:
            change = round((value - prior) / abs(prior), 4)
            # A half-percent wobble is noise, not a movement. Calling it flat
            # keeps the scorecard from crying wolf on every tile.
            if abs(change) >= 0.005:
                direction = "up" if change > 0 else "down"
        return cls(
            key=key,
            label=label,
            value=value,
            unit=unit,
            prior_value=prior,
            change_pct=change,
            direction=direction,
        )


@dataclass(frozen=True, slots=True)
class DashboardSection:
    """A section's rows plus the provenance of the query that produced them."""

    rows: list[dict[str, Any]]
    meta: dict[str, Any] = field(default_factory=dict)


class ExecutiveDashboardService:
    """Assembles the executive surface from governed analytics."""

    def __init__(
        self,
        analytics: AnalyticsService,
        semantic: SemanticLayerClient,
        alerts: AlertReadRepository,
        recommendations: RecommendationReadRepository,
    ) -> None:
        self._analytics = analytics
        # Injected directly rather than reached through the analytics service:
        # forecasts are already at their final grain and do not pass through
        # the metric registry's aggregation path.
        self._semantic = semantic
        self._alerts = alerts
        self._recommendations = recommendations

    # ── Revenue ──────────────────────────────────────────────────────

    async def revenue_today(
        self, principal: Principal, *, as_of: date | None = None
    ) -> tuple[list[MetricCard], dict[str, Any]]:
        """Headline revenue for the latest business day, versus a week earlier.

        "Today" means the latest day the *warehouse* has, not the wall clock:
        a dashboard that shows an empty today because the nightly load has not
        finished is a dashboard nobody trusts.
        """
        day = as_of or await self._latest_business_date(principal)
        prior = day - timedelta(days=COMPARISON_DAYS)

        current = await self._totals(principal, "revenue", day, day)
        previous = await self._totals(principal, "revenue", prior, prior)

        cards = [
            MetricCard.build(
                key=key,
                label=label,
                unit=unit,
                value=_number(current.get(key)),
                prior=_number(previous.get(key)),
            )
            for key, label, unit in (
                ("net_revenue", "Net Revenue", "currency"),
                ("units_sold", "Units Sold", "units"),
                ("orders", "Orders", "count"),
                ("aov", "Average Order Value", "currency"),
                ("discount_rate", "Discount Rate", "rate"),
            )
        ]
        return cards, {
            "business_date": day.isoformat(),
            "comparison_date": prior.isoformat(),
            "comparison_basis": f"{COMPARISON_DAYS} days earlier (same weekday)",
        }

    async def revenue_trend(
        self, principal: Principal, *, days: int = 30, as_of: date | None = None
    ) -> DashboardSection:
        """Daily revenue and units for the trend chart."""
        end = as_of or await self._latest_business_date(principal)
        answer = await self._analytics.trend(
            principal,
            domain_key="revenue",
            metrics=["net_revenue", "units_sold", "orders"],
            start_date=end - timedelta(days=days - 1),
            end_date=end,
        )
        return DashboardSection(rows=answer.rows, meta=answer.result.meta)

    async def growth(self, principal: Principal, *, as_of: date | None = None) -> DashboardSection:
        """Revenue growth over several horizons at once.

        Day, week, and month deltas answer different questions — a bad day, a
        bad week, and a bad month need different reactions — so the dashboard
        shows all three rather than making the reader pick a period and
        re-query.
        """
        end = as_of or await self._latest_business_date(principal)
        horizons = {"day": 1, "week": 7, "month": 28}
        rows: list[dict[str, Any]] = []

        for label, span in horizons.items():
            current = await self._totals(principal, "revenue", end - timedelta(days=span - 1), end)
            prior = await self._totals(
                principal,
                "revenue",
                end - timedelta(days=2 * span - 1),
                end - timedelta(days=span),
            )
            current_value = _number(current.get("net_revenue")) or 0.0
            prior_value = _number(prior.get("net_revenue")) or 0.0
            rows.append(
                {
                    "horizon": label,
                    "days": span,
                    "current_revenue": current_value,
                    "prior_revenue": prior_value,
                    "change_amount": round(current_value - prior_value, 2),
                    "change_pct": (
                        round((current_value - prior_value) / abs(prior_value), 4)
                        if prior_value
                        else None
                    ),
                }
            )
        return DashboardSection(rows=rows, meta={"as_of": end.isoformat()})

    # ── Profit ───────────────────────────────────────────────────────

    async def profit(
        self, principal: Principal, *, days: int = 7, as_of: date | None = None
    ) -> tuple[list[MetricCard], DashboardSection]:
        """Margin headline plus its breakdown by category.

        Requires the profitability module: revenue access alone does not
        include margin, because cost is exactly the number most organisations
        restrict.
        """
        authz.require(principal, Permission.ANALYTICS_PROFITABILITY_READ)

        end = as_of or await self._latest_business_date(principal)
        start = end - timedelta(days=days - 1)
        prior_end = start - timedelta(days=1)

        current = await self._totals(principal, "profitability", start, end)
        previous = await self._totals(
            principal, "profitability", prior_end - timedelta(days=days - 1), prior_end
        )

        cards = [
            MetricCard.build(
                key=key,
                label=label,
                unit=unit,
                value=_number(current.get(key)),
                prior=_number(previous.get(key)),
            )
            for key, label, unit in (
                ("margin_amount", "Gross Margin", "currency"),
                ("margin_rate", "Margin Rate", "rate"),
                ("cogs_amount", "COGS", "currency"),
                ("markdown_impact", "Markdown Impact", "currency"),
            )
        ]

        breakdown = await self._analytics.query(
            principal,
            domain_key="profitability",
            metrics=["margin_amount", "margin_rate", "net_revenue"],
            dimensions=["category"],
            start_date=start,
            end_date=end,
            sort_by="margin_amount",
            limit=10,
        )
        return cards, DashboardSection(rows=breakdown.rows, meta=breakdown.result.meta)

    # ── Products and stores ──────────────────────────────────────────

    async def top_products(
        self,
        principal: Principal,
        *,
        days: int = 7,
        limit: int = 10,
        by: str = "net_revenue",
        as_of: date | None = None,
    ) -> DashboardSection:
        """Best-performing SKUs over the period.

        Ranking metric is a parameter because "top" is not one question: a
        merchandiser ranks by revenue, a buyer by margin, a planner by units.
        """
        end = as_of or await self._latest_business_date(principal)
        answer = await self._analytics.query(
            principal,
            domain_key="product",
            metrics=["net_revenue", "units_sold", "margin_amount", "margin_rate"],
            dimensions=["sku", "product_name", "category"],
            start_date=end - timedelta(days=days - 1),
            end_date=end,
            sort_by=by,
            descending=True,
            limit=limit,
        )
        return DashboardSection(rows=answer.rows, meta=answer.result.meta)

    async def store_rankings(
        self,
        principal: Principal,
        *,
        days: int = 7,
        limit: int = 10,
        cluster: str | None = None,
        as_of: date | None = None,
    ) -> DashboardSection:
        """Store league table, ranked within its peer cluster.

        The `cluster` filter is the honest way to read this: comparing a
        flagship against an outlet is analytical malpractice (Analytics §3).
        Unfiltered results still carry `store_cluster` so the client can group
        rather than mislead.
        """
        end = as_of or await self._latest_business_date(principal)
        answer = await self._analytics.query(
            principal,
            domain_key="store",
            metrics=["net_revenue", "margin_amount", "units_sold", "aov"],
            dimensions=["store", "store_name", "region", "store_cluster"],
            start_date=end - timedelta(days=days - 1),
            end_date=end,
            filters={"store_cluster": cluster} if cluster else None,
            sort_by="net_revenue",
            descending=True,
            limit=limit,
        )
        rows = [{**row, "rank": index} for index, row in enumerate(answer.rows, start=1)]
        return DashboardSection(rows=rows, meta=answer.result.meta)

    # ── Inventory and forecast ───────────────────────────────────────

    async def inventory_risk(
        self, principal: Principal, *, limit: int = 10, as_of: date | None = None
    ) -> DashboardSection:
        """Categories carrying the most availability risk.

        Ranked by stockout rate rather than by absolute stockouts: a category
        with 4 stockouts out of 20 positions is in more trouble than one with
        10 out of 5,000, and absolute counts hide that.
        """
        end = as_of or await self._latest_business_date(principal)
        answer = await self._analytics.query(
            principal,
            domain_key="inventory",
            metrics=[
                "stockout_rate",
                "stockout_positions",
                "on_hand_units",
                "cover_days",
                "inventory_value_cost",
            ],
            dimensions=["category", "region"],
            # A single day: positions are semi-additive, so a multi-day window
            # would sum stock that existed only once (Analytics §4).
            start_date=end,
            end_date=end,
            sort_by="stockout_rate",
            descending=True,
            limit=limit,
        )
        return DashboardSection(
            rows=answer.rows, meta={**answer.result.meta, "position_date": end.isoformat()}
        )

    async def forecast(
        self, principal: Principal, *, days: int = 14, as_of: date | None = None
    ) -> tuple[DashboardSection, dict[str, Any]]:
        """Revenue forecast with intervals, plus the model's accuracy record.

        Accuracy travels *with* the forecast, never behind a separate click.
        A planner deciding how much to trust a number needs to know how wrong
        the model has been, and the design makes that non-optional (ARCH §28).
        """
        authz.require(principal, Permission.FORECASTS_READ)

        end = as_of or await self._latest_business_date(principal)
        series = self._semantic.execute(_forecast_query(end - timedelta(days=days - 1), end))
        scoreboard = self._semantic.execute(_accuracy_query())

        accuracy = scoreboard.rows[0] if scoreboard.rows else {}
        return (
            DashboardSection(rows=series.rows, meta=series.meta),
            {
                "model_name": accuracy.get("model_name"),
                "model_class": accuracy.get("model_class"),
                "wape": accuracy.get("wape"),
                "bias": accuracy.get("bias"),
                "interval_coverage": accuracy.get("interval_coverage"),
                "forecast_days_evaluated": accuracy.get("forecast_days"),
            },
        )

    # ── Attention: alerts and recommendations ────────────────────────

    async def alerts(self, principal: Principal, *, limit: int = 5) -> list[AlertSummary]:
        """Open alerts, most severe first."""
        authz.require(principal, Permission.ALERTS_READ)
        return await self._alerts.open_alerts(principal.tenant_id, limit=limit)

    async def alert_counts(self, principal: Principal) -> dict[str, int]:
        authz.require(principal, Permission.ALERTS_READ)
        return await self._alerts.open_count_by_severity(principal.tenant_id)

    async def recommendations(
        self, principal: Principal, *, limit: int = 5
    ) -> list[RecommendationSummary]:
        """Highest-value proposed actions."""
        authz.require(principal, Permission.RECOMMENDATIONS_READ)
        return await self._recommendations.top_proposed(principal.tenant_id, limit=limit)

    # ── Internals ────────────────────────────────────────────────────

    async def _latest_business_date(self, principal: Principal) -> date:
        """The most recent day the warehouse actually holds.

        Anchoring on data rather than on `date.today()` is what keeps the
        dashboard honest during a backfill, a demo, or a late nightly load.
        """
        _snapshot, freshness = self._semantic.snapshot()
        return freshness or date.today()

    async def _totals(
        self, principal: Principal, domain: str, start: date, end: date
    ) -> dict[str, Any]:
        answer = await self._analytics.summary(
            principal, domain_key=domain, start_date=start, end_date=end
        )
        return answer.rows[0] if answer.rows else {}


def _number(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _forecast_query(start: date, end: date) -> SemanticQuery:
    """Forecast series for a window.

    Built here rather than through the metric registry because a forecast is
    already at its final grain — there is nothing to aggregate, and forcing it
    through a GROUP BY would only obscure that.
    """
    return SemanticQuery(
        relation="v_fct_forecast",
        select=[
            "business_date",
            "model_name",
            "yhat_revenue",
            "yhat_revenue_lower",
            "yhat_revenue_upper",
            "yhat_units",
            "actual_revenue",
            "within_interval",
        ],
        filters=[("business_date", "gte", start), ("business_date", "lte", end)],
        order_by=[("business_date", False)],
        limit=400,
    )


def _accuracy_query() -> SemanticQuery:
    """The published scoreboard for every model that has produced forecasts."""
    return SemanticQuery(
        relation="v_mart_forecast_accuracy",
        select=[
            "model_name",
            "model_class",
            "forecast_days",
            "wape",
            "mape",
            "bias",
            "interval_coverage",
        ],
        order_by=[("wape", False)],
        limit=5,
    )
