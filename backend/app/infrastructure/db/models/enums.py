"""Enumerations stored as CHECK-constrained text (DB design §11).

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
    """Alert detectors (ARCH §29 ensemble)."""

    ZSCORE = "zscore"
    STL_RESID = "stl_resid"
    IFOREST = "iforest"
    THRESHOLD = "threshold"


class Severity(StrEnum):
    INFO = "info"
    WARN = "warn"
    CRITICAL = "critical"


class AlertStatus(StrEnum):
    OPEN = "open"
    ACKED = "acked"
    RESOLVED = "resolved"


class NlqOutcome(StrEnum):
    """Per-turn funnel outcome — a monitored product metric (PRD §24)."""

    ANSWERED = "answered"
    CLARIFIED = "clarified"
    REFUSED = "refused"
    ERROR = "error"


class RecommendationType(StrEnum):
    REORDER = "reorder"
    MARKDOWN = "markdown"
    PROMO = "promo"
    ASSORTMENT = "assortment"


class RecommendationStatus(StrEnum):
    PROPOSED = "proposed"
    ACCEPTED = "accepted"
    DISMISSED = "dismissed"
    EXPIRED = "expired"


class DismissReason(StrEnum):
    """Enumerated dismissal reasons — the learning signal (DB §40)."""

    SUPPLIER_CONSTRAINT = "supplier_constraint"
    ALREADY_PLANNED = "already_planned"
    DISAGREE_FORECAST = "disagree_forecast"
    OTHER = "other"


class Confidence(StrEnum):
    """Deterministic-rubric confidence bands (AI design §0.1)."""

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
    """Feed card taxonomy (DB §39 — the feed is the system of record)."""

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
