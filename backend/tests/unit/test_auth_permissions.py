"""RBAC matrix invariants — pure domain, no I/O.

These tests encode the *policy decisions* from the design, not the current
contents of the matrix. A test that merely restated the matrix would pass
forever and catch nothing; these assert the properties that must survive
future edits (least privilege for admin, no export for store managers,
separation between platform governance and business visibility).
"""

import uuid

import pytest

from app.domain.auth.entities import Principal
from app.domain.auth.permissions import (
    ROLE_DESCRIPTIONS,
    ROLE_PERMISSIONS,
    Permission,
    RoleKey,
    permissions_for,
)


def _principal(*roles: RoleKey) -> Principal:
    return Principal.for_user(
        user_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        email="user@example.test",
        roles=frozenset(roles),
        token_version=1,
    )


def test_every_role_has_permissions_and_a_description() -> None:
    for role in RoleKey:
        assert ROLE_PERMISSIONS.get(role), f"{role} grants nothing"
        assert ROLE_DESCRIPTIONS.get(role), f"{role} is undocumented"


def test_every_role_can_see_the_business_at_headline_level() -> None:
    """No role may be able to sign in yet see nothing — a dead-end login is a bug."""
    for role in RoleKey:
        perms = ROLE_PERMISSIONS[role]
        assert Permission.DASHBOARDS_READ in perms
        assert Permission.ANALYTICS_REVENUE_READ in perms


def test_only_admin_holds_platform_governance() -> None:
    """Admin governance verbs must not leak into business roles (least privilege)."""
    governance = {
        Permission.ADMIN_USERS,
        Permission.ADMIN_ROLES,
        Permission.ADMIN_BUDGETS,
        Permission.ADMIN_CONNECTORS,
        Permission.AUDIT_READ,
        Permission.DATA_MANAGE,
        Permission.ALERTS_CONFIGURE,
    }
    for role, perms in ROLE_PERMISSIONS.items():
        overlap = perms & governance
        if role is RoleKey.ADMIN:
            assert overlap == governance
        else:
            assert not overlap, f"{role} unexpectedly holds {sorted(p.value for p in overlap)}"


def test_admin_is_not_a_business_superuser() -> None:
    """Platform power and business visibility are separate concerns.

    An admin manages the platform; they do not get customer, margin, or
    supplier analytics by default. This is the property that makes 'admin'
    safe to hand to IT.
    """
    admin = ROLE_PERMISSIONS[RoleKey.ADMIN]
    assert Permission.ANALYTICS_CUSTOMER_READ not in admin
    assert Permission.ANALYTICS_PROFITABILITY_READ not in admin
    assert Permission.METRICS_EXPORT not in admin


def test_ceo_sees_every_analytics_module() -> None:
    ceo = ROLE_PERMISSIONS[RoleKey.CEO]
    module_reads = {p for p in Permission if p.value.startswith("analytics.")}
    assert module_reads <= ceo


def test_ceo_cannot_configure_the_platform() -> None:
    """Executive breadth is read/analyse, not operate."""
    ceo = ROLE_PERMISSIONS[RoleKey.CEO]
    assert Permission.ALERTS_CONFIGURE not in ceo
    assert Permission.ADMIN_USERS not in ceo
    assert Permission.DATA_MANAGE not in ceo


def test_store_manager_is_scoped_and_cannot_export() -> None:
    """Store managers act on their inbox; data leaves the building via managers."""
    perms = ROLE_PERMISSIONS[RoleKey.STORE_MANAGER]
    assert Permission.ALERTS_ACK in perms
    assert Permission.RECOMMENDATIONS_ACT in perms
    assert Permission.METRICS_EXPORT not in perms
    assert Permission.ANALYTICS_PROFITABILITY_READ not in perms
    assert Permission.ANALYTICS_MARKETING_READ not in perms


def test_functional_roles_see_their_own_module() -> None:
    expected = {
        RoleKey.MARKETING: Permission.ANALYTICS_MARKETING_READ,
        RoleKey.INVENTORY: Permission.ANALYTICS_INVENTORY_READ,
        RoleKey.FINANCE: Permission.ANALYTICS_PROFITABILITY_READ,
        RoleKey.STORE_MANAGER: Permission.ANALYTICS_STORE_READ,
    }
    for role, permission in expected.items():
        assert permission in ROLE_PERMISSIONS[role]


def test_finance_reads_money_but_does_not_operate() -> None:
    perms = ROLE_PERMISSIONS[RoleKey.FINANCE]
    assert Permission.ANALYTICS_PROFITABILITY_READ in perms
    assert Permission.METRICS_EXPORT in perms
    assert Permission.RECOMMENDATIONS_ACT not in perms  # reads recs, does not act
    assert Permission.ALERTS_ACK not in perms


def test_marketing_cannot_see_profitability_or_suppliers() -> None:
    """Module separation is the point of having functional roles at all."""
    perms = ROLE_PERMISSIONS[RoleKey.MARKETING]
    assert Permission.ANALYTICS_PROFITABILITY_READ not in perms
    assert Permission.ANALYTICS_SUPPLIER_READ not in perms


def test_multiple_roles_union_their_permissions() -> None:
    combined = permissions_for({RoleKey.MARKETING, RoleKey.FINANCE})
    assert Permission.ANALYTICS_MARKETING_READ in combined
    assert Permission.ANALYTICS_PROFITABILITY_READ in combined
    assert combined == ROLE_PERMISSIONS[RoleKey.MARKETING] | ROLE_PERMISSIONS[RoleKey.FINANCE]


def test_principal_resolves_permissions_from_roles() -> None:
    principal = _principal(RoleKey.INVENTORY)
    assert principal.has(Permission.FORECASTS_READ)
    assert not principal.has(Permission.ADMIN_USERS)


def test_principal_is_immutable() -> None:
    """Identity must not drift between middleware and service (frozen dataclass)."""
    principal = _principal(RoleKey.CEO)
    with pytest.raises((AttributeError, TypeError)):
        principal.roles = frozenset()  # type: ignore[misc]


def test_unknown_role_grants_nothing() -> None:
    assert permissions_for(set()) == frozenset()
