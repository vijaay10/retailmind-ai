"""v1 router aggregation.

Every feature router mounts here; ``main`` mounts this once at ``/api/v1``.
Keeping the prefix in one place means versioning stays a routing concern
rather than something each module has to remember (Backend design §7).
"""

from fastapi import APIRouter

from app.api.v1 import analytics, auth, customers, dashboard, inventory

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(analytics.router)
api_router.include_router(dashboard.router)
api_router.include_router(customers.router)
api_router.include_router(inventory.router)

# TODO(S5+): nlq, alerts, rca, forecasts, recommendations, reports,
# dashboards, admin — mounted as each lands.
