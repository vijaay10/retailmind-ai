"""Sensors for RetailMind data platform.

Sensors react to events and trigger jobs:
- Failure recovery: Retry failed partitions
- Late-arriving data: Re-process when new files appear
- Quality quarantine: Alert on quality gate failures
"""

from dagster import (
    DagsterRunStatus,
    DefaultSensorStatus,
    RunRequest,
    RunsFilter,
    SensorEvaluationContext,
    SensorResult,
    sensor,
)
from dagster._core.definitions.run_request import SkipReason

from .schedules import ingestion_job


@sensor(
    name="failed_partition_retry",
    job=ingestion_job,
    default_status=DefaultSensorStatus.STOPPED,  # Manual enable
    description="Retry failed ingestion partitions after 1 hour",
    minimum_interval_seconds=3600,  # Check hourly
)
def failed_partition_retry_sensor(context: SensorEvaluationContext):
    """Automatically retry failed ingestion partitions.

    Checks for failed ingestion runs in the last 24 hours and
    creates retry requests for their partitions.

    Strategy:
    - Wait 1 hour after failure (transient issues may resolve)
    - Retry up to 3 times per partition
    - Stop retrying after 3 failures (requires manual intervention)
    """
    # Query recent failed runs
    runs_filter = RunsFilter(
        job_name="daily_ingestion",
        statuses=[DagsterRunStatus.FAILURE],
        created_after=context.sensor_runtime - 86400,  # Last 24 hours
    )

    failed_runs = context.instance.get_runs(filters=runs_filter, limit=100)

    if not failed_runs:
        return SkipReason("No failed ingestion runs in last 24 hours")

    # Group by partition and count retries
    partition_retries: dict[str, int] = {}
    retry_requests = []

    for run in failed_runs:
        if not run.tags or "dagster/partition" not in run.tags:
            continue

        partition = run.tags["dagster/partition"]

        # Count previous retries for this partition
        if partition not in partition_retries:
            # Count how many times this partition has failed
            partition_runs = [
                r for r in failed_runs if r.tags.get("dagster/partition") == partition
            ]
            partition_retries[partition] = len(partition_runs)

        # Only retry if < 3 failures
        if partition_retries[partition] < 3:
            retry_requests.append(
                RunRequest(
                    partition_key=partition,
                    run_config={},
                    tags={
                        "retry_attempt": str(partition_retries[partition]),
                        "original_run_id": run.run_id,
                    },
                )
            )
            context.log.info(
                f"Scheduling retry for partition {partition} "
                f"(attempt {partition_retries[partition]}/3)"
            )
        else:
            context.log.warning(f"Partition {partition} has failed 3 times, skipping auto-retry")

    if not retry_requests:
        return SkipReason("All failed partitions already retried 3 times")

    return SensorResult(
        run_requests=retry_requests,
        cursor=str(context.sensor_runtime),
    )


@sensor(
    name="quality_quarantine_alert",
    job=None,  # No job - just logging/alerting
    default_status=DefaultSensorStatus.RUNNING,
    description="Alert on quality gate failures (quarantined partitions)",
    minimum_interval_seconds=3600,  # Check hourly
)
def quality_quarantine_alert_sensor(context: SensorEvaluationContext):
    """Alert when partitions are quarantined due to quality failures.

    Checks for ingestion runs that failed with quality gate errors
    and logs alerts for operator attention.

    In production, this would:
    - Send Slack/email notifications
    - Create Jira tickets
    - Update quarantine dashboard
    """
    # Query recent failed runs
    runs_filter = RunsFilter(
        job_name="daily_ingestion",
        statuses=[DagsterRunStatus.FAILURE],
        created_after=context.sensor_runtime - 3600,  # Last hour
    )

    failed_runs = context.instance.get_runs(filters=runs_filter, limit=50)

    if not failed_runs:
        return SkipReason("No failed ingestion runs in last hour")

    quarantined_partitions = []

    for run in failed_runs:
        # Check if failure was due to quality gate
        # (CLI exits with code 1 for quality failures)
        if run.tags and "quality_failure" in run.tags.get("failure_reason", ""):
            partition = run.tags.get("dagster/partition", "unknown")
            quarantined_partitions.append(
                {
                    "partition": partition,
                    "run_id": run.run_id,
                    "failed_at": run.end_time,
                }
            )

    if not quarantined_partitions:
        return SkipReason("No quality gate failures in last hour")

    # Log alert (in production: send to Slack/PagerDuty)
    for quarantine in quarantined_partitions:
        context.log.error(
            f"QUALITY ALERT: Partition {quarantine['partition']} quarantined "
            f"(run: {quarantine['run_id']})"
        )

    return SkipReason(f"Logged {len(quarantined_partitions)} quality gate failures")


# Note: Late-arriving data sensor would check for new files in inbox
# and trigger re-processing. This requires filesystem monitoring or
# S3 event notifications, which depends on the deployment environment.
# For now, the existing late_arrival_window_days in the CLI handles this.
