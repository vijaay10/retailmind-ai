"""Security primitives and policies: hashing, JWT, lockout, authz guard."""

import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from app.core.config import AuthSettings
from app.core.security import (
    TokenSigner,
    generate_api_key,
    generate_refresh_token,
    hash_password,
    hash_refresh_token,
    verify_password,
)
from app.domain.auth.entities import LockoutPolicy, Principal, RefreshTokenRecord
from app.domain.auth.permissions import Permission, RoleKey
from app.domain.shared.errors import AuthenticationError, AuthorizationError
from app.services.shared import authz


@pytest.fixture(scope="module")
def signer() -> TokenSigner:
    # No key configured → ephemeral dev keypair, which is exactly the path we
    # want covered here.
    return TokenSigner(AuthSettings())


# ── Password hashing ─────────────────────────────────────────────────


def test_password_hash_is_argon2id_and_salted() -> None:
    first, second = hash_password("correct horse battery"), hash_password("correct horse battery")
    assert first.startswith("$argon2id$")
    assert first != second, "identical passwords must not produce identical hashes"


def test_password_verification_round_trip() -> None:
    stored = hash_password("s3cret-password")
    assert verify_password("s3cret-password", stored)
    assert not verify_password("wrong-password", stored)


def test_verify_password_handles_sso_identity_without_hash() -> None:
    """No stored hash must return False, not raise — SSO users have none."""
    assert not verify_password("anything", None)


def test_verify_password_rejects_corrupt_hash() -> None:
    assert not verify_password("anything", "not-a-valid-argon2-hash")


# ── Token generation ─────────────────────────────────────────────────


def test_refresh_tokens_are_unique_and_hashed_for_storage() -> None:
    token = generate_refresh_token()
    assert len(token) >= 32
    assert token != generate_refresh_token()
    digest = hash_refresh_token(token)
    assert digest != token and len(digest) == 64  # sha256 hex
    assert hash_refresh_token(token) == digest  # deterministic lookup key


def test_api_key_exposes_prefix_but_stores_only_a_hash() -> None:
    full, prefix, key_hash = generate_api_key()
    assert full.startswith("rmk_live_")
    assert prefix.startswith("rmk_live_") and len(prefix) < len(full)
    assert key_hash != full and len(key_hash) == 64


# ── JWT ──────────────────────────────────────────────────────────────


def test_access_token_round_trip_carries_expected_claims(signer: TokenSigner) -> None:
    user_id, tenant_id = uuid.uuid4(), uuid.uuid4()
    token, expires_in, jti = signer.issue_access_token(
        subject=user_id, tenant_id=tenant_id, roles=["finance"], token_version=3
    )
    claims = signer.verify_access_token(token)

    assert claims["sub"] == str(user_id)
    assert claims["tenant_id"] == str(tenant_id)
    assert claims["roles"] == ["finance"]
    assert claims["token_version"] == 3
    assert claims["jti"] == jti
    assert expires_in == 15 * 60


def test_token_does_not_carry_permissions(signer: TokenSigner) -> None:
    """Permissions resolve server-side so matrix changes apply immediately."""
    token, _, _ = signer.issue_access_token(
        subject=uuid.uuid4(), tenant_id=uuid.uuid4(), roles=["ceo"], token_version=1
    )
    assert "permissions" not in signer.verify_access_token(token)


def test_expired_token_is_rejected(signer: TokenSigner) -> None:
    settings = AuthSettings(access_ttl_minutes=-1)  # already expired on issue
    expired_signer = TokenSigner(settings)
    token, _, _ = expired_signer.issue_access_token(
        subject=uuid.uuid4(), tenant_id=uuid.uuid4(), roles=["ceo"], token_version=1
    )
    with pytest.raises(AuthenticationError, match="expired"):
        expired_signer.verify_access_token(token)


def test_tampered_token_is_rejected(signer: TokenSigner) -> None:
    token, _, _ = signer.issue_access_token(
        subject=uuid.uuid4(), tenant_id=uuid.uuid4(), roles=["store_manager"], token_version=1
    )
    header, payload, signature = token.split(".")
    forged = f"{header}.{payload}.{signature[:-4]}AAAA"
    with pytest.raises(AuthenticationError):
        signer.verify_access_token(forged)


def test_alg_none_downgrade_is_rejected(signer: TokenSigner) -> None:
    """The classic JWT confusion attack: unsigned token claiming alg=none."""
    forged = jwt.encode({"sub": str(uuid.uuid4())}, key="", algorithm="none")
    with pytest.raises(AuthenticationError):
        signer.verify_access_token(forged)


def test_token_from_another_issuer_is_rejected() -> None:
    """A token minted by a different deployment must not be accepted."""
    foreign = TokenSigner(AuthSettings(jwt_iss="somebody-else"))
    ours = TokenSigner(AuthSettings())
    token, _, _ = foreign.issue_access_token(
        subject=uuid.uuid4(), tenant_id=uuid.uuid4(), roles=["ceo"], token_version=1
    )
    with pytest.raises(AuthenticationError):
        ours.verify_access_token(token)


# ── Lockout policy ───────────────────────────────────────────────────


def test_lockout_does_not_trigger_below_threshold() -> None:
    assert not LockoutPolicy.is_locked(4, datetime.now(tz=UTC))


def test_lockout_triggers_at_threshold_and_backs_off_exponentially() -> None:
    now = datetime.now(tz=UTC)
    assert LockoutPolicy.is_locked(5, now)

    at_threshold = LockoutPolicy.locked_until(5, now)
    one_more = LockoutPolicy.locked_until(6, now)
    assert at_threshold and one_more and one_more > at_threshold


def test_lockout_backoff_is_capped() -> None:
    now = datetime.now(tz=UTC)
    until = LockoutPolicy.locked_until(50, now)
    assert until == now + LockoutPolicy.max_backoff


def test_lockout_expires_after_the_backoff_window() -> None:
    long_ago = datetime.now(tz=UTC) - timedelta(hours=2)
    assert not LockoutPolicy.is_locked(6, long_ago)


# ── Refresh-token record state ───────────────────────────────────────


def _record(**overrides: object) -> RefreshTokenRecord:
    base = {
        "id": uuid.uuid4(),
        "user_id": uuid.uuid4(),
        "family_id": uuid.uuid4(),
        "generation": 1,
        "expires_at": datetime.now(tz=UTC) + timedelta(days=1),
        "rotated_at": None,
        "revoked_at": None,
    }
    return RefreshTokenRecord(**{**base, **overrides})  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({}, True),
        ({"rotated_at": datetime.now(tz=UTC)}, False),
        ({"revoked_at": datetime.now(tz=UTC)}, False),
        ({"expires_at": datetime.now(tz=UTC) - timedelta(seconds=1)}, False),
    ],
    ids=["fresh", "rotated", "revoked", "expired"],
)
def test_refresh_record_activity(overrides: dict[str, object], expected: bool) -> None:
    assert _record(**overrides).is_active() is expected


# ── Authorization guard ──────────────────────────────────────────────


def _principal(*roles: RoleKey) -> Principal:
    return Principal.for_user(
        user_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        email="u@example.test",
        roles=frozenset(roles),
        token_version=1,
    )


def test_require_passes_when_permitted() -> None:
    authz.require(_principal(RoleKey.INVENTORY), Permission.FORECASTS_READ)


def test_require_raises_and_names_the_permission() -> None:
    with pytest.raises(AuthorizationError) as excinfo:
        authz.require(_principal(RoleKey.STORE_MANAGER), Permission.ADMIN_USERS)
    assert excinfo.value.permission == Permission.ADMIN_USERS.value


def test_require_any_accepts_a_partial_match() -> None:
    authz.require_any(
        _principal(RoleKey.FINANCE), Permission.ADMIN_USERS, Permission.METRICS_EXPORT
    )


def test_require_any_rejects_when_none_match() -> None:
    with pytest.raises(AuthorizationError):
        authz.require_any(
            _principal(RoleKey.STORE_MANAGER), Permission.ADMIN_USERS, Permission.ADMIN_BUDGETS
        )


def test_has_is_non_raising() -> None:
    principal = _principal(RoleKey.MARKETING)
    assert authz.has(principal, Permission.ANALYTICS_MARKETING_READ) is True
    assert authz.has(principal, Permission.ADMIN_USERS) is False
