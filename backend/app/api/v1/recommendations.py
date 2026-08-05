"""Recommendation endpoints (Analytics §10).

Turns the platform's analysis into proposed actions across seven categories:
inventory, pricing, promotion, store, marketing, customer targeting, and
supplier.

**On the pound figures.** An expected revenue impact is a causal claim — "do
this and that will follow" — and most retail interventions have no measured
causal model behind them here. Raising a price moves profit by an amount that
depends on elasticity; running a campaign recovers some share of at-risk value.
Neither has been measured in this platform, and printing a confident number for
either would be an invention wearing arithmetic's clothes.

So every estimate declares its basis — `measured`, `modelled`, or `assumed` —
and the basis caps the confidence. Assumed estimates name the parameter they
turn on and ship a sensitivity range beside the point estimate, because the
only question that usually matters is whether disagreeing with the assumption
changes the decision.

Requires `recommendations.read`.
"""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import PrincipalDep, RecommendationServiceDep
from app.schemas.recommendations import RecommendationsResponse
from app.services.recommendations.contracts import Category
from app.services.recommendations.service import summarise

router = APIRouter(prefix="/recommendations", tags=["recommendations"])

_FORBIDDEN = {
    "description": "Requires recommendation access.",
    "content": {
        "application/problem+json": {
            "example": {
                "type": "https://retailmind.ai/errors/forbidden",
                "title": "Permission denied",
                "status": 403,
                "detail": "You do not have permission to perform this action.",
                "hint": "Requires the 'recommendations.read' permission.",
            }
        }
    },
}


@router.get(
    "",
    response_model=RecommendationsResponse,
    summary="Ranked recommendations with impact, confidence, and risk",
    responses={403: _FORBIDDEN},
)
async def recommendations(
    principal: PrincipalDep,
    service: RecommendationServiceDep,
    categories: Annotated[
        list[str] | None,
        Query(
            description=(
                "Restrict to certain categories: inventory, pricing, promotion, "
                "store, marketing, customer, supplier. Omit for all seven."
            )
        ),
    ] = None,
    end_date: Annotated[
        date | None, Query(description="Last day of the supporting analysis window.")
    ] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> RecommendationsResponse:
    """What to do next, ranked by what it is worth net of what it risks.

    **Ranking is risk-adjusted.** The score is expected profit weighted by
    confidence, less the downside weighted by the chance the reasoning fails.
    Ranking on headline value instead would put an irreversible,
    assumption-heavy markdown above a certain, reversible reorder worth nearly
    as much — and getting that trade right is most of what a recommendation
    engine is for.

    **Every recommendation carries a downside and a reversibility.** These are
    not the same risk. Ordering stock that turns out unnecessary costs its
    carrying charge, because the units still sell. A 40% markdown gives away
    margin that never comes back. A large upside does not make the second one
    safe, so irreversibility dominates the risk band rather than being averaged
    into it.

    **Every recommendation names its disqualifier.** `do_not_act_if` states the
    condition under which the advice is wrong — a line being discontinued, a
    store under refit, a supplier that is sole-source. The platform cannot see
    any of those, and the reader usually can.

    **Impact bases, and why they cap confidence:**

    * `measured` — arithmetic over observed data. The capital tied up in dead
      stock is subtraction, not a forecast.
    * `modelled` — uses a forecast or a documented model. Sales recovered by
      fixing a stockout are forecast demand for the days the shelf is empty.
    * `assumed` — turns on a behavioural parameter nobody here has measured:
      elasticity, promotional incrementality, campaign save rate. These carry a
      sensitivity range, and where that range crosses zero the honest next step
      is a test rather than a rollout.

    **Two totals, deliberately.** `gross_profit_opportunity` adds every
    estimate; `net_profit_opportunity` counts overlapping actions once.
    Reordering a line and fixing the supplier that made it late chase the same
    pounds, and a plan built on the gross figure will miss by the difference.

    `capital_freed` is never added to profit. Clearing dead stock releases cash
    *and* books a loss; combining them is how a clearance programme gets
    approved on the strength of the thing that makes it expensive.
    """
    selected = tuple(Category(value) for value in categories) if categories else None
    portfolio = await service.recommend(
        principal, categories=selected, end_date=end_date, limit=limit
    )
    return RecommendationsResponse(**summarise(portfolio))
