"""Reading series from the warehouse and writing forecasts back.

**Series are read from the semantic views, not from the raw facts.** The
forecast has to predict the same "net revenue" the dashboard reports, and the
only way to guarantee that is to read the same definition. Re-deriving revenue
in Python would produce a second definition that agrees today and drifts the
first time discounts or returns are handled differently — and the divergence
would surface as an apparently inaccurate forecast rather than as the
definition mismatch it actually is.

**Forecasts are written to a schema this package owns.** dbt reads them as a
source and unions them into ``fct_forecast`` alongside the in-warehouse
baseline, so the accuracy scoreboard grades every model through one path. The
tables are created on write, so a warehouse that has never been trained
against still builds.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import duckdb
import structlog

from forecasting.series import GapPolicy, TimeSeries, build_series

log = structlog.get_logger(__name__)

#: Schema owned by the training job. Kept separate from the dbt-managed
#: schemas so a `dbt build --full-refresh` can never drop model output, and so
#: it is obvious which artefacts a rebuild reproduces and which it does not.
ML_SCHEMA = "analytics_ml"

SEMANTIC_SCHEMA = "analytics_semantic"

PREDICTIONS_TABLE = f"{ML_SCHEMA}.forecast_predictions"
RUNS_TABLE = f"{ML_SCHEMA}.forecast_runs"
EXPLANATIONS_TABLE = f"{ML_SCHEMA}.forecast_explanations"

# Statements are assembled once, here, from the table constants above — never
# from anything a caller supplies. Every value is bound as a parameter, so the
# only interpolation is the identifier this module itself owns.
INSERT_RUN = f"""
    insert into {RUNS_TABLE} (
        run_id, target, model_name, model_class, version, promoted,
        promotion_reason, horizon, training_start, training_end,
        data_snapshot_id, wape, mase, bias, interval_coverage, evaluation_points
    ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""  # noqa: S608
INSERT_PREDICTION = f"insert into {PREDICTIONS_TABLE} values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"  # noqa: S608
INSERT_EXPLANATION = f"insert into {EXPLANATIONS_TABLE} values (?, ?, ?, ?, ?, ?, ?, ?, ?)"  # noqa: S608
DELETE_PREDICTIONS_FOR_TARGET = f"delete from {PREDICTIONS_TABLE} where target = ?"  # noqa: S608
DELETE_RUN_FOR_TARGET = f"delete from {RUNS_TABLE} where run_id = ? and target = ?"  # noqa: S608
DELETE_EXPLANATIONS_FOR_TARGET = f"delete from {EXPLANATIONS_TABLE} where target = ?"  # noqa: S608


@dataclass(frozen=True, slots=True)
class PredictionRow:
    """One forecast, at one horizon, from one run."""

    run_id: str
    target: str
    series_key: str
    model_name: str
    model_class: str
    origin_date: date
    business_date: date
    horizon: int
    yhat: float
    yhat_lower: float
    yhat_upper: float


@dataclass(frozen=True, slots=True)
class ExplanationRow:
    """One feature's contribution to one forecast."""

    run_id: str
    target: str
    series_key: str
    business_date: date
    horizon: int
    feature: str
    feature_value: float
    effect: float
    baseline: float


def connect(path: Path, *, read_only: bool = False) -> duckdb.DuckDBPyConnection:
    """Open the warehouse with the session timezone pinned to UTC.

    The pin matters for the same reason it matters in ingestion: date
    arithmetic that resolves against the host timezone produces different
    business dates on a laptop and in CI, and a forecast keyed to the wrong
    day is silently wrong rather than loudly broken.
    """
    connection = duckdb.connect(str(path), read_only=read_only)
    connection.execute("SET TimeZone = 'UTC'")
    return connection


# ── Reading ──────────────────────────────────────────────────────────


def read_daily_series(
    connection: duckdb.DuckDBPyConnection,
    *,
    key: str,
    column: str,
    gap_policy: GapPolicy,
    view: str = "v_mart_kpi_daily",
) -> TimeSeries:
    """One network-level daily series from a governed semantic view."""
    rows = connection.execute(
        f"select business_date, {column} from {SEMANTIC_SCHEMA}.{view} order by business_date"  # noqa: S608 — view and column are package constants, never caller input
    ).fetchall()
    if not rows:
        raise ValueError(f"{key}: {view} returned no rows — has the warehouse been built?")

    observations = {row[0]: float(row[1] or 0.0) for row in rows}
    return build_series(key, observations, gap_policy=gap_policy)


def read_demand_series(
    connection: duckdb.DuckDBPyConnection,
    *,
    limit: int,
    min_days: int,
) -> list[TimeSeries]:
    """Per SKU × store demand series, busiest first.

    Limited by design. Forecasting every SKU-store pair in a real estate is
    hundreds of thousands of series, most of which sell single digits a month
    and cannot support a fitted model at all. The pipeline forecasts the head
    of the distribution and lets the reorder maths fall back to trailing
    averages for the tail — which is what the tail deserves.
    """
    rows = connection.execute(
        f"""
        with ranked as (
            select sku, store_id, sum(units) as total_units, count(*) as days
            from (
                select sku, store_id, business_date, sum(quantity) as units
                from {SEMANTIC_SCHEMA}.v_fct_sales
                where not is_return
                group by 1, 2, 3
            )
            group by 1, 2
            having count(*) >= ?
            order by total_units desc
            limit ?
        )
        select d.sku, d.store_id, d.business_date, d.units
        from (
            select sku, store_id, business_date, sum(quantity) as units
            from {SEMANTIC_SCHEMA}.v_fct_sales
            where not is_return
            group by 1, 2, 3
        ) d
        join ranked r on d.sku = r.sku and d.store_id = r.store_id
        order by d.sku, d.store_id, d.business_date
        """,  # noqa: S608 — schema name is a package constant
        [min_days, limit],
    ).fetchall()

    if not rows:
        return []

    span = connection.execute(
        f"select min(business_date), max(business_date) from {SEMANTIC_SCHEMA}.v_fct_sales"  # noqa: S608
    ).fetchone()
    if span is None or span[0] is None:
        raise ValueError("v_fct_sales has no rows — the calendar span is undefined")
    first, last = span[0], span[1]

    grouped: dict[str, dict[date, float]] = {}
    for sku, store_id, business_date, units in rows:
        grouped.setdefault(f"{sku}|{store_id}", {})[business_date] = float(units or 0.0)

    # Every demand series spans the full calendar: a day absent from the sales
    # table is a day nobody bought the item, and it has to be present as a
    # zero or the weekly pattern is computed over the wrong denominator.
    return [
        build_series(key, observations, gap_policy=GapPolicy.ZERO, start=first, end=last)
        for key, observations in grouped.items()
    ]


def read_margin_rate(connection: duckdb.DuckDBPyConnection) -> TimeSeries:
    """Daily margin rate — the stable half of the profit decomposition."""
    return read_daily_series(
        connection, key="margin_rate", column="margin_rate", gap_policy=GapPolicy.ERROR
    )


def snapshot_id(connection: duckdb.DuckDBPyConnection) -> str:
    """Identifier for the warehouse state the model was trained on.

    Written into every model card. Without it, "the forecast was wrong" and
    "the forecast was trained before the restatement" are indistinguishable
    months later.
    """
    row = connection.execute(
        f"select max(business_date), count(*) from {SEMANTIC_SCHEMA}.v_mart_kpi_daily"  # noqa: S608
    ).fetchone()
    if row is None:
        raise ValueError("v_mart_kpi_daily returned no rows — has the warehouse been built?")
    return f"kpi:{row[0]}:{row[1]}"


# ── Writing ──────────────────────────────────────────────────────────


def ensure_schema(connection: duckdb.DuckDBPyConnection) -> None:
    """Create the ML tables if absent.

    Idempotent and called on every write, so a fresh clone can train without a
    separate migration step, and dbt's source for these tables always exists
    even before the first training run.
    """
    connection.execute(f"create schema if not exists {ML_SCHEMA}")
    connection.execute(
        f"""
        create table if not exists {RUNS_TABLE} (
            run_id varchar not null,
            target varchar not null,
            model_name varchar not null,
            model_class varchar not null,
            version varchar not null,
            promoted boolean not null,
            promotion_reason varchar,
            horizon integer not null,
            training_start date,
            training_end date,
            data_snapshot_id varchar,
            wape double,
            mase double,
            bias double,
            interval_coverage double,
            evaluation_points integer,
            created_at timestamp default current_timestamp
        )
        """
    )
    connection.execute(
        f"""
        create table if not exists {PREDICTIONS_TABLE} (
            run_id varchar not null,
            target varchar not null,
            series_key varchar not null,
            model_name varchar not null,
            model_class varchar not null,
            origin_date date not null,
            business_date date not null,
            horizon integer not null,
            yhat double,
            yhat_lower double,
            yhat_upper double
        )
        """
    )
    connection.execute(
        f"""
        create table if not exists {EXPLANATIONS_TABLE} (
            run_id varchar not null,
            target varchar not null,
            series_key varchar not null,
            business_date date not null,
            horizon integer not null,
            feature varchar not null,
            feature_value double,
            effect double,
            baseline double
        )
        """
    )


def write_run(connection: duckdb.DuckDBPyConnection, record: dict[str, Any]) -> None:
    """Record one target's training outcome, replacing any earlier attempt.

    Delete-then-insert rather than an upsert on a declared key. The table's
    DDL exists in two places — here and in the dbt on-run-start hook, because
    dbt has to be able to build against a warehouse that has never been
    trained — and whichever runs first wins. Depending on a PRIMARY KEY
    surviving that race is depending on build order, and the failure mode is
    an insert that raises in production and not in a fresh test warehouse.

    The natural key is (run_id, target), not run_id: one run trains every
    target, so run_id alone was never unique here.
    """
    ensure_schema(connection)
    connection.execute(DELETE_RUN_FOR_TARGET, [record["run_id"], record["target"]])
    connection.execute(
        INSERT_RUN,
        [
            record["run_id"],
            record["target"],
            record["model_name"],
            record["model_class"],
            record["version"],
            record["promoted"],
            record["promotion_reason"],
            record["horizon"],
            record["training_start"],
            record["training_end"],
            record["data_snapshot_id"],
            record["wape"],
            record["mase"],
            record["bias"],
            record["interval_coverage"],
            record["evaluation_points"],
        ],
    )


def write_predictions(connection: duckdb.DuckDBPyConnection, rows: Iterable[PredictionRow]) -> int:
    ensure_schema(connection)
    payload = [
        (
            row.run_id,
            row.target,
            row.series_key,
            row.model_name,
            row.model_class,
            row.origin_date,
            row.business_date,
            row.horizon,
            row.yhat,
            row.yhat_lower,
            row.yhat_upper,
        )
        for row in rows
    ]
    if not payload:
        return 0

    # A run is replaced wholesale rather than appended to, so re-running the
    # trainer for a target is idempotent instead of quietly doubling its rows.
    targets = {row[1] for row in payload}
    for target in targets:
        connection.execute(DELETE_PREDICTIONS_FOR_TARGET, [target])

    connection.executemany(INSERT_PREDICTION, payload)
    log.info("forecast.predictions_written", rows=len(payload), targets=sorted(targets))
    return len(payload)


def write_explanations(
    connection: duckdb.DuckDBPyConnection, rows: Iterable[ExplanationRow]
) -> int:
    ensure_schema(connection)
    payload = [
        (
            row.run_id,
            row.target,
            row.series_key,
            row.business_date,
            row.horizon,
            row.feature,
            row.feature_value,
            row.effect,
            row.baseline,
        )
        for row in rows
    ]
    if not payload:
        return 0

    targets = {row[1] for row in payload}
    for target in targets:
        connection.execute(DELETE_EXPLANATIONS_FOR_TARGET, [target])

    connection.executemany(INSERT_EXPLANATION, payload)
    return len(payload)
