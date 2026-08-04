"""The contract every forecaster implements.

Three properties are required of a model here, and each exists because of a
way forecasting systems go wrong in production.

**It must produce intervals, not just points.** A point forecast invites
planning as though the number were certain. Every model returns a band, and
the band is graded (see ``metrics.interval_coverage``).

**It must serialise to plain data.** Models persist as JSON, never pickle —
see ``registry``. A forecaster that cannot describe itself as numbers and
strings cannot be stored, which is a deliberate constraint rather than an
inconvenience.

**It must explain a prediction exactly.** Not approximately, not with a
post-hoc attribution method: the explanation must reconstruct the prediction
to floating-point tolerance. A planner overriding a forecast needs to know
what drove it, and an explanation that does not add up to the number it
explains is a story rather than a reason.
"""

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

import numpy as np
import numpy.typing as npt

from forecasting.series import TimeSeries

Array = npt.NDArray[np.float64]


@dataclass(frozen=True, slots=True)
class Prediction:
    """A forecast for one horizon step, with its band."""

    horizon: int
    point: float
    lower: float
    upper: float

    def __post_init__(self) -> None:
        if self.lower > self.upper:
            raise ValueError(f"h={self.horizon}: lower {self.lower} above upper {self.upper}")


@dataclass(frozen=True, slots=True)
class Contribution:
    """One feature's signed effect on a single prediction."""

    feature: str
    value: float
    """The feature's value for this prediction, in its original units."""
    effect: float
    """Signed contribution to the point forecast, in the series' units."""


@dataclass(frozen=True, slots=True)
class Explanation:
    """Why a prediction came out where it did.

    ``baseline + Σ contributions`` reconstructs ``prediction`` exactly. That
    identity is asserted in the test suite, because an explanation that does
    not reproduce its own prediction is decoration.
    """

    horizon: int
    prediction: float
    baseline: float
    """What the model predicts with every feature at its training mean — the
    reference point the contributions move away from."""
    contributions: tuple[Contribution, ...]

    def top(self, count: int = 5) -> tuple[Contribution, ...]:
        """The largest movers by absolute effect, sign preserved."""
        return tuple(sorted(self.contributions, key=lambda c: abs(c.effect), reverse=True)[:count])

    def reconstructs(self, *, tolerance: float = 1e-6) -> bool:
        total = self.baseline + sum(c.effect for c in self.contributions)
        return bool(abs(total - self.prediction) <= tolerance * max(1.0, abs(self.prediction)))


@runtime_checkable
class Forecaster(Protocol):
    """What the training pipeline and registry require of a model."""

    name: str
    """Stable identifier written to fct_forecast as ``model_name``."""

    model_class: str
    """``baseline`` or ``model`` — the scoreboard groups on it, and a
    challenger that cannot be told from its baseline cannot be judged."""

    def fit(self, series: TimeSeries, *, horizon: int) -> "Forecaster":
        """Train on history, returning self."""
        ...

    def predict(self, series: TimeSeries) -> list[Prediction]:
        """Forecast forward from the end of ``series``."""
        ...

    def to_dict(self) -> dict[str, Any]:
        """Serialise to JSON-safe primitives."""
        ...


#: Below this many residuals per horizon, no interval can be narrower than the
#: observed range while still guaranteeing its nominal coverage, so the band
#: simply *is* the range and says so.
MIN_RESIDUALS_FOR_QUANTILES = 8


def residual_quantile_band(
    residuals_by_horizon: dict[int, Array], *, level: float
) -> dict[int, tuple[float, float]]:
    """Empirical prediction bands from backtest residuals, per horizon.

    Three choices worth defending.

    **Empirical, not Gaussian.** Retail residuals are skewed — a stockout
    truncates the upside while a promotion has no ceiling — so a symmetric
    normal band is wrong in a direction that matters. Quantiles of the actual
    residuals make no distributional claim.

    **Per horizon, not pooled.** Forecast error grows with distance, so a band
    that is the same width on day 14 as on day 1 is simultaneously too wide at
    the near end and too narrow at the far end. Pooling residuals across
    horizons is the most common way an interval ends up miscalibrated while
    looking principled.

    **Conformal ranks, not raw percentiles.** This is the subtle one. Taking
    ``np.quantile(residuals, 0.1)`` puts the cut at index ``0.1·(n−1)``, which
    for n=12 sits between the second and third smallest — so two points fall
    below the band at each end and an "80%" interval covers 67% of the very
    residuals that built it. The error is systematic, not noise, and it shrinks
    only slowly with n.

    The split-conformal correction takes the ``⌈(n+1)(1−α/2)⌉``-th order
    statistic instead, which gives finite-sample coverage of at least the
    nominal level for any n and any residual distribution. The price is that
    the band is *conservative* when residuals are few — with a dozen it
    widens to the observed range — and being visibly too wide on thin evidence
    is the right failure direction for a number someone orders stock against.
    """
    if not 0.0 < level < 1.0:
        raise ValueError(f"level must be in (0, 1), got {level}")

    tail = (1.0 - level) / 2.0
    bands: dict[int, tuple[float, float]] = {}

    for horizon, residuals in residuals_by_horizon.items():
        count = residuals.size
        if count == 0:
            continue

        ordered = np.sort(residuals)
        if count < MIN_RESIDUALS_FOR_QUANTILES:
            bands[horizon] = (float(ordered[0]), float(ordered[-1]))
            continue

        # Conformal order statistics, 1-indexed then clamped into the sample.
        upper_rank = int(np.ceil((count + 1) * (1.0 - tail)))
        lower_rank = int(np.floor((count + 1) * tail))
        upper_index = min(max(upper_rank, 1), count) - 1
        lower_index = min(max(lower_rank, 1), count) - 1

        bands[horizon] = (float(ordered[lower_index]), float(ordered[upper_index]))

    return bands
