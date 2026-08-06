"""AI Business Analyst DTOs.

Facts and inferences are separate fields, not one list with a tag. A client
that renders them together would undo the distinction the service exists to
preserve — and the distinction between what the data shows and what somebody
concluded from it is the whole difference between an analyst and a chatbot.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ResponseModel(BaseModel):
    model_config = ConfigDict(from_attributes=False)


class AskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=500)
    conversation: list[dict[str, Any]] = Field(
        default_factory=list,
        max_length=20,
        description=(
            "Prior turns, so a follow-up like 'why did that drop?' can resolve. "
            "Only the subject and period of each turn are used — never its prose."
        ),
    )
    as_of: str | None = None
    period_days: int = Field(default=28, ge=7, le=180)


class AnalystResponse(ResponseModel):
    question: str
    capability: str = Field(
        description=(
            "Which of the eight capabilities answered: answer, explain_kpi, "
            "investigate, recommend, summarise, compare, explain_forecast, improve."
        )
    )
    headline: str = Field(description="The answer in one sentence. Stopping here must not mislead.")
    facts: list[dict[str, Any]] = Field(
        default_factory=list, description="What the data shows. Arithmetic, reproducible."
    )
    inferences: list[dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "What follows from it, if anything. Hypotheses consistent with the "
            "data, not established by it — kept apart so a guess is never read "
            "as a measurement."
        ),
    )
    checked: list[str] = Field(default_factory=list)
    not_checked: list[str] = Field(
        default_factory=list,
        description=(
            "What was left out, and why. The most useful sentence a senior "
            "analyst says is often 'I haven't looked at returns yet'."
        ),
    )
    caveats: list[str] = Field(default_factory=list)
    follow_ups: list[dict[str, Any]] = Field(
        default_factory=list,
        description="The next question worth asking, and why it is worth asking.",
    )
    data: dict[str, Any] = Field(default_factory=dict)
    conversation: dict[str, Any] = Field(default_factory=dict)
    meta: dict[str, Any] = Field(default_factory=dict)
