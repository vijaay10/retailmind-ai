"""The two layers that decide what happens when something goes wrong.

A cache and an error handler are only interesting in their failure paths, and
those paths are exactly the ones an integration suite never reaches: Redis is
up in CI, and the API returns 200. So they are tested here, directly.

The properties that matter:

* **A cache outage degrades to a miss.** Analytics must keep answering when
  Redis is down. A cache that raises turns a warm-path optimisation into a
  single point of failure.
* **Tenant isolation is in the key.** The cache inherits the isolation the
  query layer enforces; losing it there would let one workspace read another's
  rows without any query ever crossing a boundary.
* **Errors keep their hint.** This API's problem details usually name the fix,
  and a handler that flattens them to "Request failed" throws away the only
  actionable part.
"""

import json
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from starlette.requests import Request

from app.core.errors import _problem, _resolve
from app.domain.shared.errors import (
    AccountLockedError,
    AppError,
    AuthorizationError,
    ConflictError,
    NotFoundError,
    RateLimitedError,
    ValidationDomainError,
)
from app.infrastructure.cache.redis_cache import AnalyticsCache, _encode, build_cache


class _Broken:
    """A Redis that fails the way a real one does under load."""

    async def get(self, key: str) -> str:
        raise ConnectionError("connection reset by peer")

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        raise ConnectionError("connection reset by peer")

    async def aclose(self) -> None:
        return None


def _request(path: str, *, request_id: str | None = None) -> Request:
    """A minimal ASGI request — enough for the problem document's fields."""
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": [],
        }
    )
    if request_id is not None:
        request.state.request_id = request_id
    return request


class _Memory:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.store[key] = value

    async def aclose(self) -> None:
        return None


# ── The cache degrades, never fails ──────────────────────────────────


async def test_a_cache_outage_reads_as_a_miss() -> None:
    """Analytics must keep answering when Redis is down."""
    cache = AnalyticsCache(_Broken())  # type: ignore[arg-type]
    assert await cache.get("any-key") is None


async def test_a_cache_outage_on_write_is_swallowed() -> None:
    """A failed write must not fail the request that produced the answer."""
    cache = AnalyticsCache(_Broken())  # type: ignore[arg-type]
    await cache.set("any-key", {"rows": []})  # must not raise


async def test_a_corrupt_entry_is_treated_as_a_miss() -> None:
    """Half-written JSON is not worth a 500 — recompute instead."""
    redis = _Memory()
    redis.store["k"] = "{not json"
    cache = AnalyticsCache(redis)  # type: ignore[arg-type]
    assert await cache.get("k") is None


async def test_a_disabled_cache_is_indistinguishable_from_an_empty_one() -> None:
    """Callers never branch on availability, so the no-Redis path must behave
    exactly like a cold cache rather than raising."""
    cache = build_cache(None)
    assert cache.enabled is False
    assert await cache.get("k") is None
    await cache.set("k", {"rows": [1]})
    assert await cache.get("k") is None
    await cache.close()


async def test_a_round_trip_returns_what_went_in() -> None:
    redis = _Memory()
    cache = AnalyticsCache(redis)  # type: ignore[arg-type]
    await cache.set("k", {"rows": [{"net_revenue": 1.5}]})
    assert await cache.get("k") == {"rows": [{"net_revenue": 1.5}]}


def test_the_key_carries_the_tenant_and_the_snapshot() -> None:
    """One workspace must never read another's cached rows, and a new snapshot
    must never serve the previous one's numbers."""
    cache = AnalyticsCache(None, env="prod")

    mine = cache.key(query_fingerprint="abc", snapshot_id="snap_1", tenant_id="t1")
    theirs = cache.key(query_fingerprint="abc", snapshot_id="snap_1", tenant_id="t2")
    newer = cache.key(query_fingerprint="abc", snapshot_id="snap_2", tenant_id="t1")

    assert mine != theirs
    assert mine != newer
    assert "t1" in mine
    assert mine.startswith("rm:prod:sem:")


def test_a_query_with_no_snapshot_still_keys_distinctly() -> None:
    """A missing snapshot id must not collapse into the same key as another."""
    cache = AnalyticsCache(None)
    assert "nosnap" in cache.key(query_fingerprint="abc", snapshot_id=None, tenant_id="t1")


# ── Encoding is a boundary concern, not arithmetic ───────────────────


def test_warehouse_types_survive_the_cache_boundary() -> None:
    """Decimals and dates come out of DuckDB and json refuses both."""
    assert _encode(Decimal("12.34")) == 12.34
    assert _encode(date(2026, 7, 21)) == "2026-07-21"
    assert _encode(datetime(2026, 7, 21, 9, 30, tzinfo=UTC)).startswith("2026-07-21T09:30")


def test_a_type_the_cache_cannot_encode_is_refused_loudly() -> None:
    """Silently dropping a value would cache a partial row and serve it as
    complete."""
    with pytest.raises(TypeError, match="cannot serialize"):
        json.dumps({"x": object()}, default=_encode)


# ── Errors keep the part that names the fix ──────────────────────────


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (AuthorizationError("recommendations.act"), 403),
        (NotFoundError("no such domain"), 404),
        (ValidationDomainError("bad window"), 422),
        (ConflictError("already decided"), 409),
        (RateLimitedError("slow down"), 429),
        # 401, not 423: a distinct status for a locked account would tell an
        # attacker their guess was the right username.
        (AccountLockedError("locked"), 401),
    ],
)
def test_each_domain_error_maps_to_its_own_status(error: AppError, expected: int) -> None:
    """Collapsing these onto 400 would make a permission problem, a typo and a
    locked account indistinguishable to the caller."""
    resolved, _, _ = _resolve(error)
    assert resolved == expected


def test_an_unregistered_error_becomes_a_500_rather_than_leaking() -> None:
    """A domain error nobody mapped must not fall through as a 200 or expose
    its class name to the caller."""

    class UnmappedError(AppError):
        pass

    resolved, slug, title = _resolve(UnmappedError("x"))
    assert resolved == 500
    assert slug == "internal"
    assert "Unmapped" not in title


def _body(response: object) -> dict[str, object]:
    return json.loads(bytes(response.body))  # type: ignore[attr-defined]


def test_a_problem_document_carries_a_type_and_a_request_id() -> None:
    """RFC 7807: the type URI is what lets a client branch on the failure, and
    the request id is what support asks for."""
    response = _problem(
        _request("/api/v1/recommendations/decisions", request_id="abc-123"),
        http_status=403,
        slug="forbidden",
        title="Permission denied",
        detail="You do not have permission to perform this action.",
        hint="Requires the 'recommendations.act' permission.",
    )
    body = _body(response)

    assert str(body["type"]).startswith("https://")
    assert body["status"] == 403
    assert body["request_id"] == "abc-123"
    assert "recommendations.act" in str(body["hint"])
    assert response.media_type == "application/problem+json"  # type: ignore[attr-defined]


def test_a_problem_without_a_hint_omits_the_key_rather_than_sending_null() -> None:
    """A null hint reads as "there is no fix", which is a different claim from
    "we did not compute one"."""
    body = _body(
        _problem(
            _request("/x"),
            http_status=500,
            slug="internal",
            title="Internal error",
            detail="",
        )
    )
    assert "hint" not in body


def test_the_instance_is_the_path_that_failed() -> None:
    """Support triage starts from "which endpoint", and a document without it
    sends them to the logs to find out."""
    body = _body(
        _problem(
            _request("/api/v1/forecasts/revenue"),
            http_status=404,
            slug="not_found",
            title="Not found",
            detail="no such target",
        )
    )
    assert body["instance"] == "/api/v1/forecasts/revenue"
