"""Auth DTOs (Backend design §15–16).

Requests are strict (``extra="forbid"``) so a typo'd field is a 422 rather
than a silently ignored parameter. Responses are hand-mapped from domain
objects — never ORM rows — because auto-serializing a model is how password
hashes end up in a JSON body.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class RequestModel(BaseModel):
    """Base for inbound payloads: reject unknown fields, freeze after parse."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class ResponseModel(BaseModel):
    model_config = ConfigDict(from_attributes=False)


class LoginRequest(RequestModel):
    email: EmailStr = Field(examples=["marcus@northwind.example"])
    password: str = Field(min_length=8, max_length=200, examples=["ChangeMe-Demo1!"])


class RefreshRequest(RequestModel):
    """Body-based refresh for non-browser clients.

    Browsers send the httpOnly cookie and omit this entirely; CLI and service
    clients, which have no cookie jar, pass the token explicitly.
    """

    refresh_token: str | None = Field(
        default=None,
        description="Omit when using the httpOnly refresh cookie (browser clients).",
    )


class TokenResponse(ResponseModel):
    """Access token plus metadata.

    The refresh token is intentionally absent: it is delivered as an httpOnly
    cookie so JavaScript cannot read it. Non-browser clients receive it in
    ``refresh_token`` only when they authenticated without cookie support.
    """

    access_token: str
    token_type: str = "bearer"  # noqa: S105 — scheme name, not a credential
    expires_in: int = Field(description="Access-token lifetime in seconds.")
    refresh_token: str | None = Field(
        default=None,
        description="Only populated for non-browser clients; browsers use the cookie.",
    )
    refresh_expires_at: datetime


class RoleSummary(ResponseModel):
    key: str
    description: str


class CurrentUserResponse(ResponseModel):
    """Identity plus the *resolved* permission set.

    Clients render navigation and controls from ``permissions``, never by
    inspecting role names — the same rule the backend follows, so the UI can
    never drift from what the API will actually allow.
    """

    id: str
    email: str
    display_name: str
    tenant_id: str
    roles: list[str]
    permissions: list[str]


class PermissionCatalogEntry(ResponseModel):
    role: str
    description: str
    permissions: list[str]
