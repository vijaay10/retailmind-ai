"""Detection sweep and notification fan-out.

The sweep runs every detector, suppresses what should not be sent, and fans
the rest out to the channels each recipient has asked for. Three properties
matter more than the mechanics.

**A failing detector does not fail the sweep.** Six detectors read six
surfaces, and one of them being unavailable — a mart not yet rebuilt, a model
not yet trained — must not silence the other five. Failures are caught per
detector, recorded, and reported; a sweep that dies on the first exception is
one that stops alerting entirely the first time anything is degraded.

**Nobody is notified about what they cannot see.** Fan-out honours the
per-user, per-type preference matrix the schema already models, and a
recipient without the permission behind an alert is not sent it. An email
about profitability to someone who gets a 403 opening the link is worse than
no email.

**The sweep is idempotent.** Running it twice in a minute produces one set of
notifications, because suppression is keyed on what has already been sent
rather than on when the job last ran. That matters because retries, overlapping
schedules, and manual triggers all happen, and none of them should double-send.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

import structlog

from app.domain.auth.entities import Principal
from app.domain.auth.permissions import Permission
from app.infrastructure.notifications.email import EmailSender, render
from app.services.analytics.service import AnalyticsService
from app.services.forecasting.service import ForecastingService
from app.services.notifications import detectors, suppression
from app.services.notifications.contracts import (
    AlertCandidate,
    AlertKind,
    SweepResult,
    event_type_for,
)
from app.services.notifications.suppression import SuppressionState
from app.services.recommendations.service import RecommendationService
from app.services.recommendations.service import summarise as summarise_recommendations
from app.services.shared import authz

log = structlog.get_logger(__name__)

#: Comparison window for the sales-drop detector.
COMPARISON_DAYS = 7

#: Rows pulled per supporting query. Detection reads the head of each list —
#: the worst stockouts, the worst suppliers — not the whole estate.
QUERY_LIMIT = 100

#: Permission required to be told about each kind. A recipient who cannot open
#: the underlying screen should not receive the alert pointing at it.
REQUIRED_PERMISSION: dict[AlertKind, Permission] = {
    AlertKind.LOW_INVENTORY: Permission.ANALYTICS_INVENTORY_READ,
    AlertKind.INVENTORY_RISK: Permission.ANALYTICS_INVENTORY_READ,
    AlertKind.SALES_DROP: Permission.ANALYTICS_REVENUE_READ,
    AlertKind.FRAUD_RISK: Permission.ANALYTICS_REVENUE_READ,
    AlertKind.FORECAST_RISK: Permission.FORECASTS_READ,
    AlertKind.RECOMMENDATION_READY: Permission.RECOMMENDATIONS_READ,
}


@dataclass(frozen=True, slots=True)
class Recipient:
    """Someone to notify, and what they have asked to receive."""

    user_id: str
    email: str
    permissions: frozenset[Permission]
    channels: frozenset[str] = frozenset({"in_app"})

    def wants(self, candidate: AlertCandidate) -> bool:
        required = REQUIRED_PERMISSION.get(candidate.kind)
        return required is None or required in self.permissions


class NotificationSink:
    """Where in-app notifications are written."""

    async def record(
        self, *, user_id: str, channel: str, event_type: str, payload: dict[str, Any]
    ) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    async def last_notified(self) -> dict[str, datetime]:  # pragma: no cover
        raise NotImplementedError


class NotificationService:
    """Runs the detection sweep and delivers what survives suppression."""

    def __init__(
        self,
        analytics: AnalyticsService,
        *,
        forecasts: ForecastingService | None = None,
        recommendations: RecommendationService | None = None,
        sink: NotificationSink | None = None,
        email: EmailSender | None = None,
        repository: Any | None = None,
        recipients: list["Recipient"] | None = None,
    ) -> None:
        self._analytics = analytics
        self._forecasts = forecasts
        self._recommendations = recommendations
        self._sink = sink
        self._email = email
        self._repository = repository
        self._recipients = recipients or []

    async def sweep(
        self,
        principal: Principal,
        *,
        recipients: list[Recipient],
        as_of: date | None = None,
        state: SuppressionState | None = None,
        now: datetime | None = None,
    ) -> SweepResult:
        """Detect, suppress, and deliver."""
        authz.require(principal, Permission.ALERTS_READ)

        moment = now or datetime.now(UTC)
        today = as_of or moment.date()
        failures: dict[str, str] = {}

        detected: list[AlertCandidate] = []
        for name, run in self._detectors(principal, today).items():
            try:
                detected.extend(await run())
            except Exception as error:  # noqa: BLE001 — one detector must not stop the rest
                failures[name] = f"{type(error).__name__}: {error}"
                log.warning("notifications.detector_failed", detector=name, error=str(error))

        history = state or SuppressionState(
            last_notified=await self._sink.last_notified() if self._sink else {}
        )
        sending, withheld = suppression.apply(detected, history, now=moment)

        digest = suppression.summarise_withheld(withheld)
        if digest is not None:
            sending.append(digest)

        deliveries = 0
        for candidate in sending:
            deliveries += await self._deliver(candidate, recipients)

        result = SweepResult(
            detected=tuple(detected),
            notified=tuple(sending),
            suppressed=tuple(withheld),
            deliveries=deliveries,
            detectors_failed=failures,
            started_at=moment,
        )
        log.info("notifications.sweep", **result.as_dict())
        return result

    # ── Inbox ────────────────────────────────────────────────────────

    async def inbox(
        self, principal: Principal, *, unread_only: bool = False, limit: int = 50
    ) -> tuple[list[Any], int]:
        """The caller's own notifications, and their unread count.

        Scoped by the principal rather than by a parameter: an inbox that
        takes a user id is one where guessing an identifier reads somebody
        else's mail.
        """
        authz.require(principal, Permission.ALERTS_READ)
        if self._repository is None:
            return [], 0
        rows = await self._repository.inbox(principal.user_id, unread_only=unread_only, limit=limit)
        return rows, await self._repository.unread_count(principal.user_id)

    async def mark_read(self, principal: Principal, ids: list[Any]) -> int:
        authz.require(principal, Permission.ALERTS_READ)
        if self._repository is None:
            return 0
        return int(await self._repository.mark_read(principal.user_id, ids))

    async def run_sweep(self, principal: Principal) -> SweepResult:
        """Trigger detection on demand, exactly as the scheduler does.

        The caller becomes the sole recipient when no recipient list is
        configured, so a manual trigger never fans out to an estate on
        somebody's behalf.
        """
        recipients = self._recipients or [
            Recipient(
                user_id=str(principal.user_id),
                email=principal.email,
                permissions=frozenset(principal.permissions),
                channels=frozenset({"in_app"}),
            )
        ]
        return await self.sweep(principal, recipients=recipients)

    # ── Detectors ────────────────────────────────────────────────────

    def _detectors(
        self, principal: Principal, as_of: date
    ) -> dict[str, Callable[[], Awaitable[list[AlertCandidate]]]]:
        return {
            "low_inventory": lambda: self._low_inventory(principal, as_of),
            "sales_drop": lambda: self._sales_drop(principal, as_of),
            "forecast_risk": lambda: self._forecast_risk(principal, as_of),
            "fraud_risk": lambda: self._fraud_risk(principal, as_of),
            "inventory_risk": lambda: self._inventory_risk(principal, as_of),
            "recommendation_ready": lambda: self._recommendation_ready(principal, as_of),
        }

    async def _low_inventory(self, principal: Principal, as_of: date) -> list[AlertCandidate]:
        answer = await self._analytics.query(
            principal,
            domain_key="reorder",
            metrics=[
                "soonest_stockout_days",
                "daily_demand",
                "revenue_at_risk",
                "suggested_order_qty",
            ],
            dimensions=["sku", "store_id"],
            sort_by="revenue_at_risk",
            limit=QUERY_LIMIT,
        )
        return detectors.low_inventory(answer.result.rows, as_of=as_of)

    async def _sales_drop(self, principal: Principal, as_of: date) -> list[AlertCandidate]:
        start = as_of - timedelta(days=COMPARISON_DAYS - 1)
        prior_end = start - timedelta(days=1)
        prior_start = prior_end - timedelta(days=COMPARISON_DAYS - 1)

        async def total(first: date, last: date) -> float:
            answer = await self._analytics.query(
                principal,
                domain_key="revenue",
                metrics=["net_revenue"],
                dimensions=[],
                start_date=first,
                end_date=last,
                limit=1,
            )
            rows = answer.result.rows
            return float(rows[0].get("net_revenue") or 0.0) if rows else 0.0

        regions = await self._analytics.query(
            principal,
            domain_key="revenue",
            metrics=["net_revenue"],
            dimensions=["region"],
            start_date=start,
            end_date=as_of,
            sort_by="net_revenue",
            descending=False,
            limit=20,
        )
        return detectors.sales_drop(
            current=await total(start, as_of),
            prior=await total(prior_start, prior_end),
            region_rows=regions.result.rows,
            as_of=as_of,
        )

    async def _forecast_risk(self, principal: Principal, as_of: date) -> list[AlertCandidate]:
        if self._forecasts is None:
            return []
        section = await self._forecasts.forecast(principal, target="revenue")
        rows = [{**row, "target": "revenue"} for row in section.rows]
        return detectors.forecast_risk(rows, as_of=as_of)

    async def _fraud_risk(self, principal: Principal, as_of: date) -> list[AlertCandidate]:
        answer = await self._analytics.query(
            principal,
            domain_key="rca_slice",
            metrics=["return_rate", "return_amount", "net_revenue"],
            dimensions=["slice_value"],
            filters={"slice_type": "store"},
            start_date=as_of - timedelta(days=COMPARISON_DAYS - 1),
            end_date=as_of,
            sort_by="return_rate",
            limit=QUERY_LIMIT,
        )
        return detectors.fraud_risk(answer.result.rows, as_of=as_of)

    async def _inventory_risk(self, principal: Principal, as_of: date) -> list[AlertCandidate]:
        answer = await self._analytics.query(
            principal,
            domain_key="supplier",
            metrics=["otif_rate", "closed_lines", "ordered_value"],
            dimensions=["supplier_name"],
            sort_by="ordered_value",
            limit=QUERY_LIMIT,
        )
        return detectors.inventory_risk(answer.result.rows, as_of=as_of)

    async def _recommendation_ready(
        self, principal: Principal, as_of: date
    ) -> list[AlertCandidate]:
        if self._recommendations is None:
            return []
        portfolio = await self._recommendations.recommend(principal, end_date=as_of)
        return detectors.recommendation_ready(summarise_recommendations(portfolio), as_of=as_of)

    # ── Delivery ─────────────────────────────────────────────────────

    async def _deliver(self, candidate: AlertCandidate, recipients: list[Recipient]) -> int:
        event_type = event_type_for(candidate.kind, candidate.severity)
        payload = candidate.as_payload()
        delivered = 0

        for recipient in recipients:
            if not recipient.wants(candidate):
                continue

            for channel in sorted(recipient.channels):
                if channel == "in_app" and self._sink is not None:
                    await self._sink.record(
                        user_id=recipient.user_id,
                        channel=channel,
                        event_type=event_type,
                        payload=payload,
                    )
                    delivered += 1
                elif channel == "email" and self._email is not None and recipient.email:
                    message = render(payload)
                    try:
                        self._email.send(
                            type(message)(
                                to=recipient.email,
                                subject=message.subject,
                                body=message.body,
                                html=message.html,
                            )
                        )
                        delivered += 1
                    except Exception as error:  # noqa: BLE001
                        # A failed send is recorded and the sweep continues.
                        # One bad address must not stop everyone else being
                        # told, and silently swallowing it would let the system
                        # report perfect health while nobody receives anything.
                        log.warning(
                            "notifications.email_failed",
                            to=recipient.email,
                            error=str(error),
                        )

        return delivered
