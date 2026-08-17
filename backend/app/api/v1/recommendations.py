"""Recommendation endpoints (Analytics).

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
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query

from app.api.deps import CalibrationServiceDep, PrincipalDep, RecommendationServiceDep
from app.schemas.recommendations import (
    DecisionLogResponse,
    DecisionRequest,
    DecisionResponse,
    RecommendationsResponse,
)
from app.services.recommendations.contracts import Category
from app.services.recommendations.service import UnknownRecommendationError, summarise

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
    payload = summarise(portfolio)

    # A card whose decision is already made must not read as pending. The
    # engine recomputes proposals every request and has no memory of its own,
    # so without this an accepted action reappears tomorrow as if nobody had
    # ever looked at it — which is how an action queue loses its readers.
    decisions = await service.decisions_for(
        [item["decision_key"] for item in payload["recommendations"]]
    )
    for item in payload["recommendations"]:
        item["decision"] = decisions.get(item["decision_key"])
    payload["decided_count"] = sum(1 for item in payload["recommendations"] if item["decision"])

    return RecommendationsResponse(**payload)


@router.post(
    "/decisions",
    response_model=DecisionResponse,
    summary="Accept or dismiss a proposed action",
    responses={
        403: _FORBIDDEN,
        404: {"description": "No current recommendation matches that key."},
    },
)
async def decide(
    principal: PrincipalDep,
    service: RecommendationServiceDep,
    request: DecisionRequest,
    end_date: Annotated[
        date | None, Query(description="Analysis window the decision was taken against.")
    ] = None,
) -> DecisionResponse:
    """Record what a human decided, and stop showing the card as pending.

    **Requires `recommendations.act`, not `recommendations.read`.** Seeing a
    proposal and committing the business to it are different privileges, and
    the role matrix already separates them: the managers who own the
    consequences may act; the broad read-only roles may not.

    **The proposal is re-derived server-side rather than taken from the
    request.** A client that could supply its own action text and expected
    profit could write "accepted: +£10m" into the ledger, and the ledger is
    what everyone later reasons from. The request carries a key and a verb;
    every other field is read from the engine.

    **A key that no longer matches anything is refused.** Positions move. If
    the reorder that was proposed this morning is no longer advised this
    afternoon, recording an approval for it would be recording agreement with
    advice the platform has withdrawn.

    **Deciding twice replaces rather than appends.** One current decision per
    subject, so a card cannot render as both accepted and dismissed. The audit
    ledger keeps the history of who changed their mind.

    What this endpoint does *not* do is execute anything. No purchase order is
    raised, no price changes. It records a judgement and the number that
    judgement was made against, which is what makes the later question —
    "were we right?" — answerable at all.
    """
    try:
        decision = await service.decide(
            principal,
            decision_key=request.decision_key,
            action=request.action,
            reason_code=request.reason_code,
            note=request.note,
            end_date=end_date,
        )
    except UnknownRecommendationError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    return DecisionResponse(decision=decision, decided_by=principal.email)


@router.get(
    "/decisions",
    response_model=DecisionLogResponse,
    summary="What this team has decided lately",
    responses={403: _FORBIDDEN},
)
async def decision_log(
    principal: PrincipalDep,
    service: RecommendationServiceDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> DecisionLogResponse:
    """The decision log, newest first.

    `accepted_profit` totals what the accepted actions were *expected* to be
    worth, as estimated at the moment each was accepted. It is not realised
    profit and must never be read as such — nothing in this platform measures
    what happened after somebody acted. Recording the expectation is the
    precondition for one day being able to.
    """
    return DecisionLogResponse(**await service.decision_log(principal, limit=limit))


# ── Calibration ──────────────────────────────────────────────────────


@router.get(
    "/calibration",
    summary="Recommendation calibration summary",
    responses={403: _FORBIDDEN},
)
async def calibration_summary(
    principal: PrincipalDep,
    calibration_service: CalibrationServiceDep,
) -> dict[str, Any]:
    """Overall calibration analysis across all measured recommendation outcomes.

    Shows how well recommendations perform in practice vs. expectations:
    - Overall metrics (realization ratio, direction accuracy, success rate)
    - Generator performance (which recommendation types perform best)
    - Confidence calibration (are high-confidence recs actually more reliable?)
    - Systematic biases (which generators over/underestimate)
    - Sample sizes and statistical significance flags

    **This is observational data only.** The calibration engine learns from
    outcomes but does NOT automatically change production recommendations,
    confidence scores, or estimator logic.

    Returns empty metrics with limitations when insufficient measured outcomes
    exist (N < 20).
    """
    summary = await calibration_service.get_summary()
    return summary.as_dict()


@router.get(
    "/calibration/generators/{generator}",
    summary="Calibration for one generator",
    responses={
        403: _FORBIDDEN,
        404: {"description": "No measured outcomes for this generator."},
    },
)
async def generator_calibration(
    generator: str,
    principal: PrincipalDep,
    calibration_service: CalibrationServiceDep,
) -> dict[str, Any]:
    """Detailed calibration metrics for one recommendation generator.

    Args:
        generator: inventory | pricing | promotion | store | customer | supplier

    Returns:
        - Overall metrics for this generator
        - Breakdown by estimate basis (measured | modelled | assumed)
        - Confidence band calibration
        - Quality score (0.0-1.0)
        - Statistical significance flags

    Raises 404 if no measured outcomes exist for this generator.
    """
    performance = await calibration_service.get_generator_performance(generator)

    if performance is None:
        raise HTTPException(
            status_code=404,
            detail=f"No measured outcomes found for generator: {generator}",
        )

    return performance.as_dict()


@router.get(
    "/calibration/confidence",
    summary="Confidence calibration analysis",
    responses={403: _FORBIDDEN},
)
async def confidence_calibration(
    principal: PrincipalDep,
    calibration_service: CalibrationServiceDep,
) -> dict[str, Any]:
    """Confidence band calibration: do confidence scores match reliability?

    Answers: "Are recommendations we mark as 80-90% confident actually reliable
    80-90% of the time?"

    Returns confidence bands (0.0-0.2, 0.2-0.4, ..., 0.8-1.0) with:
    - Expected success rate (midpoint of band)
    - Actual success rate (observed)
    - Calibration error (abs difference)
    - Sample size
    - Statistical significance flag

    Well-calibrated bands have calibration_error < 0.10. Overconfident bands
    have actual < expected by >10pp.
    """
    bands = await calibration_service.get_confidence_calibration()

    return {
        "confidence_bands": [band.as_dict() for band in bands],
        "interpretation": (
            "A well-calibrated system has calibration_error < 0.10 for all bands. "
            "Overconfident bands show actual_success_rate significantly below expected. "
            "All bands must have sufficient sample size (N >= 30) for statistical reliability."
        ),
    }
