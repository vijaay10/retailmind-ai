"""Declarative base, naming conventions, and shared mixins.

Conventions (DB design):
    * constraint names: ``ix_`` / ``uq_`` / ``fk_`` / ``ck_`` prefixes, generated
      by the metadata naming convention so Alembic diffs stay deterministic;
    * UUIDv7 primary keys, generated **database-side** by ``uuid_generate_v7()``
      (created in the genesis migration) so raw-SQL inserts get correct keys too;
    * ``created_at``/``updated_at`` on every business table; ``updated_at`` is
      maintained by a DB trigger (genesis migration) — application code cannot
      forget it;
    * every tenant-scoped table carries an indexed ``tenant_id`` (RESTRICT —
      tenant offboarding is a runbook, not a cascade; DB R1).
"""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, MetaData, text
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column

# Annotation for JSONB payload columns; resolves via type_annotation_map.
JSONDict = dict[str, Any]

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map: dict[Any, Any] = {
        datetime: TIMESTAMP(timezone=True),
        dict: JSONB,  # bare Mapped[dict] — the common annotation in models
        dict[str, Any]: JSONB,
    }


def uuid_pk() -> Mapped[uuid.UUID]:
    """UUIDv7 primary key column (time-ordered → index-friendly, DB)."""
    return mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("uuid_generate_v7()"),
    )


def enum_check(column: str, enum: type[StrEnum]) -> CheckConstraint:
    """CHECK constraint binding a text column to a StrEnum's values (DB)."""
    values = ", ".join(f"'{member.value}'" for member in enum)
    return CheckConstraint(f"{column} IN ({values})", name=f"{column}_valid")


class TimestampMixin:
    """Audit timestamps. ``updated_at`` is trigger-maintained (see genesis migration)."""

    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(server_default=text("now()"))


class TenantScopedMixin:
    """Row scoping for multi-tenancy.

    Repositories inject the tenant predicate structurally (Backend); this
    mixin guarantees the column and its FK exist so that injection is always
    possible. Composite ``(tenant_id, …)`` indexes are declared per-table where
    a hot query needs them (DB–13).
    """

    @declared_attr
    def tenant_id(cls) -> Mapped[uuid.UUID]:  # noqa: N805 — SQLAlchemy declared_attr idiom
        return mapped_column(
            ForeignKey("tenant.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        )
