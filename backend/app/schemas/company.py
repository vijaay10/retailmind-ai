"""Company (tenant) profile DTOs — Prompt 12 onboarding."""

from pydantic import BaseModel, ConfigDict, Field


class ResponseModel(BaseModel):
    model_config = ConfigDict(from_attributes=False)


class CompanyProfile(ResponseModel):
    """The tenant's business profile. Nullable fields are unset, not zero."""

    name: str
    slug: str
    plan: str
    base_currency: str
    industry: str | None
    country_code: str | None
    timezone: str | None
    fiscal_year_start_month: int | None


class CompanyProfileUpdate(BaseModel):
    """PATCH body. Every field is optional: only what's supplied changes."""

    model_config = ConfigDict(extra="forbid")

    industry: str | None = Field(default=None, max_length=200)
    country_code: str | None = Field(default=None, min_length=2, max_length=2)
    timezone: str | None = Field(default=None, max_length=64)
    fiscal_year_start_month: int | None = Field(default=None, ge=1, le=12)
