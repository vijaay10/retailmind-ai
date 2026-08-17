"""Add tenant company-profile fields — Prompt 12 onboarding.

Revision ID: 0007_tenant_company_profile
Revises: 0006_recommendation_category
Create Date: 2026-08-16 21:00:00

A new company onboarding onto the platform needs somewhere to record basic
profile information (industry, country, timezone, fiscal year start) beyond
the name/slug/currency the tenant table already carried. All four columns
are nullable and additive: no existing row needs a value, and nothing in the
analytics/forecasting/recommendation engines reads them yet — this is
onboarding metadata, not a business-logic input.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_tenant_company_profile"
down_revision: str | None = "0006_recommendation_category"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMNS = ("industry", "country_code", "timezone", "fiscal_year_start_month")


def _existing_columns() -> set[str]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return {col["name"] for col in inspector.get_columns("tenant")}


def upgrade() -> None:
    # Genesis is `Base.metadata.create_all` against the *current* models, so
    # a database built after these fields were added to the model already
    # has them — same reasoning as every prior additive migration in this
    # chain (0003, 0005, 0006). Guard per-column rather than all-or-nothing
    # since a partially-applied prior run is possible in principle.
    present = _existing_columns()

    if "industry" not in present:
        op.add_column(
            "tenant",
            sa.Column(
                "industry", sa.String(), nullable=True, comment="Free-text industry/sub-industry"
            ),
        )
    if "country_code" not in present:
        op.add_column(
            "tenant",
            sa.Column("country_code", sa.String(), nullable=True, comment="ISO 3166-1 alpha-2"),
        )
    if "timezone" not in present:
        op.add_column(
            "tenant",
            sa.Column(
                "timezone",
                sa.String(),
                nullable=True,
                server_default=sa.text("'UTC'"),
                comment="IANA timezone name",
            ),
        )
    if "fiscal_year_start_month" not in present:
        op.add_column(
            "tenant",
            sa.Column(
                "fiscal_year_start_month",
                sa.SmallInteger(),
                nullable=True,
                server_default=sa.text("1"),
                comment="1-12; 1 means the fiscal year matches the calendar year",
            ),
        )


def downgrade() -> None:
    present = _existing_columns()
    for column in _COLUMNS:
        if column in present:
            op.drop_column("tenant", column)
