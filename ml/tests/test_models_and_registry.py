"""Model behaviour, explainability, intervals, and the promotion gate."""

import json
from datetime import date
from pathlib import Path

import numpy as np
import pytest

from forecasting.metrics import ForecastScore
from forecasting.models.base import (
    MIN_RESIDUALS_FOR_QUANTILES,
    Prediction,
    residual_quantile_band,
)
from forecasting.models.baselines import MovingAverage, SeasonalNaive
from forecasting.models.ridge import RidgeForecaster
from forecasting.registry import (
    MIN_IMPROVEMENT,
    ModelCard,
    ModelRegistry,
    decide_promotion,
    new_version,
)
from forecasting.series import (
    GapPolicy,
    InsufficientHistoryError,
    TimeSeries,
    build_series,
)

START = date(2026, 1, 5)


def weekly_series(weeks: int = 20) -> TimeSeries:
    values = [100.0 + 20.0 * np.sin(2 * np.pi * d / 7) + 0.3 * d for d in range(weeks * 7)]
    return TimeSeries("test", START, np.array(values, dtype=np.float64))


def a_score(**overrides: float) -> ForecastScore:
    defaults = dict(
        points=100,
        mae=1.0,
        rmse=1.0,
        wape=0.20,
        smape=0.2,
        bias=0.0,
        mase=0.5,
        mape=0.2,
        mape_excluded_points=0,
    )
    defaults.update(overrides)
    return ForecastScore(**defaults)  # type: ignore[arg-type]


# ── Series construction ──────────────────────────────────────────────


def test_a_gap_in_an_aggregate_series_is_an_error_not_a_zero() -> None:
    """Filling it would teach the model a trough that never happened."""
    observations = {date(2026, 1, 5): 10.0, date(2026, 1, 7): 12.0}
    with pytest.raises(ValueError, match="pipeline failure"):
        build_series("revenue", observations, gap_policy=GapPolicy.ERROR)


def test_a_gap_in_a_demand_series_is_genuinely_a_zero() -> None:
    observations = {date(2026, 1, 5): 3.0, date(2026, 1, 7): 1.0}
    series = build_series("AC-1|S1", observations, gap_policy=GapPolicy.ZERO)

    assert len(series) == 3
    assert series.values[1] == 0.0


def test_short_history_is_refused_rather_than_fitted() -> None:
    short = TimeSeries("test", START, np.ones(10))
    with pytest.raises(InsufficientHistoryError, match="weekly cycles"):
        short.require_history()


def test_non_zero_share_identifies_intermittent_demand() -> None:
    sparse = TimeSeries("sparse", START, np.array([0.0] * 27 + [5.0]))
    assert sparse.non_zero_share == pytest.approx(1 / 28)


# ── Explainability is exact, not approximate ─────────────────────────


def test_ridge_explanation_reconstructs_its_own_prediction() -> None:
    """The property that separates a reason from a story."""
    series = weekly_series()
    model = RidgeForecaster().fit(series, horizon=7)

    for step in range(1, 8):
        explanation = model.explain(series, horizon=step)
        assert explanation.reconstructs(), f"h={step} does not add up"


def test_ridge_explanation_matches_the_prediction_it_explains() -> None:
    series = weekly_series()
    model = RidgeForecaster().fit(series, horizon=5)
    predictions = {p.horizon: p.point for p in model.predict(series)}

    for step, point in predictions.items():
        assert model.explain(series, horizon=step).prediction == pytest.approx(point)


def test_baseline_explanations_also_reconstruct() -> None:
    """Not exempt: a baseline that cannot explain itself is still opaque."""
    series = weekly_series()
    for model in (SeasonalNaive().fit(series, horizon=7), MovingAverage().fit(series, horizon=7)):
        assert model.explain(series, horizon=3).reconstructs()


def test_explanation_ranks_by_absolute_effect_keeping_sign() -> None:
    series = weekly_series()
    explanation = RidgeForecaster().fit(series, horizon=3).explain(series, horizon=3)
    top = explanation.top(3)

    magnitudes = [abs(c.effect) for c in top]
    assert magnitudes == sorted(magnitudes, reverse=True)


def test_global_importance_is_a_distribution_over_features() -> None:
    series = weekly_series()
    importance = RidgeForecaster().fit(series, horizon=3).global_importance(3)
    assert sum(importance.values()) == pytest.approx(1.0)


def test_ridge_finds_the_weekly_signal_that_is_actually_there() -> None:
    """A sanity check on the fit itself, not just its plumbing."""
    series = weekly_series()
    importance = RidgeForecaster().fit(series, horizon=1).global_importance(1)
    weekday_weight = sum(v for k, v in importance.items() if k.startswith("dow_"))
    assert weekday_weight > 0.1


# ── Intervals ────────────────────────────────────────────────────────


def test_conformal_band_covers_at_least_its_nominal_level() -> None:
    """The finite-sample guarantee, checked on the residuals that built it."""
    rng = np.random.default_rng(11)
    residuals = {1: rng.normal(0, 5, size=40)}
    band = residual_quantile_band(residuals, level=0.8)

    low, high = band[1]
    covered = np.mean((residuals[1] >= low) & (residuals[1] <= high))
    assert covered >= 0.8


def test_a_plain_percentile_would_undercover_where_conformal_does_not() -> None:
    """The bug this replaced, pinned so it cannot come back.

    With 12 residuals, numpy's 10th percentile sits between the second and
    third smallest, so four of twelve fall outside an "80%" band.
    """
    rng = np.random.default_rng(3)
    sample = rng.normal(0, 5, size=12)

    naive_low, naive_high = np.quantile(sample, 0.1), np.quantile(sample, 0.9)
    naive_coverage = np.mean((sample >= naive_low) & (sample <= naive_high))

    low, high = residual_quantile_band({1: sample}, level=0.8)[1]
    conformal_coverage = np.mean((sample >= low) & (sample <= high))

    assert naive_coverage < 0.8
    assert conformal_coverage >= 0.8


def test_thin_residual_sets_widen_to_the_observed_range() -> None:
    sample = np.array([-3.0, -1.0, 2.0, 4.0])
    assert sample.size < MIN_RESIDUALS_FOR_QUANTILES

    low, high = residual_quantile_band({1: sample}, level=0.8)[1]
    assert (low, high) == (-3.0, 4.0)


def test_band_rejects_a_nonsensical_level() -> None:
    with pytest.raises(ValueError, match="level"):
        residual_quantile_band({1: np.zeros(10)}, level=1.5)


def test_a_prediction_cannot_have_an_inverted_band() -> None:
    with pytest.raises(ValueError, match="above upper"):
        Prediction(horizon=1, point=10.0, lower=12.0, upper=8.0)


# ── Persistence ──────────────────────────────────────────────────────


def test_models_round_trip_through_json_unchanged() -> None:
    series = weekly_series()
    original = RidgeForecaster().fit(series, horizon=5)
    original.bands = {1: (-2.0, 2.0)}

    revived = RidgeForecaster.from_dict(json.loads(json.dumps(original.to_dict())))

    assert [p.point for p in revived.predict(series)] == [p.point for p in original.predict(series)]


def test_baselines_round_trip_too() -> None:
    series = weekly_series()
    for cls in (SeasonalNaive, MovingAverage):
        original = cls().fit(series, horizon=4)
        revived = cls.from_dict(json.loads(json.dumps(original.to_dict())))
        assert [p.point for p in revived.predict(series)] == [
            p.point for p in original.predict(series)
        ]


def test_registry_refuses_an_artefact_that_is_not_json(tmp_path: Path) -> None:
    """The security boundary, asserted.

    Loading a pickle executes code, so a model directory would become a
    code-execution path for anyone who can write to it.
    """
    registry = ModelRegistry(tmp_path)

    class Unstorable:
        name = "unstorable"
        model_class = "model"

        def to_dict(self) -> dict[str, object]:
            return {"callback": lambda: None}

    with pytest.raises(TypeError, match="never execute code"):
        registry.save(target="revenue", model=Unstorable(), card=_card("revenue"))


def test_saved_artefacts_are_readable_json(tmp_path: Path) -> None:
    registry = ModelRegistry(tmp_path)
    series = weekly_series()
    directory = registry.save(
        target="revenue", model=SeasonalNaive().fit(series, horizon=3), card=_card("revenue")
    )

    payload = json.loads((directory / "model.json").read_text())
    assert payload["kind"] == "seasonal_naive"


def test_registry_keeps_one_champion_per_series_not_per_target(tmp_path: Path) -> None:
    """Demand trains thousands of series; keying on target alone loses them all."""
    registry = ModelRegistry(tmp_path)
    series = weekly_series()
    model = SeasonalNaive().fit(series, horizon=3)

    for key in ("AC-1010|S2001", "AC-1010|S2002"):
        registry.save(target="demand", model=model, card=_card("demand", series_key=key))
        registry.promote("demand", key, "v1", reason="test")

    assert registry.champion_version("demand", "AC-1010|S2001") == "v1"
    assert registry.champion_version("demand", "AC-1010|S2002") == "v1"
    assert registry.versions("demand", "AC-1010|S2001") == ["v1"]


def test_version_stamps_sort_chronologically() -> None:
    assert new_version(date(2026, 1, 5)) < new_version(date(2026, 6, 5))


def _card(target: str, series_key: str | None = None) -> ModelCard:
    return ModelCard(
        target=target,
        series_key=series_key or target,
        model_name="seasonal_naive_w4",
        model_class="baseline",
        version="v1",
        created_at="2026-07-21",
        training_start="2026-01-05",
        training_end="2026-07-21",
        training_days=140,
        horizon=14,
        data_snapshot_id="kpi:2026-07-21:140",
        feature_names=[],
        metrics={},
        challenger_accepted=False,
        promotion_reason="test",
    )


# ── The promotion gate ───────────────────────────────────────────────


def test_a_clear_winner_is_promoted() -> None:
    decision = decide_promotion(
        champion_name="seasonal_naive_w4",
        champion=a_score(wape=0.20),
        challenger_name="ridge",
        challenger=a_score(wape=0.10, mase=0.5),
    )
    assert decision.promoted
    assert "clears" in decision.reason


def test_a_marginal_winner_is_rejected() -> None:
    """Inside the margin, the difference is which weeks landed in the window."""
    decision = decide_promotion(
        champion_name="seasonal_naive_w4",
        champion=a_score(wape=0.20),
        challenger_name="ridge",
        challenger=a_score(wape=0.19, mase=0.5),
    )
    assert not decision.promoted
    assert decision.improvement < MIN_IMPROVEMENT


def test_a_model_that_cannot_beat_seasonal_naive_is_rejected_outright() -> None:
    decision = decide_promotion(
        champion_name="seasonal_naive_w4",
        champion=a_score(wape=0.90),
        challenger_name="ridge",
        challenger=a_score(wape=0.10, mase=1.4),
    )
    assert not decision.promoted
    assert "MASE" in decision.reason


def test_a_brilliant_score_on_too_few_points_is_rejected() -> None:
    decision = decide_promotion(
        champion_name="seasonal_naive_w4",
        champion=a_score(wape=0.50),
        challenger_name="ridge",
        challenger=a_score(wape=0.01, mase=0.1, points=3),
    )
    assert not decision.promoted
    assert "evidence floor" in decision.reason
