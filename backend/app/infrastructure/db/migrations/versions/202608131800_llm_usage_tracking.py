"""LLM usage tracking.

Create table for tracking all LLM API calls: request metadata, token usage,
cost accounting, and error logging.

Revision ID: 0005_llm_usage_tracking
Revises: 0004_outcome_measurement
Create Date: 2026-08-13 18:00:00

**Why this revision checks before it creates.** Same reason as
202608061347_recommendation_decisions.py: genesis materialises the whole
schema with ``Base.metadata.create_all``, so a database built today already
has ``llm_request_log`` (and its indexes) the moment the model exists. An
unguarded ``create_table`` here fails every fresh install with "relation
already exists" — confirmed by actually running upgrade base -> head against
a disposable Postgres during Prompt 10.5, which this migration had never
been through before.
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic
revision = "0005_llm_usage_tracking"
down_revision = "0004_outcome_measurement"
branch_labels = None
depends_on = None

TABLE = "llm_request_log"


def _exists() -> bool:
    return TABLE in sa.inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    """Create llm_request_log table for detailed request tracking and audit trail."""
    if _exists():
        return

    op.create_table(
        "llm_request_log",
        sa.Column(
            "id",
            sa.UUID(),
            server_default=sa.text("uuid_generate_v7()"),
            nullable=False,
        ),
        sa.Column(
            "request_id",
            sa.String(),
            nullable=False,
            comment="Unique request identifier (UUID)",
        ),
        sa.Column(
            "tenant_id",
            sa.UUID(),
            nullable=False,
            comment="Which tenant made this request",
        ),
        sa.Column(
            "user_id",
            sa.UUID(),
            nullable=True,
            comment="Which user initiated the request (null for system)",
        ),
        sa.Column(
            "model_id",
            sa.String(),
            nullable=False,
            comment="Model used (e.g., claude-sonnet-4-5-20250929)",
        ),
        sa.Column(
            "prompt_version",
            sa.String(),
            nullable=False,
            comment="Version of prompt template used",
        ),
        sa.Column(
            "tokens_in",
            sa.Integer(),
            nullable=False,
            comment="Input tokens consumed",
        ),
        sa.Column(
            "tokens_out",
            sa.Integer(),
            nullable=False,
            comment="Output tokens generated",
        ),
        sa.Column(
            "estimated_cost_usd",
            sa.Numeric(10, 6),
            nullable=False,
            comment="Estimated cost in USD",
        ),
        sa.Column(
            "latency_ms",
            sa.Integer(),
            nullable=False,
            comment="Response latency in milliseconds",
        ),
        sa.Column(
            "status",
            sa.String(),
            nullable=False,
            comment="success | error | timeout | rate_limited",
        ),
        sa.Column(
            "error",
            sa.Text(),
            nullable=True,
            comment="Error message if status != success",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="When this request was made",
        ),
        sa.PrimaryKeyConstraint("id"),
        comment="Detailed LLM request log for audit trail and debugging",
    )

    # Indexes for common queries
    op.create_index(
        "ix_llm_request_log_tenant_created",
        "llm_request_log",
        ["tenant_id", "created_at"],
        comment="Query usage by tenant over time",
    )
    op.create_index(
        "ix_llm_request_log_request_id",
        "llm_request_log",
        ["request_id"],
        unique=True,
        comment="Look up by request ID",
    )
    op.create_index(
        "ix_llm_request_log_status",
        "llm_request_log",
        ["status", "created_at"],
        comment="Find errors and failures",
    )


def downgrade() -> None:
    """Drop llm_request_log table."""
    op.drop_index("ix_llm_request_log_status", table_name="llm_request_log")
    op.drop_index("ix_llm_request_log_request_id", table_name="llm_request_log")
    op.drop_index("ix_llm_request_log_tenant_created", table_name="llm_request_log")
    op.drop_table("llm_request_log")
