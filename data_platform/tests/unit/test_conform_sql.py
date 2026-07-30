"""The conform pipeline, executed against real DuckDB.

These are the tests that matter most: they assert the *behaviour* of parsing,
missing values, duplicates, dates, and currency on crafted rows with known
answers — the ETL design's "data-behavioral" layer.
"""

# ruff: noqa: S608 — test SQL is built from local fixtures, not user input.

from datetime import date

import duckdb
import pytest

from ingestion.core.duck import connect
from ingestion.domain.schema import SourceSchema
from ingestion.transform.currency import build_fx_lookup_sql
from ingestion.transform.sql import (
    REASON_BAD_DATE,
    REASON_BAD_TYPE,
    REASON_IMPOSSIBLE,
    REASON_MISSING_KEY,
    REASON_MISSING_MEASURE,
    UNKNOWN_MEMBER,
    build_conform_sql,
    build_rejects_sql,
)

SCHEMA = SourceSchema.from_dict(
    {
        "source": "pos",
        "table": "sales",
        "version": "1.0",
        "natural_key": ["order_id", "line_no"],
        "event_time_column": "ts",
        "timezone_column": "tz",
        "dedupe_tiebreaker": "updated_at",
        "columns": [
            {"name": "order_id", "type": "string", "class": "business_key", "normalize": "upper"},
            {"name": "line_no", "type": "integer", "class": "business_key"},
            {
                "name": "ts",
                "type": "timestamp",
                "class": "event_time",
                "formats": ["%Y-%m-%d %H:%M:%S"],
            },
            {
                "name": "updated_at",
                "type": "timestamp",
                "class": "descriptor",
                "required": False,
                "formats": ["%Y-%m-%d %H:%M:%S"],
            },
            {"name": "qty", "type": "decimal", "class": "measure", "min": -1000, "max": 1000},
            {
                "name": "amount",
                "type": "decimal",
                "class": "measure",
                "currency_column": "currency",
            },
            {"name": "currency", "type": "string", "class": "dimension", "normalize": "upper"},
            {"name": "channel", "type": "string", "class": "dimension", "normalize": "lower"},
            {"name": "note", "type": "string", "class": "descriptor", "required": False},
            {"name": "tz", "type": "string", "class": "dimension", "required": False},
        ],
    }
)

COLUMNS = "order_id, line_no, ts, updated_at, qty, amount, currency, channel, note, tz"


@pytest.fixture
def conn() -> duckdb.DuckDBPyConnection:
    connection = connect()
    yield connection
    connection.close()


def _rows_relation(rows: list[tuple[str, ...]]) -> str:
    """Build an all-VARCHAR relation, exactly as read_csv would produce."""
    values = ", ".join(
        "(" + ", ".join("NULL" if v is None else f"'{v}'" for v in row) + ")" for row in rows
    )
    return f"(SELECT * FROM (VALUES {values}) AS t({COLUMNS}))"


def _conform(conn: duckdb.DuckDBPyConnection, rows: list[tuple[str, ...]]) -> list[dict]:
    sql = build_conform_sql(SCHEMA, source_relation=_rows_relation(rows))
    cursor = conn.execute(sql)
    names = [d[0] for d in cursor.description or []]
    return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]


def _rejects(conn: duckdb.DuckDBPyConnection, rows: list[tuple[str, ...]]) -> list[dict]:
    sql = build_rejects_sql(SCHEMA, source_relation=_rows_relation(rows))
    cursor = conn.execute(sql)
    names = [d[0] for d in cursor.description or []]
    return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]


GOOD = (
    "ord-1",
    "1",
    "2026-07-21 10:00:00",
    "2026-07-21 10:00:00",
    "2",
    "50.00",
    "usd",
    "STORE",
    "ok",
    "UTC",
)


# ── Parsing and normalization ────────────────────────────────────────


def test_clean_row_passes_through_with_normalization(conn: duckdb.DuckDBPyConnection) -> None:
    (row,) = _conform(conn, [GOOD])
    assert row["order_id"] == "ORD-1"  # upper-normalized
    assert row["channel"] == "store"  # lower-normalized
    assert row["currency"] == "USD"
    assert row["qty"] == 2
    assert row["business_date"] == date(2026, 7, 21)


def test_whitespace_and_sentinel_nulls_are_canonicalized(conn: duckdb.DuckDBPyConnection) -> None:
    """'  N/A  ' is absence, not a string — trim must precede the comparison."""
    row = (
        "  ord-2  ",
        "1",
        "2026-07-21 10:00:00",
        None,
        "1",
        "10.00",
        "usd",
        "store",
        "  N/A  ",
        "UTC",
    )
    (result,) = _conform(conn, [row])
    assert result["order_id"] == "ORD-2"
    assert result["note"] is None


# ── Missing-value policy (ETL §9) ────────────────────────────────────


def test_missing_business_key_is_rejected(conn: duckdb.DuckDBPyConnection) -> None:
    row = (None, "1", "2026-07-21 10:00:00", None, "1", "10.00", "usd", "store", "", "UTC")
    assert _conform(conn, [row]) == []
    assert _rejects(conn, [row])[0]["_reject_reason"] == REASON_MISSING_KEY


def test_missing_required_measure_is_rejected_never_imputed(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    """A guessed amount is a lie in a ledger — reject instead."""
    row = ("ord-3", "1", "2026-07-21 10:00:00", None, "1", None, "usd", "store", "", "UTC")
    assert _rejects(conn, [row])[0]["_reject_reason"] == REASON_MISSING_MEASURE


def test_missing_dimension_becomes_visible_unknown(conn: duckdb.DuckDBPyConnection) -> None:
    """Analysis-safe and reconcilable — not a silent blank."""
    row = ("ord-4", "1", "2026-07-21 10:00:00", None, "1", "10.00", "usd", None, "", "UTC")
    (result,) = _conform(conn, [row])
    assert result["channel"] == UNKNOWN_MEMBER


def test_missing_optional_descriptor_stays_null(conn: duckdb.DuckDBPyConnection) -> None:
    row = ("ord-5", "1", "2026-07-21 10:00:00", None, "1", "10.00", "usd", "store", None, "UTC")
    (result,) = _conform(conn, [row])
    assert result["note"] is None


# ── Type and range screening ─────────────────────────────────────────


def test_uncastable_value_is_a_type_reject_not_a_missing_one(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    """Present-but-unparseable must be diagnosed differently from absent."""
    row = (
        "ord-6",
        "1",
        "2026-07-21 10:00:00",
        None,
        "not-a-number",
        "10.00",
        "usd",
        "store",
        "",
        "UTC",
    )
    assert _rejects(conn, [row])[0]["_reject_reason"] == REASON_BAD_TYPE


def test_unparseable_event_time_is_rejected(conn: duckdb.DuckDBPyConnection) -> None:
    row = ("ord-7", "1", "21/07/2026", None, "1", "10.00", "usd", "store", "", "UTC")
    assert _rejects(conn, [row])[0]["_reject_reason"] == REASON_BAD_DATE


def test_impossible_value_is_rejected_but_large_ones_are_kept(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    """Corruption screening, not outlier deletion: 999 units is a real order."""
    impossible = (
        "ord-8",
        "1",
        "2026-07-21 10:00:00",
        None,
        "5000",
        "10.00",
        "usd",
        "store",
        "",
        "UTC",
    )
    assert _rejects(conn, [impossible])[0]["_reject_reason"] == REASON_IMPOSSIBLE

    large = ("ord-9", "1", "2026-07-21 10:00:00", None, "999", "10.00", "usd", "store", "", "UTC")
    assert len(_conform(conn, [large])) == 1


def test_returns_are_kept_as_negative_quantities(conn: duckdb.DuckDBPyConnection) -> None:
    row = ("ord-10", "1", "2026-07-21 10:00:00", None, "-2", "-20.00", "usd", "store", "", "UTC")
    (result,) = _conform(conn, [row])
    assert result["qty"] == -2


# ── Duplicate detection (ETL §10) ────────────────────────────────────


def test_natural_key_duplicate_resolved_by_tiebreaker(conn: duckdb.DuckDBPyConnection) -> None:
    """Latest updated_at wins; the collapse is counted, not hidden."""
    rows = [
        (
            "ord-11",
            "1",
            "2026-07-21 10:00:00",
            "2026-07-21 10:00:00",
            "1",
            "10.00",
            "usd",
            "store",
            "old",
            "UTC",
        ),
        (
            "ord-11",
            "1",
            "2026-07-21 10:00:00",
            "2026-07-21 11:30:00",
            "3",
            "30.00",
            "usd",
            "store",
            "new",
            "UTC",
        ),
    ]
    (result,) = _conform(conn, rows)
    assert result["note"] == "new"
    assert result["qty"] == 3
    assert result["_duplicates_collapsed"] == 1


def test_distinct_keys_are_not_collapsed(conn: duckdb.DuckDBPyConnection) -> None:
    rows = [
        GOOD,
        (
            "ord-1",
            "2",
            "2026-07-21 10:00:00",
            "2026-07-21 10:00:00",
            "1",
            "5.00",
            "usd",
            "store",
            "second line",
            "UTC",
        ),
    ]
    assert len(_conform(conn, rows)) == 2


def test_rejected_rows_do_not_participate_in_dedup(conn: duckdb.DuckDBPyConnection) -> None:
    """A broken duplicate must not win the tiebreaker and erase a good row."""
    rows = [
        (
            "ord-12",
            "1",
            "2026-07-21 10:00:00",
            "2026-07-21 09:00:00",
            "1",
            "10.00",
            "usd",
            "store",
            "good",
            "UTC",
        ),
        (
            "ord-12",
            "1",
            "2026-07-21 10:00:00",
            "2026-07-21 23:00:00",
            "1",
            None,
            "usd",
            "store",
            "broken",
            "UTC",
        ),
    ]
    (result,) = _conform(conn, rows)
    assert result["note"] == "good"


# ── Date handling (ETL §16) ──────────────────────────────────────────


def test_business_date_is_derived_in_the_stores_timezone(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    """A 23:30 Los Angeles sale belongs to that local day, not UTC's tomorrow."""
    row = (
        "ord-13",
        "1",
        "2026-07-21 23:30:00",
        None,
        "1",
        "10.00",
        "usd",
        "store",
        "",
        "America/Los_Angeles",
    )
    (result,) = _conform(conn, [row])
    assert result["business_date"] == date(2026, 7, 21)


def test_ambiguous_date_formats_are_refused_not_guessed(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    """03/04/2026 could be March 4th or April 3rd. Guessing is unauditable."""
    row = ("ord-14", "1", "03/04/2026 10:00:00", None, "1", "10.00", "usd", "store", "", "UTC")
    assert _rejects(conn, [row])[0]["_reject_reason"] == REASON_BAD_DATE


# ── Currency standardization (ETL §15) ───────────────────────────────


def _with_fx(conn: duckdb.DuckDBPyConnection, rows: list[tuple[str, ...]]) -> list[dict]:
    conn.execute(
        """
        CREATE OR REPLACE TABLE fx_rates AS
        SELECT * FROM (VALUES
            (DATE '2026-07-21', 'EUR', 'USD', 1.10),
            (DATE '2026-07-17', 'GBP', 'USD', 1.30)
        ) AS t(rate_date, currency, base_currency, rate)
        """
    )
    conformed = build_conform_sql(SCHEMA, source_relation=_rows_relation(rows))
    sql = build_fx_lookup_sql(
        SCHEMA,
        source_relation=f"({conformed})",
        fx_relation="fx_rates",
        base_currency="USD",
        carry_forward_days=3,
    )
    cursor = conn.execute(sql)
    names = [d[0] for d in cursor.description or []]
    return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]


def test_base_currency_rows_pass_through_unconverted(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    (row,) = _with_fx(conn, [GOOD])
    assert row["amount"] == 50.0
    assert row["source_currency"] == "USD"
    assert not row["_fx_missing"]


def test_foreign_currency_converts_at_the_transaction_date_rate(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    """Transaction-date rates are what make backfills reproducible."""
    eur = ("ord-15", "1", "2026-07-21 10:00:00", None, "1", "100.00", "eur", "store", "", "UTC")
    (row,) = _with_fx(conn, [eur])
    assert row["amount"] == pytest.approx(110.0)
    assert row["amount_source_amount"] == 100.0  # original preserved
    assert row["source_currency"] == "EUR"


def test_rate_carries_forward_within_tolerance_and_is_flagged(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    """Weekends have no rates; reaching back is legitimate but must be visible."""
    gbp = ("ord-16", "1", "2026-07-20 10:00:00", None, "1", "100.00", "gbp", "store", "", "UTC")
    (row,) = _with_fx(conn, [gbp])
    assert row["amount"] == pytest.approx(130.0)
    assert row["_fx_carried_days"] == 3
    assert not row["_fx_missing"]


def test_stale_rate_beyond_tolerance_is_flagged_not_silently_applied(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    """Money math stops rather than using a week-old rate."""
    gbp = ("ord-17", "1", "2026-07-25 10:00:00", None, "1", "100.00", "gbp", "store", "", "UTC")
    (row,) = _with_fx(conn, [gbp])
    assert row["_fx_missing"] is True


def test_utc_instant_before_local_midnight_belongs_to_the_previous_day(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    """05:00 UTC is 22:00 the previous evening in Los Angeles.

    The mirror of the late-evening case: getting only one of these right means
    the conversion is happening in the wrong direction.
    """
    row = (
        "ord-18",
        "1",
        "2026-07-21 05:00:00",
        None,
        "1",
        "10.00",
        "usd",
        "store",
        "",
        "America/Los_Angeles",
    )
    (result,) = _conform(conn, [row])
    assert result["business_date"] == date(2026, 7, 20)


def test_missing_timezone_falls_back_to_the_declared_default(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    row = ("ord-19", "1", "2026-07-21 23:30:00", None, "1", "10.00", "usd", "store", "", None)
    (result,) = _conform(conn, [row])
    assert result["business_date"] == date(2026, 7, 21)
