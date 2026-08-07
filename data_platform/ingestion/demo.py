"""Build a warehouse from nothing — synthetic sources, ingestion, dbt.

This is the code behind `make demo`, and it is also what the API integration
suites build their fixtures with. It lived in `backend/tests/integration/`
first, because tests were the only thing that needed a warehouse from scratch.
That made it untouchable from anywhere else: the Makefile cannot import from a
test package, so the demo path would have had to grow a second copy of the same
pipeline, and the two would have disagreed within a month.

So the building lives here, in the package that owns ingestion, and the test
fixtures import it. Whatever a recruiter sees on `make demo` is produced by
exactly the code the integration suites assert against.

**Everything is deterministic.** Every generator takes an explicit seed and the
last business date is fixed rather than derived from today. A demo whose
numbers move with the wall clock is a demo where nobody can tell a real bug
from a Tuesday.
"""

import os
import subprocess
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DBT_DIR = REPO / "data_platform" / "dbt"

#: The last business date generated. Fixed on purpose — see the module note.
LAST_DAY = date(2026, 7, 21)


class DemoBuildError(RuntimeError):
    """A stage of the build failed. Carries the output that explains it."""


@dataclass(frozen=True, slots=True)
class Shape:
    """How much world to generate.

    Deliberately small: every field costs build time for everyone who shares
    the shape, and what these fixtures prove is behaviour — decomposition,
    suppression, permission — not scale.
    """

    days: int
    stores: int
    lines_per_store: int
    skus_per_store: int

    @property
    def slug(self) -> str:
        return f"{self.days}d_{self.stores}s_{self.lines_per_store}l_{self.skus_per_store}k"


#: What `make demo` builds.
#:
#: Sized against a stopwatch, not a guess. The build costs roughly twenty
#: seconds of fixed dbt work plus three and a third seconds per generated day,
#: almost regardless of store count — so `days` is the only dial that matters
#: and every extra week costs another twenty-three seconds of somebody's first
#: impression.
#:
#: Twenty-eight days is the smallest history that still makes the product
#: legible: the Command Center compares a fortnight against the fortnight
#: before it, which is the shortest window where a root cause has a baseline to
#: be a cause *of*. Four stores is the fewest that makes a league table look
#: like a ranking rather than a pair.
DEMO = Shape(days=28, stores=4, lines_per_store=16, skus_per_store=6)


def build(
    shape: Shape,
    root: Path,
    *,
    last_day: date = LAST_DAY,
    filename: str = "wh.duckdb",
) -> Path:
    """Generate source files, ingest them, and build the star schema.

    Returns the path to the built DuckDB file. Everything is written under
    `root`, so callers get a self-contained tree they can throw away.

    `filename` exists because **DuckDB takes its catalog name from the file
    name**, and dbt bakes that catalog into every view it compiles. Build as
    `wh.duckdb` and the views say `wh.analytics_semantic....`; rename the file
    afterwards and every one of them breaks with `Catalog "wh" does not exist`
    — at query time, from the API, long after the build reported success.
    Callers that intend to move the result must build it under its final name.
    """
    # Imported lazily: this module is imported by the CLI at startup, and the
    # generators pull in the whole ingestion stack.
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

    settings = EtlSettings(
        landing_root=root / "lake",
        inbox_root=root / "inbox",
        warehouse_path=root / filename,
        # Generated data is well-formed by construction; a threshold this loose
        # exists so a single odd synthetic row cannot quarantine a whole demo.
        reject_rate_threshold=0.10,
    )

    first_day = last_day - timedelta(days=shape.days - 1)
    for offset in range(shape.days):
        day = first_day + timedelta(days=offset)
        pos_files.generate_day(
            settings.inbox_dir("pos"),
            day,
            stores=shape.stores,
            lines_per_store=shape.lines_per_store,
            seed=7 + offset,
            history_start=first_day,
            history_end=last_day,
        )
        inventory_files.generate_day(
            settings.inbox_dir("inventory"),
            day,
            stores=shape.stores,
            skus_per_store=shape.skus_per_store,
            seed=600 + offset,
        )
        purchase_orders.generate_day(
            settings.inbox_dir("purchasing"),
            day,
            stores=shape.stores,
            lines=16,
            seed=900 + offset,
            as_of=last_day,
        )
        weather.generate_day(
            settings.inbox_dir("weather"), day, seed=41 + offset, history_end=last_day
        )
        fulfilment.generate_day(
            settings.inbox_dir("fulfilment"),
            day,
            stores=shape.stores,
            seed=55 + offset,
            history_end=last_day,
        )

    schema_root = REPO / "data_platform" / "ingestion" / "schemas"
    window = Window(first_day, last_day + timedelta(days=1))
    connection = connect(settings.warehouse_path)
    try:
        for source, table, units in (
            ("pos", "sales", shape.stores),
            ("inventory", "positions", shape.stores),
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
            if summary.quarantined:
                raise DemoBuildError(f"{source}.{table} quarantined: {summary.quarantined}")
    finally:
        connection.close()

    run_dbt(settings.warehouse_path, target_path=root / "dbt_target")
    return settings.warehouse_path


def run_dbt(warehouse_path: Path, *, target_path: Path, select: str | None = None) -> None:
    """`dbt seed`, `snapshot`, `build` against a warehouse.

    Ordering is not arbitrary: seeds are reference data the models join to,
    snapshots capture the SCD2 dimensions, and only then do the models have
    something to build from.
    """
    env = {
        **os.environ,
        "RM_WAREHOUSE_DUCKDB_PATH": str(warehouse_path),
        "DBT_TARGET_PATH": str(target_path),
    }
    steps: tuple[tuple[str, list[str]], ...] = (
        ("seed", []),
        ("snapshot", []),
        ("build", ["--select", select] if select else []),
    )
    for step, extra in steps:
        result = subprocess.run(  # noqa: S603 — fixed argv, no shell
            ["uv", "run", "dbt", step, "--profiles-dir", ".", *extra],  # noqa: S607
            cwd=DBT_DIR,
            env=env,
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise DemoBuildError(f"dbt {step} failed:\n{result.stdout[-3000:]}")
