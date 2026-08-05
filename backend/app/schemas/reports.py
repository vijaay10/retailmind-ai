"""Report DTOs.

The JSON shape is the same document the binary exports render, so a client can
preview a report in the browser and download it knowing the two agree. That is
the point of having one document model rather than three format-specific
generators.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ResponseModel(BaseModel):
    model_config = ConfigDict(from_attributes=False)


class ReportResponse(ResponseModel):
    title: str
    subtitle: str
    period_start: str
    period_end: str
    period_label: str
    generated_at: str
    sections: list[dict[str, Any]] = Field(
        description=(
            "Ordered sections. A section that found nothing carries an "
            "`unavailable_reason` rather than being dropped — an omitted "
            "section and an empty one mean opposite things."
        )
    )
    caveats: list[str]
    meta: dict[str, Any] = Field(default_factory=dict)
