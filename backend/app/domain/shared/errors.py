"""Domain error hierarchy.

Services raise these; the API layer maps them to RFC 7807 responses in
``app.core.errors``. Routers never construct HTTP errors themselves — that
mapping lives in exactly one registry so status codes cannot drift between
endpoints.
"""


class AppError(Exception):
    """Root of every error the application raises deliberately.

    ``public_message`` is safe to show a user; ``hint`` suggests the fix.
    Anything not derived from AppError is treated as a bug and becomes an
    opaque 500 (details go to logs and Sentry, never to the client).
    """

    public_message = "Something went wrong."
    hint: str | None = None

    def __init__(self, message: str | None = None, *, hint: str | None = None) -> None:
        self.public_message = message or self.public_message
        if hint is not None:
            self.hint = hint
        super().__init__(self.public_message)


class AuthenticationError(AppError):
    """Credentials missing, malformed, expired, or revoked → 401."""

    public_message = "Authentication required."


class InvalidCredentialsError(AuthenticationError):
    """Deliberately indistinguishable from 'unknown account' (Backend).

    Login must not reveal whether an email exists, so both paths raise this.
    """

    public_message = "Email or password is incorrect."


class AccountLockedError(AuthenticationError):
    """Too many failed attempts; locked with exponential backoff (Backend)."""

    public_message = "Account temporarily locked after repeated failed sign-in attempts."
    hint = "Wait for the lockout to expire, or ask an admin to reset your password."


class AccountDisabledError(AuthenticationError):
    public_message = "This account is not active."


class TokenReuseError(AuthenticationError):
    """A rotated refresh token was presented again — theft signal.

    The whole token family is revoked; every session for the user dies.
    """

    public_message = "Session invalidated. Please sign in again."
    hint = "This can happen if a session was restored from an old copy of your credentials."


class AuthorizationError(AppError):
    """Authenticated but not permitted → 403."""

    public_message = "You do not have permission to perform this action."

    def __init__(self, permission: str | None = None) -> None:
        self.permission = permission
        hint = f"Requires the '{permission}' permission." if permission else None
        super().__init__(hint=hint)


class NotFoundError(AppError):
    """Missing — or out of tenant scope.

    Cross-tenant reads raise this, never AuthorizationError: confirming that a
    resource exists elsewhere is itself a leak (Backend policy).
    """

    public_message = "Resource not found."


class ValidationDomainError(AppError):
    """Domain invariant violated (as opposed to a transport-level 422)."""

    public_message = "The request is not valid for the current state."


class ConflictError(AppError):
    """Concurrent modification or duplicate creation → 409."""

    public_message = "The resource has changed since you last read it."


class RateLimitedError(AppError):
    """Too many requests → 429."""

    public_message = "Too many requests."

    def __init__(self, retry_after_seconds: int) -> None:
        self.retry_after_seconds = retry_after_seconds
        super().__init__(hint=f"Retry in {retry_after_seconds} seconds.")


class DependencyError(AppError):
    """A downstream dependency failed (warehouse, cache, LLM, object store).

    ``retryable`` drives the retry/circuit-breaker behaviour in services and
    the presence of a Retry-After header in the response.
    """

    public_message = "A dependency is temporarily unavailable."

    def __init__(self, dependency: str, *, retryable: bool = True) -> None:
        self.dependency = dependency
        self.retryable = retryable
        super().__init__(f"{dependency} is temporarily unavailable.")
