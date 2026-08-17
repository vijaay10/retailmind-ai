"""Platform spine: snapshots, registry versions, audit (DB,).

``data_snapshot`` is the reproducibility anchor every AI artifact pins
(DB R10). ``audit_event`` is append-only **by grants** (genesis migration
revokes UPDATE/DELETE) and monthly-partitioned (DB,).
"""

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Index, PrimaryKeyConstraint, text
from sqlalchemy.dialects.postgresql import INET
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.db.models.base import Base, JSONDict, enum_check, uuid_pk
from app.infrastructure.db.models.enums import ActorType


class DataSnapshot(Base):
    """One successful gold publish. String PK matches the warehouse-side id
    (e.g. ``snap_2026-07-21``) so cross-estate reconciliation is a string match
    (DB)."""

    __tablename__ = "data_snapshot"

    id: Mapped[str] = mapped_column(primary_key=True, comment="snap_YYYY-MM-DD[.n]")
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenant.id", ondelete="RESTRICT"), index=True
    )
    dag_run_id: Mapped[str]
    manifest_digest: Mapped[str] = mapped_column(comment="dbt manifest digest at publish")
    mart_row_counts: Mapped[JSONDict] = mapped_column(server_default=text("'{}'::jsonb"))
    published_at: Mapped[datetime]
    superseded_by: Mapped[str | None] = mapped_column(
        ForeignKey("data_snapshot.id", ondelete="RESTRICT"),
        comment="Set on restatement — artifacts show the 'restated' chip (PRD)",
    )


class MetricRegistryVersion(Base):
    """Which code-defined registry contract is live per tenant (DB)."""

    __tablename__ = "metric_registry_version"

    id: Mapped[uuid.UUID] = uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenant.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[str] = mapped_column(comment="Registry semver")
    yaml_digest: Mapped[str]
    activated_at: Mapped[datetime] = mapped_column(server_default=text("now()"))


class AuditEvent(Base):
    """Append-only action ledger — monthly-partitioned by ``at`` (DB).

    INSERT-only is enforced by REVOKE in the genesis migration, not by
    convention. ``prev_hash`` chains rows for tamper evidence on the archive
    path (DB). No FKs: audit must never fail because a referent moved.
    """

    __tablename__ = "audit_event"
    __table_args__ = (
        PrimaryKeyConstraint("id", "at"),
        Index("ix_audit_event_tenant_at", "tenant_id", text("at DESC")),
        Index("ix_audit_event_resource", "tenant_id", "resource_type", "resource_id"),
        enum_check("actor_type", ActorType),
        {"postgresql_partition_by": "RANGE (at)"},
    )

    id: Mapped[uuid.UUID] = mapped_column(server_default=text("uuid_generate_v7()"))
    tenant_id: Mapped[uuid.UUID]
    actor_id: Mapped[uuid.UUID | None]
    actor_type: Mapped[str] = mapped_column(server_default=text("'user'"))
    action: Mapped[str] = mapped_column(comment="Verb phrase: alert.acked, authz.denied, …")
    resource_type: Mapped[str]
    resource_id: Mapped[str] = mapped_column(comment="Text: audit outlives typed referents")
    detail: Mapped[JSONDict] = mapped_column(server_default=text("'{}'::jsonb"))
    ip: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None]
    request_id: Mapped[str | None] = mapped_column(comment="Edge-minted correlation id chain")
    prev_hash: Mapped[str | None]
    at: Mapped[datetime] = mapped_column(server_default=text("now()"))


class AuthEvent(Base):
    """Security-relevant auth happenings, separated for volume (DB).

    Feeds lockout logic (Backend) and security analytics.
    """

    __tablename__ = "auth_event"
    __table_args__ = (
        Index("ix_auth_event_user", "user_id", text("at DESC")),
        Index("ix_auth_event_ip", "ip", text("at DESC")),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(comment="NULL for unresolvable attempts")
    user_id: Mapped[uuid.UUID | None]
    event: Mapped[str] = mapped_column(
        comment="login.success | login.failed | refresh.rotated | refresh.reuse_detected | lockout"
    )
    ip: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None]
    detail: Mapped[JSONDict] = mapped_column(server_default=text("'{}'::jsonb"))
    at: Mapped[datetime] = mapped_column(server_default=text("now()"))
