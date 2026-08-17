"""Dagster resources for RetailMind data platform.

Resources provide access to external systems (warehouse, CLI commands)
without coupling assets to specific implementations.
"""

import subprocess
from pathlib import Path
from typing import Any

import duckdb
from pydantic import Field

from dagster import ConfigurableResource

# Anchored to this file's location rather than any developer's home
# directory or the resource's own `working_directory` (which production
# sets to the repo root, not `data_platform/`) — `run_dbt` needs the real
# dbt project location regardless of what cwd the subprocess runs with.
_DATA_PLATFORM_DIR = Path(__file__).resolve().parents[2]
_DBT_PROJECT_DIR = _DATA_PLATFORM_DIR / "dbt"


class DuckDBWarehouse(ConfigurableResource):
    """DuckDB warehouse connection resource.

    Provides connection to the RetailMind warehouse for queries and
    data quality checks. Not used for writes (CLI handles that).
    """

    database_path: str = Field(
        default=".local/retailmind.duckdb",
        description="Path to DuckDB database file",
    )

    def get_connection(self) -> duckdb.DuckDBPyConnection:
        """Get a DuckDB connection to the warehouse."""
        return duckdb.connect(self.database_path, read_only=False)

    def execute_query(self, query: str) -> list[tuple[Any, ...]]:
        """Execute a query and return results."""
        conn = self.get_connection()
        try:
            return conn.execute(query).fetchall()
        finally:
            conn.close()

    def table_exists(self, schema: str, table: str) -> bool:
        """Check if a table exists in the warehouse."""
        # schema/table are always module-internal identifiers (SOURCES,
        # asset factory args), never external input; DuckDB doesn't support
        # bind-parameterizing identifiers in DDL/information_schema queries
        # anyway.
        query = f"""
            SELECT COUNT(*)
            FROM information_schema.tables
            WHERE table_schema = '{schema}'
            AND table_name = '{table}'
        """  # noqa: S608
        result = self.execute_query(query)
        return result[0][0] > 0

    def get_row_count(self, schema: str, table: str) -> int:
        """Get row count for a table."""
        if not self.table_exists(schema, table):
            return 0
        result = self.execute_query(f"SELECT COUNT(*) FROM {schema}.{table}")  # noqa: S608
        return result[0][0]


class CliExecutor(ConfigurableResource):
    """Executor for RetailMind CLI commands.

    Wraps subprocess calls to existing CLI tools (retailmind-etl, dbt, forecasting).
    All CLI commands are already idempotent and window-based, so this is a thin wrapper.
    """

    working_directory: str = Field(
        default_factory=lambda: str(_DATA_PLATFORM_DIR),
        description="Working directory for CLI commands",
    )

    def run_command(
        self,
        command: list[str],
        *,
        check: bool = True,
        capture_output: bool = True,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
    ) -> subprocess.CompletedProcess:
        """Execute a CLI command and return the result.

        Args:
            command: Command and arguments as list
            check: Raise exception on non-zero exit
            capture_output: Capture stdout/stderr
            env: Additional environment variables
            cwd: Override `working_directory` for this call only. Most
                callers don't need this — `working_directory` already fits
                `uv run`-based commands, which resolve the workspace member
                from anywhere within it. `run_dbt` does need it: dbt
                resolves `profiles.yml`'s relative default warehouse path
                against the invocation cwd, not `--project-dir`.

        Returns:
            CompletedProcess with returncode, stdout, stderr

        Raises:
            subprocess.CalledProcessError if check=True and returncode != 0
        """
        return subprocess.run(  # noqa: S603 — command is built internally, never shell=True
            command,
            cwd=cwd if cwd is not None else self.working_directory,
            check=check,
            capture_output=capture_output,
            text=True,
            env=env,
        )

    def run_ingestion(
        self,
        source: str,
        table: str,
        business_date: str,
        *,
        expected_stores: int | None = None,
    ) -> subprocess.CompletedProcess:
        """Run ingestion for a single business date.

        Calls: retailmind-etl run --source {source} --table {table} --day {date}

        Exit codes:
            0: Success (partition written, quality passed)
            1: Quarantine (quality gate blocking failure)
        """
        cmd = [
            "uv",
            "run",
            "retailmind-etl",
            "run",
            "--source",
            source,
            "--table",
            table,
            "--day",
            business_date,
        ]
        if expected_stores:
            cmd.extend(["--expected-stores", str(expected_stores)])

        return self.run_command(cmd)

    def run_dbt(
        self,
        command: str,
        *,
        select: str | None = None,
        exclude: str | None = None,
    ) -> subprocess.CompletedProcess:
        """Run dbt command.

        Calls: dbt {command} [--select {select}] [--exclude {exclude}]

        Common commands:
            - seed: Load reference data
            - snapshot: Run SCD2 snapshots
            - build: Run models + tests
            - test: Run tests only

        `--profiles-dir`/`--project-dir` are absolute (anchored to this
        module's location, not `self.working_directory`) because dbt
        resolves *those* flags' own relative paths against the subprocess's
        cwd, and this resource's `working_directory` is configured to the
        repository root in production — one level above where the dbt
        project actually lives (`data_platform/dbt`). A relative `"dbt"`
        would silently look for `<working_directory>/dbt`, which doesn't
        exist whenever `working_directory` isn't `data_platform/` itself.

        The subprocess still runs with `cwd=_DBT_PROJECT_DIR` explicitly
        (not `self.working_directory`), independent of the flags above:
        `profiles.yml`'s default warehouse path
        (`env_var('RM_WAREHOUSE_DUCKDB_PATH', '../../.local/retailmind.duckdb')`)
        is itself relative, and dbt resolves *that* against the invocation
        cwd rather than `--project-dir` — so getting the flags right alone
        still pointed dbt at the wrong on-disk database when cwd was the
        repository root.
        """
        cmd = [
            "dbt",
            command,
            "--profiles-dir",
            str(_DBT_PROJECT_DIR),
            "--project-dir",
            str(_DBT_PROJECT_DIR),
        ]
        if select:
            cmd.extend(["--select", select])
        if exclude:
            cmd.extend(["--exclude", exclude])

        return self.run_command(cmd, cwd=str(_DBT_PROJECT_DIR))

    def run_forecast_training(
        self,
        *,
        horizon: int = 28,
        folds: int = 12,
        demand_series: int = 25,
    ) -> subprocess.CompletedProcess:
        """Run forecast model training.

        Calls: retailmind-forecast train

        Exit codes:
            0: Success (models trained, champion published)
            1: Failure (training error, no predictions written)
        """
        cmd = [
            "uv",
            "run",
            "python",
            "-m",
            "forecasting.cli",
            "train",
            "--horizon",
            str(horizon),
            "--folds",
            str(folds),
            "--demand-series",
            str(demand_series),
        ]

        return self.run_command(cmd)


class AuditLedger(ConfigurableResource):
    """Audit ledger resource for recording pipeline runs.

    Wraps the existing ingestion.audit.ledger module to record
    run metadata in Postgres for observability.
    """

    postgres_dsn: str = Field(
        default="postgresql://localhost/retailmind",
        description="Postgres connection string for audit ledger",
    )
    enabled: bool = Field(
        default=True,
        description="Whether to write to audit ledger (best-effort)",
    )

    def start_run(
        self,
        tenant_id: str,
        connector_id: str,
        dag_run_id: str,
        window_start: str,
        window_end: str,
    ) -> str:
        """Record the start of a pipeline run.

        Returns:
            run_id (UUID as string)
        """
        if not self.enabled:
            return "disabled"

        # Import here to avoid circular dependencies
        from ingestion.audit.ledger import AuditLedger as LedgerImpl
        from ingestion.domain.window import Window

        ledger = LedgerImpl(self.postgres_dsn)
        window = Window(window_start, window_end)

        return str(ledger.start_run(tenant_id, connector_id, dag_run_id, window))

    def finish_run(
        self,
        run_id: str,
        status: str,
        rows_read: int,
        rows_rejected: int,
        rows_written: int,
        watermark: str,
        error: str | None = None,
    ) -> None:
        """Record the completion of a pipeline run."""
        if not self.enabled or run_id == "disabled":
            return

        from ingestion.audit.ledger import AuditLedger as LedgerImpl

        ledger = LedgerImpl(self.postgres_dsn)
        ledger.finish_run(
            run_id,
            status,
            rows_read,
            rows_rejected,
            rows_written,
            watermark,
            error,
        )
