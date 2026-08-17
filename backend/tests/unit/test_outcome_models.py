"""Outcome-measurement contracts.

These objects carry the numbers that say whether a recommendation was right.
They shipped with no tests at all, which is the worst combination for this
particular module: every value here is arithmetic that ends up in a row
somebody later reads as evidence, and arithmetic with no test is a claim with
no support.

What is asserted is behaviour rather than shape — the window arithmetic that
decides *which days* count, the rounding that decides what a stored figure
says, and the flattening that decides what reaches the database.
"""

from datetime import UTC, date, datetime, timedelta

from app.services.outcomes.models import (
    BaselineCalculation,
    ImpactMeasurement,
    MeasurementResult,
    MeasurementWindow,
    ObservationResult,
    OutcomeRecord,
)

DECISION_DAY = date(2026, 7, 1)


def window(horizon: int = 7, *, decision: date = DECISION_DAY) -> MeasurementWindow:
    return MeasurementWindow(
        decision_date=decision,
        horizon_days=horizon,
        baseline_start=decision - timedelta(days=horizon),
        baseline_end=decision - timedelta(days=1),
        observation_start=decision,
        observation_end=decision + timedelta(days=horizon),
    )


def baseline() -> BaselineCalculation:
    return BaselineCalculation(
        method="comparable_period",
        value=1000.456,
        confidence="medium",
        limitations=["short baseline window"],
        metadata={"periods": 2},
    )


def observation() -> ObservationResult:
    return ObservationResult(
        value=1250.789, data_completeness=0.987654, confounding_events=["promotion overlap"]
    )


def impact() -> ImpactMeasurement:
    return ImpactMeasurement(
        baseline_value=1000.456,
        observed_value=1250.789,
        realized_impact=250.333,
        expected_impact=200.0,
        absolute_error=50.333,
        realization_ratio=1.251665,
        direction_correct=True,
    )


def result(horizon: int = 7) -> MeasurementResult:
    return MeasurementResult(
        decision_key="abc123",
        horizon_days=horizon,
        window=window(horizon),
        baseline=baseline(),
        observation=observation(),
        impact=impact(),
        measurement_confidence="medium",
        limitations=["short baseline window", "promotion overlap"],
        measured_at=datetime(2026, 7, 9, 12, 0, tzinfo=UTC),
    )


# ── Window arithmetic ────────────────────────────────────────────────


def test_day_counts_are_inclusive_of_both_ends() -> None:
    """Off-by-one here silently changes every per-day figure derived from it."""
    w = window(horizon=7)
    assert w.baseline_days == 7
    assert w.observation_days == 8  # decision day through decision + 7


def test_a_single_day_window_counts_one_day_not_zero() -> None:
    w = MeasurementWindow(
        decision_date=DECISION_DAY,
        horizon_days=1,
        baseline_start=DECISION_DAY - timedelta(days=1),
        baseline_end=DECISION_DAY - timedelta(days=1),
        observation_start=DECISION_DAY,
        observation_end=DECISION_DAY,
    )
    assert w.baseline_days == 1
    assert w.observation_days == 1


def test_a_window_is_mature_only_once_its_observation_period_has_closed() -> None:
    """Measuring early reports an outcome for days that have not happened."""
    today = date.today()
    closed = MeasurementWindow(
        decision_date=today - timedelta(days=30),
        horizon_days=7,
        baseline_start=today - timedelta(days=37),
        baseline_end=today - timedelta(days=31),
        observation_start=today - timedelta(days=30),
        observation_end=today - timedelta(days=23),
    )
    still_open = MeasurementWindow(
        decision_date=today,
        horizon_days=7,
        baseline_start=today - timedelta(days=7),
        baseline_end=today - timedelta(days=1),
        observation_start=today,
        observation_end=today + timedelta(days=7),
    )
    assert closed.is_mature
    assert not still_open.is_mature


def test_a_window_closing_today_is_mature() -> None:
    """The boundary case: `>=`, not `>`."""
    today = date.today()
    w = MeasurementWindow(
        decision_date=today - timedelta(days=7),
        horizon_days=7,
        baseline_start=today - timedelta(days=14),
        baseline_end=today - timedelta(days=8),
        observation_start=today - timedelta(days=7),
        observation_end=today,
    )
    assert w.is_mature


# ── Serialisation: what actually gets stored ─────────────────────────


def test_baseline_rounds_money_to_two_places_and_keeps_its_caveats() -> None:
    d = baseline().as_dict()
    assert d["value"] == 1000.46
    assert d["method"] == "comparable_period"
    assert d["confidence"] == "medium"
    # Limitations must survive serialisation — a baseline that loses its
    # caveats reads as more certain than it is.
    assert d["limitations"] == ["short baseline window"]
    assert d["metadata"] == {"periods": 2}


def test_observation_keeps_four_places_on_completeness() -> None:
    """Completeness is a ratio, not money — two places would erase 0.9876."""
    d = observation().as_dict()
    assert d["value"] == 1250.79
    assert d["data_completeness"] == 0.9877
    assert d["confounding_events"] == ["promotion overlap"]


def test_impact_serialises_every_figure_a_reader_would_compare() -> None:
    d = impact().as_dict()
    assert d["baseline_value"] == 1000.46
    assert d["observed_value"] == 1250.79
    assert d["realized_impact"] == 250.33
    assert d["expected_impact"] == 200.0
    assert d["absolute_error"] == 50.33
    assert d["realization_ratio"] == 1.2517
    assert d["direction_correct"] is True


def test_an_unmeasurable_ratio_stays_none_rather_than_becoming_zero() -> None:
    """Expected impact of zero has no ratio. Zero would read as total failure."""
    d = ImpactMeasurement(
        baseline_value=100.0,
        observed_value=120.0,
        realized_impact=20.0,
        expected_impact=0.0,
        absolute_error=20.0,
        realization_ratio=None,
        direction_correct=True,
    ).as_dict()
    assert d["realization_ratio"] is None


def test_a_wrong_direction_is_recorded_as_such() -> None:
    d = ImpactMeasurement(
        baseline_value=100.0,
        observed_value=80.0,
        realized_impact=-20.0,
        expected_impact=15.0,
        absolute_error=35.0,
        realization_ratio=-1.3333,
        direction_correct=False,
    ).as_dict()
    assert d["direction_correct"] is False
    assert d["realized_impact"] == -20.0


def test_the_full_result_serialises_dates_as_iso_and_nests_its_parts() -> None:
    d = result().as_dict()
    assert d["decision_key"] == "abc123"
    assert d["horizon_days"] == 7
    assert d["window"]["decision_date"] == "2026-07-01"
    assert d["window"]["baseline_start"] == "2026-06-24"
    assert d["window"]["observation_end"] == "2026-07-08"
    assert d["measured_at"].startswith("2026-07-09T12:00")
    assert set(d) >= {"baseline", "observation", "impact", "measurement_confidence"}
    assert d["limitations"] == ["short baseline window", "promotion overlap"]


# ── Flattening for persistence ───────────────────────────────────────


def test_a_measurement_flattens_into_a_persistable_record() -> None:
    record = OutcomeRecord.from_measurement("rec-1", "abc123", result())

    assert isinstance(record, OutcomeRecord)
    assert record.recommendation_id == "rec-1"
    assert record.decision_key == "abc123"
    assert record.horizon_days == 7
    assert record.baseline_method == "comparable_period"
    assert record.baseline_value == 1000.456
    assert record.observed_value == 1250.789
    assert record.realized_impact == 250.333
    assert record.expected_impact == 200.0
    assert record.direction_correct is True
    assert record.measurement_confidence == "medium"


def test_the_windows_survive_flattening_unchanged() -> None:
    """The row must still say which days it measured."""
    record = OutcomeRecord.from_measurement("rec-1", "abc123", result())
    assert record.baseline_window_start == date(2026, 6, 24)
    assert record.baseline_window_end == date(2026, 6, 30)
    assert record.observation_window_start == date(2026, 7, 1)
    assert record.observation_window_end == date(2026, 7, 8)


def test_limitations_reach_the_row_rather_than_being_dropped() -> None:
    """A stored outcome without its caveats is a number nobody can qualify."""
    record = OutcomeRecord.from_measurement("rec-1", "abc123", result())
    assert isinstance(record.limitations, str)
    assert "short baseline window" in record.limitations
    assert "promotion overlap" in record.limitations


def test_a_measurement_with_no_limitations_flattens_to_an_empty_string() -> None:
    clean = MeasurementResult(
        decision_key="abc123",
        horizon_days=7,
        window=window(),
        baseline=BaselineCalculation(
            method="pre_decision", value=10.0, confidence="high", limitations=[], metadata={}
        ),
        observation=ObservationResult(value=12.0, data_completeness=1.0, confounding_events=[]),
        impact=impact(),
        measurement_confidence="high",
        limitations=[],
        measured_at=datetime(2026, 7, 9, tzinfo=UTC),
    )
    record = OutcomeRecord.from_measurement("rec-2", "abc123", clean)
    assert record.limitations == ""
    assert record.measurement_confidence == "high"


def test_every_supported_horizon_produces_a_coherent_window() -> None:
    for horizon in (1, 7, 14, 30):
        w = window(horizon)
        assert w.observation_days == horizon + 1
        assert w.baseline_end < w.observation_start
        record = OutcomeRecord.from_measurement("rec", "key", result(horizon))
        assert record.horizon_days == horizon
