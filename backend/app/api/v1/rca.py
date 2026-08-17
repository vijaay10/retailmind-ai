"""Root cause analysis endpoints (Analytics).

One endpoint, because root-cause analysis is one question: something moved,
and the answer is a ranked set of candidate explanations with the evidence
behind each.

**On the word "cause".** Everything this platform holds is observational — no
experiment, no control group, no randomisation. Observational data supports
statements about contribution and coincidence, and does not support statements
about causation. The API therefore never returns a bare "cause": every finding
declares its `claim_type` and its `evidence_tier`, and the tier caps how much
confidence the finding may express. A weather correlation with an
overwhelming signal is still capped, because the limit reflects what that
*kind* of evidence can support rather than how loud the number came out.

Requires `rca.run`.
"""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import PrincipalDep, RcaServiceDep
from app.schemas.rca import InvestigationResponse
from app.services.rca.contracts import Dimension
from app.services.rca.service import summarise

router = APIRouter(prefix="/rca", tags=["root cause analysis"])

_FORBIDDEN = {
    "description": "Requires permission to run investigations.",
    "content": {
        "application/problem+json": {
            "example": {
                "type": "https://retailmind.ai/errors/forbidden",
                "title": "Permission denied",
                "status": 403,
                "detail": "You do not have permission to perform this action.",
                "hint": "Requires the 'rca.run' permission.",
            }
        }
    },
}


@router.get(
    "/investigate",
    response_model=InvestigationResponse,
    summary="Investigate a KPI movement",
    responses={403: _FORBIDDEN},
)
async def investigate(
    principal: PrincipalDep,
    service: RcaServiceDep,
    metric: Annotated[
        str, Query(description="net_revenue | margin_amount | units_sold | orders")
    ] = "net_revenue",
    current_start: Annotated[
        date | None, Query(description="First day under investigation.")
    ] = None,
    current_end: Annotated[date | None, Query(description="Last day under investigation.")] = None,
    baseline_start: Annotated[date | None, Query(description="First comparison day.")] = None,
    baseline_end: Annotated[date | None, Query(description="Last comparison day.")] = None,
    dimensions: Annotated[
        list[str] | None,
        Query(
            description=(
                "Restrict the sweep. Omit to investigate all nine: region, "
                "store, segment, product, promotion, inventory, returns, "
                "shipping, weather."
            )
        ),
    ] = None,
) -> InvestigationResponse:
    """Investigate why a KPI moved, across all nine dimensions.

    The response is split into **where** and **why**, and the split is the
    point.

    `where` is arithmetic. Decomposing a revenue change across regions,
    stores, products, and segments is subtraction — the shares are exact and
    they sum to the whole. What it cannot tell you is whether a slice is the
    *origin* of a problem or simply the place an upstream one surfaced.

    `why` is hypothesis, graded. A stockout has a mechanism (an empty shelf
    cannot sell) and is graded accordingly. A late-delivery correlation has a
    plausible mechanism this platform has not measured. Weather has no
    mechanism in this data at all. Each tier carries a hard confidence
    ceiling, so a very strong correlation cannot be promoted into a very
    strong claim.

    **Slices are ranked by excess, not by size.** A naive engine ranks by raw
    contribution and therefore ranks by how big each slice already was — the
    largest region "explains" most of every decline the estate ever has, which
    is true, useless, and endlessly repeatable. Ranking by how much a slice
    moved *differently from the network* surfaces the one that actually
    changed behaviour, and a slice that fell exactly in line with everything
    else correctly appears nowhere.

    **Revenue changes are split into volume and rate.** Fewer transactions and
    smaller baskets are different problems with different owners — one is
    footfall or availability, the other is pricing or mix — and "revenue fell
    12%" narrows nothing down.

    **A quiet answer is a real answer.** Movements below the investigation
    floor return no findings and say why. An engine that always produces three
    causes produces three causes for noise, and a team that acts on those
    quickly learns to ignore all of them.

    `explained_share` counts only the arithmetic findings. Mechanisms and
    correlations re-describe pounds the decomposition has already attributed,
    and adding them would routinely push the total past 100% with every
    individual number still correct.
    """
    selected = tuple(Dimension(value) for value in dimensions) if dimensions else None
    investigation = await service.investigate(
        principal,
        metric=metric,
        current_start=current_start,
        current_end=current_end,
        baseline_start=baseline_start,
        baseline_end=baseline_end,
        dimensions=selected,
    )
    return InvestigationResponse(**summarise(investigation))
