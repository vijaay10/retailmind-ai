"""Auth domain entities and policies — framework-free.

Nothing here imports FastAPI, SQLAlchemy, or JWT libraries: these are the
rules, not the plumbing. That is what makes them unit-testable without a
database and reusable from workers as well as HTTP handlers.
"""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from app.domain.auth.permissions import Permission, RoleKey, permissions_for


class PrincipalKind(StrEnum):
    """How the caller authenticated. Downstream policy can differ by kind —
    e.g. API keys never receive interactive-only capabilities."""

    USER = "user"
    API_KEY = "api_key"
    SERVICE = "service"


@dataclass(frozen=True, slots=True)
class Principal:
    """The authenticated caller, resolved once per request.

    Immutable by construction: a request's identity must not drift between the
    middleware that established it and the service that authorizes against it.
    """

    user_id: uuid.UUID
    tenant_id: uuid.UUID
    email: str
    roles: frozenset[RoleKey]
    token_version: int
    kind: PrincipalKind = PrincipalKind.USER
    jti: str | None = None
    permissions: frozenset[Permission] = field(default_factory=frozenset)

    @classmethod
    def for_user(
        cls,
        *,
        user_id: uuid.UUID,
        tenant_id: uuid.UUID,
        email: str,
        roles: frozenset[RoleKey],
        token_version: int,
        jti: str | None = None,
        kind: PrincipalKind = PrincipalKind.USER,
    ) -> "Principal":
        """Build a principal with permissions derived from roles.

        Permissions are resolved here rather than carried in the JWT so a
        matrix change takes effect on the next request, with no token
        invalidation and no stale grants in flight.
        """
        return cls(
            user_id=user_id,
            tenant_id=tenant_id,
            email=email,
            roles=roles,
            token_version=token_version,
            kind=kind,
            jti=jti,
            permissions=permissions_for(roles),
        )

    def has(self, permission: Permission) -> bool:
        return permission in self.permissions


@dataclass(frozen=True, slots=True)
class TokenPair:
    """What a successful login or refresh hands back."""

    access_token: str
    access_expires_in: int
    refresh_token: str
    refresh_expires_at: datetime


@dataclass(frozen=True, slots=True)
class RefreshTokenRecord:
    """Stored state for one generation in a rotation family."""

    id: uuid.UUID
    user_id: uuid.UUID
    family_id: uuid.UUID
    generation: int
    expires_at: datetime
    rotated_at: datetime | None
    revoked_at: datetime | None

    def is_active(self, now: datetime | None = None) -> bool:
        now = now or datetime.now(tz=UTC)
        return self.revoked_at is None and self.rotated_at is None and self.expires_at > now


class LockoutPolicy:
    """Account lockout with exponential backoff.

    Counts recent failures from the ``auth_event`` ledger rather than a
    counter column: the ledger is already written for security analytics, and
    deriving lockout from it means the two can never disagree.

    Below the threshold, nothing happens. At and above it, each additional
    failure doubles the wait, capped — enough to make credential stuffing
    uneconomic without permanently locking out a human having a bad morning.
    """

    threshold = 5
    window = timedelta(minutes=15)
    base_backoff = timedelta(minutes=1)
    max_backoff = timedelta(minutes=60)

    @classmethod
    def locked_until(
        cls, recent_failures: int, last_failure_at: datetime | None
    ) -> datetime | None:
        """Return when the account unlocks, or None if it is not locked."""
        if last_failure_at is None or recent_failures < cls.threshold:
            return None
        # Clamp the exponent before doubling: a determined attacker can drive
        # the failure count arbitrarily high, and 2**n on a timedelta overflows
        # long before the cap would ever apply.
        max_doublings = int(cls.max_backoff / cls.base_backoff).bit_length()
        excess = min(recent_failures - cls.threshold, max_doublings)
        backoff = min(cls.base_backoff * (2**excess), cls.max_backoff)
        unlocks_at: datetime = last_failure_at + backoff
        return unlocks_at

    @classmethod
    def is_locked(
        cls, recent_failures: int, last_failure_at: datetime | None, now: datetime | None = None
    ) -> bool:
        until = cls.locked_until(recent_failures, last_failure_at)
        if until is None:
            return False
        return (now or datetime.now(tz=UTC)) < until
