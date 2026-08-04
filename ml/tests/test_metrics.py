"""Metric correctness, against values worked out by hand.

Every assertion here is a number someone could recompute on paper. Testing a
metric against another implementation of the same metric proves only that two
things agree, and forecasting is a field where an entire team can agree on a
wrong denominator for years.
"""

import numpy as np
import pytest

from forecasting.metrics import (
    ForecastScore,
    bias,
    interval_coverage,
    mae,
    mape,
    mase,
    pinball_loss,
    rmse,
    score,
    seasonal_naive_scale,
    smape,
    wape,
)


def arr(*values: float) -> np.ndarray:
    return np.array(values, dtype=np.float64)


# ── Point accuracy ───────────────────────────────────────────────────


def test_mae_is_the_mean_absolute_gap() -> None:
    assert mae(arr(10, 20, 30), arr(12, 18, 33)) == pytest.approx((2 + 2 + 3) / 3)


def test_rmse_exceeds_mae_when_error_is_concentrated() -> None:
    """The gap between them is the diagnostic: it says the error is lumpy."""
    even = (arr(10, 10, 10, 10), arr(12, 12, 12, 12))
    lumpy = (arr(10, 10, 10, 10), arr(10, 10, 10, 18))

    assert rmse(*even) == pytest.approx(mae(*even))
    assert rmse(*lumpy) > mae(*lumpy)


def test_wape_weights_by_volume_where_mape_does_not() -> None:
    """The reason WAPE is the headline, in one assertion.

    One quiet day missed by 100% and one big day missed by 5%. MAPE averages
    the percentages and reports a disaster; WAPE weighs the pounds and reports
    what actually happened to the business.
    """
    actual = arr(1.0, 1000.0)
    predicted = arr(2.0, 1050.0)

    assert mape(actual, predicted).value == pytest.approx((1.0 + 0.05) / 2)
    assert wape(actual, predicted) == pytest.approx(51.0 / 1001.0)
    assert wape(actual, predicted) < 0.1


def test_wape_survives_a_zero_actual_that_would_break_mape() -> None:
    actual = arr(0.0, 100.0)
    predicted = arr(5.0, 90.0)
    assert np.isfinite(wape(actual, predicted))


# ── MAPE's exclusions are reported, not hidden ───────────────────────


def test_mape_reports_how_many_points_it_could_not_use() -> None:
    """Silently dropping undefined points turns "unmeasurable" into a number."""
    result = mape(arr(0.0, 0.0, 100.0), arr(5.0, 5.0, 90.0))

    assert result.excluded_points == 2
    assert result.total_points == 3
    assert not result.is_representative
    assert result.value == pytest.approx(0.1)


def test_mape_is_nan_when_every_actual_is_zero() -> None:
    result = mape(arr(0.0, 0.0), arr(1.0, 1.0))
    assert np.isnan(result.value)
    assert result.excluded_points == 2


def test_smape_stays_finite_where_mape_cannot() -> None:
    assert np.isfinite(smape(arr(0.0, 100.0), arr(5.0, 90.0)))


# ── MASE: the adoption gate ──────────────────────────────────────────


def test_seasonal_scale_uses_the_seasonal_difference() -> None:
    """A perfectly weekly series has zero seasonal difference, so no scale."""
    weekly = np.tile(arr(1, 2, 3, 4, 5, 6, 7), 4)
    assert np.isnan(seasonal_naive_scale(weekly, 7))


def test_mase_below_one_means_better_than_seasonal_naive() -> None:
    actual = arr(10, 10, 10, 10)
    good = arr(10.5, 9.5, 10.5, 9.5)
    poor = arr(15, 5, 15, 5)

    scale = 1.0
    assert mase(actual, good, scale=scale) < 1.0
    assert mase(actual, poor, scale=scale) > 1.0


def test_mase_is_scale_free_across_wildly_different_series() -> None:
    """Two units a day and forty thousand pounds a day must be comparable."""
    small = mase(arr(2, 2, 2), arr(3, 1, 3), scale=1.0)
    large = mase(arr(2e4, 2e4, 2e4), arr(3e4, 1e4, 3e4), scale=1e4)
    assert small == pytest.approx(large)


def test_mase_is_nan_rather_than_infinite_on_a_degenerate_scale() -> None:
    assert np.isnan(mase(arr(1, 2), arr(1, 2), scale=0.0))


# ── Bias ─────────────────────────────────────────────────────────────


def test_bias_is_signed_and_survives_what_mae_averages_away() -> None:
    """A consistent over-forecast is invisible to MAE and obvious to bias."""
    actual = arr(100, 100, 100)
    over = arr(105, 105, 105)
    alternating = arr(105, 95, 105)

    assert mae(actual, over) == pytest.approx(mae(actual, alternating), rel=0.7)
    assert bias(actual, over) == pytest.approx(0.05)
    assert abs(bias(actual, alternating)) < 0.03


# ── Intervals ────────────────────────────────────────────────────────


def test_coverage_counts_actuals_inside_the_band() -> None:
    actual = arr(1, 2, 3, 4)
    assert interval_coverage(actual, arr(0, 0, 0, 0), arr(5, 5, 5, 5)) == 1.0
    assert interval_coverage(actual, arr(0, 0, 0, 0), arr(5, 5, 0, 0)) == 0.5


def test_pinball_loss_punishes_the_width_coverage_ignores() -> None:
    """Coverage alone is gameable; an absurd band scores perfectly on it."""
    actual = arr(10, 10, 10)
    tight = arr(11, 11, 11)
    absurd = arr(1000, 1000, 1000)

    assert interval_coverage(actual, arr(0, 0, 0), absurd) == 1.0
    assert pinball_loss(actual, absurd, quantile=0.9) > pinball_loss(actual, tight, quantile=0.9)


def test_pinball_rejects_a_quantile_outside_the_open_unit_interval() -> None:
    with pytest.raises(ValueError, match="quantile"):
        pinball_loss(arr(1.0), arr(1.0), quantile=1.0)


# ── The scorecard ────────────────────────────────────────────────────


def test_score_reports_error_growing_with_horizon() -> None:
    """Pooling horizons hides whether day 14 is useless."""
    actual = arr(10, 10, 10, 10)
    predicted = arr(10.1, 10.1, 13, 13)
    horizons = np.array([1, 1, 14, 14])

    result = score(actual, predicted, scale=1.0, horizons=horizons)
    assert result.per_horizon_wape[1] < result.per_horizon_wape[14]


def test_score_flags_a_result_with_too_few_points() -> None:
    result = score(arr(1, 2), arr(1, 2), scale=1.0)
    assert not result.is_representative


def test_beats_seasonal_naive_is_exactly_mase_below_one() -> None:
    assert ForecastScore(
        points=50,
        mae=1,
        rmse=1,
        wape=0.1,
        smape=0.1,
        bias=0,
        mase=0.99,
        mape=0.1,
        mape_excluded_points=0,
    ).beats_seasonal_naive
    assert not ForecastScore(
        points=50,
        mae=1,
        rmse=1,
        wape=0.1,
        smape=0.1,
        bias=0,
        mase=1.0,
        mape=0.1,
        mape_excluded_points=0,
    ).beats_seasonal_naive


def test_metrics_refuse_mismatched_inputs() -> None:
    with pytest.raises(ValueError, match="shape mismatch"):
        mae(arr(1, 2, 3), arr(1, 2))
    with pytest.raises(ValueError, match="empty"):
        mae(arr(), arr())
