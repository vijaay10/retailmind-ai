"""Decision ledger for computed recommendations.

Revision ID: 0003_recommendation_decisions
Revises: 0002_enterprise_roles

The analytical recommendation engine does not persist its proposals — it
recomputes them from the warehouse on every request. That is deliberate: a
stored proposal goes stale the moment the underlying position moves, and an
inbox of stale proposals is worse than none. But it leaves nowhere to record
that a human *acted*, and an action nobody can see afterwards is not a
decision, it is a click.

This table is that record. It cannot be `recommendation_feedback`, which
carries a foreign key to a stored `recommendation` row the engine never
writes.

Identity is a digest of what the action is about — category plus subject —
rather than of its wording, so a reorder whose quantity moves from 122 units
to 130 units is still the same decision rather than a new one. The action
text, the expected profit, and the estimate basis are copied onto the row: the
engine's numbers change daily, and a ledger that redisplayed today's figure
beside yesterday's decision would be rewriting what somebody approved.

Forward-only, additive; no existing table is touched.

**Why this revision checks before it creates.** Genesis materialises the whole
schema with ``Base.metadata.create_all``, so a database built today already
has this table the moment the model exists — and an unguarded ``create_table``
here would fail every fresh install with "relation already exists". This
revision closes the gap for databases that predate the model, and is a no-op
for those that do not. The two paths must agree, and the migration round-trip
test is what proves they do.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_recommendation_decisions"
down_revision: str | None = "0002_enterprise_roles"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "recommendation_decision"


def _exists() -> bool:
    return TABLE in sa.inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    if _exists():
        return

    op.create_table(
        "recommendation_decision",
        sa.Column(
            "id",
            sa.UUID(as_uuid=True),
            server_default=sa.text("uuid_generate_v7()"),
            nullable=False,
        ),
        sa.Column(
            "decision_key",
            sa.String(),
            nullable=False,
            comment="Digest of category + subject — stable while the numbers move",
        ),
        sa.Column("action", sa.String(), nullable=False, comment="accepted | dismissed"),
        sa.Column(
            "category",
            sa.String(),
            nullable=False,
            comment="Engine category, e.g. inventory | pricing",
        ),
        sa.Column(
            "subject",
            sa.String(),
            nullable=False,
            comment="What the action is about, e.g. SKU-1@S2016",
        ),
        sa.Column(
            "action_text",
            sa.String(),
            nullable=False,
            comment="The proposal as worded when decided — snapshot, not a pointer",
        ),
        sa.Column(
            "expected_profit",
            sa.Numeric(14, 2),
            nullable=True,
            comment="Estimate at decision time, for later calibration",
        ),
        sa.Column(
            "estimate_basis",
            sa.String(),
            nullable=True,
            comment="measured | modelled | assumed — what the estimate rested on",
        ),
        sa.Column(
            "reason_code",
            sa.String(),
            nullable=True,
            comment="Enumerated reason, dismissals only",
        ),
        sa.Column("note", sa.String(), nullable=True),
        sa.Column("decided_by", sa.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "decided_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("tenant_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "action IN ('accepted', 'dismissed')",
            name="action",
        ),
        sa.ForeignKeyConstraint(
            ["decided_by"],
            ["app_user.id"],
            name="fk_recommendation_decision_decided_by",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenant.id"],
            name="fk_recommendation_decision_tenant_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_recommendation_decision"),
    )

    # One current decision per subject: re-deciding overwrites rather than
    # appending, so a card can never render as both accepted and dismissed.
    op.create_index(
        "uq_recommendation_decision_key",
        "recommendation_decision",
        ["tenant_id", "decision_key"],
        unique=True,
    )
    op.create_index(
        "ix_recommendation_decision_recent",
        "recommendation_decision",
        ["tenant_id", "decided_at"],
    )
    op.create_index(
        "ix_recommendation_decision_tenant_id",
        "recommendation_decision",
        ["tenant_id"],
    )

    op.execute(
        """
        CREATE TRIGGER trg_recommendation_decision_updated_at
        BEFORE UPDATE ON recommendation_decision
        FOR EACH ROW EXECUTE FUNCTION set_updated_at()
        """
    )


def downgrade() -> None:
    op.drop_table(TABLE)
