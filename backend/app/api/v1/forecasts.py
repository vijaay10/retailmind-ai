"""Forecasting endpoints (Analytics M7,).

Five targets — revenue, sales, demand, inventory, profit — plus the accuracy
scoreboard and per-forecast explanations.

**These serve published forecasts; they do not fit models.** The training job
(`ml/forecasting`) runs as a batch, writes to the warehouse, and the API reads
those rows through the same governed semantic layer every other surface uses.
That means a forecast is the same number for everyone until the next training
run, it carries a run id and a model card, and a retrained model needs no API
deploy. Scoring inside the request would make a forecast a function of when
you asked, which is exactly what makes a number impossible to defend.

All endpoints require `forecasts.read` — including the accuracy scoreboard,
deliberately: a role that can read a forecast must be able to see how wrong
that forecast has been.
"""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Path, Query

from app.api.deps import ForecastServiceDep, PrincipalDep
from app.schemas.forecasting import (
    AccuracyResponse,
    ExplanationResponse,
    ForecastResponse,
    ForecastTotalsResponse,
    SectionMeta,
)
from app.services.forecasting.service import ForecastSection

router = APIRouter(prefix="/forecasts", tags=["forecasting"])

_FORBIDDEN = {
    "description": "Requires forecast access.",
    "content": {
        "application/problem+json": {
            "example": {
                "type": "https://retailmind.ai/errors/forbidden",
                "title": "Permission denied",
                "status": 403,
                "detail": "You do not have permission to perform this action.",
                "hint": "Requires the 'forecasts.read' permission.",
            }
        }
    },
}

TARGETS = "revenue | sales | demand | inventory | profit"


def _meta(section: ForecastSection) -> SectionMeta:
    return SectionMeta(**{k: v for k, v in section.meta.items() if k in SectionMeta.model_fields})


def _respond(section: ForecastSection, *, target: str) -> ForecastResponse:
    horizons = {row.get("horizon") for row in section.rows if row.get("horizon") is not None}
    return ForecastResponse(
        target=target,
        horizon_days=len(horizons),
        data=section.rows,
        caveats=list(section.caveats),
        meta=_meta(section),
    )


# ── Forecasts ────────────────────────────────────────────────────────


@router.get(
    "/{target}",
    response_model=ForecastResponse,
    summary="Forecast for one target",
    responses={403: _FORBIDDEN},
)
async def forecast(
    principal: PrincipalDep,
    service: ForecastServiceDep,
    target: Annotated[str, Path(description=TARGETS)],
    series: Annotated[
        str | None, Query(description="Restrict to one series, e.g. 'AC-1010|S2001' for demand.")
    ] = None,
    horizon: Annotated[
        int | None, Query(ge=1, le=90, description="A single horizon step, in days ahead.")
    ] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
) -> ForecastResponse:
    """Published forecasts with prediction intervals, nearest date first.

    Each row is a statement about **one specific day**, not an average over
    the horizon — a mean forecast across a fortnight is a number no decision
    consumes.

    Three things travel with every forecast, and the response is much less
    useful without any of them:

    * **The interval.** Bands are empirical, built from how wrong this model
      has actually been at *this horizon* in backtesting, and widened by the
      conformal correction so that coverage holds in finite samples rather
      than only asymptotically. They make no distributional assumption.

      They do not necessarily widen with the horizon, and that is not a bug.
      A model extrapolating from a recent level does get worse with distance;
      seasonal naive does not, because its forecast is a weekday profile and
      predicting three weeks out is no harder than three days out. Its
      narrowest bands land on multiples of seven, where the weekly alignment
      is exact. This is a large part of why the baseline is hard to beat at
      long horizons.
    * **`model_mase`.** Below 1.0 the model beats seasonal naive; at or above,
      it does not, and the caveats say so in words. A forecast no better than
      "next Tuesday looks like last Tuesday" should be used accordingly.
    * **`model_wape`.** The model's measured out-of-sample error — the single
      number that says how much weight the point estimate deserves.

    Not every target is fitted. `inventory` is *projected* through the stock
    identity (opening − demand + scheduled receipts) and `profit` is revenue
    multiplied by forecast margin rate; both inherit the uncertainty of the
    model beneath them rather than claiming their own.
    """
    section = await service.forecast(
        principal, target=target, series=series, horizon=horizon, limit=limit
    )
    return _respond(section, target=target)


@router.get(
    "/{target}/total",
    response_model=ForecastTotalsResponse,
    summary="Horizon total for one target",
    responses={403: _FORBIDDEN},
)
async def totals(
    principal: PrincipalDep,
    service: ForecastServiceDep,
    target: Annotated[str, Path(description=TARGETS)],
    by: Annotated[str, Query(description="target | series_key | model_name")] = "target",
) -> ForecastTotalsResponse:
    """The whole horizon rolled into one number.

    Summing point forecasts across days is legitimate: a fortnight's expected
    revenue is the sum of its days. Summing the **bounds** is conservative
    rather than exact — independent daily errors partially cancel, so the
    aggregate band is wider than the true one. That is stated in the caveats
    instead of being silently corrected, because the correction depends on a
    day-to-day error correlation nobody here has measured, and inventing one
    would make the interval narrower than the evidence supports.
    """
    section = await service.totals(principal, target=target, by=by)
    return ForecastTotalsResponse(
        target=target,
        grouped_by=by,
        data=section.rows,
        caveats=list(section.caveats),
        meta=_meta(section),
    )


# ── Accuracy ─────────────────────────────────────────────────────────


@router.get(
    "/meta/accuracy",
    response_model=AccuracyResponse,
    summary="Forecast accuracy scoreboard",
    responses={403: _FORBIDDEN},
)
async def accuracy(principal: PrincipalDep, service: ForecastServiceDep) -> AccuracyResponse:
    """How wrong each model has actually been — published, not buried.

    This endpoint is the platform grading itself in public, and it exists
    because the alternative is forecasts trusted in proportion to how
    confidently they are presented.

    * **WAPE** is the headline: total absolute error over total actual, so a
      big day counts for what it is worth. **MAPE** is reported beside it for
      readers who expect it, and is the least reliable of the set.
    * **Bias** is the one to watch for replenishment. A model with good WAPE
      and strong bias is wrong in a *consistent direction*, and a 5%
      over-forecast held for a quarter is a quarter of excess stock rather
      than an error that averages out.
    * **`interval_coverage`** grades the bands against their nominal 80%. One
      claiming 80% and delivering 50% is miscalibrated, and the caveats say so
      rather than leaving a reader to notice.
    * **`forecast_days` and `pending_days`** are separated because a forecast
      for a day that has not happened has nothing to be scored against.
      Counting it would quietly inflate the sample behind every rate here.

    Rows are split by `produced_by`. The warehouse SQL baseline and the
    Python-trained one share the name `seasonal_naive_w4` because they are the
    same idea, and they are not the same code — grading them together would
    attribute one implementation's accuracy to the other.
    """
    section = await service.accuracy(principal)
    scored = [row for row in section.rows if row.get("forecast_days")]
    return AccuracyResponse(
        models=section.rows,
        best_model=str(scored[0].get("model_name")) if scored else None,
        caveats=list(section.caveats),
        meta=_meta(section),
    )


@router.get(
    "/{target}/explain",
    response_model=ExplanationResponse,
    summary="Why a forecast came out where it did",
    responses={403: _FORBIDDEN},
)
async def explain(
    principal: PrincipalDep,
    service: ForecastServiceDep,
    target: Annotated[str, Path(description=TARGETS)],
    business_date: Annotated[
        date | None, Query(description="Restrict to a single forecast date.")
    ] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
) -> ExplanationResponse:
    """Per-feature contributions behind individual forecasts.

    **These are exact, not approximated.** The models here are linear, so a
    prediction *is* `baseline + Σ(coefficient × feature)`, and the
    contributions returned are that decomposition rather than a SHAP estimate
    of it. `baseline + sum(effect)` reconstructs the point forecast to
    floating-point tolerance, and the test suite asserts it does.

    That exactness is the point. A planner overriding a forecast needs a
    reason they can check; an explanation that does not add up to the number
    it explains is a story about the model rather than a description of it.

    `baseline` is what the model predicts with every feature at its training
    mean, so each effect reads as "how far this feature moved the forecast
    away from a typical day" — which is the form the question is usually asked
    in.
    """
    section = await service.explain(
        principal, target=target, business_date=business_date, limit=limit
    )
    return ExplanationResponse(
        target=target,
        data=section.rows,
        method=(
            "exact linear decomposition: prediction = baseline + Σ(coefficient × "
            "standardised feature); contributions reconstruct the forecast to "
            "floating-point tolerance"
        ),
        meta=_meta(section),
    )
