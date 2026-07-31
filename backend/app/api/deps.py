"""Dependency providers (Backend design §6).

Request-scoped objects are assembled by a provider chain:

    request → principal (middleware) → session → repositories → services

Each layer declares only what it needs, so a router asking for ``AuthService``
transitively gets a live session and repositories without knowing they exist.
Singletons (engine, signer, settings) live on ``app.state``, built once by the
app factory — no module-level globals, which keeps tests free to build an app
with different settings.
"""

from collections.abc import AsyncIterator, Callable
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import AuthSettings
from app.core.security import TokenSigner
from app.domain.auth.entities import Principal
from app.domain.auth.permissions import Permission
from app.domain.shared.errors import AuthenticationError
from app.infrastructure.db.repositories.auth import (
    AuthEventRepository,
    RefreshTokenRepository,
    UserRepository,
)
from app.infrastructure.db.repositories.insights import (
    AlertReadRepository,
    RecommendationReadRepository,
)
from app.infrastructure.semantic.repository import AnalyticsRepository
from app.services.analytics.service import AnalyticsService
from app.services.auth.service import AuthService
from app.services.customers.service import CustomerIntelligenceService
from app.services.dashboard.service import ExecutiveDashboardService
from app.services.shared import authz
from app.services.shared.uow import UnitOfWork

# Declared purely so OpenAPI advertises bearer auth and Swagger UI shows the
# Authorize button; enforcement happens in ``current_principal``.
bearer_scheme = HTTPBearer(auto_error=False, description="Access token from /auth/login")


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """One session per request, committed on success, rolled back on error.

    This is the Unit of Work boundary: a handler that raises leaves no partial
    writes behind, including audit rows.
    """
    factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except BaseException:
            await session.rollback()
            raise


SessionDep = Annotated[AsyncSession, Depends(get_session)]


def get_auth_settings(request: Request) -> AuthSettings:
    settings: AuthSettings = request.app.state.auth_settings
    return settings


def get_token_signer(request: Request) -> TokenSigner:
    signer: TokenSigner = request.app.state.token_signer
    return signer


def get_auth_service(
    session: SessionDep,
    settings: Annotated[AuthSettings, Depends(get_auth_settings)],
    signer: Annotated[TokenSigner, Depends(get_token_signer)],
) -> AuthService:
    return AuthService(
        users=UserRepository(session),
        tokens=RefreshTokenRepository(session),
        events=AuthEventRepository(session),
        signer=signer,
        settings=settings,
        uow=UnitOfWork(session),
    )


AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]


async def current_principal(
    request: Request,
    service: AuthServiceDep,
    _credentials: Annotated[object, Depends(bearer_scheme)] = None,
) -> Principal:
    """The authenticated caller, re-validated against the database.

    The middleware already verified the signature; this re-checks the live
    account state (status, ``token_version``, current roles) so a revoked
    session or a role change takes effect on the next request rather than at
    token expiry.
    """
    auth_error = getattr(request.state, "auth_error", None)
    if auth_error is not None:
        raise auth_error

    claims_principal: Principal | None = getattr(request.state, "principal", None)
    if claims_principal is None:
        raise AuthenticationError(
            "Authentication required.", hint="Send an Authorization: Bearer <token> header."
        )

    principal = await service.resolve_principal(claims_principal)
    request.state.principal = principal  # refresh with authoritative state
    return principal


PrincipalDep = Annotated[Principal, Depends(current_principal)]


def requires(permission: Permission) -> Callable[[Principal], Principal]:
    """Route guard factory: ``dependencies=[Depends(requires(Permission.X))]``.

    Declaring the requirement on the route means unauthorized requests are
    rejected before the handler runs, and the requirement is visible in the
    generated OpenAPI description. Services still re-check independently
    (Backend §9's two-layer rule).
    """

    def _guard(principal: PrincipalDep) -> Principal:
        authz.require(principal, permission)
        return principal

    return _guard


# ── Analytics ────────────────────────────────────────────────────────


def get_analytics_service(request: Request) -> AnalyticsService:
    """Analytics service over the process-wide semantic client and cache.

    Both are singletons: the client holds no per-request state, and a
    per-request cache connection would defeat the pool.
    """
    return AnalyticsService(
        AnalyticsRepository(
            client=request.app.state.semantic_client,
            cache=request.app.state.analytics_cache,
        )
    )


AnalyticsServiceDep = Annotated[AnalyticsService, Depends(get_analytics_service)]


def get_dashboard_service(
    request: Request,
    session: SessionDep,
    analytics: AnalyticsServiceDep,
) -> ExecutiveDashboardService:
    """Executive dashboard service.

    Composes the analytics service (warehouse metrics) with OLTP read
    repositories (alerts, recommendations) — the two estates meet here, in the
    service layer, rather than in a query that spans them.
    """
    return ExecutiveDashboardService(
        analytics=analytics,
        semantic=request.app.state.semantic_client,
        alerts=AlertReadRepository(session),
        recommendations=RecommendationReadRepository(session),
    )


DashboardServiceDep = Annotated[ExecutiveDashboardService, Depends(get_dashboard_service)]


def get_customer_service(analytics: AnalyticsServiceDep) -> CustomerIntelligenceService:
    """Customer intelligence over the governed analytics registry.

    Holds no state of its own: the privacy floor and aggregation rules are
    pure functions of the rows the registry returns.
    """
    return CustomerIntelligenceService(analytics)


CustomerServiceDep = Annotated[CustomerIntelligenceService, Depends(get_customer_service)]
