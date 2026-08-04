"""Inventory intelligence DTOs.

Recommendation responses carry their **inputs** alongside their outputs. A
buyer who cannot see the demand, lead time, and variability behind a suggested
order quantity will override it, and an overridden recommendation engine is
decoration with a maintenance cost.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ResponseModel(BaseModel):
    model_config = ConfigDict(from_attributes=False)


class SectionMeta(ResponseModel):
    """Where these numbers came from."""

    row_count: int | None = None
    data_snapshot_id: str | None = None
    freshness: str | None = None
    cache: str | None = None
    elapsed_ms: float | None = None


class InventorySectionResponse(ResponseModel):
    """The default shape: rows plus provenance."""

    grouped_by: str = Field(description="Dimension the rows are grouped by.")
    data: list[dict[str, Any]]
    meta: SectionMeta


class AbcResponse(ResponseModel):
    data: list[dict[str, Any]] = Field(
        description=(
            "One row per class (or per class × dimension), with each row's "
            "share of the returned revenue and the running cumulative share."
        )
    )
    grouped_by: str
    meta: SectionMeta


class StockoutRiskResponse(ResponseModel):
    at_risk_positions: int = Field(
        description=(
            "Positions projected to hit zero before a replenishment could "
            "physically arrive. For these, ordering today is already late."
        )
    )
    stockout_positions: int = Field(description="Positions already at zero on hand.")
    data: list[dict[str, Any]]
    grouped_by: str
    meta: SectionMeta


class OverstockResponse(ResponseModel):
    excess_value: float = Field(description="Working capital held above the twelve-week horizon.")
    overstocked_positions: int
    dead_stock_positions: int = Field(
        description=(
            "Positions with stock and no demand at all. A different problem "
            "from overstock: it will not clear without markdown."
        )
    )
    data: list[dict[str, Any]]
    grouped_by: str
    meta: SectionMeta


class ReorderResponse(ResponseModel):
    lines_due: int = Field(description="Positions that have fallen through their reorder point.")
    total_order_qty: float = Field(description="Units to order across all due lines.")
    revenue_at_risk: float = Field(
        description="Sales expected to be lost before replenishment lands, if nothing is ordered."
    )
    method: str = Field(
        description="How the quantities were derived — stated so the buyer can check them."
    )
    data: list[dict[str, Any]]
    grouped_by: str
    meta: SectionMeta


class SupplierRiskResponse(ResponseModel):
    evidence_floor: int = Field(
        description="Received PO lines needed before a supplier's rates are treated as measured."
    )
    below_evidence_floor: int = Field(
        description=(
            "Suppliers returned with too few receipts to score. Reported, not "
            "hidden: an unmeasured vendor is not the same as one with no orders."
        )
    )
    data: list[dict[str, Any]]
    grouped_by: str
    meta: SectionMeta


class WarehouseHealthResponse(ResponseModel):
    network_health_score: float | None = Field(
        default=None,
        description=(
            "Position-weighted composite across everything returned. A ranking "
            "device, not a diagnosis — the components say what to fix."
        ),
    )
    weakest: str | None = Field(
        default=None, description="Lowest-scoring group, which is where to look first."
    )
    data: list[dict[str, Any]]
    grouped_by: str
    meta: SectionMeta
