"""Notification endpoints (Analytics §13, PRD §32).

The in-app inbox, and a manual trigger for the detection sweep that the
scheduler otherwise runs hourly.

**Suppression is the feature.** Six detectors over a live estate produce far
more true conditions than anyone can read, and an alerting system that sends
all of them gets muted — after which the one alert that mattered is filed with
the rest. Every sweep therefore reports what it withheld and why, so the
silence is auditable rather than mysterious.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Body, Query

from app.api.deps import NotificationServiceDep, PrincipalDep
from app.schemas.notifications import (
    InboxResponse,
    MarkReadRequest,
    MarkReadResponse,
    NotificationItem,
    SweepResponse,
)

router = APIRouter(prefix="/notifications", tags=["notifications"])

_FORBIDDEN = {
    "description": "Requires alert access.",
    "content": {
        "application/problem+json": {
            "example": {
                "type": "https://retailmind.ai/errors/forbidden",
                "title": "Permission denied",
                "status": 403,
                "detail": "You do not have permission to perform this action.",
                "hint": "Requires the 'alerts.read' permission.",
            }
        }
    },
}


@router.get(
    "",
    response_model=InboxResponse,
    summary="The caller's notification inbox",
    responses={403: _FORBIDDEN},
)
async def inbox(
    principal: PrincipalDep,
    service: NotificationServiceDep,
    unread_only: Annotated[bool, Query(description="Only notifications not yet read.")] = False,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> InboxResponse:
    """Notifications addressed to the authenticated user, newest first.

    Scoped to the caller by their token rather than by a parameter — an inbox
    endpoint that takes a user id is one where guessing an identifier reads
    somebody else's mail.
    """
    rows, unread = await service.inbox(principal, unread_only=unread_only, limit=limit)
    return InboxResponse(
        notifications=[
            NotificationItem(
                id=str(row.id),
                event_type=row.event_type,
                severity=row.severity,
                payload=row.payload,
                read=row.read_at is not None,
                created_at=row.created_at.isoformat(),
            )
            for row in rows
        ],
        unread_count=unread,
    )


@router.post(
    "/read",
    response_model=MarkReadResponse,
    summary="Mark notifications read",
    responses={403: _FORBIDDEN},
)
async def mark_read(
    principal: PrincipalDep,
    service: NotificationServiceDep,
    request: Annotated[MarkReadRequest, Body()],
) -> MarkReadResponse:
    """Mark the caller's own notifications as read.

    The update is filtered by the caller's id as well as by the identifiers
    supplied, so a guessed or copied identifier belonging to someone else
    changes nothing and the count comes back lower than requested.
    """
    marked = await service.mark_read(
        principal, [uuid.UUID(value) for value in request.notification_ids]
    )
    return MarkReadResponse(marked=marked)


@router.post(
    "/sweep",
    response_model=SweepResponse,
    summary="Run the detection sweep now",
    responses={403: _FORBIDDEN},
)
async def sweep(principal: PrincipalDep, service: NotificationServiceDep) -> SweepResponse:
    """Run every detector immediately, as the scheduler does hourly.

    **Safe to call repeatedly.** The sweep decides what to send from what has
    already been sent rather than from when it last ran, so a second call a
    minute later delivers nothing new. That is the same property that makes
    the scheduled task safe to retry after a worker dies mid-run.

    The response reports what was *withheld* alongside what was sent. An
    operator asking why they heard nothing about a stockout gets an answer —
    "unchanged since it was last sent; quiet for another 9h" — rather than
    having to guess whether detection ran at all.

    A detector that fails is recorded in `detectors_failed` and the others
    continue. Six detectors read six surfaces, and one of them being degraded
    must not silence the rest.
    """
    result = await service.run_sweep(principal)
    return SweepResponse(**result.as_dict())
