"""Idempotency middleware for mutation operations.

Prevents duplicate execution of mutations (POST/PUT/PATCH/DELETE) when clients
retry failed requests. Uses Redis to cache responses keyed by idempotency key.

Usage:
    Client sends: Idempotency-Key: <uuid>
    First request: executes, stores response, returns result
    Retry: returns cached response without re-executing

Architecture:
    Redis key: idempotency:{user_id}:{idempotency_key}
    Value: {status_code, headers, body, created_at}
    TTL: 24 hours

Only applied to mutation methods (POST/PUT/PATCH/DELETE) with Idempotency-Key header.
GET requests are naturally idempotent and don't need this.
"""

import hashlib
import json
import time
from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from fastapi import FastAPI, Request, Response
from redis.asyncio import Redis
from starlette.middleware.base import BaseHTTPMiddleware

log = structlog.get_logger(__name__)

RequestHandler = Callable[[Request], Awaitable[Response]]

# Mutation methods that benefit from idempotency
_MUTATION_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# Paths where idempotency is never needed (public, readonly, or stateless)
_EXEMPT_PATHS = frozenset(
    {
        "/health",
        "/ready",
        "/metrics",
        "/api/v1/auth/login",  # Login: same creds → same session
        "/api/v1/auth/refresh",  # Refresh is inherently idempotent
    }
)


class IdempotencyMiddleware(BaseHTTPMiddleware):
    """Idempotency middleware using Redis cache.

    Caches responses for mutation operations when client sends Idempotency-Key header.
    Subsequent requests with same key return cached response without re-executing.

    Key format: idempotency:{user_id}:{idempotency_key}
    TTL: 24 hours

    Only applies to:
    - POST, PUT, PATCH, DELETE methods
    - Requests with Idempotency-Key header
    - Authenticated requests (uses user_id for namespacing)
    """

    def __init__(self, app: FastAPI, redis: Redis, *, ttl_seconds: int = 86400) -> None:
        """Initialize idempotency middleware.

        Args:
            app: FastAPI application
            redis: Redis connection for response cache
            ttl_seconds: Cache TTL in seconds (default: 24 hours)
        """
        super().__init__(app)
        self._redis = redis
        self._ttl_seconds = ttl_seconds

    def _build_cache_key(self, user_id: str, idempotency_key: str) -> str:
        """Build Redis cache key.

        Namespaced by user_id to prevent cross-user cache poisoning.

        Args:
            user_id: User UUID
            idempotency_key: Client-provided idempotency key

        Returns:
            Redis key string
        """
        # Hash the idempotency key to prevent Redis key length issues
        key_hash = hashlib.sha256(idempotency_key.encode()).hexdigest()[:16]
        return f"idempotency:{user_id}:{key_hash}"

    async def _get_cached_response(self, cache_key: str) -> dict[str, Any] | None:
        """Retrieve cached response.

        Args:
            cache_key: Redis key

        Returns:
            Cached response dict or None if not found/expired
        """
        try:
            cached = await self._redis.get(cache_key)
            if cached:
                result: dict[str, Any] = json.loads(cached)
                return result
        except Exception as exc:
            log.warning("idempotency.cache_read_failed", error=str(exc))
        return None

    async def _cache_response(
        self, cache_key: str, status_code: int, headers: dict[str, str], body: bytes
    ) -> None:
        """Cache a response.

        Args:
            cache_key: Redis key
            status_code: HTTP status code
            headers: Response headers dict
            body: Response body bytes
        """
        try:
            cached_response = {
                "status_code": status_code,
                "headers": dict(headers),  # Convert to dict for JSON serialization
                "body": body.decode("utf-8", errors="replace"),
                "cached_at": time.time(),
            }
            await self._redis.setex(
                cache_key,
                self._ttl_seconds,
                json.dumps(cached_response),
            )
        except Exception as exc:
            # Cache write failure: don't break the request
            log.warning("idempotency.cache_write_failed", error=str(exc))

    async def dispatch(self, request: Request, call_next: RequestHandler) -> Response:
        # Only apply to mutation methods
        if request.method not in _MUTATION_METHODS:
            return await call_next(request)

        # Exempt paths
        if request.url.path in _EXEMPT_PATHS:
            return await call_next(request)

        # Require Idempotency-Key header
        idempotency_key = request.headers.get("Idempotency-Key")
        if not idempotency_key:
            return await call_next(request)

        # Require authentication (namespace by user)
        principal = getattr(request.state, "principal", None)
        if not principal or not principal.user_id:
            # Unauthenticated mutation: pass through (auth middleware will reject)
            return await call_next(request)

        cache_key = self._build_cache_key(str(principal.user_id), idempotency_key)

        # Check for cached response
        cached = await self._get_cached_response(cache_key)
        if cached:
            cached_at = float(cached.get("cached_at", 0))
            log.info(
                "idempotency.cache_hit",
                idempotency_key=idempotency_key[:16],
                cached_at=cached_at,
                age_seconds=int(time.time() - cached_at),
            )
            return Response(
                content=cached["body"],
                status_code=int(cached["status_code"]),
                headers={
                    **cached["headers"],
                    "X-Idempotency-Cached": "true",
                    "X-Idempotency-Age": str(int(time.time() - cached_at)),
                },
            )

        # Execute request
        response = await call_next(request)

        # Cache successful mutations (2xx and 3xx)
        # Don't cache errors (4xx/5xx) - client may want to retry with fixes
        if 200 <= response.status_code < 400:
            # Read response body
            body = b""
            if hasattr(response, "body_iterator"):
                async for chunk in response.body_iterator:
                    body += chunk
            elif hasattr(response, "body"):
                resp_body = response.body
                body = bytes(resp_body) if not isinstance(resp_body, bytes) else resp_body

            # Cache the response
            await self._cache_response(
                cache_key,
                response.status_code,
                dict(response.headers),
                body,
            )

            log.info(
                "idempotency.cached",
                idempotency_key=idempotency_key[:16],
                status_code=response.status_code,
            )

            # Return response with original body
            return Response(
                content=body,
                status_code=response.status_code,
                headers={
                    **response.headers,
                    "X-Idempotency-Cached": "false",
                },
            )

        # Error response: don't cache, pass through
        return response
