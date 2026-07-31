"""Customer intelligence DTOs.

Every response carries a `privacy` block. Suppression is reported rather than
silent because a caller seeing six of eight segments must know that two exist
and why they are missing — otherwise the suppression itself becomes a source
of wrong conclusions.
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


class PrivacyNote(ResponseModel):
    """What was withheld to publish this response, and why."""

    floor: int = Field(description="Minimum group size that may be reported.")
    suppressed_groups: int = Field(
        default=0, description="Groups withheld for falling below the floor."
    )
    suppressed_customers: int = Field(
        default=0, description="Customers inside those withheld groups."
    )
    note: str | None = Field(
        default=None,
        description="Human-readable explanation, present only when something was withheld.",
    )


class CustomerSectionResponse(ResponseModel):
    data: list[dict[str, Any]]
    privacy: PrivacyNote
    meta: SectionMeta


class RetentionResponse(ResponseModel):
    cohorts: list[str] = Field(description="Cohort weeks present in the response.")
    data: list[dict[str, Any]] = Field(
        description=(
            "One row per cohort × weeks-since-acquisition. Rows stop at the "
            "observation edge; a missing week has not happened yet."
        )
    )
    privacy: PrivacyNote
    meta: SectionMeta


class JourneyResponse(ResponseModel):
    stages: list[dict[str, Any]] = Field(
        description="Lifecycle stages with cumulative reach and stage-to-stage conversion."
    )
    privacy: PrivacyNote
    meta: SectionMeta


class ChurnRiskResponse(ResponseModel):
    grouped_by: str
    total_value_at_risk: float = Field(
        description="Lifetime value held by customers in medium or higher risk bands."
    )
    vip_value_at_risk: float = Field(
        description="The subset held by VIPs — expensive to replace, still reachable."
    )
    bands: list[dict[str, Any]]
    privacy: PrivacyNote
    meta: SectionMeta
