"""LLM Gateway domain models and contracts.

Defines the data structures for LLM interactions, evidence grounding,
and usage tracking.
"""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class EvidenceTier(StrEnum):
    """Classification of evidence reliability.

    MEASURED: Direct measurement from warehouse (e.g., actual sales, inventory counts)
    MODELLED: Output from validated models (e.g., forecasts, elasticity estimates)
    ASSUMED: Parameters or assumptions not measured (e.g., default margins, industry benchmarks)
    """

    MEASURED = "measured"
    MODELLED = "modelled"
    ASSUMED = "assumed"


@dataclass(frozen=True, slots=True)
class EvidencePackage:
    """Structured evidence for LLM grounding.

    The LLM receives this package rather than arbitrary database access.
    Every piece of evidence is sourced, dated, and classified by reliability.
    """

    metric: str
    """Metric name (e.g., 'revenue', 'gross_margin', 'stockout_rate')"""

    value: float | str | dict[str, Any]
    """The actual value — number, text, or structured data"""

    period: str
    """Time period this evidence covers (e.g., '2026-07-01 to 2026-07-31')"""

    dimension: dict[str, str] | None = None
    """Dimensional filters (e.g., {'store_id': 'S2016', 'category': 'electronics'})"""

    source: str = "analytics"
    """Where this came from (analytics | forecast | rca | recommendation)"""

    query_id: str | None = None
    """Reference to semantic layer query or result ID for audit trail"""

    confidence: str = "high"
    """low | medium | high — confidence in this evidence"""

    tier: EvidenceTier = EvidenceTier.MEASURED
    """Evidence reliability tier"""

    limitations: list[str] = field(default_factory=list)
    """Known limitations (e.g., ['partial data', 'forecast not validated'])"""

    def as_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "value": self.value,
            "period": self.period,
            "dimension": self.dimension,
            "source": self.source,
            "query_id": self.query_id,
            "confidence": self.confidence,
            "tier": self.tier.value,
            "limitations": self.limitations,
        }


@dataclass(frozen=True, slots=True)
class LLMRequest:
    """Request to LLM provider.

    Encapsulates all information needed for a single LLM call.
    """

    prompt: str
    """The actual prompt text sent to the model"""

    system_prompt: str | None = None
    """System-level instructions (if provider supports it)"""

    evidence: list[EvidencePackage] = field(default_factory=list)
    """Grounded evidence the LLM should reason over"""

    prompt_version: str = "1.0"
    """Version of the prompt template used"""

    max_tokens: int = 2048
    """Maximum tokens in response"""

    temperature: float = 0.7
    """Temperature parameter (0.0-1.0)"""

    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    """Unique request identifier"""

    user_id: str | None = None
    """User making this request (for audit trail)"""

    scrub_pii: bool = True
    """Whether to scrub PII before sending to provider"""

    def as_dict(self) -> dict[str, Any]:
        return {
            "prompt": self.prompt,
            "system_prompt": self.system_prompt,
            "evidence": [e.as_dict() for e in self.evidence],
            "prompt_version": self.prompt_version,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "request_id": self.request_id,
            "user_id": self.user_id,
        }


@dataclass(frozen=True, slots=True)
class LLMResponse:
    """Response from LLM provider.

    Contains the generated text plus full usage metadata.
    """

    content: str
    """The generated text"""

    request_id: str
    """Matches the request_id from LLMRequest"""

    model_id: str
    """Actual model used (e.g., 'claude-sonnet-4-5-20250929')"""

    prompt_version: str
    """Prompt version that was used"""

    tokens_in: int
    """Input tokens consumed"""

    tokens_out: int
    """Output tokens generated"""

    estimated_cost_usd: float
    """Estimated cost in USD"""

    latency_ms: int
    """Response latency in milliseconds"""

    status: str
    """success | error | timeout | rate_limited"""

    error: str | None = None
    """Error message if status != success"""

    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    """When this response was generated"""

    metadata: dict[str, Any] = field(default_factory=dict)
    """Additional provider-specific metadata"""

    def as_dict(self) -> dict[str, Any]:
        return {
            "content": self.content,
            "request_id": self.request_id,
            "model_id": self.model_id,
            "prompt_version": self.prompt_version,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "estimated_cost_usd": self.estimated_cost_usd,
            "latency_ms": self.latency_ms,
            "status": self.status,
            "error": self.error,
            "timestamp": self.timestamp.isoformat(),
            "metadata": self.metadata,
        }


@dataclass(frozen=True, slots=True)
class LLMUsage:
    """Usage record for cost tracking and auditing.

    Written to database for every LLM call.
    """

    request_id: str
    tenant_id: str
    user_id: str | None
    model_id: str
    prompt_version: str
    tokens_in: int
    tokens_out: int
    estimated_cost_usd: float
    latency_ms: int
    status: str
    error: str | None
    timestamp: datetime

    def as_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "tenant_id": self.tenant_id,
            "user_id": self.user_id,
            "model_id": self.model_id,
            "prompt_version": self.prompt_version,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "estimated_cost_usd": self.estimated_cost_usd,
            "latency_ms": self.latency_ms,
            "status": self.status,
            "error": self.error,
            "timestamp": self.timestamp.isoformat(),
        }
