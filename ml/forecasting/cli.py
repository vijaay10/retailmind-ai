"""Operator entrypoint for the forecasting job.

    retailmind-forecast train --warehouse .local/retailmind.duckdb

Training runs *after* a dbt build, because it reads the marts, and its output
is picked up by the *next* build, which unions it into fct_forecast. That
two-pass shape is inherent to putting a model between two warehouse layers,
and stating it here is cheaper than letting an operator discover it from an
empty scoreboard.
"""

import json
from pathlib import Path
from typing import Annotated

import typer

from forecasting.pipeline import DEFAULT_FOLDS, DEFAULT_HORIZON, run_training
from forecasting.registry import ModelRegistry
from forecasting.targets import TARGETS

app = typer.Typer(help="Train, inspect, and publish RetailMind forecasts.", no_args_is_help=True)

DEFAULT_WAREHOUSE = Path(".local/retailmind.duckdb")
DEFAULT_REGISTRY = Path(".local/models")


@app.command()
def train(
    warehouse: Annotated[Path, typer.Option(help="DuckDB warehouse path")] = DEFAULT_WAREHOUSE,
    registry: Annotated[Path, typer.Option(help="Model registry root")] = DEFAULT_REGISTRY,
    horizon: Annotated[int, typer.Option(help="Days ahead to forecast")] = DEFAULT_HORIZON,
    folds: Annotated[int, typer.Option(help="Rolling-origin backtest folds")] = DEFAULT_FOLDS,
    demand_series: Annotated[int, typer.Option(help="Top SKU-store series to fit")] = 25,
) -> None:
    """Backtest every candidate, gate the winner, and publish forecasts."""
    report = run_training(
        warehouse, registry, horizon=horizon, folds=folds, demand_series_limit=demand_series
    )

    typer.echo(f"\nrun {report.run_id}")
    typer.echo(f"  predictions:  {report.predictions_written:,}")
    typer.echo(f"  explanations: {report.explanations_written:,}")
    if report.skipped:
        typer.echo(f"  skipped:      {len(report.skipped)} series (too short to fit)")

    typer.echo(f"\n  {'target':<12}{'series':<18}{'champion':<22}{'WAPE':>8}{'MASE':>8}  gate")
    for result in report.results:
        score = result.score
        verdict = "challenger accepted" if result.decision.promoted else "baseline retained"
        typer.echo(
            f"  {result.target:<12}{result.series_key[:17]:<18}{result.champion_name:<22}"
            f"{score.wape:>8.3f}{score.mase:>8.3f}  {verdict}"
        )

    # A model that fails its gate is not an error — it is the gate working —
    # so this exits zero. Only an unusable run should fail a scheduler.
    if not report.predictions_written:
        typer.echo("\nno forecasts published", err=True)
        raise typer.Exit(code=1)


@app.command()
def models(
    registry: Annotated[Path, typer.Option(help="Model registry root")] = DEFAULT_REGISTRY,
) -> None:
    """List stored models and the champion for each series."""
    store = ModelRegistry(registry)
    for target in store.targets():
        typer.echo(f"\n{target}")
        target_dir = registry / target
        for series_dir in sorted(p for p in target_dir.iterdir() if p.is_dir()):
            versions = store.versions(target, series_dir.name)
            champion = store.champion_version(target, series_dir.name)
            typer.echo(f"  {series_dir.name:<24} {len(versions)} version(s), champion {champion}")


@app.command()
def card(
    target: Annotated[str, typer.Argument(help="Forecast target")],
    series: Annotated[str, typer.Argument(help="Series key")],
    registry: Annotated[Path, typer.Option(help="Model registry root")] = DEFAULT_REGISTRY,
) -> None:
    """Print the model card for a series' champion."""
    store = ModelRegistry(registry)
    version = store.champion_version(target, series)
    if version is None:
        typer.echo(f"no champion for {target}/{series}", err=True)
        raise typer.Exit(code=1)
    typer.echo(json.dumps(store.load_card(target, series, version), indent=2))


@app.command()
def targets() -> None:
    """Describe what each target is and how it is produced."""
    for spec in TARGETS.values():
        typer.echo(f"\n{spec.key}  [{spec.kind}]  {spec.label} ({spec.unit})")
        typer.echo(f"  {spec.description}")
        if spec.derived_from:
            typer.echo(f"  derived from: {', '.join(spec.derived_from)}")
        if spec.caveat:
            typer.echo(f"  caveat: {spec.caveat}")


if __name__ == "__main__":
    app()
