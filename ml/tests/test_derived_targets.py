"""Derived targets: the ones that are computed rather than fitted.

Inventory and profit are not models, and these tests exist to pin the
properties that make them better than models would be. An inventory
regression can forecast negative stock; a projection through the identity
cannot. That is the whole argument, and it should be a test rather than a
paragraph.
"""

from datetime import date, timedelta

import duckdb
import pytest

from forecasting.pipeline import derive_profit, project_inventory
from forecasting.warehouse import PredictionRow

ORIGIN = date(2026, 7, 21)


def prediction(
    horizon: int,
    yhat: float,
    *,
    target: str = "demand",
    series: str = "AC-1010|S2001",
    spread: float = 1.0,
) -> PredictionRow:
    return PredictionRow(
        run_id="run",
        target=target,
        series_key=series,
        model_name="seasonal_naive_w4",
        model_class="baseline",
        origin_date=ORIGIN,
        business_date=ORIGIN + timedelta(days=horizon),
        horizon=horizon,
        yhat=yhat,
        yhat_lower=yhat - spread,
        yhat_upper=yhat + spread,
    )


@pytest.fixture
def warehouse() -> duckdb.DuckDBPyConnection:
    """A minimal stand-in for the inventory position the projection reads."""
    connection = duckdb.connect(":memory:")
    connection.execute("create schema analytics_semantic")
    connection.execute(
        """
        create table analytics_semantic.v_mart_inventory_health as
        select 'AC-1010' as sku, 'S2001' as store_id, 100.0 as on_hand_qty,
               48.0 as on_order_total, 5 as contract_lead_time_days
        """
    )
    return connection


# ── Inventory: an identity, not a regression ─────────────────────────


def test_stock_depletes_by_forecast_demand(warehouse: duckdb.DuckDBPyConnection) -> None:
    demand = [prediction(h, 10.0) for h in range(1, 4)]
    projected = project_inventory(warehouse, demand, run_id="run")

    # 100 opening, 10 a day, no receipt until day 5.
    assert [row.yhat for row in projected[:3]] == [90.0, 80.0, 70.0]


def test_scheduled_receipt_lands_once_on_its_arrival_day(
    warehouse: duckdb.DuckDBPyConnection,
) -> None:
    """A smooth daily drip would let the projection dodge every stockout.

    Spreading an inbound delivery evenly across the horizon is the tempting
    simplification, and it makes stock appear to top up a little each morning
    — which is exactly the shape that hides the day a store actually runs out.
    """
    demand = [prediction(h, 30.0) for h in range(1, 7)]
    projected = project_inventory(warehouse, demand, run_id="run")
    levels = [row.yhat for row in projected]

    # h:      1     2     3     4              5                  6
    # stock: 70 →  40 →  10 →   0 (clamped) → 18 (48 lands) →     0
    assert levels[:4] == pytest.approx([70.0, 40.0, 10.0, 0.0])
    assert levels[4] > levels[3], "the 48-unit order should land whole on day 5"
    assert levels[5] < levels[4], "and deplete again immediately after"


def test_projected_stock_never_goes_negative(warehouse: duckdb.DuckDBPyConnection) -> None:
    """A regression on on-hand would happily forecast −40 units.

    Negative stock is not a small numerical wrinkle: it is two different facts
    collapsed into one number. The store is out, *and* there is unmet demand,
    and those have different responses.
    """
    demand = [prediction(h, 500.0) for h in range(1, 8)]
    projected = project_inventory(warehouse, demand, run_id="run")

    assert all(row.yhat >= 0 for row in projected)
    assert all(row.yhat_lower >= 0 for row in projected)


def test_the_stock_band_inverts_the_demand_band(warehouse: duckdb.DuckDBPyConnection) -> None:
    """Heavy demand depletes faster, so the demand upper bound is the stock lower one."""
    demand = [prediction(h, 10.0, spread=5.0) for h in range(1, 4)]
    projected = project_inventory(warehouse, demand, run_id="run")

    for row in projected:
        assert row.yhat_lower <= row.yhat <= row.yhat_upper


def test_projection_skips_series_with_no_stock_position(
    warehouse: duckdb.DuckDBPyConnection,
) -> None:
    """A forecast for something the warehouse does not stock is not stock."""
    demand = [prediction(1, 5.0, series="ZZ-9999|S9999")]
    assert project_inventory(warehouse, demand, run_id="run") == []


def test_projection_is_marked_derived_not_fitted(warehouse: duckdb.DuckDBPyConnection) -> None:
    """The model class is how a reader knows this was not fitted to on-hand."""
    projected = project_inventory(warehouse, [prediction(1, 5.0)], run_id="run")
    assert projected[0].model_class == "derived"
    assert "projected" in projected[0].model_name


# ── Profit: a decomposition, not a fit ───────────────────────────────


def test_profit_is_revenue_times_margin_rate() -> None:
    revenue = [prediction(h, 1000.0, target="revenue", series="revenue") for h in (1, 2)]
    rate = [prediction(h, 0.4, target="margin_rate", series="margin_rate") for h in (1, 2)]

    profit = derive_profit(revenue, rate, run_id="run")
    assert [row.yhat for row in profit] == [400.0, 400.0]


def test_profit_band_scales_the_revenue_band_not_both() -> None:
    """Multiplying two intervals compounds them into a useless range.

    Rate uncertainty is second-order here — a two-point rate error moves
    profit far less than a ten-percent revenue error — so the band tracks
    revenue and the rate enters at its point estimate.
    """
    revenue = [prediction(1, 1000.0, target="revenue", series="revenue", spread=100.0)]
    rate = [prediction(1, 0.4, target="margin_rate", series="margin_rate", spread=0.05)]

    profit = derive_profit(revenue, rate, run_id="run")[0]
    assert profit.yhat_lower == pytest.approx(900.0 * 0.4)
    assert profit.yhat_upper == pytest.approx(1100.0 * 0.4)


def test_profit_skips_horizons_with_no_rate_forecast() -> None:
    """Better to return fewer rows than to silently assume last week's margin."""
    revenue = [prediction(h, 1000.0, target="revenue", series="revenue") for h in (1, 2, 3)]
    rate = [prediction(1, 0.4, target="margin_rate", series="margin_rate")]

    assert len(derive_profit(revenue, rate, run_id="run")) == 1


def test_profit_is_marked_derived() -> None:
    revenue = [prediction(1, 1000.0, target="revenue", series="revenue")]
    rate = [prediction(1, 0.4, target="margin_rate", series="margin_rate")]

    row = derive_profit(revenue, rate, run_id="run")[0]
    assert row.model_class == "derived"
    assert row.target == "profit"
