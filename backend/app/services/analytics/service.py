"""Analytics service — one use-case per question the product asks.

Each method authorizes first, then delegates to the repository. Authorization
is checked here as well as on the route (Backend design §9's two-layer rule):
a service invoked from a worker, a script, or a future endpoint still enforces
its own rules.

The `analytics.<module>.read` permissions are what actually separate the roles
— a Marketing user and a Finance user share most action verbs but see
different modules (Backend §10).
"""

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import structlog

from app.domain.auth.entities import Principal
from app.domain.auth.permissions import Permission
from app.domain.shared.errors import NotFoundError
from app.infrastructure.semantic.client import QueryResult
from app.infrastructure.semantic.repository import AnalyticsRepository, AnalyticsRequest
from app.services.analytics.registry import DOMAINS, Domain, get_domain
from app.services.shared import authz

log = structlog.get_logger(__name__)

#: Which module permission gates which analytics domain.
DOMAIN_PERMISSIONS: dict[str, Permission] = {
    "revenue": Permission.ANALYTICS_REVENUE_READ,
    "store": Permission.ANALYTICS_STORE_READ,
    "customer": Permission.ANALYTICS_CUSTOMER_READ,
    "inventory": Permission.ANALYTICS_INVENTORY_READ,
    "marketing": Permission.ANALYTICS_MARKETING_READ,
    "profitability": Permission.ANALYTICS_PROFITABILITY_READ,
    # Product performance rides with revenue: anyone who can see revenue
    # can see which products produced it.
    "product": Permission.ANALYTICS_REVENUE_READ,
    # Every customer-intelligence surface rides the customer module: RFM,
    # cohorts, journey, churn, and VIP are views of one population, and
    # splitting their permissions would let a role see the risk without the
    # segment it belongs to.
    "rfm": Permission.ANALYTICS_CUSTOMER_READ,
    "cohorts": Permission.ANALYTICS_CUSTOMER_READ,
    "lifecycle": Permission.ANALYTICS_CUSTOMER_READ,
    "churn": Permission.ANALYTICS_CUSTOMER_READ,
    "vip": Permission.ANALYTICS_CUSTOMER_READ,
    # Inventory intelligence rides the inventory module for the same reason:
    # a reorder suggestion without the supplier reliability behind it is a
    # number to be overridden, and ABC without stock position is trivia.
    "product_abc": Permission.ANALYTICS_INVENTORY_READ,
    "inventory_health": Permission.ANALYTICS_INVENTORY_READ,
    "reorder": Permission.ANALYTICS_INVENTORY_READ,
    "supplier": Permission.ANALYTICS_INVENTORY_READ,
    "warehouse_health": Permission.ANALYTICS_INVENTORY_READ,
}

DEFAULT_LOOKBACK_DAYS = 30


@dataclass(frozen=True, slots=True)
class AnalyticsAnswer:
    """A query result plus the provenance the API surfaces."""

    domain: str
    metrics: list[str]
    dimensions: list[str]
    result: QueryResult

    @property
    def rows(self) -> list[dict[str, Any]]:
        return self.result.rows


class AnalyticsService:
    """Serves every analytics domain through one governed path."""

    def __init__(self, repository: AnalyticsRepository) -> None:
        self._repository = repository

    async def query(
        self,
        principal: Principal,
        *,
        domain_key: str,
        metrics: list[str],
        dimensions: list[str] | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        filters: dict[str, str] | None = None,
        sort_by: str | None = None,
        descending: bool = True,
        limit: int = 100,
        offset: int = 0,
    ) -> AnalyticsAnswer:
        """Answer one analytics question, subject to the caller's module access."""
        domain = self._require_domain(principal, domain_key)

        # A missing period means "recent", not "all history": an unbounded
        # scan is a self-inflicted outage the day the table gets big.
        if domain.date_column and start_date is None and end_date is None:
            end_date = date.today()
            start_date = end_date - timedelta(days=DEFAULT_LOOKBACK_DAYS)

        request = AnalyticsRequest(
            domain=domain,
            metrics=metrics,
            dimensions=dimensions or [],
            start_date=start_date,
            end_date=end_date,
            filters=filters,
            sort_by=sort_by,
            descending=descending,
            limit=limit,
            offset=offset,
        )
        result = await self._repository.run(request, tenant_id=str(principal.tenant_id))

        log.info(
            "analytics.query",
            domain=domain.key,
            metrics=metrics,
            dimensions=dimensions or [],
            rows=result.row_count,
            cache=result.cache,
            elapsed_ms=round(result.elapsed_ms, 1),
        )
        return AnalyticsAnswer(
            domain=domain.key,
            metrics=metrics,
            dimensions=dimensions or [],
            result=result,
        )

    async def summary(
        self,
        principal: Principal,
        *,
        domain_key: str,
        start_date: date | None = None,
        end_date: date | None = None,
        filters: dict[str, str] | None = None,
    ) -> AnalyticsAnswer:
        """Headline totals for a domain — no dimensional breakdown.

        Every module's landing view needs the same thing: the domain's own
        metrics for a period, at one row. Making it a first-class method keeps
        six endpoints from each inventing their own version.
        """
        domain = self._require_domain(principal, domain_key)
        return await self.query(
            principal,
            domain_key=domain_key,
            metrics=list(domain.metrics),
            dimensions=[],
            start_date=start_date,
            end_date=end_date,
            filters=filters,
            limit=1,
        )

    async def trend(
        self,
        principal: Principal,
        *,
        domain_key: str,
        metrics: list[str],
        start_date: date | None = None,
        end_date: date | None = None,
        filters: dict[str, str] | None = None,
        limit: int = 400,
    ) -> AnalyticsAnswer:
        """A metric over time — the shape every chart needs."""
        domain = self._require_domain(principal, domain_key)
        if not domain.date_column:
            raise NotFoundError(
                f"{domain.label} has no time grain",
                hint="Customer segments are point-in-time aggregates, not a series.",
            )
        return await self.query(
            principal,
            domain_key=domain_key,
            metrics=metrics,
            dimensions=["business_date"],
            start_date=start_date,
            end_date=end_date,
            filters=filters,
            sort_by="business_date",
            descending=False,
            limit=limit,
        )

    def catalog(self, principal: Principal) -> list[dict[str, Any]]:
        """Metrics and dimensions the caller may actually use.

        Filtered by permission on purpose: a catalog that advertises modules
        the caller cannot query produces a UI full of dead ends.
        """
        catalog: list[dict[str, Any]] = []
        for key, domain in DOMAINS.items():
            permission = DOMAIN_PERMISSIONS.get(key)
            if permission is None or not authz.has(principal, permission):
                continue
            catalog.append(
                {
                    "domain": domain.key,
                    "label": domain.label,
                    "has_time_grain": bool(domain.date_column),
                    "metrics": [
                        {
                            "key": metric.key,
                            "label": metric.label,
                            "unit": metric.unit,
                            "additivity": metric.additivity.value,
                            "description": metric.description,
                        }
                        for metric in domain.metrics.values()
                    ],
                    "dimensions": [
                        {"key": dim.key, "label": dim.label} for dim in domain.dimensions.values()
                    ],
                }
            )
        return catalog

    def _require_domain(self, principal: Principal, domain_key: str) -> Domain:
        domain = get_domain(domain_key)
        if domain is None:
            raise NotFoundError(
                f"unknown analytics domain '{domain_key}'",
                hint=f"Available: {', '.join(sorted(DOMAINS))}",
            )
        permission = DOMAIN_PERMISSIONS.get(domain.key)
        if permission is None:
            # Registering a domain without declaring who may read it is a
            # programming error. Treat it as unreadable rather than open, and
            # say so in the log — an ungoverned domain must never be the
            # permissive case.
            log.error("analytics.domain_without_permission", domain=domain.key)
            raise NotFoundError(f"unknown analytics domain '{domain_key}'")
        authz.require(principal, permission)
        return domain
