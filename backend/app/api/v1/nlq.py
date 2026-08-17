"""Natural-language query endpoints (Analytics).

Ask a question in English; get a table, a chart, and a business explanation.

**How this prevents SQL injection.** Not by sanitising generated SQL — that
approach cannot be made safe, because a malicious statement is
indistinguishable from a legitimate one by inspection, and parameter binding
protects values while here the whole statement would be attacker-influenced.

Instead no SQL is generated from text at all. A question is resolved into a
*structured plan* whose every field must name something the metric registry
declares, and the registry is a closed set that no request can extend. The
plan is then compiled by the same governed layer every other endpoint uses.

Four independent layers, any three of which could fail without the feature
becoming unsafe:

1. The planner emits registry keys, never statements or fragments.
2. Validation rejects any metric, dimension, or filter the domain does not
   declare — a payload of `'; DROP TABLE users; --` is simply an unknown
   metric name, and fails exactly like a typo.
3. The compiler owns the statement: identifiers come from the registry,
   values bind as parameters, operators come from a fixed map.
4. The warehouse connection is opened read-only.

The request body has no field capable of carrying SQL, and rejects unknown
fields outright.
"""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Body

from app.api.deps import NlqServiceDep, PrincipalDep
from app.domain.shared.errors import ValidationDomainError
from app.schemas.nlq import AskRequest, AskResponse, CatalogueResponse
from app.services.nlq.contracts import UnsupportedQuestionError

router = APIRouter(prefix="/nlq", tags=["natural language"])

_FORBIDDEN = {
    "description": "Requires natural-language query access.",
    "content": {
        "application/problem+json": {
            "example": {
                "type": "https://retailmind.ai/errors/forbidden",
                "title": "Permission denied",
                "status": 403,
                "detail": "You do not have permission to perform this action.",
                "hint": "Requires the 'nlq.ask' permission.",
            }
        }
    },
}


@router.post(
    "/ask",
    response_model=AskResponse,
    summary="Ask a question in plain English",
    responses={403: _FORBIDDEN},
)
async def ask(
    principal: PrincipalDep,
    service: NlqServiceDep,
    request: Annotated[AskRequest, Body()],
) -> AskResponse:
    """Answer a business question, and show how it was understood.

    **Questions are routed, not all translated.** "Show top customers" and
    "compare stores" become governed aggregate queries. "Why did sales
    decrease?" does not — no SELECT statement answers *why*, and one that
    appears to produces a table a reader will over-interpret. It is routed to
    root cause analysis, which decomposes the change and grades each candidate
    explanation by the kind of evidence behind it. "What will sell next week"
    goes to the published forecasts; "what should I do" to the recommendation
    engine.

    **The interpretation comes back with the answer.** `plan` states the
    domain, metrics, dimensions, and period that were chosen, with a
    confidence and a list of any terms that could not be resolved. This is the
    part that makes the feature trustworthy: a user cannot distinguish a
    correct answer from an answer to a *different question* unless the
    interpretation is shown, and a term silently dropped is how somebody comes
    to rely on a number that answered something else.

    **The chart explains its own shape.** Categorical results get bars rather
    than lines, because a line implies the categories sit on a continuum. Long
    results get a table, because past a few dozen rows no chart reads clearly.

    **The explanation is arithmetic, not prose generation.** Totals, shares,
    and concentration are computed from the returned rows, so the narrative
    cannot assert a trend the numbers do not contain.

    `compiled_sql` is returned so the query can be audited. It is output only:
    this endpoint accepts a question and nothing else, and the request schema
    rejects unknown fields.
    """
    try:
        answer = await service.ask(
            principal,
            request.question,
            as_of=date.fromisoformat(request.as_of) if request.as_of else None,
        )
    except UnsupportedQuestionError as error:
        # Refused rather than answered approximately. A system that always
        # returns something teaches users that every answer is a guess, and
        # the guesses look exactly like the correct ones.
        raise ValidationDomainError(
            str(error),
            hint=error.hint or "Ask GET /nlq/catalogue to see what this platform can answer.",
        ) from error

    return AskResponse(**answer.as_dict())


@router.get(
    "/catalogue",
    response_model=CatalogueResponse,
    summary="What can be asked about",
    responses={403: _FORBIDDEN},
)
async def catalogue(principal: PrincipalDep, service: NlqServiceDep) -> CatalogueResponse:
    """The exact vocabulary this interface understands.

    Published deliberately. An interface whose scope is invisible trains users
    to guess at what it knows, and a guess that happens to return rows is
    indistinguishable from one that returns the *right* rows.

    This is also the complete list of things a question can reach. There is no
    domain, table, or column outside it — which is the same fact that makes
    the endpoint injection-proof, stated from the user's side rather than the
    attacker's.
    """
    return CatalogueResponse(domains=service.catalogue(principal))
