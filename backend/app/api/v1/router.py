"""v1 router aggregation.

Every feature router mounts here; ``main`` mounts this once at ``/api/v1``.
Keeping the prefix in one place means versioning stays a routing concern
rather than something each module has to remember.
"""

from fastapi import APIRouter

from app.api.v1 import (
    analyst,
    analytics,
    auth,
    company,
    customers,
    dashboard,
    forecasts,
    inventory,
    nlq,
    notifications,
    rca,
    recommendations,
    reports,
)

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(analytics.router)
api_router.include_router(dashboard.router)
api_router.include_router(customers.router)
api_router.include_router(inventory.router)
api_router.include_router(forecasts.router)
api_router.include_router(rca.router)
api_router.include_router(recommendations.router)
api_router.include_router(nlq.router)
api_router.include_router(reports.router)
api_router.include_router(notifications.router)
api_router.include_router(analyst.router)
api_router.include_router(company.router)

# TODO(S5+):
# dashboards, admin — mounted as each lands.
