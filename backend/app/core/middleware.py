"""Middleware stack. **Order is behavior** — see below.

Registration in FastAPI is inside-out (last added runs first), so
``install_middleware`` adds them in reverse of the documented order. The
documented order, outermost first:

1. RequestID      — mint/propagate the correlation id everything else logs
2. AccessLog      — one structured line per request, with duration and status
3. SecurityHeaders— defence-in-depth headers on every response
4. RateLimit      — protect against abuse via sliding window limits
5. Idempotency    — cache mutation responses by idempotency key
6. CORS           — allow-listed origins only
7. Authentication — parse the bearer token, resolve the Principal

Authentication lives here *and* is re-declared as a route dependency
(``app.api.deps``). The middleware makes identity available to logging and to
every handler uniformly; the dependency makes it visible in OpenAPI and
enforces it per route. Two layers, one source of truth for the parsing.
"""

import time
import uuid
from collections.abc import Awaitable, Callable

import structlog
from fastapi import FastAPI, Request, Response
from redis.asyncio import Redis
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.cors import CORSMiddleware

from app.core.idempotency import IdempotencyMiddleware
from app.core.rate_limit import RateLimitMiddleware
from app.core.security import TokenSigner
from app.domain.auth.entities import Principal, PrincipalKind
from app.domain.auth.permissions import RoleKey
from app.domain.shared.errors import AuthenticationError

log = structlog.get_logger(__name__)

RequestHandler = Callable[[Request], Awaitable[Response]]

# Paths that never require (or attempt) authentication.
_PUBLIC_PATHS = frozenset(
    {
        "/health",
        "/ready",
        "/api/docs",
        "/api/redoc",
        "/api/openapi.json",
        "/api/v1/auth/login",
        "/api/v1/auth/refresh",
    }
)


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Establish the correlation id for the whole request lifecycle.

        Honours an inbound ``X-Request-ID`` from the edge (nginx/ALB mints one per
    ) so a single id spans proxy, API, worker, and warehouse query
        tags. UUIDv7 when we mint it ourselves: time-ordered ids sort usefully in
        log storage.
    """

    async def dispatch(self, request: Request, call_next: RequestHandler) -> Response:
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        request.state.request_id = request_id
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response


class AccessLogMiddleware(BaseHTTPMiddleware):
    """One ``http.request`` event per request.

    Logs the *route template* (``/api/v1/alerts/{id}``) rather than the raw
    path: raw paths explode metric cardinality and leak identifiers into log
    indexes.
    """

    async def dispatch(self, request: Request, call_next: RequestHandler) -> Response:
        started = time.perf_counter()
        response = await call_next(request)
        duration_ms = round((time.perf_counter() - started) * 1000, 2)

        route = request.scope.get("route")
        log.info(
            "http.request",
            method=request.method,
            path=getattr(route, "path", request.url.path),
            status=response.status_code,
            duration_ms=duration_ms,
        )
        response.headers["X-Response-Time-ms"] = str(duration_ms)
        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Baseline hardening headers (Backend).

    The edge sets these too; duplicating them here means a misconfigured proxy
    cannot silently remove the app's protection.
    """

    async def dispatch(self, request: Request, call_next: RequestHandler) -> Response:
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        # API responses are never a document context; a restrictive CSP costs
        # nothing here and blocks content-sniffing style attacks on error pages.
        response.headers.setdefault(
            "Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'"
        )
        response.headers.setdefault(
            "Cache-Control", "no-store" if request.url.path.startswith("/api") else "no-cache"
        )
        return response


class AuthenticationMiddleware(BaseHTTPMiddleware):
    """Parse ``Authorization: Bearer`` into ``request.state.principal``.

    Deliberately *non-enforcing*: an absent or invalid token leaves the
    principal unset and the request continues. Enforcement is the route
    dependency's job — that split is what lets public and authenticated routes
    share one parsing path, and what puts the requirement in OpenAPI where
    clients can see it.

    The ``token_version`` claim is checked against the database by the
    dependency layer (it needs a session); this layer only validates the
    signature and shape.
    """

    def __init__(self, app: FastAPI, signer: TokenSigner) -> None:
        super().__init__(app)
        self._signer = signer

    async def dispatch(self, request: Request, call_next: RequestHandler) -> Response:
        request.state.principal = None
        request.state.auth_error = None

        header = request.headers.get("Authorization")
        if (
            header
            and header.lower().startswith("bearer ")
            and request.url.path not in _PUBLIC_PATHS
        ):
            token = header.split(" ", 1)[1].strip()
            try:
                claims = self._signer.verify_access_token(token)
                principal = Principal.for_user(
                    user_id=uuid.UUID(claims["sub"]),
                    tenant_id=uuid.UUID(claims["tenant_id"]),
                    email="",  # not carried in the token; loaded on demand
                    roles=frozenset(RoleKey(r) for r in claims.get("roles", [])),
                    token_version=int(claims.get("token_version", 0)),
                    jti=claims.get("jti"),
                    kind=PrincipalKind.USER,
                )
                request.state.principal = principal
                structlog.contextvars.bind_contextvars(
                    tenant_id=str(principal.tenant_id),
                    user_id=str(principal.user_id),
                )
            except AuthenticationError as exc:
                # Stash it; the dependency raises so the failure surfaces on
                # protected routes only.
                request.state.auth_error = exc
            except (ValueError, KeyError) as exc:
                request.state.auth_error = AuthenticationError("Malformed access token.")
                log.info("auth.malformed_token", error=str(exc))

        return await call_next(request)


def install_middleware(
    app: FastAPI,
    *,
    signer: TokenSigner,
    cors_origins: list[str],
    redis: Redis | None = None,
    rate_limit_enabled: bool = True,
    idempotency_enabled: bool = True,
) -> None:
    """Register the stack. Added in reverse — see the module docstring.

    Args:
        app: FastAPI application
        signer: JWT token signer for authentication
        cors_origins: Allowed CORS origins
        redis: Redis connection for rate limiting & idempotency (optional)
        rate_limit_enabled: Whether to enable rate limiting (default: True)
        idempotency_enabled: Whether to enable idempotency (default: True)
    """
    # Starlette types add_middleware for positional-arg factories; ours takes
    # a keyword-only dependency, which the overloads do not model.
    app.add_middleware(AuthenticationMiddleware, signer=signer)  # type: ignore[arg-type]

    if cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_credentials=True,  # refresh cookie must survive the round trip
            allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type", "X-Request-ID", "Idempotency-Key"],
            expose_headers=["X-Request-ID", "X-Response-Time-ms", "X-Idempotency-Cached"],
        )

    # Idempotency (requires Redis, must be after auth to namespace by user)
    if idempotency_enabled and redis is not None:
        app.add_middleware(
            IdempotencyMiddleware,  # type: ignore[arg-type]
            redis=redis,
            ttl_seconds=86400,  # 24-hour cache
        )

    # Rate limiting (requires Redis)
    if rate_limit_enabled and redis is not None:
        app.add_middleware(
            RateLimitMiddleware,  # type: ignore[arg-type]
            redis=redis,
            per_ip_limit=100,  # 100 requests per minute per IP
            per_user_limit=200,  # 200 requests per minute per authenticated user
            window_seconds=60,
        )

    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(AccessLogMiddleware)
    app.add_middleware(RequestIDMiddleware)
