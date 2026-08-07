"""The decision-loop ledger: recommendations, feedback, outcomes (DB §40).

Evidence is pointers (query ids + snapshot), never embedded copies — one source
of truth. The dedup rule ("no simultaneous markdown + reorder per subject") is
a partial unique index, not application discipline.
"""

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Index, Numeric, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.db.models.base import (
    Base,
    JSONDict,
    TenantScopedMixin,
    enum_check,
    uuid_pk,
)
from app.infrastructure.db.models.enums import (
    Confidence,
    DecisionAction,
    DismissReason,
    RecommendationStatus,
    RecommendationType,
)


class Recommendation(Base, TenantScopedMixin):
    __tablename__ = "recommendation"
    __table_args__ = (
        # Inbox hot path (DB §13 ix_rec_inbox)
        Index("ix_rec_inbox", "tenant_id", "status", "type", "expires_at"),
        # One active rec per subject — structural dedup (DB §40)
        Index(
            "uq_rec_active_dedup",
            "tenant_id",
            "dedup_key",
            unique=True,
            postgresql_where=text("status = 'proposed'"),
        ),
        enum_check("type", RecommendationType),
        enum_check("status", RecommendationStatus),
        enum_check("confidence", Confidence),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    type: Mapped[str]
    subject: Mapped[JSONDict] = mapped_column(
        comment="Typed per rec-type, e.g. reorder: {sku, store_id, suggested_qty, order_by_date}"
    )
    dedup_key: Mapped[str] = mapped_column(comment="Deterministic digest of the subject")
    expected_impact: Mapped[JSONDict] = mapped_column(
        comment="{metric, value_usd, method, confidence} — method is mandatory (honesty rule)"
    )
    rationale: Mapped[str | None] = mapped_column(comment="Grounded LLM narration (AI §2)")
    rule_id: Mapped[str] = mapped_column(comment="Which eligibility rule fired (auditable)")
    rule_version: Mapped[str]
    model_run_id: Mapped[str | None] = mapped_column(
        comment="Forecast/elasticity run that fed quantities (provenance)"
    )
    score: Mapped[float] = mapped_column(
        Numeric(12, 2), comment="impact × confidence × urgency decay (ARCH §26 ranking)"
    )
    status: Mapped[str] = mapped_column(server_default=text("'proposed'"))
    confidence: Mapped[str]
    evidence: Mapped[JSONDict] = mapped_column(
        comment="Pointers: query ids + snapshot, never copies"
    )
    expires_at: Mapped[datetime]
    data_snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("data_snapshot.id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"))

    feedback: Mapped[list["RecommendationFeedback"]] = relationship(
        back_populates="recommendation", cascade="all, delete-orphan"
    )
    outcomes: Mapped[list["RecommendationOutcome"]] = relationship(
        back_populates="recommendation", cascade="all, delete-orphan"
    )


class RecommendationFeedback(Base):
    """Accept/dismiss events. Dismissals carry an enum reason — the learning signal."""

    __tablename__ = "recommendation_feedback"
    __table_args__ = (enum_check("reason_code", DismissReason),)

    id: Mapped[uuid.UUID] = uuid_pk()
    recommendation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("recommendation.id", ondelete="CASCADE"), index=True
    )
    actor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("app_user.id", ondelete="RESTRICT"))
    action: Mapped[str] = mapped_column(comment="accepted | dismissed")
    reason_code: Mapped[str | None] = mapped_column(comment="Required for dismissals (UI-enforced)")
    note: Mapped[str | None]
    at: Mapped[datetime] = mapped_column(server_default=text("now()"))

    recommendation: Mapped[Recommendation] = relationship(back_populates="feedback")


class RecommendationOutcome(Base):
    """Post-hoc measurement — ships at v1, populated by the Horizon-1 job (DB §40).

    The substrate for 'did accepted reorders actually prevent stockouts' and the
    calibration loop (AI §10): expected vs. realized, by rec type.
    """

    __tablename__ = "recommendation_outcome"

    id: Mapped[uuid.UUID] = uuid_pk()
    recommendation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("recommendation.id", ondelete="CASCADE"), index=True
    )
    measured_at: Mapped[datetime]
    window_days: Mapped[int]
    outcome_metrics: Mapped[JSONDict] = mapped_column(
        comment="e.g. {stockout_occurred: bool, realized_units, realized_margin_delta}"
    )
    method: Mapped[str] = mapped_column(
        comment="How it was measured — outcomes are explainable too"
    )
    vs_expected_ratio: Mapped[float | None] = mapped_column(Numeric(8, 4))

    recommendation: Mapped[Recommendation] = relationship(back_populates="outcomes")


class RecommendationDecision(Base, TenantScopedMixin):
    """What a human decided about a *computed* recommendation.

    Separate from :class:`RecommendationFeedback`, which hangs off a stored
    ``recommendation`` row written by the batch rule engine. The analytical
    engine does not store its proposals: it recomputes them from the warehouse
    on every request, so there is no row to reference and no foreign key to
    hang a decision on. Identity comes from ``decision_key`` — a digest of what
    the action is *about*, not of its wording, so a reorder whose quantity
    moves from 122 to 130 units is still the same decision.

    The action text, the expected profit, and the estimate basis are snapshot
    onto the row rather than referenced. The engine's numbers change daily; a
    ledger that showed today's figure beside yesterday's decision would be
    quietly rewriting what somebody actually approved.
    """

    __tablename__ = "recommendation_decision"
    __table_args__ = (
        # One current decision per subject. Re-deciding overwrites rather than
        # appending, so the inbox cannot show a card as both accepted and
        # dismissed; the audit ledger keeps the history.
        Index(
            "uq_recommendation_decision_key",
            "tenant_id",
            "decision_key",
            unique=True,
        ),
        Index("ix_recommendation_decision_recent", "tenant_id", "decided_at"),
        enum_check("action", DecisionAction),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    decision_key: Mapped[str] = mapped_column(
        comment="Digest of category + subject — stable while the numbers move"
    )
    action: Mapped[str] = mapped_column(comment="accepted | dismissed")
    category: Mapped[str] = mapped_column(comment="Engine category, e.g. inventory | pricing")
    subject: Mapped[str] = mapped_column(comment="What the action is about, e.g. SKU-1@S2016")
    action_text: Mapped[str] = mapped_column(
        comment="The proposal as worded when decided — snapshot, not a pointer"
    )
    expected_profit: Mapped[float | None] = mapped_column(
        Numeric(14, 2), comment="Estimate at decision time, for later calibration"
    )
    estimate_basis: Mapped[str | None] = mapped_column(
        comment="measured | modelled | assumed — what the estimate rested on"
    )
    reason_code: Mapped[str | None] = mapped_column(comment="Enumerated reason, dismissals only")
    note: Mapped[str | None]
    decided_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("app_user.id", ondelete="RESTRICT"))
    decided_at: Mapped[datetime] = mapped_column(server_default=text("now()"))
