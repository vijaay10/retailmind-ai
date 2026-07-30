"""Authentication endpoints (Backend design §13 endpoint map).

Session model, in one paragraph: sign in with credentials to receive a
short-lived access token (15 min, sent as ``Authorization: Bearer``) plus a
long-lived refresh token delivered as an httpOnly cookie. When the access
token expires, call ``POST /auth/refresh`` — the refresh token is *rotated*
on every use, and presenting a previously-rotated token revokes the entire
session family as a theft signal.
"""

from typing import Annotated

from fastapi import APIRouter, Cookie, Request, Response, status

from app.api.deps import AuthServiceDep, PrincipalDep, get_auth_settings
from app.core.config import AuthSettings
from app.domain.auth.entities import Principal, TokenPair
from app.domain.auth.permissions import ROLE_DESCRIPTIONS, ROLE_PERMISSIONS
from app.schemas.auth import (
    CurrentUserResponse,
    LoginRequest,
    PermissionCatalogEntry,
    RefreshRequest,
    TokenResponse,
)

router = APIRouter(prefix="/auth", tags=["auth"])

# Problem+json responses reused across the module's OpenAPI docs.
_UNAUTHORIZED = {
    "description": "Credentials rejected, expired, or the session was invalidated.",
    "content": {
        "application/problem+json": {
            "example": {
                "type": "https://retailmind.ai/errors/invalid-credentials",
                "title": "Sign-in failed",
                "status": 401,
                "detail": "Email or password is incorrect.",
                "instance": "/api/v1/auth/login",
                "request_id": "0f9a…",
            }
        }
    },
}


def _set_refresh_cookie(response: Response, pair: TokenPair, settings: AuthSettings) -> None:
    """Deliver the refresh token as an httpOnly cookie.

    ``httponly`` keeps it away from JavaScript (XSS cannot exfiltrate it),
    ``samesite=strict`` blocks CSRF-style cross-site replay, and the narrow
    ``path`` means it is only ever sent to the auth endpoints that need it.
    """
    response.set_cookie(
        key=settings.refresh_cookie_name,
        value=pair.refresh_token,
        max_age=settings.refresh_ttl_days * 24 * 3600,
        httponly=True,
        secure=bool(settings.cookie_secure),
        samesite="strict",
        path=settings.refresh_cookie_path,
    )


def _token_response(pair: TokenPair, *, include_refresh_in_body: bool) -> TokenResponse:
    return TokenResponse(
        access_token=pair.access_token,
        expires_in=pair.access_expires_in,
        refresh_token=pair.refresh_token if include_refresh_in_body else None,
        refresh_expires_at=pair.refresh_expires_at,
    )


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Sign in with email and password",
    responses={401: _UNAUTHORIZED},
)
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    service: AuthServiceDep,
) -> TokenResponse:
    """Exchange credentials for an access token and a refresh session.

    Returns 401 for both unknown accounts and wrong passwords — the responses
    are deliberately identical so this endpoint cannot be used to discover
    which email addresses exist. Repeated failures lock the account with
    exponential backoff.

    Browser clients receive the refresh token as an httpOnly cookie and should
    ignore the (null) `refresh_token` body field. Non-browser clients get the
    value in the body.
    """
    settings = get_auth_settings(request)
    is_browser = "cookie" in request.headers or "sec-fetch-mode" in request.headers

    _principal, pair = await service.login(
        email=payload.email,
        password=payload.password,
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    _set_refresh_cookie(response, pair, settings)
    return _token_response(pair, include_refresh_in_body=not is_browser)


@router.post(
    "/refresh",
    response_model=TokenResponse,
    summary="Rotate the session and get a fresh access token",
    responses={401: _UNAUTHORIZED},
)
async def refresh(
    payload: RefreshRequest,
    request: Request,
    response: Response,
    service: AuthServiceDep,
    rm_refresh: Annotated[str | None, Cookie()] = None,
) -> TokenResponse:
    """Rotate the refresh token and issue a new access token.

    The presented token is retired immediately. Presenting an already-rotated
    token is treated as theft: the whole session family is revoked and every
    device must sign in again.
    """
    settings = get_auth_settings(request)
    token = payload.refresh_token or rm_refresh
    if not token:
        from app.domain.shared.errors import AuthenticationError

        raise AuthenticationError(
            "No refresh token supplied.",
            hint="Send the refresh cookie, or include refresh_token in the body.",
        )

    _principal, pair = await service.refresh(
        refresh_token=token,
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    _set_refresh_cookie(response, pair, settings)
    return _token_response(pair, include_refresh_in_body=payload.refresh_token is not None)


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="End the current session",
)
async def logout(
    request: Request,
    response: Response,
    service: AuthServiceDep,
    rm_refresh: Annotated[str | None, Cookie()] = None,
) -> None:
    """Revoke the presented session family and clear the refresh cookie.

    Idempotent: logging out twice, or without a valid session, still succeeds.
    """
    settings = get_auth_settings(request)
    principal: Principal | None = getattr(request.state, "principal", None)
    await service.logout(refresh_token=rm_refresh, principal=principal)
    response.delete_cookie(
        settings.refresh_cookie_name,
        path=settings.refresh_cookie_path,
        httponly=True,
        secure=bool(settings.cookie_secure),
        samesite="strict",
    )


@router.get(
    "/me",
    response_model=CurrentUserResponse,
    summary="Who am I, and what may I do?",
    responses={401: _UNAUTHORIZED},
)
async def me(principal: PrincipalDep, service: AuthServiceDep) -> CurrentUserResponse:
    """Return the caller's identity, roles, and resolved permissions.

    Front-ends should drive navigation and control visibility from
    `permissions`, not from `roles` — that keeps the UI in lockstep with what
    the API will actually authorize.
    """
    email, display_name = await service.get_profile(principal.user_id)
    return CurrentUserResponse(
        id=str(principal.user_id),
        email=email,
        display_name=display_name,
        tenant_id=str(principal.tenant_id),
        roles=sorted(r.value for r in principal.roles),
        permissions=sorted(p.value for p in principal.permissions),
    )


@router.post(
    "/sessions/revoke-all",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Sign out of every device",
    responses={401: _UNAUTHORIZED},
)
async def revoke_all_sessions(principal: PrincipalDep, service: AuthServiceDep) -> None:
    """Revoke every refresh family **and** invalidate outstanding access tokens.

    Use after a suspected compromise: unlike `/logout`, already-issued access
    tokens stop working immediately rather than at their next expiry.
    """
    await service.revoke_all_sessions(principal.user_id)


@router.get(
    "/permissions",
    response_model=list[PermissionCatalogEntry],
    summary="Role → permission catalog",
)
async def permission_catalog() -> list[PermissionCatalogEntry]:
    """The full RBAC matrix, for admin UIs and access reviews.

    Public within the authenticated product surface: knowing which permissions
    a role *would* grant reveals nothing about who holds it, and access reviews
    are easier when the matrix is inspectable rather than folklore.
    """
    return [
        PermissionCatalogEntry(
            role=role.value,
            description=ROLE_DESCRIPTIONS[role],
            permissions=sorted(p.value for p in perms),
        )
        for role, perms in ROLE_PERMISSIONS.items()
    ]
