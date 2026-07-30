"""The validation gate — where bad batches stop (ETL design §8, §18).

Checks run cheapest-first and short-circuit on the first blocking failure:
there is no value in computing distribution drift for a batch whose schema is
already broken.

The gate returns a verdict rather than raising, so the caller can record every
result to the audit ledger before deciding what to do. Quarantine is a
workflow, not an exception.
"""

import statistics
from dataclasses import dataclass, field
from datetime import date

import structlog

from ingestion.domain.schema import DriftFinding, DriftKind, SourceSchema
from quality.rules import (
    BUSINESS_KEY_PRESENT,
    DUPLICATE_RATE,
    ENUM_DOMAIN,
    FILE_COMPLETENESS,
    FRESHNESS,
    FX_STALENESS,
    REJECT_RATE,
    SCHEMA_FINGERPRINT,
    VOLUME_BAND,
    QualityRule,
    RuleResult,
)

log = structlog.get_logger(__name__)


@dataclass(slots=True)
class BatchStats:
    """What the gate measures a batch against."""

    rows_read: int
    rows_rejected: int
    rows_landed: int
    business_dates: set[date] = field(default_factory=set)
    duplicates_collapsed: int = 0
    fx_missing_rows: int = 0
    unmapped_enum_values: dict[str, list[str]] = field(default_factory=dict)
    files_expected: int = 0
    files_arrived: int = 0
    null_rates: dict[str, float] = field(default_factory=dict)

    @property
    def reject_rate(self) -> float:
        return self.rows_rejected / self.rows_read if self.rows_read else 0.0

    @property
    def duplicate_rate(self) -> float:
        return self.duplicates_collapsed / self.rows_read if self.rows_read else 0.0

    @property
    def file_completeness(self) -> float:
        return self.files_arrived / self.files_expected if self.files_expected else 1.0


@dataclass(slots=True)
class GateVerdict:
    results: list[RuleResult]

    @property
    def passed(self) -> bool:
        return not self.failed_blocking

    @property
    def failed_blocking(self) -> list[RuleResult]:
        return [r for r in self.results if r.blocking_failure]

    @property
    def warnings(self) -> list[RuleResult]:
        return [r for r in self.results if not r.passed and not r.blocking_failure]

    @property
    def failed_rule_ids(self) -> list[str]:
        return [r.rule.id for r in self.failed_blocking]


def volume_band(
    history: list[int], *, tolerance: float, min_history: int = 4
) -> tuple[float, float] | None:
    """Acceptable row-count range from trailing history.

    Uses median and MAD rather than mean and standard deviation: retail volume
    history contains promotions and outages, and a single Black Friday would
    drag a mean-based band wide enough to accept anything (ETL §8).

    Returns None until enough history exists to say anything meaningful —
    refusing to judge is better than judging on two data points.
    """
    if len(history) < min_history:
        return None

    median = statistics.median(history)
    deviations = [abs(value - median) for value in history]
    mad = statistics.median(deviations)

    # MAD collapses to zero for very stable sources; fall back to a
    # proportional band so the check still has a mouth.
    spread = max(mad * 3, median * tolerance)
    return max(0.0, median - spread), median + spread


class QualityGate:
    """Applies the rule catalog to one batch."""

    def __init__(
        self,
        schema: SourceSchema,
        *,
        reject_rate_threshold: float = 0.005,
        duplicate_rate_threshold: float = 0.001,
        completeness_threshold: float = 0.98,
        volume_tolerance: float = 0.35,
    ) -> None:
        self._schema = schema
        self._reject_threshold = reject_rate_threshold
        self._duplicate_threshold = duplicate_rate_threshold
        self._completeness_threshold = completeness_threshold
        self._volume_tolerance = volume_tolerance

    def evaluate(
        self,
        stats: BatchStats,
        *,
        drift: list[DriftFinding],
        expected_dates: set[date],
        volume_history: list[int] | None = None,
    ) -> GateVerdict:
        """Run the catalog in cost order, stopping at the first blocking failure."""
        results: list[RuleResult] = []

        def record(rule: QualityRule, passed: bool, **observed: object) -> bool:
            result = RuleResult(rule=rule, passed=passed, observed=observed)
            results.append(result)
            if result.blocking_failure:
                log.warning(
                    "etl.gate.blocking_failure",
                    rule_id=rule.id,
                    source=self._schema.source,
                    table=self._schema.table,
                    **observed,
                )
            return passed

        # 1 — schema (cheapest, and everything downstream assumes it)
        blocking_drift = [d for d in drift if d.blocking]
        if not record(
            SCHEMA_FINGERPRINT,
            not blocking_drift,
            missing_columns=[
                d.column for d in blocking_drift if d.kind is DriftKind.MISSING_COLUMN
            ],
            new_columns=[d.column for d in drift if d.kind is DriftKind.NEW_COLUMN],
            fingerprint=self._schema.fingerprint,
        ):
            return GateVerdict(results)

        # 2 — file completeness
        if not record(
            FILE_COMPLETENESS,
            stats.file_completeness >= self._completeness_threshold,
            arrived=stats.files_arrived,
            expected=stats.files_expected,
            completeness=round(stats.file_completeness, 4),
        ):
            return GateVerdict(results)

        # 3 — freshness: the data must actually be for the window we asked for
        unexpected = stats.business_dates - expected_dates
        if not record(
            FRESHNESS,
            not unexpected and bool(stats.business_dates or not stats.rows_landed),
            observed_dates=sorted(d.isoformat() for d in stats.business_dates),
            unexpected_dates=sorted(d.isoformat() for d in unexpected),
        ):
            return GateVerdict(results)

        # 4 — volume band
        band = volume_band(volume_history or [], tolerance=self._volume_tolerance)
        if band is not None:
            low, high = band
            if not record(
                VOLUME_BAND,
                low <= stats.rows_read <= high,
                rows=stats.rows_read,
                band_low=round(low),
                band_high=round(high),
            ):
                return GateVerdict(results)
        else:
            record(VOLUME_BAND, True, rows=stats.rows_read, band="insufficient history")

        # 5 — reject rate: the row/batch boundary
        if not record(
            REJECT_RATE,
            stats.reject_rate <= self._reject_threshold,
            reject_rate=round(stats.reject_rate, 6),
            threshold=self._reject_threshold,
            rejected=stats.rows_rejected,
        ):
            return GateVerdict(results)

        # 6 — surviving rows must all be keyed (rejects already removed the rest)
        record(BUSINESS_KEY_PRESENT, True, rows_landed=stats.rows_landed)

        # 7 — FX staleness (money math must not run on expired rates)
        if not record(
            FX_STALENESS,
            stats.fx_missing_rows == 0,
            rows_without_rate=stats.fx_missing_rows,
        ):
            return GateVerdict(results)

        # 8 — warnings: never stop the load, always lower the score
        record(
            DUPLICATE_RATE,
            stats.duplicate_rate <= self._duplicate_threshold,
            duplicate_rate=round(stats.duplicate_rate, 6),
            collapsed=stats.duplicates_collapsed,
        )
        record(
            ENUM_DOMAIN,
            not stats.unmapped_enum_values,
            unmapped=stats.unmapped_enum_values,
        )

        return GateVerdict(results)
