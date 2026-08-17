"""Add tenant.warehouse_path — Prompt 12.5 warehouse isolation.

Revision ID: 0008_tenant_warehouse_isolation
Revises: 0007_tenant_company_profile
Create Date: 2026-08-17 09:00:00

Prompt 12 shipped tenant creation, onboarding, and OLTP-level isolation, but
left one real gap: every tenant's analytics read the same single shared
DuckDB file, because `SemanticLayerClient` was constructed once at process
startup from one global `RM_WAREHOUSE_DUCKDB_PATH`. This migration adds the
one column the fix needs: `warehouse_path`, nullable. NULL resolves to the
per-tenant convention (`{RM_WAREHOUSE_ROOT}/{tenant.slug}.duckdb` — see
`app.infrastructure.semantic.tenancy`); a non-NULL value overrides it.

The demo tenant (`northwind-threads`) predates this column and its real
warehouse data already lives wherever `RM_WAREHOUSE_DUCKDB_PATH` points in
this environment — not at a `northwind-threads.duckdb` file. Rather than
require every environment (local dev, `make demo`, prod) to rename or
re-mount that file, this migration backfills the demo tenant's
`warehouse_path` with the *current* `RM_WAREHOUSE_DUCKDB_PATH` value, read
from `WarehouseSettings()` at migration-run time — correct in whichever
environment the migration actually runs in, never a hardcoded path.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_tenant_warehouse_isolation"
down_revision: str | None = "0007_tenant_company_profile"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DEMO_TENANT_SLUG = "northwind-threads"


def _column_exists() -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = [col["name"] for col in inspector.get_columns("tenant")]
    return "warehouse_path" in columns


def upgrade() -> None:
    if _column_exists():
        return

    op.add_column(
        "tenant",
        sa.Column(
            "warehouse_path",
            sa.String(),
            nullable=True,
            comment="Explicit DuckDB file path override; NULL uses the per-slug convention",
        ),
    )

    # Read lazily so importing this module never requires backend settings
    # to be configured (e.g. when alembic just autogenerates against a bare
    # metadata diff) — only `upgrade()` actually needs them.
    from app.core.config import WarehouseSettings

    demo_path = WarehouseSettings().duckdb_path
    op.execute(
        sa.text(
            "UPDATE tenant SET warehouse_path = :path WHERE slug = :slug AND warehouse_path IS NULL"
        ).bindparams(path=demo_path, slug=_DEMO_TENANT_SLUG)
    )


def downgrade() -> None:
    if _column_exists():
        op.drop_column("tenant", "warehouse_path")
