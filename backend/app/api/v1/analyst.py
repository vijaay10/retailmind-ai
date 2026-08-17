"""AI Business Analyst endpoint (Analytics).

One conversational surface over everything the platform can do: answering
questions, explaining KPIs, investigating movements, recommending actions,
summarising, comparing periods, explaining forecasts, and saying where the
measurement itself is weak.

**What makes it senior rather than junior.** Not that it knows more — that it
says what it did *not* check, distinguishes what the data shows from what it
infers, refuses questions the platform cannot answer instead of approximating
them, and volunteers the next question worth asking. Those are structural
fields on every response, not a matter of tone.

Requires `insights.read`. Each capability additionally enforces its own
permission through the engine behind it, so a role that cannot open the
profitability screen cannot reach it by asking politely.
"""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Body

from app.api.deps import AnalystServiceDep, PrincipalDep
from app.domain.shared.errors import ValidationDomainError
from app.schemas.analyst import AnalystResponse, AskRequest
from app.services.analyst.contracts import CannotAnswerError, Capability, Conversation, Turn
from app.services.analyst.service import as_payload

router = APIRouter(prefix="/analyst", tags=["business analyst"])

_FORBIDDEN = {
    "description": "Requires insight access.",
    "content": {
        "application/problem+json": {
            "example": {
                "type": "https://retailmind.ai/errors/forbidden",
                "title": "Permission denied",
                "status": 403,
                "detail": "You do not have permission to perform this action.",
                "hint": "Requires the 'insights.read' permission.",
            }
        }
    },
}


@router.post(
    "/ask",
    response_model=AnalystResponse,
    summary="Ask the business analyst",
    responses={403: _FORBIDDEN},
)
async def ask(
    principal: PrincipalDep,
    service: AnalystServiceDep,
    request: Annotated[AskRequest, Body()],
) -> AnalystResponse:
    """Answer a business question the way a senior analyst would.

    **Eight capabilities, routed rather than merged.** "What does AOV mean"
    needs no data at all and is answered from the metric registry. "Why did
    revenue fall" is not a query and goes to root cause analysis. "How much
    should I trust the forecast" is a question about a model, not about the
    business. Forcing all of them down one path produces answers that look
    right and address something else.

    **Facts and inferences come back separately.** A decomposition is
    arithmetic; an explanation for it is a hypothesis. Merging them into one
    list is how a reader comes to treat a guess as a measurement, so the API
    keeps them in different fields and a client cannot accidentally undo that.

    **`not_checked` is part of the answer.** An assistant that reports only
    what it looked at leaves its silences unreadable — the reader cannot tell
    whether returns were fine or simply never examined. The most valuable
    sentence here is often "I haven't looked at that yet."

    **Follow-ups are the point.** An analyst who answers exactly what was asked
    and stops is a search box. Each response proposes the next question and
    says why it is worth asking.

    **Context resolves pronouns.** Pass prior turns and "why did that drop?"
    resolves against the last subject. Only the subject and period of each turn
    are used — carrying prose forward is how an assistant starts answering
    questions about its own earlier phrasing. The substitution appears in the
    response, so a wrong guess is visible rather than silent.

    Questions the platform genuinely cannot answer are **refused with a
    reason**, not approximated. An assistant that always produces something
    teaches its users that every reply is a guess.
    """
    conversation = Conversation(
        turns=tuple(
            Turn(
                question=str(turn.get("question", "")),
                capability=Capability(turn.get("capability", "answer")),
                subject=str(turn.get("subject", "")),
                period_end=(
                    date.fromisoformat(turn["period_end"]) if turn.get("period_end") else None
                ),
                period_days=int(turn.get("period_days") or 0),
            )
            for turn in request.conversation
        )
    )

    try:
        answer, updated = await service.ask(
            principal,
            request.question,
            conversation=conversation,
            as_of=date.fromisoformat(request.as_of) if request.as_of else None,
            period_days=request.period_days,
        )
    except CannotAnswerError as error:
        # Refused rather than approximated. "I can't tell you that, and here is
        # why" is the more senior answer, and it keeps the answers that *are*
        # given worth trusting.
        raise ValidationDomainError(
            str(error),
            hint=error.because or "Ask /nlq/catalogue for what this platform can answer.",
        ) from error

    return AnalystResponse(**as_payload(answer, updated))
