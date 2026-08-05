"""Natural-language query DTOs.

The response always carries the **plan** — what the question was understood to
mean — alongside the answer. That is the one thing a natural-language
interface most needs to prove: a user cannot tell a correct answer from an
answer to a different question unless the interpretation is shown back.

`compiled_sql` is displayed for the same reason. It is output, never input:
there is no field on any request in this module that accepts SQL, and nothing
would execute it if there were.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ResponseModel(BaseModel):
    model_config = ConfigDict(from_attributes=False)


class AskRequest(BaseModel):
    """A question, and nothing else.

    Deliberately minimal. There is no `sql`, no `table`, no `filter_expression`
    — a request shape that cannot express a statement cannot smuggle one.
    """

    model_config = ConfigDict(extra="forbid")

    question: str = Field(
        min_length=1,
        max_length=500,
        description="A question in plain English, e.g. 'show top customers'.",
    )
    as_of: str | None = Field(
        default=None, description="Anchor date for relative periods (YYYY-MM-DD)."
    )


class AskResponse(ResponseModel):
    question: str
    plan: dict[str, Any] = Field(
        description=(
            "How the question was understood: the domain, metrics, dimensions, "
            "and period chosen, with a confidence and anything left unresolved."
        )
    )
    columns: list[str] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    row_count: int = 0
    chart: dict[str, Any] | None = Field(
        default=None,
        description=(
            "A renderer-agnostic chart specification, with the reason its shape "
            "was chosen — a line across categories would imply an ordering they "
            "do not have."
        ),
    )
    explanation: dict[str, Any] | None = Field(
        default=None,
        description=(
            "The business reading, derived arithmetically from the rows rather "
            "than written by a model, so it cannot assert a trend the data does "
            "not show."
        ),
    )
    compiled_sql: str = Field(
        default="",
        description=(
            "The statement that ran, shown so the interpretation can be "
            "audited. Output only — this API accepts no SQL."
        ),
    )
    routed_to: str = Field(
        default="",
        description=(
            "Set when the question was not a query. 'Why did sales fall' is "
            "answered by root cause analysis, not by a SELECT."
        ),
    )
    payload: dict[str, Any] = Field(default_factory=dict)
    meta: dict[str, Any] = Field(default_factory=dict)


class CatalogueResponse(ResponseModel):
    domains: list[dict[str, Any]] = Field(
        description="Everything that can be asked about, and the exact terms available."
    )
