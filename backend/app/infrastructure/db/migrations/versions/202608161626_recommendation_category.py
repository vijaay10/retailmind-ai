"""Add recommendation.category — the calibration API's generator filter.

Revision ID: 0006_recommendation_category
Revises: 0005_llm_usage_tracking
Create Date: 2026-08-16 16:26:00

Prompt 11 found, and Prompt 11.5 root-caused, why the calibration API's
per-generator filtering could never work for any real caller:
`OutcomeRepository.find_measured()` filtered on `recommendation.type`, which
is constrained to `reorder | markdown | promo | assortment` — the *kind of
action* a batch-engine-written row represents. The API's own docs describe
"generator" as `inventory | pricing | promotion | store | marketing |
customer | supplier` — the live analytical engine's `Category`, a disjoint
vocabulary that was never persisted anywhere on this table.

This migration adds the missing column. Nullable and additive: existing
`recommendation` rows (if any) get `category = NULL`, which the fixed
repository query correctly treats as "doesn't match any generator filter"
rather than an error — no existing behavior changes for callers that don't
filter by generator.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_recommendation_category"
down_revision: str | None = "0005_llm_usage_tracking"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CATEGORIES = ("inventory", "pricing", "promotion", "store", "marketing", "customer", "supplier")


def _column_exists() -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = [col["name"] for col in inspector.get_columns("recommendation")]
    return "category" in columns


def upgrade() -> None:
    # Same reasoning as 202608061347_recommendation_decisions.py and
    # 202608131800_llm_usage_tracking.py: genesis is `Base.metadata.create_all`
    # against the *current* models, so a database built after `category` was
    # added to the model already has the column — and its check constraint,
    # under the declarative naming convention (`ck_recommendation_category_valid`,
    # not the unprefixed name `create_check_constraint` would otherwise try to
    # add). Adding either unconditionally fails on a fresh install with
    # "already exists" — confirmed by actually running this migration against
    # a disposable database before trusting it.
    if _column_exists():
        return

    op.add_column(
        "recommendation",
        sa.Column(
            "category",
            sa.String(),
            nullable=True,
            comment=(
                "inventory | pricing | promotion | store | marketing | "
                "customer | supplier — which generator produced this "
                "recommendation."
            ),
        ),
    )
    categories_sql = ", ".join(f"'{value}'" for value in _CATEGORIES)
    op.create_check_constraint(
        "category_valid",
        "recommendation",
        f"category IN ({categories_sql})",
    )


def downgrade() -> None:
    op.drop_constraint("category_valid", "recommendation", type_="check")
    if _column_exists():
        op.drop_column("recommendation", "category")
