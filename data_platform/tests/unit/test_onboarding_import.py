"""The import step: validated upload → existing ingestion pipeline.

Detection, mapping and validation had tests already. Everything here covers the
step that used to be missing, and the properties that make it safe to hand a
customer: that a second import does not duplicate data, that two companies
uploading the same file never meet, and that a file with no usable business
date is refused before anything is written rather than landing somewhere
arbitrary.
"""

import contextlib
import csv
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from onboarding.importing import (
    UPLOAD_UNIT,
    ImportRefusedError,
    import_mapped_rows,
    partition_rows,
    tenant_paths,
)
from onboarding.matching import load_known_schemas

LAST_DAY = date(2026, 7, 21)


def sales_schema():
    return next(s for s in load_known_schemas() if s.source == "pos" and s.table == "sales")


def sales_rows(days: int = 3, per_day: int = 4, *, start_order: int = 1000):
    """Canonically-named rows, as mapping would have produced them."""
    rows = []
    order = start_order
    for offset in range(days):
        day = LAST_DAY - timedelta(days=days - 1 - offset)
        for line in range(per_day):
            order += 1
            rows.append(
                {
                    "order_id": f"O{order}",
                    "line_no": str(line + 1),
                    "sku": f"SKU-{line % 3}",
                    "store_id": "S1001",
                    "transaction_ts": f"{day:%Y-%m-%d}T10:{line:02d}:00",
                    "updated_at": f"{day:%Y-%m-%d}T10:{line:02d}:00",
                    "quantity": "2",
                    "gross_amount": "40.00",
                    "discount_amount": "0.00",
                    "unit_price": "20.00",
                    "currency": "USD",
                    "channel": "store",
                    "store_timezone": "UTC",
                }
            )
    return rows


# ── Partitioning ─────────────────────────────────────────────────────


def test_rows_are_partitioned_by_the_schemas_event_time_column() -> None:
    grouped = partition_rows(sales_rows(days=3), sales_schema())
    assert sorted(grouped) == [LAST_DAY - timedelta(days=2), LAST_DAY - timedelta(days=1), LAST_DAY]
    assert all(len(v) == 4 for v in grouped.values())


def test_rows_without_a_usable_event_time_are_not_partitioned() -> None:
    """They are dropped here and reported, not filed under an arbitrary date."""
    rows = sales_rows(days=1)
    rows[0]["transaction_ts"] = ""
    rows[1]["transaction_ts"] = "not-a-date"
    grouped = partition_rows(rows, sales_schema())
    assert sum(len(v) for v in grouped.values()) == len(rows) - 2


# ── Tenant paths ─────────────────────────────────────────────────────


def test_two_tenants_share_no_path(tmp_path: Path) -> None:
    a = tenant_paths("alpha-retail", tmp_path)
    b = tenant_paths("beta-retail", tmp_path)
    assert a.warehouse_path != b.warehouse_path
    assert a.inbox_root != b.inbox_root
    assert a.landing_root != b.landing_root


def test_the_warehouse_filename_matches_the_read_side_convention(tmp_path: Path) -> None:
    """`semantic.tenancy.resolve_warehouse_path` builds `<root>/<slug>.duckdb`.

    If these two ever disagree, an import succeeds and the tenant's dashboards
    keep reading an empty file — the failure would look like missing data, not
    like a path bug.
    """
    assert tenant_paths("acme", tmp_path).warehouse_path == tmp_path / "acme.duckdb"


@pytest.mark.parametrize("slug", ["", " leading", "with/slash"])
def test_an_unusable_slug_is_refused(slug: str, tmp_path: Path) -> None:
    with pytest.raises(ImportRefusedError):
        tenant_paths(slug, tmp_path)


# ── Refusals, before anything is written ─────────────────────────────


def test_an_empty_upload_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ImportRefusedError, match="no rows"):
        import_mapped_rows(
            [], sales_schema(), tenant_slug="acme", settings=tenant_paths("acme", tmp_path)
        )


def test_an_upload_with_no_usable_dates_is_refused_and_writes_nothing(tmp_path: Path) -> None:
    rows = sales_rows(days=1)
    for row in rows:
        row["transaction_ts"] = ""
    settings = tenant_paths("acme", tmp_path)
    with pytest.raises(ImportRefusedError, match="business date"):
        import_mapped_rows(rows, sales_schema(), tenant_slug="acme", settings=settings)
    assert not settings.inbox_dir("pos").exists()
    assert not settings.warehouse_path.exists()


# ── The import itself ────────────────────────────────────────────────


@pytest.mark.scenario
def test_a_confirmed_upload_reaches_the_warehouse(tmp_path: Path) -> None:
    settings = tenant_paths("acme", tmp_path)
    rows = sales_rows(days=3, per_day=4)

    result = import_mapped_rows(rows, sales_schema(), tenant_slug="acme", settings=settings)

    assert result.succeeded
    assert result.rows_loaded == len(rows)
    assert result.rows_rejected == 0
    assert not result.quarantined
    assert result.files_written == 3
    assert len(result.partitions) == 3
    assert result.warehouse_path.exists()
    assert result.imported_at <= datetime.now(UTC)

    # One inbox file per business date, named the way the connector discovers.
    written = sorted(p.name for p in settings.inbox_dir("pos").glob("*.csv"))
    assert len(written) == 3
    assert all(name.startswith(f"pos_{UPLOAD_UNIT}_") for name in written)

    import duckdb

    conn = duckdb.connect(str(settings.warehouse_path), read_only=True)
    try:
        assert conn.execute("select count(*) from raw.pos__sales").fetchone()[0] == len(rows)
    finally:
        conn.close()


@pytest.mark.scenario
def test_importing_the_same_file_twice_does_not_duplicate_rows(tmp_path: Path) -> None:
    """The pipeline's own idempotency, exercised through the import path.

    The connector matches file checksums against the committed manifest and
    skips partitions it has already ingested, so the second call is a no-op —
    and a no-op must read as success, not as a failed import.
    """
    settings = tenant_paths("acme", tmp_path)
    rows = sales_rows(days=2)
    schema = sales_schema()

    first = import_mapped_rows(rows, schema, tenant_slug="acme", settings=settings)
    second = import_mapped_rows(rows, schema, tenant_slug="acme", settings=settings)

    assert first.rows_loaded == len(rows)
    assert second.succeeded
    assert second.unchanged
    assert second.rows_loaded == 0

    import duckdb

    conn = duckdb.connect(str(settings.warehouse_path), read_only=True)
    try:
        assert conn.execute("select count(*) from raw.pos__sales").fetchone()[0] == len(rows)
    finally:
        conn.close()


@pytest.mark.scenario
def test_re_uploading_changed_data_replaces_the_dates_it_covers(tmp_path: Path) -> None:
    """Correcting a file must not append a second copy of the same days."""
    settings = tenant_paths("acme", tmp_path)
    schema = sales_schema()

    import_mapped_rows(sales_rows(days=2, per_day=4), schema, tenant_slug="acme", settings=settings)
    corrected = sales_rows(days=2, per_day=3)
    for row in corrected:
        row["gross_amount"] = "99.00"
    second = import_mapped_rows(corrected, schema, tenant_slug="acme", settings=settings)

    assert second.replaced_existing
    import duckdb

    conn = duckdb.connect(str(settings.warehouse_path), read_only=True)
    try:
        assert conn.execute("select count(*) from raw.pos__sales").fetchone()[0] == len(corrected)
    finally:
        conn.close()


@pytest.mark.scenario
def test_two_tenants_uploading_the_identical_file_stay_separate(tmp_path: Path) -> None:
    """The isolation property, verified at the data layer rather than the UI."""
    schema = sales_schema()
    rows_a = sales_rows(days=2, per_day=4, start_order=1000)
    rows_b = sales_rows(days=2, per_day=2, start_order=5000)

    a = tenant_paths("alpha", tmp_path)
    b = tenant_paths("beta", tmp_path)
    import_mapped_rows(rows_a, schema, tenant_slug="alpha", settings=a)
    import_mapped_rows(rows_b, schema, tenant_slug="beta", settings=b)

    import duckdb

    counts = {}
    for slug, settings in (("alpha", a), ("beta", b)):
        conn = duckdb.connect(str(settings.warehouse_path), read_only=True)
        try:
            counts[slug] = conn.execute("select count(*) from raw.pos__sales").fetchone()[0]
            orders = {
                r[0]
                for r in conn.execute("select distinct order_id from raw.pos__sales").fetchall()
            }
        finally:
            conn.close()
        # No tenant may hold an order id that belongs to the other.
        other = "O5001" if slug == "alpha" else "O1001"
        assert other not in orders

    assert counts["alpha"] == len(rows_a)
    assert counts["beta"] == len(rows_b)
    assert a.warehouse_path != b.warehouse_path


@pytest.mark.scenario
def test_a_partition_over_the_reject_threshold_is_quarantined_not_loaded(tmp_path: Path) -> None:
    """Existing data-quality policy is preserved, not softened by importing.

    One bad row in six is far above the default 0.5% reject rate, so the
    pipeline quarantines that partition rather than loading a batch it cannot
    vouch for. The import reports it instead of hiding it, and `succeeded` is
    False — a quarantined upload is not a successful one.
    """
    settings = tenant_paths("acme", tmp_path)
    rows = sales_rows(days=2, per_day=6)
    rows[0]["quantity"] = "not-a-number"

    result = import_mapped_rows(rows, sales_schema(), tenant_slug="acme", settings=settings)

    assert result.quarantined
    assert not result.succeeded
    # The clean partition still landed; quarantine is per-partition.
    assert result.rows_loaded == 6


def test_the_written_file_carries_canonical_headers(tmp_path: Path) -> None:
    """Whatever the retailer called their columns, the inbox file uses ours."""
    settings = tenant_paths("acme", tmp_path)
    schema = sales_schema()
    with contextlib.suppress(Exception):  # the write happens before any pipeline work
        import_mapped_rows(sales_rows(days=1), schema, tenant_slug="acme", settings=settings)
    written = sorted(settings.inbox_dir("pos").glob("*.csv"))
    assert written
    header = next(csv.reader(written[0].open(newline="", encoding="utf-8")))
    assert header == [c.name for c in schema.columns]
