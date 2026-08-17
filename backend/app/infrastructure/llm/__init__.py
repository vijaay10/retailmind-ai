"""LLM Gateway — provider-independent interface to external AI models.

Architecture:
    Application
    ↓
    LLM Gateway
    ↓
    Provider Interface
    ↓
    Concrete Provider (Anthropic, OpenAI, etc.)
    ↓
    External API

Claude MUST NOT become the source of business numbers. Numbers originate from:
- Warehouse
- Semantic Layer
- Analytics Engine
- RCA Engine
- Forecasting Engine
- Recommendation Engine

Claude's job is to:
- Interpret verified evidence
- Reason over business data
- Explain patterns
- Summarize findings
- Answer business questions
- Suggest investigation paths
- Generate natural-language explanations
"""

from app.infrastructure.llm.gateway import LlmGateway
from app.infrastructure.llm.models import (
    EvidencePackage,
    EvidenceTier,
    LLMRequest,
    LLMResponse,
    LLMUsage,
)
from app.infrastructure.llm.prompts import PromptRegistry
from app.infrastructure.llm.provider import LLMProvider

__all__ = [
    "LLMProvider",
    "LLMRequest",
    "LLMResponse",
    "LLMUsage",
    "EvidencePackage",
    "EvidenceTier",
    "LlmGateway",
    "PromptRegistry",
]
