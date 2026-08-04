"""Baseline forecasters — the bar every real model has to clear.

These are not placeholders. Seasonal naive is the *denominator* of MASE and
the incumbent in the promotion gate, so it does real work: it is what stops a
model being adopted because its accuracy sounds good in isolation. A great
many production forecasting systems, measured honestly, do not beat the model
in this file, and the only way to find that out is to keep running it.
"""

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import numpy.typing as npt

from forecasting.models.base import Contribution, Explanation, Prediction
from forecasting.series import SEASON_LENGTH, TimeSeries

Array = npt.NDArray[np.float64]


@dataclass
class SeasonalNaive:
    """The same weekday, averaged over its recent occurrences.

    Retail's dominant signal is the weekly cycle, and this captures all of it
    and nothing else. Averaging the trailing occurrences rather than taking
    the single most recent one trades a little responsiveness for a lot of
    noise rejection: with one occurrence, a single wet Saturday defines what
    Saturdays are.
    """

    lookback: int = 4
    """Occurrences of each weekday to average. Four balances responsiveness
    against noise — fewer and one odd day dominates, more and the forecast
    stops reacting to a genuine level shift."""

    season_length: int = SEASON_LENGTH
    name: str = "seasonal_naive_w4"
    model_class: str = "baseline"
    bands: dict[int, tuple[float, float]] = field(default_factory=dict)

    _profile: dict[int, float] = field(default_factory=dict, repr=False)
    _horizon: int = field(default=0, repr=False)

    def fit(self, series: TimeSeries, *, horizon: int) -> "SeasonalNaive":
        series.require_history(self.season_length * 2)
        self._horizon = horizon
        self._profile = {}

        for phase in range(self.season_length):
            # Walk backwards from the end in season-length strides, so the
            # "recent occurrences" of each weekday are genuinely the most
            # recent ones rather than whatever the array start happened to be.
            indices = [index for index in range(len(series) - 1 - phase, -1, -self.season_length)][
                : self.lookback
            ]
            if indices:
                weekday = series.date_at(indices[0]).weekday()
                self._profile[weekday] = float(np.mean(series.values[indices]))

        return self

    def predict(self, series: TimeSeries) -> list[Prediction]:
        if not self._profile:
            raise RuntimeError(f"{self.name}: predict called before fit")

        fallback = float(np.mean(list(self._profile.values())))
        predictions: list[Prediction] = []
        for step in range(1, self._horizon + 1):
            weekday = series.date_at(len(series) - 1 + step).weekday()
            point = self._profile.get(weekday, fallback)
            low, high = self.bands.get(step, (0.0, 0.0))
            predictions.append(
                Prediction(horizon=step, point=point, lower=point + low, upper=point + high)
            )
        return predictions

    def explain(self, series: TimeSeries, *, horizon: int) -> Explanation:
        """Exact by construction: the forecast *is* the weekday's trailing mean.

        The decomposition that carries information is against the *overall*
        weekly mean, so the contribution reads as "this day of the week runs
        18% above a typical day" — which is the entire content of a seasonal
        naive forecast, stated plainly.
        """
        weekday = series.date_at(len(series) - 1 + horizon).weekday()
        overall = float(np.mean(list(self._profile.values())))
        point = self._profile.get(weekday, overall)
        return Explanation(
            horizon=horizon,
            prediction=point,
            baseline=overall,
            contributions=(
                Contribution(
                    feature=f"weekday_{weekday}_profile",
                    value=float(weekday),
                    effect=point - overall,
                ),
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "model_class": self.model_class,
            "kind": "seasonal_naive",
            "lookback": self.lookback,
            "season_length": self.season_length,
            "horizon": self._horizon,
            "profile": {str(k): v for k, v in self._profile.items()},
            "bands": {str(k): list(v) for k, v in self.bands.items()},
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "SeasonalNaive":
        model = cls(
            lookback=int(payload["lookback"]),
            season_length=int(payload["season_length"]),
            name=str(payload["name"]),
        )
        model._profile = {int(k): float(v) for k, v in payload["profile"].items()}
        model._horizon = int(payload["horizon"])
        model.bands = {
            int(k): (float(v[0]), float(v[1])) for k, v in payload.get("bands", {}).items()
        }
        return model


@dataclass
class MovingAverage:
    """Trailing mean, flat across the horizon — deliberately seasonality-blind.

    Kept alongside seasonal naive because comparing the two is diagnostic. If
    a series is forecast just as well by a flat mean, it has no usable weekly
    cycle, and any model that appears to find one is fitting noise.
    """

    window: int = 28
    name: str = "moving_average_28"
    model_class: str = "baseline"
    bands: dict[int, tuple[float, float]] = field(default_factory=dict)

    _level: float = field(default=0.0, repr=False)
    _horizon: int = field(default=0, repr=False)

    def fit(self, series: TimeSeries, *, horizon: int) -> "MovingAverage":
        series.require_history(min(self.window, len(series)))
        self._horizon = horizon
        self._level = float(np.mean(series.values[-self.window :]))
        return self

    def predict(self, series: TimeSeries) -> list[Prediction]:
        predictions = []
        for step in range(1, self._horizon + 1):
            low, high = self.bands.get(step, (0.0, 0.0))
            predictions.append(
                Prediction(
                    horizon=step,
                    point=self._level,
                    lower=self._level + low,
                    upper=self._level + high,
                )
            )
        return predictions

    def explain(self, series: TimeSeries, *, horizon: int) -> Explanation:
        """A flat forecast has one input and says so: the trailing level."""
        return Explanation(
            horizon=horizon,
            prediction=self._level,
            baseline=self._level,
            contributions=(
                Contribution(
                    feature=f"trailing_mean_{self.window}d", value=self._level, effect=0.0
                ),
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "model_class": self.model_class,
            "kind": "moving_average",
            "window": self.window,
            "level": self._level,
            "horizon": self._horizon,
            "bands": {str(k): list(v) for k, v in self.bands.items()},
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "MovingAverage":
        model = cls(window=int(payload["window"]), name=str(payload["name"]))
        model._level = float(payload["level"])
        model._horizon = int(payload["horizon"])
        model.bands = {
            int(k): (float(v[0]), float(v[1])) for k, v in payload.get("bands", {}).items()
        }
        return model
