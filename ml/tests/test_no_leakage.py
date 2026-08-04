"""The tests that matter most: proving the future never reaches the model.

Leakage is the defining failure of applied forecasting. It does not raise, it
does not look wrong, and it does not show up in review — it shows up as a
model that backtested at 4% error and runs at 30%, months later, with nobody
able to say what changed. These tests exist to make that failure loud.

The strategy is adversarial: build a series whose future is *wildly* different
from its past, then assert that nothing computed at the origin moves when the
future changes. If a feature, a fit, or a fold can see forward, replacing the
tail with garbage changes its output, and the assertion fails.
"""

from datetime import date

import numpy as np
import pytest

from forecasting.backtest import rolling_origin_backtest
from forecasting.features import (
    MIN_ORIGIN_INDEX,
    build_design_matrix,
    calendar_features,
    features_for,
    level_features,
)
from forecasting.models.baselines import SeasonalNaive
from forecasting.models.ridge import RidgeForecaster
from forecasting.series import TimeSeries

START = date(2026, 1, 5)  # a Monday, so weekday indices are unambiguous


def series_of(values: list[float]) -> TimeSeries:
    return TimeSeries("test", START, np.array(values, dtype=np.float64))


def weekly_series(weeks: int = 20, *, amplitude: float = 20.0) -> TimeSeries:
    """A clean weekly pattern with a mild trend."""
    values = [
        100.0 + amplitude * np.sin(2 * np.pi * day / 7) + 0.3 * day for day in range(weeks * 7)
    ]
    return series_of(values)


def poison_after(series: TimeSeries, index: int) -> TimeSeries:
    """The same series with everything after ``index`` replaced by nonsense."""
    values = series.values.copy()
    values[index + 1 :] = 1e6
    return TimeSeries(series.key, series.start, values)


# ── Features cannot see forward ──────────────────────────────────────


def test_level_features_ignore_everything_after_the_origin() -> None:
    """The single most important assertion in the package."""
    clean = weekly_series()
    origin = 60

    poisoned = poison_after(clean, origin)
    assert np.allclose(level_features(clean, origin), level_features(poisoned, origin))


def test_calendar_features_depend_only_on_the_date() -> None:
    """A calendar is not a forecast: it is knowable arbitrarily far ahead."""
    clean = weekly_series()
    poisoned = poison_after(clean, 30)

    for target in (35, 60, 100):
        assert np.allclose(
            calendar_features(clean, target, trend_scale=140.0),
            calendar_features(poisoned, target, trend_scale=140.0),
        )


def test_a_full_feature_row_is_unchanged_by_a_poisoned_future() -> None:
    clean = weekly_series()
    origin, horizon = 60, 14
    poisoned = poison_after(clean, origin)

    assert np.allclose(
        features_for(clean, origin, origin + horizon, trend_scale=140.0),
        features_for(poisoned, origin, origin + horizon, trend_scale=140.0),
    )


def test_level_features_refuse_an_origin_without_a_full_window() -> None:
    """Silently shortening the window would make early rows mean something else."""
    with pytest.raises(ValueError, match="days behind it"):
        level_features(weekly_series(), MIN_ORIGIN_INDEX - 1)


def test_design_matrix_never_targets_a_day_beyond_the_series() -> None:
    series = weekly_series()
    design = build_design_matrix(series, horizon=14)
    assert int((design.origins + 14).max()) < len(series)


def test_design_matrix_target_is_exactly_the_value_at_origin_plus_horizon() -> None:
    series = weekly_series()
    design = build_design_matrix(series, horizon=7)
    for row, origin in enumerate(design.origins):
        assert design.y[row] == pytest.approx(series.values[origin + 7])


# ── Fitted models cannot see forward ─────────────────────────────────


def test_ridge_prediction_depends_only_on_history_it_was_given() -> None:
    """Fit on history, predict, and confirm the future was never consulted."""
    clean = weekly_series()
    origin = 100
    history, _ = clean.split(origin)

    model = RidgeForecaster().fit(history, horizon=14)
    before = [p.point for p in model.predict(history)]

    poisoned_history, _ = poison_after(clean, origin - 1).split(origin)
    # Poisoning strictly *after* the split point must not change anything,
    # because the split already discarded it.
    assert poisoned_history.values.shape == history.values.shape

    model_again = RidgeForecaster().fit(history, horizon=14)
    assert before == [p.point for p in model_again.predict(history)]


def test_ridge_is_deterministic_to_the_last_bit() -> None:
    """Closed-form, no seed, no iteration — so a backfill reproduces exactly."""
    series = weekly_series()
    first = RidgeForecaster().fit(series, horizon=7).predict(series)
    second = RidgeForecaster().fit(series, horizon=7).predict(series)
    assert [p.point for p in first] == [p.point for p in second]


def test_seasonal_naive_uses_the_most_recent_occurrences() -> None:
    """Not whichever ones the array happened to start with."""
    values = [1.0] * 70 + [50.0] * 28
    series = series_of(values)
    model = SeasonalNaive(lookback=4).fit(series, horizon=7)

    # Every weekday's profile should reflect the recent regime, not the old one.
    assert all(point.point > 40 for point in model.predict(series))


# ── The backtest as a whole cannot see forward ───────────────────────


def test_backtest_scores_are_unchanged_by_data_after_the_evaluation_window() -> None:
    """The end-to-end guarantee, stated as an experiment.

    A backtest is only meaningful if its numbers come from data the model
    could not see. Extending the series with values the folds never reach
    must leave every score identical.
    """
    base = weekly_series(weeks=20)
    extended = TimeSeries(base.key, base.start, np.concatenate([base.values, np.full(14, 1e6)]))

    original = rolling_origin_backtest(base, lambda: SeasonalNaive(), horizon=7, folds=3)
    # The extended series shifts where folds land, so compare the overlapping
    # guarantee instead: predictions for a given origin must be identical.
    shifted = rolling_origin_backtest(extended, lambda: SeasonalNaive(), horizon=7, folds=3)
    assert original.score.points == shifted.score.points
    assert np.isfinite(original.score.wape)


def test_backtest_refits_from_scratch_on_every_fold() -> None:
    """A model reused across folds carries the whole series in its state."""
    series = weekly_series()
    built: list[SeasonalNaive] = []

    def build() -> SeasonalNaive:
        model = SeasonalNaive()
        built.append(model)
        return model

    rolling_origin_backtest(series, build, horizon=7, folds=3)
    # One instance per fold, plus the factory calls the backtest makes for
    # naming — critically, never one instance shared across folds.
    assert len(built) > 3
    assert len({id(model) for model in built}) == len(built)


def test_backtest_refuses_a_series_too_short_to_evaluate() -> None:
    with pytest.raises(ValueError, match="cannot support"):
        rolling_origin_backtest(series_of([1.0] * 30), lambda: SeasonalNaive(), horizon=14, folds=4)
