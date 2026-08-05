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
from app.services.forecasting.service import ForecastingService
from app.services.inventory.service import InventoryIntelligenceService
from app.services.nlq.service import NaturalLanguageService
from app.services.notifications.service import NotificationService
from app.services.rca.service import RootCauseService
from app.services.recommendations.service import RecommendationService
from app.services.reporting.composer import ReportComposer
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


def get_inventory_service(analytics: AnalyticsServiceDep) -> InventoryIntelligenceService:
    """Inventory intelligence over the governed analytics registry.

    Stateless. Reorder arithmetic lives in the warehouse, not here, so the
    same numbers appear whether they are reached through this API, a dbt
    docs page, or a direct query — which is what makes a buyer's override
    reproducible instead of a disagreement with a black box.
    """
    return InventoryIntelligenceService(analytics)


InventoryServiceDep = Annotated[InventoryIntelligenceService, Depends(get_inventory_service)]


def get_forecast_service(analytics: AnalyticsServiceDep) -> ForecastingService:
    """Forecast serving over the governed analytics registry.

    Reads published forecasts; never fits a model. Training runs as a batch
    job in ml/forecasting, so the API image carries no numerical stack and a
    retrained model reaches users without a deploy.
    """
    return ForecastingService(analytics)


ForecastServiceDep = Annotated[ForecastingService, Depends(get_forecast_service)]


def get_rca_service(analytics: AnalyticsServiceDep) -> RootCauseService:
    """Root cause analysis over the governed analytics registry.

    Reads every RCA relation through the same registry as the rest of the
    platform. That breadth is the feature — a revenue drop can originate in
    inventory, shipping, or the weather — and routing it through the registry
    is what keeps the breadth from becoming an exemption from access control.
    """
    return RootCauseService(analytics)


RcaServiceDep = Annotated[RootCauseService, Depends(get_rca_service)]


def get_recommendation_service(analytics: AnalyticsServiceDep) -> RecommendationService:
    """Recommendations over the governed analytics registry.

    Composes surfaces the platform already publishes rather than reading the
    warehouse directly, so a recommendation can never quote a number the
    corresponding dashboard would disagree with.
    """
    return RecommendationService(analytics)


RecommendationServiceDep = Annotated[RecommendationService, Depends(get_recommendation_service)]


def get_nlq_service(
    analytics: AnalyticsServiceDep,
    rca: RcaServiceDep,
    forecasts: ForecastServiceDep,
    recommendations: RecommendationServiceDep,
) -> NaturalLanguageService:
    """Natural-language querying over the governed registry.

    Composed from the engines that already answer each kind of question, so a
    question routed to root cause analysis returns the same graded findings
    the RCA endpoint does — rather than a second, weaker implementation of the
    same reasoning living behind a chat box.
    """
    return NaturalLanguageService(
        analytics, rca=rca, forecasts=forecasts, recommendations=recommendations
    )


NlqServiceDep = Annotated[NaturalLanguageService, Depends(get_nlq_service)]


def get_report_service(
    analytics: AnalyticsServiceDep,
    rca: RcaServiceDep,
    forecasts: ForecastServiceDep,
    recommendations: RecommendationServiceDep,
) -> ReportComposer:
    """Report composition over the platform's own surfaces.

    The composer takes no request context and renders nothing, so the same
    object can be driven by a worker writing to object storage when scheduled
    delivery lands.
    """
    return ReportComposer(analytics, rca=rca, forecasts=forecasts, recommendations=recommendations)


ReportServiceDep = Annotated[ReportComposer, Depends(get_report_service)]


def get_notification_service(
    request: Request,
    principal: PrincipalDep,
    session: SessionDep,
    analytics: AnalyticsServiceDep,
    forecasts: ForecastServiceDep,
    recommendations: RecommendationServiceDep,
) -> NotificationService:
    """Detection and the in-app inbox, over one request-scoped session.

    The email sender is deliberately absent on the request path: a manual
    sweep from the UI writes in-app notifications and nothing leaves the
    building. Email fan-out belongs to the scheduled worker, where the
    recipient list is the estate rather than whoever clicked.
    """
    from app.infrastructure.db.repositories.notifications import NotificationRepository
    from app.workers.container import _RepositorySink

    repository = NotificationRepository(session, principal.tenant_id)
    return NotificationService(
        analytics,
        forecasts=forecasts,
        recommendations=recommendations,
        sink=_RepositorySink(repository),  # type: ignore[arg-type]
        repository=repository,
    )


NotificationServiceDep = Annotated[NotificationService, Depends(get_notification_service)]
