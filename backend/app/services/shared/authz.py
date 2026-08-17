"""Authorization policy — the single decision point.

Two operations, deliberately. Everything in the platform that asks "may this
caller do X?" comes through :func:`require` or :func:`has`; nothing else is
allowed to interpret roles. Grep for ``ROLE_PERMISSIONS`` and this module plus
its tests should be the only hits outside the matrix itself.

Enforcement is layered: routers declare a permission dependency (so the
requirement appears in OpenAPI and unauthorized calls die before body
parsing), and service methods call ``require`` again as their first statement.
The duplication is intentional — a service invoked from a worker, a script, or
a future route still enforces its own rules.
"""

import structlog

from app.domain.auth.entities import Principal
from app.domain.auth.permissions import Permission
from app.domain.shared.errors import AuthorizationError

log = structlog.get_logger(__name__)


def has(principal: Principal, permission: Permission) -> bool:
    """Non-raising check, for shaping responses (hiding a button, trimming a nav)."""
    return principal.has(permission)


def require(principal: Principal, permission: Permission) -> None:
    """Raise :class:`AuthorizationError` unless the principal holds ``permission``.

    Denials are logged with the permission and principal for the security
    audit trail; the caller receives a 403 that names the requirement but
    nothing about what exists behind it.
    """
    if not principal.has(permission):
        log.info(
            "authz.check_failed",
            permission=permission.value,
            roles=sorted(r.value for r in principal.roles),
        )
        raise AuthorizationError(permission.value)


def require_any(principal: Principal, *permissions: Permission) -> None:
    """Pass if the principal holds at least one of ``permissions``.

    For endpoints reachable by different roles for different reasons — e.g. a
    metric query a Finance user runs for margin and an Inventory user runs for
    cover days.
    """
    if not any(principal.has(p) for p in permissions):
        raise AuthorizationError(" or ".join(p.value for p in permissions))
