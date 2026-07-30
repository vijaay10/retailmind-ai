"""Auth repositories (Backend design §4).

Repositories own SQL and return domain-shaped results; services never see
SQLAlchemy constructs. Each method takes the scoping it needs explicitly —
there is no ambient "current tenant" that a future caller could forget.

Note the deliberate absence of a generic ``find_all``: each interface exposes
only the queries the use-cases actually need, which keeps them honest, fast,
and trivially fakeable in unit tests.
"""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.auth.entities import RefreshTokenRecord
from app.infrastructure.db.models import AppUser, AuthEvent, RefreshToken


class UserRepository:
    """Reads and writes ``app_user`` (with roles eagerly loaded)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_email(self, email: str) -> AppUser | None:
        """Lookup for login. Email is citext, so matching is case-insensitive.

        Not tenant-scoped: at login there is no established tenant yet. Email
        is unique per tenant, so a multi-tenant deployment sharing an address
        across tenants needs a tenant hint — captured as a TODO because the
        product ships single-tenant-per-email today.
        """
        stmt = (
            select(AppUser)
            .options(selectinload(AppUser.roles))
            .where(AppUser.email == email)
            .limit(2)  # detect ambiguity rather than silently taking the first
        )
        rows = list((await self._session.scalars(stmt)).all())
        return rows[0] if len(rows) == 1 else None

    async def get_by_id(self, user_id: uuid.UUID) -> AppUser | None:
        stmt = select(AppUser).options(selectinload(AppUser.roles)).where(AppUser.id == user_id)
        user: AppUser | None = await self._session.scalar(stmt)
        return user

    async def touch_last_login(self, user_id: uuid.UUID) -> None:
        await self._session.execute(
            update(AppUser).where(AppUser.id == user_id).values(last_login_at=datetime.now(tz=UTC))
        )

    async def update_password_hash(self, user_id: uuid.UUID, password_hash: str) -> None:
        """Used for transparent re-hash when Argon2 parameters change."""
        await self._session.execute(
            update(AppUser).where(AppUser.id == user_id).values(password_hash=password_hash)
        )

    async def bump_token_version(self, user_id: uuid.UUID) -> None:
        """Invalidate every outstanding access token for this user.

        Called on password change, role change, and disable — the cheap global
        logout that does not require tracking individual JWTs (Backend §8).
        """
        await self._session.execute(
            update(AppUser)
            .where(AppUser.id == user_id)
            .values(token_version=AppUser.token_version + 1)
        )


class RefreshTokenRepository:
    """Rotation-family lifecycle for refresh tokens (Backend design §8)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        user_id: uuid.UUID,
        token_hash: str,
        family_id: uuid.UUID,
        generation: int,
        ttl_days: int,
    ) -> RefreshTokenRecord:
        row = RefreshToken(
            user_id=user_id,
            token_hash=token_hash,
            family_id=family_id,
            generation=generation,
            expires_at=datetime.now(tz=UTC) + timedelta(days=ttl_days),
        )
        self._session.add(row)
        await self._session.flush()
        return _to_record(row)

    async def get_by_hash(self, token_hash: str) -> RefreshTokenRecord | None:
        """Lookup by digest — the plaintext is never stored or compared."""
        row = await self._session.scalar(
            select(RefreshToken).where(RefreshToken.token_hash == token_hash)
        )
        return _to_record(row) if row else None

    async def mark_rotated(self, token_id: uuid.UUID) -> None:
        await self._session.execute(
            update(RefreshToken)
            .where(RefreshToken.id == token_id)
            .values(rotated_at=datetime.now(tz=UTC))
        )

    async def revoke_family(self, family_id: uuid.UUID) -> int:
        """Kill every generation in a family. Returns how many were live.

        Triggered by reuse of an already-rotated token: either the user copied
        a stale session or someone stole one — both mean nothing in the family
        can be trusted again.
        """
        result = await self._session.execute(
            update(RefreshToken)
            .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=datetime.now(tz=UTC))
        )
        return int(getattr(result, "rowcount", 0) or 0)

    async def revoke_all_for_user(self, user_id: uuid.UUID) -> int:
        result = await self._session.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=datetime.now(tz=UTC))
        )
        return int(getattr(result, "rowcount", 0) or 0)


class AuthEventRepository:
    """Append-only security ledger; also the source of truth for lockout."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(
        self,
        *,
        event: str,
        tenant_id: uuid.UUID | None = None,
        user_id: uuid.UUID | None = None,
        ip: str | None = None,
        user_agent: str | None = None,
        detail: dict[str, object] | None = None,
    ) -> None:
        self._session.add(
            AuthEvent(
                tenant_id=tenant_id,
                user_id=user_id,
                event=event,
                ip=ip,
                user_agent=user_agent,
                detail=detail or {},
            )
        )

    async def recent_failures(
        self, user_id: uuid.UUID, window: timedelta
    ) -> tuple[int, datetime | None]:
        """``(failure_count, last_failure_at)`` inside the window.

        Feeds :class:`~app.domain.auth.entities.LockoutPolicy`; deriving
        lockout from the ledger means the ledger and the lock can never
        disagree about what happened.
        """
        since = datetime.now(tz=UTC) - window
        stmt = select(func.count(), func.max(AuthEvent.at)).where(
            AuthEvent.user_id == user_id,
            AuthEvent.event == "login.failed",
            AuthEvent.at >= since,
        )
        count, last_at = (await self._session.execute(stmt)).one()
        return int(count or 0), last_at


def _to_record(row: RefreshToken) -> RefreshTokenRecord:
    """ORM row → domain record. Mapping lives here so nothing above this layer
    ever touches a SQLAlchemy object."""
    return RefreshTokenRecord(
        id=row.id,
        user_id=row.user_id,
        family_id=row.family_id,
        generation=row.generation,
        expires_at=row.expires_at,
        rotated_at=row.rotated_at,
        revoked_at=row.revoked_at,
    )
