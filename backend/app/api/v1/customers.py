"""Customer intelligence endpoints (Analytics §2).

Eight surfaces over one population: segmentation, RFM grid, lifetime value,
retention cohorts, repeat purchase, journey, churn risk, and VIPs.

**Everything here is a population aggregate.** The warehouse holds per-customer
rows so joins work, but no endpoint can project one — the product analyses
cohorts, not people. Groups below the 20-customer reporting floor are withheld
and *counted* in `privacy`, so a caller knows the picture is partial rather
than quietly seeing a smaller world.

All eight require `analytics.customer.read`. They are not split by permission:
they are views of one population, and letting a role see churn risk without
the segment it belongs to would produce decisions made on half a picture.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Query

from app.api.deps import CustomerServiceDep, PrincipalDep
from app.schemas.customers import (
    ChurnRiskResponse,
    CustomerSectionResponse,
    JourneyResponse,
    PrivacyNote,
    RetentionResponse,
    SectionMeta,
)
from app.services.customers.service import PRIVACY_FLOOR, CustomerSection

router = APIRouter(prefix="/customers", tags=["customer intelligence"])

#: Bands that count toward the headline "value at risk". `none` and `low` are
#: excluded because they are not at risk, and `unknown` because a customer
#: whose cadence cannot be established is not evidence of anything.
ELEVATED_BANDS = frozenset({"medium", "high", "critical"})

_FORBIDDEN = {
    "description": "Requires the customer analytics module.",
    "content": {
        "application/problem+json": {
            "example": {
                "type": "https://retailmind.ai/errors/forbidden",
                "title": "Permission denied",
                "status": 403,
                "detail": "You do not have permission to perform this action.",
                "hint": "Requires the 'analytics.customer.read' permission.",
            }
        }
    },
}


def _privacy(section: CustomerSection) -> PrivacyNote:
    return PrivacyNote(
        floor=PRIVACY_FLOOR,
        suppressed_groups=section.suppressed_groups,
        suppressed_customers=section.suppressed_customers,
        note=section.privacy_note,
    )


def _meta(section: CustomerSection) -> SectionMeta:
    return SectionMeta(**{k: v for k, v in section.meta.items() if k in SectionMeta.model_fields})


def _section(section: CustomerSection) -> CustomerSectionResponse:
    return CustomerSectionResponse(
        data=section.rows, privacy=_privacy(section), meta=_meta(section)
    )


# ── Segmentation ─────────────────────────────────────────────────────


@router.get(
    "/segments",
    response_model=CustomerSectionResponse,
    summary="RFM segment distribution",
    responses={403: _FORBIDDEN},
)
async def segments(principal: PrincipalDep, service: CustomerServiceDep) -> CustomerSectionResponse:
    """The customer base split into named RFM segments.

    Segment names come from a fixed rule map held in the warehouse, so
    "Champions" means the same thing here, in a scheduled report, and in a
    targeting recommendation. Changing the map is a reviewed change, not a
    query parameter — segments that drift in meaning make every trend built on
    them meaningless.
    """
    return _section(await service.segments(principal))


@router.get(
    "/rfm",
    response_model=CustomerSectionResponse,
    summary="Recency × frequency grid",
    responses={403: _FORBIDDEN},
)
async def rfm_grid(principal: PrincipalDep, service: CustomerServiceDep) -> CustomerSectionResponse:
    """The RFM grid: one row per recency × frequency cell, sized by value.

    Named segments are a *summary* of this grid; the grid is where a marketer
    sees that a single segment holds two populations behaving differently.
    Scores run 1–5 on each axis, and 5 is always "best" — recent, frequent, or
    valuable — so the reader never has to remember which way an axis points.
    """
    return _section(await service.rfm_grid(principal))


# ── Value ────────────────────────────────────────────────────────────


@router.get(
    "/lifetime-value",
    response_model=CustomerSectionResponse,
    summary="Customer lifetime value by segment",
    responses={403: _FORBIDDEN},
)
async def lifetime_value(
    principal: PrincipalDep, service: CustomerServiceDep
) -> CustomerSectionResponse:
    """Historic and projected customer value.

    `avg_lifetime_value` is arithmetic over what actually happened, and is
    trustworthy. `avg_predicted_clv_12m` is an **extrapolation** — observed
    average order value multiplied by annualised purchase frequency, with no
    survival model and no discounting. It carries a confidence grade derived
    from customer tenure, because annualising a fortnight of history is a
    guess wearing a number's clothes.
    """
    return _section(await service.lifetime_value(principal))


# ── Retention ────────────────────────────────────────────────────────


@router.get(
    "/retention",
    response_model=RetentionResponse,
    summary="Weekly acquisition cohorts and retention curves",
    responses={403: _FORBIDDEN},
)
async def retention(
    principal: PrincipalDep,
    service: CustomerServiceDep,
    limit: Annotated[int, Query(ge=10, le=500, description="Maximum cohort rows.")] = 200,
) -> RetentionResponse:
    """The retention triangle: cohorts by week, tracked over weeks since acquisition.

    Cohorts are the only honest way to compare customer quality across time.
    A raw repeat rate compares a cohort acquired last month against one
    acquired last year and measures how long each has had to come back, not
    how good either is.

    Rows stop at the **observation edge**: a cohort acquired two weeks ago has
    no week-8 retention yet, and emitting a zero there would draw a cliff that
    does not exist. Absence is the honest value.
    """
    section = await service.retention(principal, limit=limit)
    cohorts = sorted({str(row.get("cohort_week")) for row in section.rows})
    return RetentionResponse(
        cohorts=cohorts,
        data=section.rows,
        privacy=_privacy(section),
        meta=_meta(section),
    )


@router.get(
    "/repeat-purchase",
    response_model=CustomerSectionResponse,
    summary="Repeat-purchase behaviour by lifecycle stage",
    responses={403: _FORBIDDEN},
)
async def repeat_purchase(
    principal: PrincipalDep, service: CustomerServiceDep
) -> CustomerSectionResponse:
    """How often customers come back, and how long they take.

    Cadence matters as much as rate. Knowing that Established customers buy
    every eleven days is what turns "overdue" into a decidable question rather
    than a feeling — and it is the denominator the churn-risk surface uses.
    """
    return _section(await service.repeat_purchase(principal))


# ── Journey ──────────────────────────────────────────────────────────


@router.get(
    "/journey",
    response_model=JourneyResponse,
    summary="The New → Repeat → Established → Loyal funnel",
    responses={403: _FORBIDDEN},
)
async def journey(principal: PrincipalDep, service: CustomerServiceDep) -> JourneyResponse:
    """Lifecycle funnel with stage-to-stage conversion.

    Stages are frequency-based: how far along the repeat-purchase progression
    a customer has travelled. Recency is tracked *separately* as churn risk,
    so a lapsing Loyal customer stays Loyal **and** at risk — collapsing both
    into one label would hide exactly the customer worth saving.

    Watch the New → Repeat step: a customer who never makes a second purchase
    never earns back their acquisition cost.
    """
    section = await service.journey(principal)
    return JourneyResponse(stages=section.rows, privacy=_privacy(section), meta=_meta(section))


# ── Risk ─────────────────────────────────────────────────────────────


@router.get(
    "/churn-risk",
    response_model=ChurnRiskResponse,
    summary="Customers drifting away, and the value drifting with them",
    responses={403: _FORBIDDEN},
)
async def churn_risk(
    principal: PrincipalDep,
    service: CustomerServiceDep,
    by: Annotated[
        str, Query(description="risk_band | segment | stage — how to group the risk.")
    ] = "risk_band",
) -> ChurnRiskResponse:
    """Churn risk in bands, ranked by value at risk.

    **Bands, not probabilities.** Risk is derived from how many of a
    customer's own purchase cycles have elapsed unfulfilled: someone who buys
    monthly and last bought three months ago is three cycles overdue. Calling
    that "68% likely to churn" would imply a calibration nobody has measured,
    so the API reports what is known and no more.

    Ranked by `value_at_risk` rather than headcount, because sorting retention
    effort by number of customers is how a team spends a quarter saving people
    worth less than the campaign. `vip_value_at_risk` is the sharpest line in
    the response: expensive to replace, and still reachable.
    """
    section = await service.churn_risk(principal, by=by)

    # The headline counts elevated bands only, and is always read off the
    # risk-band grouping — never off whichever grouping the caller happened to
    # display. Two reasons. Summing every band puts customers in the `none`
    # band into a figure labelled "value at risk", which here is 44% of the
    # total and turns a retention brief into fiction. And a headline whose
    # meaning changes when someone flips a display toggle is a number nobody
    # can quote in a meeting.
    banded = section if by == "risk_band" else await service.churn_risk(principal, by="risk_band")
    elevated = [
        row for row in banded.rows if str(row.get("churn_risk_band", "")).lower() in ELEVATED_BANDS
    ]

    return ChurnRiskResponse(
        grouped_by=by,
        total_value_at_risk=round(_total(elevated, "value_at_risk"), 2),
        vip_value_at_risk=round(_total(elevated, "vip_value_at_risk"), 2),
        bands=section.rows,
        privacy=_privacy(section),
        meta=_meta(section),
    )


def _total(rows: list[dict[str, Any]], column: str) -> float:
    return sum(float(row.get(column) or 0) for row in rows)


@router.get(
    "/vip",
    response_model=CustomerSectionResponse,
    summary="The VIP cohort and its risk profile",
    responses={403: _FORBIDDEN},
)
async def vip(
    principal: PrincipalDep,
    service: CustomerServiceDep,
    by: Annotated[
        str, Query(description="risk_band | segment — one axis at a time.")
    ] = "risk_band",
) -> CustomerSectionResponse:
    """VIPs by segment and risk band, never as a name list.

    A customer is a VIP when they are in the **top decile by lifetime value**
    *and* a repeat buyer. The repeat condition matters: a single large order is
    a good day, not a relationship, and treating it as VIP spends retention
    budget on somebody who was passing through.

    What a merchant needs here is not names but shape — how much of the
    business this group carries, and how many of them are drifting.
    `share_of_total_value` is the figure that changes decisions: when a tenth
    of customers hold a third of the value, retention stops being a line item.

    Grouped by one axis at a time. Crossing risk band with segment splits an
    already-small population into cells below the reporting floor, and the
    response then suppresses itself into nothing useful.
    """
    return _section(await service.vip(principal, by=by))
