"""Settings — the RM_* environment contract (.env.example is the reference doc).

Scaffold scope: enough to boot. The full layered model (per-area nested
settings, *_FILE secret variants, fail-fast missing-settings table) lands in S1
per Backend design §16 / DevOps design §3.
"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


def read_secret(path: str) -> str:
    """Read a mounted secret.

    Trailing whitespace is stripped, and that detail is load-bearing rather
    than tidiness. Docker's own ``file_env`` helper reads ``*_FILE`` secrets
    with ``$(< file)``, which drops trailing newlines — so a secret file
    written with a final newline gives Postgres a password without it and this
    application a password with it. The two disagree, authentication fails,
    and the error says nothing about a newline.
    """
    return Path(path).read_text().rstrip("\r\n")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="RM_APP_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    env: str = "dev"
    log_level: str = "INFO"
    base_url: str = "http://localhost:8080"
    version: str = "local"


class AuthSettings(BaseSettings):
    """RM_AUTH_* — token lifetimes, signing keys, cookie behaviour.

    Secrets follow the ``*_FILE`` convention (DevOps §4): the platform mounts
    the key and we read it, so the value never appears in process env or logs.
    """

    model_config = SettingsConfigDict(
        env_prefix="RM_AUTH_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    jwt_iss: str = "retailmind"
    jwt_aud: str = "retailmind-app"
    access_ttl_minutes: int = 15
    refresh_ttl_days: int = 14

    jwt_private_key_pem: str | None = None
    jwt_private_key_file: str | None = None

    # Refresh cookie: httpOnly + SameSite=Strict always; Secure everywhere but
    # local http development, where the browser would otherwise drop it.
    refresh_cookie_name: str = "rm_refresh"
    refresh_cookie_path: str = "/api/v1/auth"
    # None → derived from the environment in model_post_init. Browsers refuse
    # to send Secure cookies over plain http, so local development would have
    # no working session at all if this were unconditionally True.
    cookie_secure: bool | None = None

    @property
    def require_configured_keys(self) -> bool:
        """Ephemeral dev keys are tolerated only outside staging/production."""
        return Settings().env in {"staging", "prod"}

    def model_post_init(self, __context: object) -> None:
        """Resolve ``*_FILE`` indirection and environment-derived defaults."""
        if self.jwt_private_key_pem is None and self.jwt_private_key_file:
            self.jwt_private_key_pem = read_secret(self.jwt_private_key_file)
        if self.cookie_secure is None:
            # Secure everywhere the app is actually served over TLS; explicit
            # RM_AUTH_COOKIE_SECURE always wins over this default.
            self.cookie_secure = Settings().env != "dev"


class DatabaseSettings(BaseSettings):
    """RM_DB_* — the app OLTP database (defaults are dev-safe, DevOps §3)."""

    model_config = SettingsConfigDict(
        env_prefix="RM_DB_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    host: str = "localhost"
    port: int = 5432
    name: str = "retailmind_app"
    user: str = "api_rw"
    password: str = "dev-only-password"  # noqa: S105 — dev default; prod injects via secrets
    #: RM_DB_PASSWORD_FILE — the production form. The overlay mounts the
    #: secret and sets only this; without the field below pydantic's
    #: ``extra="ignore"`` swallows it and the app quietly falls back to the
    #: dev password above, which fails authentication against a database that
    #: was initialised from the same secret file.
    password_file: str | None = None
    pool_size: int = 10
    sslmode: str = "disable"  # TODO(S8): map to asyncpg ssl context for the cloud profile

    def model_post_init(self, __context: object) -> None:
        """Resolve ``*_FILE`` indirection."""
        if self.password_file:
            self.password = read_secret(self.password_file)

    @property
    def async_dsn(self) -> str:
        """asyncpg DSN for the runtime engine and Alembic's async env."""
        return (
            f"postgresql+asyncpg://{self.user}:{self.password}@{self.host}:{self.port}/{self.name}"
        )


class WarehouseSettings(BaseSettings):
    """RM_WAREHOUSE_* — the analytical store the semantic layer reads."""

    model_config = SettingsConfigDict(
        env_prefix="RM_WAREHOUSE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    profile: str = "duckdb"
    duckdb_path: str = ".local/retailmind.duckdb"
    semantic_schema: str = "analytics_semantic"
    core_schema: str = "analytics_analytics"


class CacheSettings(BaseSettings):
    """RM_REDIS_* — the analytics result cache.

    An unset URL disables caching rather than failing: reads pass through to
    the warehouse and the product stays up, slower (Backend §20).
    """

    model_config = SettingsConfigDict(
        env_prefix="RM_REDIS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    cache_url: str | None = None
    ttl_seconds: int = 86_400
