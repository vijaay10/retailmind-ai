"""Tenant configuration: metric overlays, alert rules, channels, connectors, flags.

The metric *registry* is code (versioned YAML, DB); these tables are the
tenant overlay on it (targets, sensitivity, enablement) — FR-P05 no-code config.
"""

import uuid

from sqlalchemy import ForeignKey, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.db.models.base import (
    Base,
    JSONDict,
    TenantScopedMixin,
    TimestampMixin,
    enum_check,
    uuid_pk,
)
from app.infrastructure.db.models.enums import Detector, Sensitivity, Severity


class MetricConfig(Base, TenantScopedMixin, TimestampMixin):
    __tablename__ = "metric_config"
    __table_args__ = (
        UniqueConstraint("tenant_id", "metric_key"),
        enum_check("sensitivity", Sensitivity),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    metric_key: Mapped[str] = mapped_column(
        comment="FK-by-convention into the code-defined registry; reconciled nightly (DB)"
    )
    display_name: Mapped[str]
    target_value: Mapped[float | None] = mapped_column(comment="Plan/target for vs-plan KPIs")
    sensitivity: Mapped[str] = mapped_column(server_default=text("'med'"))
    enabled: Mapped[bool] = mapped_column(server_default=text("true"))

    alert_rules: Mapped[list["AlertRule"]] = relationship(
        back_populates="metric_config", cascade="all, delete-orphan"
    )


class AlertRule(Base, TimestampMixin):
    """Detector binding for one metric config (CASCADE: meaningless without it, DB R2)."""

    __tablename__ = "alert_rule"
    __table_args__ = (
        enum_check("detector", Detector),
        enum_check("min_severity_notify", Severity),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    metric_config_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("metric_config.id", ondelete="CASCADE"), index=True
    )
    detector: Mapped[str]
    params: Mapped[JSONDict] = mapped_column(
        server_default=text("'{}'::jsonb"),
        comment="Detector parameters (z-threshold, static bounds…); schema owned by alert engine",
    )
    # 'medium' — the closest equivalent under the current 5-tier Severity
    # scale (info/low/medium/high/critical). The old default, 'warn', was a
    # leftover from a 3-tier (info/warn/critical) scale and violated this
    # table's own ck_alert_rule_min_severity_notify_valid constraint on any
    # insert that relied on the column default — never caught before because
    # the seed scripts had never actually been run against a fresh database
    # (found running the Prompt 10.5 migration round-trip test).
    min_severity_notify: Mapped[str] = mapped_column(server_default=text("'medium'"))
    enabled: Mapped[bool] = mapped_column(server_default=text("true"))
    retired_at: Mapped[str | None] = mapped_column(
        comment="Soft retirement — alerts are history; rules are never hard-deleted (DB R3)"
    )

    metric_config: Mapped[MetricConfig] = relationship(back_populates="alert_rules")


class ChannelPref(Base, TimestampMixin):
    """Per-user notification preference matrix (PRD: per-type, never all-or-nothing)."""

    __tablename__ = "channel_pref"
    __table_args__ = (UniqueConstraint("user_id", "channel", "event_type"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("app_user.id", ondelete="CASCADE"), index=True
    )
    channel: Mapped[str] = mapped_column(comment="in_app | email | slack")
    event_type: Mapped[str] = mapped_column(comment="alert.critical | digest.daily | report | rec")
    enabled: Mapped[bool] = mapped_column(server_default=text("true"))


class ConnectorConfig(Base, TenantScopedMixin, TimestampMixin):
    """Source registry entry (ETL). Credentials live in the secrets store, never here."""

    __tablename__ = "connector_config"
    __table_args__ = (UniqueConstraint("tenant_id", "source_key"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    source_key: Mapped[str] = mapped_column(comment="pos | ecom | inventory | promo | weather …")
    display_name: Mapped[str]
    connector_class: Mapped[str] = mapped_column(comment="Dotted path within retailmind-etl")
    schedule: Mapped[str] = mapped_column(comment="Cron expression (tenant-local)")
    params: Mapped[JSONDict] = mapped_column(
        server_default=text("'{}'::jsonb"), comment="Non-secret connector parameters"
    )
    secret_ref: Mapped[str | None] = mapped_column(comment="Name in the secrets store (DevOps)")
    enabled: Mapped[bool] = mapped_column(server_default=text("true"))


class FeatureFlagState(Base, TimestampMixin):
    """Per-tenant feature flags — release-independent kill switches (DevOps)."""

    __tablename__ = "feature_flag_state"
    __table_args__ = (UniqueConstraint("tenant_id", "flag_key"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenant.id", ondelete="CASCADE"), index=True
    )
    flag_key: Mapped[str]
    enabled: Mapped[bool] = mapped_column(server_default=text("false"))
