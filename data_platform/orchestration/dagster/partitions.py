"""Dagster partition definitions for RetailMind data platform.

All partitions are business-date based (not wall-clock time) to maintain
idempotency and align with the existing Window-based pipeline design.
"""

from dagster import DailyPartitionsDefinition

# Daily partition for ingestion and dbt runs
# Start date: 2026-06-01 (aligns with demo data)
# Timezone: UTC (business dates are calendar dates, not time-zone aware)
daily_partition = DailyPartitionsDefinition(
    start_date="2026-06-01",
    timezone="UTC",
    fmt="%Y-%m-%d",
    end_offset=0,  # Today is the last partition available
)

# Weekly partition for aggregated reporting
# Used for weekly rollups and outcome measurement
weekly_partition = DailyPartitionsDefinition(
    start_date="2026-06-01",
    timezone="UTC",
    fmt="%Y-%m-%d",
    end_offset=0,
    cron_schedule="0 0 * * 1",  # Monday at midnight
)
