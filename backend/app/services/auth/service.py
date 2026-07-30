"""Authentication use cases (Backend design §5, §8).

Every method here is a complete unit of work: it validates, mutates, writes
its security-ledger entry, and returns domain objects. HTTP concerns (cookies,
status codes) belong to the router; persistence details belong to the
repositories. What is left in the middle is the actual policy, which is what
makes this file the one to read when asking "how does login work?".
"""

import uuid
from datetime import UTC, datetime

import structlog

from app.core.config import AuthSettings
from app.core.security import (
    TokenSigner,
    generate_refresh_token,
    hash_password,
    hash_refresh_token,
    needs_rehash,
    verify_password,
)
from app.domain.auth.entities import LockoutPolicy, Principal, TokenPair
from app.domain.auth.permissions import RoleKey
from app.domain.shared.errors import (
    AccountDisabledError,
    AccountLockedError,
    AuthenticationError,
    InvalidCredentialsError,
    TokenReuseError,
)
from app.infrastructure.db.models import AppUser
from app.infrastructure.db.repositories.auth import (
    AuthEventRepository,
    RefreshTokenRepository,
    UserRepository,
)
from app.services.shared.uow import UnitOfWork

log = structlog.get_logger(__name__)


class AuthService:
    """Login, refresh, logout, and principal resolution."""

    def __init__(
        self,
        *,
        users: UserRepository,
        tokens: RefreshTokenRepository,
        events: AuthEventRepository,
        signer: TokenSigner,
        settings: AuthSettings,
        uow: UnitOfWork,
    ) -> None:
        self._users = users
        self._tokens = tokens
        self._events = events
        self._uow = uow
        self._signer = signer
        self._settings = settings

    # ── Login ────────────────────────────────────────────────────────

    async def login(
        self,
        *,
        email: str,
        password: str,
        ip: str | None = None,
        user_agent: str | None = None,
    ) -> tuple[Principal, TokenPair]:
        """Authenticate with credentials and open a new token family.

        The unknown-account and wrong-password paths are indistinguishable to
        the caller *and* take comparable time (the password verifier burns a
        dummy hash when there is no user), so this endpoint cannot be used to
        enumerate accounts.
        """
        user = await self._users.get_by_email(email)

        if user is None:
            verify_password(password, None)  # equalize timing
            await self._events.record(
                event="login.failed",
                ip=ip,
                user_agent=user_agent,
                detail={"reason": "no_such_user"},
            )
            await self._uow.commit_now()  # the ledger must outlive the raise
            raise InvalidCredentialsError

        failures, last_failure = await self._events.recent_failures(user.id, LockoutPolicy.window)
        if LockoutPolicy.is_locked(failures, last_failure):
            await self._events.record(
                event="login.blocked",
                tenant_id=user.tenant_id,
                user_id=user.id,
                ip=ip,
                user_agent=user_agent,
                detail={"failures": failures},
            )
            await self._uow.commit_now()
            raise AccountLockedError

        if not verify_password(password, user.password_hash):
            await self._events.record(
                event="login.failed",
                tenant_id=user.tenant_id,
                user_id=user.id,
                ip=ip,
                user_agent=user_agent,
                detail={"reason": "bad_password"},
            )
            await self._uow.commit_now()  # feeds the lockout counter
            raise InvalidCredentialsError

        if user.status != "active":
            await self._events.record(
                event="login.blocked",
                tenant_id=user.tenant_id,
                user_id=user.id,
                ip=ip,
                detail={"reason": f"status:{user.status}"},
            )
            await self._uow.commit_now()
            raise AccountDisabledError

        # Transparent upgrade when cost parameters have been raised since the
        # password was set — the user never notices, the estate self-heals.
        if user.password_hash and needs_rehash(user.password_hash):
            await self._users.update_password_hash(user.id, hash_password(password))
            log.info("auth.password_rehashed", user_id=str(user.id))

        await self._users.touch_last_login(user.id)
        await self._events.record(
            event="login.success",
            tenant_id=user.tenant_id,
            user_id=user.id,
            ip=ip,
            user_agent=user_agent,
        )

        principal = self._principal_for(user)
        pair = await self._issue_pair(user, family_id=uuid.uuid4(), generation=1)
        return principal, pair

    # ── Refresh ──────────────────────────────────────────────────────

    async def refresh(
        self,
        *,
        refresh_token: str,
        ip: str | None = None,
        user_agent: str | None = None,
    ) -> tuple[Principal, TokenPair]:
        """Rotate a refresh token, with reuse detection.

        Rotation means the presented token is retired the moment it is
        redeemed. If a *retired* token is presented again, either a stale
        session was replayed or a token was stolen — we cannot tell which, so
        the entire family is revoked and everyone re-authenticates. Losing a
        session is a smaller harm than honouring a stolen one.
        """
        record = await self._tokens.get_by_hash(hash_refresh_token(refresh_token))
        if record is None:
            await self._events.record(event="refresh.unknown", ip=ip, user_agent=user_agent)
            await self._uow.commit_now()
            raise AuthenticationError("Refresh token is not recognized.")

        if record.rotated_at is not None:
            revoked = await self._tokens.revoke_family(record.family_id)
            await self._events.record(
                event="refresh.reuse_detected",
                user_id=record.user_id,
                ip=ip,
                user_agent=user_agent,
                detail={"family_id": str(record.family_id), "revoked_tokens": revoked},
            )
            log.warning(
                "auth.refresh_reuse_detected",
                user_id=str(record.user_id),
                family_id=str(record.family_id),
                revoked=revoked,
            )
            await self._uow.commit_now()  # revocation must survive the raise
            raise TokenReuseError

        if not record.is_active():
            raise AuthenticationError("Refresh token has expired or been revoked.")

        user = await self._users.get_by_id(record.user_id)
        if user is None or user.status != "active":
            raise AccountDisabledError

        await self._tokens.mark_rotated(record.id)
        await self._events.record(
            event="refresh.rotated",
            tenant_id=user.tenant_id,
            user_id=user.id,
            ip=ip,
            detail={"generation": record.generation + 1},
        )

        principal = self._principal_for(user)
        pair = await self._issue_pair(
            user, family_id=record.family_id, generation=record.generation + 1
        )
        return principal, pair

    # ── Logout ───────────────────────────────────────────────────────

    async def logout(self, *, refresh_token: str | None, principal: Principal | None) -> None:
        """End the presented session, or every session for the principal.

        Idempotent by design: logging out twice, or with a token the server has
        never seen, is a success. There is nothing to gain from telling a
        caller that their token was already invalid.
        """
        if refresh_token:
            record = await self._tokens.get_by_hash(hash_refresh_token(refresh_token))
            if record is not None:
                await self._tokens.revoke_family(record.family_id)
                await self._events.record(
                    event="logout", user_id=record.user_id, detail={"scope": "family"}
                )
                return
        if principal is not None:
            await self._tokens.revoke_all_for_user(principal.user_id)
            await self._events.record(
                event="logout",
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
                detail={"scope": "all_sessions"},
            )

    async def revoke_all_sessions(self, user_id: uuid.UUID) -> None:
        """Hard logout everywhere: kill refresh families *and* outstanding JWTs.

        Bumping ``token_version`` is what makes already-issued access tokens
        stop working before their 15-minute expiry.
        """
        await self._tokens.revoke_all_for_user(user_id)
        await self._users.bump_token_version(user_id)
        await self._events.record(event="sessions.revoked", user_id=user_id)

    async def get_profile(self, user_id: uuid.UUID) -> tuple[str, str]:
        """``(email, display_name)`` for rendering the signed-in identity.

        A read-only projection so the router never reaches into repositories
        (or, worse, into this service's privates) for a display string.
        """
        user = await self._users.get_by_id(user_id)
        if user is None:
            raise AuthenticationError("Account no longer exists.")
        return user.email, user.display_name

    # ── Principal resolution ─────────────────────────────────────────

    async def resolve_principal(self, claims_principal: Principal) -> Principal:
        """Re-validate a token-derived principal against current database state.

        The JWT is proof of a *past* authentication. Roles may have changed,
        the account may have been disabled, the password may have been reset —
        so the authoritative record is loaded and ``token_version`` compared.
        A mismatch means the token was invalidated after issuance.

        TODO(S3): cache this lookup in Redis with a 60s TTL (Backend §8's
        bounded-staleness contract). Correct first, fast second.
        """
        user = await self._users.get_by_id(claims_principal.user_id)
        if user is None:
            raise AuthenticationError("Account no longer exists.")
        if user.status != "active":
            raise AccountDisabledError
        if user.token_version != claims_principal.token_version:
            raise AuthenticationError(
                "Session is no longer valid.",
                hint="Your permissions or password changed. Sign in again.",
            )
        return self._principal_for(user, jti=claims_principal.jti)

    # ── Internals ────────────────────────────────────────────────────

    def _principal_for(self, user: AppUser, *, jti: str | None = None) -> Principal:
        return Principal.for_user(
            user_id=user.id,
            tenant_id=user.tenant_id,
            email=user.email,
            roles=frozenset(RoleKey(r.key) for r in user.roles),
            token_version=user.token_version,
            jti=jti,
        )

    async def _issue_pair(
        self, user: AppUser, *, family_id: uuid.UUID, generation: int
    ) -> TokenPair:
        access, expires_in, _jti = self._signer.issue_access_token(
            subject=user.id,
            tenant_id=user.tenant_id,
            roles=sorted(r.key for r in user.roles),
            token_version=user.token_version,
        )
        refresh = generate_refresh_token()
        record = await self._tokens.create(
            user_id=user.id,
            token_hash=hash_refresh_token(refresh),
            family_id=family_id,
            generation=generation,
            ttl_days=self._settings.refresh_ttl_days,
        )
        return TokenPair(
            access_token=access,
            access_expires_in=expires_in,
            refresh_token=refresh,
            refresh_expires_at=record.expires_at or datetime.now(tz=UTC),
        )
