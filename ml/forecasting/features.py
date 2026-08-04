"""Design-matrix construction for the feature model.

**Every feature must be knowable at forecast time.** That single rule is what
separates a backtest that predicts production accuracy from one that flatters
it. A feature like "this week's average" is available when you score history
and unavailable when you forecast next Tuesday, and a model trained on it
reports excellent accuracy right up until it meets the future.

So features come in exactly two kinds:

* **Calendar features of the target date** — day of week, weekend, position in
  the trend. Known years ahead, because a calendar is not a forecast.
* **Level features of the forecast origin** — the last observed value, the
  trailing weekly and four-weekly means, the trailing spread. Known at the
  moment the forecast is made, and *never* recomputed as the horizon extends.

The consequence is that a 14-day-ahead forecast sees exactly the same level
features as a 1-day-ahead one, and only the calendar moves. That is the
truthful setup: standing at Monday, you do not know Wednesday's sales, so the
model must not either.
"""

from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from forecasting.series import SEASON_LENGTH, TimeSeries

Array = npt.NDArray[np.float64]

#: Trailing windows used for level features, in days.
SHORT_WINDOW = 7
LONG_WINDOW = 28

#: Feature names, in the order the design matrix builds them. Persisted with
#: the model so a stored artefact can be read without rerunning this code —
#: a coefficient vector whose feature order is implicit is unauditable.
FEATURE_NAMES: tuple[str, ...] = (
    "level_last",
    "level_mean_7",
    "level_mean_28",
    "level_spread_7",
    "trend",
    "is_weekend",
    "dow_1",
    "dow_2",
    "dow_3",
    "dow_4",
    "dow_5",
    "dow_6",
)

#: Earliest origin index that has full trailing windows behind it. Building a
#: 28-day mean from 9 days would silently mean something different for the
#: first rows than the rest, and the model would learn the artefact.
MIN_ORIGIN_INDEX = LONG_WINDOW - 1


@dataclass(frozen=True, slots=True)
class DesignMatrix:
    """Features, targets, and the bookkeeping needed to interpret them."""

    x: Array
    """Shape (samples, features)."""

    y: Array
    """Shape (samples,) — the value at origin + horizon."""

    origins: npt.NDArray[np.int_]
    """Index of the forecast origin behind each row, for rolling-origin splits."""

    feature_names: tuple[str, ...] = FEATURE_NAMES


def level_features(series: TimeSeries, origin: int) -> Array:
    """The four level features observable at ``origin``.

    Uses data up to and including ``origin`` and nothing after it. This is the
    function that would leak the future if it were written carelessly, so it
    takes the origin explicitly rather than slicing from the end.
    """
    if origin < MIN_ORIGIN_INDEX:
        raise ValueError(
            f"origin {origin} has less than {LONG_WINDOW} days behind it; "
            "trailing windows would be computed over a shorter span than "
            "everywhere else and the model would learn the difference"
        )

    values = series.values
    short = values[origin - SHORT_WINDOW + 1 : origin + 1]
    long = values[origin - LONG_WINDOW + 1 : origin + 1]

    return np.array(
        [
            values[origin],
            float(np.mean(short)),
            float(np.mean(long)),
            float(np.std(short)),
        ],
        dtype=np.float64,
    )


def calendar_features(series: TimeSeries, target_index: int, *, trend_scale: float) -> Array:
    """Calendar features of the *target* date — known arbitrarily far ahead."""
    target_date = series.date_at(target_index)
    weekday = target_date.weekday()

    # Monday is the reference level and gets no dummy: with one dummy per day
    # plus an intercept the matrix is singular, and ridge would silently
    # distribute the intercept across seven collinear columns rather than fail.
    dow = np.zeros(SEASON_LENGTH - 1, dtype=np.float64)
    if weekday > 0:
        dow[weekday - 1] = 1.0

    return np.concatenate(
        [
            np.array(
                [target_index / trend_scale, 1.0 if weekday >= 5 else 0.0],
                dtype=np.float64,
            ),
            dow,
        ]
    )


def features_for(
    series: TimeSeries, origin: int, target_index: int, *, trend_scale: float
) -> Array:
    """One feature row: level at ``origin``, calendar at ``target_index``."""
    return np.concatenate(
        [
            level_features(series, origin),
            calendar_features(series, target_index, trend_scale=trend_scale),
        ]
    )


def build_design_matrix(series: TimeSeries, *, horizon: int) -> DesignMatrix:
    """Training rows for a single horizon step.

    One matrix per horizon, because the relationship between "what I can see
    today" and "what happens in h days" genuinely changes with h. The
    alternative — one model applied recursively, feeding its own output back
    as the next input — compounds its own error and produces intervals that
    are far too narrow by the end of the horizon.
    """
    if horizon < 1:
        raise ValueError(f"horizon must be at least 1, got {horizon}")

    trend_scale = float(max(len(series), 1))
    rows: list[Array] = []
    targets: list[float] = []
    origins: list[int] = []

    for origin in range(MIN_ORIGIN_INDEX, len(series) - horizon):
        target_index = origin + horizon
        rows.append(features_for(series, origin, target_index, trend_scale=trend_scale))
        targets.append(float(series.values[target_index]))
        origins.append(origin)

    if not rows:
        raise ValueError(
            f"{series.key}: no training rows at horizon {horizon} — "
            f"{len(series)} days of history is too short once a "
            f"{LONG_WINDOW}-day feature window is reserved"
        )

    return DesignMatrix(
        x=np.vstack(rows),
        y=np.array(targets, dtype=np.float64),
        origins=np.array(origins, dtype=np.int_),
    )
