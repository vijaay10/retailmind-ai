"""Background jobs for detection and delivery.

Each task is a thin shell: it builds the services, runs one async coroutine,
and returns a summary. The logic lives in the service layer so that it can be
tested without a broker, and so that a manual trigger from the API executes
exactly the same code the scheduler does.

**Every task is idempotent.** The sweep decides what to send from what has
already been sent, not from when it last ran, so a retried or duplicated
invocation produces no additional notifications. That is what makes
`task_acks_late` safe: a task killed halfway can be redelivered without anyone
receiving two copies.
"""

import asyncio
from typing import Any

import structlog

from app.workers.celery_app import celery_app

log = structlog.get_logger(__name__)

#: Retry policy for delivery. Exponential with a ceiling: a mail server that
#: is down stays down for minutes, and hammering it makes the outage longer
#: for everyone else on the relay.
MAX_RETRIES = 5
RETRY_BACKOFF = 60


@celery_app.task(name="notifications.sweep", bind=True, max_retries=2)
def sweep(self: Any, tenant_id: str | None = None) -> dict[str, Any]:
    """Run every detector and deliver what survives suppression."""
    from app.workers.runtime import run_sweep

    try:
        result = asyncio.run(run_sweep(tenant_id))
    except Exception as error:  # noqa: BLE001 — retried, then surfaced
        log.error("notifications.sweep_failed", error=str(error))
        raise self.retry(exc=error, countdown=RETRY_BACKOFF) from error

    log.info("notifications.sweep_task", **result)
    return result


@celery_app.task(name="notifications.digest", bind=True, max_retries=2)
def digest(self: Any, tenant_id: str | None = None) -> dict[str, Any]:
    """Send the daily summary.

    Deliberately a separate task from the sweep rather than a flag on it. A
    digest that shares the sweep's suppression state would be silenced by it —
    everything in a digest has, by definition, already been notified.
    """
    from app.workers.runtime import run_digest

    try:
        return asyncio.run(run_digest(tenant_id))
    except Exception as error:  # noqa: BLE001
        raise self.retry(exc=error, countdown=RETRY_BACKOFF) from error


@celery_app.task(name="notifications.retry_failed", bind=True)
def retry_failed(self: Any, tenant_id: str | None = None) -> dict[str, Any]:
    """Re-attempt deliveries the ledger records as failed.

    Bounded by the attempt trail on each row rather than by task retries: a
    notification that has failed five times is a bad address or a dead
    integration, and retrying it forever hides that behind a busy queue.
    """
    from app.workers.runtime import run_delivery_retry

    return asyncio.run(run_delivery_retry(tenant_id, max_attempts=MAX_RETRIES))
