"""Forecast endpoints against a real warehouse and a real training run.

The fixture builds the warehouse, trains models through the actual pipeline,
rebuilds so dbt picks the forecasts up, and only then serves them — because
that two-pass shape is the thing most likely to break, and a fixture that
inserts rows directly would never exercise it.

The assertions are about honesty rather than plumbing: that a forecast carries
its model's track record, that an explanation reconstructs the number it
explains, that pending forecasts cannot inflate the accuracy sample, and that
a band is never inverted.
"""

import os
import subprocess
import sys
from collections.abc import AsyncIterator, Iterator
from datetime import date, timedelta
from pathlib import Path

import pytest

pytest.importorskip("testcontainers", reason="integration extra not installed")
from httpx import ASGITransport, AsyncClient  # noqa: E402

pytestmark = pytest.mark.integration

REPO = Path(__file__).resolve().parents[3]
DBT_DIR = REPO / "data_platform" / "dbt"
LAST_DAY = date(2026, 7, 21)
HISTORY_DAYS = 140  # enough for a fortnight's horizon with real backtest folds
DEMO_PASSWORD = "ChangeMe-Demo1!"  # noqa: S105 — seeded demo credential

USERS = {"ceo": "priya@northwind.example", "admin": "sam@northwind.example"}


def _dbt(step: str, warehouse: Path, target: Path) -> None:
    result = subprocess.run(  # noqa: S603
        ["uv", "run", "dbt", step, "--profiles-dir", "."],  # noqa: S607
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
    assert result.returncode == 0, f"dbt {step} failed:\n{result.stdout[-3000:]}"


@pytest.fixture(scope="module")
def forecast_warehouse(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    """Build → train → rebuild, exactly as the operator runbook prescribes."""
    sys.path.insert(0, str(REPO / "data_platform"))
    sys.path.insert(0, str(REPO / "ml"))

    from ingestion.connectors.csv_files import CsvFileConnector
    from ingestion.core.config import EtlSettings
    from ingestion.core.duck import connect
    from ingestion.domain.schema import SourceSchema
    from ingestion.domain.window import Window
    from ingestion.generators import inventory_files, pos_files, purchase_orders
    from ingestion.pipeline import IngestionPipeline

    root = tmp_path_factory.mktemp("forecast_wh")
    settings = EtlSettings(
        landing_root=root / "lake",
        inbox_root=root / "inbox",
        warehouse_path=root / "wh.duckdb",
        reject_rate_threshold=0.10,
    )

    stores = 3  # small estate: this suite tests forecasting, not scale
    first_day = LAST_DAY - timedelta(days=HISTORY_DAYS - 1)
    for offset in range(HISTORY_DAYS):
        day = first_day + timedelta(days=offset)
        pos_files.generate_day(
            settings.inbox_dir("pos"),
            day,
            stores=stores,
            lines_per_store=20,
            seed=7 + offset,
            history_start=first_day,
            history_end=LAST_DAY,
        )
        inventory_files.generate_day(
            settings.inbox_dir("inventory"), day, stores=stores, skus_per_store=8, seed=600 + offset
        )
        purchase_orders.generate_day(
            settings.inbox_dir("purchasing"),
            day,
            stores=stores,
            lines=20,
            seed=900 + offset,
            as_of=LAST_DAY,
        )

    schema_root = REPO / "data_platform" / "ingestion" / "schemas"
    window = Window(first_day, LAST_DAY + timedelta(days=1))
    conn = connect(settings.warehouse_path)
    for source, table, units in (
        ("pos", "sales", stores),
        ("inventory", "positions", stores),
        ("purchasing", "orders", 1),
    ):
        schema = SourceSchema.from_yaml(schema_root / source / f"{table}.yml")
        connector = CsvFileConnector(
            schema=schema, settings=settings, connection=conn, expected_units=units
        )
        summary = IngestionPipeline(connector=connector, settings=settings, connection=conn).run(
            window
        )
        assert not summary.quarantined, f"{source}: {summary.quarantined}"
    conn.close()

    target = root / "dbt_target"
    for step in ("seed", "snapshot", "build"):
        _dbt(step, settings.warehouse_path, target)

    # Train against the built marts, then rebuild so dbt unions the forecasts
    # into fct_forecast. The ordering is the point: training reads the marts
    # and the scoreboard reads training's output.
    from forecasting.pipeline import run_training

    report = run_training(
        settings.warehouse_path, root / "models", horizon=7, folds=8, demand_series_limit=3
    )
    assert report.predictions_written > 0, "training published nothing"

    _dbt("build", settings.warehouse_path, target)
    yield settings.warehouse_path


@pytest.fixture
async def client(
    migrated_db: dict[str, str], forecast_warehouse: Path
) -> AsyncIterator[AsyncClient]:
    os.environ["RM_WAREHOUSE_DUCKDB_PATH"] = str(forecast_warehouse)
    os.environ.pop("RM_REDIS_CACHE_URL", None)

    from app.main import create_app

    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http
    await app.state.engine.dispose()


async def _auth(client: AsyncClient, role: str = "ceo") -> dict[str, str]:
    response = await client.post(
        "/api/v1/auth/login", json={"email": USERS[role], "password": DEMO_PASSWORD}
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def _get(client: AsyncClient, path: str, role: str = "ceo", **params: object) -> dict:
    response = await client.get(
        f"/api/v1/forecasts{path}", headers=await _auth(client, role), params=params
    )
    assert response.status_code == 200, response.text
    return response.json()


# ── Forecasts are served, with their uncertainty ─────────────────────


async def test_every_target_is_forecast(client: AsyncClient) -> None:
    for target in ("revenue", "sales", "demand", "inventory", "profit"):
        body = await _get(client, f"/{target}")
        assert body["data"], f"no forecast published for {target}"


async def test_forecasts_are_for_days_that_have_not_happened(client: AsyncClient) -> None:
    """A forecast about yesterday is a report, and belongs elsewhere."""
    body = await _get(client, "/revenue")
    assert all(row["business_date"] > LAST_DAY.isoformat() for row in body["data"])


async def test_every_forecast_carries_an_interval(client: AsyncClient) -> None:
    """A point estimate alone invites planning as though it were certain."""
    for row in (await _get(client, "/revenue"))["data"]:
        assert row["forecast_lower"] <= row["forecast"] <= row["forecast_upper"]


async def test_bands_are_estimated_per_horizon(client: AsyncClient) -> None:
    """Each horizon gets its own band, from its own residuals.

    Deliberately *not* asserting that bands widen with distance. That holds
    for a model extrapolating from a recent level, and not for seasonal naive,
    whose forecast is a weekday profile: predicting three weeks out is no
    harder than predicting three days out because neither uses the recent
    level at all. That flatness is a large part of why the baseline is so hard
    to beat at long horizons.

    What must hold is that the bands were estimated separately — a single
    pooled width applied everywhere would be too wide near and too narrow far.
    """
    rows = (await _get(client, "/revenue"))["data"]
    widths = {row["horizon"]: row["forecast_upper"] - row["forecast_lower"] for row in rows}

    assert all(width > 0 for width in widths.values())
    assert len(set(widths.values())) > 1, "one width across every horizon means pooled residuals"


async def test_a_forecast_travels_with_its_models_track_record(client: AsyncClient) -> None:
    """Without it a forecast is an assertion rather than evidence."""
    for row in (await _get(client, "/revenue"))["data"]:
        assert row["model_wape"] is not None
        assert row["model_mase"] is not None


async def test_a_single_horizon_can_be_isolated(client: AsyncClient) -> None:
    body = await _get(client, "/revenue", horizon=3)
    assert {row["horizon"] for row in body["data"]} == {3}


async def test_an_unknown_target_returns_an_empty_answer_with_a_reason(
    client: AsyncClient,
) -> None:
    """Silence would be indistinguishable from a broken endpoint."""
    body = await _get(client, "/not_a_target")
    assert body["data"] == []
    assert any("no published forecast" in c.lower() for c in body["caveats"])


# ── Derived targets keep their identities ────────────────────────────


async def test_projected_inventory_is_never_negative(client: AsyncClient) -> None:
    """The reason inventory is projected rather than fitted."""
    for row in (await _get(client, "/inventory", limit=200))["data"]:
        assert row["forecast"] >= 0
        assert row["forecast_lower"] >= 0


async def test_derived_targets_are_labelled_as_derived(client: AsyncClient) -> None:
    for target in ("inventory", "profit"):
        rows = (await _get(client, f"/{target}"))["data"]
        assert all(
            "derived" in row["model_name"] or "projected" in row["model_name"] for row in rows
        )


# ── Totals ───────────────────────────────────────────────────────────


async def test_horizon_total_sums_the_daily_forecasts(client: AsyncClient) -> None:
    daily = (await _get(client, "/revenue"))["data"]
    total = (await _get(client, "/revenue/total"))["data"]

    assert total[0]["forecast"] == pytest.approx(sum(r["forecast"] for r in daily), rel=1e-6)


async def test_totals_admit_that_summed_bounds_are_conservative(client: AsyncClient) -> None:
    """Independent daily errors partially cancel; the summed band does not."""
    body = await _get(client, "/revenue/total")
    assert any("cancel" in caveat for caveat in body["caveats"])


# ── Accuracy ─────────────────────────────────────────────────────────


async def test_accuracy_grades_the_models(client: AsyncClient) -> None:
    body = await _get(client, "/meta/accuracy")
    assert body["models"]


async def test_pending_forecasts_do_not_inflate_the_accuracy_sample(
    client: AsyncClient,
) -> None:
    """A prediction about Thursday made on Monday has nothing to be scored against."""
    for row in (await _get(client, "/meta/accuracy"))["models"]:
        if row["pending_days"] and not row["forecast_days"]:
            assert row["wape"] is None, "scored a model with no actuals"


async def test_the_two_seasonal_naive_implementations_are_graded_separately(
    client: AsyncClient,
) -> None:
    """Same name, different code — merging them misattributes accuracy."""
    producers = {row["produced_by"] for row in (await _get(client, "/meta/accuracy"))["models"]}
    assert "warehouse_sql" in producers


async def test_accuracy_reports_bias_not_just_magnitude(client: AsyncClient) -> None:
    """Bias compounds into working capital where absolute error averages out."""
    scored = [r for r in (await _get(client, "/meta/accuracy"))["models"] if r["forecast_days"]]
    assert scored
    assert all(row["bias"] is not None for row in scored)


# ── Explainability ───────────────────────────────────────────────────


async def test_explanations_reconstruct_the_forecast_they_explain(
    client: AsyncClient,
) -> None:
    """The property that separates a reason from a story.

    `baseline + Σ effect` must equal the point forecast. If it does not, the
    explanation is describing a model nobody is running.
    """
    forecasts = {
        (row["business_date"], row["horizon"]): row["forecast"]
        for row in (await _get(client, "/revenue"))["data"]
    }
    explained = (await _get(client, "/revenue/explain", limit=200))["data"]
    assert explained

    rebuilt: dict[tuple[str, int], float] = {}
    baselines: dict[tuple[str, int], float] = {}
    for row in explained:
        key = (row["business_date"], row["horizon"])
        rebuilt[key] = rebuilt.get(key, 0.0) + row["effect"]
        baselines[key] = row["baseline"]

    checked = 0
    for key, total in rebuilt.items():
        if key in forecasts:
            assert baselines[key] + total == pytest.approx(forecasts[key], rel=1e-6)
            checked += 1
    assert checked > 0, "no explanation matched a forecast"


async def test_explanations_state_their_method(client: AsyncClient) -> None:
    body = await _get(client, "/revenue/explain")
    assert "exact" in body["method"]


async def test_contributions_carry_a_direction(client: AsyncClient) -> None:
    for row in (await _get(client, "/revenue/explain"))["data"]:
        assert row["direction"] in {"increases", "decreases"}


# ── Authorization ────────────────────────────────────────────────────


async def test_a_role_without_forecast_access_is_refused(client: AsyncClient) -> None:
    """Admin administers; it does not read the business's forecasts."""
    for path in ("/revenue", "/meta/accuracy"):
        response = await client.get(
            f"/api/v1/forecasts{path}",
            headers=await _auth(client, "admin"),
        )
        assert response.status_code == 403, path


async def test_anonymous_access_is_rejected(client: AsyncClient) -> None:
    response = await client.get("/api/v1/forecasts/revenue")
    assert response.status_code == 401


async def test_accuracy_rides_the_same_permission_as_the_forecast(
    client: AsyncClient,
) -> None:
    """Anyone who can read a forecast must be able to see how wrong it has been."""
    assert (await _get(client, "/meta/accuracy"))["models"]
