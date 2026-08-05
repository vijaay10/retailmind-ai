"""Root cause analysis DTOs.

Every response separates **where** from **why**, and every finding carries the
kind of claim it is making. That structure is the honesty mechanism: a reader
who cannot tell a decomposition from a correlation will treat them alike, and
one of them is arithmetic while the other is a hypothesis.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ResponseModel(BaseModel):
    model_config = ConfigDict(from_attributes=False)


class WindowModel(ResponseModel):
    start: str
    end: str
    days: int


class InvestigationResponse(ResponseModel):
    metric: str
    current: WindowModel
    baseline: WindowModel
    current_value: float
    baseline_value: float = Field(
        description="Baseline scaled to the current window's length, so the comparison is per-day."
    )
    change: float
    relative_change: float | None

    explained_share: float = Field(
        description=(
            "Share of the change accounted for by the best single cut among "
            "the findings reported here. Counts only arithmetic findings: "
            "mechanisms and correlations re-describe pounds the decomposition "
            "has already attributed. Cuts are not added together either — "
            "region and segment are alternative views of the same change, so "
            "summing them counts every pound once per cut. "
            "Values above 100% are legitimate: when some slices fell while "
            "others grew, the fallers must over-explain a smaller net change."
        )
    )

    findings: list[dict[str, Any]] = Field(
        description="Every finding, ranked by confidence weighted by what is at stake."
    )
    where: list[dict[str, Any]] = Field(
        description="Exact decompositions: which parts of the estate moved."
    )
    why: list[dict[str, Any]] = Field(
        description="Candidate explanations: mechanisms and correlations, graded."
    )

    dimensions_investigated: list[str]
    dimensions_unavailable: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Dimensions that could not be investigated, and why. Reported "
            "rather than omitted: a silently missing dimension is "
            "indistinguishable from one that found nothing."
        ),
    )
    caveats: list[str]
    meta: dict[str, Any] = Field(default_factory=dict)
