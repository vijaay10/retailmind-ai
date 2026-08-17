"""Root cause analysis orchestration (Analytics).

Given a KPI movement, this runs every investigator, ranks what they return,
and hands back a briefing rather than a dashboard.

Three properties shape the whole design.

**The arithmetic and the explanation are kept apart.** Decomposing revenue
across regions is exact and tells you *where*. Lining a stockout up against
that region tells you a candidate *why*. Merging them into a single "cause"
list with one confidence scale would make a subtraction and a correlation look
like the same kind of statement, and they are not. So findings carry their
evidence tier, and the tier caps the confidence.

**Explained share only counts arithmetic.** Weather, shipping, and returns are
alternative accounts of pounds the dimensional decomposition has already
attributed. Adding their impact to the explained total would routinely exceed
100% while every individual number stayed correct — the classic way an RCA
tool loses a reader's trust in one screen.

**A quiet answer is a valid answer.** When nothing clears the materiality
floor, the response says so. An engine that always produces three causes will
produce three causes for a random fluctuation, and a team that acts on them
learns to ignore it.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import structlog

from app.domain.auth.entities import Principal
from app.domain.auth.permissions import Permission
from app.domain.shared.errors import ValidationDomainError
from app.services.analytics.service import AnalyticsService
from app.services.rca import investigators
from app.services.rca.contracts import (
    Dimension,
    EvidenceTier,
    Finding,
    Investigation,
    Window,
)
from app.services.shared import authz

log = structlog.get_logger(__name__)

#: Default comparison: the fortnight before the period under investigation.
DEFAULT_CURRENT_DAYS = 7
DEFAULT_BASELINE_MULTIPLE = 3

#: Findings returned. Beyond this a ranked list stops being a ranking.
MAX_FINDINGS = 12

#: Findings per dimension in the ranked briefing. Without a cap the cut with
#: the most slices wins the whole page: five variations on "some segments fell"
#: is one finding presented five times, and it crowds out the regional and
#: operational findings a reader needs to see beside it. The full per-cut
#: detail is still available by investigating that dimension directly.
MAX_PER_DIMENSION = 2

#: A movement smaller than this is not investigated. Running a full causal
#: sweep on a 0.4% wobble produces confident explanations for noise, which is
#: the fastest way to teach a team that the tool is wrong.
MIN_INVESTIGABLE_CHANGE = 0.02

#: How the unpivoted slice mart's cuts map onto reported dimensions.
SLICE_DIMENSIONS: dict[str, Dimension] = {
    "region": Dimension.REGION,
    "store": Dimension.STORE,
    "category": Dimension.PRODUCT,
    "department": Dimension.PRODUCT,
    "segment": Dimension.SEGMENT,
}

#: Rows a single governed query may return before the picture is partial.
SLICE_QUERY_LIMIT = 1000


@dataclass(frozen=True, slots=True)
class _Period:
    """One window's worth of pre-aggregated numbers."""

    slices: dict[str, dict[str, dict[str, float]]]
    """slice_type → slice_value → metric → value."""

    factors: dict[str, dict[str, float]]
    """region → factor → value."""

    network: dict[str, float]


class RootCauseService:
    """Investigates a KPI movement across all nine dimensions."""

    def __init__(self, analytics: AnalyticsService) -> None:
        self._analytics = analytics

    async def investigate(
        self,
        principal: Principal,
        *,
        metric: str = "net_revenue",
        current_start: date | None = None,
        current_end: date | None = None,
        baseline_start: date | None = None,
        baseline_end: date | None = None,
        dimensions: tuple[Dimension, ...] | None = None,
    ) -> Investigation:
        """Run the full sweep and rank what comes back."""
        authz.require(principal, Permission.RCA_RUN)

        current, baseline = self._windows(current_start, current_end, baseline_start, baseline_end)
        requested = dimensions or tuple(Dimension)

        current_data = await self._load(principal, current)
        baseline_data = await self._load(principal, baseline)

        current_value = current_data.network.get(metric, 0.0)
        baseline_value = baseline_data.network.get(metric, 0.0)

        # Windows of different length are compared per-day. Comparing a week
        # against a month otherwise reports a 75% collapse that is entirely an
        # artefact of the question.
        scale = current.days / baseline.days if baseline.days else 1.0
        baseline_value_scaled = baseline_value * scale

        caveats = list(self._caveats(current, baseline, current_data, baseline_data, scale))
        change = current_value - baseline_value_scaled
        relative = change / abs(baseline_value_scaled) if baseline_value_scaled else 0.0

        findings: list[Finding] = []
        unavailable: dict[str, str] = {}

        if abs(relative) < MIN_INVESTIGABLE_CHANGE:
            caveats.insert(
                0,
                f"The metric moved {relative:+.1%}, inside the "
                f"{MIN_INVESTIGABLE_CHANGE:.0%} floor for investigation. No "
                "causes are proposed: attributing a movement this size would "
                "produce confident explanations for ordinary variation.",
            )
        else:
            findings, unavailable = await self._run_investigators(
                principal,
                requested=requested,
                metric=metric,
                current=current_data,
                baseline=baseline_data,
                scale=scale,
                current_value=current_value,
                baseline_value=baseline_value_scaled,
                current_window=current,
            )

        findings.sort(key=lambda finding: finding.rank_score, reverse=True)
        findings = _diversify(findings)[:MAX_FINDINGS]

        investigation = Investigation(
            metric=metric,
            current=current,
            baseline=baseline,
            current_value=current_value,
            baseline_value=baseline_value_scaled,
            findings=tuple(findings),
            dimensions_investigated=requested,
            dimensions_unavailable=unavailable,
            caveats=tuple(caveats),
            meta={"baseline_scaled_by": round(scale, 4)},
        )

        log.info(
            "rca.investigation",
            metric=metric,
            change=round(change, 2),
            relative_change=round(relative, 4),
            findings=len(findings),
            explained_share=round(investigation.explained_share, 4),
        )
        return investigation

    # ── Windows ──────────────────────────────────────────────────────

    def _windows(
        self,
        current_start: date | None,
        current_end: date | None,
        baseline_start: date | None,
        baseline_end: date | None,
    ) -> tuple[Window, Window]:
        end = current_end or date.today()
        start = current_start or end - timedelta(days=DEFAULT_CURRENT_DAYS - 1)
        if start > end:
            raise ValidationDomainError("current_start must not be after current_end")

        days = (end - start).days + 1
        b_end = baseline_end or start - timedelta(days=1)
        b_start = baseline_start or b_end - timedelta(days=days * DEFAULT_BASELINE_MULTIPLE - 1)
        if b_start > b_end:
            raise ValidationDomainError("baseline_start must not be after baseline_end")

        # Overlap would put the same days on both sides of the comparison,
        # damping any real movement toward zero without any error surfacing.
        if b_end >= start:
            raise ValidationDomainError(
                "baseline window overlaps the current window",
                hint=(
                    "Shared days appear on both sides of the comparison and "
                    "mute the very change being investigated."
                ),
            )

        return Window(start, end), Window(b_start, b_end)

    # ── Loading ──────────────────────────────────────────────────────

    async def _load(self, principal: Principal, window: Window) -> _Period:
        """One governed query per relation, aggregated over the window."""
        slice_answer = await self._analytics.query(
            principal,
            domain_key="rca_slice",
            metrics=["net_revenue", "orders", "units_sold", "return_amount", "return_units"],
            dimensions=["slice_type", "slice_value"],
            start_date=window.start,
            end_date=window.end,
            limit=SLICE_QUERY_LIMIT,
        )
        factor_answer = await self._analytics.query(
            principal,
            domain_key="rca_factor",
            metrics=[
                "stockout_rate",
                "stockout_positions",
                "sku_store_positions",
                "on_time_rate",
                "shipments_closed",
                "avg_days_late",
                "carriers_missing_promise",
                "severe_days",
                "max_precipitation_z",
                "max_wind_kph",
                "promo_revenue",
                "active_promotions",
                "avg_promo_depth",
            ],
            dimensions=["region"],
            start_date=window.start,
            end_date=window.end,
            limit=SLICE_QUERY_LIMIT,
        )

        slices: dict[str, dict[str, dict[str, float]]] = {}
        network: dict[str, float] = {}
        for row in slice_answer.result.rows:
            slice_type = str(row.get("slice_type"))
            slice_value = str(row.get("slice_value"))
            values = {
                key: float(row.get(key) or 0.0)
                for key in ("net_revenue", "orders", "units_sold", "return_amount", "return_units")
            }
            values["return_rate"] = values["return_amount"] / (
                values["net_revenue"] + values["return_amount"] or 1.0
            )
            if slice_type == "network":
                network = values
            else:
                slices.setdefault(slice_type, {})[slice_value] = values

        factors = {
            str(row.get("region")): {
                key: float(value)
                for key, value in row.items()
                if key != "region" and isinstance(value, int | float)
            }
            for row in factor_answer.result.rows
        }

        return _Period(slices=slices, factors=factors, network=network)

    # ── Investigators ────────────────────────────────────────────────

    async def _run_investigators(
        self,
        principal: Principal,
        *,
        requested: tuple[Dimension, ...],
        metric: str,
        current: _Period,
        baseline: _Period,
        scale: float,
        current_value: float,
        baseline_value: float,
        current_window: Window,
    ) -> tuple[list[Finding], dict[str, str]]:
        findings: list[Finding] = []
        unavailable: dict[str, str] = {}
        total_change = current_value - baseline_value

        # ── Dimensional cuts ──
        for slice_type, dimension in SLICE_DIMENSIONS.items():
            if dimension not in requested:
                continue
            current_slices = current.slices.get(slice_type, {})
            if not current_slices:
                unavailable[dimension.value] = f"no '{slice_type}' rows in the current window"
                continue
            findings.extend(
                investigators.investigate_dimension(
                    dimension,
                    current=current_slices,
                    baseline=_scaled(baseline.slices.get(slice_type, {}), scale),
                    network_current=current_value,
                    network_baseline=baseline_value,
                    metric=metric,
                )
            )

        # ── Factors, per region ──
        worst_region = self._worst_region(current, baseline, scale, metric)

        if Dimension.INVENTORY in requested:
            for region, factors in current.factors.items():
                findings.extend(
                    investigators.investigate_inventory(
                        current=factors,
                        baseline=baseline.factors.get(region, {}),
                        region=region,
                        revenue_at_stake=current.slices.get("region", {})
                        .get(region, {})
                        .get(metric, 0.0),
                        total_change=total_change,
                    )
                )

        if Dimension.RETURNS in requested:
            findings.extend(
                investigators.investigate_returns(
                    current=current.network,
                    baseline=_scale_values(baseline.network, scale),
                    subject="Network",
                    total_change=total_change,
                )
            )

        if Dimension.SHIPPING in requested:
            for region, factors in current.factors.items():
                findings.extend(
                    investigators.investigate_shipping(
                        current=factors,
                        baseline=baseline.factors.get(region, {}),
                        subject=region,
                        total_change=total_change,
                        revenue_at_stake=current.slices.get("region", {})
                        .get(region, {})
                        .get(metric, 0.0),
                    )
                )

        if Dimension.PROMOTION in requested:
            # Promotions are national, so any region's row carries the same
            # figures. Reading one rather than aggregating avoids multiplying
            # the national number by the number of regions.
            sample = next(iter(current.factors.values()), {})
            baseline_sample = next(iter(baseline.factors.values()), {})
            findings.extend(
                investigators.investigate_promotion(
                    current=sample,
                    baseline=_scale_values(baseline_sample, scale, keys=("promo_revenue",)),
                    total_change=total_change,
                )
            )

        if Dimension.WEATHER in requested:
            weather_findings, weather_note = await self._weather(
                principal,
                current=current,
                baseline=baseline,
                total_change=total_change,
                window=current_window,
                worst_region=worst_region,
            )
            findings.extend(weather_findings)
            if weather_note:
                unavailable[Dimension.WEATHER.value] = weather_note

        return findings, unavailable

    async def _weather(
        self,
        principal: Principal,
        *,
        current: _Period,
        baseline: _Period,
        total_change: float,
        window: Window,
        worst_region: str | None,
    ) -> tuple[list[Finding], str]:
        """Weather findings, sized from the observed severe-day gap."""
        answer = await self._analytics.query(
            principal,
            domain_key="rca_weather",
            metrics=["severe_day_gap", "severe_day_gap_pct", "severe_days", "ordinary_days"],
            dimensions=["region"],
            limit=100,
        )
        effects = {str(row.get("region")): row for row in answer.result.rows}

        findings: list[Finding] = []
        for region, factors in current.factors.items():
            severe_days = int(factors.get("severe_days") or 0)
            if not severe_days:
                continue

            effect = effects.get(region)
            gap = float(effect.get("severe_day_gap") or 0.0) if effect else 0.0
            if not gap:
                continue

            findings.extend(
                investigators.investigate_weather(
                    severe_days=severe_days,
                    window_days=window.days,
                    baseline_severe_days=int(
                        baseline.factors.get(region, {}).get("severe_days") or 0
                    ),
                    observed_daily_gap=gap,
                    region=region,
                    total_change=total_change,
                    detail={
                        "precipitation_z": factors.get("max_precipitation_z") or 0.0,
                        "wind_kph_max": factors.get("max_wind_kph") or 0.0,
                    },
                )
            )

        if not findings and worst_region:
            return [], (
                f"no severe weather recorded in the current window; "
                f"{worst_region}'s shortfall is not explained by weather"
            )
        return findings, ""

    def _worst_region(
        self, current: _Period, baseline: _Period, scale: float, metric: str
    ) -> str | None:
        regions = current.slices.get("region", {})
        if not regions:
            return None
        base = baseline.slices.get("region", {})
        return min(
            regions,
            key=lambda region: (
                regions[region].get(metric, 0.0) - base.get(region, {}).get(metric, 0.0) * scale
            ),
        )

    # ── Caveats ──────────────────────────────────────────────────────

    def _caveats(
        self,
        current: Window,
        baseline: Window,
        current_data: _Period,
        baseline_data: _Period,
        scale: float,
    ) -> list[str]:
        caveats = [
            "Every finding here is observational. This platform runs no "
            "experiments, so nothing below establishes causation — findings "
            "are graded by whether a mechanism exists, and the grade caps the "
            "confidence.",
            "Contributions state where a change landed, not where it "
            "originated. A region can be the source of a problem or the place "
            "an upstream one surfaced, and the arithmetic cannot separate them.",
        ]

        if abs(scale - 1.0) > 1e-9:
            caveats.append(
                f"Windows differ in length ({current.days} vs {baseline.days} "
                f"days); the baseline is scaled by {scale:.3f} so the "
                "comparison is per-day. Day-of-week mix is not corrected for."
            )

        if "segment" in current_data.slices:
            caveats.append(
                "Customer segments are assigned as of today and applied to "
                "historical purchases. A customer who is Loyal now was counted "
                "as Loyal when they were still New, so the segment cut "
                "measures today's population against its own past rather than "
                "a like-for-like comparison. Treat segment findings as "
                "directional."
            )

        if not current_data.factors:
            caveats.append(
                "No operational factors were available for the current window, "
                "so only dimensional decomposition ran."
            )

        return caveats


def _diversify(findings: list[Finding]) -> list[Finding]:
    """Cap findings per dimension while preserving rank order.

    Applied after ranking, so the strongest finding from each cut always
    survives. A briefing dominated by one dimension is not more informative
    for it — it is the same observation restated.
    """
    seen: dict[Dimension, int] = {}
    kept: list[Finding] = []
    for finding in findings:
        count = seen.get(finding.dimension, 0)
        if count >= MAX_PER_DIMENSION:
            continue
        seen[finding.dimension] = count + 1
        kept.append(finding)
    return kept


def _scaled(slices: dict[str, dict[str, float]], scale: float) -> dict[str, dict[str, float]]:
    return {label: _scale_values(values, scale) for label, values in slices.items()}


def _scale_values(
    values: dict[str, float], scale: float, keys: tuple[str, ...] | None = None
) -> dict[str, float]:
    """Scale additive measures to the current window's length.

    Rates are left alone: a stockout rate or an on-time rate is already
    normalised, and multiplying it by a window ratio would produce a
    percentage above one and a finding built on nonsense.
    """
    additive = keys or (
        "net_revenue",
        "orders",
        "units_sold",
        "return_amount",
        "return_units",
        "promo_revenue",
        "stockout_positions",
        "sku_store_positions",
        "shipments_closed",
        "severe_days",
    )
    return {key: (value * scale if key in additive else value) for key, value in values.items()}


def summarise(investigation: Investigation) -> dict[str, Any]:
    """Response payload, with the arithmetic and the explanations kept apart."""
    arithmetic = [f for f in investigation.findings if f.tier is EvidenceTier.ARITHMETIC]
    explanatory = [f for f in investigation.findings if f.tier is not EvidenceTier.ARITHMETIC]

    return {
        "metric": investigation.metric,
        "current": investigation.current.as_dict(),
        "baseline": investigation.baseline.as_dict(),
        "current_value": round(investigation.current_value, 2),
        "baseline_value": round(investigation.baseline_value, 2),
        "change": round(investigation.change, 2),
        "relative_change": (
            round(investigation.relative_change, 4)
            if investigation.relative_change is not None
            else None
        ),
        "explained_share": round(investigation.explained_share, 4),
        "findings": [finding.as_dict() for finding in investigation.findings],
        "where": [finding.as_dict() for finding in arithmetic],
        "why": [finding.as_dict() for finding in explanatory],
        "dimensions_investigated": [d.value for d in investigation.dimensions_investigated],
        "dimensions_unavailable": investigation.dimensions_unavailable,
        "caveats": list(investigation.caveats),
        "meta": investigation.meta,
    }
