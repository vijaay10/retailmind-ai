"""Inventory intelligence service (Analytics, methods M7–M11).

Eight surfaces over the current stock position: what matters (ABC), what is
about to run out, what is never going to sell, how old it is, how long supply
takes, which vendors are the problem, what to order, and how the whole network
is doing.

Three rules run through all of them.

**The recommendation carries its own evidence.** Every number that suggests an
action ships with what produced it — demand, lead time, variability, the
service level being planned to. A reorder quantity a buyer cannot interrogate
is a number they will override, and then the system is decoration.

**Variability outranks averages.** Safety stock is driven by the *spread* of
demand and lead time, not their means. A supplier that is reliably eight days
late can be planned around by ordering eight days earlier; one that averages
eight but ranges two to thirty cannot, and costs far more to buffer.

**Thin evidence is labelled, not hidden.** A supplier with nine receipts has no
measurable OTIF, and rendering one anyway invites a contract renegotiation
built on noise. Those rows are returned with the evidence floor flagged rather
than silently dropped — the buyer should know the vendor exists.
"""

from dataclasses import dataclass
from typing import Any

import structlog

from app.domain.auth.entities import Principal
from app.domain.auth.permissions import Permission
from app.services.analytics.service import AnalyticsService
from app.services.shared import authz

log = structlog.get_logger(__name__)

#: Received PO lines needed before a supplier's OTIF is reported as measured.
#: Below this a rate is arithmetic, not evidence.
SUPPLIER_EVIDENCE_FLOOR = 20


@dataclass(frozen=True, slots=True)
class InventorySection:
    """Rows plus the query metadata that lets a caller judge them."""

    rows: list[dict[str, Any]]
    meta: dict[str, Any]


class InventoryIntelligenceService:
    """The eight inventory surfaces, all reading the governed registry."""

    def __init__(self, analytics: AnalyticsService) -> None:
        self._analytics = analytics

    # ── What matters ─────────────────────────────────────────────────

    async def abc_analysis(
        self, principal: Principal, *, by: str = "abc_class", limit: int = 100
    ) -> InventorySection:
        """Pareto classification: which products carry the business.

        Classes are cut on **cumulative revenue share within category** — A to
        80%, B to 95%, C beyond — not across the whole assortment. Classifying
        globally makes every product in a low-revenue category a C and starves
        it of service level until the category dies and the classification
        looks retrospectively correct.

        The service level attached to each class is what makes this actionable:
        it flows straight into the safety-stock term, so an A item is planned
        to 98% availability and a C to 85%.
        """
        section = await self._query(
            principal,
            domain="product_abc",
            metrics=[
                "skus",
                "revenue",
                "units",
                "margin",
                "margin_rate",
                "units_per_selling_day",
                "avg_service_level",
            ],
            dimensions=[by] if by == "abc_class" else [by, "abc_class"],
            sort_by="revenue",
            limit=limit,
        )
        return self._with_share(section, value_key="revenue", share_key="revenue_share")

    # ── What is about to go wrong ────────────────────────────────────

    async def stockout_risk(
        self, principal: Principal, *, by: str | None = None, limit: int = 50
    ) -> InventorySection:
        """Positions running out, soonest first.

        "Days until stockout" is on-hand divided by average daily demand — a
        projection, not a forecast, and it assumes demand continues as observed.
        The column that decides action is `at_risk_positions`: positions that
        will hit zero **before a replenishment could physically arrive**. For
        those, ordering today is already late, and the only remaining levers
        are expediting or transferring from another store.

        Without a grouping dimension this returns the position-level queue a
        replenishment analyst works through. Grouped, it becomes the summary a
        regional manager reads.
        """
        dimensions = [by] if by else ["sku", "product_name", "store_id", "store_name", "abc_class"]
        return await self._query(
            principal,
            domain="inventory_health",
            metrics=[
                "positions",
                "on_hand_units",
                "on_order_units",
                "daily_demand",
                "cover_days",
                "soonest_stockout_days",
                "at_risk_positions",
                "stockout_positions",
            ],
            dimensions=dimensions,
            sort_by="soonest_stockout_days",
            descending=False,
            limit=limit,
        )

    async def overstock(
        self, principal: Principal, *, by: str = "category", limit: int = 50
    ) -> InventorySection:
        """Stock that will not sell through in a reasonable horizon.

        Overstock is defined by **cover**, not by absolute quantity: 400 units
        of a product selling 30 a day is healthy, 40 units of one selling 0.1 a
        day is three years of supply. The threshold is twelve weeks.

        Dead stock is called out separately because it is a different problem.
        Overstock clears eventually and costs carrying charges; dead stock has
        no demand at all and will not clear without markdown — the decision is
        liquidation, not patience.
        """
        return await self._query(
            principal,
            domain="inventory_health",
            metrics=[
                "positions",
                "overstocked_positions",
                "dead_stock_positions",
                "excess_units",
                "excess_value",
                "inventory_value",
                "cover_days",
                "on_hand_units",
            ],
            dimensions=[by],
            sort_by="excess_value",
            limit=limit,
        )

    async def aging(self, principal: Principal, *, by: str = "aging_bucket") -> InventorySection:
        """How long stock has been sitting since it was received.

        Age is a leading indicator that the cover ratio misses. A position with
        eight weeks of cover looks acceptable; the same position untouched for
        five months is telling you the demand estimate is stale and the cover
        number is built on it.

        Measured from last receipt rather than from first receipt of the batch:
        the warehouse holds position snapshots, not lot-level traceability, so
        claiming true lot age would be inventing precision the data cannot
        support.

        Buckets are defined once, in the warehouse, so a position cannot be
        "31-60 days" on a dashboard and "1-2 months" in an export.
        """
        section = await self._query(
            principal,
            domain="inventory_health",
            metrics=[
                "positions",
                "avg_days_since_receipt",
                "inventory_value",
                "excess_value",
                "dead_stock_positions",
                "on_hand_units",
                "cover_days",
            ],
            dimensions=[by],
            # Always order by age itself, never by the bucket label: sorting
            # those lexically puts "180+" second, between "0-30" and "31-60".
            # Ascending for buckets gives the natural aging curve; descending
            # for anything else puts the oldest group first, where it belongs.
            sort_by="avg_days_since_receipt",
            descending=by != "aging_bucket",
        )
        return self._with_share(section, value_key="inventory_value", share_key="value_share")

    # ── Supply ───────────────────────────────────────────────────────

    async def lead_time(
        self, principal: Principal, *, by: str = "supplier_name"
    ) -> InventorySection:
        """Observed lead times against contracted ones.

        Three numbers, because one is not enough to plan with. The **mean**
        says what usually happens. The **P90** is what you actually plan to —
        planning to the mean means being out of stock half the time. The
        **coefficient of variation** is what makes suppliers comparable: a
        ±3-day spread is trivial on a 30-day lead time and severe on a 5-day
        one.

        `lead_time_vs_contract` is the gap between promise and practice, and
        it is the number that belongs in a vendor review.
        """
        return await self._query(
            principal,
            domain="supplier",
            metrics=[
                "closed_lines",
                "avg_lead_time_days",
                "p90_lead_time_days",
                "worst_lead_time_stddev",
                "worst_lead_time_cov",
                "avg_days_late",
                "on_time_rate",
            ],
            dimensions=[by],
            sort_by="avg_lead_time_days",
        )

    async def supplier_risk(
        self, principal: Principal, *, by: str = "supplier_name"
    ) -> InventorySection:
        """Supplier scorecard: reliability, and the exposure behind it.

        OTIF is split into its halves because they fail for different reasons.
        Late is a logistics conversation; short is a capacity or allocation
        one, and a vendor at 95% on-time and 70% in-full needs the second
        conversation, not the first.

        The risk band is **rule-based and legible** — a buyer has to defend it
        in a supplier review, and "the model said so" is not a defence. Vendors
        below the evidence floor are banded `insufficient_data` rather than
        given a flattering or damning rate computed from a handful of lines.
        """
        section = await self._query(
            principal,
            domain="supplier",
            metrics=[
                "po_lines",
                "closed_lines",
                "open_lines",
                "ordered_value",
                "otif_rate",
                "on_time_rate",
                "in_full_rate",
                "fill_rate",
                "avg_lead_time_days",
                "worst_lead_time_cov",
            ],
            dimensions=[by, "risk_band"] if by != "risk_band" else [by],
            sort_by="ordered_value",
        )
        return self._flag_evidence(section)

    # ── What to do ───────────────────────────────────────────────────

    async def reorder_suggestions(
        self, principal: Principal, *, by: str | None = None, due_only: bool = True, limit: int = 50
    ) -> InventorySection:
        """What to order, how much, and why — ranked by revenue at risk.

        The quantity is newsvendor arithmetic, not a rule of thumb:

            safety stock = z(service level) × √(LT·σ²demand + demand²·σ²LT)
            reorder point = demand × lead time + safety stock
            order = order-up-to level − (on hand + on order)

        Two properties matter. **Lead-time variance is in the safety stock**,
        which is why an erratic supplier costs more working capital than a
        merely slow one. And **on-order is subtracted**, so a position with a
        delivery already in transit is not ordered twice — the single most
        common way an automated reorder system destroys a working-capital
        budget.

        Ranked by revenue at risk rather than by how far below the reorder
        point a position sits. A staple 10% below its point loses more sales
        than a slow mover at zero.
        """
        dimensions = (
            [by]
            if by
            else [
                "sku",
                "product_name",
                "store_id",
                "store_name",
                "supplier_name",
                "abc_class",
                "on_order_source",
            ]
        )
        section = await self._query(
            principal,
            domain="reorder",
            metrics=[
                "positions",
                "below_reorder_point",
                "on_hand_units",
                "on_order_units",
                "daily_demand",
                "lead_time_days",
                "safety_stock",
                "reorder_point",
                "order_up_to_level",
                "suggested_order_qty",
                "revenue_at_risk",
                "soonest_stockout_days",
            ],
            dimensions=dimensions,
            sort_by="revenue_at_risk",
            limit=limit,
        )
        if not due_only:
            return section
        # A suggestion for a position that is comfortably stocked is noise in a
        # buyer's queue. Filtering here rather than in SQL keeps the registry
        # expression honest for the grouped views, which legitimately want the
        # full denominator.
        due = [row for row in section.rows if _as_int(row.get("below_reorder_point")) > 0]
        return InventorySection(rows=due, meta=section.meta)

    async def warehouse_health(
        self, principal: Principal, *, by: str = "region"
    ) -> InventorySection:
        """A composite health score per region, and the components behind it.

        The score is a **ranking device, not a diagnosis**. It exists so a
        network operator knows where to look first; the five components say
        what to actually fix, and they are always returned with it. A region
        scoring 74 because availability collapsed needs a different response
        from one scoring 74 because capital is trapped in excess, and a
        headline number cannot tell them apart.

        Scores are position-weighted when regions are rolled together, so a
        400-position region does not get the same vote as a 40-position one.
        """
        return await self._query(
            principal,
            domain="warehouse_health",
            metrics=[
                "health_score",
                "availability_score",
                "replenishment_score",
                "capital_efficiency_score",
                "assortment_score",
                "freshness_score",
                "positions",
                "stockout_positions",
                "stockout_rate",
                "at_risk_positions",
                "excess_value_share",
                "inventory_value",
                "avg_cover_days",
            ],
            dimensions=[by],
            sort_by="health_score",
            descending=False,
        )

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
    ) -> InventorySection:
        authz.require(principal, Permission.ANALYTICS_INVENTORY_READ)
        answer = await self._analytics.query(
            principal,
            domain_key=domain,
            metrics=metrics,
            dimensions=dimensions,
            sort_by=sort_by,
            descending=descending,
            limit=limit,
        )
        return InventorySection(rows=answer.result.rows, meta=answer.result.meta)

    def _with_share(
        self, section: InventorySection, *, value_key: str, share_key: str
    ) -> InventorySection:
        """Attach each row's share of the returned total.

        Computed here rather than in SQL because the denominator is *the
        result set*, not the table: a window function would divide by the
        whole assortment even when the caller asked about one department, and
        the shares would not add to 1.
        """
        total = sum(_as_float(row.get(value_key)) for row in section.rows)
        if total <= 0:
            return section

        running = 0.0
        rows: list[dict[str, Any]] = []
        for row in section.rows:
            value = _as_float(row.get(value_key))
            running += value
            rows.append(
                {
                    **row,
                    share_key: round(value / total, 4),
                    "cumulative_share": round(running / total, 4),
                }
            )
        return InventorySection(rows=rows, meta=section.meta)

    def _flag_evidence(self, section: InventorySection) -> InventorySection:
        """Mark rows whose rates rest on too few receipts to mean anything.

        Returned rather than filtered: a buyer needs to know a vendor exists
        and is unmeasured. Silently dropping it looks identical to the vendor
        having no orders, which is a different and much better situation.
        """
        rows = [
            {
                **row,
                "meets_evidence_floor": _as_int(row.get("closed_lines")) >= SUPPLIER_EVIDENCE_FLOOR,
                "evidence_floor": SUPPLIER_EVIDENCE_FLOOR,
            }
            for row in section.rows
        ]
        thin = sum(1 for row in rows if not row["meets_evidence_floor"])
        if thin:
            log.info(
                "inventory.supplier_evidence_floor", below_floor=thin, floor=SUPPLIER_EVIDENCE_FLOOR
            )
        return InventorySection(rows=rows, meta=section.meta)


def _as_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
