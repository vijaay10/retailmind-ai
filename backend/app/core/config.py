"""Settings — the RM_* environment contract (.env.example is the reference doc).

Scaffold scope: enough to boot. The full layered model (per-area nested
settings, *_FILE secret variants, fail-fast missing-settings table) lands in S1
per Backend design §16 / DevOps design §3.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


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
    pool_size: int = 10
    sslmode: str = "disable"  # TODO(S8): map to asyncpg ssl context for the cloud profile

    @property
    def async_dsn(self) -> str:
        """asyncpg DSN for the runtime engine and Alembic's async env."""
        return (
            f"postgresql+asyncpg://{self.user}:{self.password}@{self.host}:{self.port}/{self.name}"
        )
