"""Assembling services in worker context.

A worker has no request and no dependency graph, so it builds its own — from
the same factories the API uses. A background job that reached the warehouse
by a different path would eventually disagree with the screen, and the
disagreement would surface as an alert nobody can reproduce.
"""

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import WarehouseSettings
from app.domain.auth.permissions import Permission, RoleKey, permissions_for
from app.domain.shared.errors import NotFoundError
from app.infrastructure.db.models.auth import AppUser, Role, Tenant, UserRole
from app.infrastructure.db.repositories.auth import TenantRepository
from app.infrastructure.db.repositories.notifications import NotificationRepository
from app.infrastructure.db.session import create_engine, create_session_factory
from app.infrastructure.semantic.client import SemanticLayerClient
from app.infrastructure.semantic.repository import AnalyticsRepository
from app.infrastructure.semantic.tenancy import resolve_warehouse_path
from app.services.analytics.service import AnalyticsService
from app.services.forecasting.service import ForecastingService
from app.services.notifications.service import NotificationService, Recipient
from app.services.recommendations.service import RecommendationService
from app.workers.runtime import email_sender


class _NoCache:
    """The analytics cache port, satisfied without Redis.

    A worker running a handful of queries an hour gains nothing from a cache
    and would gain a failure mode: a sweep that cannot alert because the cache
    is down is strictly worse than one that reads the warehouse directly.
    """

    def key(self, **_: object) -> str:
        return "worker"

    async def get(self, _: str) -> None:
        return None

    async def set(self, _: str, __: object) -> None:
        return None


class _RepositorySink:
    """Adapts the notification repository to the service's sink port."""

    def __init__(self, repository: NotificationRepository) -> None:
        self._repository = repository

    async def record(
        self, *, user_id: str, channel: str, event_type: str, payload: dict[str, object]
    ) -> None:
        await self._repository.record(
            user_id=user_id, channel=channel, event_type=event_type, payload=payload
        )

    async def last_notified(self) -> dict[str, object]:
        return dict(await self._repository.last_notified())


def build_analytics(tenant: Tenant) -> AnalyticsService:
    """Analytics over the given tenant's own warehouse — never a fixed path.

    Prompt 12.5: this used to read `RM_WAREHOUSE_DUCKDB_PATH` unconditionally,
    ignoring which tenant the sweep was running for entirely — every tenant's
    scheduled notification sweep read the same file the API's shared client
    did before this pass, the identical bug at a different call site. Now
    resolved the same way the API resolves it (`resolve_warehouse_path`), so
    there is exactly one place in the codebase that knows the convention.
    """
    warehouse = resolve_warehouse_path(tenant, WarehouseSettings())
    return AnalyticsService(
        AnalyticsRepository(SemanticLayerClient(warehouse), _NoCache())  # type: ignore[arg-type]
    )


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """One database session for the life of a task.

    The engine is created and disposed per task rather than held open. A
    scheduled job runs briefly and infrequently, and a pool left open between
    hourly runs is a pool holding connections a request could have used.
    """
    engine = create_engine()
    factory = create_session_factory(engine)
    try:
        async with factory() as session:
            yield session
    finally:
        await engine.dispose()


async def build_notification_service(tenant_id: str, session: AsyncSession) -> NotificationService:
    """The sweep, wired against a live session."""
    tenant = await TenantRepository(session).get(uuid.UUID(tenant_id))
    if tenant is None:
        raise NotFoundError("Tenant not found.")
    analytics = build_analytics(tenant)
    repository = NotificationRepository(session, uuid.UUID(tenant_id))
    return NotificationService(
        analytics,
        forecasts=ForecastingService(analytics),
        recommendations=RecommendationService(analytics),
        sink=_RepositorySink(repository),  # type: ignore[arg-type]
        email=email_sender(),
    )


async def load_recipients(session: AsyncSession, tenant_id: uuid.UUID) -> list[Recipient]:
    """Everyone who should hear about this tenant's alerts.

    Permissions come from the user's role rather than from a notification
    setting, so a role change immediately narrows what that person is told.
    A separate opt-in list would drift, and the drift stays invisible until
    somebody receives an alert they cannot open.
    """
    rows = (
        await session.execute(
            select(AppUser.id, AppUser.email, Role.key)
            .join(UserRole, UserRole.user_id == AppUser.id)
            .join(Role, Role.id == UserRole.role_id)
            .where(AppUser.tenant_id == tenant_id, AppUser.status == "active")
        )
    ).all()

    # A user may hold several roles, so permissions are the union — matching
    # how an authenticated principal is built. Resolving them differently here
    # would mean somebody receiving alerts they cannot open, or missing ones
    # they can.
    grouped: dict[uuid.UUID, tuple[str, set[RoleKey]]] = {}
    for user_id, email, role_key in rows:
        _, keys = grouped.setdefault(user_id, (email, set()))
        # A role key the enum does not recognise is skipped rather than
        # crashing the sweep: a seeded row from a newer schema must not stop
        # everyone else being told about a stockout.
        try:
            keys.add(RoleKey(role_key))
        except ValueError:
            continue

    recipients: list[Recipient] = []
    for user_id, (email, keys) in grouped.items():
        permissions: set[Permission] = set(permissions_for(frozenset(keys)))
        recipients.append(
            Recipient(
                user_id=str(user_id),
                email=email,
                permissions=frozenset(permissions),
                channels=frozenset({"in_app"}),
            )
        )
    return recipients
