"""Forecasting models: two baselines and one challenger.

The baselines are not scaffolding. Seasonal naive is the MASE denominator and
the incumbent in the promotion gate, so it runs on every series regardless of
whether anyone intends to ship it.
"""

from forecasting.models.base import (
    Contribution,
    Explanation,
    Forecaster,
    Prediction,
    residual_quantile_band,
)
from forecasting.models.baselines import MovingAverage, SeasonalNaive
from forecasting.models.ridge import RidgeForecaster

__all__ = [
    "Contribution",
    "Explanation",
    "Forecaster",
    "MovingAverage",
    "Prediction",
    "RidgeForecaster",
    "SeasonalNaive",
    "residual_quantile_band",
]
