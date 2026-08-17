"""Expand the role catalog from 3 generic roles to 7 enterprise roles.

Revision ID: 0002_enterprise_roles
Revises: 0001_genesis

The permission model does not change — roles have always resolved to
permission verbs (Backend), which is exactly what makes this migration a
data-and-constraint change rather than an authorization rewrite.

Mapping of existing grants (ids are preserved so ``user_role`` rows need no
rewrite, and nobody loses access mid-deploy):

    id 1  admin    → admin             unchanged
    id 2  analyst  → regional_manager  broad operational access + can act
    id 3  viewer   → ceo               broad read-only visibility

Both replacements are supersets of what the old role granted, so this cannot
silently *remove* someone's access — the safe direction for an automatic
remap. Any tenant needing the narrower functional roles (marketing, inventory,
finance, store_manager) assigns them explicitly afterwards.
"""

# ruff: noqa: S608 — statements are built from the module-level role constants
# below, never from user input; parameter binding is unavailable in Alembic DDL.
from collections.abc import Sequence

from alembic import op

revision: str = "0002_enterprise_roles"
down_revision: str | None = "0001_genesis"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NEW_ROLES = [
    (1, "admin", "Platform administration: users, connectors, budgets, audit"),
    (2, "regional_manager", "Regional performance ownership; acts on alerts and recommendations"),
    (3, "ceo", "Whole-business visibility, self-service analysis, scenario modelling"),
    (4, "store_manager", "Store-level performance and inventory; acts on assigned recommendations"),
    (5, "marketing", "Promotions, campaigns, and customer analytics"),
    (6, "inventory", "Demand planning, replenishment, and supplier performance"),
    (7, "finance", "Margin, profitability, and cost analytics with export rights"),
]

_OLD_ROLES = [
    (1, "admin", "Workspace administration, config, budgets, audit"),
    (2, "analyst", "Query, investigate, act on recommendations"),
    (3, "viewer", "Read dashboards and reports; no export"),
]


def upgrade() -> None:
    # Drop → remap → re-add, and the ordering is not cosmetic.
    #
    # The old and new key sets are *disjoint* for ids 2 and 3
    # ('analyst'/'viewer' become 'regional_manager'/'ceo'), so no ordering
    # works while a constraint is installed: the old one rejects the new
    # values, the new one rejects the old. The rows must move while
    # unconstrained, inside this migration's transaction, where no concurrent
    # writer can observe the gap.
    #
    # Installing the new constraint first passes on an empty database and
    # fails on one that already has rows — that is, never in CI and always in
    # production.
    op.execute("ALTER TABLE role DROP CONSTRAINT IF EXISTS ck_role_key_valid")

    # 1 — remap ids 2 and 3 in place; existing user_role rows follow the id and
    #     keep working without a rewrite.
    for role_id, key, description in _NEW_ROLES[:3]:
        op.execute(
            f"UPDATE role SET key = '{key}', description = '{description}' WHERE id = {role_id}"
        )

    # 2 — add the four new functional roles.
    for role_id, key, description in _NEW_ROLES[3:]:
        op.execute(
            f"""
            INSERT INTO role (id, key, description)
            VALUES ({role_id}, '{key}', '{description}')
            ON CONFLICT (id) DO UPDATE
              SET key = EXCLUDED.key, description = EXCLUDED.description
            """
        )

    # 3 — re-install the constraint now that every row satisfies it.
    op.execute(
        """
        ALTER TABLE role ADD CONSTRAINT ck_role_key_valid
        CHECK (key IN ('admin', 'ceo', 'regional_manager', 'store_manager',
                       'marketing', 'inventory', 'finance'))
        """
    )


def downgrade() -> None:
    """Reverse the catalog. Grants of the four new roles are dropped first —
    they have no equivalent in the 3-role model, and leaving dangling
    ``user_role`` rows would violate the FK."""
    op.execute("DELETE FROM user_role WHERE role_id IN (4, 5, 6, 7)")
    op.execute("DELETE FROM role WHERE id IN (4, 5, 6, 7)")

    # Drop → remap → re-add. The two key sets are disjoint for ids 2 and 3
    # ('analyst'/'viewer' vs 'regional_manager'/'ceo'), so *no* ordering works
    # while a constraint is installed: the old constraint rejects the new
    # values and the new one rejects the old. The rows must move while
    # unconstrained. Safe here because it happens inside the migration's
    # transaction — concurrent writers cannot observe the gap.
    op.execute("ALTER TABLE role DROP CONSTRAINT IF EXISTS ck_role_key_valid")

    for role_id, key, description in _OLD_ROLES:
        op.execute(
            f"UPDATE role SET key = '{key}', description = '{description}' WHERE id = {role_id}"
        )

    op.execute(
        """
        ALTER TABLE role ADD CONSTRAINT ck_role_key_valid
        CHECK (key IN ('admin', 'analyst', 'viewer'))
        """
    )
