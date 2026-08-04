"""Warehouse behavioural tests — the dbt build is run, then interrogated.

dbt's own tests assert schema-level invariants (uniqueness, referential
integrity, ranges). These assert the *semantics* that no generic test can see:
that the fiscal calendar matches published NRF dates, that as-was attribution
actually survives a dimension change, and that money is conserved from staging
through to every mart.

The suite builds the warehouse once per module from generated CSVs, so it
exercises the whole chain — ingestion → star → marts → semantic views — rather
than testing SQL in isolation.
"""

# ruff: noqa: S608 — assertions query fixed schema names built in this module;
# there is no user input anywhere in this file.

import shutil
import subprocess
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import duckdb
import pytest

from ingestion.connectors.csv_files import CsvFileConnector
from ingestion.core.config import EtlSettings
from ingestion.core.duck import connect
from ingestion.domain.schema import SourceSchema
from ingestion.domain.window import Window
from ingestion.generators import inventory_files, pos_files, purchase_orders
from ingestion.pipeline import IngestionPipeline

DBT_DIR = Path(__file__).resolve().parents[2] / "dbt"
BUSINESS_DAY = date(2026, 7, 21)

CORE = "analytics_analytics"
SEMANTIC = "analytics_semantic"
STAGING = "analytics_staging"


def _dbt(command: str, warehouse: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    """Run dbt against a throwaway warehouse."""
    return subprocess.run(  # noqa: S603
        ["uv", "run", "dbt", command, "--profiles-dir", ".", *extra],  # noqa: S607
        cwd=DBT_DIR,
        env={
            **_clean_env(),
            "RM_WAREHOUSE_DUCKDB_PATH": str(warehouse),
            "DBT_TARGET_PATH": str(warehouse.parent / "dbt_target"),
        },
        check=False,
        capture_output=True,
        text=True,
    )


def _clean_env() -> dict[str, str]:
    import os

    return dict(os.environ)


@pytest.fixture(scope="module")
def warehouse(tmp_path_factory: pytest.TempPathFactory) -> Iterator[duckdb.DuckDBPyConnection]:
    """Generate → ingest → dbt build, then hand back a read connection."""
    root = tmp_path_factory.mktemp("warehouse")
    settings = EtlSettings(
        landing_root=root / "lake",
        inbox_root=root / "inbox",
        warehouse_path=root / "wh.duckdb",
        reject_rate_threshold=0.10,
    )

    pos_files.generate_day(settings.inbox_dir("pos"), BUSINESS_DAY, stores=6, lines_per_store=25)
    inventory_files.generate_day(
        settings.inbox_dir("inventory"), BUSINESS_DAY, stores=6, skus_per_store=20
    )
    purchase_orders.generate_day(
        settings.inbox_dir("purchasing"), BUSINESS_DAY, stores=6, lines=40, as_of=BUSINESS_DAY
    )

    schema_root = Path(__file__).resolve().parents[2] / "ingestion/schemas"
    conn = connect(settings.warehouse_path)
    # Purchasing arrives as one file for the whole estate rather than one per
    # store, so its completeness check counts a single unit.
    for source, table, units in (
        ("pos", "sales", 6),
        ("inventory", "positions", 6),
        ("purchasing", "orders", 1),
    ):
        schema = SourceSchema.from_yaml(schema_root / source / f"{table}.yml")
        connector = CsvFileConnector(
            schema=schema, settings=settings, connection=conn, expected_units=units
        )
        summary = IngestionPipeline(connector=connector, settings=settings, connection=conn).run(
            Window.for_day(BUSINESS_DAY)
        )
        assert summary.status == "succeeded", f"{source} ingestion must succeed"
    conn.close()

    for step in ("seed", "snapshot"):
        result = _dbt(step, settings.warehouse_path)
        assert result.returncode == 0, f"dbt {step} failed:\n{result.stdout}"

    result = _dbt("build", settings.warehouse_path)
    assert result.returncode == 0, f"dbt build failed:\n{result.stdout}"

    read = connect(settings.warehouse_path, read_only=True)
    yield read
    read.close()
    shutil.rmtree(root, ignore_errors=True)


def _rows(conn: duckdb.DuckDBPyConnection, sql: str) -> list[tuple]:
    return conn.execute(sql).fetchall()


def _scalar(conn: duckdb.DuckDBPyConnection, sql: str):
    row = conn.execute(sql).fetchone()
    return row[0] if row else None


# ── Star schema shape ────────────────────────────────────────────────


def test_star_schema_objects_exist(warehouse: duckdb.DuckDBPyConnection) -> None:
    tables = {
        r[0]
        for r in _rows(
            warehouse,
            "SELECT table_name FROM information_schema.tables "
            f"WHERE table_schema IN ('{CORE}', '{SEMANTIC}')",
        )
    }
    for name in (
        "dim_date",
        "dim_product",
        "dim_store",
        "dim_channel",
        "fct_sales",
        "mart_sales_daily",
        "mart_kpi_daily",
        "v_fct_sales",
        "v_mart_kpi_daily",
        "dim_customer",
        "dim_promotion",
        "fct_inventory_daily",
        "mart_inventory_daily",
    ):
        assert name in tables, f"{name} missing from the warehouse"


def test_fact_grain_is_one_row_per_order_line(warehouse: duckdb.DuckDBPyConnection) -> None:
    """The grain *is* the design: a fanned-out fact inflates every measure."""
    total = _scalar(warehouse, f"SELECT count(*) FROM {CORE}.fct_sales")
    distinct = _scalar(
        warehouse,
        f"SELECT count(*) FROM (SELECT DISTINCT order_id, line_no FROM {CORE}.fct_sales)",
    )
    assert total == distinct


def test_every_fact_row_resolves_to_real_dimension_members(
    warehouse: duckdb.DuckDBPyConnection,
) -> None:
    """UNKNOWN is a valid destination, but a warehouse full of it is broken."""
    unknown = _scalar(
        warehouse,
        f"SELECT count(*) FROM {CORE}.fct_sales "
        "WHERE product_key = -1 OR store_key = -1 OR channel_key = -1",
    )
    assert unknown == 0


def test_dimension_keys_never_collide_with_reserved_members(
    warehouse: duckdb.DuckDBPyConnection,
) -> None:
    """Generated keys are non-negative by construction (the >> 1 in the macro),
    so they cannot collide with the -1/-2 sentinels."""
    for table, key in (("dim_product", "product_key"), ("dim_store", "store_key")):
        negatives = _scalar(
            warehouse, f"SELECT count(*) FROM {CORE}.{table} WHERE {key} < 0 AND {key} <> -1"
        )
        assert negatives == 0


# ── Money conservation (the highest-value check) ─────────────────────


def test_revenue_is_conserved_from_staging_through_to_marts(
    warehouse: duckdb.DuckDBPyConnection,
) -> None:
    """Catches join fanout, dedup overreach, and window slips in one assertion.

    Per-row tests structurally cannot see any of those; all three change the
    total.
    """
    staging = _scalar(warehouse, f"SELECT sum(net_amount) FROM {STAGING}.stg_pos__sales")
    fact = _scalar(warehouse, f"SELECT sum(net_amount) FROM {CORE}.fct_sales")
    sales_mart = _scalar(warehouse, f"SELECT sum(net_revenue) FROM {CORE}.mart_sales_daily")
    kpi_mart = _scalar(warehouse, f"SELECT sum(net_revenue) FROM {CORE}.mart_kpi_daily")

    assert fact == pytest.approx(staging, abs=0.01)
    assert sales_mart == pytest.approx(staging, abs=0.01)
    assert kpi_mart == pytest.approx(staging, abs=0.01)


def test_row_counts_are_conserved_from_staging_to_fact(
    warehouse: duckdb.DuckDBPyConnection,
) -> None:
    staging = _scalar(warehouse, f"SELECT count(*) FROM {STAGING}.stg_pos__sales")
    fact = _scalar(warehouse, f"SELECT count(*) FROM {CORE}.fct_sales")
    assert fact == staging


def test_margin_identity_holds_row_by_row(warehouse: duckdb.DuckDBPyConnection) -> None:
    broken = _scalar(
        warehouse,
        f"SELECT count(*) FROM {CORE}.fct_sales "
        "WHERE cogs_amount IS NOT NULL "
        "AND abs(margin_amount - (net_amount - cogs_amount)) > 0.01",
    )
    assert broken == 0


# ── 4-5-4 retail calendar ────────────────────────────────────────────


@pytest.mark.parametrize(
    ("fiscal_year", "expected_start"),
    [(2025, date(2025, 2, 2)), (2026, date(2026, 2, 1)), (2027, date(2027, 1, 31))],
)
def test_fiscal_year_starts_match_published_nrf_dates(
    warehouse: duckdb.DuckDBPyConnection, fiscal_year: int, expected_start: date
) -> None:
    """External facts, not something to derive and hope about.

    If these drift, every year-over-year comparison in the platform silently
    compares the wrong weeks.
    """
    actual = _scalar(
        warehouse,
        f"SELECT min(full_date) FROM {CORE}.dim_date WHERE fiscal_year = {fiscal_year}",
    )
    assert actual == expected_start


def test_fiscal_weeks_are_exactly_seven_days(warehouse: duckdb.DuckDBPyConnection) -> None:
    """A short week produces comparisons that look like performance changes."""
    bad = _rows(
        warehouse,
        f"""
        SELECT fiscal_year, fiscal_week, count(*) AS days
        FROM {CORE}.dim_date
        GROUP BY 1, 2 HAVING count(*) <> 7
        """,
    )
    assert bad == []


def test_fiscal_years_are_52_or_53_weeks(warehouse: duckdb.DuckDBPyConnection) -> None:
    lengths = {
        r[1]
        for r in _rows(
            warehouse,
            f"SELECT fiscal_year, count(DISTINCT fiscal_week) FROM {CORE}.dim_date GROUP BY 1",
        )
    }
    assert lengths <= {52, 53}


def test_454_period_pattern_within_each_quarter(warehouse: duckdb.DuckDBPyConnection) -> None:
    """The 4-5-4 split is the whole point of the calendar: periods within a
    quarter must contain 4, 5, and 4 weeks respectively."""
    pattern = _rows(
        warehouse,
        f"""
        SELECT fiscal_period, count(DISTINCT fiscal_week) AS weeks
        FROM {CORE}.dim_date
        WHERE fiscal_year = 2026
        GROUP BY 1 ORDER BY 1
        """,
    )
    assert [weeks for _, weeks in pattern[:3]] == [4, 5, 4]


def test_yoy_helper_lands_on_the_same_weekday(warehouse: duckdb.DuckDBPyConnection) -> None:
    """364 days, not 365: the alignment that keeps the weekday mix constant."""
    mismatched = _scalar(
        warehouse,
        f"""
        SELECT count(*) FROM {CORE}.dim_date d
        JOIN {CORE}.dim_date ly ON d.same_fiscal_week_last_year = ly.full_date
        WHERE d.day_of_week <> ly.day_of_week
        """,
    )
    assert mismatched == 0


# ── SCD2 behaviour ───────────────────────────────────────────────────


def test_scd2_dimensions_have_exactly_one_current_version(
    warehouse: duckdb.DuckDBPyConnection,
) -> None:
    for table, entity in (("dim_product", "sku"), ("dim_store", "store_id")):
        offenders = _rows(
            warehouse,
            f"""
            SELECT {entity} FROM {CORE}.{table}
            WHERE is_current GROUP BY 1 HAVING count(*) <> 1
            """,
        )
        assert offenders == [], f"{table} has entities without exactly one current version"


def test_scd2_first_version_is_backdated_so_history_resolves(
    warehouse: duckdb.DuckDBPyConnection,
) -> None:
    """The fix for the classic 'warehouse built after the facts' problem.

    dbt records valid_from as the moment the snapshot first *observed* a row.
    Without backdating version 1, every fact older than the first snapshot run
    fails the as-was predicate and lands on UNKNOWN.
    """
    earliest = _scalar(
        warehouse, f"SELECT min(valid_from) FROM {CORE}.dim_product WHERE version_number = 1"
    )
    assert earliest.year == 1900


def test_as_was_join_attributes_facts_to_the_version_valid_at_sale_time(
    warehouse: duckdb.DuckDBPyConnection,
) -> None:
    """Attribution must reflect the world as it was, not as it is now."""
    violations = _scalar(
        warehouse,
        f"""
        SELECT count(*) FROM {CORE}.fct_sales f
        JOIN {CORE}.dim_product p ON f.product_key = p.product_key
        WHERE p.product_key <> -1
          AND NOT (f.transaction_ts >= p.valid_from AND f.transaction_ts < p.valid_to)
        """,
    )
    assert violations == 0


# ── Marts and semantic layer ─────────────────────────────────────────


def test_kpi_mart_has_one_row_per_business_date(warehouse: duckdb.DuckDBPyConnection) -> None:
    total = _scalar(warehouse, f"SELECT count(*) FROM {CORE}.mart_kpi_daily")
    distinct = _scalar(
        warehouse, f"SELECT count(DISTINCT business_date) FROM {CORE}.mart_kpi_daily"
    )
    assert total == distinct


def test_kpi_ratios_are_recomputed_not_summed(warehouse: duckdb.DuckDBPyConnection) -> None:
    """AOV must equal revenue ÷ orders at this grain — averaging an average is
    the classic non-additive mistake."""
    row = _rows(
        warehouse,
        f"SELECT net_revenue, orders, aov, units_sold, asp FROM {CORE}.mart_kpi_daily LIMIT 1",
    )[0]
    revenue, orders, aov, units, asp = row
    assert float(aov) == pytest.approx(float(revenue) / orders, abs=0.01)
    assert float(asp) == pytest.approx(float(revenue) / float(units), abs=0.01)


def test_semantic_views_expose_flattened_dimensions(
    warehouse: duckdb.DuckDBPyConnection,
) -> None:
    """The app binds to these names; the star stays free to change beneath."""
    columns = {r[0] for r in _rows(warehouse, f"DESCRIBE {SEMANTIC}.v_fct_sales")}
    for expected in ("category", "region", "store_cluster", "fiscal_week", "net_amount"):
        assert expected in columns


def test_current_views_return_only_open_versions(
    warehouse: duckdb.DuckDBPyConnection,
) -> None:
    stale = _scalar(
        warehouse, f"SELECT count(*) FROM {SEMANTIC}.v_dim_product_current WHERE NOT is_current"
    )
    assert stale == 0


def test_sales_mart_aggregates_reconcile_with_the_semantic_view(
    warehouse: duckdb.DuckDBPyConnection,
) -> None:
    mart = _scalar(warehouse, f"SELECT sum(net_revenue) FROM {SEMANTIC}.v_mart_sales_daily")
    detail = _scalar(warehouse, f"SELECT sum(net_amount) FROM {SEMANTIC}.v_fct_sales")
    assert float(mart) == pytest.approx(float(detail), abs=0.01)


# ── Performance structures ───────────────────────────────────────────


def test_point_lookup_indexes_exist_on_the_fact(
    warehouse: duckdb.DuckDBPyConnection,
) -> None:
    """Indexes serve equality lookups (drill-down, evidence links); range
    scans on dates are served by the columnar layout instead."""
    indexes = {r[0] for r in _rows(warehouse, "SELECT index_name FROM duckdb_indexes()")}
    assert "ix_fct_sales_order_id" in indexes
    assert "ix_fct_sales_product_key" in indexes


def test_aggregation_mart_is_far_smaller_than_the_fact(
    warehouse: duckdb.DuckDBPyConnection,
) -> None:
    """The point of pre-aggregation: dashboards scan thousands of rows, not
    millions. If the mart approaches fact size, its grain is wrong."""
    fact_rows = _scalar(warehouse, f"SELECT count(*) FROM {CORE}.fct_sales")
    mart_rows = _scalar(warehouse, f"SELECT count(*) FROM {CORE}.mart_sales_daily")
    assert mart_rows < fact_rows
