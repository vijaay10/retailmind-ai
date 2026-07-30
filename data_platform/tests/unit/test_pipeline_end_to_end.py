"""The whole pipeline, on real CSV files, against a real DuckDB warehouse.

Runs discovery → conform → gate → land → load and asserts the guarantees the
design makes: idempotency, atomic commits, quarantine containment, reject
routing, and reconciliation. These are cheap enough to keep in the unit suite
because DuckDB is in-process and the fixtures are small.
"""

# ruff: noqa: S608 — test SQL is built from local fixtures, not user input.

import csv
from datetime import date
from pathlib import Path

import pytest

from ingestion.connectors.csv_files import CsvFileConnector
from ingestion.core.config import EtlSettings
from ingestion.core.duck import connect
from ingestion.domain.manifest import PartitionManifest
from ingestion.domain.schema import SourceSchema
from ingestion.domain.window import Window
from ingestion.pipeline import IngestionPipeline

HEADER = [
    "order_id",
    "line_no",
    "sku",
    "store_id",
    "transaction_ts",
    "updated_at",
    "quantity",
    "gross_amount",
    "discount_amount",
    "unit_price",
    "currency",
    "channel",
    "store_timezone",
    "promo_code",
    "customer_id",
    "cashier_id",
    "customer_email",
]


def _row(order_id: str, line_no: int = 1, **overrides: str) -> dict[str, str]:
    row = {
        "order_id": order_id,
        "line_no": str(line_no),
        "sku": "OW-1042",
        "store_id": "S2117",
        "transaction_ts": "2026-07-21 14:30:00",
        "updated_at": "2026-07-21 14:30:00",
        "quantity": "2",
        "gross_amount": "129.00",
        "discount_amount": "0.00",
        "unit_price": "64.50",
        "currency": "USD",
        "channel": "store",
        "store_timezone": "UTC",
        "promo_code": "",
        "customer_id": "CU-00001",
        "cashier_id": "C-9",
        "customer_email": "shopper@example.test",
    }
    return {**row, **overrides}


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=HEADER)
        writer.writeheader()
        writer.writerows(rows)


@pytest.fixture
def pipeline_env(tmp_path: Path, pos_schema: SourceSchema):
    """A pipeline wired to temp storage, with the FX table pre-seeded."""
    settings = EtlSettings(
        landing_root=tmp_path / "lake",
        inbox_root=tmp_path / "inbox",
        warehouse_path=tmp_path / "wh.duckdb",
        reject_rate_threshold=0.10,  # fixtures are tiny; one bad row is 10%
    )
    conn = connect(settings.warehouse_path)
    conn.execute(
        """
        CREATE TABLE fx_rates AS SELECT * FROM (VALUES
            (DATE '2026-07-21', 'EUR', 'USD', 1.10)
        ) AS t(rate_date, currency, base_currency, rate)
        """
    )
    connector = CsvFileConnector(
        schema=pos_schema, settings=settings, connection=conn, expected_units=1
    )
    pipeline = IngestionPipeline(connector=connector, settings=settings, connection=conn)
    yield pipeline, settings, conn, pos_schema
    conn.close()


WINDOW = Window.for_day(date(2026, 7, 21))


def test_happy_path_lands_and_loads(pipeline_env) -> None:
    pipeline, settings, conn, schema = pipeline_env
    _write_csv(
        settings.inbox_dir("pos") / "pos_S2117_20260721.csv",
        [_row("ORD-1"), _row("ORD-2"), _row("ORD-3")],
    )

    summary = pipeline.run(WINDOW)

    assert summary.status == "succeeded"
    assert summary.rows_read == 3
    assert summary.rows_loaded == 3

    rows = conn.execute("SELECT count(*) FROM raw.pos__sales").fetchone()
    assert rows and rows[0] == 3


def test_partition_is_committed_with_a_manifest(pipeline_env) -> None:
    """The manifest is the commit marker — no manifest, no partition."""
    pipeline, settings, _conn, schema = pipeline_env
    _write_csv(settings.inbox_dir("pos") / "pos_S2117_20260721.csv", [_row("ORD-1")])

    pipeline.run(WINDOW)

    manifest = PartitionManifest.read(settings.bronze_dir("pos", "sales", "2026-07-21"))
    assert manifest is not None
    assert manifest.rows_landed == 1
    assert manifest.schema_fingerprint == schema.fingerprint
    assert manifest.source_files[0].name == "pos_S2117_20260721.csv"


def test_rerunning_a_window_is_idempotent(pipeline_env) -> None:
    """The core guarantee: re-runs overwrite, they never double-count.

    This is what makes retries, late re-lands, and backfills all safe — they
    are the same operation with a different window.
    """
    pipeline, settings, conn, _schema = pipeline_env
    _write_csv(settings.inbox_dir("pos") / "pos_S2117_20260721.csv", [_row("ORD-1"), _row("ORD-2")])

    first = pipeline.run(WINDOW)
    after_first = conn.execute("SELECT count(*) FROM raw.pos__sales").fetchone()

    second = pipeline.run(WINDOW)
    after_second = conn.execute("SELECT count(*) FROM raw.pos__sales").fetchone()

    assert after_first == after_second == (2,)
    assert first.rows_loaded == 2
    # The second pass recognizes identical content and skips the work entirely.
    assert second.outcomes[0].status == "skipped"


def test_changed_file_relands_the_partition_as_a_correction(pipeline_env) -> None:
    """Same name, different checksum = a correction, not a duplicate."""
    pipeline, settings, conn, _schema = pipeline_env
    path = settings.inbox_dir("pos") / "pos_S2117_20260721.csv"

    _write_csv(path, [_row("ORD-1")])
    pipeline.run(WINDOW)

    _write_csv(path, [_row("ORD-1"), _row("ORD-2"), _row("ORD-3")])
    summary = pipeline.run(WINDOW)

    assert summary.outcomes[0].status == "loaded"
    rows = conn.execute("SELECT count(*) FROM raw.pos__sales").fetchone()
    assert rows == (3,)  # replaced, not appended


def test_bad_rows_are_rejected_while_good_rows_load(pipeline_env) -> None:
    """Row problems are data: the batch still publishes, minus the bad rows."""
    pipeline, settings, conn, _schema = pipeline_env
    _write_csv(
        settings.inbox_dir("pos") / "pos_S2117_20260721.csv",
        [
            _row("ORD-1"),
            _row("ORD-2"),
            _row("", line_no=1),  # missing business key
            _row("ORD-4", quantity="not-a-number"),  # uncastable
            *[_row(f"ORD-{i}") for i in range(10, 30)],
        ],
    )

    summary = pipeline.run(WINDOW)

    assert summary.status == "succeeded"
    assert summary.rows_rejected == 2
    assert summary.rows_loaded == 22

    rejects = settings.rejects_dir("pos", "sales", "2026-07-21") / "rejects.parquet"
    assert rejects.exists()
    reasons = conn.execute(
        f"SELECT DISTINCT _reject_reason FROM read_parquet('{rejects}')"
    ).fetchall()
    assert {r[0] for r in reasons} == {"missing_business_key", "unparseable_type"}


def test_reject_flood_quarantines_the_batch_and_publishes_nothing(pipeline_env) -> None:
    """Batch problems are incidents: the warehouse keeps yesterday's truth."""
    pipeline, settings, conn, _schema = pipeline_env
    _write_csv(
        settings.inbox_dir("pos") / "pos_S2117_20260721.csv",
        [_row("ORD-1"), _row("", line_no=1), _row("", line_no=2), _row("", line_no=3)],
    )

    summary = pipeline.run(WINDOW)

    assert summary.status == "quarantined"
    assert "QR-REJ-011" in summary.quarantined[0].failed_rules
    # Nothing landed and nothing loaded — the gate protected both.
    assert PartitionManifest.read(settings.bronze_dir("pos", "sales", "2026-07-21")) is None
    tables = conn.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name = 'pos__sales'"
    ).fetchone()
    assert tables == (0,)


def test_missing_column_quarantines_before_any_work(pipeline_env) -> None:
    """A broken contract stops at the boundary, cheaply."""
    pipeline, settings, _conn, _schema = pipeline_env
    path = settings.inbox_dir("pos") / "pos_S2117_20260721.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    reduced = [c for c in HEADER if c != "gross_amount"]
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=reduced)
        writer.writeheader()
        writer.writerow({k: v for k, v in _row("ORD-1").items() if k != "gross_amount"})

    summary = pipeline.run(WINDOW)

    assert summary.status == "quarantined"
    assert summary.quarantined[0].failed_rules == ["QR-SCH-001"]


def test_undeclared_column_lands_without_blocking(pipeline_env) -> None:
    """Bronze is as-received; the new column simply stays invisible downstream."""
    pipeline, settings, conn, _schema = pipeline_env
    path = settings.inbox_dir("pos") / "pos_S2117_20260721.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    extended = [*HEADER, "loyalty_id"]
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=extended)
        writer.writeheader()
        writer.writerow({**_row("ORD-1"), "loyalty_id": "L-1"})

    summary = pipeline.run(WINDOW)

    assert summary.status == "succeeded"
    columns = {row[0] for row in conn.execute("DESCRIBE raw.pos__sales").fetchall()}
    assert "loyalty_id" not in columns  # undeclared stays out of the warehouse


def test_foreign_currency_is_converted_and_the_original_kept(pipeline_env) -> None:
    pipeline, settings, conn, _schema = pipeline_env
    _write_csv(
        settings.inbox_dir("pos") / "pos_S2117_20260721.csv",
        [_row("ORD-EUR", currency="EUR", gross_amount="100.00")],
    )

    pipeline.run(WINDOW)

    row = conn.execute(
        "SELECT gross_amount, gross_amount_source_amount, source_currency FROM raw.pos__sales"
    ).fetchone()
    assert row is not None
    converted, original, currency = row
    assert float(converted) == pytest.approx(110.0)
    assert float(original) == pytest.approx(100.0)
    assert currency == "EUR"


def test_unmapped_currency_blocks_the_batch(pipeline_env) -> None:
    """Money math stops rather than converting at an imaginary rate."""
    pipeline, settings, _conn, _schema = pipeline_env
    _write_csv(
        settings.inbox_dir("pos") / "pos_S2117_20260721.csv",
        [_row("ORD-GBP", currency="GBP", gross_amount="100.00")],
    )

    summary = pipeline.run(WINDOW)

    assert summary.status == "quarantined"
    assert "QR-FX-041" in summary.quarantined[0].failed_rules


def test_duplicate_order_lines_collapse_with_the_latest_winning(pipeline_env) -> None:
    pipeline, settings, conn, _schema = pipeline_env
    _write_csv(
        settings.inbox_dir("pos") / "pos_S2117_20260721.csv",
        [
            _row("ORD-1", quantity="1", updated_at="2026-07-21 10:00:00"),
            _row("ORD-1", quantity="5", updated_at="2026-07-21 18:00:00"),
        ],
    )

    summary = pipeline.run(WINDOW)

    assert summary.rows_loaded == 1
    row = conn.execute("SELECT quantity FROM raw.pos__sales").fetchone()
    assert row is not None and float(row[0]) == 5.0


def test_empty_partition_is_reported_not_failed(pipeline_env) -> None:
    """Nothing arrived is a completeness fact, not a crash."""
    pipeline, settings, _conn, _schema = pipeline_env
    settings.inbox_dir("pos").mkdir(parents=True, exist_ok=True)

    summary = pipeline.run(WINDOW)

    assert summary.outcomes[0].status == "empty"
    assert summary.status == "succeeded"


def test_backfill_over_multiple_days_partitions_correctly(pipeline_env) -> None:
    """Each business date lands in its own partition and its own window load."""
    pipeline, settings, conn, _schema = pipeline_env
    for day in ("20260721", "20260722"):
        iso = f"{day[:4]}-{day[4:6]}-{day[6:]}"
        _write_csv(
            settings.inbox_dir("pos") / f"pos_S2117_{day}.csv",
            [_row(f"ORD-{day}", transaction_ts=f"{iso} 12:00:00", updated_at=f"{iso} 12:00:00")],
        )

    summary = pipeline.run(Window(date(2026, 7, 21), date(2026, 7, 23)))

    assert summary.rows_loaded == 2
    per_day = conn.execute(
        "SELECT business_date, count(*) FROM raw.pos__sales GROUP BY 1 ORDER BY 1"
    ).fetchall()
    assert per_day == [(date(2026, 7, 21), 1), (date(2026, 7, 22), 1)]
