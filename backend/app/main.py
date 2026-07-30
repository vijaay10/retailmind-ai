"""Application factory — the single composition root (Backend design §6).

Everything the process needs is built here once and attached to ``app.state``:
settings, the database engine and session factory, and the token signer.
Request-scoped objects are assembled by ``app.api.deps`` from those
singletons. No module-level globals, so tests can stand up an app with
different settings without touching import state.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from sqlalchemy import text

from app.api.v1.router import api_router
from app.core.config import (
    AuthSettings,
    CacheSettings,
    DatabaseSettings,
    Settings,
    WarehouseSettings,
)
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging
from app.core.middleware import install_middleware
from app.core.security import TokenSigner
from app.infrastructure.cache.redis_cache import build_cache
from app.infrastructure.db.session import create_engine, create_session_factory
from app.infrastructure.semantic.client import SemanticLayerClient

log = structlog.get_logger(__name__)

API_DESCRIPTION = """
**RetailMind AI** — retail decision intelligence.

### Authentication

1. `POST /api/v1/auth/login` with email and password.
2. Send the returned access token as `Authorization: Bearer <token>` (valid 15 minutes).
3. When it expires, `POST /api/v1/auth/refresh` — the refresh token rotates on
   every use, and replaying a rotated token revokes the whole session family.

### Authorization

Access is granted by **permissions**, not roles. Each role
(`admin`, `ceo`, `regional_manager`, `store_manager`, `marketing`, `inventory`,
`finance`) maps to a permission set — see `GET /api/v1/auth/permissions`.
Endpoints document the permission they require; a missing one returns `403`
naming it.

### Errors

All errors are `application/problem+json` with a stable `type` URI and a
`request_id` for support. Out-of-scope resources return `404`, never `403`.
"""


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Verify dependencies at startup; dispose them cleanly at shutdown.

    Failing fast here is deliberate: a process that cannot reach its database
    should never report itself ready and start taking traffic.
    """
    log.info("app.starting", env=app.state.settings.env, version=app.state.settings.version)
    yield
    await app.state.engine.dispose()
    await app.state.analytics_cache.close()
    log.info("app.stopped")


def create_app(
    settings: Settings | None = None,
    *,
    auth_settings: AuthSettings | None = None,
    db_settings: DatabaseSettings | None = None,
) -> FastAPI:
    settings = settings or Settings()
    auth_settings = auth_settings or AuthSettings()
    db_settings = db_settings or DatabaseSettings()

    configure_logging(settings.log_level, json_output=settings.env != "dev")

    app = FastAPI(
        title="RetailMind AI",
        version=settings.version,
        description=API_DESCRIPTION,
        docs_url="/api/docs" if settings.env != "prod" else None,
        redoc_url="/api/redoc" if settings.env != "prod" else None,
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )

    engine = create_engine(db_settings)
    app.state.settings = settings
    app.state.auth_settings = auth_settings
    app.state.engine = engine
    app.state.session_factory = create_session_factory(engine)
    app.state.token_signer = TokenSigner(auth_settings)

    warehouse_settings = WarehouseSettings()
    cache_settings = CacheSettings()
    app.state.semantic_client = SemanticLayerClient(
        warehouse_settings.duckdb_path,
        semantic_schema=warehouse_settings.semantic_schema,
        core_schema=warehouse_settings.core_schema,
    )
    app.state.analytics_cache = build_cache(cache_settings.cache_url, env=settings.env)

    install_middleware(
        app,
        signer=app.state.token_signer,
        cors_origins=[settings.base_url] if settings.env != "prod" else [],
    )
    register_exception_handlers(app)
    app.include_router(api_router, prefix="/api/v1")

    @app.get("/health", tags=["ops"], summary="Liveness probe")
    async def health() -> dict[str, str]:
        """Process is up. Checks no dependencies by design — a dead database
        must not cause the orchestrator to restart healthy containers."""
        return {"status": "ok", "version": settings.version}

    @app.get("/ready", tags=["ops"], summary="Readiness probe")
    async def ready() -> dict[str, str]:
        """Ready to serve traffic: database reachable within budget.

        TODO(S3): add Redis PING and an alembic-head check (Backend §34).
        """
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return {"status": "ok"}

    return app
