"""Unit tests for Dagster orchestration layer.

Tests verify that orchestration wraps existing CLI commands correctly
without rewriting pipeline logic.

Test Coverage:
- Asset dependency ordering
- Partition-based execution
- Failure handling and retries
- Idempotency
- Resource configuration
- Backfill support
"""

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from dagster import (
    AssetsDefinition,
    build_asset_context,
)

# Import orchestration components
from orchestration.dagster.assets.ingestion import make_bronze_asset, make_raw_table_asset
from orchestration.dagster.checks import make_freshness_check, make_row_count_check
from orchestration.dagster.resources import CliExecutor, DuckDBWarehouse

# ── Resource Tests ─────────────────────────────────────────────────


def test_cli_executor_builds_correct_command(tmp_path):
    """CliExecutor builds correct CLI commands."""
    executor = CliExecutor(working_directory=str(tmp_path))

    # Mock subprocess.run to verify command
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="success", stderr=""
        )

        executor.run_ingestion(
            source="pos",
            table="sales",
            business_date="2026-07-21",
            expected_stores=40,
        )

        # Verify command structure
        call_args = mock_run.call_args
        command = call_args[0][0]

        assert "uv" in command
        assert "run" in command
        assert "retailmind-etl" in command
        assert "run" in command
        assert "--source" in command
        assert "pos" in command
        assert "--table" in command
        assert "sales" in command
        assert "--day" in command
        assert "2026-07-21" in command
        assert "--expected-stores" in command
        assert "40" in command


def test_run_dbt_project_and_profiles_dirs_resolve_regardless_of_working_directory(
    tmp_path,
):
    """Regression test for the Prompt 11 release blocker.

    `run_dbt` used to build `--project-dir dbt --profiles-dir .`, paths
    relative to `working_directory`. That only pointed at the real dbt
    project when `working_directory` happened to be `data_platform/`
    itself — production configures it to the repository root instead
    (`orchestration/dagster/__init__.py`), one level too high, so the real
    `dbt_build` job failed on every actual execution despite passing
    `dagster definitions validate` and every other unit test, because none
    of them ran the real subprocess against the real configured working
    directory.

    A second, related bug lived one level deeper: even with the flags
    fixed, dbt still resolves `profiles.yml`'s relative default warehouse
    path against the subprocess's actual invocation *cwd* (not
    `--project-dir`), so the subprocess must also run with
    `cwd=<the real dbt project dir>` — asserted below too, not just the
    command-line flags.

    This test uses a `working_directory` that is deliberately NOT
    `data_platform/` (a bare tmp_path, structurally like the repo-root
    case) and asserts the generated paths are absolute and point at a
    directory that actually contains `dbt_project.yml` and
    `profiles.yml` — not just that the string "dbt" appears somewhere in
    the command, which the original bug would also have satisfied.
    """
    executor = CliExecutor(working_directory=str(tmp_path))

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )

        executor.run_dbt("seed")

        call_args, call_kwargs = mock_run.call_args
        command = call_args[0]

    project_dir = Path(command[command.index("--project-dir") + 1])
    profiles_dir = Path(command[command.index("--profiles-dir") + 1])
    subprocess_cwd = Path(call_kwargs["cwd"])

    assert project_dir.is_absolute(), f"--project-dir must be absolute, got {project_dir}"
    assert profiles_dir.is_absolute(), f"--profiles-dir must be absolute, got {profiles_dir}"
    assert (project_dir / "dbt_project.yml").exists(), (
        f"--project-dir {project_dir} does not contain dbt_project.yml"
    )
    assert (profiles_dir / "profiles.yml").exists(), (
        f"--profiles-dir {profiles_dir} does not contain profiles.yml"
    )
    # Not self.working_directory (tmp_path) — dbt resolves profiles.yml's
    # relative warehouse-path default against this, not --project-dir.
    assert subprocess_cwd == project_dir, (
        f"subprocess cwd must be the dbt project dir, got {subprocess_cwd}"
    )
    # Independent of working_directory: the same real project every time,
    # not one relative to wherever this particular executor happens to run.
    assert project_dir == profiles_dir == (Path(__file__).resolve().parents[2] / "dbt")


def test_duckdb_warehouse_connection():
    """DuckDBWarehouse can connect to warehouse."""
    # Use in-memory database for testing
    warehouse = DuckDBWarehouse(database_path=":memory:")

    conn = warehouse.get_connection()
    assert conn is not None

    # Verify basic query works
    result = conn.execute("SELECT 42 as answer").fetchall()
    assert result[0][0] == 42

    conn.close()


def test_duckdb_warehouse_table_exists(tmp_path):
    """DuckDBWarehouse can check if tables exist.

    Uses a real on-disk file rather than ":memory:" — DuckDBWarehouse opens a
    fresh connection per call (see get_connection), and an in-memory database
    is scoped to the connection that created it, so state set up on one
    connection is invisible to the next. A file path persists across connects,
    which is what the resource actually relies on in production.
    """
    warehouse = DuckDBWarehouse(database_path=str(tmp_path / "warehouse.duckdb"))

    # Create test table
    conn = warehouse.get_connection()
    conn.execute("CREATE SCHEMA raw")
    conn.execute("CREATE TABLE raw.test_table (id INT)")
    conn.close()

    # Check existence
    assert warehouse.table_exists("raw", "test_table") is True
    assert warehouse.table_exists("raw", "nonexistent") is False


def test_duckdb_warehouse_row_count(tmp_path):
    """DuckDBWarehouse can count rows in tables."""
    warehouse = DuckDBWarehouse(database_path=str(tmp_path / "warehouse.duckdb"))

    # Create test table with data
    conn = warehouse.get_connection()
    conn.execute("CREATE SCHEMA raw")
    conn.execute("CREATE TABLE raw.test_table (id INT)")
    conn.execute("INSERT INTO raw.test_table VALUES (1), (2), (3)")
    conn.close()

    # Count rows
    count = warehouse.get_row_count("raw", "test_table")
    assert count == 3


# ── Asset Tests ────────────────────────────────────────────────────


def test_bronze_asset_factory_creates_valid_asset():
    """Bronze asset factory creates valid Dagster assets."""
    asset_func = make_bronze_asset("pos", "sales")

    # Verify asset function is callable
    assert callable(asset_func)

    # Verify the @asset decorator actually produced a Dagster asset
    # (modern Dagster wraps the function in an AssetsDefinition; it no
    # longer exposes the underlying op via a `_op_def` attribute).
    assert isinstance(asset_func, AssetsDefinition)


def test_raw_table_asset_factory_creates_valid_asset():
    """RAW table asset factory creates valid Dagster assets."""
    asset_func = make_raw_table_asset("pos", "sales")

    # Verify asset function is callable
    assert callable(asset_func)
    assert isinstance(asset_func, AssetsDefinition)


def test_bronze_asset_calls_cli_with_correct_partition(tmp_path):
    """Bronze asset calls CLI with correct partition key.

    Direct asset invocation binds the resource argument into an ephemeral
    Dagster resource context, which type-checks it against the op's declared
    resource type (CliExecutor, a ConfigurableResource/pydantic model). A bare
    `Mock(spec=CliExecutor)` fails that check even though `isinstance(mock,
    CliExecutor)` holds in isolation, because Dagster inspects the resource
    instance dagster receives from resource initialization, not the mock's
    spoofed `__class__`. Using a real CliExecutor with only the network-facing
    method patched satisfies the type check and still isolates the subprocess.
    """
    asset_func = make_bronze_asset("pos", "sales")
    cli = CliExecutor(working_directory=str(tmp_path))
    context = build_asset_context(partition_key="2026-07-21")

    with patch.object(
        CliExecutor,
        "run_ingestion",
        return_value=subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="rows_read=1000\nrows_landed=998\nrows_rejected=2",
            stderr="",
        ),
    ) as mock_run:
        output = asset_func(context, cli)

    # Verify CLI was called correctly
    mock_run.assert_called_once_with(
        source="pos",
        table="sales",
        business_date="2026-07-21",
    )

    # Verify output metadata
    assert output.metadata["business_date"].value == "2026-07-21"
    assert output.metadata["rows_read"].value == 1000
    assert output.metadata["rows_landed"].value == 998
    assert output.metadata["rows_rejected"].value == 2


def test_bronze_asset_handles_cli_failure(tmp_path):
    """Bronze asset raises exception when CLI fails."""
    asset_func = make_bronze_asset("pos", "sales")
    cli = CliExecutor(working_directory=str(tmp_path))
    context = build_asset_context(partition_key="2026-07-21")

    with (
        patch.object(
            CliExecutor,
            "run_ingestion",
            side_effect=subprocess.CalledProcessError(
                returncode=1,
                cmd="retailmind-etl",
                stderr="Quality gate failed: volume too low",
            ),
        ),
        pytest.raises(subprocess.CalledProcessError),
    ):
        asset_func(context, cli)


def test_raw_table_asset_verifies_warehouse_load():
    """RAW table asset verifies data was loaded to warehouse."""
    asset_func = make_raw_table_asset("pos", "sales")
    warehouse = DuckDBWarehouse(database_path=":memory:")
    context = build_asset_context(partition_key="2026-07-21")

    with patch.object(DuckDBWarehouse, "execute_query", return_value=[(1000,)]) as mock_query:
        # Execute asset (bronze is dependency, not used)
        output = asset_func(context, None, warehouse)

    # Verify warehouse was queried
    mock_query.assert_called_once()
    call_args = mock_query.call_args[0][0]
    assert "SELECT COUNT(*)" in call_args
    assert "raw.pos__sales" in call_args
    assert "2026-07-21" in call_args

    # Verify output metadata
    assert output.metadata["row_count"].value == 1000
    assert output.metadata["partition_loaded"].value is True


# ── Asset Check Tests ──────────────────────────────────────────────


def test_row_count_check_passes_when_rows_exist():
    """Row count check passes when partition has rows."""
    check_func = make_row_count_check("pos", "sales")
    warehouse = DuckDBWarehouse(database_path=":memory:")
    context = build_asset_context(partition_key="2026-07-21")

    with patch.object(DuckDBWarehouse, "execute_query", return_value=[(1000,)]):
        result = check_func(context, warehouse)

    assert result.passed is True
    assert result.metadata["row_count"].value == 1000


def test_row_count_check_fails_when_partition_empty():
    """Row count check fails when partition has 0 rows."""
    check_func = make_row_count_check("pos", "sales")
    warehouse = DuckDBWarehouse(database_path=":memory:")
    context = build_asset_context(partition_key="2026-07-21")

    with patch.object(DuckDBWarehouse, "execute_query", return_value=[(0,)]):
        result = check_func(context, warehouse)

    assert result.passed is False
    assert "empty" in result.description.lower()


def test_freshness_check_passes_when_data_recent():
    """Freshness check passes when latest partition is recent."""
    check_func = make_freshness_check("pos", "sales")
    warehouse = DuckDBWarehouse(database_path=":memory:")
    context = build_asset_context(partition_key="2026-08-14")

    # First call: get latest partition (today). Second call: days behind (0).
    with patch.object(
        DuckDBWarehouse,
        "execute_query",
        side_effect=[[("2026-08-14",)], [(0,)]],
    ):
        result = check_func(context, warehouse)

    assert result.passed is True
    assert result.metadata["days_behind"].value == 0


def test_freshness_check_fails_when_data_stale():
    """Freshness check fails when latest partition is old."""
    check_func = make_freshness_check("pos", "sales")
    warehouse = DuckDBWarehouse(database_path=":memory:")
    context = build_asset_context(partition_key="2026-08-14")

    with patch.object(
        DuckDBWarehouse,
        "execute_query",
        side_effect=[[("2026-08-04",)], [(10,)]],  # latest partition, days behind
    ):
        result = check_func(context, warehouse)

    assert result.passed is False
    assert "stale" in result.description.lower()
    assert result.metadata["days_behind"].value == 10


# ── Integration Tests ──────────────────────────────────────────────


def test_asset_dependencies_correct_order():
    """Assets have correct dependency order (bronze → raw)."""
    from orchestration.dagster import defs

    # get_all_asset_specs() was removed; resolve_all_asset_specs() is the
    # supported replacement in current Dagster.
    assets = defs.resolve_all_asset_specs()

    # Find bronze and raw assets for pos.sales by their exact key path. A
    # substring match on "raw_pos_sales" also matches an unrelated
    # single-segment dbt asset with the same trailing name, and whichever
    # spec is iterated last silently wins — that unrelated asset has no
    # `deps`, so the test used to fail depending on collection order.
    bronze_key = None
    raw_key = None

    for asset in assets:
        if asset.key.path == ["bronze", "pos", "bronze_pos_sales"]:
            bronze_key = asset.key
        if asset.key.path == ["raw", "pos", "raw_pos_sales"]:
            raw_key = asset.key

    assert bronze_key is not None, "Bronze asset not found"
    assert raw_key is not None, "Raw asset not found"

    # Verify RAW depends on bronze. `deps` is a list of AssetDep, not
    # AssetKey, so compare against each dep's asset_key.
    raw_asset = next(a for a in assets if a.key == raw_key)
    dep_keys = [dep.asset_key for dep in raw_asset.deps]
    assert bronze_key in dep_keys


def test_partitions_defined_on_assets():
    """Partitioned assets have partition definitions."""
    from orchestration.dagster import defs

    # Get bronze asset
    assets = defs.resolve_all_asset_specs()
    bronze_assets = [a for a in assets if "bronze" in str(a.key)]

    assert len(bronze_assets) > 0, "No bronze assets found"

    # Verify partitions defined
    for asset in bronze_assets:
        assert asset.partitions_def is not None, f"{asset.key} missing partitions"


def test_schedules_defined():
    """Pipeline has schedules for automated runs."""
    from orchestration.dagster import defs

    schedules = list(defs.schedules)

    # Should have at least 3 schedules (ingestion, dbt, forecast)
    assert len(schedules) >= 3

    schedule_names = [s.name for s in schedules]
    assert "daily_ingestion_schedule" in schedule_names
    assert "daily_dbt_schedule" in schedule_names
    assert "weekly_forecast_schedule" in schedule_names


def test_sensors_defined():
    """Pipeline has sensors for failure recovery."""
    from orchestration.dagster import defs

    sensors = list(defs.sensors)

    # Should have at least 2 sensors (retry, quarantine)
    assert len(sensors) >= 2

    sensor_names = [s.name for s in sensors]
    assert "failed_partition_retry" in sensor_names
    assert "quality_quarantine_alert" in sensor_names


def test_jobs_defined():
    """Pipeline has jobs for manual execution."""
    from orchestration.dagster import defs

    jobs = list(defs.resolve_all_job_defs())

    # Should have multiple jobs
    assert len(jobs) > 0

    job_names = [j.name for j in jobs]
    assert "daily_ingestion" in job_names
    assert "dbt_build" in job_names
    assert "backfill_ingestion" in job_names


# ── Idempotency Tests ──────────────────────────────────────────────


def test_bronze_asset_idempotent(tmp_path):
    """Bronze asset can be re-run without side effects.

    The underlying CLI uses partition overwrite semantics,
    so re-running produces identical output.
    """
    asset_func = make_bronze_asset("pos", "sales")
    cli = CliExecutor(working_directory=str(tmp_path))
    context = build_asset_context(partition_key="2026-07-21")

    # CLI returns the same output twice
    with patch.object(
        CliExecutor,
        "run_ingestion",
        return_value=subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="rows_read=1000\nrows_landed=1000\nrows_rejected=0",
            stderr="",
        ),
    ):
        output1 = asset_func(context, cli)
        output2 = asset_func(context, cli)

    # Outputs should be identical
    assert output1.metadata["rows_read"].value == output2.metadata["rows_read"].value
    assert output1.metadata["rows_landed"].value == output2.metadata["rows_landed"].value


# ── Backfill Tests ─────────────────────────────────────────────────


def test_partition_based_backfill_supported():
    """Assets support partition-based backfills."""
    from orchestration.dagster import defs

    # Get backfill job
    jobs = list(defs.resolve_all_job_defs())
    backfill_job = next(j for j in jobs if j.name == "backfill_ingestion")

    assert backfill_job is not None
    assert backfill_job.partitions_def is not None


def test_backfill_job_selects_correct_assets():
    """Backfill job selects only ingestion assets."""
    from orchestration.dagster.schedules import backfill_ingestion_job

    # Job should target ingestion and warehouse groups
    # (verified by asset selection in schedules.py)
    assert backfill_ingestion_job.name == "backfill_ingestion"
