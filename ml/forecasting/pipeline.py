"""The training pipeline: load → backtest → gate → persist → forecast.

The order matters. **Backtesting happens before promotion, and promotion
happens before anything is published.** A model that fails the gate is still
stored, with its card and its numbers, but the champion pointer does not move
and its forecasts are not written as production output. Keeping the rejected
version is deliberate: "we tried this and it lost to the baseline by 4%" is
the institutional memory that stops the same idea being re-proposed annually.

Every fitted target follows the same path, so revenue, units, and demand are
judged by identical rules. The derived targets — inventory and profit — are
computed from those outputs afterwards, through the relationships documented
in ``targets``, and cannot contradict them.
"""

import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import structlog

from forecasting import warehouse
from forecasting.backtest import DEFAULT_INTERVAL_LEVEL, compare, rolling_origin_backtest
from forecasting.metrics import ForecastScore
from forecasting.models.baselines import MovingAverage, SeasonalNaive
from forecasting.models.ridge import RidgeForecaster
from forecasting.registry import (
    ModelCard,
    ModelRegistry,
    PromotionDecision,
    decide_promotion,
    new_version,
)
from forecasting.series import MIN_HISTORY_DAYS, TimeSeries
from forecasting.targets import get_target

log = structlog.get_logger(__name__)

DEFAULT_HORIZON = 14

#: Backtest folds. The fold count determines how many residuals each horizon
#: gets, and the residuals are what the prediction interval is built from — so
#: this is an interval-calibration setting as much as an accuracy one. Twenty
#: is what six months of daily history supports at a fortnight's horizon with
#: origins a week apart; the backtest silently uses fewer on shorter series.
DEFAULT_FOLDS = 20

#: Minimum share of days with movement before a feature model is even
#: attempted on a demand series. Below this the series is intermittent, a
#: regression fits the mean of a mostly-zero sequence, and the output is a
#: confident fractional forecast for an item that sells in whole units or not
#: at all. Those series get the baseline, which at least cannot pretend.
MIN_NON_ZERO_SHARE = 0.30

#: How many top contributions to persist per forecast. All twelve would make
#: the explanation table twelve times the prediction table to say the same
#: thing — the tail of a ridge explanation is noise around zero.
EXPLAIN_TOP_N = 5


@dataclass(frozen=True, slots=True)
class TrainingResult:
    """What one target's training run produced."""

    target: str
    series_key: str
    champion_name: str
    decision: PromotionDecision
    version: str
    score: ForecastScore
    predictions: list[warehouse.PredictionRow] = field(default_factory=list)
    explanations: list[warehouse.ExplanationRow] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _candidates() -> list[Any]:
    """Model factories, baseline first.

    Seasonal naive is not optional. It is the MASE denominator and the
    incumbent in the gate, so it runs on every series whether or not anyone
    intends to use it.
    """
    return [
        lambda: SeasonalNaive(),
        lambda: MovingAverage(),
        lambda: RidgeForecaster(),
    ]


def train_series(
    series: TimeSeries,
    *,
    target: str,
    registry: ModelRegistry,
    snapshot: str,
    horizon: int = DEFAULT_HORIZON,
    folds: int = DEFAULT_FOLDS,
    interval_level: float = DEFAULT_INTERVAL_LEVEL,
    run_id: str = "",
) -> TrainingResult:
    """Backtest every candidate on one series, gate the winner, persist it."""
    spec = get_target(target)
    series.require_history()
    run_id = run_id or str(uuid.uuid4())
    notes: list[str] = []

    eligible = _candidates()
    if series.non_zero_share < MIN_NON_ZERO_SHARE:
        # Intermittent series get baselines only. A ridge on a series that is
        # 80% zeros produces smooth fractional demand for an item that sells
        # in ones, and the reorder maths downstream would treat that
        # smoothness as information.
        eligible = eligible[:2]
        notes.append(
            f"intermittent series ({series.non_zero_share:.0%} of days with movement, "
            f"floor {MIN_NON_ZERO_SHARE:.0%}) — feature model not attempted"
        )

    results = []
    for build in eligible:
        try:
            results.append(
                rolling_origin_backtest(
                    series,
                    build,
                    horizon=horizon,
                    folds=folds,
                    interval_level=interval_level,
                )
            )
        except (ValueError, RuntimeError) as error:
            notes.append(f"{getattr(build(), 'name', 'candidate')} skipped: {error}")

    if not results:
        raise ValueError(f"{series.key}: no candidate model could be evaluated")

    ranked = compare(results)
    baseline = next((r for r in results if r.model_name.startswith("seasonal_naive")), ranked[-1])
    best = ranked[0]

    decision = decide_promotion(
        champion_name=baseline.model_name,
        champion=baseline.score,
        challenger_name=best.model_name,
        challenger=best.score,
    )

    # The published model is the challenger only if it cleared the gate.
    # Otherwise the baseline ships — which is the whole point of having a gate.
    winner = best if decision.promoted else baseline
    if not decision.promoted and best.model_name != baseline.model_name:
        notes.append(decision.reason)

    # Two different things are called "promotion" and conflating them makes
    # the logs lie. `decision.promoted` is whether the challenger won; the
    # champion pointer is which model is live. When the challenger loses, the
    # baseline is live — that is a successful outcome, not a failed promotion.
    ship_reason = (
        decision.reason
        if decision.promoted
        else f"{winner.model_name} retained as champion — {decision.reason}"
    )

    model = _refit(winner.model_name, series, horizon=horizon, bands=winner.bands)
    version = new_version()

    card = ModelCard(
        target=target,
        series_key=series.key,
        model_name=winner.model_name,
        model_class=getattr(model, "model_class", "model"),
        version=version,
        created_at=date.today().isoformat(),
        training_start=series.start.isoformat(),
        training_end=series.end.isoformat(),
        training_days=len(series),
        horizon=horizon,
        data_snapshot_id=snapshot,
        feature_names=list(getattr(model, "feature_names", ())),
        metrics=winner.score.as_dict(),
        challenger_accepted=decision.promoted,
        promotion_reason=ship_reason,
        baseline_name=baseline.model_name,
        baseline_wape=baseline.score.wape,
        improvement_over_baseline=decision.improvement,
        notes=notes + ([spec.caveat] if spec.caveat else []),
    )
    registry.save(target=target, model=model, card=card)
    registry.promote(target, series.key, version, reason=ship_reason)

    predictions, explanations = _forecast_forward(
        model, series, target=target, run_id=run_id, horizon=horizon
    )

    return TrainingResult(
        target=target,
        series_key=series.key,
        champion_name=winner.model_name,
        decision=decision,
        version=version,
        score=winner.score,
        predictions=predictions,
        explanations=explanations,
        notes=notes,
    )


def _refit(
    model_name: str, series: TimeSeries, *, horizon: int, bands: dict[int, tuple[float, float]]
) -> Any:
    """Refit the winning model on the *full* series before publishing.

    The backtest deliberately withholds recent data to score honestly. Once
    the winner is chosen, throwing away the most recent weeks would be
    perverse: the model that ships should see everything known. The bands come
    from the backtest, so the interval still reflects genuinely out-of-sample
    error rather than the residuals of a model fitted to its own test data.
    """
    model: Any
    if model_name.startswith("seasonal_naive"):
        model = SeasonalNaive()
    elif model_name.startswith("moving_average"):
        model = MovingAverage()
    else:
        model = RidgeForecaster()
    model.fit(series, horizon=horizon)
    model.bands = bands
    return model


def _forecast_forward(
    model: Any,
    series: TimeSeries,
    *,
    target: str,
    run_id: str,
    horizon: int,
) -> tuple[list[warehouse.PredictionRow], list[warehouse.ExplanationRow]]:
    """Produce and shape the forward forecast for persistence."""
    predictions: list[warehouse.PredictionRow] = []
    explanations: list[warehouse.ExplanationRow] = []

    for prediction in model.predict(series):
        business_date = series.end + timedelta(days=prediction.horizon)
        predictions.append(
            warehouse.PredictionRow(
                run_id=run_id,
                target=target,
                series_key=series.key,
                model_name=model.name,
                model_class=getattr(model, "model_class", "model"),
                origin_date=series.end,
                business_date=business_date,
                horizon=prediction.horizon,
                yhat=prediction.point,
                yhat_lower=prediction.lower,
                yhat_upper=prediction.upper,
            )
        )

        explanation = model.explain(series, horizon=prediction.horizon)
        for contribution in explanation.top(EXPLAIN_TOP_N):
            explanations.append(
                warehouse.ExplanationRow(
                    run_id=run_id,
                    target=target,
                    series_key=series.key,
                    business_date=business_date,
                    horizon=prediction.horizon,
                    feature=contribution.feature,
                    feature_value=contribution.value,
                    effect=contribution.effect,
                    baseline=explanation.baseline,
                )
            )

    return predictions, explanations


# ── Derived targets ──────────────────────────────────────────────────


def derive_profit(
    revenue: list[warehouse.PredictionRow],
    margin_rate: list[warehouse.PredictionRow],
    *,
    run_id: str,
) -> list[warehouse.PredictionRow]:
    """Profit = forecast revenue × forecast margin rate.

    The interval multiplies the revenue band by the *point* rate rather than
    combining both bands. Multiplying two intervals compounds them into a
    range so wide it stops constraining anything, and rate uncertainty is
    second-order here — a two-point rate error moves profit far less than a
    ten-percent revenue error does.
    """
    rates = {row.horizon: row for row in margin_rate}
    derived: list[warehouse.PredictionRow] = []

    for row in revenue:
        rate = rates.get(row.horizon)
        if rate is None:
            continue
        derived.append(
            warehouse.PredictionRow(
                run_id=run_id,
                target="profit",
                series_key="profit",
                model_name=f"derived({row.model_name}×margin_rate)",
                model_class="derived",
                origin_date=row.origin_date,
                business_date=row.business_date,
                horizon=row.horizon,
                yhat=row.yhat * rate.yhat,
                yhat_lower=row.yhat_lower * rate.yhat,
                yhat_upper=row.yhat_upper * rate.yhat,
            )
        )
    return derived


def project_inventory(
    connection: duckdb.DuckDBPyConnection,
    demand: list[warehouse.PredictionRow],
    *,
    run_id: str,
) -> list[warehouse.PredictionRow]:
    """Roll stock forward through the inventory identity.

        closing = opening − forecast demand + scheduled receipts

    Clamped at zero, because stock does not go negative: what a regression
    would report as −40 units is really a stockout plus 40 units of unmet
    demand, and those are different facts with different responses. The
    projection reports the stockout; the unmet demand shows up as the demand
    forecast exceeding available stock, which is what the replenishment
    surface already reads.

    Receipts come from **open purchase orders**, not from a forecast of future
    ordering. Projecting hypothetical orders would let the stock projection
    quietly assume the replenishment it is supposed to be justifying.
    """
    positions = connection.execute(
        f"""
        select sku, store_id, on_hand_qty, on_order_total, contract_lead_time_days
        from {warehouse.SEMANTIC_SCHEMA}.v_mart_inventory_health
        """  # noqa: S608 — schema name is a package constant
    ).fetchall()
    opening = {
        f"{row[0]}|{row[1]}": (float(row[2] or 0), float(row[3] or 0), int(row[4] or 14))
        for row in positions
    }

    by_series: dict[str, list[warehouse.PredictionRow]] = {}
    for row in demand:
        by_series.setdefault(row.series_key, []).append(row)

    projected: list[warehouse.PredictionRow] = []
    for series_key, rows in by_series.items():
        position = opening.get(series_key)
        if position is None:
            continue
        on_hand, on_order, lead_time = position

        stock = on_hand
        stock_low = on_hand
        stock_high = on_hand
        for row in sorted(rows, key=lambda r: r.horizon):
            # The open order lands once, on its contracted arrival day. A
            # smooth daily drip would let the projection avoid every stockout
            # by receiving a fraction of a delivery each morning.
            receipt = on_order if row.horizon == lead_time else 0.0

            stock = max(0.0, stock - row.yhat + receipt)
            # The stock band inverts the demand band: heavy demand depletes
            # faster, so the *upper* demand bound produces the *lower* stock.
            stock_low = max(0.0, stock_low - row.yhat_upper + receipt)
            stock_high = max(0.0, stock_high - row.yhat_lower + receipt)

            projected.append(
                warehouse.PredictionRow(
                    run_id=run_id,
                    target="inventory",
                    series_key=series_key,
                    model_name=f"projected({row.model_name})",
                    model_class="derived",
                    origin_date=row.origin_date,
                    business_date=row.business_date,
                    horizon=row.horizon,
                    yhat=stock,
                    yhat_lower=stock_low,
                    yhat_upper=stock_high,
                )
            )

    return projected


# ── Orchestration ────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class PipelineReport:
    run_id: str
    results: list[TrainingResult]
    predictions_written: int
    explanations_written: int
    skipped: list[str] = field(default_factory=list)


def run_training(
    warehouse_path: Path,
    registry_root: Path,
    *,
    horizon: int = DEFAULT_HORIZON,
    folds: int = DEFAULT_FOLDS,
    demand_series_limit: int = 25,
    targets: tuple[str, ...] = ("revenue", "sales", "demand", "profit", "inventory"),
) -> PipelineReport:
    """Train every requested target and publish the results."""
    run_id = str(uuid.uuid4())
    registry = ModelRegistry(registry_root)
    connection = warehouse.connect(warehouse_path)

    try:
        snapshot = warehouse.snapshot_id(connection)
        results: list[TrainingResult] = []
        skipped: list[str] = []
        predictions: list[warehouse.PredictionRow] = []
        explanations: list[warehouse.ExplanationRow] = []
        revenue_rows: list[warehouse.PredictionRow] = []
        demand_rows: list[warehouse.PredictionRow] = []

        for key in ("revenue", "sales"):
            if key not in targets:
                continue
            spec = get_target(key)
            series = warehouse.read_daily_series(
                connection, key=key, column=spec.source_column, gap_policy=spec.gap_policy
            )
            result = train_series(
                series,
                target=key,
                registry=registry,
                snapshot=snapshot,
                horizon=horizon,
                folds=folds,
                run_id=run_id,
            )
            results.append(result)
            predictions.extend(result.predictions)
            explanations.extend(result.explanations)
            if key == "revenue":
                revenue_rows = result.predictions

        if "demand" in targets:
            series_list = warehouse.read_demand_series(
                connection, limit=demand_series_limit, min_days=MIN_HISTORY_DAYS
            )
            for series in series_list:
                try:
                    result = train_series(
                        series,
                        target="demand",
                        registry=registry,
                        snapshot=snapshot,
                        horizon=horizon,
                        folds=folds,
                        run_id=run_id,
                    )
                except ValueError as error:
                    skipped.append(f"{series.key}: {error}")
                    continue
                results.append(result)
                predictions.extend(result.predictions)
                demand_rows.extend(result.predictions)

        if "profit" in targets and revenue_rows:
            rate_series = warehouse.read_margin_rate(connection)
            rate_result = train_series(
                rate_series,
                target="margin_rate",
                registry=registry,
                snapshot=snapshot,
                horizon=horizon,
                folds=folds,
                run_id=run_id,
            )
            results.append(rate_result)
            predictions.extend(derive_profit(revenue_rows, rate_result.predictions, run_id=run_id))

        if "inventory" in targets and demand_rows:
            predictions.extend(project_inventory(connection, demand_rows, run_id=run_id))

        written = warehouse.write_predictions(connection, predictions)
        explained = warehouse.write_explanations(connection, explanations)

        for result in results:
            warehouse.write_run(
                connection,
                {
                    "run_id": run_id,
                    "target": result.target,
                    "model_name": result.champion_name,
                    "model_class": "baseline"
                    if result.champion_name.startswith(("seasonal_naive", "moving_average"))
                    else "model",
                    "version": result.version,
                    "promoted": result.decision.promoted,
                    "promotion_reason": result.decision.reason,
                    "horizon": horizon,
                    "training_start": None,
                    "training_end": None,
                    "data_snapshot_id": snapshot,
                    "wape": _finite(result.score.wape),
                    "mase": _finite(result.score.mase),
                    "bias": _finite(result.score.bias),
                    "interval_coverage": result.score.interval_coverage,
                    "evaluation_points": result.score.points,
                },
            )

        log.info(
            "forecast.training_complete",
            run_id=run_id,
            targets=len(results),
            predictions=written,
            skipped=len(skipped),
        )
        return PipelineReport(
            run_id=run_id,
            results=results,
            predictions_written=written,
            explanations_written=explained,
            skipped=skipped,
        )
    finally:
        connection.close()


def _finite(value: float) -> float | None:
    """NaN is not representable in SQL; store the absence instead."""
    return float(value) if np.isfinite(value) else None
