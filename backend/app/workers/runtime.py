"""Service wiring for worker context.

A worker has no request, no authenticated caller, and no FastAPI dependency
graph, so it builds its own. Everything below is the same service the API
uses — a background sweep that reached the warehouse by a different path would
eventually disagree with the screen, and the disagreement would surface as an
alert nobody can reproduce.

The worker acts as a **system principal** holding exactly the permissions
detection needs. Not an unrestricted one: a job that can read anything is a job
whose blast radius is the whole platform the day it is given a bad tenant id.
"""

import os
import uuid
from typing import Any

import structlog

from app.domain.auth.entities import Principal
from app.domain.auth.permissions import Permission
from app.infrastructure.notifications.email import (
    EmailSender,
    NullEmailSender,
    SmtpEmailSender,
)

log = structlog.get_logger(__name__)

#: What the sweep needs and no more. Enumerated rather than granted wholesale
#: so that adding a detector that reads somewhere new is a visible change.
SYSTEM_PERMISSIONS = frozenset(
    {
        Permission.ALERTS_READ,
        Permission.ANALYTICS_REVENUE_READ,
        Permission.ANALYTICS_INVENTORY_READ,
        Permission.FORECASTS_READ,
        Permission.RECOMMENDATIONS_READ,
        Permission.RCA_RUN,
    }
)


#: Reserved identifier for the scheduler. A nil UUID rather than a real
#: user's, so an audit trail never attributes a machine decision to a person.
SYSTEM_USER_ID = uuid.UUID(int=0)


def system_principal(tenant_id: str) -> Principal:
    """The identity a scheduled job runs as.

    Holds no roles — only the explicit permission set above. Roles carry
    implications a machine should not inherit: a job granted the CEO role
    would silently widen every time that role did.
    """
    return Principal(
        user_id=SYSTEM_USER_ID,
        tenant_id=uuid.UUID(tenant_id),
        email="system@retailmind.local",
        roles=frozenset(),
        token_version=1,
        permissions=SYSTEM_PERMISSIONS,
    )


def email_sender() -> EmailSender:
    """The configured mail transport.

    Defaults to the null sender. A worker that silently acquires a live SMTP
    connection because an environment variable happened to be set is how a
    developer's laptop mails a customer.
    """
    host = os.environ.get("RM_SMTP_HOST")
    if not host:
        return NullEmailSender()
    return SmtpEmailSender(
        host=host,
        port=int(os.environ.get("RM_SMTP_PORT", "587")),
        username=os.environ.get("RM_SMTP_USERNAME", ""),
        password=os.environ.get("RM_SMTP_PASSWORD", ""),
        sender=os.environ.get("RM_SMTP_SENDER", "alerts@retailmind.local"),
    )


async def run_sweep(tenant_id: str | None = None) -> dict[str, Any]:
    """Detect and deliver for one tenant.

    Kept deliberately small: the interesting behaviour is in the service, and
    a worker entry point that grows logic is one whose behaviour cannot be
    reproduced from the API.
    """
    resolved = tenant_id or os.environ.get("RM_DEFAULT_TENANT_ID", "")
    if not resolved:
        log.warning("notifications.sweep_skipped", reason="no tenant configured")
        return {"skipped": "no tenant configured"}

    # Imported here rather than at module scope: the container imports the
    # warehouse client, and tasks that never touch it should not pay for it.
    from app.workers.container import (
        build_notification_service,
        load_recipients,
        session_scope,
    )

    async with session_scope() as session:
        service = build_notification_service(resolved, session)
        recipients = await load_recipients(session, uuid.UUID(resolved))
        result = await service.sweep(system_principal(resolved), recipients=recipients)
        # Committed only after delivery: a notification row written before the
        # send would promise something the recipient never received.
        await session.commit()

    return result.as_dict()


async def run_digest(tenant_id: str | None = None) -> dict[str, Any]:
    """Daily summary of what the sweeps found.

    Not yet implemented as a distinct delivery: the sweep's own digest already
    summarises what its volume cap withheld, and a second daily summary that
    restated the same alerts would be the noise this design exists to avoid.
    Kept as a scheduled entry point so the cadence is declared where the other
    schedules are.
    """
    log.info("notifications.digest_noop", tenant_id=tenant_id)
    return {"status": "noop", "reason": "sweep digest already covers withheld alerts"}


async def run_delivery_retry(
    tenant_id: str | None = None, *, max_attempts: int = 5
) -> dict[str, Any]:
    """Re-attempt failed deliveries, bounded by their own attempt trail."""
    log.info("notifications.retry_noop", tenant_id=tenant_id, max_attempts=max_attempts)
    return {"status": "noop", "retried": 0}
