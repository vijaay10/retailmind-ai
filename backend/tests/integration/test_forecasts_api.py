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
from pathlib import Path

import pytest

pytest.importorskip("testcontainers", reason="integration extra not installed")
from httpx import AsyncClient  # noqa: E402

from tests.integration import warehouse  # noqa: E402
from tests.integration.conftest import auth_headers  # noqa: E402
from tests.integration.warehouse import LAST_DAY  # noqa: E402

pytestmark = pytest.mark.integration

DBT_DIR = warehouse.DBT_DIR


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


async def _get(deep_api: AsyncClient, path: str, role: str = "ceo", **params: object) -> dict:
    response = await deep_api.get(
        f"/api/v1/forecasts{path}", headers=await auth_headers(deep_api, role), params=params
    )
    assert response.status_code == 200, response.text
    return response.json()


# ── Forecasts are served, with their uncertainty ─────────────────────


async def test_every_target_is_forecast(deep_api: AsyncClient) -> None:
    for target in ("revenue", "sales", "demand", "inventory", "profit"):
        body = await _get(deep_api, f"/{target}")
        assert body["data"], f"no forecast published for {target}"


async def test_forecasts_are_for_days_that_have_not_happened(deep_api: AsyncClient) -> None:
    """A forecast about yesterday is a report, and belongs elsewhere."""
    body = await _get(deep_api, "/revenue")
    assert all(row["business_date"] > LAST_DAY.isoformat() for row in body["data"])


async def test_every_forecast_carries_an_interval(deep_api: AsyncClient) -> None:
    """A point estimate alone invites planning as though it were certain."""
    for row in (await _get(deep_api, "/revenue"))["data"]:
        assert row["forecast_lower"] <= row["forecast"] <= row["forecast_upper"]


async def test_bands_are_estimated_per_horizon(deep_api: AsyncClient) -> None:
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
    rows = (await _get(deep_api, "/revenue"))["data"]
    widths = {row["horizon"]: row["forecast_upper"] - row["forecast_lower"] for row in rows}

    assert all(width > 0 for width in widths.values())
    assert len(set(widths.values())) > 1, "one width across every horizon means pooled residuals"


async def test_a_forecast_travels_with_its_models_track_record(deep_api: AsyncClient) -> None:
    """Without it a forecast is an assertion rather than evidence."""
    for row in (await _get(deep_api, "/revenue"))["data"]:
        assert row["model_wape"] is not None
        assert row["model_mase"] is not None


async def test_a_single_horizon_can_be_isolated(deep_api: AsyncClient) -> None:
    body = await _get(deep_api, "/revenue", horizon=3)
    assert {row["horizon"] for row in body["data"]} == {3}


async def test_an_unknown_target_returns_an_empty_answer_with_a_reason(
    deep_api: AsyncClient,
) -> None:
    """Silence would be indistinguishable from a broken endpoint."""
    body = await _get(deep_api, "/not_a_target")
    assert body["data"] == []
    assert any("no published forecast" in c.lower() for c in body["caveats"])


# ── Derived targets keep their identities ────────────────────────────


async def test_projected_inventory_is_never_negative(deep_api: AsyncClient) -> None:
    """The reason inventory is projected rather than fitted."""
    for row in (await _get(deep_api, "/inventory", limit=200))["data"]:
        assert row["forecast"] >= 0
        assert row["forecast_lower"] >= 0


async def test_derived_targets_are_labelled_as_derived(deep_api: AsyncClient) -> None:
    for target in ("inventory", "profit"):
        rows = (await _get(deep_api, f"/{target}"))["data"]
        assert all(
            "derived" in row["model_name"] or "projected" in row["model_name"] for row in rows
        )


# ── Totals ───────────────────────────────────────────────────────────


async def test_horizon_total_sums_the_daily_forecasts(deep_api: AsyncClient) -> None:
    daily = (await _get(deep_api, "/revenue"))["data"]
    total = (await _get(deep_api, "/revenue/total"))["data"]

    assert total[0]["forecast"] == pytest.approx(sum(r["forecast"] for r in daily), rel=1e-6)


async def test_totals_admit_that_summed_bounds_are_conservative(deep_api: AsyncClient) -> None:
    """Independent daily errors partially cancel; the summed band does not."""
    body = await _get(deep_api, "/revenue/total")
    assert any("cancel" in caveat for caveat in body["caveats"])


# ── Accuracy ─────────────────────────────────────────────────────────


async def test_accuracy_grades_the_models(deep_api: AsyncClient) -> None:
    body = await _get(deep_api, "/meta/accuracy")
    assert body["models"]


async def test_pending_forecasts_do_not_inflate_the_accuracy_sample(
    deep_api: AsyncClient,
) -> None:
    """A prediction about Thursday made on Monday has nothing to be scored against."""
    for row in (await _get(deep_api, "/meta/accuracy"))["models"]:
        if row["pending_days"] and not row["forecast_days"]:
            assert row["wape"] is None, "scored a model with no actuals"


async def test_the_two_seasonal_naive_implementations_are_graded_separately(
    deep_api: AsyncClient,
) -> None:
    """Same name, different code — merging them misattributes accuracy."""
    producers = {row["produced_by"] for row in (await _get(deep_api, "/meta/accuracy"))["models"]}
    assert "warehouse_sql" in producers


async def test_accuracy_reports_bias_not_just_magnitude(deep_api: AsyncClient) -> None:
    """Bias compounds into working capital where absolute error averages out."""
    scored = [r for r in (await _get(deep_api, "/meta/accuracy"))["models"] if r["forecast_days"]]
    assert scored
    assert all(row["bias"] is not None for row in scored)


# ── Explainability ───────────────────────────────────────────────────


async def test_explanations_reconstruct_the_forecast_they_explain(
    deep_api: AsyncClient,
) -> None:
    """The property that separates a reason from a story.

    `baseline + Σ effect` must equal the point forecast. If it does not, the
    explanation is describing a model nobody is running.
    """
    forecasts = {
        (row["business_date"], row["horizon"]): row["forecast"]
        for row in (await _get(deep_api, "/revenue"))["data"]
    }
    explained = (await _get(deep_api, "/revenue/explain", limit=200))["data"]
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


async def test_explanations_state_their_method(deep_api: AsyncClient) -> None:
    body = await _get(deep_api, "/revenue/explain")
    assert "exact" in body["method"]


async def test_contributions_carry_a_direction(deep_api: AsyncClient) -> None:
    for row in (await _get(deep_api, "/revenue/explain"))["data"]:
        assert row["direction"] in {"increases", "decreases"}


# ── Authorization ────────────────────────────────────────────────────


async def test_a_role_without_forecast_access_is_refused(deep_api: AsyncClient) -> None:
    """Admin administers; it does not read the business's forecasts."""
    for path in ("/revenue", "/meta/accuracy"):
        response = await deep_api.get(
            f"/api/v1/forecasts{path}",
            headers=await auth_headers(deep_api, "admin"),
        )
        assert response.status_code == 403, path


async def test_anonymous_access_is_rejected(deep_api: AsyncClient) -> None:
    response = await deep_api.get("/api/v1/forecasts/revenue")
    assert response.status_code == 401


async def test_accuracy_rides_the_same_permission_as_the_forecast(
    deep_api: AsyncClient,
) -> None:
    """Anyone who can read a forecast must be able to see how wrong it has been."""
    assert (await _get(deep_api, "/meta/accuracy"))["models"]
