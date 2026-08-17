"""What a calibration report admits about itself.

The limitations list is the honesty surface of calibration: it is what stops a
scoreboard built from eleven outcomes reading like one built from a thousand.
The branching that produces it had no direct test, so a caveat could stop being
emitted without anything going red.

Also covers the confidence-band filter, which exists because passing a
categorical confidence ("high"/"medium"/"low") into a function expecting a
0.0-1.0 float raised TypeError on every call with real data. That fix is a
`isinstance` filter one line long and easy to delete by accident.
"""

from typing import Any

from app.services.calibration.models import CalibrationMetrics, GeneratorPerformance
from app.services.calibration.service import CalibrationService


def service() -> CalibrationService:
    """No repository is touched by the pure helpers under test."""
    return CalibrationService(repository=None)  # type: ignore[arg-type]


def outcome(horizon: int = 7, **extra: Any) -> dict[str, Any]:
    return {"horizon_days": horizon, "realized_impact": 10.0, "expected_impact": 12.0, **extra}


def performance(name: str, sample_size: int) -> GeneratorPerformance:
    return GeneratorPerformance(
        generator_name=name,
        metrics=CalibrationMetrics(
            sample_size=sample_size,
            mean_realization_ratio=0.9,
            median_realization_ratio=0.9,
            mean_absolute_error=1.0,
            mean_absolute_percentage_error=0.1,
            mean_bias=0.0,
            bias_percentage=0.0,
            direction_correct_count=sample_size,
            direction_accuracy=1.0,
            success_count=sample_size,
            success_rate=1.0,
            is_statistically_significant=sample_size >= 20,
        ),
        estimate_basis_breakdown={},
        confidence_bands=[],
    )


# ── Sample size ──────────────────────────────────────────────────────


def test_no_outcomes_says_so_and_says_nothing_else() -> None:
    """An empty scoreboard must not also imply the other caveats were checked."""
    limitations = service()._assess_limitations([], [])
    assert limitations == ["No measured outcomes available yet."]


def test_a_small_sample_is_declared_unreliable_with_its_size() -> None:
    limitations = service()._assess_limitations([outcome() for _ in range(5)], [])
    joined = " ".join(limitations)
    assert "N=5" in joined
    assert "not statistically reliable" in joined


def test_a_sample_of_twenty_is_no_longer_flagged_as_small() -> None:
    """The boundary: `< 20`, so twenty itself passes."""
    outcomes = [outcome(horizon=7 if i % 2 else 14) for i in range(20)]
    assert not any(
        "Small sample size" in limit for limit in service()._assess_limitations(outcomes, [])
    )


# ── Per-generator samples ────────────────────────────────────────────


def test_generators_with_thin_samples_are_named() -> None:
    """Naming them is the point — "some generators" is not actionable."""
    outcomes = [outcome(horizon=7 if i % 2 else 14) for i in range(30)]
    limitations = service()._assess_limitations(
        outcomes, [performance("reorder", 5), performance("markdown", 40)]
    )
    joined = " ".join(limitations)
    assert "reorder" in joined
    assert "markdown" not in joined


def test_no_generator_warning_when_every_generator_has_enough() -> None:
    outcomes = [outcome(horizon=7 if i % 2 else 14) for i in range(30)]
    limitations = service()._assess_limitations(
        outcomes, [performance("reorder", 25), performance("markdown", 40)]
    )
    assert not any("<20 samples" in limit for limit in limitations)


# ── Horizon coverage ─────────────────────────────────────────────────


def test_a_single_horizon_is_flagged_as_not_generalising() -> None:
    outcomes = [outcome(horizon=7) for _ in range(30)]
    limitations = service()._assess_limitations(outcomes, [])
    joined = " ".join(limitations)
    assert "H+7" in joined
    assert "may not generalize" in joined


def test_several_horizons_are_not_flagged() -> None:
    outcomes = [outcome(horizon=h) for h in (1, 7, 14, 30) for _ in range(8)]
    limitations = service()._assess_limitations(outcomes, [])
    assert not any("single horizon" in limit for limit in limitations)


def test_caveats_accumulate_rather_than_replacing_one_another() -> None:
    """A small, single-horizon sample with a thin generator has three problems."""
    limitations = service()._assess_limitations(
        [outcome(horizon=7) for _ in range(5)], [performance("reorder", 2)]
    )
    assert len(limitations) == 3


# ── Confidence-band filtering ────────────────────────────────────────


def test_categorical_confidence_is_filtered_out_rather_than_crashing() -> None:
    """The regression guard: `Recommendation.confidence` is high/medium/low.

    Before this filter, feeding those strings to the band segmenter raised
    TypeError and took down the whole calibration endpoint over one
    sub-metric. Returning no confidence-band calibration is the correct
    degradation.
    """
    outcomes = [outcome(confidence=band) for band in ("high", "medium", "low")]
    assert service()._calculate_confidence_calibration(outcomes) == []


def test_missing_confidence_is_also_filtered() -> None:
    assert service()._calculate_confidence_calibration([outcome(), outcome()]) == []


def test_numeric_confidence_is_kept_and_returned_in_ascending_order() -> None:
    outcomes = [
        outcome(confidence=0.9, realization_ratio=1.0, direction_correct=True),
        outcome(confidence=0.1, realization_ratio=0.5, direction_correct=False),
    ]
    calibrations = service()._calculate_confidence_calibration(outcomes)
    assert calibrations
    assert [c.confidence_min for c in calibrations] == sorted(
        c.confidence_min for c in calibrations
    )
