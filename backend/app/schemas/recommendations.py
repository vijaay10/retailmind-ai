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
    decided_count: int = Field(
        default=0, description="How many of these already carry a recorded decision."
    )
    categories_empty: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Categories that produced nothing, and why. A silently absent "
            "category is indistinguishable from one that was never run."
        ),
    )
    caveats: list[str]
    meta: dict[str, Any] = Field(default_factory=dict)


class DecisionRequest(BaseModel):
    """A human's decision about one proposed action."""

    model_config = ConfigDict(extra="forbid")

    decision_key: str = Field(
        min_length=8,
        max_length=64,
        description="From the recommendation's `decision_key`. Identity of the subject acted on.",
    )
    action: str = Field(description="accepted | dismissed")
    reason_code: str | None = Field(
        default=None,
        description=(
            "Why it was dismissed: supplier_constraint | already_planned | "
            "disagree_forecast | other. Meaningless on an acceptance."
        ),
    )
    note: str | None = Field(default=None, max_length=500)


class DecisionResponse(ResponseModel):
    """What was recorded, read back."""

    decision: dict[str, Any]
    decided_by: str


class DecisionLogResponse(ResponseModel):
    """The team's recent decisions, newest first."""

    decisions: list[dict[str, Any]]
    count: int
    accepted_profit: float = Field(
        description=(
            "Expected profit across accepted actions, as estimated when each was "
            "accepted. Not realised profit — nothing here measures what happened next."
        )
    )
