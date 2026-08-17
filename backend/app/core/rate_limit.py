"""Rate limiting using Redis sliding window.

Per-IP and per-user rate limits to protect against abuse and ensure fair resource
allocation. Limits are enforced via middleware and increment on every request.

Architecture:
    Redis key: rate_limit:{scope}:{identifier}:{window}
    Value: sorted set of timestamps (sliding window)

Example:
    rate_limit:ip:192.168.1.1:60s → {ts1, ts2, ts3, ...}

On each request:
    1. Remove timestamps older than window
    2. Count remaining timestamps
    3. If count >= limit, reject (429 Too Many Requests)
    4. Otherwise, add current timestamp and allow

Cleanup is automatic (Redis ZREMRANGEBYSCORE removes old entries).
"""

import time
from collections.abc import Awaitable, Callable

import structlog
from fastapi import FastAPI, Request, Response, status
from redis.asyncio import Redis
from starlette.middleware.base import BaseHTTPMiddleware

log = structlog.get_logger(__name__)

RequestHandler = Callable[[Request], Awaitable[Response]]

# Paths that are never rate limited (health checks, metrics).
_EXEMPT_PATHS = frozenset(
    {
        "/health",
        "/ready",
        "/metrics",
    }
)


class RateLimitExceededError(Exception):
    """Raised when a request exceeds the rate limit."""

    def __init__(self, retry_after: int) -> None:
        self.retry_after = retry_after
        super().__init__(f"Rate limit exceeded. Retry after {retry_after} seconds.")


class RateLimiter:
    """Sliding window rate limiter backed by Redis.

    Uses sorted sets (ZSET) where each member is a timestamp. The score is also
    the timestamp, allowing efficient removal of old entries via ZREMRANGEBYSCORE.

    This is more accurate than fixed windows and doesn't suffer from boundary
    issues where 2N requests can be made in a short burst at window edges.
    """

    def __init__(self, redis: Redis, *, window_seconds: int, max_requests: int) -> None:
        """Initialize rate limiter.

        Args:
            redis: Redis connection
            window_seconds: Time window in seconds (e.g., 60 for per-minute)
            max_requests: Maximum requests allowed in the window
        """
        self._redis = redis
        self._window_seconds = window_seconds
        self._max_requests = max_requests

    async def check_rate_limit(self, key: str) -> tuple[bool, int]:
        """Check if request is within rate limit.

        Args:
            key: Rate limit key (e.g., "ip:192.168.1.1" or "user:uuid")

        Returns:
            Tuple of (allowed: bool, retry_after: int seconds)

        Example:
            allowed, retry_after = await limiter.check_rate_limit("ip:1.2.3.4")
            if not allowed:
                raise RateLimitExceededError(retry_after)
        """
        now = time.time()
        window_start = now - self._window_seconds

        redis_key = f"rate_limit:{key}:{self._window_seconds}s"

        # Use pipeline to atomically:
        # 1. Remove timestamps older than window
        # 2. Count remaining timestamps
        # 3. Add current timestamp if under limit
        pipeline = self._redis.pipeline()
        pipeline.zremrangebyscore(redis_key, "-inf", window_start)
        pipeline.zcard(redis_key)
        result = await pipeline.execute()

        current_count = result[1]

        if current_count >= self._max_requests:
            # Find oldest timestamp to calculate retry_after
            oldest = await self._redis.zrange(redis_key, 0, 0, withscores=True)
            if oldest:
                oldest_timestamp = float(oldest[0][1])
                retry_after = int(self._window_seconds - (now - oldest_timestamp)) + 1
            else:
                retry_after = self._window_seconds

            return False, retry_after

        # Under limit: add current request timestamp
        pipeline = self._redis.pipeline()
        pipeline.zadd(redis_key, {str(now): now})
        pipeline.expire(redis_key, self._window_seconds + 60)  # TTL with buffer
        await pipeline.execute()

        return True, 0

    async def get_current_usage(self, key: str) -> tuple[int, int]:
        """Get current usage for a key.

        Args:
            key: Rate limit key

        Returns:
            Tuple of (current_requests, max_requests)
        """
        now = time.time()
        window_start = now - self._window_seconds
        redis_key = f"rate_limit:{key}:{self._window_seconds}s"

        # Remove old entries and count
        pipeline = self._redis.pipeline()
        pipeline.zremrangebyscore(redis_key, "-inf", window_start)
        pipeline.zcard(redis_key)
        result = await pipeline.execute()

        return result[1], self._max_requests


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Rate limit middleware with per-IP and per-user limits.

    Rate limits are layered:
    1. Per-IP limit (100 requests/minute) - prevents single IP abuse
    2. Per-user limit (200 requests/minute) - prevents single account abuse

    Both limits must be satisfied. The stricter limit wins.

    Exempt paths (health checks, metrics) are never rate limited.
    """

    def __init__(
        self,
        app: FastAPI,
        redis: Redis,
        *,
        per_ip_limit: int = 100,
        per_user_limit: int = 200,
        window_seconds: int = 60,
    ) -> None:
        """Initialize rate limit middleware.

        Args:
            app: FastAPI application
            redis: Redis connection for rate limit storage
            per_ip_limit: Max requests per IP per window (default: 100/min)
            per_user_limit: Max requests per user per window (default: 200/min)
            window_seconds: Time window in seconds (default: 60)
        """
        super().__init__(app)
        self._ip_limiter = RateLimiter(
            redis, window_seconds=window_seconds, max_requests=per_ip_limit
        )
        self._user_limiter = RateLimiter(
            redis, window_seconds=window_seconds, max_requests=per_user_limit
        )

    def _get_client_ip(self, request: Request) -> str:
        """Extract client IP from request.

        Prefers X-Forwarded-For (set by nginx/ALB) over direct connection IP.
        Takes the rightmost IP in X-Forwarded-For to avoid spoofing.
        """
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            # X-Forwarded-For: client, proxy1, proxy2
            # Rightmost untrusted IP is the real client
            return forwarded.split(",")[-1].strip()
        return request.client.host if request.client else "unknown"

    async def dispatch(self, request: Request, call_next: RequestHandler) -> Response:
        # Exempt paths: never rate limited
        if request.url.path in _EXEMPT_PATHS:
            return await call_next(request)

        client_ip = self._get_client_ip(request)

        # Check IP rate limit
        try:
            ip_allowed, ip_retry_after = await self._ip_limiter.check_rate_limit(f"ip:{client_ip}")
            if not ip_allowed:
                log.warning(
                    "rate_limit.exceeded",
                    scope="ip",
                    identifier=client_ip,
                    retry_after=ip_retry_after,
                )
                error_content = (
                    '{"error": "Rate limit exceeded", "retry_after": ' + str(ip_retry_after) + "}"
                )
                return Response(
                    content=error_content,
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    media_type="application/json",
                    headers={
                        "Retry-After": str(ip_retry_after),
                        "X-RateLimit-Limit": str(self._ip_limiter._max_requests),
                        "X-RateLimit-Remaining": "0",
                        "X-RateLimit-Reset": str(int(time.time()) + ip_retry_after),
                    },
                )
        except Exception as exc:
            # Redis failure: allow request (fail open, not closed)
            log.error("rate_limit.redis_failure", error=str(exc), scope="ip")
            return await call_next(request)

        # Check user rate limit (if authenticated)
        principal = getattr(request.state, "principal", None)
        if principal and principal.user_id:
            try:
                user_allowed, user_retry_after = await self._user_limiter.check_rate_limit(
                    f"user:{principal.user_id}"
                )
                if not user_allowed:
                    log.warning(
                        "rate_limit.exceeded",
                        scope="user",
                        identifier=str(principal.user_id),
                        retry_after=user_retry_after,
                    )
                    user_error_content = (
                        '{"error": "Rate limit exceeded", "retry_after": '
                        + str(user_retry_after)
                        + "}"
                    )
                    return Response(
                        content=user_error_content,
                        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                        media_type="application/json",
                        headers={
                            "Retry-After": str(user_retry_after),
                            "X-RateLimit-Limit": str(self._user_limiter._max_requests),
                            "X-RateLimit-Remaining": "0",
                            "X-RateLimit-Reset": str(int(time.time()) + user_retry_after),
                        },
                    )
            except Exception as exc:
                log.error("rate_limit.redis_failure", error=str(exc), scope="user")
                # Redis failure: allow request
                pass

        # Add rate limit headers to response
        response = await call_next(request)

        try:
            ip_current, ip_max = await self._ip_limiter.get_current_usage(f"ip:{client_ip}")
            response.headers["X-RateLimit-Limit"] = str(ip_max)
            response.headers["X-RateLimit-Remaining"] = str(max(0, ip_max - ip_current))
        except Exception as exc:
            # Best-effort header addition - don't fail request if Redis unavailable
            log.debug("rate_limit.header_failed", error=str(exc))

        return response
