"""Forecast serving (Analytics M7,).

Reads forecasts the training job published; never fits a model. That split is
deliberate and worth stating, because the obvious alternative — score on
demand inside the request — is worse in every dimension that matters here.
Batch training means a forecast is reproducible (the same row is served to
everyone until the next run), auditable (it has a run id, a model card, and a
scorecard), and cheap (a request is a table read). On-demand scoring would
make a forecast a function of when you asked, which is precisely the property
that makes a number impossible to defend in a planning meeting.

**Every forecast is served with its model's track record.** The point estimate,
the interval, and the out-of-sample error of the model that produced them
travel together. A forecast without its accuracy is an assertion; with it, it
is evidence a planner can weigh.
"""

from dataclasses import dataclass
from datetime import date
from typing import Any

import structlog

from app.domain.auth.entities import Principal
from app.domain.auth.permissions import Permission
from app.services.analytics.service import AnalyticsService
from app.services.shared import authz

log = structlog.get_logger(__name__)

#: MASE at or above this means the model did not beat seasonal naive. Surfaced
#: in responses rather than kept in the training logs, because "this forecast
#: is no better than assuming last week repeats" is the single most useful
#: thing a reader can know about it.
NAIVE_PARITY = 1.0

#: Relative interval width beyond which a forecast is too vague to plan
#: against. Not an error — a wide band on a volatile series is the honest
#: answer — but a reader deciding stock levels should be told.
WIDE_INTERVAL = 0.5


@dataclass(frozen=True, slots=True)
class ForecastSection:
    """Forecast rows plus the caveats a reader needs to weigh them."""

    rows: list[dict[str, Any]]
    meta: dict[str, Any]
    caveats: tuple[str, ...] = ()


class ForecastingService:
    """The forecast surfaces, all reading the governed registry."""

    def __init__(self, analytics: AnalyticsService) -> None:
        self._analytics = analytics

    # ── Forecasts ────────────────────────────────────────────────────

    async def forecast(
        self,
        principal: Principal,
        *,
        target: str,
        series: str | None = None,
        horizon: int | None = None,
        limit: int = 100,
    ) -> ForecastSection:
        """Published forecasts for one target, nearest date first.

        Grouped by date and horizon rather than aggregated across them: a
        forecast is a statement about a specific day, and a mean forecast over
        a fortnight is a number no decision consumes.
        """
        filters: dict[str, str] = {"target": target}
        if series:
            filters["series_key"] = series
        if horizon is not None:
            filters["horizon"] = str(horizon)

        section = await self._query(
            principal,
            domain="forecast",
            metrics=[
                "forecast",
                "forecast_lower",
                "forecast_upper",
                "relative_interval_width",
                "model_wape",
                "model_mase",
            ],
            dimensions=["business_date", "horizon", "series_key", "model_name"],
            filters=filters,
            sort_by="business_date",
            descending=False,
            limit=limit,
        )
        return self._with_caveats(section, target=target)

    async def totals(
        self, principal: Principal, *, target: str, by: str = "target"
    ) -> ForecastSection:
        """The horizon rolled into one number, where that is meaningful.

        Summing point forecasts across days is legitimate — a fortnight's
        expected revenue is the sum of its days. Summing the *bounds* is
        conservative rather than exact: independent daily errors partially
        cancel, so the summed band is wider than the true one. Stated in the
        caveats rather than silently corrected, because the correction depends
        on a correlation nobody here has measured.
        """
        section = await self._query(
            principal,
            domain="forecast",
            metrics=["forecast", "forecast_lower", "forecast_upper", "horizons", "series"],
            dimensions=[by],
            filters={"target": target} if by != "target" else None,
            sort_by="forecast",
        )
        return ForecastSection(
            rows=section.rows,
            meta=section.meta,
            caveats=(
                "Interval bounds are summed, not convolved: independent daily "
                "errors partially cancel, so the aggregate band is wider than "
                "the true one.",
            ),
        )

    # ── Accuracy ─────────────────────────────────────────────────────

    async def accuracy(self, principal: Principal) -> ForecastSection:
        """The scoreboard: how wrong each model has actually been.

        Published as a first-class surface rather than buried in training
        logs. A planner who can see the model's track record knows how much to
        trust the next number, and a model whose accuracy nobody can look up
        will be trusted exactly as much as its presentation deserves.
        """
        section = await self._query(
            principal,
            domain="forecast_accuracy",
            metrics=[
                "wape",
                "mape",
                "bias",
                "interval_coverage",
                "mean_absolute_error",
                "forecast_days",
                "pending_days",
            ],
            dimensions=["model_name", "model_class", "produced_by"],
            sort_by="wape",
            descending=False,
        )

        caveats: list[str] = []
        for row in section.rows:
            coverage = row.get("interval_coverage")
            if coverage is not None and float(coverage) < 0.7:
                caveats.append(
                    f"{row.get('model_name')}: interval covers "
                    f"{float(coverage):.0%} of actuals — the band is "
                    "miscalibrated and should not be planned against."
                )
            if not row.get("forecast_days"):
                caveats.append(
                    f"{row.get('model_name')}: no scored days yet — every "
                    "forecast is for a date that has not happened, so the "
                    "model has no measured accuracy."
                )
        return ForecastSection(rows=section.rows, meta=section.meta, caveats=tuple(caveats))

    async def explain(
        self,
        principal: Principal,
        *,
        target: str,
        business_date: date | None = None,
        limit: int = 50,
    ) -> ForecastSection:
        """What drove a forecast, as exact per-feature contributions.

        These are the model's own arithmetic, not a post-hoc attribution:
        `baseline + Σ effect` reconstructs the point forecast to floating
        point. That matters because a planner overriding a forecast needs a
        reason they can check, and an explanation that does not add up to the
        number it explains is a story.
        """
        filters: dict[str, str] = {"target": target}
        if business_date is not None:
            filters["business_date"] = business_date.isoformat()

        section = await self._query(
            principal,
            domain="forecast_explanation",
            metrics=["effect", "effect_magnitude", "baseline", "features"],
            dimensions=["business_date", "horizon", "feature", "direction", "series_key"],
            filters=filters,
            sort_by="effect_magnitude",
            limit=limit,
        )
        return ForecastSection(rows=section.rows, meta=section.meta)

    # ── Internals ────────────────────────────────────────────────────

    async def _query(
        self,
        principal: Principal,
        *,
        domain: str,
        metrics: list[str],
        dimensions: list[str],
        filters: dict[str, str] | None = None,
        sort_by: str | None = None,
        descending: bool = True,
        limit: int = 100,
    ) -> ForecastSection:
        authz.require(principal, Permission.FORECASTS_READ)
        answer = await self._analytics.query(
            principal,
            domain_key=domain,
            metrics=metrics,
            dimensions=dimensions,
            filters=filters,
            sort_by=sort_by,
            descending=descending,
            limit=limit,
        )
        return ForecastSection(rows=answer.result.rows, meta=answer.result.meta)

    def _with_caveats(self, section: ForecastSection, *, target: str) -> ForecastSection:
        """Attach the honest warnings a reader needs before acting.

        Computed from the rows rather than configured, so a forecast that
        degrades starts carrying its warning without anyone remembering to add
        one.
        """
        caveats: list[str] = []
        if not section.rows:
            return ForecastSection(
                rows=[],
                meta=section.meta,
                caveats=(
                    f"No published forecast for '{target}'. The training job "
                    "has either not run or found the series too short to fit.",
                ),
            )

        mase_values = [
            float(row["model_mase"]) for row in section.rows if row.get("model_mase") is not None
        ]
        if mase_values and max(mase_values) >= NAIVE_PARITY:
            caveats.append(
                f"MASE {max(mase_values):.2f} — this model does not beat "
                "seasonal naive. The forecast is no better than assuming the "
                "same weekday repeats."
            )

        widths = [
            float(row["relative_interval_width"])
            for row in section.rows
            if row.get("relative_interval_width") is not None
        ]
        if widths and max(widths) > WIDE_INTERVAL:
            caveats.append(
                f"Interval reaches ±{max(widths) / 2:.0%} of the point "
                "forecast at the far horizon — wide enough that the far end "
                "constrains little."
            )

        if caveats:
            log.info("forecast.caveats_attached", target=target, count=len(caveats))
        return ForecastSection(rows=section.rows, meta=section.meta, caveats=tuple(caveats))
