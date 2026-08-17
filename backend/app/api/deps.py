"""Dependency providers.

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
from app.domain.shared.errors import AuthenticationError, NotFoundError
from app.infrastructure.db.repositories.auth import (
    AuthEventRepository,
    RefreshTokenRepository,
    TenantRepository,
    UserRepository,
)
from app.infrastructure.db.repositories.insights import (
    AlertReadRepository,
    RecommendationReadRepository,
)
from app.infrastructure.db.repositories.outcomes import OutcomeRepository
from app.infrastructure.db.repositories.recommendation_decisions import (
    RecommendationDecisionRepository,
)
from app.infrastructure.llm.gateway import LlmGateway
from app.infrastructure.semantic.client import SemanticLayerClient
from app.infrastructure.semantic.repository import AnalyticsRepository
from app.infrastructure.semantic.tenancy import TenantWarehouseRegistry
from app.services.analyst.service import BusinessAnalystService
from app.services.analytics.service import AnalyticsService
from app.services.auth.service import AuthService
from app.services.calibration.service import CalibrationService
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
    (Backend's two-layer rule).
    """

    def _guard(principal: PrincipalDep) -> Principal:
        authz.require(principal, permission)
        return principal

    return _guard


# ── Analytics ────────────────────────────────────────────────────────


def get_tenant_repository(session: SessionDep) -> TenantRepository:
    """Not tenant-constructed like the others — its methods take the id
    explicitly and the handler passes ``principal.tenant_id`` itself, since
    a repository over the tenant table has no tenant to be scoped *to*."""
    return TenantRepository(session)


TenantRepositoryDep = Annotated[TenantRepository, Depends(get_tenant_repository)]


async def get_tenant_semantic_client(
    request: Request, principal: PrincipalDep, tenants: TenantRepositoryDep
) -> SemanticLayerClient:
    """The authenticated caller's own tenant's warehouse — never another's.

    Prompt 12.5: this is the one place every analytics-touching dependency
    now resolves its warehouse connection from, replacing the single
    process-wide client every request used to share regardless of tenant.
    `TenantWarehouseRegistry` (`app.state.warehouse_registry`) caches the
    resolved client per tenant id so this costs one Postgres lookup per
    request (the tenant row itself), not a warehouse file open — DuckDB
    connections are opened per *query*, inside `SemanticLayerClient`, same
    as before.
    """
    tenant = await tenants.get(principal.tenant_id)
    if tenant is None:
        raise NotFoundError("Tenant not found.")
    registry: TenantWarehouseRegistry = request.app.state.warehouse_registry
    return registry.client_for(tenant)


TenantSemanticClientDep = Annotated[SemanticLayerClient, Depends(get_tenant_semantic_client)]


def get_analytics_service(request: Request, client: TenantSemanticClientDep) -> AnalyticsService:
    """Analytics service over the caller's own tenant's warehouse.

    The cache is still process-wide (a per-request cache connection would
    defeat the pool) — safe because `AnalyticsCache.key()` already embeds
    `tenant_id`, so no two tenants can ever collide on one cache entry even
    though they share the Redis instance.
    """
    return AnalyticsService(
        AnalyticsRepository(
            client=client,
            cache=request.app.state.analytics_cache,
        )
    )


AnalyticsServiceDep = Annotated[AnalyticsService, Depends(get_analytics_service)]


def get_llm_gateway(request: Request) -> LlmGateway:
    """Process-wide LLM gateway.

    The gateway is a singleton: the provider holds no per-request state. Usage
    tracking is request-scoped and attached by services that need it.
    """
    gateway: LlmGateway = request.app.state.llm_gateway
    return gateway


LlmGatewayDep = Annotated[LlmGateway, Depends(get_llm_gateway)]


def get_dashboard_service(
    session: SessionDep,
    analytics: AnalyticsServiceDep,
    semantic: TenantSemanticClientDep,
) -> ExecutiveDashboardService:
    """Executive dashboard service.

    Composes the analytics service (warehouse metrics) with OLTP read
    repositories (alerts, recommendations) — the two estates meet here, in the
    service layer, rather than in a query that spans them. `semantic` used to
    be read directly off `app.state` here, bypassing the per-tenant
    resolution `AnalyticsServiceDep` already went through — same bug class
    Prompt 12.5 was written to find (a boundary trusted for one call site,
    quietly skipped at another). Now both come from the same tenant-resolved
    dependency.
    """
    return ExecutiveDashboardService(
        analytics=analytics,
        semantic=semantic,
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


def get_recommendation_service(
    analytics: AnalyticsServiceDep,
    principal: PrincipalDep,
    session: SessionDep,
) -> RecommendationService:
    """Recommendations over the governed analytics registry.

    Composes surfaces the platform already publishes rather than reading the
    warehouse directly, so a recommendation can never quote a number the
    corresponding dashboard would disagree with.

    The decision ledger is tenant-scoped at construction, so no call site can
    read or write another tenant's decisions by passing the wrong id.
    """
    return RecommendationService(
        analytics,
        RecommendationDecisionRepository(session, principal.tenant_id),
    )


RecommendationServiceDep = Annotated[RecommendationService, Depends(get_recommendation_service)]


def get_calibration_service(
    principal: PrincipalDep,
    session: SessionDep,
) -> CalibrationService:
    """Calibration service for recommendation outcome analysis.

    Provides calibration metrics across generators, confidence bands, and
    horizons. Read-only: learns from outcomes but does not automatically
    change production recommendations.
    """
    return CalibrationService(
        OutcomeRepository(session, principal.tenant_id),
    )


CalibrationServiceDep = Annotated[CalibrationService, Depends(get_calibration_service)]


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


def get_analyst_service(
    analytics: AnalyticsServiceDep,
    nlq: NlqServiceDep,
    rca: RcaServiceDep,
    forecasts: ForecastServiceDep,
    recommendations: RecommendationServiceDep,
    reports: ReportServiceDep,
    llm_gateway: LlmGatewayDep,
) -> BusinessAnalystService:
    """The analyst, composed from every engine that can answer something.

    It holds no query logic of its own: each capability delegates to the
    surface that owns the question, so an answer here is the same answer the
    corresponding endpoint gives. An assistant with its own implementation
    would eventually contradict the screen the user is looking at.

    The LLM gateway enhances narration fluency while keeping all numbers
    grounded in verified evidence from the analytical engines.
    """
    return BusinessAnalystService(
        analytics,
        nlq=nlq,
        rca=rca,
        forecasts=forecasts,
        recommendations=recommendations,
        reports=reports,
        llm_gateway=llm_gateway,
    )


AnalystServiceDep = Annotated[BusinessAnalystService, Depends(get_analyst_service)]
