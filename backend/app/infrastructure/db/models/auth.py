"""Tenancy and identity: tenant, users, roles, tokens, API keys (DB §1, §12.2)."""

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, ForeignKey, Index, SmallInteger, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import CITEXT
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.domain.auth.permissions import RoleKey  # single source for the role catalog
from app.infrastructure.db.models.base import (
    Base,
    JSONDict,
    TimestampMixin,
    enum_check,
    uuid_pk,
)
from app.infrastructure.db.models.enums import UserStatus


class Tenant(Base, TimestampMixin):
    """Root of all row scoping (DB §21 R1). Offboarding is a runbook, never a cascade."""

    __tablename__ = "tenant"

    id: Mapped[uuid.UUID] = uuid_pk()
    slug: Mapped[str] = mapped_column(unique=True, comment="URL-safe immutable business key")
    name: Mapped[str]
    plan: Mapped[str] = mapped_column(server_default=text("'standard'"))
    base_currency: Mapped[str] = mapped_column(
        server_default=text("'USD'"),
        comment="ISO-4217; facts store base-currency amounts (ETL §15)",
    )
    llm_budget_tokens_month: Mapped[int] = mapped_column(
        BigInteger,
        server_default=text("5000000"),
        comment="Hard cap enforced via Redis counters, reconciled against llm_usage (Backend §20)",
    )

    users: Mapped[list["AppUser"]] = relationship(back_populates="tenant")


class AppUser(Base, TimestampMixin):
    """Application user. ``user`` is a reserved word — table is app_user (DB §25)."""

    __tablename__ = "app_user"
    __table_args__ = (
        UniqueConstraint("tenant_id", "email"),
        enum_check("status", UserStatus),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenant.id", ondelete="RESTRICT"), index=True
    )
    email: Mapped[str] = mapped_column(CITEXT, comment="Case-insensitive; unique per tenant")
    display_name: Mapped[str]
    password_hash: Mapped[str | None] = mapped_column(
        comment="argon2id; NULL for SSO-only identities"
    )
    status: Mapped[str] = mapped_column(server_default=text("'active'"))
    token_version: Mapped[int] = mapped_column(
        server_default=text("1"),
        comment="Bumped on password/role change to invalidate outstanding JWTs (Backend §8)",
    )
    last_login_at: Mapped[datetime | None]

    tenant: Mapped[Tenant] = relationship(back_populates="users")
    # user_role carries two FKs to app_user (user_id, granted_by) — joins must be
    # explicit. viewonly: grants are written through UserRole rows, never this proxy.
    roles: Mapped[list["Role"]] = relationship(
        secondary="user_role",
        primaryjoin="AppUser.id == UserRole.user_id",
        secondaryjoin="Role.id == UserRole.role_id",
        viewonly=True,
        lazy="selectin",
    )


class Role(Base):
    """Fixed role catalog: admin / analyst / viewer (seeded; DB §34 two-plane model)."""

    __tablename__ = "role"
    __table_args__ = (enum_check("key", RoleKey),)

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, autoincrement=False)
    key: Mapped[str] = mapped_column(unique=True)
    description: Mapped[str]


class UserRole(Base):
    """M:N user↔role. Detail rows are meaningless without the user → CASCADE (DB §21)."""

    __tablename__ = "user_role"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("app_user.id", ondelete="CASCADE"), primary_key=True
    )
    role_id: Mapped[int] = mapped_column(
        ForeignKey("role.id", ondelete="RESTRICT"), primary_key=True
    )
    granted_at: Mapped[datetime] = mapped_column(server_default=text("now()"))
    granted_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("app_user.id", ondelete="SET NULL")
    )


class RefreshToken(Base):
    """Rotating refresh tokens with family-level theft detection (Backend §8).

    Reuse of a rotated (superseded) token revokes the whole ``family_id``.
    """

    __tablename__ = "refresh_token"
    __table_args__ = (Index("ix_refresh_token_family", "user_id", "family_id"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("app_user.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(unique=True, comment="SHA-256 of the opaque token")
    family_id: Mapped[uuid.UUID] = mapped_column(comment="Rotation family (gen chain)")
    generation: Mapped[int]
    expires_at: Mapped[datetime]
    rotated_at: Mapped[datetime | None] = mapped_column(
        comment="Set when superseded; a *used* rotated token = theft signal"
    )
    revoked_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"))


class ApiKey(Base, TimestampMixin):
    """Tenant-scoped programmatic access keys (prefix stored, secret hashed; Backend §8)."""

    __tablename__ = "api_key"

    id: Mapped[uuid.UUID] = uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenant.id", ondelete="RESTRICT"), index=True
    )
    prefix: Mapped[str] = mapped_column(unique=True, comment="rmk_live_xxxx — displayed identifier")
    key_hash: Mapped[str] = mapped_column(comment="SHA-256 of the full key")
    name: Mapped[str]
    scopes: Mapped[JSONDict] = mapped_column(
        server_default=text("'[]'::jsonb"), comment="Permission subset (Backend §9 verbs)"
    )
    last_used_at: Mapped[datetime | None]
    revoked_at: Mapped[datetime | None]
