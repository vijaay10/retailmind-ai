"""Forecast DTOs.

Every response carries `caveats`. A forecast is a claim about a day that has
not happened, and the conditions under which it is weak — a model that does
not beat seasonal naive, a band too wide to plan against — are part of the
answer rather than a footnote someone has to go and look up.
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


class ForecastResponse(ResponseModel):
    target: str
    horizon_days: int = Field(description="Distinct horizon steps in the response.")
    data: list[dict[str, Any]] = Field(
        description="One row per forecast date × horizon × series, with its interval."
    )
    caveats: list[str] = Field(
        default_factory=list,
        description=(
            "Conditions that weaken this forecast — a model at parity with "
            "seasonal naive, or a band too wide to constrain a decision."
        ),
    )
    meta: SectionMeta


class ForecastTotalsResponse(ResponseModel):
    target: str
    grouped_by: str
    data: list[dict[str, Any]]
    caveats: list[str] = Field(default_factory=list)
    meta: SectionMeta


class AccuracyResponse(ResponseModel):
    models: list[dict[str, Any]] = Field(
        description="One row per model per producer, best WAPE first."
    )
    best_model: str | None = Field(
        default=None, description="Lowest WAPE among models with scored days."
    )
    caveats: list[str] = Field(default_factory=list)
    meta: SectionMeta


class ExplanationResponse(ResponseModel):
    target: str
    data: list[dict[str, Any]] = Field(
        description=(
            "Per-feature contributions. `baseline + sum(effect)` reconstructs "
            "the point forecast exactly — this is the model's own arithmetic, "
            "not an approximation of it."
        )
    )
    method: str = Field(description="How the contributions were derived.")
    meta: SectionMeta
