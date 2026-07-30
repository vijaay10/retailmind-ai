"""Windows, manifests, retry policy, and the quality gate — pure logic."""

from datetime import date

import pytest

from ingestion.core.errors import (
    ErrorClass,
    ParseError,
    SourceUnavailableError,
    WarehouseError,
)
from ingestion.core.retry import with_retries
from ingestion.domain.manifest import PartitionManifest, SourceFile, file_checksum
from ingestion.domain.schema import DriftFinding, DriftKind
from ingestion.domain.window import Window
from quality.gate import BatchStats, QualityGate, volume_band
from quality.rules import CATALOG, Severity

# ── Windows ──────────────────────────────────────────────────────────


def test_window_is_half_open() -> None:
    window = Window(date(2026, 7, 1), date(2026, 7, 4))
    assert window.days == 3
    assert window.partitions == ("2026-07-01", "2026-07-02", "2026-07-03")
    assert window.contains(date(2026, 7, 3))
    assert not window.contains(date(2026, 7, 4))  # end is exclusive


def test_single_day_window() -> None:
    window = Window.for_day(date(2026, 7, 21))
    assert window.days == 1
    assert window.partitions == ("2026-07-21",)


def test_trailing_window_covers_the_late_arrival_period() -> None:
    window = Window.trailing(date(2026, 7, 22), days=35)
    assert window.days == 35
    assert window.end == date(2026, 7, 22)


def test_empty_or_inverted_windows_are_rejected() -> None:
    with pytest.raises(ValueError, match="must be after"):
        Window(date(2026, 7, 2), date(2026, 7, 1))
    with pytest.raises(ValueError):
        Window(date(2026, 7, 1), date(2026, 7, 1))


# ── Manifests ────────────────────────────────────────────────────────


def test_manifest_round_trip(tmp_path) -> None:
    manifest = PartitionManifest(
        source="pos",
        table="sales",
        partition="2026-07-21",
        schema_version="1.0",
        schema_fingerprint="abc123",
        connector_version="1.0",
        rows_read=100,
        rows_rejected=2,
        rows_landed=98,
        source_files=[SourceFile(name="a.csv", bytes=10, checksum="deadbeef")],
    )
    manifest.write(tmp_path)

    loaded = PartitionManifest.read(tmp_path)
    assert loaded is not None
    assert loaded.rows_landed == 98
    assert loaded.source_files[0].checksum == "deadbeef"
    assert loaded.reject_rate == pytest.approx(0.02)


def test_uncommitted_partition_reads_as_none(tmp_path) -> None:
    """No manifest means the partition does not exist — the commit protocol."""
    (tmp_path / "part-000.parquet").write_bytes(b"data but no manifest")
    assert PartitionManifest.read(tmp_path) is None


def test_checksum_detects_content_change(tmp_path) -> None:
    path = tmp_path / "f.csv"
    path.write_text("a,b\n1,2\n")
    original = file_checksum(path)

    path.write_text("a,b\n1,2\n")
    assert file_checksum(path) == original  # identical content, identical digest

    path.write_text("a,b\n1,3\n")
    assert file_checksum(path) != original


# ── Retry policy (ETL §22) ───────────────────────────────────────────


def test_retryable_error_is_retried_then_succeeds() -> None:
    attempts = {"n": 0}

    def flaky() -> str:
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise SourceUnavailableError("sftp down")
        return "ok"

    result = with_retries(flaky, name="t", sleep=lambda _: None, jitter=lambda: 0.5)
    assert result == "ok"
    assert attempts["n"] == 3


def test_deterministic_failure_is_not_retried() -> None:
    """Same input, same failure — retrying a parse error only wastes time."""
    attempts = {"n": 0}

    def broken() -> None:
        attempts["n"] += 1
        raise ParseError("malformed header")

    with pytest.raises(ParseError):
        with_retries(broken, name="t", sleep=lambda _: None)
    assert attempts["n"] == 1


def test_retries_are_exhausted_and_then_reraise() -> None:
    with pytest.raises(WarehouseError):
        with_retries(
            lambda: (_ for _ in ()).throw(WarehouseError("locked")),
            name="t",
            max_attempts=3,
            sleep=lambda _: None,
            jitter=lambda: 0.5,
        )


def test_backoff_grows_between_attempts() -> None:
    delays: list[float] = []
    with pytest.raises(SourceUnavailableError):
        with_retries(
            lambda: (_ for _ in ()).throw(SourceUnavailableError("down")),
            name="t",
            max_attempts=4,
            base_seconds=1.0,
            sleep=delays.append,
            jitter=lambda: 0.5,
        )
    assert delays == sorted(delays) and delays[0] < delays[-1]


def test_error_classes_declare_their_retryability() -> None:
    assert SourceUnavailableError("x").retryable
    assert not ParseError("x").retryable
    assert ParseError("x").error_class is ErrorClass.PARSE_ERROR


# ── Volume band (ETL §8) ─────────────────────────────────────────────


def test_volume_band_needs_history_before_it_judges() -> None:
    """Refusing to judge beats judging on two data points."""
    assert volume_band([100, 105], tolerance=0.35) is None


def test_volume_band_is_robust_to_a_single_spike() -> None:
    """Median/MAD, not mean/stdev: one Black Friday must not widen the band
    until it accepts anything."""
    history = [1000, 1010, 990, 1005, 995, 50_000]
    band = volume_band(history, tolerance=0.35)
    assert band is not None
    low, high = band
    assert low <= 1000 <= high
    assert high < 10_000  # the spike did not drag the ceiling up with it


def test_volume_band_widens_for_stable_sources() -> None:
    """Zero MAD would otherwise reject any variation at all."""
    band = volume_band([1000] * 8, tolerance=0.2)
    assert band is not None
    low, high = band
    assert low < 1000 < high


# ── Quality gate (ETL §8, §17) ───────────────────────────────────────


def _gate(schema) -> QualityGate:
    return QualityGate(schema, reject_rate_threshold=0.005)


def _stats(**overrides) -> BatchStats:
    base = dict(
        rows_read=1000,
        rows_rejected=0,
        rows_landed=1000,
        business_dates={date(2026, 7, 21)},
        files_expected=10,
        files_arrived=10,
    )
    return BatchStats(**{**base, **overrides})


def test_clean_batch_passes(minimal_schema) -> None:
    verdict = _gate(minimal_schema).evaluate(_stats(), drift=[], expected_dates={date(2026, 7, 21)})
    assert verdict.passed
    assert not verdict.failed_rule_ids


def test_missing_column_blocks_and_short_circuits(minimal_schema) -> None:
    """No point measuring volume on a batch whose schema is already broken."""
    drift = [DriftFinding(DriftKind.MISSING_COLUMN, "amount", True, "gone")]
    verdict = _gate(minimal_schema).evaluate(
        _stats(), drift=drift, expected_dates={date(2026, 7, 21)}
    )
    assert not verdict.passed
    assert verdict.failed_rule_ids == ["QR-SCH-001"]
    assert len(verdict.results) == 1  # stopped immediately


def test_new_column_does_not_block(minimal_schema) -> None:
    drift = [DriftFinding(DriftKind.NEW_COLUMN, "loyalty_id", False, "undeclared")]
    assert (
        _gate(minimal_schema)
        .evaluate(_stats(), drift=drift, expected_dates={date(2026, 7, 21)})
        .passed
    )


def test_incomplete_file_set_blocks(minimal_schema) -> None:
    """Partial store coverage looks exactly like a sales decline."""
    verdict = _gate(minimal_schema).evaluate(
        _stats(files_arrived=7, files_expected=10),
        drift=[],
        expected_dates={date(2026, 7, 21)},
    )
    assert "QR-CMP-004" in verdict.failed_rule_ids


def test_wrong_business_date_blocks(minimal_schema) -> None:
    """Loading yesterday's file as today's data is undetectable downstream."""
    verdict = _gate(minimal_schema).evaluate(
        _stats(business_dates={date(2026, 7, 20)}),
        drift=[],
        expected_dates={date(2026, 7, 21)},
    )
    assert "QR-FRS-003" in verdict.failed_rule_ids


def test_reject_flood_turns_row_problems_into_a_batch_incident(minimal_schema) -> None:
    verdict = _gate(minimal_schema).evaluate(
        _stats(rows_rejected=200, rows_landed=800),
        drift=[],
        expected_dates={date(2026, 7, 21)},
    )
    assert "QR-REJ-011" in verdict.failed_rule_ids


def test_a_few_rejects_are_data_not_an_incident(minimal_schema) -> None:
    verdict = _gate(minimal_schema).evaluate(
        _stats(rows_rejected=2, rows_landed=998),
        drift=[],
        expected_dates={date(2026, 7, 21)},
    )
    assert verdict.passed


def test_stale_fx_rate_blocks_the_batch(minimal_schema) -> None:
    verdict = _gate(minimal_schema).evaluate(
        _stats(fx_missing_rows=5), drift=[], expected_dates={date(2026, 7, 21)}
    )
    assert "QR-FX-041" in verdict.failed_rule_ids


def test_duplicate_rate_warns_without_blocking(minimal_schema) -> None:
    verdict = _gate(minimal_schema).evaluate(
        _stats(duplicates_collapsed=50), drift=[], expected_dates={date(2026, 7, 21)}
    )
    assert verdict.passed
    assert "QR-DUP-020" in [w.rule.id for w in verdict.warnings]


def test_volume_collapse_blocks(minimal_schema) -> None:
    verdict = _gate(minimal_schema).evaluate(
        _stats(rows_read=10),
        drift=[],
        expected_dates={date(2026, 7, 21)},
        volume_history=[1000, 1010, 990, 1005, 995],
    )
    assert "QR-VOL-002" in verdict.failed_rule_ids


# ── Rule catalog ─────────────────────────────────────────────────────


def test_every_rule_documents_why_it_exists() -> None:
    for rule in CATALOG.values():
        assert rule.rationale, f"{rule.id} has no stated rationale"
        assert rule.severity in (Severity.BLOCKING, Severity.WARNING)


def test_rule_ids_are_unique() -> None:
    ids = [rule.id for rule in CATALOG.values()]
    assert len(ids) == len(set(ids))
