"""Alert candidates, and the identity that makes them suppressible.

The hard problem in alerting is not detection. It is **not sending the same
thing twice**. A detector that runs hourly and re-fires an unchanged condition
produces twenty-four identical emails a day, and the recipient's response is
not to fix the condition — it is to build a mail rule. After that the system
is worse than nothing, because the one alert that mattered is filed away with
the rest.

So every candidate carries a **fingerprint**: a stable identity derived from
what the alert is about, not from when it was detected. Two runs over an
unchanged world produce identical fingerprints, and the suppression layer can
tell "still true" from "true again".

Severity is derived from the condition rather than configured per detector.
A detector that decides its own severity drifts toward critical, because
whoever wrote it believes their signal is the important one.
"""

import hashlib
import math
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from typing import Any


class AlertKind(StrEnum):
    """The six conditions this platform watches for."""

    LOW_INVENTORY = "low_inventory"
    SALES_DROP = "sales_drop"
    FORECAST_RISK = "forecast_risk"
    FRAUD_RISK = "fraud_risk"
    INVENTORY_RISK = "inventory_risk"
    RECOMMENDATION_READY = "recommendation_ready"


class Severity(StrEnum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"
    # Backwards compatibility
    WARN = "medium"  # Map WARN to MEDIUM for migration


#: Event types written to the notification ledger, matching the schema's
#: documented convention (``alert.critical | report.ready | rec.proposed``).
def event_type_for(kind: AlertKind, severity: Severity) -> str:
    if kind is AlertKind.RECOMMENDATION_READY:
        return "rec.proposed"
    return f"alert.{severity.value}"


@dataclass(frozen=True, slots=True)
class AlertCandidate:
    """One detected condition, before deduplication.

    "Candidate" rather than "alert" on purpose: detection produces things that
    *might* be worth telling someone, and whether they are depends on what has
    already been sent. That decision belongs to the suppression layer, not to
    a detector that can only see its own signal.
    """

    kind: AlertKind
    subject: str
    """What the alert is about — a SKU, a store, a supplier, a region."""

    title: str
    body: str
    severity: Severity
    observed: float
    expected_low: float | None = 0.0
    expected_high: float | None = 0.0
    """``None`` means unbounded on that side. JSON cannot represent infinity,
    and a bound that only exists in Python is a bound that dies at the
    database boundary — see :func:`_json_number`."""
    detected_for: date | None = None
    evidence: dict[str, Any] = field(default_factory=dict)
    deep_link: str = ""
    """Where a reader goes to act on it. An alert with nowhere to go is a
    notification the recipient has to translate into an action themselves,
    and most of them will not."""

    @property
    def fingerprint(self) -> str:
        """Stable identity for this condition.

        Deliberately excludes the observed value, the timestamp, and the
        narrative. A stockout that deepens from 3 units to 2 is the *same*
        stockout, and including the number would make every re-detection look
        novel — which is exactly how an alerting system starts shouting.

        Severity is included, so an escalation from warn to critical is a new
        fingerprint and does re-notify. That is the one change worth
        interrupting someone for.
        """
        material = f"{self.kind.value}|{self.subject}|{self.severity.value}"
        return hashlib.sha256(material.encode()).hexdigest()[:32]

    def as_payload(self) -> dict[str, Any]:
        """The notification payload — template variables plus the deep link."""
        return {
            "kind": self.kind.value,
            "subject": self.subject,
            "title": self.title,
            "body": self.body,
            "severity": self.severity.value,
            "observed": _json_number(self.observed),
            "expected_low": _json_number(self.expected_low),
            "expected_high": _json_number(self.expected_high),
            "detected_for": self.detected_for.isoformat() if self.detected_for else None,
            "evidence": {key: _json_safe(value) for key, value in self.evidence.items()},
            "deep_link": self.deep_link,
            "fingerprint": self.fingerprint,
        }


def _json_number(value: float | None) -> float | None:
    """Round for display, and turn what JSON cannot hold into null.

    JSON has no infinity and no NaN. `json.dumps` emits the JavaScript
    spellings — `Infinity`, `NaN` — and Postgres rejects those outright when
    the payload lands in a `jsonb` column. The blast radius is the reason this
    is a function rather than a comment: delivery happens inside the sweep, so
    one unbounded ratio in one payload fails the insert and takes every other
    alert in the run down with it.

    Null is also the honest encoding. An infinite upper bound meant "no upper
    bound", and a NaN meant the value could not be computed.
    """
    if value is None or not math.isfinite(value):
        return None
    return round(value, 4)


def _json_safe(value: Any) -> Any:
    """Same guarantee, for the free-form evidence dictionary."""
    if isinstance(value, float):
        return _json_number(value)
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_json_safe(item) for item in value]
    return value


@dataclass(frozen=True, slots=True)
class Delivery:
    """One notification queued for one user on one channel."""

    user_id: str
    channel: str
    event_type: str
    candidate: AlertCandidate


@dataclass(frozen=True, slots=True)
class SweepResult:
    """What one detection run did, and what it deliberately did not do."""

    detected: tuple[AlertCandidate, ...] = ()
    notified: tuple[AlertCandidate, ...] = ()
    suppressed: tuple[tuple[AlertCandidate, str], ...] = ()
    """Candidates withheld, each with the reason. Counted and reported rather
    than discarded silently: an operator asking "why didn't I hear about
    this?" needs an answer, and "it was suppressed as a duplicate for six
    more hours" is one."""

    deliveries: int = 0
    detectors_failed: dict[str, str] = field(default_factory=dict)
    started_at: datetime | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "detected": len(self.detected),
            "notified": len(self.notified),
            "suppressed": len(self.suppressed),
            "deliveries": self.deliveries,
            "suppression_reasons": _counted(reason for _, reason in self.suppressed),
            "by_kind": _counted(item.kind.value for item in self.notified),
            "detectors_failed": self.detectors_failed,
        }


def _counted(values: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return counts
