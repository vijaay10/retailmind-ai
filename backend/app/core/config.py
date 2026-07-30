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
