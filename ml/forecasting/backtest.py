"""Rolling-origin backtesting.

**Never a random split.** K-fold cross-validation on a time series trains on
Thursday to predict Wednesday. The resulting accuracy is not merely optimistic,
it is meaningless: the model has seen the level, the trend, and often the exact
neighbouring days of every point it is scored on. Published forecast accuracy
built this way collapses in production and nobody can explain why.

So evaluation walks forward. Stand at an origin, fit on everything up to it,
forecast the next H days, score against what actually happened, advance, and
repeat. Every prediction is scored against data the model provably could not
see, which is the only setup whose numbers transfer to production.

The second thing this produces is the **residual distribution per horizon**,
which is where prediction intervals come from. Bands built from backtest
residuals are calibrated by construction against how wrong this model has
actually been, rather than against a distributional assumption it was never
checked for.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
import structlog

from forecasting.metrics import ForecastScore, score, seasonal_naive_scale
from forecasting.models.base import residual_quantile_band
from forecasting.series import MIN_HISTORY_DAYS, SEASON_LENGTH, TimeSeries

log = structlog.get_logger(__name__)

Array = npt.NDArray[np.float64]

#: Nominal coverage of the published interval. 80% rather than 95%: with a few
#: dozen residuals per horizon, the 2.5th percentile is an extrapolation from
#: one observation, and a band whose edges are that noisy claims a precision
#: the evidence does not carry.
DEFAULT_INTERVAL_LEVEL = 0.8


@dataclass(frozen=True, slots=True)
class BacktestResult:
    """Out-of-sample predictions, their score, and the residual bands."""

    model_name: str
    predictions: Array
    actuals: Array
    horizons: npt.NDArray[np.int_]
    origins: npt.NDArray[np.int_]
    score: ForecastScore
    bands: dict[int, tuple[float, float]]
    folds: int

    def residuals_by_horizon(self) -> dict[int, Array]:
        return {
            step: self.actuals[self.horizons == step] - self.predictions[self.horizons == step]
            for step in sorted({int(h) for h in self.horizons})
        }


def rolling_origin_backtest(
    series: TimeSeries,
    build: Callable[[], object],
    *,
    horizon: int,
    folds: int = 4,
    step: int = SEASON_LENGTH,
    interval_level: float = DEFAULT_INTERVAL_LEVEL,
    min_train: int = MIN_HISTORY_DAYS,
) -> BacktestResult:
    """Walk forward through ``series``, scoring each fold out of sample.

    ``build`` is a factory rather than a fitted model: every fold must start
    from an untrained instance. Refitting a model that has already seen the
    whole series is the subtlest form of leakage, and it looks identical to a
    correct backtest from the outside.

    Folds are spaced a season apart so each origin lands on a different point
    in the weekly cycle. Consecutive-day origins would produce folds that
    overlap almost completely and an accuracy estimate with far less
    independent evidence behind it than the fold count suggests.
    """
    if horizon < 1:
        raise ValueError(f"horizon must be at least 1, got {horizon}")
    if folds < 1:
        raise ValueError(f"folds must be at least 1, got {folds}")

    # Work backwards from the end so the most recent data is always evaluated:
    # a backtest that stops a month early grades the model on a world that has
    # since moved on.
    origins = [len(series) - horizon - offset * step for offset in range(folds)]
    origins = [origin for origin in origins if origin >= min_train]
    if not origins:
        raise ValueError(
            f"{series.key}: {len(series)} days cannot support {folds} folds at "
            f"horizon {horizon} with {min_train} days of minimum training data"
        )
    origins.reverse()

    all_predictions: list[float] = []
    all_actuals: list[float] = []
    all_horizons: list[int] = []
    all_origins: list[int] = []

    for origin in origins:
        history, _ = series.split(origin)
        model = build()
        model.fit(history, horizon=horizon)  # type: ignore[attr-defined]

        for prediction in model.predict(history):  # type: ignore[attr-defined]
            target_index = origin + prediction.horizon - 1
            if target_index >= len(series):
                continue
            all_predictions.append(prediction.point)
            all_actuals.append(float(series.values[target_index]))
            all_horizons.append(prediction.horizon)
            all_origins.append(origin)

    predictions = np.array(all_predictions, dtype=np.float64)
    actuals = np.array(all_actuals, dtype=np.float64)
    horizons = np.array(all_horizons, dtype=np.int_)

    # MASE is scaled against the seasonal naive computed on training data
    # only. Using the full series would let a model look good because the
    # evaluation window happened to be calm.
    scale = seasonal_naive_scale(series.values[: origins[0]], SEASON_LENGTH)

    residuals = {
        step_: actuals[horizons == step_] - predictions[horizons == step_]
        for step_ in sorted({int(h) for h in horizons})
    }
    bands = residual_quantile_band(residuals, level=interval_level)

    lower = np.array(
        [
            predictions[i] + bands.get(int(horizons[i]), (0.0, 0.0))[0]
            for i in range(predictions.size)
        ]
    )
    upper = np.array(
        [
            predictions[i] + bands.get(int(horizons[i]), (0.0, 0.0))[1]
            for i in range(predictions.size)
        ]
    )

    result = BacktestResult(
        model_name=getattr(build(), "name", "unknown"),
        predictions=predictions,
        actuals=actuals,
        horizons=horizons,
        origins=np.array(all_origins, dtype=np.int_),
        score=score(
            actuals,
            predictions,
            scale=scale,
            lower=lower,
            upper=upper,
            horizons=horizons,
            interval_quantile=1.0 - (1.0 - interval_level) / 2.0,
        ),
        bands=bands,
        folds=len(origins),
    )

    log.info(
        "forecast.backtest",
        series=series.key,
        model=result.model_name,
        folds=result.folds,
        points=result.score.points,
        wape=round(result.score.wape, 4),
        mase=round(result.score.mase, 4),
        beats_naive=result.score.beats_seasonal_naive,
    )
    return result


def compare(results: Sequence[BacktestResult]) -> list[BacktestResult]:
    """Rank models best-first on WAPE, with unrepresentative scores demoted.

    A model scored on four points can post an excellent WAPE by luck. Sorting
    representativeness first means a well-evidenced good model outranks a
    barely-evidenced brilliant one, which is the correct preference when the
    output drives ordering decisions.
    """
    return sorted(
        results,
        key=lambda r: (not r.score.is_representative, r.score.wape),
    )
