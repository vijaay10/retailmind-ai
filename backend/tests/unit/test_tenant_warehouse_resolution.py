"""Per-tenant warehouse path resolution — Prompt 12.5.

Fast, no-Docker unit coverage for the pure resolution logic that fixes the
shared-warehouse gap Prompt 12 found — the end-to-end proof (two real
warehouses, real revenue figures) lives in
`backend/tests/integration/test_tenant_warehouse_isolation.py`, which needs
Postgres and takes minutes; this file is what runs on every `make test`.
"""

import uuid

from app.core.config import WarehouseSettings
from app.infrastructure.db.models.auth import Tenant
from app.infrastructure.semantic.tenancy import TenantWarehouseRegistry, resolve_warehouse_path


def _tenant(*, slug: str, warehouse_path: str | None = None) -> Tenant:
    tenant = Tenant(slug=slug, name=slug, base_currency="USD")
    tenant.id = uuid.uuid4()
    tenant.warehouse_path = warehouse_path
    return tenant


def test_a_tenant_without_an_override_resolves_from_its_own_slug() -> None:
    settings = WarehouseSettings(root=".local/tenants")
    tenant = _tenant(slug="acme-retail")
    assert resolve_warehouse_path(tenant, settings) == ".local/tenants/acme-retail.duckdb"


def test_two_different_tenants_never_resolve_to_the_same_path() -> None:
    settings = WarehouseSettings(root=".local/tenants")
    a = resolve_warehouse_path(_tenant(slug="tenant-a"), settings)
    b = resolve_warehouse_path(_tenant(slug="tenant-b"), settings)
    assert a != b


def test_an_explicit_override_wins_over_the_slug_convention() -> None:
    """The demo tenant's real path — set once by migration
    0008_tenant_warehouse_isolation from whatever `RM_WAREHOUSE_DUCKDB_PATH`
    resolved to at migrate time — must never be silently replaced by the
    per-slug guess, or the demo tenant's dashboard would 503 in production.
    """
    settings = WarehouseSettings(root=".local/tenants")
    tenant = _tenant(slug="northwind-threads", warehouse_path="/data/warehouse/retailmind.duckdb")
    assert resolve_warehouse_path(tenant, settings) == "/data/warehouse/retailmind.duckdb"


def test_the_registry_caches_one_client_per_tenant_id() -> None:
    settings = WarehouseSettings(root=".local/tenants")
    registry = TenantWarehouseRegistry(settings)
    tenant = _tenant(slug="acme-retail")

    first = registry.client_for(tenant)
    second = registry.client_for(tenant)
    assert first is second


def test_two_tenants_never_share_a_cached_client() -> None:
    settings = WarehouseSettings(root=".local/tenants")
    registry = TenantWarehouseRegistry(settings)
    a = registry.client_for(_tenant(slug="tenant-a"))
    b = registry.client_for(_tenant(slug="tenant-b"))
    assert a is not b


def test_invalidate_forces_a_fresh_client_on_next_access() -> None:
    settings = WarehouseSettings(root=".local/tenants")
    registry = TenantWarehouseRegistry(settings)
    tenant = _tenant(slug="acme-retail")

    first = registry.client_for(tenant)
    registry.invalidate(str(tenant.id))
    second = registry.client_for(tenant)
    assert first is not second
