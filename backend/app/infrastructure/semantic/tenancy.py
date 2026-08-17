"""Per-tenant warehouse resolution — Prompt 12.5.

Before this module existed, every request shared one `SemanticLayerClient`
built once at process startup from a single global `RM_WAREHOUSE_DUCKDB_PATH`
(`app.main:create_app`). Every tenant's analytics, dashboard, recommendation,
forecast, and RCA queries — everything that goes through the semantic layer —
read the same file regardless of who asked. Cache keys were already
tenant-scoped (`AnalyticsCache.key()`), which stopped one tenant from being
served *another tenant's cached response*, but did nothing about the query
itself reading the wrong warehouse on a cache miss.

The fix is physical isolation, not a filter: each tenant gets its own DuckDB
file. A bug that forgets a `WHERE tenant_id = ...` clause — the exact failure
mode Prompt 12.5 warns about — cannot leak a row that was never in the file
being queried. No dbt model changes were needed for this: the same,
unmodified pipeline (`ingestion.demo.build`, or the real ingestion CLI) is
run once per tenant, into a different output file, with that tenant's own
input data.
"""

from pathlib import Path

from app.core.config import WarehouseSettings
from app.infrastructure.db.models.auth import Tenant
from app.infrastructure.semantic.client import SemanticLayerClient


def resolve_warehouse_path(tenant: Tenant, settings: WarehouseSettings) -> str:
    """The DuckDB file this tenant's queries must read from — and no other.

    `tenant.warehouse_path` wins when set (the demo tenant's real data
    predates the per-slug convention below, so its row carries an explicit
    override — see migration `0008_tenant_warehouse_isolation`). Every other
    tenant resolves deterministically from its own immutable `slug`, which
    is exactly why `slug` is a URL-safe, unique, immutable business key
    already (`Tenant.slug`'s own docstring) — it was already the right
    handle for this, it just wasn't used for it yet.
    """
    if tenant.warehouse_path:
        return tenant.warehouse_path
    return str(Path(settings.root) / f"{tenant.slug}.duckdb")


class TenantWarehouseRegistry:
    """A small, process-local cache of `SemanticLayerClient` per tenant.

    `SemanticLayerClient` itself already opens a fresh DuckDB connection on
    every call (see its own docstring: "opens a read-only connection per
    call... isolation is worth it") — this registry doesn't add connection
    pooling, it just avoids re-resolving `warehouse_path` and re-constructing
    a client object (cheap, but not free — mostly the point is that it keeps
    each tenant's `_snapshot_cache` memoization alive across requests,
    exactly as it already worked for the single pre-Prompt-12.5 client).

    Not thread-safe against a true race on first access — two concurrent
    requests for the same brand-new tenant could each build one client and
    the second write wins. Harmless: both clients are identical (same path,
    same schema names), so the "race" only ever costs one extra, immediately
    discarded object. A lock was not worth the complexity for that.
    """

    def __init__(self, settings: WarehouseSettings) -> None:
        self._settings = settings
        self._clients: dict[str, SemanticLayerClient] = {}

    def client_for(self, tenant: Tenant) -> SemanticLayerClient:
        key = str(tenant.id)
        client = self._clients.get(key)
        if client is None:
            path = resolve_warehouse_path(tenant, self._settings)
            client = SemanticLayerClient(
                path,
                semantic_schema=self._settings.semantic_schema,
                core_schema=self._settings.core_schema,
            )
            self._clients[key] = client
        return client

    def invalidate(self, tenant_id: str) -> None:
        """Drop a cached client — e.g. after a tenant's warehouse is rebuilt."""
        self._clients.pop(tenant_id, None)
