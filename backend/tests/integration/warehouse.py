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

import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]

sys.path.insert(0, str(REPO / "data_platform"))

from ingestion.demo import Shape  # noqa: E402
from ingestion.demo import build as demo_build  # noqa: E402
from ingestion.demo import run_dbt as rebuild  # noqa: E402

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


#: The estate nine of the ten API suites run against. Ten stores is enough for
#: a league table and a region decomposition; sixty-three days covers a
#: three-week window against a six-week baseline.
ESTATE = Shape(days=63, stores=10, lines_per_store=24, skus_per_store=8)

#: Forecasting needs folds, not breadth. A hundred and forty days of three
#: stores backtests properly and builds in a fraction of the time a wide
#: estate would take over the same history.
DEEP_HISTORY = Shape(days=140, stores=3, lines_per_store=20, skus_per_store=8)


def build(shape: Shape, root: Path) -> Path:
    """Generate sources, ingest them, and build the warehouse.

    Thin by design. The building itself lives in `ingestion.demo` so that
    `make demo` can call it without importing a test package — the fixtures
    these suites assert against and the warehouse a recruiter sees are then
    provably the same pipeline, not two copies drifting apart.
    """
    return demo_build(shape, root, last_day=LAST_DAY)


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

    # Same dbt invocation the initial build uses, from the same place — a
    # second copy here is how the fixture and the demo start disagreeing about
    # which steps run.
    rebuild(warehouse_path, target_path=root / "dbt_target")
    return int(report.predictions_written)
