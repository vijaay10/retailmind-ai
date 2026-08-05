"""Recommendation DTOs.

Every impact figure travels with the **basis** it was computed on, and every
recommendation with its **downside**. Both are structural rather than
decorative: a response that reports only upside is a sales pitch, and two
estimates of the same size are not comparable when one is arithmetic over
observed stock and the other rests on an elasticity nobody has measured.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ResponseModel(BaseModel):
    model_config = ConfigDict(from_attributes=False)


class RecommendationsResponse(ResponseModel):
    recommendations: list[dict[str, Any]] = Field(
        description=(
            "Ranked by risk-adjusted profit: expected value weighted by "
            "confidence, less the downside weighted by the chance the "
            "reasoning does not hold."
        )
    )
    count: int
    by_category: dict[str, int]

    gross_profit_opportunity: float = Field(
        description=(
            "Naive sum of every recommendation's profit estimate. Reported "
            "only alongside the net figure, because overlapping actions chase "
            "the same pounds."
        )
    )
    net_profit_opportunity: float = Field(
        description=(
            "Sum with overlapping recommendations counted once. Where two "
            "actions touch the same SKU, store, or supplier, only the largest "
            "estimate counts — conservative, because the true joint effect "
            "needs a model of how the actions interact that nobody has."
        )
    )
    capital_freed: float = Field(
        description=(
            "Working capital released. Deliberately not added to profit: "
            "clearing dead stock frees cash and books a loss, and netting them "
            "would make every markdown look profitable."
        )
    )

    categories_requested: list[str]
    categories_empty: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Categories that produced nothing, and why. A silently absent "
            "category is indistinguishable from one that was never run."
        ),
    )
    caveats: list[str]
    meta: dict[str, Any] = Field(default_factory=dict)
