"""Enumerations stored as CHECK-constrained text (DB design).

Native Postgres enums are deliberately avoided: adding a value to a text CHECK
is a plain migration, not type surgery. Every enum here pairs with a
``CheckConstraint`` built by :func:`app.infrastructure.db.models.base.enum_check`.
"""

from enum import StrEnum


class UserStatus(StrEnum):
    ACTIVE = "active"
    INVITED = "invited"
    DISABLED = "disabled"


class Sensitivity(StrEnum):
    LOW = "low"
    MED = "med"
    HIGH = "high"


class Detector(StrEnum):
    """Alert detectors (ARCH ensemble)."""

    ZSCORE = "zscore"
    STL_RESID = "stl_resid"
    IFOREST = "iforest"
    THRESHOLD = "threshold"


class Severity(StrEnum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class AlertStatus(StrEnum):
    OPEN = "open"
    ACKED = "acked"
    RESOLVED = "resolved"


class NlqOutcome(StrEnum):
    """Per-turn funnel outcome — a monitored product metric (PRD)."""

    ANSWERED = "answered"
    CLARIFIED = "clarified"
    REFUSED = "refused"
    ERROR = "error"


class RecommendationType(StrEnum):
    REORDER = "reorder"
    MARKDOWN = "markdown"
    PROMO = "promo"
    ASSORTMENT = "assortment"


class RecommendationCategory(StrEnum):
    """The seven business categories the recommendation engine proposes
    across (`app.services.recommendations.contracts.Category`, mirrored here
    rather than imported — `infrastructure` does not depend on `services`).

    Distinct from `RecommendationType`, which is the narrower *kind of
    action* a batch-engine-written `recommendation` row represents (reorder,
    markdown, promo, assortment). `category` is what the calibration API's
    "generator" filtering actually means and always meant to filter on —
    `OutcomeRepository.find_measured()` filtered on `type` instead, which
    can never match a category value and made generator-filtering fail for
    every real caller (Prompt 11 finding, fixed in Prompt 11.5).
    """

    INVENTORY = "inventory"
    PRICING = "pricing"
    PROMOTION = "promotion"
    STORE = "store"
    MARKETING = "marketing"
    CUSTOMER = "customer"
    SUPPLIER = "supplier"


class RecommendationStatus(StrEnum):
    PROPOSED = "proposed"
    ACCEPTED = "accepted"
    DISMISSED = "dismissed"
    EXPIRED = "expired"


class DismissReason(StrEnum):
    """Enumerated dismissal reasons — the learning signal (DB)."""

    SUPPLIER_CONSTRAINT = "supplier_constraint"
    ALREADY_PLANNED = "already_planned"
    DISAGREE_FORECAST = "disagree_forecast"
    OTHER = "other"


class Confidence(StrEnum):
    """Deterministic-rubric confidence bands (AI design.1)."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class RunStatus(StrEnum):
    """Shared lifecycle for pipeline/report/job runs."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    QUARANTINED = "quarantined"  # pipeline_run only


class NotificationChannel(StrEnum):
    IN_APP = "in_app"
    EMAIL = "email"
    SLACK = "slack"


class DeliveryStatus(StrEnum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"


class InsightKind(StrEnum):
    """Feed card taxonomy (DB — the feed is the system of record)."""

    ALERT = "alert"
    RCA = "rca"
    RECOMMENDATION = "recommendation"
    REPORT = "report"
    DQ = "dq"


class ActorType(StrEnum):
    USER = "user"
    SERVICE = "service"
    SYSTEM = "system"


class DecisionAction(StrEnum):
    """What a human did with a proposed action.

    Deliberately two values. "Snooze" reads as a decision and is not one: it
    records that nobody wanted to think about it yet, and a queue that fills
    with snoozed items is a queue nobody trusts.
    """

    ACCEPTED = "accepted"
    DISMISSED = "dismissed"


class OutcomeStatus(StrEnum):
    """Lifecycle status for recommendation outcome measurement.

    PENDING: decision made, measurement window not yet matured
    MEASURING: job is actively measuring this outcome
    MEASURED: measurement completed successfully
    FAILED: measurement attempted but failed (e.g., query error)
    INSUFFICIENT_DATA: not enough data to measure outcome
    """

    PENDING = "pending"
    MEASURING = "measuring"
    MEASURED = "measured"
    FAILED = "failed"
    INSUFFICIENT_DATA = "insufficient_data"


class BaselineMethod(StrEnum):
    """How the counterfactual baseline was calculated.

    COMPARABLE_PERIOD: same period last year/month/week
    PRE_DECISION: period immediately before decision
    PEER_BASELINE: peer stores/SKUs without the intervention
    FORECAST_BASELINE: what the forecast predicted
    """

    COMPARABLE_PERIOD = "comparable_period"
    PRE_DECISION = "pre_decision"
    PEER_BASELINE = "peer_baseline"
    FORECAST_BASELINE = "forecast_baseline"
