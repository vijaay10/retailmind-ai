"""Redis result cache for analytics queries (Backend design §20).

The invalidation story is the interesting part: cache keys embed the warehouse
**snapshot id**, so a publish mints a new key space and yesterday's entries
simply age out. There is no invalidation code to get wrong, and no window
where a stale number can be served as fresh — correctness by construction
rather than by discipline.

Degradation is deliberate: a Redis outage produces cache misses, never errors.
Analytics reads pass through to the warehouse and the product stays up, slower.
"""

import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import structlog

log = structlog.get_logger(__name__)

try:  # pragma: no cover — optional at runtime
    from redis.asyncio import Redis

    _REDIS_AVAILABLE = True
except ImportError:  # pragma: no cover
    _REDIS_AVAILABLE = False


def _encode(value: Any) -> Any:
    """JSON encoder for warehouse types the stdlib refuses.

    Decimals become floats *only at the cache boundary* — the values were
    already rounded by the warehouse, and keeping them as strings would force
    every consumer to parse. Money arithmetic never happens here.
    """
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    raise TypeError(f"cannot serialize {type(value).__name__} for cache")


class AnalyticsCache:
    """Namespaced, snapshot-scoped cache for query results."""

    def __init__(
        self, redis: "Redis | None", *, ttl_seconds: int = 86_400, env: str = "dev"
    ) -> None:
        self._redis = redis if _REDIS_AVAILABLE else None
        self._ttl = ttl_seconds
        self._prefix = f"rm:{env}:sem"

    @property
    def enabled(self) -> bool:
        return self._redis is not None

    def key(self, *, query_fingerprint: str, snapshot_id: str | None, tenant_id: str) -> str:
        """``rm:{env}:sem:{tenant}:{snapshot}:{query}``.

        Tenant is in the key so one workspace can never read another's cached
        rows — the cache inherits the isolation the query layer enforces.
        """
        return f"{self._prefix}:{tenant_id}:{snapshot_id or 'nosnap'}:{query_fingerprint}"

    async def get(self, key: str) -> dict[str, Any] | None:
        if self._redis is None:
            return None
        try:
            payload = await self._redis.get(key)
        except Exception as exc:  # noqa: BLE001 — a cache outage is not an error
            log.warning("cache.read_failed", error=str(exc))
            return None
        if payload is None:
            return None
        try:
            return dict(json.loads(payload))
        except (TypeError, ValueError):
            # Corrupt entry: treat as a miss rather than failing the request.
            return None

    async def set(self, key: str, value: dict[str, Any]) -> None:
        if self._redis is None:
            return
        try:
            await self._redis.set(key, json.dumps(value, default=_encode), ex=self._ttl)
        except Exception as exc:  # noqa: BLE001
            log.warning("cache.write_failed", error=str(exc))

    async def close(self) -> None:
        if self._redis is not None:
            await self._redis.aclose()


def build_cache(url: str | None, *, env: str = "dev") -> AnalyticsCache:
    """Construct a cache, or a no-op one when Redis is unavailable.

    Callers never branch on availability — a disabled cache reports every
    lookup as a miss, so the calling code path is identical either way.
    """
    if not url or not _REDIS_AVAILABLE:
        return AnalyticsCache(None, env=env)
    try:
        from redis.asyncio import Redis

        return AnalyticsCache(Redis.from_url(url, decode_responses=True), env=env)
    except Exception as exc:  # noqa: BLE001 — never fail startup over a cache
        log.warning("cache.unavailable", error=str(exc))
        return AnalyticsCache(None, env=env)
