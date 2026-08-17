"""Enhance recommendation outcomes for measurement lifecycle tracking.

Revision ID: 0004_outcome_measurement
Revises: 0003_recommendation_decisions

The RecommendationOutcome table exists from genesis but was never populated.
This migration adds the fields needed to track the measurement lifecycle:

- Status tracking (pending → measuring → measured/failed/insufficient_data)
- Baseline calculation method and window
- Measurement confidence and limitations
- Realized impact metrics
- Error tracking for failed measurements

The outcome measurement job uses these fields to track which decisions need
measurement, what has been measured, and how confident each measurement is.

Backward-compatible: adds columns with sensible defaults, existing rows
(if any) become status=null and are skipped by the measurement job.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_outcome_measurement"
down_revision: str | None = "0003_recommendation_decisions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _column_exists(table: str, column: str) -> bool:
    """Check if a column exists in the table."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = [col["name"] for col in inspector.get_columns(table)]
    return column in columns


def upgrade() -> None:
    """Add outcome measurement lifecycle tracking fields."""

    # Add status field to track measurement lifecycle
    if not _column_exists("recommendation_outcome", "status"):
        op.add_column(
            "recommendation_outcome",
            sa.Column(
                "status",
                sa.String(),
                nullable=True,  # Existing rows stay null (skipped by measurement job)
                comment=(
                    "pending | measuring | measured | failed | insufficient_data — "
                    "measurement lifecycle status"
                ),
            ),
        )

    # Add baseline calculation fields
    if not _column_exists("recommendation_outcome", "baseline_method"):
        op.add_column(
            "recommendation_outcome",
            sa.Column(
                "baseline_method",
                sa.String(),
                nullable=True,
                comment=(
                    "comparable_period | pre_decision | peer_baseline | forecast_baseline — "
                    "how the counterfactual was calculated"
                ),
            ),
        )

    if not _column_exists("recommendation_outcome", "baseline_window_start"):
        op.add_column(
            "recommendation_outcome",
            sa.Column(
                "baseline_window_start",
                sa.Date(),
                nullable=True,
                comment="Start of baseline observation period",
            ),
        )

    if not _column_exists("recommendation_outcome", "baseline_window_end"):
        op.add_column(
            "recommendation_outcome",
            sa.Column(
                "baseline_window_end",
                sa.Date(),
                nullable=True,
                comment="End of baseline observation period",
            ),
        )

    if not _column_exists("recommendation_outcome", "observation_window_start"):
        op.add_column(
            "recommendation_outcome",
            sa.Column(
                "observation_window_start",
                sa.Date(),
                nullable=True,
                comment="Start of post-decision measurement period",
            ),
        )

    if not _column_exists("recommendation_outcome", "observation_window_end"):
        op.add_column(
            "recommendation_outcome",
            sa.Column(
                "observation_window_end",
                sa.Date(),
                nullable=True,
                comment="End of post-decision measurement period",
            ),
        )

    # Add realized impact fields
    if not _column_exists("recommendation_outcome", "baseline_value"):
        op.add_column(
            "recommendation_outcome",
            sa.Column(
                "baseline_value",
                sa.Numeric(14, 2),
                nullable=True,
                comment="Baseline metric value (what would have happened without intervention)",
            ),
        )

    if not _column_exists("recommendation_outcome", "observed_value"):
        op.add_column(
            "recommendation_outcome",
            sa.Column(
                "observed_value",
                sa.Numeric(14, 2),
                nullable=True,
                comment="Observed metric value in the measurement window",
            ),
        )

    if not _column_exists("recommendation_outcome", "realized_impact"):
        op.add_column(
            "recommendation_outcome",
            sa.Column(
                "realized_impact",
                sa.Numeric(14, 2),
                nullable=True,
                comment="Observed minus baseline — the measured effect",
            ),
        )

    if not _column_exists("recommendation_outcome", "expected_impact"):
        op.add_column(
            "recommendation_outcome",
            sa.Column(
                "expected_impact",
                sa.Numeric(14, 2),
                nullable=True,
                comment="Expected impact from the recommendation (snapshot at decision time)",
            ),
        )

    if not _column_exists("recommendation_outcome", "absolute_error"):
        op.add_column(
            "recommendation_outcome",
            sa.Column(
                "absolute_error",
                sa.Numeric(14, 2),
                nullable=True,
                comment="abs(realized - expected) — forecast accuracy metric",
            ),
        )

    if not _column_exists("recommendation_outcome", "realization_ratio"):
        op.add_column(
            "recommendation_outcome",
            sa.Column(
                "realization_ratio",
                sa.Numeric(8, 4),
                nullable=True,
                comment="realized / expected — calibration metric, 1.0 = perfect",
            ),
        )

    if not _column_exists("recommendation_outcome", "direction_correct"):
        op.add_column(
            "recommendation_outcome",
            sa.Column(
                "direction_correct",
                sa.Boolean(),
                nullable=True,
                comment="Did the outcome move in the expected direction?",
            ),
        )

    # Add measurement confidence and limitations
    if not _column_exists("recommendation_outcome", "measurement_confidence"):
        op.add_column(
            "recommendation_outcome",
            sa.Column(
                "measurement_confidence",
                sa.String(),
                nullable=True,
                comment="low | medium | high — how confident we are in this measurement",
            ),
        )

    if not _column_exists("recommendation_outcome", "limitations"):
        op.add_column(
            "recommendation_outcome",
            sa.Column(
                "limitations",
                sa.Text(),
                nullable=True,
                comment=(
                    "Known measurement limitations, e.g., 'short observation window', "
                    "'partial data', 'confounding event detected'"
                ),
            ),
        )

    # Add error tracking for failed measurements
    if not _column_exists("recommendation_outcome", "error_message"):
        op.add_column(
            "recommendation_outcome",
            sa.Column(
                "error_message",
                sa.Text(),
                nullable=True,
                comment="Error message if status=failed",
            ),
        )

    if not _column_exists("recommendation_outcome", "measurement_attempts"):
        op.add_column(
            "recommendation_outcome",
            sa.Column(
                "measurement_attempts",
                sa.Integer(),
                server_default="0",
                nullable=False,
                comment="Number of measurement attempts (for retry logic)",
            ),
        )

    if not _column_exists("recommendation_outcome", "last_attempt_at"):
        op.add_column(
            "recommendation_outcome",
            sa.Column(
                "last_attempt_at",
                sa.TIMESTAMP(timezone=True),
                nullable=True,
                comment="When measurement was last attempted",
            ),
        )

    # Add check constraints for enums (drop and recreate to handle if-exists).
    #
    # `IF EXISTS` via raw SQL, not try/except around op.drop_constraint: a
    # missing constraint raises a Postgres error that aborts the whole
    # migration transaction, and catching the Python exception does not
    # un-abort it — every statement after the except block would then fail
    # with "current transaction is aborted", which is exactly what happened
    # against a fresh database (this migration had never actually been run
    # end to end before Prompt 10.5). Same convention as
    # 202607301500_enterprise_roles.py.
    op.execute("ALTER TABLE recommendation_outcome DROP CONSTRAINT IF EXISTS outcome_status")
    op.create_check_constraint(
        "outcome_status",
        "recommendation_outcome",
        "status IS NULL OR status IN "
        "('pending', 'measuring', 'measured', 'failed', 'insufficient_data')",
    )

    op.execute(
        "ALTER TABLE recommendation_outcome DROP CONSTRAINT IF EXISTS outcome_baseline_method"
    )
    op.create_check_constraint(
        "outcome_baseline_method",
        "recommendation_outcome",
        "baseline_method IS NULL OR baseline_method IN "
        "('comparable_period', 'pre_decision', 'peer_baseline', 'forecast_baseline')",
    )

    op.execute(
        "ALTER TABLE recommendation_outcome "
        "DROP CONSTRAINT IF EXISTS outcome_measurement_confidence"
    )
    op.create_check_constraint(
        "outcome_measurement_confidence",
        "recommendation_outcome",
        "measurement_confidence IS NULL OR measurement_confidence IN ('low', 'medium', 'high')",
    )

    # Add index for finding pending measurements. Same transaction-safety
    # reasoning as above: IF NOT EXISTS instead of try/except.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_outcome_pending_measurement "
        "ON recommendation_outcome (status, observation_window_end) "
        "WHERE status = 'pending'"
    )


def downgrade() -> None:
    """Remove outcome measurement fields."""
    op.drop_index("ix_outcome_pending_measurement", table_name="recommendation_outcome")
    op.drop_constraint("outcome_measurement_confidence", "recommendation_outcome", type_="check")
    op.drop_constraint("outcome_baseline_method", "recommendation_outcome", type_="check")
    op.drop_constraint("outcome_status", "recommendation_outcome", type_="check")

    columns_to_drop = [
        "status",
        "baseline_method",
        "baseline_window_start",
        "baseline_window_end",
        "observation_window_start",
        "observation_window_end",
        "baseline_value",
        "observed_value",
        "realized_impact",
        "expected_impact",
        "absolute_error",
        "realization_ratio",
        "direction_correct",
        "measurement_confidence",
        "limitations",
        "error_message",
        "measurement_attempts",
        "last_attempt_at",
    ]

    for column in columns_to_drop:
        if _column_exists("recommendation_outcome", column):
            op.drop_column("recommendation_outcome", column)
