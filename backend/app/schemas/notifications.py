"""Notification DTOs."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ResponseModel(BaseModel):
    model_config = ConfigDict(from_attributes=False)


class NotificationItem(ResponseModel):
    id: str
    event_type: str
    severity: str | None
    payload: dict[str, Any]
    read: bool
    created_at: str


class InboxResponse(ResponseModel):
    notifications: list[NotificationItem]
    unread_count: int


class MarkReadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    notification_ids: list[str] = Field(min_length=1, max_length=200)


class MarkReadResponse(ResponseModel):
    marked: int = Field(
        description=(
            "How many rows changed. Lower than the number requested when some "
            "were already read or belong to another user — the update is "
            "scoped to the caller, so a guessed identifier changes nothing."
        )
    )


class SweepResponse(ResponseModel):
    detected: int
    notified: int
    suppressed: int
    deliveries: int
    suppression_reasons: dict[str, int] = Field(
        default_factory=dict,
        description=(
            "Why candidates were withheld. Reported rather than discarded: "
            "'why didn't I hear about this?' deserves an answer."
        ),
    )
    by_kind: dict[str, int] = Field(default_factory=dict)
    detectors_failed: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Detectors that errored. One failing must not silence the other "
            "five, so failures are recorded and the sweep continues."
        ),
    )
