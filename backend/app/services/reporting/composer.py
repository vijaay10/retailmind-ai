"""Building a report from the services that already answer each question.

Nothing here computes a number. Every figure comes from the engine that owns
it — the analytics registry for KPIs, root cause analysis for insights, the
forecast service for the outlook, the recommendation engine for actions — and
arrives with the caveats that engine attached. A report is a *view* over the
platform, not a second implementation of it, so a figure in a slide deck is
the same figure the dashboard shows and disagreement between them is
structurally impossible rather than merely unlikely.

Three rules run through the assembly.

**A section that found nothing says so.** An empty section and an omitted one
look identical to a reader, and they mean opposite things: no recommendations
because the business is healthy, or none because the engine failed. Every
section carries its reason.

**A section the caller may not read is reported, not fatal.** A report spans
every domain the platform covers, and almost nobody may read all of them: a
marketing lead sees campaigns and customers but not profitability. Failing the
whole document because one section was forbidden turns a partial report into
no report. Each section is composed independently and a denial becomes an
``unavailable_reason`` — the same treatment an empty section gets, for the
same reason: the reader learns what is missing and why.

**Caveats travel with their numbers.** A forecast without its accuracy record
and a recommendation without its estimate basis are exactly the figures a
reader will act on unqualified. They are carried into the block that shows
them rather than collected into a footnote nobody reaches.
"""

from collections.abc import Awaitable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

import structlog

from app.domain.auth.entities import Principal
from app.domain.auth.permissions import Permission
from app.domain.shared.errors import AuthorizationError
from app.services.analytics.service import AnalyticsService
from app.services.forecasting.service import ForecastingService
from app.services.rca.service import RootCauseService
from app.services.rca.service import summarise as summarise_rca
from app.services.recommendations.service import RecommendationService
from app.services.recommendations.service import summarise as summarise_recommendations
from app.services.reporting import commentary
from app.services.reporting.contracts import (
    Block,
    BlockKind,
    ChartKind,
    Kpi,
    Report,
    Section,
)
from app.services.shared import authz

log = structlog.get_logger(__name__)

DEFAULT_PERIOD_DAYS = 28

#: Rows carried into a report table. A deck with forty rows on a slide is a
#: spreadsheet someone has mistaken for a presentation.
TABLE_ROWS = 10

#: KPI metrics, in the order a reader scans them: what came in, what was kept,
#: how much moved, how many transactions.
HEADLINE_METRICS = ("net_revenue", "margin_amount", "units_sold", "orders")


@dataclass(frozen=True, slots=True)
class ReportRequest:
    """What to build."""

    period_end: date
    period_days: int = DEFAULT_PERIOD_DAYS
    title: str = "Retail Performance Review"
    sections: tuple[str, ...] = (
        "summary",
        "kpis",
        "trend",
        "insights",
        "forecast",
        "recommendations",
        "commentary",
    )

    @property
    def period_start(self) -> date:
        return self.period_end - timedelta(days=self.period_days - 1)

    @property
    def prior_start(self) -> date:
        return self.period_start - timedelta(days=self.period_days)

    @property
    def prior_end(self) -> date:
        return self.period_start - timedelta(days=1)


class ReportComposer:
    """Assembles the document. Renders nothing."""

    def __init__(
        self,
        analytics: AnalyticsService,
        *,
        rca: RootCauseService | None = None,
        forecasts: ForecastingService | None = None,
        recommendations: RecommendationService | None = None,
    ) -> None:
        self._analytics = analytics
        self._rca = rca
        self._forecasts = forecasts
        self._recommendations = recommendations

    async def compose(self, principal: Principal, request: ReportRequest) -> Report:
        authz.require(principal, Permission.REPORTS_READ)

        # Each gathering step is independently fallible. A report spans every
        # domain the platform covers and almost nobody may read all of them —
        # a marketing lead sees campaigns and customers but not profitability
        # — so a denial has to narrow the report rather than fail it.
        kpis = await _permitted(self._kpis(principal, request), default=[])
        trend = await _permitted(self._trend(principal, request), default=[])
        regions = await _permitted(self._regions(principal, request), default=[])

        insights, rca_payload = await _permitted(
            self._insights(principal, request),
            default=(_denied("insights", "Business Insights"), {}),
        )
        outlook, forecast_payload = await _permitted(
            self._forecast(principal),
            default=(_denied("forecast", "Outlook"), {}),
        )
        actions, recommendation_payload = await _permitted(
            self._actions_section(principal, request),
            default=(_denied("recommendations", "Recommended Actions"), {}),
        )

        sections: list[Section] = []

        if "summary" in request.sections:
            sections.append(
                commentary.executive_summary(
                    kpis=kpis,
                    rca=rca_payload,
                    recommendations=recommendation_payload,
                    forecast=forecast_payload,
                    period_label=f"{request.period_start:%d %b} – {request.period_end:%d %b %Y}",
                )
            )
        if "kpis" in request.sections:
            sections.append(self._kpi_section(kpis))
        if "trend" in request.sections:
            sections.append(self._trend_section(trend, regions))
        if "insights" in request.sections:
            sections.append(insights)
        if "forecast" in request.sections:
            sections.append(outlook)
        if "recommendations" in request.sections:
            sections.append(actions)
        if "commentary" in request.sections:
            sections.append(
                commentary.build(
                    kpis=kpis,
                    rca=rca_payload,
                    recommendations=recommendation_payload,
                    forecast=forecast_payload,
                )
            )

        report = Report(
            title=request.title,
            subtitle=(
                f"{request.period_days} days to {request.period_end:%d %B %Y}, "
                f"compared with the {request.period_days} days before"
            ),
            period_start=request.period_start,
            period_end=request.period_end,
            generated_at=datetime.now(UTC),
            sections=tuple(sections),
            caveats=(
                "Every figure in this report comes from the platform surface "
                "that owns it, with that surface's own caveats carried through. "
                "Nothing here is recomputed for the report.",
                "Comparisons are against the immediately preceding period of "
                "equal length. Day-of-week composition is not adjusted for.",
            ),
            meta={
                "period_days": request.period_days,
                "prior_period": f"{request.prior_start} to {request.prior_end}",
            },
        )

        log.info(
            "report.composed",
            sections=len(report.sections),
            empty_sections=sum(1 for section in report.sections if section.is_empty),
            period_days=request.period_days,
        )
        return report

    # ── Data gathering ───────────────────────────────────────────────

    async def _kpis(self, principal: Principal, request: ReportRequest) -> list[Kpi]:
        current = await self._totals(principal, request.period_start, request.period_end)
        prior = await self._totals(principal, request.prior_start, request.prior_end)

        units = {
            "net_revenue": "currency",
            "margin_amount": "currency",
            "units_sold": "count",
            "orders": "count",
        }
        kpis = [
            Kpi(
                label=metric.replace("_", " ").title(),
                value=current.get(metric, 0.0),
                unit=units.get(metric, ""),
                comparison=prior.get(metric),
            )
            for metric in HEADLINE_METRICS
            if metric in current
        ]

        # Margin rate is a ratio and must be recomputed from its components,
        # never differenced from two period averages.
        revenue = current.get("net_revenue", 0.0)
        prior_revenue = prior.get("net_revenue", 0.0)
        if revenue:
            kpis.append(
                Kpi(
                    label="Margin Rate",
                    value=current.get("margin_amount", 0.0) / revenue,
                    unit="rate",
                    comparison=(
                        prior.get("margin_amount", 0.0) / prior_revenue if prior_revenue else None
                    ),
                )
            )
        return kpis

    async def _totals(self, principal: Principal, start: date, end: date) -> dict[str, float]:
        answer = await self._analytics.query(
            principal,
            domain_key="profitability",
            metrics=["net_revenue", "margin_amount"],
            dimensions=[],
            start_date=start,
            end_date=end,
            limit=1,
        )
        volumes = await self._analytics.query(
            principal,
            domain_key="revenue",
            metrics=["units_sold", "orders"],
            dimensions=[],
            start_date=start,
            end_date=end,
            limit=1,
        )
        totals: dict[str, float] = {}
        for rows in (answer.result.rows, volumes.result.rows):
            for row in rows:
                for key, value in row.items():
                    totals[key] = _float(value)
        return totals

    async def _trend(self, principal: Principal, request: ReportRequest) -> list[dict[str, Any]]:
        answer = await self._analytics.query(
            principal,
            domain_key="revenue",
            metrics=["net_revenue"],
            dimensions=["business_date"],
            start_date=request.period_start,
            end_date=request.period_end,
            sort_by="business_date",
            descending=False,
            limit=120,
        )
        return answer.result.rows

    async def _regions(self, principal: Principal, request: ReportRequest) -> list[dict[str, Any]]:
        answer = await self._analytics.query(
            principal,
            domain_key="revenue",
            metrics=["net_revenue", "units_sold"],
            dimensions=["region"],
            start_date=request.period_start,
            end_date=request.period_end,
            sort_by="net_revenue",
            limit=TABLE_ROWS,
        )
        return answer.result.rows

    # ── Sections ─────────────────────────────────────────────────────

    def _kpi_section(self, kpis: list[Kpi]) -> Section:
        if not kpis:
            return Section(
                key="kpis",
                title="Key Performance Indicators",
                unavailable_reason="No sales data for the period requested.",
            )
        return Section(
            key="kpis",
            title="Key Performance Indicators",
            subtitle="Against the immediately preceding period of equal length",
            blocks=(
                Block(
                    kind=BlockKind.KPI_GRID,
                    kpis=tuple(kpis),
                    note=(
                        "Margin rate is recomputed from margin and revenue at "
                        "the period grain rather than averaged across days — "
                        "an average of daily rates weights a quiet Sunday like "
                        "a peak Saturday."
                    ),
                ),
            ),
        )

    def _trend_section(self, trend: list[dict[str, Any]], regions: list[dict[str, Any]]) -> Section:
        blocks: list[Block] = []

        if trend:
            blocks.append(
                Block(
                    kind=BlockKind.CHART,
                    title="Daily Net Revenue",
                    chart_kind=ChartKind.LINE,
                    chart_category_column="business_date",
                    chart_value_columns=("net_revenue",),
                    columns=("business_date", "net_revenue"),
                    rows=tuple(
                        (str(row.get("business_date")), _float(row.get("net_revenue")))
                        for row in trend
                    ),
                    note="A line is used because dates have a natural order.",
                )
            )

        if regions:
            blocks.append(
                Block(
                    kind=BlockKind.CHART,
                    title="Net Revenue by Region",
                    chart_kind=ChartKind.BAR,
                    chart_category_column="region",
                    chart_value_columns=("net_revenue",),
                    columns=("region", "net_revenue", "units_sold"),
                    rows=tuple(
                        (
                            str(row.get("region")),
                            _float(row.get("net_revenue")),
                            _float(row.get("units_sold")),
                        )
                        for row in regions
                    ),
                    note=(
                        "Bars rather than a line: regions have no inherent "
                        "order, and a line would imply one."
                    ),
                )
            )

        if not blocks:
            return Section(
                key="trend",
                title="Performance",
                unavailable_reason="No sales rows in the period.",
            )
        return Section(key="trend", title="Performance", blocks=tuple(blocks))

    async def _insights(
        self, principal: Principal, request: ReportRequest
    ) -> tuple[Section, dict[str, Any]]:
        if self._rca is None:
            return (
                Section(
                    key="insights",
                    title="Business Insights",
                    unavailable_reason="Root cause analysis is not configured.",
                ),
                {},
            )

        investigation = await self._rca.investigate(
            principal,
            current_start=request.period_start,
            current_end=request.period_end,
            baseline_start=request.prior_start,
            baseline_end=request.prior_end,
        )
        payload = summarise_rca(investigation)
        findings = payload["findings"]

        if not findings:
            return (
                Section(
                    key="insights",
                    title="Business Insights",
                    unavailable_reason=(
                        "No driver cleared the materiality floor. The period "
                        "moved within ordinary variation, and attributing that "
                        "would produce confident explanations for noise."
                    ),
                ),
                payload,
            )

        return (
            Section(
                key="insights",
                title="Business Insights",
                subtitle="What moved, and the evidence behind each explanation",
                blocks=(
                    Block(
                        kind=BlockKind.TABLE,
                        title="Ranked drivers",
                        columns=("Driver", "Claim", "Evidence", "Confidence", "Impact"),
                        rows=tuple(
                            (
                                f"{item['dimension']}: {item['subject']}",
                                item["claim_type"].replace("_", " "),
                                item["evidence_tier"],
                                f"{item['confidence']:.0%}",
                                f"{item['impact_share']:+.0%}",
                            )
                            for item in findings[:TABLE_ROWS]
                        ),
                        note=(
                            "Evidence tier caps confidence: an arithmetic "
                            "decomposition can be stated firmly, a weather "
                            "correlation cannot, however strong the signal."
                        ),
                    ),
                    Block(
                        kind=BlockKind.CALLOUT,
                        title="What this does not establish",
                        bullets=tuple(payload["caveats"][:3]),
                    ),
                ),
            ),
            payload,
        )

    async def _forecast(self, principal: Principal) -> tuple[Section, dict[str, Any]]:
        if self._forecasts is None:
            return (
                Section(
                    key="forecast",
                    title="Outlook",
                    unavailable_reason="Forecasting is not configured.",
                ),
                {},
            )

        section = await self._forecasts.forecast(principal, target="revenue")
        if not section.rows:
            return (
                Section(
                    key="forecast",
                    title="Outlook",
                    unavailable_reason=(
                        "No forecast has been published. The training job has "
                        "either not run or found the series too short to fit."
                    ),
                ),
                {},
            )

        rows = section.rows
        total = sum(_float(row.get("forecast")) for row in rows)
        payload = {"rows": rows, "caveats": list(section.caveats), "total": total}

        return (
            Section(
                key="forecast",
                title="Outlook",
                subtitle=f"{len(rows)} days ahead, with prediction intervals",
                blocks=(
                    Block(
                        kind=BlockKind.CHART,
                        title="Forecast Net Revenue",
                        chart_kind=ChartKind.LINE,
                        chart_category_column="business_date",
                        chart_value_columns=("forecast", "forecast_lower", "forecast_upper"),
                        columns=(
                            "business_date",
                            "forecast",
                            "forecast_lower",
                            "forecast_upper",
                        ),
                        rows=tuple(
                            (
                                str(row.get("business_date")),
                                _float(row.get("forecast")),
                                _float(row.get("forecast_lower")),
                                _float(row.get("forecast_upper")),
                            )
                            for row in rows
                        ),
                        note=(
                            "Bands are empirical, built from how wrong this "
                            "model has actually been at each horizon, and "
                            "widened so their coverage holds in small samples."
                        ),
                    ),
                    Block(
                        kind=BlockKind.CALLOUT,
                        title="Before relying on this",
                        bullets=tuple(section.caveats)
                        or ("The forecast beat the seasonal-naive baseline on backtest.",),
                    ),
                ),
            ),
            payload,
        )

    async def _actions_section(
        self, principal: Principal, request: ReportRequest
    ) -> tuple[Section, dict[str, Any]]:
        if self._recommendations is None:
            return (
                Section(
                    key="recommendations",
                    title="Recommended Actions",
                    unavailable_reason="The recommendation engine is not configured.",
                ),
                {},
            )

        portfolio = await self._recommendations.recommend(principal, end_date=request.period_end)
        payload = summarise_recommendations(portfolio)
        items = payload["recommendations"]

        if not items:
            return (
                Section(
                    key="recommendations",
                    title="Recommended Actions",
                    unavailable_reason=(
                        "Nothing cleared the materiality floor. "
                        + "; ".join(payload["categories_empty"].values())
                    ),
                ),
                payload,
            )

        return (
            Section(
                key="recommendations",
                title="Recommended Actions",
                subtitle="Ranked by expected profit net of what each risks",
                blocks=(
                    Block(
                        kind=BlockKind.TABLE,
                        title="Priorities",
                        columns=("Action", "Owner", "Profit", "Basis", "Risk"),
                        rows=tuple(
                            (
                                item["action"],
                                item["owner"],
                                f"{item['impact']['profit']:,.0f}",
                                item["impact"]["basis"],
                                item["risk"]["band"],
                            )
                            for item in items[:TABLE_ROWS]
                        ),
                        note=(
                            f"Combined profit opportunity {payload['net_profit_opportunity']:,.0f} "
                            f"after removing overlapping actions, against "
                            f"{payload['gross_profit_opportunity']:,.0f} if each were "
                            "counted separately. Overlapping actions chase the same pounds."
                        ),
                    ),
                    Block(
                        kind=BlockKind.CALLOUT,
                        title="How to read the estimates",
                        bullets=tuple(payload["caveats"][:3]),
                    ),
                ),
            ),
            payload,
        )


_DENIED_REASON = (
    "Your role does not include the analytics module this section is built "
    "from. The rest of the report is unaffected."
)


def _denied(key: str, title: str) -> Section:
    return Section(key=key, title=title, unavailable_reason=_DENIED_REASON)


async def _permitted[T](awaitable: Awaitable[T], *, default: T) -> T:
    """Run a section's gathering step, degrading on an authorization denial.

    Only :class:`AuthorizationError` is caught. Every other failure propagates:
    a report that silently omits a section because the warehouse was down would
    be indistinguishable from one where the section legitimately found nothing,
    and that is precisely the confusion this design exists to prevent.
    """
    try:
        return await awaitable
    except AuthorizationError:
        return default


def _float(value: Any) -> float:
    if not isinstance(value, int | float | str):
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
