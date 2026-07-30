"""AI insight tables: RCA, NLQ, LLM usage, insight feed (DB §39).

Common contract: every artifact pins ``data_snapshot_id``, carries
``prompt_version``/``model_id`` where an LLM touched it, and versions its JSONB
payloads — the reproducibility join (PRD G7) is a hard schema property here.
"""

import uuid
from datetime import date, datetime

from sqlalchemy import BigInteger, ForeignKey, Index, Numeric, PrimaryKeyConstraint, text
from sqlalchemy.dialects.postgresql import DATERANGE, Range
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.db.models.base import (
    Base,
    JSONDict,
    TenantScopedMixin,
    enum_check,
    uuid_pk,
)
from app.infrastructure.db.models.enums import Confidence, InsightKind, NlqOutcome


class RcaResult(Base, TenantScopedMixin):
    """A completed root-cause investigation (ARCH §27; AI §1)."""

    __tablename__ = "rca_result"
    __table_args__ = (
        Index("ix_rca_result_tenant_created", "tenant_id", text("created_at DESC")),
        enum_check("confidence", Confidence),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    alert_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("alert.id", ondelete="SET NULL"),
        comment="NULL = ad-hoc 'Why?' investigation (DB §21 R4)",
    )
    metric_key: Mapped[str]
    period_a: Mapped[Range[date]] = mapped_column(DATERANGE, comment="Baseline period")
    period_b: Mapped[Range[date]] = mapped_column(DATERANGE, comment="Comparison period")
    scope: Mapped[JSONDict] = mapped_column(
        server_default=text("'{}'::jsonb"), comment="Filters the investigation was scoped to"
    )
    decomposition: Mapped[JSONDict] = mapped_column(
        comment="Contribution tree + volume/rate/mix split; payload schema_version inside"
    )
    top_contributors: Mapped[JSONDict] = mapped_column(comment="Ranked [{segment, delta, share}]")
    driver_correlations: Mapped[JSONDict] = mapped_column(
        server_default=text("'[]'::jsonb"),
        comment="Co-occurring drivers — structurally labeled associational, never causal (AI §1)",
    )
    narrative: Mapped[str | None]
    compiled_queries: Mapped[JSONDict] = mapped_column(comment="Query ids for evidence links")
    confidence: Mapped[str]
    confidence_rubric: Mapped[JSONDict] = mapped_column(
        server_default=text("'{}'::jsonb"), comment="The deterministic signals behind the band"
    )
    prompt_version: Mapped[str | None]
    model_id: Mapped[str | None]
    runtime_ms: Mapped[int]
    rating: Mapped[bool | None] = mapped_column(comment="👍/👎 feedback (US-01 quality loop)")
    rated_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("app_user.id", ondelete="SET NULL")
    )
    data_snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("data_snapshot.id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"))


class NlqSession(Base, TenantScopedMixin):
    __tablename__ = "nlq_session"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("app_user.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"))

    turns: Mapped[list["NlqTurn"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )


class NlqTurn(Base):
    """One NLQ turn — the per-generation audit row NFRs demand (DB §12.2).

    ``result_digest`` stores shape + first-rows hash, never full results (privacy-lean).
    """

    __tablename__ = "nlq_turn"
    __table_args__ = (
        Index("ix_nlq_turn_session", "session_id", "created_at"),
        enum_check("outcome", NlqOutcome),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("nlq_session.id", ondelete="CASCADE"))
    question: Mapped[str]
    intent: Mapped[JSONDict | None] = mapped_column(comment="Validated IntentJSON (AI §6)")
    compiled_sql: Mapped[str | None] = mapped_column(comment="The 'show the query' artifact")
    result_digest: Mapped[JSONDict | None]
    chart_spec: Mapped[JSONDict | None]
    narrative: Mapped[str | None]
    outcome: Mapped[str]
    refusal_reason: Mapped[str | None]
    grounding_validation: Mapped[JSONDict | None] = mapped_column(
        comment="Numeral-check results — the zero-hallucination evidence trail (PRD §24)"
    )
    prompt_version: Mapped[str | None]
    model_id: Mapped[str | None]
    tokens_in: Mapped[int] = mapped_column(server_default=text("0"))
    tokens_out: Mapped[int] = mapped_column(server_default=text("0"))
    latency_ms: Mapped[int]
    feedback: Mapped[bool | None]
    data_snapshot_id: Mapped[str | None] = mapped_column(
        ForeignKey("data_snapshot.id", ondelete="RESTRICT"),
        comment="NULL only for refused/errored turns that never touched data",
    )
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"))

    session: Mapped[NlqSession] = relationship(back_populates="turns")


class LlmUsage(Base):
    """Cost & budget ledger — partitioned monthly by ``at`` (DB §16).

    The Redis counter is the gate; this table is the truth, reconciled nightly
    (Backend §20). PK includes the partition key as Postgres requires.
    """

    __tablename__ = "llm_usage"
    __table_args__ = (
        PrimaryKeyConstraint("id", "at"),
        Index("ix_llm_usage_tenant_at", "tenant_id", "at"),
        {"postgresql_partition_by": "RANGE (at)"},
    )

    id: Mapped[uuid.UUID] = mapped_column(server_default=text("uuid_generate_v7()"))
    tenant_id: Mapped[uuid.UUID] = mapped_column(comment="No FK on partitioned high-churn table")
    module: Mapped[str] = mapped_column(comment="nlq | rca | reports | recs | insights …")
    model_id: Mapped[str]
    prompt_version: Mapped[str | None]
    tokens_in: Mapped[int] = mapped_column(BigInteger)
    tokens_out: Mapped[int] = mapped_column(BigInteger)
    cost_usd: Mapped[float] = mapped_column(Numeric(10, 4))
    at: Mapped[datetime] = mapped_column(server_default=text("now()"))


class Insight(Base, TenantScopedMixin):
    """The unified feed — system of record; channels are projections (PRD §32)."""

    __tablename__ = "insight"
    __table_args__ = (
        # Feed hot path: keyset pagination newest-first (Backend §28)
        Index("ix_insight_feed", "tenant_id", text("occurred_at DESC")),
        enum_check("kind", InsightKind),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    kind: Mapped[str]
    resource_type: Mapped[str] = mapped_column(
        comment="Polymorphic target; integrity via service facade + orphan sweep (DB §21)"
    )
    resource_id: Mapped[uuid.UUID]
    headline: Mapped[str] = mapped_column(comment="≤22-word card text (AI §7)")
    severity: Mapped[str | None]
    salience_score: Mapped[float] = mapped_column(Numeric(8, 2), server_default=text("0"))
    ai_generated: Mapped[bool] = mapped_column(server_default=text("false"))
    occurred_at: Mapped[datetime]
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"))


class InsightFeedback(Base):
    """Cross-artifact 👍/👎 with reasons — golden-set mining substrate (DB §39)."""

    __tablename__ = "insight_feedback"

    id: Mapped[uuid.UUID] = uuid_pk()
    resource_type: Mapped[str]
    resource_id: Mapped[uuid.UUID]
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("app_user.id", ondelete="CASCADE"))
    positive: Mapped[bool]
    reason: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"))
