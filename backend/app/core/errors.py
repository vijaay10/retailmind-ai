"""Exception → RFC 7807 mapping (Backend design §12, §19).

One registry decides the HTTP status, ``type`` URI, and public wording for
every domain error. Routers raise domain errors and stay out of the HTTP
business entirely, which is why status codes cannot drift between endpoints.

Error bodies are ``application/problem+json`` and always carry ``request_id``
so a user can quote one string and an engineer can find the trace. Internal
detail — stack traces, SQL, dependency names — never crosses this boundary.
"""

import structlog
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.domain.shared.errors import (
    AccountDisabledError,
    AccountLockedError,
    AppError,
    AuthenticationError,
    AuthorizationError,
    ConflictError,
    DependencyError,
    InvalidCredentialsError,
    NotFoundError,
    RateLimitedError,
    TokenReuseError,
    ValidationDomainError,
)

log = structlog.get_logger(__name__)

_TYPE_BASE = "https://retailmind.ai/errors"

# Exception class → (status, type slug, title). Order matters only in that
# subclasses must precede their parents when looked up by MRO walk below.
_REGISTRY: list[tuple[type[AppError], int, str, str]] = [
    (
        InvalidCredentialsError,
        status.HTTP_401_UNAUTHORIZED,
        "invalid-credentials",
        "Sign-in failed",
    ),
    (AccountLockedError, status.HTTP_401_UNAUTHORIZED, "account-locked", "Account locked"),
    (AccountDisabledError, status.HTTP_401_UNAUTHORIZED, "account-disabled", "Account disabled"),
    (TokenReuseError, status.HTTP_401_UNAUTHORIZED, "token-reuse-detected", "Session invalidated"),
    (
        AuthenticationError,
        status.HTTP_401_UNAUTHORIZED,
        "unauthenticated",
        "Authentication required",
    ),
    (AuthorizationError, status.HTTP_403_FORBIDDEN, "forbidden", "Permission denied"),
    (NotFoundError, status.HTTP_404_NOT_FOUND, "not-found", "Not found"),
    (ConflictError, status.HTTP_409_CONFLICT, "conflict", "Conflict"),
    (ValidationDomainError, status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid-state", "Invalid state"),
    (RateLimitedError, status.HTTP_429_TOO_MANY_REQUESTS, "rate-limited", "Too many requests"),
    (DependencyError, status.HTTP_503_SERVICE_UNAVAILABLE, "dependency-unavailable", "Unavailable"),
]


def _resolve(exc: AppError) -> tuple[int, str, str]:
    for klass, http_status, slug, title in _REGISTRY:
        if isinstance(exc, klass):
            return http_status, slug, title
    return status.HTTP_500_INTERNAL_SERVER_ERROR, "internal", "Internal error"


def _problem(
    request: Request,
    *,
    http_status: int,
    slug: str,
    title: str,
    detail: str,
    hint: str | None = None,
    extra: dict[str, object] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    body: dict[str, object] = {
        "type": f"{_TYPE_BASE}/{slug}",
        "title": title,
        "status": http_status,
        "detail": detail,
        "instance": request.url.path,
        "request_id": getattr(request.state, "request_id", None),
    }
    if hint:
        body["hint"] = hint
    if extra:
        body.update(extra)
    return JSONResponse(
        status_code=http_status,
        content=body,
        media_type="application/problem+json",
        headers=headers,
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Attach handlers. Called once by the app factory."""

    @app.exception_handler(AppError)
    async def _handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        http_status, slug, title = _resolve(exc)
        headers: dict[str, str] = {}

        # 401s must advertise the scheme; browsers and clients rely on it.
        if http_status == status.HTTP_401_UNAUTHORIZED:
            headers["WWW-Authenticate"] = "Bearer"
        if isinstance(exc, RateLimitedError):
            headers["Retry-After"] = str(exc.retry_after_seconds)
        if isinstance(exc, DependencyError) and exc.retryable:
            headers["Retry-After"] = "30"

        # Authorization denials are audited as security signal (Backend §9);
        # authentication failures are expected noise and logged at info.
        if isinstance(exc, AuthorizationError):
            log.warning(
                "authz.denied",
                permission=exc.permission,
                path=request.url.path,
                request_id=getattr(request.state, "request_id", None),
            )

        return _problem(
            request,
            http_status=http_status,
            slug=slug,
            title=title,
            detail=exc.public_message,
            hint=exc.hint,
            headers=headers or None,
        )

    @app.exception_handler(RequestValidationError)
    async def _handle_validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        """Pydantic 422s reshaped into the platform's single error format.

        Field errors are surfaced as a list so forms can bind them; they are
        client bugs, so they are counted but never logged at ERROR (§14).
        """
        errors = [
            {
                "field": ".".join(str(p) for p in err["loc"][1:]) or str(err["loc"][0]),
                "message": err["msg"],
                "type": err["type"],
            }
            for err in exc.errors()
        ]
        return _problem(
            request,
            http_status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            slug="validation-failed",
            title="Request validation failed",
            detail="One or more fields are invalid.",
            extra={"errors": errors},
        )

    @app.exception_handler(Exception)
    async def _handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        """Anything unmapped is a bug: log it fully, tell the client nothing."""
        log.exception(
            "http.unhandled_exception",
            path=request.url.path,
            request_id=getattr(request.state, "request_id", None),
        )
        return _problem(
            request,
            http_status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            slug="internal",
            title="Internal error",
            detail="Something went wrong on our side. We have been notified.",
        )
