"""Cryptographic primitives: password hashing, JWT signing, token generation.

Deliberate choices, each with its reason:

* **Argon2id** (m=64MB, t=3, p=4) for passwords — memory-hard, the current
  OWASP recommendation, and the parameters are tuned high enough that a
  stolen hash dump is expensive to attack while login stays ~50ms.
* **RS256** for access tokens — asymmetric signing means verifiers only ever
  hold the public key. Today one process does both; when NLQ or ML serving is
  extracted (ARCH) they verify without the ability to mint.
* **Opaque random refresh tokens**, stored only as SHA-256 digests. They carry
  no claims, so a leaked database reveals nothing and a stolen token cannot be
  decoded or extended.

Refresh digests use plain SHA-256, not Argon2: these are 256-bit random
secrets, not human passwords — there is no dictionary to attack, and lookup
happens on every refresh where a 50ms KDF would be a self-inflicted DoS.
"""

import hashlib
import secrets
import uuid
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import Any, Final

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.core.config import AuthSettings
from app.domain.shared.errors import AuthenticationError

_JWT_ALGORITHM: Final = "RS256"
_REFRESH_TOKEN_BYTES: Final = 32  # 256 bits

# OWASP-aligned Argon2id parameters (see module docstring).
_hasher = PasswordHasher(time_cost=3, memory_cost=64 * 1024, parallelism=4)

# A pre-computed hash of a throwaway value. Verifying against it on the
# unknown-account path keeps login timing indistinguishable from the
# wrong-password path, closing the user-enumeration side channel.
_DUMMY_HASH: Final = _hasher.hash("timing-equalizer")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    """Constant-ish time password check.

    ``password_hash`` may be None (SSO-only identity) — we still burn the same
    CPU against the dummy hash so absence is not observable in the response time.
    """
    if password_hash is None:
        with suppress(VerifyMismatchError, InvalidHashError):
            _hasher.verify(_DUMMY_HASH, password)  # burn equivalent CPU
        return False
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    """True when a stored hash predates the current cost parameters.

    Callers re-hash transparently on a successful login, so tuning the
    parameters upgrades the estate without a password reset.
    """
    return _hasher.check_needs_rehash(password_hash)


def generate_refresh_token() -> str:
    """A 256-bit URL-safe opaque secret."""
    return secrets.token_urlsafe(_REFRESH_TOKEN_BYTES)


def hash_refresh_token(token: str) -> str:
    """Digest for storage/lookup — the plaintext never touches the database."""
    return hashlib.sha256(token.encode()).hexdigest()


def generate_api_key(prefix: str = "rmk_live") -> tuple[str, str, str]:
    """Return ``(full_key, public_prefix, key_hash)`` for a new API key.

    Only the prefix and the hash are persisted; the full key is shown once.
    """
    secret = secrets.token_urlsafe(_REFRESH_TOKEN_BYTES)
    full = f"{prefix}_{secret}"
    return full, f"{prefix}_{secret[:8]}", hashlib.sha256(full.encode()).hexdigest()


class TokenSigner:
    """Issues and verifies access tokens.

    Holds the key material for the process. Constructed once (DI singleton)
    so key parsing happens at startup, not per request.
    """

    def __init__(self, settings: AuthSettings) -> None:
        self._settings = settings
        self._private_key, self._public_key = _load_or_generate_keypair(settings)

    def issue_access_token(
        self,
        *,
        subject: uuid.UUID,
        tenant_id: uuid.UUID,
        roles: list[str],
        token_version: int,
    ) -> tuple[str, int, str]:
        """Mint an access token. Returns ``(token, expires_in_seconds, jti)``.

        Claims carry *roles*, never permissions: the role → permission matrix
        is resolved server-side on every request, so a matrix change applies
        immediately instead of waiting for tokens to expire.
        """
        now = datetime.now(tz=UTC)
        expires_in = self._settings.access_ttl_minutes * 60
        jti = str(uuid.uuid4())
        claims: dict[str, Any] = {
            "sub": str(subject),
            "tenant_id": str(tenant_id),
            "roles": roles,
            "token_version": token_version,
            "jti": jti,
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(seconds=expires_in)).timestamp()),
            "iss": self._settings.jwt_iss,
            "aud": self._settings.jwt_aud,
        }
        token = jwt.encode(claims, self._private_key, algorithm=_JWT_ALGORITHM)
        return token, expires_in, jti

    def verify_access_token(self, token: str) -> dict[str, Any]:
        """Decode and validate a token, or raise AuthenticationError.

        Algorithm is pinned to RS256: accepting the token's own ``alg`` header
        is the classic JWT confusion vulnerability (``none``/HS256 downgrade).
        """
        try:
            payload: dict[str, Any] = jwt.decode(
                token,
                self._public_key,
                algorithms=[_JWT_ALGORITHM],
                issuer=self._settings.jwt_iss,
                audience=self._settings.jwt_aud,
                options={"require": ["exp", "iat", "sub", "iss", "aud"]},
            )
        except jwt.ExpiredSignatureError as exc:
            raise AuthenticationError(
                "Access token has expired.", hint="Refresh the session and retry."
            ) from exc
        except jwt.InvalidTokenError as exc:
            raise AuthenticationError("Access token is not valid.") from exc
        return payload


def _load_or_generate_keypair(settings: AuthSettings) -> tuple[bytes, bytes]:
    """Resolve signing keys from settings, or mint an ephemeral dev pair.

    In non-production, an unconfigured key generates a throwaway RSA-2048 pair
    so ``make up`` works with zero setup; tokens simply do not survive a
    restart. In production this is a hard failure — silently minting keys
    would mean every replica signs with a different key and every deploy
    logs everyone out.
    """
    if settings.jwt_private_key_pem:
        private_pem = settings.jwt_private_key_pem.encode()
        private_key = serialization.load_pem_private_key(private_pem, password=None)
        public_pem = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        return private_pem, public_pem

    if settings.require_configured_keys:
        raise RuntimeError(
            "RM_AUTH_JWT_PRIVATE_KEY_PEM (or _FILE) must be set outside development — "
            "ephemeral keys would differ per replica and invalidate sessions on deploy."
        )

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_pem = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return private_pem, public_pem
