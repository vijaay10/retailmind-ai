"""Quality-rule catalog.

Every check the gate can apply, with a stable id, a severity, and the reason
it exists. Ids appear in reject files, quarantine alerts, DQ scores, and the
in-app data-trust screens — they are a contract, so rename nothing.

Severity is the whole design:

``BLOCKING``
    Corruption class. The batch does not reach the warehouse; it quarantines
    and waits for a human. Reserved for things that make the *whole batch*
    untrustworthy — a broken schema, a collapsed row count, stale data.

``WARNING``
    Something to look at, not something to stop for. Content oddities, drift
    signals, distribution shifts. They lower the quality score and surface in
    the digest.

The line between them is the design's central distinction: *row problems are
data, batch problems are incidents* (ETL).
"""

from dataclasses import dataclass
from enum import StrEnum


class Severity(StrEnum):
    BLOCKING = "blocking"
    WARNING = "warning"


@dataclass(frozen=True, slots=True)
class QualityRule:
    id: str
    layer: str
    description: str
    severity: Severity
    rationale: str


@dataclass(frozen=True, slots=True)
class RuleResult:
    """Outcome of one rule against one batch."""

    rule: QualityRule
    passed: bool
    observed: dict[str, object]

    @property
    def blocking_failure(self) -> bool:
        return not self.passed and self.rule.severity is Severity.BLOCKING


# ── The catalog ──────────────────────────────────────────────────────

SCHEMA_FINGERPRINT = QualityRule(
    id="QR-SCH-001",
    layer="boundary",
    description="Arriving columns match the declared schema",
    severity=Severity.BLOCKING,
    rationale="A source that dropped a column has broken its contract; "
    "guessing which column moved is how silent corruption starts.",
)

VOLUME_BAND = QualityRule(
    id="QR-VOL-002",
    layer="boundary",
    description="Row count falls inside the trailing volume band",
    severity=Severity.BLOCKING,
    rationale="A collapsed row count is the most common symptom of a truncated "
    "extract, and the one users notice last.",
)

FRESHNESS = QualityRule(
    id="QR-FRS-003",
    layer="boundary",
    description="Partition covers the requested business date",
    severity=Severity.BLOCKING,
    rationale="Loading yesterday's file as today's data is undetectable downstream.",
)

FILE_COMPLETENESS = QualityRule(
    id="QR-CMP-004",
    layer="boundary",
    description="Expected share of source files arrived",
    severity=Severity.BLOCKING,
    rationale="Partial store coverage looks exactly like a sales decline.",
)

BUSINESS_KEY_PRESENT = QualityRule(
    id="QR-KEY-010",
    layer="boundary",
    description="No surviving row is missing a business key",
    severity=Severity.BLOCKING,
    rationale="Unjoinable rows corrupt every aggregate they reach.",
)

REJECT_RATE = QualityRule(
    id="QR-REJ-011",
    layer="boundary",
    description="Row-level reject rate stays below threshold",
    severity=Severity.BLOCKING,
    rationale="A few bad rows are data; a flood of them means the batch itself "
    "is suspect and should not be published piecemeal.",
)

ENUM_DOMAIN = QualityRule(
    id="QR-ENM-012",
    layer="boundary",
    description="Categorical values map to the declared domain",
    severity=Severity.WARNING,
    rationale="New codes appear legitimately; they route to UNKNOWN and get "
    "reviewed rather than failing the night's load.",
)

DUPLICATE_RATE = QualityRule(
    id="QR-DUP-020",
    layer="conform",
    description="Natural-key duplicate rate stays below threshold",
    severity=Severity.WARNING,
    rationale="Dedup handles duplicates structurally; a rising rate is an "
    "upstream bug signal worth watching.",
)

FX_STALENESS = QualityRule(
    id="QR-FX-041",
    layer="conform",
    description="No FX rate carried forward beyond the tolerance",
    severity=Severity.BLOCKING,
    rationale="Converting money at a stale rate is a wrong number that looks right.",
)

RECONCILIATION = QualityRule(
    id="QR-BAL-030",
    layer="load",
    description="Row counts and measure totals reconcile across layers",
    severity=Severity.BLOCKING,
    rationale="Conservation-of-money checks catch join fanout, dedup overreach, "
    "and window slips — whole classes of bug that per-row checks structurally cannot.",
)

NULL_RATE_DRIFT = QualityRule(
    id="QR-DRF-040",
    layer="boundary",
    description="Null rates stay near their trailing norm",
    severity=Severity.WARNING,
    rationale="A column that quietly goes empty is a schema change nobody announced.",
)

CATALOG: dict[str, QualityRule] = {
    rule.id: rule
    for rule in (
        SCHEMA_FINGERPRINT,
        VOLUME_BAND,
        FRESHNESS,
        FILE_COMPLETENESS,
        BUSINESS_KEY_PRESENT,
        REJECT_RATE,
        ENUM_DOMAIN,
        DUPLICATE_RATE,
        FX_STALENESS,
        RECONCILIATION,
        NULL_RATE_DRIFT,
    )
}
