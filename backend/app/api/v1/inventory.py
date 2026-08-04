"""Inventory intelligence endpoints (Analytics §7).

Eight surfaces over the current stock position: ABC classification, stockout
prediction, overstock detection, aging, lead time, supplier risk, reorder
suggestions, and network health.

These read **point-in-time** marts — the stock position as of the latest
snapshot, not a time series. There is deliberately no date range parameter:
the warehouse keeps the current picture and the history of what actually
happened, not a replayable log of every recommendation it has ever made.
Answering "what did you suggest last Tuesday" would require storing
suggestions, which is a different feature with different retention rules.

All eight require `analytics.inventory.read`.
"""

from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import InventoryServiceDep, PrincipalDep
from app.schemas.inventory import (
    AbcResponse,
    InventorySectionResponse,
    OverstockResponse,
    ReorderResponse,
    SectionMeta,
    StockoutRiskResponse,
    SupplierRiskResponse,
    WarehouseHealthResponse,
)
from app.services.inventory.service import SUPPLIER_EVIDENCE_FLOOR, InventorySection

router = APIRouter(prefix="/inventory", tags=["inventory intelligence"])

_FORBIDDEN = {
    "description": "Requires the inventory analytics module.",
    "content": {
        "application/problem+json": {
            "example": {
                "type": "https://retailmind.ai/errors/forbidden",
                "title": "Permission denied",
                "status": 403,
                "detail": "You do not have permission to perform this action.",
                "hint": "Requires the 'analytics.inventory.read' permission.",
            }
        }
    },
}


def _meta(section: InventorySection) -> SectionMeta:
    return SectionMeta(**{k: v for k, v in section.meta.items() if k in SectionMeta.model_fields})


def _total(section: InventorySection, key: str) -> float:
    return round(sum(float(row.get(key) or 0) for row in section.rows), 2)


# ── What matters ─────────────────────────────────────────────────────


@router.get(
    "/abc",
    response_model=AbcResponse,
    summary="ABC classification",
    responses={403: _FORBIDDEN},
)
async def abc_analysis(
    principal: PrincipalDep,
    service: InventoryServiceDep,
    by: Annotated[
        str, Query(description="abc_class | category | department — how to group the classes.")
    ] = "abc_class",
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> AbcResponse:
    """Pareto classification: the few products that carry the business.

    Classes are cut on cumulative revenue share **within category** — A to 80%,
    B to 95%, C beyond. Classifying across the whole assortment instead is a
    trap: every product in a genuinely smaller category becomes a C, gets
    starved of service level, stops selling, and the classification looks
    retrospectively correct while having caused the outcome it predicted.

    `target_service_level` is what makes this operational rather than
    descriptive. It feeds the safety-stock term directly, so an A item is
    planned to 98% availability and a C to 85% — the classification decides
    where working capital goes.
    """
    section = await service.abc_analysis(principal, by=by, limit=limit)
    return AbcResponse(data=section.rows, grouped_by=by, meta=_meta(section))


# ── What is about to go wrong ────────────────────────────────────────


@router.get(
    "/stockout-risk",
    response_model=StockoutRiskResponse,
    summary="Stockout prediction",
    responses={403: _FORBIDDEN},
)
async def stockout_risk(
    principal: PrincipalDep,
    service: InventoryServiceDep,
    by: Annotated[
        str | None,
        Query(description="Omit for the position-level queue; set to group (region, category…)."),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
) -> StockoutRiskResponse:
    """Positions running out, soonest first.

    `days_until_stockout` is on-hand divided by observed daily demand. It is a
    **projection, not a forecast** — it assumes demand continues as it has, and
    says nothing about a promotion next week. The forecast endpoints are where
    that question belongs.

    The number that decides action is `at_risk_positions`: positions projected
    to reach zero *before a replenishment could physically arrive*, given the
    supplier's contracted lead time. Ordering those today is already late, and
    the remaining levers are expediting or transferring from another store.
    Separating them from ordinary low stock is the difference between a queue a
    buyer can work and an alarm that fires on everything.
    """
    section = await service.stockout_risk(principal, by=by, limit=limit)
    return StockoutRiskResponse(
        at_risk_positions=int(_total(section, "at_risk_positions")),
        stockout_positions=int(_total(section, "stockout_positions")),
        data=section.rows,
        grouped_by=by or "position",
        meta=_meta(section),
    )


@router.get(
    "/overstock",
    response_model=OverstockResponse,
    summary="Overstock and dead stock detection",
    responses={403: _FORBIDDEN},
)
async def overstock(
    principal: PrincipalDep,
    service: InventoryServiceDep,
    by: Annotated[
        str, Query(description="category | department | region | store_id | supplier_name.")
    ] = "category",
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
) -> OverstockResponse:
    """Stock that will not sell through in a reasonable horizon.

    Overstock is defined by **cover, not quantity**. Four hundred units of a
    product selling thirty a day is a healthy fortnight; forty units of one
    selling a tenth a day is over a year of supply. A quantity threshold would
    flag the first and miss the second, which is exactly backwards.

    `excess_value` is the number that belongs in a working-capital conversation
    — units above the twelve-week horizon, valued at cost.

    Dead stock is reported separately because the decision differs. Overstock
    clears eventually and costs carrying charges in the meantime; dead stock
    has no demand at all and will not clear without markdown, so the question
    is liquidation rather than patience.
    """
    section = await service.overstock(principal, by=by, limit=limit)
    return OverstockResponse(
        excess_value=_total(section, "excess_value"),
        overstocked_positions=int(_total(section, "overstocked_positions")),
        dead_stock_positions=int(_total(section, "dead_stock_positions")),
        data=section.rows,
        grouped_by=by,
        meta=_meta(section),
    )


@router.get(
    "/aging",
    response_model=InventorySectionResponse,
    summary="Inventory aging",
    responses={403: _FORBIDDEN},
)
async def aging(
    principal: PrincipalDep,
    service: InventoryServiceDep,
    by: Annotated[
        str, Query(description="aging_bucket | region | category | store_id | supplier_name.")
    ] = "aging_bucket",
) -> InventorySectionResponse:
    """How long stock has been sitting, bucketed.

    Age catches what the cover ratio misses. A position with eight weeks of
    cover reads as acceptable; the same position untouched for five months is
    telling you the demand estimate underneath that cover number is stale.

    Buckets widen as they age — 0-30, 31-60, 61-90, 91-180, 180+ — because the
    difference between five and twenty days on hand is a replenishment signal
    and the difference between two hundred and two hundred fifteen is not.
    They are defined once in the warehouse, so a position cannot be "31-60
    days" on a dashboard and "1-2 months" in an export.

    Measured from **last receipt**, not from the arrival of the oldest unit:
    the warehouse holds position snapshots, not lot-level traceability, and
    claiming true lot age would invent precision the data cannot support.
    """
    section = await service.aging(principal, by=by)
    return InventorySectionResponse(data=section.rows, grouped_by=by, meta=_meta(section))


# ── Supply ───────────────────────────────────────────────────────────


@router.get(
    "/lead-time",
    response_model=InventorySectionResponse,
    summary="Supplier lead time analysis",
    responses={403: _FORBIDDEN},
)
async def lead_time(
    principal: PrincipalDep,
    service: InventoryServiceDep,
    by: Annotated[str, Query(description="supplier_name | country | risk_band.")] = "supplier_name",
) -> InventorySectionResponse:
    """Observed lead times against contracted ones.

    Three numbers, because no single one is plannable:

    * **Mean** — what usually happens.
    * **P90** — what you actually plan to. Planning to the mean means being
      out of stock roughly half the time, which is the most common way a
      well-run replenishment system still misses.
    * **Coefficient of variation** — spread relative to the mean, and the only
      one of the three that compares suppliers fairly. A ±3-day spread is
      trivial on a 30-day lead time and severe on a 5-day one.

    Variability is the expensive property. A supplier reliably eight days late
    can be planned around by ordering eight days earlier; one averaging eight
    days but ranging from two to thirty has to be buffered against the tail,
    and that buffer is working capital.
    """
    section = await service.lead_time(principal, by=by)
    return InventorySectionResponse(data=section.rows, grouped_by=by, meta=_meta(section))


@router.get(
    "/supplier-risk",
    response_model=SupplierRiskResponse,
    summary="Supplier risk scorecard",
    responses={403: _FORBIDDEN},
)
async def supplier_risk(
    principal: PrincipalDep,
    service: InventoryServiceDep,
    by: Annotated[str, Query(description="supplier_name | country | risk_band.")] = "supplier_name",
) -> SupplierRiskResponse:
    """Vendor reliability, and the exposure sitting behind it.

    **OTIF is split into its halves.** On-time and in-full fail for different
    reasons and need different conversations: late is logistics, short is
    capacity or allocation. A vendor at 95% on-time and 70% in-full has the
    second problem, and a blended OTIF of 66% would send the buyer into the
    wrong meeting.

    **Open lines are excluded from every rate.** A purchase order still in
    transit has not failed, and counting it as a miss would make a supplier's
    score worsen simply because orders were placed recently.

    **The risk band is rule-based and legible.** A buyer has to defend it in a
    supplier review, where "the model said so" is not a defence. Bands come
    from OTIF and lead-time variability against stated thresholds, and a vendor
    with fewer than 20 received lines is banded `insufficient_data` rather than
    given a rate computed from noise — reported, not hidden, because an
    unmeasured vendor is not the same as one with no orders.

    Ordered value is returned alongside, because a 60% OTIF vendor carrying 2%
    of spend is a different priority from one carrying 40%.
    """
    section = await service.supplier_risk(principal, by=by)
    below = sum(1 for row in section.rows if not row.get("meets_evidence_floor"))
    return SupplierRiskResponse(
        evidence_floor=SUPPLIER_EVIDENCE_FLOOR,
        below_evidence_floor=below,
        data=section.rows,
        grouped_by=by,
        meta=_meta(section),
    )


# ── What to do ───────────────────────────────────────────────────────


@router.get(
    "/reorder",
    response_model=ReorderResponse,
    summary="Reorder suggestions",
    responses={403: _FORBIDDEN},
)
async def reorder_suggestions(
    principal: PrincipalDep,
    service: InventoryServiceDep,
    by: Annotated[
        str | None,
        Query(description="Omit for the buyer's line-level queue; set to group (supplier_name…)."),
    ] = None,
    due_only: Annotated[
        bool, Query(description="Only positions below their reorder point.")
    ] = True,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
) -> ReorderResponse:
    """What to order, how much, and the arithmetic behind it.

    Quantities come from the newsvendor model, not a rule of thumb:

    ```
    safety stock   = z(service level) × √(LT·σ²demand + demand²·σ²LT)
    reorder point  = demand × lead time + safety stock
    suggested qty  = order-up-to level − (on hand + on order)
    ```

    Two properties are worth checking, because systems that get them wrong fail
    expensively and quietly:

    **Lead-time variance sits inside the safety stock.** The second term under
    the root is why an erratic supplier ties up more working capital than a
    merely slow one — and why fixing supplier consistency releases cash that
    ordering less never will.

    **On-order is subtracted.** A position with a delivery already in transit
    is not ordered again. Double-ordering in-transit stock is the single most
    common way an automated replenishment system destroys a working-capital
    budget, and it does it while every individual suggestion looks reasonable.

    Ranked by `revenue_at_risk`, not by how far a position sits below its
    point. A staple 10% below its reorder point loses more sales than a slow
    mover already at zero, and a buyer with an hour should spend it on the
    first.

    `due_only=false` returns the full position set including healthy stock —
    useful for auditing the policy itself rather than working the queue.
    """
    section = await service.reorder_suggestions(principal, by=by, due_only=due_only, limit=limit)
    return ReorderResponse(
        lines_due=int(_total(section, "below_reorder_point")),
        total_order_qty=_total(section, "suggested_order_qty"),
        revenue_at_risk=_total(section, "revenue_at_risk"),
        method=(
            "newsvendor: safety stock = z(service level) × "
            "sqrt(lead_time · var(demand) + demand² · var(lead_time)); "
            "on-order subtracted from the order-up-to level"
        ),
        data=section.rows,
        grouped_by=by or "position",
        meta=_meta(section),
    )


@router.get(
    "/warehouse-health",
    response_model=WarehouseHealthResponse,
    summary="Network and warehouse health",
    responses={403: _FORBIDDEN},
)
async def warehouse_health(
    principal: PrincipalDep,
    service: InventoryServiceDep,
    by: Annotated[str, Query(description="region | health_band.")] = "region",
) -> WarehouseHealthResponse:
    """A composite health score per region, with the components that made it.

    The score is a **ranking device, not a diagnosis**. It answers "where do I
    look first" across a network too large to inspect position by position, and
    that is all it answers. The five components are always returned with it,
    because a region scoring 74 on collapsed availability needs a completely
    different response from one scoring 74 on capital trapped in excess stock,
    and the headline number cannot tell them apart.

    Components, each 0–100:

    * **Availability** — can a customer buy it? Driven by stockout rate.
    * **Replenishment** — is supply keeping up? Driven by positions that will
      run out inside their own lead time.
    * **Capital efficiency** — how much stock is dead weight.
    * **Assortment** — breadth actually carried against breadth ranged.
    * **Freshness** — how long stock has been sitting.

    Scores are **position-weighted** when groups are rolled together, so a
    400-position region does not get the same vote as a 40-position one. An
    unweighted mean of regional scores is how a network with one healthy
    outpost and four struggling metros reports itself as fine.
    """
    section = await service.warehouse_health(principal, by=by)
    weighted = 0.0
    positions = 0.0
    for row in section.rows:
        count = float(row.get("positions") or 0)
        weighted += float(row.get("health_score") or 0) * count
        positions += count

    return WarehouseHealthResponse(
        network_health_score=round(weighted / positions, 1) if positions else None,
        weakest=str(section.rows[0].get(by)) if section.rows else None,
        data=section.rows,
        grouped_by=by,
        meta=_meta(section),
    )
