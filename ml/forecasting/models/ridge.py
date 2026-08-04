"""Ridge regression on calendar and level features — the challenger model.

**Why a linear model rather than gradient boosting.** Three reasons, in order
of weight:

1. *Explainability is exact.* A ridge prediction is `intercept + Σ wᵢxᵢ`, so
   the contribution of each feature is the decomposition itself — not a SHAP
   estimate of it, not a permutation proxy. A planner asking "why is Saturday
   forecast 30% above Tuesday" gets the actual arithmetic. Tree ensembles can
   only approximate this, and the approximation is the thing you cannot check.

2. *The data cannot support more.* Six weeks of daily history is roughly forty
   training rows per horizon. A boosted ensemble on forty rows memorises them
   and reports a backtest number that will not survive contact with November.
   Model capacity has to match evidence, and here the evidence is thin.

3. *It serialises to numbers.* The whole artefact is a coefficient vector, a
   scaler, and a feature list — storable as JSON, readable by a human,
   loadable without executing anything. See ``registry`` for why that matters.

Solved in closed form: `w = (XᵀX + λI)⁻¹Xᵀy`. No iteration, no learning rate,
no random seed, so two runs on the same data produce bit-identical
coefficients and a backfill genuinely reproduces.
"""

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import numpy.typing as npt

from forecasting.features import FEATURE_NAMES, build_design_matrix, features_for
from forecasting.models.base import Contribution, Explanation, Prediction
from forecasting.series import TimeSeries

Array = npt.NDArray[np.float64]

#: Ridge penalty. Chosen conservatively: with ~40 rows and 12 features the
#: risk is over-fitting, not under-fitting, and a penalty that is slightly too
#: strong costs a little accuracy while one that is too weak costs
#: reproducibility on the next refresh.
DEFAULT_ALPHA = 1.0

#: Standard deviation below which a feature is treated as constant. Dividing
#: by a near-zero scale turns a feature that never varies into one with an
#: enormous coefficient, which then dominates every explanation.
CONSTANT_TOLERANCE = 1e-12


@dataclass
class RidgeForecaster:
    """Direct multi-horizon ridge: one coefficient vector per horizon step."""

    alpha: float = DEFAULT_ALPHA
    name: str = "ridge_calendar_level"
    model_class: str = "model"
    bands: dict[int, tuple[float, float]] = field(default_factory=dict)

    feature_names: tuple[str, ...] = FEATURE_NAMES
    _coefficients: dict[int, Array] = field(default_factory=dict, repr=False)
    _intercepts: dict[int, float] = field(default_factory=dict, repr=False)
    _centre: dict[int, Array] = field(default_factory=dict, repr=False)
    _scale: dict[int, Array] = field(default_factory=dict, repr=False)
    _horizon: int = field(default=0, repr=False)
    _trend_scale: float = field(default=1.0, repr=False)

    # ── Training ─────────────────────────────────────────────────────

    def fit(self, series: TimeSeries, *, horizon: int) -> "RidgeForecaster":
        """Fit one model per horizon step from 1 to ``horizon``."""
        series.require_history()
        self._horizon = horizon
        self._trend_scale = float(max(len(series), 1))
        self._coefficients.clear()
        self._intercepts.clear()
        self._centre.clear()
        self._scale.clear()

        for step in range(1, horizon + 1):
            design = build_design_matrix(series, horizon=step)
            self._fit_one(step, design.x, design.y)

        return self

    def _fit_one(self, step: int, x: Array, y: Array) -> None:
        """Closed-form ridge on standardised features.

        Standardisation is not cosmetic here: the ridge penalty is applied in
        feature space, so without it the penalty falls hardest on whichever
        feature happens to be measured in the smallest units. Revenue level
        features run to five figures and day-of-week dummies are 0/1; an
        unstandardised fit would regularise the calendar out of existence.
        """
        centre = x.mean(axis=0)
        scale = x.std(axis=0)
        # A constant column carries no information; setting its scale to 1
        # leaves it centred at exactly zero, so it contributes nothing rather
        # than exploding.
        scale = np.where(scale < CONSTANT_TOLERANCE, 1.0, scale)
        standardised = (x - centre) / scale

        # The intercept is deliberately outside the penalty. Penalising it
        # would shrink the forecast toward zero rather than toward the mean,
        # which for a revenue series is a systematic under-forecast.
        y_centre = float(y.mean())
        gram = standardised.T @ standardised + self.alpha * np.eye(standardised.shape[1])
        weights = np.linalg.solve(gram, standardised.T @ (y - y_centre))

        self._coefficients[step] = weights
        self._intercepts[step] = y_centre
        self._centre[step] = centre
        self._scale[step] = scale

    # ── Prediction ───────────────────────────────────────────────────

    def _raw_features(self, series: TimeSeries, step: int) -> Array:
        origin = len(series) - 1
        return features_for(series, origin, origin + step, trend_scale=self._trend_scale)

    def _point(self, series: TimeSeries, step: int) -> float:
        if step not in self._coefficients:
            raise RuntimeError(f"{self.name}: no model fitted for horizon {step}")
        raw = self._raw_features(series, step)
        standardised = (raw - self._centre[step]) / self._scale[step]
        return float(self._intercepts[step] + standardised @ self._coefficients[step])

    def predict(self, series: TimeSeries) -> list[Prediction]:
        if not self._coefficients:
            raise RuntimeError(f"{self.name}: predict called before fit")

        predictions: list[Prediction] = []
        for step in range(1, self._horizon + 1):
            point = self._point(series, step)
            low, high = self.bands.get(step, (0.0, 0.0))
            predictions.append(
                Prediction(horizon=step, point=point, lower=point + low, upper=point + high)
            )
        return predictions

    # ── Explainability ───────────────────────────────────────────────

    def explain(self, series: TimeSeries, *, horizon: int) -> Explanation:
        """Decompose one prediction into exact per-feature contributions.

        The identity ``baseline + Σ effects == prediction`` holds to floating
        point, because it is the model's own arithmetic rather than an
        attribution method applied on top of it.

        The baseline is the intercept — what the model predicts with every
        feature at its training mean — so each effect reads as "how far this
        feature moved the forecast away from a typical day".
        """
        if horizon not in self._coefficients:
            raise RuntimeError(f"{self.name}: no model fitted for horizon {horizon}")

        raw = self._raw_features(series, horizon)
        standardised = (raw - self._centre[horizon]) / self._scale[horizon]
        effects = standardised * self._coefficients[horizon]

        contributions = tuple(
            Contribution(feature=name, value=float(raw[index]), effect=float(effects[index]))
            for index, name in enumerate(self.feature_names)
        )
        return Explanation(
            horizon=horizon,
            prediction=float(self._intercepts[horizon] + effects.sum()),
            baseline=self._intercepts[horizon],
            contributions=contributions,
        )

    def global_importance(self, horizon: int) -> dict[str, float]:
        """Absolute standardised coefficients — comparable across features.

        On standardised inputs the coefficient magnitude *is* the effect of a
        one-standard-deviation move, so these are directly comparable in a way
        raw coefficients on mixed units never are.
        """
        if horizon not in self._coefficients:
            raise RuntimeError(f"{self.name}: no model fitted for horizon {horizon}")
        weights = np.abs(self._coefficients[horizon])
        total = float(weights.sum())
        if total == 0.0:
            return dict.fromkeys(self.feature_names, 0.0)
        return {
            name: float(weights[index] / total) for index, name in enumerate(self.feature_names)
        }

    # ── Persistence ──────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "model_class": self.model_class,
            "kind": "ridge",
            "alpha": self.alpha,
            "horizon": self._horizon,
            "trend_scale": self._trend_scale,
            "feature_names": list(self.feature_names),
            "coefficients": {str(k): v.tolist() for k, v in self._coefficients.items()},
            "intercepts": {str(k): v for k, v in self._intercepts.items()},
            "centre": {str(k): v.tolist() for k, v in self._centre.items()},
            "scale": {str(k): v.tolist() for k, v in self._scale.items()},
            "bands": {str(k): list(v) for k, v in self.bands.items()},
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "RidgeForecaster":
        model = cls(alpha=float(payload["alpha"]), name=str(payload["name"]))
        model.feature_names = tuple(payload["feature_names"])
        model._horizon = int(payload["horizon"])
        model._trend_scale = float(payload["trend_scale"])
        model._coefficients = {
            int(k): np.asarray(v, dtype=np.float64) for k, v in payload["coefficients"].items()
        }
        model._intercepts = {int(k): float(v) for k, v in payload["intercepts"].items()}
        model._centre = {
            int(k): np.asarray(v, dtype=np.float64) for k, v in payload["centre"].items()
        }
        model._scale = {
            int(k): np.asarray(v, dtype=np.float64) for k, v in payload["scale"].items()
        }
        model.bands = {
            int(k): (float(v[0]), float(v[1])) for k, v in payload.get("bands", {}).items()
        }
        return model
