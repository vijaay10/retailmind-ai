"""Recommendation orchestration (Analytics §10).

Reads the governed registry, runs the seven generators, and returns a ranked
portfolio with honest totals.

Three properties are worth stating up front, because each is a way this kind
of engine usually misleads.

**Ranking is risk-adjusted, not upside-ranked.** The score is expected profit
weighted by confidence, less the downside weighted by the chance the reasoning
is wrong. An engine that ranks on headline value puts an irreversible,
assumption-heavy markdown above a certain, reversible reorder worth nearly as
much — and getting that trade right is most of the job.

**The portfolio total is deduplicated.** Reordering a line and fixing the
supplier that made it late chase the same pounds. Adding them promises the
money twice, and a merchant who banks the sum finds the shortfall at quarter
end. Both figures are reported, with the gap explained.

**Working capital is never added to profit.** Liquidating dead stock frees
cash and books a loss. Netting them into one positive number makes every
markdown look like a profit opportunity, which is how a clearance programme
gets approved on the strength of the thing that makes it expensive.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import structlog

from app.domain.auth.entities import Principal
from app.domain.auth.permissions import Permission
from app.services.analytics.service import AnalyticsService
from app.services.recommendations import generators
from app.services.recommendations.contracts import (
    Category,
    Portfolio,
    Recommendation,
)
from app.services.shared import authz

log = structlog.get_logger(__name__)

#: Trading days the supporting analysis looks back over.
LOOKBACK_DAYS = 28

#: Recommendations returned across all categories.
MAX_RECOMMENDATIONS = 20

#: Rows pulled per supporting query.
QUERY_LIMIT = 200


@dataclass(frozen=True, slots=True)
class _Inputs:
    """Everything the generators need, fetched once."""

    reorder: list[dict[str, Any]]
    inventory: list[dict[str, Any]]
    categories: list[dict[str, Any]]
    promotions: list[dict[str, Any]]
    stores: list[dict[str, Any]]
    churn: list[dict[str, Any]]
    suppliers: list[dict[str, Any]]


class RecommendationService:
    """Turns the platform's analysis into a ranked set of proposed actions."""

    def __init__(self, analytics: AnalyticsService) -> None:
        self._analytics = analytics

    async def recommend(
        self,
        principal: Principal,
        *,
        categories: tuple[Category, ...] | None = None,
        end_date: date | None = None,
        limit: int = MAX_RECOMMENDATIONS,
    ) -> Portfolio:
        authz.require(principal, Permission.RECOMMENDATIONS_READ)

        end = end_date or date.today()
        start = end - timedelta(days=LOOKBACK_DAYS - 1)
        requested = categories or tuple(Category)

        data = await self._load(principal, start, end)

        produced: list[Recommendation] = []
        empty: dict[str, str] = {}

        if Category.INVENTORY in requested:
            found = generators.inventory_recommendations(
                data.reorder, margin_rate=_estate_margin_rate(data.categories)
            )
            produced.extend(found)
            if not found:
                empty[Category.INVENTORY.value] = (
                    "no replenishment shortfall above the materiality floor"
                )

        if Category.PRICING in requested:
            found = generators.pricing_recommendations(data.inventory)
            found += generators.margin_recommendations(data.categories)
            produced.extend(found)
            if not found:
                empty[Category.PRICING.value] = "no excess stock or margin erosion detected"

        if Category.PROMOTION in requested:
            found = generators.promotion_recommendations(data.promotions)
            produced.extend(found)
            if not found:
                empty[Category.PROMOTION.value] = (
                    "no campaign whose subsidy exceeds the margin it generates"
                )

        if Category.STORE in requested:
            median = _median([float(row.get("net_revenue") or 0.0) for row in data.stores])
            found = generators.store_recommendations(data.stores, peer_median_revenue=median)
            produced.extend(found)
            if not found:
                empty[Category.STORE.value] = "no store trading materially below its peers"

        if Category.MARKETING in requested or Category.CUSTOMER in requested:
            found = [
                item
                for item in generators.customer_recommendations(data.churn)
                if item.category in requested
            ]
            produced.extend(found)
            if not found:
                empty[Category.CUSTOMER.value] = "no risk band above the materiality floor"

        if Category.SUPPLIER in requested:
            found = generators.supplier_recommendations(data.suppliers)
            produced.extend(found)
            if not found:
                empty[Category.SUPPLIER.value] = (
                    "no supplier below the OTIF action threshold with enough "
                    "received lines to judge"
                )

        produced.sort(key=lambda item: item.risk_adjusted_profit, reverse=True)
        ranked = tuple(produced[:limit])

        portfolio = Portfolio(
            recommendations=ranked,
            categories_requested=requested,
            categories_empty=empty,
            caveats=_caveats(ranked),
            meta={
                "lookback_days": LOOKBACK_DAYS,
                "as_of": end.isoformat(),
                "candidates_considered": len(produced),
            },
        )

        log.info(
            "recommendations.generated",
            returned=len(ranked),
            considered=len(produced),
            gross=round(portfolio.gross_profit_opportunity, 2),
            net=round(portfolio.net_profit_opportunity, 2),
        )
        return portfolio

    async def _load(self, principal: Principal, start: date, end: date) -> _Inputs:
        """One governed query per supporting relation."""

        async def query(
            domain: str,
            metrics: list[str],
            dimensions: list[str],
            *,
            dated: bool = True,
            sort_by: str | None = None,
        ) -> list[dict[str, Any]]:
            answer = await self._analytics.query(
                principal,
                domain_key=domain,
                metrics=metrics,
                dimensions=dimensions,
                start_date=start if dated else None,
                end_date=end if dated else None,
                sort_by=sort_by,
                limit=QUERY_LIMIT,
            )
            return answer.result.rows

        reorder = await query(
            "reorder",
            [
                "suggested_order_qty",
                "daily_demand",
                "revenue_at_risk",
                "soonest_stockout_days",
                "below_reorder_point",
                "safety_stock",
            ],
            ["sku", "store_id", "category"],
            dated=False,
            sort_by="revenue_at_risk",
        )
        inventory = await query(
            "inventory_health",
            ["excess_units", "excess_value", "cover_days", "on_hand_units"],
            ["sku", "store_id"],
            dated=False,
            sort_by="excess_value",
        )
        # Two domains over one relation: discount_rate lives on revenue and
        # margin_rate on profitability. Merged here rather than adding a
        # duplicate metric to either registry, where it would exist only to
        # save this caller a join.
        discounting = await query(
            "revenue", ["net_revenue", "discount_rate"], ["category"], sort_by="net_revenue"
        )
        margins = await query(
            "profitability", ["margin_rate", "margin_amount"], ["category"], sort_by="margin_amount"
        )
        promotions = await query(
            "marketing",
            ["promo_revenue", "promo_margin", "subsidy_amount", "effective_depth"],
            ["promo"],
            sort_by="subsidy_amount",
        )
        stores = await query(
            "store", ["net_revenue", "margin_rate"], ["store"], sort_by="net_revenue"
        )
        churn = await query(
            "churn",
            ["customers", "value_at_risk", "vip_value_at_risk"],
            ["risk_band"],
            dated=False,
            sort_by="value_at_risk",
        )
        suppliers = await query(
            "supplier",
            ["otif_rate", "closed_lines", "avg_lead_time_days", "ordered_value"],
            ["supplier_name"],
            dated=False,
            sort_by="ordered_value",
        )

        by_category = {str(row.get("category")): dict(row) for row in discounting}
        for row in margins:
            by_category.setdefault(str(row.get("category")), {}).update(row)
        categories = list(by_category.values())

        return _Inputs(
            reorder=reorder,
            inventory=inventory,
            categories=categories,
            promotions=promotions,
            stores=stores,
            churn=churn,
            suppliers=suppliers,
        )


def _estate_margin_rate(categories: list[dict[str, Any]], default: float = 0.35) -> float:
    """Revenue-weighted estate margin rate.

    Weighted rather than averaged: an unweighted mean of category margins lets
    a tiny high-margin category set the valuation for the whole estate.
    """
    revenue = sum(float(row.get("net_revenue") or 0.0) for row in categories)
    if revenue <= 0:
        return default
    weighted = sum(
        float(row.get("net_revenue") or 0.0) * float(row.get("margin_rate") or 0.0)
        for row in categories
    )
    return weighted / revenue or default


def _median(values: list[float]) -> float:
    """Median, not mean.

    A peer benchmark built from a mean is dragged by the flagship store, and
    every ordinary store then looks like an underperformer. The median is the
    typical store, which is what "below its peers" is supposed to mean.
    """
    usable = sorted(value for value in values if value > 0)
    if not usable:
        return 0.0
    middle = len(usable) // 2
    if len(usable) % 2:
        return usable[middle]
    return (usable[middle - 1] + usable[middle]) / 2.0


def _caveats(recommendations: tuple[Recommendation, ...]) -> tuple[str, ...]:
    caveats = [
        "Impact figures are estimates, and each states the basis it was "
        "computed on. Only 'measured' estimates are arithmetic over observed "
        "data; the rest rest on a forecast or on a behavioural parameter this "
        "platform has never measured.",
        "Working capital freed is reported separately from profit. Clearing "
        "dead stock releases cash and books a loss, and adding the two would "
        "make every markdown look profitable.",
    ]

    assumed = [item for item in recommendations if item.impact.rests_on_unmeasured_assumptions]
    if assumed:
        parameters = sorted(
            {
                assumption.name
                for item in assumed
                for assumption in item.impact.assumptions
                if not assumption.is_evidenced
            }
        )
        caveats.append(
            f"{len(assumed)} of {len(recommendations)} recommendations rest on "
            f"unmeasured parameters ({', '.join(parameters)}). Each carries a "
            "sensitivity range; where the range crosses zero, the honest next "
            "step is a test rather than a rollout."
        )

    irreversible = [item for item in recommendations if item.risk.band == "high"]
    if irreversible:
        caveats.append(
            f"{len(irreversible)} recommendation(s) are irreversible once "
            "taken. Their downside is realised in full if the reasoning does "
            "not hold."
        )

    return tuple(caveats)


def summarise(portfolio: Portfolio) -> dict[str, Any]:
    """Response payload."""
    by_category: dict[str, int] = {}
    for item in portfolio.recommendations:
        by_category[item.category.value] = by_category.get(item.category.value, 0) + 1

    return {
        "recommendations": [item.as_dict() for item in portfolio.recommendations],
        "count": len(portfolio.recommendations),
        "by_category": by_category,
        "gross_profit_opportunity": round(portfolio.gross_profit_opportunity, 2),
        "net_profit_opportunity": round(portfolio.net_profit_opportunity, 2),
        "capital_freed": round(portfolio.capital_freed, 2),
        "categories_requested": [item.value for item in portfolio.categories_requested],
        "categories_empty": portfolio.categories_empty,
        "caveats": list(portfolio.caveats),
        "meta": portfolio.meta,
    }
