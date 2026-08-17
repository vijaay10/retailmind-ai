"""ETL settings (RM_ETL_* / RM_DB_* / RM_WAREHOUSE_*).

Defaults are dev-safe: a laptop run with no environment at all writes to
``./.local`` and never touches a shared system. Production overrides every
path via the environment (DevOps).
"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class EtlSettings(BaseSettings):
    """Storage layout, batch behaviour, and quality thresholds."""

    model_config = SettingsConfigDict(
        env_prefix="RM_ETL_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Storage ──────────────────────────────────────────────────────
    landing_root: Path = Path(".local/lake")
    """Object-store root. Local filesystem in dev; an S3/MinIO mount in cloud.

    Partition layout mirrors the design exactly (ETL):
        {root}/bronze/{source}/{table}/dt=YYYY-MM-DD/
        {root}/bronze/_rejects/{source}/{table}/dt=YYYY-MM-DD/
    """

    inbox_root: Path = Path(".local/inbox")
    """Where uploaded/dropped CSVs arrive before being landed."""

    warehouse_path: Path = Path(".local/retailmind.duckdb")

    # ── Batch behaviour ──────────────────────────────────────────────
    reject_rate_threshold: float = 0.005
    """Row-level failures are data (rejects). Above this share, the *batch* is
    suspect and quarantines instead — 'row problems are data, batch problems
    are incidents' (ETL)."""

    late_arrival_window_days: int = 35
    """How far back a nightly run reprocesses to absorb late data (ETL)."""

    volume_band_tolerance: float = 0.35
    """Fractional deviation from the trailing median that trips the volume
    check before an adaptive band has enough history (ETL)."""

    max_retries: int = 3
    retry_base_seconds: float = 5.0

    # ── Currency ─────────────────────────────────────────────────────
    base_currency: str = "USD"
    fx_carry_forward_days: int = 3
    """Missing rate tolerance. Beyond this, money math fails loudly rather
    than silently using a stale rate (ETL)."""

    def bronze_dir(self, source: str, table: str, partition: str) -> Path:
        return self.landing_root / "bronze" / source / table / f"dt={partition}"

    def rejects_dir(self, source: str, table: str, partition: str) -> Path:
        return self.landing_root / "bronze" / "_rejects" / source / table / f"dt={partition}"

    def inbox_dir(self, source: str) -> Path:
        return self.inbox_root / source
