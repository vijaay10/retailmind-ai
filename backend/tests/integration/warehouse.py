"""One warehouse build, shared by every API suite.

**Why this file exists.** Ten integration modules each used to build their own
warehouse — generate source files, run the ingestion pipeline, then `dbt seed`,
`snapshot` and `build`. The work is identical every time and takes about three
minutes, so the suite spent half an hour rebuilding the same star schema ten
times. A thirty-minute integration job is a job that does not run in CI, and an
integration suite nobody runs in CI is decoration.

The fixtures live in `conftest.py`; this module owns the building. They are
**session-scoped and cached by shape**, so a suite asking for the same estate
as an earlier one gets the warehouse that already exists.

**Two shapes, not one.** Forecasting needs deep history and does not care about
estate size; everything else needs a realistic estate and a few weeks of days.
A single superset would be 140 days × 10 stores — four times the rows of the
largest suite that needs them, paid for by every suite that does not.

**Read-only by construction.** These suites query analytics; nothing writes to
the warehouse, which is what makes sharing safe. The Postgres side is *not*
shared this way — decisions, notifications and auth events are written by
tests, and those go through `migrated_db`, which is per-session but truncated
between modules by the fixtures that need it.
"""

import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
DBT_DIR = REPO / "data_platform" / "dbt"

#: The last business date every suite asserts against. Fixed rather than
#: relative to today: a test whose expectations move with the wall clock fails
#: on a Monday for reasons nobody can reproduce on a Tuesday.
LAST_DAY = date(2026, 7, 21)

DEMO_PASSWORD = "ChangeMe-Demo1!"  # noqa: S105 — seeded demo credential

#: The seeded demo tenant, keyed by the role each user actually holds.
#: Mirrors `app.infrastructure.db.seeds.sample.USERS` exactly — every module
#: used to keep its own copy of this map, and three of them disagreed about
#: which user was which role, so a test asserting "a regional manager cannot
#: see this" was quietly signing in as marketing.
USERS = {
    "admin": "sam@northwind.example",
    "ceo": "priya@northwind.example",
    "regional_manager": "diego@northwind.example",
    "store_manager": "lena@northwind.example",
    "marketing": "marcus@northwind.example",
    "inventory": "aisha@northwind.example",
    "finance": "yusuf@northwind.example",
}
# No aliases. A suite that signs in as "manager" reads well and says nothing
# about which permissions it expects — which is how a test asserting "a
# regional manager cannot see this" ended up signing in as marketing and
# passing for the wrong reason.


@dataclass(frozen=True, slots=True)
class Shape:
    """How much world to generate.

    Deliberately small: every field here costs build time in every suite that
    shares the shape, and the assertions these suites make are about behaviour
    — decomposition, suppression, permission — not about scale.
    """

    days: int
    stores: int
    lines_per_store: int
    skus_per_store: int

    @property
    def slug(self) -> str:
        return f"{self.days}d_{self.stores}s_{self.lines_per_store}l_{self.skus_per_store}k"


#: The estate nine of the ten API suites run against. Ten stores is enough for
#: a league table and a region decomposition; sixty-three days covers a
#: three-week window against a six-week baseline.
ESTATE = Shape(days=63, stores=10, lines_per_store=24, skus_per_store=8)

#: Forecasting needs folds, not breadth. A hundred and forty days of three
#: stores backtests properly and builds in a fraction of the time a wide
#: estate would take over the same history.
DEEP_HISTORY = Shape(days=140, stores=3, lines_per_store=20, skus_per_store=8)


def build(shape: Shape, root: Path) -> Path:
    """Generate sources, ingest them, and build the warehouse."""
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

    settings = EtlSettings(
        landing_root=root / "lake",
        inbox_root=root / "inbox",
        warehouse_path=root / "wh.duckdb",
        reject_rate_threshold=0.10,
    )

    first_day = LAST_DAY - timedelta(days=shape.days - 1)
    for offset in range(shape.days):
        day = first_day + timedelta(days=offset)
        pos_files.generate_day(
            settings.inbox_dir("pos"),
            day,
            stores=shape.stores,
            lines_per_store=shape.lines_per_store,
            seed=7 + offset,
            history_start=first_day,
            history_end=LAST_DAY,
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
            as_of=LAST_DAY,
        )
        weather.generate_day(
            settings.inbox_dir("weather"), day, seed=41 + offset, history_end=LAST_DAY
        )
        fulfilment.generate_day(
            settings.inbox_dir("fulfilment"),
            day,
            stores=shape.stores,
            seed=55 + offset,
            history_end=LAST_DAY,
        )

    schema_root = REPO / "data_platform" / "ingestion" / "schemas"
    window = Window(first_day, LAST_DAY + timedelta(days=1))
    connection = connect(settings.warehouse_path)
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
        assert not summary.quarantined, f"{source}: {summary.quarantined}"
    connection.close()

    env = {
        **os.environ,
        "RM_WAREHOUSE_DUCKDB_PATH": str(settings.warehouse_path),
        "DBT_TARGET_PATH": str(root / "dbt_target"),
    }
    for step in ("seed", "snapshot", "build"):
        result = subprocess.run(  # noqa: S603
            ["uv", "run", "dbt", step, "--profiles-dir", "."],  # noqa: S607
            cwd=DBT_DIR,
            env=env,
            check=False,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"dbt {step} failed:\n{result.stdout[-3000:]}"

    return settings.warehouse_path


def train_forecasts(warehouse_path: Path, root: Path) -> int:
    """Train models against the built marts, then rebuild so dbt unions them in.

    The ordering is the point and it is easy to get wrong: training reads the
    marts, and the accuracy scoreboard reads training's output. Rebuilding
    before training publishes an empty `fct_forecast`; skipping the rebuild
    afterwards leaves the predictions in a staging table nothing selects from.
    """
    sys.path.insert(0, str(REPO / "ml"))
    from forecasting.pipeline import run_training

    report = run_training(
        warehouse_path, root / "models", horizon=7, folds=8, demand_series_limit=3
    )
    assert report.predictions_written > 0, "training published nothing"

    result = subprocess.run(  # noqa: S603
        ["uv", "run", "dbt", "build", "--profiles-dir", "."],  # noqa: S607
        cwd=DBT_DIR,
        env={
            **os.environ,
            "RM_WAREHOUSE_DUCKDB_PATH": str(warehouse_path),
            "DBT_TARGET_PATH": str(root / "dbt_target"),
        },
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"rebuild after training failed:\n{result.stdout[-3000:]}"
    return int(report.predictions_written)
