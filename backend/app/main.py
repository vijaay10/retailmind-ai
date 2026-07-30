"""Application factory.

Scaffold scope only: settings load, health endpoints, and the versioned router
mount point. Middleware stack, DI container, and exception handlers land in S1
per Backend design §1/§6/§11–12.
"""

from fastapi import FastAPI

from app.core.config import Settings


def create_app() -> FastAPI:
    settings = Settings()

    app = FastAPI(
        title="RetailMind AI",
        version=settings.version,
        docs_url="/api/docs" if settings.env != "prod" else None,
        openapi_url="/api/openapi.json",
    )

    @app.get("/health", tags=["ops"])
    async def health() -> dict[str, str]:
        """Liveness: process up, no dependency checks (DevOps design §12)."""
        return {"status": "ok", "version": settings.version}

    @app.get("/ready", tags=["ops"])
    async def ready() -> dict[str, str]:
        """Readiness. TODO(S1): pg SELECT 1, redis PING, migrations-current."""
        return {"status": "ok"}

    # TODO(S1): app.include_router(api_v1_router, prefix="/api/v1")
    # TODO(S1): middleware stack per Backend design §11 (order is behavior)
    # TODO(S1): exception handlers per Backend design §12 (problem+json)

    return app
