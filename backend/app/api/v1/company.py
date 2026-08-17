"""Company (tenant) profile endpoints — Prompt 12 onboarding.

The onboarding UI's "Company Configuration" step reads and edits these
fields. Gated by ``data.manage`` — an existing permission (granted to the
admin role) that was defined in the RBAC catalog but had no endpoint wired
to it until this file.
"""

from fastapi import APIRouter

from app.api.deps import PrincipalDep, TenantRepositoryDep
from app.domain.auth.permissions import Permission
from app.domain.shared.errors import NotFoundError
from app.infrastructure.db.models.auth import Tenant
from app.schemas.company import CompanyProfile, CompanyProfileUpdate
from app.services.shared import authz

router = APIRouter(prefix="/company", tags=["company"])

_FORBIDDEN = {
    "description": "Requires data-management access.",
    "content": {
        "application/problem+json": {
            "example": {
                "type": "https://retailmind.ai/errors/forbidden",
                "title": "Permission denied",
                "status": 403,
                "detail": "You do not have permission to perform this action.",
                "hint": "Requires the 'data.manage' permission.",
            }
        }
    },
}


def _to_schema(tenant: Tenant) -> CompanyProfile:
    return CompanyProfile(
        name=tenant.name,
        slug=tenant.slug,
        plan=tenant.plan,
        base_currency=tenant.base_currency,
        industry=tenant.industry,
        country_code=tenant.country_code,
        timezone=tenant.timezone,
        fiscal_year_start_month=tenant.fiscal_year_start_month,
    )


@router.get(
    "/profile",
    response_model=CompanyProfile,
    summary="The authenticated user's company profile",
    responses={403: _FORBIDDEN},
)
async def get_profile(principal: PrincipalDep, tenants: TenantRepositoryDep) -> CompanyProfile:
    """Basic business information: name, currency, industry, timezone.

    Not the analytics engine's configuration — those fields exist but
    nothing downstream (forecasting, recommendations, analytics) reads them
    yet. This is onboarding metadata, told honestly as such in the console.
    """
    authz.require(principal, Permission.DATA_MANAGE)
    tenant = await tenants.get(principal.tenant_id)
    if tenant is None:
        raise NotFoundError("Tenant not found.")
    return _to_schema(tenant)


@router.patch(
    "/profile",
    response_model=CompanyProfile,
    summary="Update the company profile",
    responses={403: _FORBIDDEN},
)
async def update_profile(
    principal: PrincipalDep,
    tenants: TenantRepositoryDep,
    body: CompanyProfileUpdate,
) -> CompanyProfile:
    authz.require(principal, Permission.DATA_MANAGE)
    tenant = await tenants.update_profile(
        principal.tenant_id,
        industry=body.industry,
        country_code=body.country_code,
        timezone=body.timezone,
        fiscal_year_start_month=body.fiscal_year_start_month,
    )
    if tenant is None:
        raise NotFoundError("Tenant not found.")
    return _to_schema(tenant)
