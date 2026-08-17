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


# ── Multi-worker JWT key configuration (Prompt 10.5 / P0) ──────────────
#
# Prod runs `RM_API_WORKERS` uvicorn worker processes per container, plus
# multiple replicas. Each process constructs its own TokenSigner
# independently at import time (see app.api.deps), so "multi-worker" and
# "multi-process restart" are the same scenario from the signer's point of
# view: a second TokenSigner built from the same settings must accept what
# the first one signed. A dev key that differs per instance would mean a
# token from worker A gets rejected by worker B — the documented "Known
# issue" in CLAUDE.md for the *unconfigured* dev path — while a properly
# configured key must not have that problem at all.


@pytest.fixture(scope="module")
def _shared_rsa_pem() -> str:
    """One PEM standing in for the secret every worker/replica mounts."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()


def test_worker_b_verifies_a_token_signed_by_worker_a(_shared_rsa_pem: str) -> None:
    """Two independent TokenSigner instances built from the same configured
    key behave as two uvicorn workers sharing one secret file would."""
    worker_a = TokenSigner(AuthSettings(jwt_private_key_pem=_shared_rsa_pem))
    worker_b = TokenSigner(AuthSettings(jwt_private_key_pem=_shared_rsa_pem))

    token, _, _ = worker_a.issue_access_token(
        subject=uuid.uuid4(), tenant_id=uuid.uuid4(), roles=["ceo"], token_version=1
    )

    payload = worker_b.verify_access_token(token)
    assert payload["roles"] == ["ceo"]


def test_restart_does_not_invalidate_tokens_when_key_is_configured(
    _shared_rsa_pem: str,
) -> None:
    """A new process (deploy, restart, autoscale) built from the same
    configured key must still accept tokens minted before it started."""
    before_restart = TokenSigner(AuthSettings(jwt_private_key_pem=_shared_rsa_pem))
    token, _, _ = before_restart.issue_access_token(
        subject=uuid.uuid4(), tenant_id=uuid.uuid4(), roles=["analyst"], token_version=1
    )

    after_restart = TokenSigner(AuthSettings(jwt_private_key_pem=_shared_rsa_pem))
    payload = after_restart.verify_access_token(token)
    assert payload["roles"] == ["analyst"]


def _clear_configured_key(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """Guarantee AuthSettings() actually resolves to "no key configured".

    AuthSettings reads a repo-root ``.env`` (a real, gitignored dev file that
    sets RM_AUTH_JWT_PRIVATE_KEY_FILE) whenever pytest is invoked from the
    repo root — which `make test` does. Without this, these two tests pass
    when run from backend/ but fail when run as part of the full suite,
    because they'd pick up a real configured key instead of exercising the
    unconfigured path they're named for.
    """
    monkeypatch.delenv("RM_AUTH_JWT_PRIVATE_KEY_PEM", raising=False)
    monkeypatch.delenv("RM_AUTH_JWT_PRIVATE_KEY_FILE", raising=False)
    monkeypatch.chdir(tmp_path)


def test_unconfigured_ephemeral_dev_keys_differ_per_worker(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Documents the accepted dev-only limitation (CLAUDE.md "Known issues"):
    without a configured key, each process mints its own ephemeral pair, so
    cross-worker verification legitimately fails. This is what makes the
    configured-key path above load-bearing rather than redundant."""
    _clear_configured_key(monkeypatch, tmp_path)
    worker_a = TokenSigner(AuthSettings())
    worker_b = TokenSigner(AuthSettings())

    token, _, _ = worker_a.issue_access_token(
        subject=uuid.uuid4(), tenant_id=uuid.uuid4(), roles=["ceo"], token_version=1
    )

    with pytest.raises(AuthenticationError):
        worker_b.verify_access_token(token)


def test_missing_production_key_fails_fast_instead_of_generating_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Outside dev, an unconfigured signing key must be a hard startup
    failure, never a silently-generated per-process key — that would let
    every replica mint tokens no other replica can verify."""
    _clear_configured_key(monkeypatch, tmp_path)
    monkeypatch.setenv("RM_APP_ENV", "prod")
    with pytest.raises(RuntimeError, match="RM_AUTH_JWT_PRIVATE_KEY_PEM"):
        TokenSigner(AuthSettings())


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
