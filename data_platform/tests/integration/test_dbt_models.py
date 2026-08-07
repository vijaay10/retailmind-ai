"""The warehouse's own contract: models build, and their tests hold.

**Why this is a test and not just a fixture side effect.** The API suites build
the warehouse to have something to query, so a broken dbt model surfaced there
as "fixture error: dbt build failed" on ten unrelated suites at once. That is a
terrible signal — it says nothing about which model, which column, or which
contract. Here the failure is what it actually is: a warehouse test.

`dbt build` runs each model and the tests attached to it in dependency order,
so a model that compiles but violates a uniqueness, not-null, or accepted-values
contract fails at the model that broke it.
"""

import os
import subprocess
import sys
from collections.abc import Iterator
from datetime import date, timedelta
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

REPO = Path(__file__).resolve().parents[3]
DBT_DIR = REPO / "data_platform" / "dbt"
LAST_DAY = date(2026, 7, 21)

#: Deliberately small. This suite asserts that the models and their contracts
#: hold, which is a property of the SQL rather than of the volume — and a
#: fourteen-day estate builds in well under a minute.
DAYS = 14
STORES = 4


@pytest.fixture(scope="module")
def built_warehouse(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[Path, Path]]:
    """Generate sources, ingest them, and return the warehouse and dbt target."""
    sys.path.insert(0, str(REPO / "data_platform"))

    from ingestion.connectors.csv_files import CsvFileConnector
    from ingestion.core.config import EtlSettings
    from ingestion.core.duck import connect
    from ingestion.domain.schema import SourceSchema
    from ingestion.domain.window import Window
    from ingestion.generators import (
        fulfilment,
        inventory_files,
        pos_files,
        purchase_orders,
        weather,
    )
    from ingestion.pipeline import IngestionPipeline

    root = tmp_path_factory.mktemp("dbt_models")
    settings = EtlSettings(
        landing_root=root / "lake",
        inbox_root=root / "inbox",
        warehouse_path=root / "wh.duckdb",
        reject_rate_threshold=0.10,
    )

    first_day = LAST_DAY - timedelta(days=DAYS - 1)
    for offset in range(DAYS):
        day = first_day + timedelta(days=offset)
        pos_files.generate_day(
            settings.inbox_dir("pos"),
            day,
            stores=STORES,
            lines_per_store=16,
            seed=7 + offset,
            history_start=first_day,
            history_end=LAST_DAY,
        )
        inventory_files.generate_day(
            settings.inbox_dir("inventory"), day, stores=STORES, skus_per_store=6, seed=600 + offset
        )
        purchase_orders.generate_day(
            settings.inbox_dir("purchasing"),
            day,
            stores=STORES,
            lines=10,
            seed=900 + offset,
            as_of=LAST_DAY,
        )
        weather.generate_day(
            settings.inbox_dir("weather"), day, seed=41 + offset, history_end=LAST_DAY
        )
        fulfilment.generate_day(
            settings.inbox_dir("fulfilment"),
            day,
            stores=STORES,
            seed=55 + offset,
            history_end=LAST_DAY,
        )

    schema_root = REPO / "data_platform" / "ingestion" / "schemas"
    window = Window(first_day, LAST_DAY + timedelta(days=1))
    connection = connect(settings.warehouse_path)
    for source, table, units in (
        ("pos", "sales", STORES),
        ("inventory", "positions", STORES),
        ("purchasing", "orders", 1),
        ("weather", "observations", 1),
        ("fulfilment", "deliveries", 1),
    ):
        schema = SourceSchema.from_yaml(schema_root / source / f"{table}.yml")
        connector = CsvFileConnector(
            schema=schema, settings=settings, connection=connection, expected_units=units
        )
        summary = IngestionPipeline(
            connector=connector, settings=settings, connection=connection
        ).run(window)
        assert not summary.quarantined, f"{source}: {summary.quarantined}"
    connection.close()

    yield settings.warehouse_path, root / "dbt_target"


def _dbt(step: str, warehouse: Path, target: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        ["uv", "run", "dbt", step, "--profiles-dir", ".", *extra],  # noqa: S607
        cwd=DBT_DIR,
        env={
            **os.environ,
            "RM_WAREHOUSE_DUCKDB_PATH": str(warehouse),
            "DBT_TARGET_PATH": str(target),
        },
        check=False,
        capture_output=True,
        text=True,
    )


def test_the_project_parses(built_warehouse: tuple[Path, Path]) -> None:
    """A parse failure is a syntax error in SQL nobody has run yet."""
    warehouse, target = built_warehouse
    result = _dbt("parse", warehouse, target)
    assert result.returncode == 0, result.stdout[-3000:]


def test_seeds_and_snapshots_load(built_warehouse: tuple[Path, Path]) -> None:
    """Snapshots carry the SCD2 history the marts join against; a broken one
    silently drops every historical attribute."""
    warehouse, target = built_warehouse
    for step in ("seed", "snapshot"):
        result = _dbt(step, warehouse, target)
        assert result.returncode == 0, f"dbt {step}:\n{result.stdout[-3000:]}"


def test_every_model_builds_and_its_tests_hold(built_warehouse: tuple[Path, Path]) -> None:
    """The contract test.

    `dbt build` runs models and their attached tests in dependency order, so a
    model that compiles but breaks a uniqueness, not-null or accepted-values
    contract fails at the model that broke it — rather than surfacing as a
    fixture error on ten unrelated API suites.
    """
    warehouse, target = built_warehouse
    _dbt("seed", warehouse, target)
    _dbt("snapshot", warehouse, target)

    result = _dbt("build", warehouse, target)
    assert result.returncode == 0, result.stdout[-6000:]

    # A build that runs zero tests is a build with no contracts, which passes
    # for the wrong reason.
    assert "PASS" in result.stdout


def test_the_semantic_views_the_api_queries_exist(built_warehouse: tuple[Path, Path]) -> None:
    """The API reads views, never marts directly. A renamed view breaks every
    endpoint at once, and this is the cheapest place to notice."""
    warehouse, target = built_warehouse
    _dbt("seed", warehouse, target)
    _dbt("snapshot", warehouse, target)
    _dbt("build", warehouse, target)

    sys.path.insert(0, str(REPO / "data_platform"))
    from ingestion.core.duck import connect

    connection = connect(warehouse, read_only=True)
    views = {
        row[0]
        for row in connection.execute(
            "SELECT table_name FROM information_schema.views "
            "WHERE table_schema = 'analytics_semantic'"
        ).fetchall()
    }
    connection.close()

    assert views, "the semantic layer published no views"
    assert any(name.startswith("v_mart_") for name in views)
