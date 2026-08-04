"""Forecast evaluation metrics (Analytics M7, PRD G4).

Every metric here answers a different question, and reporting only one is how
a forecast gets adopted on faith:

* **WAPE** is the headline. Total absolute error over total actual, so a big
  day counts for what it is worth.
* **MAPE** is the number stakeholders ask for by name, and it is the most
  misleading of the set — it divides by the actual, so one quiet day with a
  small denominator can dominate the average, and it is undefined where the
  actual is zero. Reported, with the excluded points counted.
* **MASE** is the one that decides adoption. It scales error against the
  seasonal naive forecast computed on the *training* data, so MASE < 1 means
  "better than assuming next Tuesday looks like last Tuesday" and MASE ≥ 1
  means the model has earned nothing.
* **Bias** says whether the model is wrong in a consistent direction. A model
  with excellent WAPE and strong positive bias systematically over-forecasts,
  which for replenishment means systematically over-ordering — an error that
  compounds into working capital rather than averaging out.
* **Coverage** and **pinball loss** grade the *intervals*. A band claiming 95%
  that contains 60% of actuals is miscalibrated, and an uncalibrated interval
  is worse than none: it invites planning against a range that does not hold.
"""

from dataclasses import dataclass, field

import numpy as np
import numpy.typing as npt

Array = npt.NDArray[np.float64]

#: Below this many points a metric is arithmetic rather than evidence. Small
#: samples produce confident-looking numbers that move wildly on one new
#: observation, and a scoreboard that hides that invites false confidence.
MIN_EVALUATION_POINTS = 7


def _validate(actual: Array, predicted: Array) -> None:
    if actual.shape != predicted.shape:
        raise ValueError(f"shape mismatch: actual {actual.shape} vs predicted {predicted.shape}")
    if actual.size == 0:
        raise ValueError("cannot evaluate an empty series")


def mae(actual: Array, predicted: Array) -> float:
    """Mean absolute error, in the units of the series."""
    _validate(actual, predicted)
    return float(np.mean(np.abs(actual - predicted)))


def rmse(actual: Array, predicted: Array) -> float:
    """Root mean squared error — penalises large misses super-linearly.

    Worth reporting beside MAE: when RMSE is far above MAE, the error is
    concentrated in a few bad days rather than spread evenly, and for
    inventory those few days are the stockouts.
    """
    _validate(actual, predicted)
    return float(np.sqrt(np.mean((actual - predicted) ** 2)))


def wape(actual: Array, predicted: Array) -> float:
    """Weighted absolute percentage error: Σ|e| / Σ|actual|.

    The honest headline for retail. Unlike MAPE it has a single denominator,
    so a quiet Tuesday cannot outvote a peak Saturday, and it stays defined
    when individual actuals are zero.
    """
    _validate(actual, predicted)
    denominator = float(np.sum(np.abs(actual)))
    if denominator == 0.0:
        return float("nan")
    return float(np.sum(np.abs(actual - predicted)) / denominator)


@dataclass(frozen=True, slots=True)
class MapeResult:
    """MAPE plus what had to be dropped to compute it."""

    value: float
    excluded_points: int
    total_points: int

    @property
    def is_representative(self) -> bool:
        """False when enough points were dropped that the number misleads."""
        return self.excluded_points == 0


def mape(actual: Array, predicted: Array) -> MapeResult:
    """Mean absolute percentage error, with zero-actual points excluded.

    Returned as a result object rather than a float on purpose. MAPE is
    undefined where the actual is zero, and silently dropping those points
    turns "we could not measure half the days" into a confident-looking
    percentage. The count travels with the value so a reader can discount it.
    """
    _validate(actual, predicted)
    usable = actual != 0
    excluded = int(np.sum(~usable))
    if not np.any(usable):
        return MapeResult(float("nan"), excluded, int(actual.size))

    errors = np.abs((actual[usable] - predicted[usable]) / actual[usable])
    return MapeResult(float(np.mean(errors)), excluded, int(actual.size))


def smape(actual: Array, predicted: Array) -> float:
    """Symmetric MAPE — bounded, and defined when the actual is zero.

    Uses the mean of |actual| and |predicted| as the denominator, so it stays
    finite everywhere both are not simultaneously zero. Bounded at 2.0, which
    is its main virtue: unlike MAPE a single bad point cannot send the
    headline to infinity.
    """
    _validate(actual, predicted)
    denominator = (np.abs(actual) + np.abs(predicted)) / 2.0
    usable = denominator != 0
    if not np.any(usable):
        return 0.0
    return float(np.mean(np.abs(actual[usable] - predicted[usable]) / denominator[usable]))


def seasonal_naive_scale(training: Array, season_length: int) -> float:
    """Mean absolute seasonal difference of the training data — MASE's denominator.

    Computed on **training** data, never on the test window. Using the test
    window would let a model look good simply because the evaluation period
    happened to be calm, and would make MASE incomparable across horizons.
    """
    if training.size <= season_length:
        return float("nan")
    differences = np.abs(training[season_length:] - training[:-season_length])
    scale = float(np.mean(differences))
    return scale if scale > 0 else float("nan")


def mase(actual: Array, predicted: Array, *, scale: float) -> float:
    """Mean absolute scaled error: MAE divided by the seasonal naive's MAE.

    The adoption gate. Below 1.0 the model beats "next Tuesday looks like last
    Tuesday"; at or above 1.0 it has earned nothing and the baseline should
    stay in production, however sophisticated the challenger is.

    Scale-free, so a demand series selling 2 units a day and a revenue series
    turning over £40,000 are directly comparable.
    """
    _validate(actual, predicted)
    if not np.isfinite(scale) or scale <= 0:
        return float("nan")
    return float(mae(actual, predicted) / scale)


def bias(actual: Array, predicted: Array) -> float:
    """Signed relative error: Σ(predicted − actual) / Σ|actual|.

    Positive means systematic over-forecasting. This is the metric that
    matters most for replenishment: absolute error averages out across a
    portfolio, but bias accumulates — a 5% over-forecast held for a quarter is
    a quarter's worth of excess stock, not a rounding difference.
    """
    _validate(actual, predicted)
    denominator = float(np.sum(np.abs(actual)))
    if denominator == 0.0:
        return float("nan")
    return float(np.sum(predicted - actual) / denominator)


def interval_coverage(actual: Array, lower: Array, upper: Array) -> float:
    """Share of actuals that fell inside the predicted interval.

    Compared against the interval's nominal level. A 95% band covering 60% is
    a bug being reported rather than a detail being hidden — planning against
    a range that does not hold is worse than planning against a point estimate
    known to be uncertain.
    """
    _validate(actual, lower)
    _validate(actual, upper)
    inside = (actual >= lower) & (actual <= upper)
    return float(np.mean(inside))


def pinball_loss(actual: Array, predicted: Array, *, quantile: float) -> float:
    """Quantile (pinball) loss — the proper scoring rule for a quantile forecast.

    Coverage alone can be gamed by an absurdly wide band: an interval from
    zero to infinity has perfect coverage and no information. Pinball loss
    punishes width as well as misses, so it is what actually grades the
    interval rather than merely auditing it.
    """
    _validate(actual, predicted)
    if not 0.0 < quantile < 1.0:
        raise ValueError(f"quantile must be in (0, 1), got {quantile}")
    errors = actual - predicted
    return float(np.mean(np.maximum(quantile * errors, (quantile - 1.0) * errors)))


@dataclass(frozen=True, slots=True)
class ForecastScore:
    """The full scorecard for one model over one evaluation window."""

    points: int
    mae: float
    rmse: float
    wape: float
    smape: float
    bias: float
    mase: float
    mape: float
    mape_excluded_points: int
    interval_coverage: float | None = None
    pinball_loss: float | None = None
    per_horizon_wape: dict[int, float] = field(default_factory=dict)

    @property
    def beats_seasonal_naive(self) -> bool:
        """MASE below 1.0 — the bar a model must clear to be worth running."""
        return bool(np.isfinite(self.mase) and self.mase < 1.0)

    @property
    def is_representative(self) -> bool:
        """Enough evaluation points that the numbers mean something."""
        return self.points >= MIN_EVALUATION_POINTS

    def as_dict(self) -> dict[str, object]:
        return {
            "points": self.points,
            "mae": round(self.mae, 6),
            "rmse": round(self.rmse, 6),
            "wape": round(self.wape, 6),
            "smape": round(self.smape, 6),
            "bias": round(self.bias, 6),
            "mase": round(self.mase, 6),
            "mape": round(self.mape, 6),
            "mape_excluded_points": self.mape_excluded_points,
            "interval_coverage": (
                round(self.interval_coverage, 6) if self.interval_coverage is not None else None
            ),
            "pinball_loss": (
                round(self.pinball_loss, 6) if self.pinball_loss is not None else None
            ),
            "beats_seasonal_naive": self.beats_seasonal_naive,
            "is_representative": self.is_representative,
            "per_horizon_wape": {str(k): round(v, 6) for k, v in self.per_horizon_wape.items()},
        }


def score(
    actual: Array,
    predicted: Array,
    *,
    scale: float,
    lower: Array | None = None,
    upper: Array | None = None,
    horizons: npt.NDArray[np.int_] | None = None,
    interval_quantile: float = 0.9,
) -> ForecastScore:
    """Compute the full scorecard in one pass."""
    mape_result = mape(actual, predicted)

    per_horizon: dict[int, float] = {}
    if horizons is not None:
        # Error grows with horizon. Reporting only the pooled number hides
        # whether a model is excellent at one day out and useless at fourteen,
        # which is exactly what a planner needs to know before trusting it.
        for step in sorted({int(h) for h in horizons}):
            mask = horizons == step
            if np.any(mask):
                per_horizon[step] = wape(actual[mask], predicted[mask])

    coverage = None
    pinball = None
    if lower is not None and upper is not None:
        coverage = interval_coverage(actual, lower, upper)
        pinball = pinball_loss(actual, upper, quantile=interval_quantile)

    return ForecastScore(
        points=int(actual.size),
        mae=mae(actual, predicted),
        rmse=rmse(actual, predicted),
        wape=wape(actual, predicted),
        smape=smape(actual, predicted),
        bias=bias(actual, predicted),
        mase=mase(actual, predicted, scale=scale),
        mape=mape_result.value,
        mape_excluded_points=mape_result.excluded_points,
        interval_coverage=coverage,
        pinball_loss=pinball,
        per_horizon_wape=per_horizon,
    )
