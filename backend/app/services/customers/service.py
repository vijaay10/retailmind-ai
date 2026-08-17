"""Customer intelligence service (Analytics, methods M1/M2/M6/M14).

Composes the customer-domain registry views into the eight questions a
merchant actually asks: who are my segments, what are they worth, who is
coming back, who is drifting away, and who must I not lose.

Two rules run through every method, and both are structural rather than
advisory:

**Nothing individual leaves this service.** Every response is a population
aggregate. `dim_customer` holds per-customer rows so joins work, but no method
here can project one, so no endpoint can leak one.

**Small groups are suppressed.** Below the privacy floor a "segment" is a
handful of identifiable people. Rows under the floor are dropped and *counted*
in the response, so the caller knows the picture is partial rather than
silently seeing a smaller world.
"""

from dataclasses import dataclass
from typing import Any

import structlog

from app.domain.auth.entities import Principal
from app.domain.auth.permissions import Permission
from app.infrastructure.semantic.client import QueryResult
from app.services.analytics.service import AnalyticsService
from app.services.shared import authz

log = structlog.get_logger(__name__)

#: k-anonymity floor (Analytics). A group smaller than this is people, not
#: a population, and reporting on it invites re-identification.
PRIVACY_FLOOR = 20


@dataclass(frozen=True, slots=True)
class CustomerSection:
    """Aggregated rows plus what had to be withheld to publish them."""

    rows: list[dict[str, Any]]
    meta: dict[str, Any]
    suppressed_groups: int = 0
    suppressed_customers: int = 0

    @property
    def privacy_note(self) -> str | None:
        if not self.suppressed_groups:
            return None
        return (
            f"{self.suppressed_groups} group(s) covering {self.suppressed_customers} "
            f"customer(s) were withheld: below the {PRIVACY_FLOOR}-customer "
            "reporting floor."
        )


class CustomerIntelligenceService:
    """The eight customer surfaces, all reading the governed registry."""

    def __init__(self, analytics: AnalyticsService) -> None:
        self._analytics = analytics

    # ── Segmentation and RFM ─────────────────────────────────────────

    async def segments(self, principal: Principal) -> CustomerSection:
        """RFM segment distribution — the map of the customer base.

        Segment names come from a fixed rule map in the warehouse, so
        "Champions" means the same thing here, in a report, and in a
        recommendation.
        """
        answer = await self._query(
            principal,
            domain="customer",
            metrics=[
                "customers",
                "segment_value",
                "avg_lifetime_value",
                "repeat_customers",
                "repeat_rate",
            ],
            dimensions=["segment"],
            sort_by="segment_value",
        )
        return self._suppress(answer, group_key="rfm_segment")

    async def rfm_grid(self, principal: Principal) -> CustomerSection:
        """The recency × frequency grid, sized by value.

        The named segments are a *summary* of this grid. The grid itself is
        where a marketer sees that one segment contains two populations
        behaving differently — the detail a label necessarily hides.
        """
        answer = await self._query(
            principal,
            domain="rfm",
            metrics=[
                "customers",
                "segment_value",
                "avg_lifetime_value",
                "at_risk_customers",
                "vip_customers",
            ],
            dimensions=["recency_score", "frequency_score"],
            sort_by="customers",
            limit=25,
        )
        return self._suppress(answer, group_key=None)

    # ── Value ────────────────────────────────────────────────────────

    async def lifetime_value(self, principal: Principal) -> CustomerSection:
        """Customer value by segment: historic, and projected with its grade.

        Historic value is arithmetic over what happened. The 12-month
        projection is an extrapolation from observed cadence — it travels with
        a confidence grade because annualising a fortnight of history is a
        guess wearing a number's clothes.
        """
        answer = await self._query(
            principal,
            domain="vip",
            metrics=[
                "vip_customers",
                "vip_value",
                "avg_lifetime_value",
                "avg_predicted_clv_12m",
                "share_of_total_value",
            ],
            dimensions=["segment"],
            sort_by="vip_value",
        )
        return self._suppress(answer, group_key="rfm_segment")

    # ── Retention and repeat purchase ────────────────────────────────

    async def retention(self, principal: Principal, *, limit: int = 200) -> CustomerSection:
        """Weekly acquisition cohorts and their retention curves.

        Cohorts stop at the observation edge: a cohort acquired two weeks ago
        has no week-8 retention *yet*, and emitting a zero there would draw a
        cliff that does not exist.
        """
        answer = await self._query(
            principal,
            domain="cohorts",
            metrics=[
                "cohort_customers",
                "active_customers",
                "retention_rate",
                "revenue",
                "cumulative_value_per_customer",
            ],
            dimensions=["cohort_week", "weeks_since"],
            sort_by="cohort_week",
            descending=False,
            limit=limit,
        )
        return self._suppress(answer, group_key="cohort_week")

    async def repeat_purchase(self, principal: Principal) -> CustomerSection:
        """Repeat-purchase behaviour by lifecycle stage.

        Cadence matters as much as rate: knowing that Established customers
        buy every eleven days is what makes "overdue" a decidable question
        rather than a feeling.
        """
        answer = await self._query(
            principal,
            domain="lifecycle",
            metrics=[
                "customers",
                "avg_days_between_orders",
                "stage_value",
                "at_risk_customers",
                "at_risk_rate",
            ],
            dimensions=["stage"],
            sort_by="customers",
        )
        return self._suppress(answer, group_key="lifecycle_stage")

    # ── Journey ──────────────────────────────────────────────────────

    async def journey(self, principal: Principal) -> CustomerSection:
        """The New → Repeat → Established → Loyal funnel.

        The New → Repeat step is the one worth staring at: a customer who
        never makes a second purchase never earns back their acquisition cost.
        """
        answer = await self._query(
            principal,
            domain="lifecycle",
            metrics=[
                "customers",
                "reached_stage",
                "conversion_from_previous",
                "stage_value",
                "at_risk_rate",
            ],
            dimensions=["stage", "stage_order"],
            sort_by="reached_stage",
        )
        return self._suppress(answer, group_key="lifecycle_stage")

    # ── Risk ─────────────────────────────────────────────────────────

    async def churn_risk(self, principal: Principal, *, by: str = "risk_band") -> CustomerSection:
        """Customers drifting away, and the value drifting with them.

        Risk is a *band* derived from how many purchase cycles have elapsed
        unfulfilled — not a probability. Calling a heuristic "68% likely to
        churn" would imply a calibration nobody has measured.

        Ranked by value at risk rather than headcount: sorting retention effort
        by number of customers is how a team spends a quarter saving people
        worth less than the campaign.
        """
        answer = await self._query(
            principal,
            domain="churn",
            metrics=[
                "customers",
                "value_at_risk",
                "vip_value_at_risk",
                "avg_cycles_overdue",
                "vip_customers",
            ],
            dimensions=[by],
            sort_by="value_at_risk",
        )
        return self._suppress(answer, group_key=None)

    async def vip(self, principal: Principal, *, by: str = "risk_band") -> CustomerSection:
        """The VIP cohort: who they are as a group, and how many are drifting.

        Grouped by **one** dimension, not a cross-tab. Crossing risk band with
        segment splits an already-small population into cells that fall below
        the privacy floor, and the response then suppresses itself into
        nothing. One axis at a time keeps groups reportable; callers who want
        the other view ask for it.

        Risk band is the default because a VIP drifting into risk is the single
        most valuable retention target the platform can surface — expensive to
        replace, and still reachable.
        """
        answer = await self._query(
            principal,
            domain="vip",
            metrics=[
                "vip_customers",
                "vip_value",
                "avg_lifetime_value",
                "avg_predicted_clv_12m",
                "share_of_total_value",
            ],
            dimensions=[by],
            sort_by="vip_value",
        )
        return self._suppress(answer, group_key=None)

    # ── Internals ────────────────────────────────────────────────────

    async def _query(
        self,
        principal: Principal,
        *,
        domain: str,
        metrics: list[str],
        dimensions: list[str],
        sort_by: str | None = None,
        descending: bool = True,
        limit: int = 100,
    ) -> QueryResult:
        authz.require(principal, Permission.ANALYTICS_CUSTOMER_READ)
        answer = await self._analytics.query(
            principal,
            domain_key=domain,
            metrics=metrics,
            dimensions=dimensions,
            sort_by=sort_by,
            descending=descending,
            limit=limit,
        )
        return answer.result

    def _suppress(self, result: QueryResult, *, group_key: str | None) -> CustomerSection:
        """Drop rows below the privacy floor, and report what was dropped.

        The count is deliberately returned rather than silently swallowed: a
        caller seeing 6 of 8 segments should know two exist and why they are
        missing, otherwise the suppression itself becomes a source of wrong
        conclusions.
        """
        kept: list[dict[str, Any]] = []
        suppressed_groups = 0
        suppressed_customers = 0

        for row in result.rows:
            headcount = _customer_count(row)
            if headcount is not None and headcount < PRIVACY_FLOOR:
                suppressed_groups += 1
                suppressed_customers += headcount
                continue
            kept.append(row)

        if suppressed_groups:
            log.info(
                "customers.privacy_suppression",
                groups=suppressed_groups,
                customers=suppressed_customers,
                group_key=group_key,
            )

        return CustomerSection(
            rows=kept,
            meta=result.meta,
            suppressed_groups=suppressed_groups,
            suppressed_customers=suppressed_customers,
        )


def _customer_count(row: dict[str, Any]) -> int | None:
    """Headcount for a row, whichever column carries it.

    Different marts name it differently (`customers`, `vip_customers`,
    `cohort_customers`); the floor must apply to all of them, so the lookup is
    by convention rather than per-caller configuration.
    """
    for key in ("customers", "vip_customers", "cohort_customers", "active_customers"):
        value = row.get(key)
        if value is not None:
            try:
                return int(value)
            except (TypeError, ValueError):
                return None
    return None
