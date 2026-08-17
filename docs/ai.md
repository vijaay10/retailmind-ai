# AI & LLM Integration Guide

Practical guide to RetailMind AI's LLM integration - configuration, usage, cost management, and grounding principles.

**Last Updated**: 2026-08-15
**Version**: 0.9.0

---

## Table of Contents

- [Overview](#overview)
- [Architecture Summary](#architecture-summary)
- [Quick Start](#quick-start)
- [Provider Configuration](#provider-configuration)
- [Evidence-Based Grounding](#evidence-based-grounding)
- [Prompt Management](#prompt-management)
- [Cost Management](#cost-management)
- [PII Scrubbing](#pii-scrubbing)
- [Testing & Validation](#testing--validation)
- [Operational Monitoring](#operational-monitoring)
- [Troubleshooting](#troubleshooting)
- [What the LLM Can and Cannot Do](#what-the-llm-can-and-cannot-do)

---

## Overview

RetailMind AI uses an **evidence-based LLM architecture** where Claude explains verified analytical findings but never generates business numbers. All metrics, forecasts, and recommendations come from deterministic analytical engines.

### Key Principles

1. **LLM as Explanation Layer** - Claude receives structured evidence packages and explains them in natural language
2. **Defaults to Mock Mode** - No external API calls unless explicitly configured (zero cost, deterministic testing)
3. **Graceful Degradation** - Falls back to template-based headlines if LLM fails
4. **Evidence-Only Inputs** - LLM never sees raw SQL, database connections, or has direct warehouse access
5. **Response Validation** - Rejects responses that invent numbers not present in evidence

### Current Integration Status

**Implemented**:
- ✅ LLM gateway infrastructure (`backend/app/infrastructure/llm/`)
- ✅ Anthropic Claude provider (production-ready)
- ✅ Mock provider (deterministic testing)
- ✅ Evidence packaging and grounding system
- ✅ Prompt registry with versioning
- ✅ PII scrubbing layer
- ✅ Usage tracking and cost estimation
- ✅ AnalystNarrator for investigation summaries

**Capabilities Using LLM** (when enabled):
- **INVESTIGATE**: Root cause analysis summaries (headline generation)
- **COMPARE**: Period comparison narratives (partial implementation)

**Capabilities NOT Using LLM** (deterministic templates):
- EXPLAIN_KPI, ANSWER, RECOMMEND, SUMMARISE, EXPLAIN_FORECAST, IMPROVE

**Default Mode**: Mock provider (no external API calls)

---

## Architecture Summary

### Request Flow

```
AnalystService.ask("Why did revenue drop 15%?")
  ↓
RcaService.investigate()
  ↓ (9 dimensional investigators)
AnalystAnswer {
  facts: ["Revenue decreased 15% ($1.25M → $1.06M)",
          "Store #42 contributed -$120K (65% of total)"],
  inferences: ["Store #42 had 30% fewer transactions"],
  caveats: ["Weather data not available for correlation"],
  data: {...},
  headline: "Revenue decreased 15% from $1.25M to $1.06M."  ← Template
}
  ↓
AnalystNarrator.narrate_investigation(answer)
  ↓
Build EvidencePackage from facts/inferences
  ↓
PromptRegistry.get_task_prompt("summarize_rca_v1")
  ↓
LlmGateway.generate(LLMRequest)
  ↓
Provider.generate() [AnthropicProvider or MockProvider]
  ↓
Claude API (only if provider=anthropic)
  ↓
ResponseValidator.validate()
  ↓
LLMResponse { content: "Revenue fell 15% ($1.25M → $1.06M), driven primarily..." }
  ↓
Enhanced AnalystAnswer {
  facts: [...],        ← UNCHANGED
  inferences: [...],   ← UNCHANGED
  caveats: [...],      ← UNCHANGED
  headline: "Revenue fell 15%..."  ← LLM-GENERATED (fluent explanation)
}
  ↓
Return to user
```

**Deterministic Preserved**: Only `headline` field is enhanced. Facts, inferences, and caveats remain unchanged.

---

## Quick Start

### Development (Mock Mode - Default)

No configuration needed. Mock provider returns deterministic template-based responses.

```bash
# .env (default settings)
RM_LLM_PROVIDER=mock

# Start the app
make up

# Test the analyst endpoint
curl -X POST http://localhost:8000/api/v1/analyst/ \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"question": "Why did revenue drop 15%?"}'
```

**Response**:
```json
{
  "headline": "Based on analysis: Revenue decreased 15% from $1.25M to $1.06M, Store #42 contributed -$120K",
  "facts": ["Revenue decreased 15% ($1.25M → $1.06M)", "..."],
  "inferences": ["..."],
  "caveats": ["..."]
}
```

**Note**: Headline is template-based (MockProvider), not fluent natural language.

### Production (Anthropic Claude)

#### 1. Obtain API Key

Sign up at https://console.anthropic.com/ and generate an API key.

#### 2. Configure Environment

```bash
# .env.prod
RM_LLM_PROVIDER=anthropic
RM_LLM_ANTHROPIC_API_KEY_FILE=/run/secrets/anthropic_api_key
RM_LLM_MODEL_DEFAULT=claude-sonnet-3-5-20240620
RM_LLM_TIMEOUT_S=30
RM_LLM_MAX_TOKENS=512
```

#### 3. Mount Secret

```yaml
# compose.prod.yml
services:
  backend-api:
    secrets:
      - anthropic_api_key

secrets:
  anthropic_api_key:
    file: ./infra/secrets/anthropic_api_key.txt
```

#### 4. Create Secret File

```bash
# infra/secrets/anthropic_api_key.txt
sk-ant-api03-...
```

**Security**: File permissions `600`, never commit to git.

#### 5. Start Production Stack

```bash
docker compose -f infra/compose/compose.yml \
               -f infra/compose/compose.prod.yml \
               up -d
```

#### 6. Verify Integration

Check logs for LLM usage:

```bash
docker compose logs -f backend-api | grep llm_request_started
```

Expected output:
```
llm_request_started request_id=abc123 prompt_version=summarize_rca_v1 max_tokens=512
narrator.success request_id=abc123 tokens_total=458 cost_usd=0.00274
```

---

## Provider Configuration

### Available Providers

| Provider | Mode | Cost | Latency | Use Case |
|----------|------|------|---------|----------|
| **MockProvider** | Deterministic | $0 | <10ms | Testing, CI, default development |
| **AnthropicProvider** | API calls to Claude | ~$0.003/request | ~200ms | Production, staging |

### Switching Providers

**Environment Variable**: `RM_LLM_PROVIDER`

```bash
# Development: Mock (default)
RM_LLM_PROVIDER=mock

# Production: Anthropic Claude
RM_LLM_PROVIDER=anthropic
RM_LLM_ANTHROPIC_API_KEY_FILE=/run/secrets/anthropic_api_key
```

**Runtime Detection**:

```python
# backend/app/infrastructure/llm/__init__.py
def create_llm_gateway(settings: LLMSettings) -> LlmGateway:
    if settings.provider == "anthropic":
        if not settings.anthropic_api_key:
            raise ConfigurationError("Anthropic API key required")
        provider = AnthropicProvider(
            api_key=settings.anthropic_api_key,
            model=settings.model_default,
        )
    else:
        provider = MockProvider()

    return LlmGateway(
        provider=provider,
        usage_repo=usage_repository,
        settings=settings,
    )
```

### Model Configuration

**Environment Variables**:

```bash
# Model selection
RM_LLM_MODEL_DEFAULT=claude-sonnet-3-5-20240620  # Primary model
RM_LLM_MODEL_CHEAP=claude-haiku-3-5-20240307     # For simple tasks (future)

# Request parameters
RM_LLM_MAX_TOKENS=512          # Max response length
RM_LLM_TEMPERATURE=0.7          # Creativity (0.0-1.0)
RM_LLM_TIMEOUT_S=30             # Request timeout
```

**Model Characteristics**:

| Model | Cost (Input/Output) | Speed | Use Case |
|-------|---------------------|-------|----------|
| **Claude Sonnet 3.5** | $3/$15 per 1M tokens | ~200ms | Investigation summaries (current) |
| **Claude Haiku 3.5** | $0.80/$4 per 1M tokens | ~100ms | Simple explanations (future) |
| **Claude Opus 4** | $15/$75 per 1M tokens | ~500ms | Complex multi-step reasoning (future) |

**Current Usage**: Sonnet 3.5 for all narration (INVESTIGATE capability only)

---

## Evidence-Based Grounding

### Core Principle

**Claude NEVER generates business numbers.** All metrics come from analytical engines.

### Evidence Package Structure

**File**: `backend/app/infrastructure/llm/models.py`

```python
@dataclass
class EvidencePackage:
    """Structured fact for LLM to explain."""
    fact: str                       # "Revenue decreased 15%"
    value: float | str | None       # 1250000.00
    source: str                     # "fct_sales" (table/model)
    tier: EvidenceTier              # MEASURED, STATISTICAL, etc.
    confidence: float               # 0.95 (0.0-1.0)
    computation: str | None         # "SUM(line_total)" (optional)
    context: dict[str, Any] | None  # Additional metadata
```

### Evidence Tiers

**File**: `backend/app/infrastructure/llm/models.py`

```python
class EvidenceTier(Enum):
    """Strength of evidence classification."""
    ARITHMETIC = "arithmetic"        # Direct calculation (2+2=4)
    MECHANICAL = "mechanical"        # Deterministic system (SQL SUM)
    STATISTICAL = "statistical"      # Regression, correlation
    ASSOCIATIVE = "associative"      # Observed pattern (not causal)
    ASSUMED = "assumed"              # Hypothesis (not verified)
    UNKNOWN = "unknown"              # Explicitly unmeasured
```

**LLM Prompt Instructions** (vary by tier):

- **ARITHMETIC/MECHANICAL**: "shows", "demonstrates", "proves"
- **STATISTICAL**: "suggests", "indicates", "implies"
- **ASSOCIATIVE**: "is associated with", "moves with", "correlates"
- **ASSUMED**: "assumes", "if true would suggest"
- **UNKNOWN**: "cannot be measured", "data unavailable"

### Building Evidence

**File**: `backend/app/services/analyst/narrator.py`

```python
def _build_evidence(self, answer: AnalystAnswer) -> list[EvidencePackage]:
    """Convert AnalystAnswer facts/inferences into evidence packages."""
    evidence = []

    # Facts are MEASURED (direct from warehouse)
    for fact in answer.facts:
        # Parse fact string to extract values
        # Example: "Revenue decreased 15% ($1.25M → $1.06M)"
        evidence.append(
            EvidencePackage(
                fact=fact,
                value=self._extract_value(fact),
                source="fct_sales",
                tier=EvidenceTier.MECHANICAL,  # Warehouse query
                confidence=1.0,
                computation="SUM(line_total)",
            )
        )

    # Inferences are STATISTICAL or ASSOCIATIVE
    for inference in answer.inferences:
        # Example: "Store #42 had 30% fewer transactions"
        tier = self._classify_inference_tier(inference)
        evidence.append(
            EvidencePackage(
                fact=inference,
                value=self._extract_value(inference),
                source="investigation",
                tier=tier,
                confidence=self._estimate_confidence(inference),
            )
        )

    return evidence
```

### Formatting Evidence for LLM

```python
def _format_evidence(self, evidence: list[EvidencePackage]) -> str:
    """Format evidence into LLM-readable text."""
    lines = []

    for idx, e in enumerate(evidence, 1):
        tier_label = e.tier.value.upper()
        confidence_pct = int(e.confidence * 100)

        line = f"{idx}. [{tier_label}, {confidence_pct}% confidence] {e.fact}"

        if e.computation:
            line += f" (computed as: {e.computation})"

        if e.source:
            line += f" [source: {e.source}]"

        lines.append(line)

    return "\n".join(lines)
```

**Example Output**:

```
1. [MECHANICAL, 100% confidence] Revenue decreased 15% from $1.25M to $1.06M (computed as: SUM(line_total)) [source: fct_sales]
2. [MECHANICAL, 100% confidence] Store #42 contributed -$120K to the change (65% of total impact) [source: rca_decomposition]
3. [STATISTICAL, 85% confidence] Store #42 had 30% fewer transactions than expected [source: investigation]
4. [ASSOCIATIVE, 70% confidence] Transaction drop correlates with local event (concert cancellation) [source: investigation]
```

This structured evidence is passed to Claude, ensuring it only explains verified facts.

---

## Prompt Management

### Prompt Registry

**File**: `backend/app/infrastructure/llm/prompts.py`

Centralized, versioned prompts with grounding instructions.

```python
class PromptRegistry:
    """Versioned prompt templates."""

    TASK_PROMPTS = {
        "summarize_rca_v1": """...""",
        "explain_comparison_v1": """...""",
        # Future: v2, v3 as prompts evolve
    }

    SYSTEM_PROMPTS = {
        "business_analyst_v1": """...""",
    }

    @classmethod
    def get_task_prompt(cls, prompt_id: str, **kwargs) -> str:
        """Get task prompt with variable substitution."""
        template = cls.TASK_PROMPTS.get(prompt_id)
        if not template:
            raise ValueError(f"Unknown prompt: {prompt_id}")
        return template.format(**kwargs)

    @classmethod
    def get_system_prompt(cls, prompt_id: str) -> str:
        """Get system prompt."""
        return cls.SYSTEM_PROMPTS.get(prompt_id, "")
```

### Current Prompts

#### System Prompt: Business Analyst

```python
SYSTEM_PROMPTS = {
    "business_analyst_v1": """You are a business analyst for a retail company.

Your role is to explain analytical findings in clear, natural language.

CRITICAL RULES:
- You receive structured evidence packages with verified facts
- ONLY reference facts explicitly listed in the evidence
- DO NOT perform calculations or generate new numbers
- DO NOT extrapolate trends beyond provided data
- DO NOT infer causation beyond the evidence tier
- Use tier-appropriate language:
  - MECHANICAL: "shows", "demonstrates"
  - STATISTICAL: "suggests", "indicates"
  - ASSOCIATIVE: "is associated with", "correlates"

Your output should be concise (1-3 sentences), actionable, and grounded in evidence.
""",
}
```

#### Task Prompt: Summarize RCA

```python
TASK_PROMPTS = {
    "summarize_rca_v1": """Based on the verified evidence provided, explain what caused the metric change.

Evidence:
{evidence}

Provide a concise explanation (1-3 sentences) that:
1. States the root cause (most impactful dimension)
2. Cites the magnitude from the evidence
3. Notes confidence level if below 90%

ONLY cite facts from the evidence above. DO NOT calculate, extrapolate, or invent numbers.

Example response format:
"Revenue fell 15% ($1.25M → $1.06M), driven primarily by Store #42 which contributed -$120K (65% of total impact). The store experienced 30% fewer transactions, likely associated with a local event cancellation."
""",
}
```

### Prompt Versioning

**Naming Convention**: `{capability}_{version}`

- `summarize_rca_v1` - Initial RCA summary prompt
- `summarize_rca_v2` - Improved with better grounding instructions
- `explain_comparison_v1` - Period comparison narration

**Immutability**: Prompts are immutable. Create new versions (`v2`, `v3`) rather than modifying existing prompts.

**Tracking**: `llm_usage` table records `prompt_version` for every request, enabling A/B testing and performance comparison.

### Adding New Prompts

```python
# 1. Define in PromptRegistry
TASK_PROMPTS["explain_kpi_v1"] = """Based on the metric definition, explain what {metric} measures.

Evidence:
{evidence}

Provide:
1. What this metric measures (one sentence)
2. How it's calculated (cite evidence)
3. Common misreadings (if mentioned in evidence)

ONLY reference the definition in the evidence. Do not add external knowledge."""

# 2. Use in narrator
task_prompt = PromptRegistry.get_task_prompt(
    "explain_kpi_v1",
    metric="Average Order Value",
    evidence=evidence_text,
)

# 3. Track in LLMRequest
request = LLMRequest(
    prompt=task_prompt,
    prompt_version="explain_kpi_v1",  # Recorded in llm_usage
    ...
)
```

---

## Cost Management

### Cost Estimation

**File**: `backend/app/infrastructure/llm/anthropic_provider.py`

```python
def _estimate_cost(self, usage: anthropic.Usage) -> Decimal:
    """Estimate cost in USD based on token usage."""
    # Claude Sonnet 3.5 pricing (as of 2024-06-20)
    INPUT_COST = Decimal("3.00") / Decimal("1_000_000")   # $3 per 1M input tokens
    OUTPUT_COST = Decimal("15.00") / Decimal("1_000_000")  # $15 per 1M output tokens

    input_cost = Decimal(usage.input_tokens) * INPUT_COST
    output_cost = Decimal(usage.output_tokens) * OUTPUT_COST

    return input_cost + output_cost
```

### Typical Request Costs

| Capability | Avg Input Tokens | Avg Output Tokens | Cost per Request | Notes |
|-----------|------------------|-------------------|------------------|-------|
| **INVESTIGATE** | ~250 | ~150 | $0.00275 | 2-3 evidence items |
| **COMPARE** | ~350 | ~200 | $0.00405 | 4-5 comparisons |
| **EXPLAIN_KPI** | ~150 | ~100 | $0.00195 | Simple definition |

**Monthly Estimate** (10,000 analyst questions):
- INVESTIGATE: 10,000 × $0.00275 = **$27.50/month**
- Mixed workload: **$20-$30/month** (estimate)

### Usage Tracking

**Database**: `llm_usage` table

```sql
SELECT
  DATE(created_at) AS date,
  COUNT(*) AS requests,
  SUM(tokens_in) AS total_input_tokens,
  SUM(tokens_out) AS total_output_tokens,
  SUM(estimated_cost_usd) AS daily_cost
FROM llm_usage
WHERE created_at >= NOW() - INTERVAL '30 days'
GROUP BY DATE(created_at)
ORDER BY date DESC;
```

**Prometheus Metrics**:

```python
# backend/app/core/monitoring/prometheus.py
llm_requests_total = Counter("llm_requests_total", "Total LLM requests", ["provider", "prompt_version", "status"])
llm_tokens_total = Counter("llm_tokens_total", "Total tokens consumed", ["provider", "direction"])  # direction: in/out
llm_cost_usd_total = Counter("llm_cost_usd_total", "Total estimated cost in USD", ["provider"])
llm_request_duration_seconds = Histogram("llm_request_duration_seconds", "LLM request latency")
```

**Grafana Dashboard**:
- Daily cost trend
- Requests per capability
- Token usage distribution
- Average latency (p50, p95, p99)

### Budget Enforcement

**File**: `backend/app/infrastructure/llm/gateway.py`

```python
async def _check_budget_limits(self):
    """Enforce spending limits."""
    if not self._settings.budget_enabled:
        return

    # Daily budget
    today = datetime.now().date()
    daily_spend = await self._usage_repo.get_daily_spend(today)

    if daily_spend >= self._settings.budget_daily_usd:
        raise BudgetExceededError(
            f"Daily budget exceeded: ${daily_spend:.2f} >= ${self._settings.budget_daily_usd}"
        )

    # Monthly budget
    month_start = today.replace(day=1)
    monthly_spend = await self._usage_repo.get_spend_since(month_start)

    if monthly_spend >= self._settings.budget_monthly_usd:
        raise BudgetExceededError(
            f"Monthly budget exceeded: ${monthly_spend:.2f} >= ${self._settings.budget_monthly_usd}"
        )
```

**Configuration**:

```bash
# .env
RM_LLM_BUDGET_ENABLED=true
RM_LLM_BUDGET_DAILY_USD=5.00        # Max $5/day
RM_LLM_BUDGET_MONTHLY_USD=100.00    # Max $100/month
```

**Behavior**: When budget exceeded, `LlmGateway` raises `BudgetExceededError`. Narrator catches and falls back to deterministic template.

---

## PII Scrubbing

### Scrubbing Layer

**File**: `backend/app/infrastructure/llm/scrubbing.py`

Removes personally identifiable information before sending to Claude.

```python
class PIIScrubber:
    """Scrub PII from text before LLM processing."""

    @staticmethod
    def scrub(text: str) -> str:
        """Remove common PII patterns."""
        # Email addresses
        text = re.sub(
            r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b',
            '[EMAIL]',
            text
        )

        # Phone numbers (US format)
        text = re.sub(
            r'\b\d{3}[-.]?\d{3}[-.]?\d{4}\b',
            '[PHONE]',
            text
        )

        # Credit card numbers
        text = re.sub(
            r'\b\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}\b',
            '[CARD]',
            text
        )

        # SSN
        text = re.sub(
            r'\b\d{3}-\d{2}-\d{4}\b',
            '[SSN]',
            text
        )

        return text
```

**Applied Automatically** in `LlmGateway`:

```python
async def generate(self, request: LLMRequest) -> LLMResponse:
    # Scrub PII from prompt
    scrubbed_prompt = PIIScrubber.scrub(request.prompt)

    # Scrub evidence facts
    scrubbed_evidence = [
        EvidencePackage(
            fact=PIIScrubber.scrub(e.fact),
            value=e.value,  # Numbers not scrubbed
            source=e.source,
            tier=e.tier,
            confidence=e.confidence,
        )
        for e in request.evidence
    ]

    # Create scrubbed request
    scrubbed_request = LLMRequest(
        prompt=scrubbed_prompt,
        evidence=scrubbed_evidence,
        ...
    )

    return await self._provider.generate(scrubbed_request)
```

### Limitations

**Current Implementation**: Basic regex-based scrubbing

**Known Gaps**:
- Names (no NER - Named Entity Recognition)
- Addresses
- Non-US phone formats
- Custom identifiers (employee IDs, customer IDs)

**Enterprise Recommendation**:
- Integrate presidio (Microsoft) for advanced PII detection
- Add allowlist for safe business terms ("Store Manager", "CFO")
- Log scrubbed entities for audit trail

---

## Testing & Validation

### Unit Tests with MockProvider

**File**: `tests/unit/test_analyst_narrator.py`

```python
@pytest.fixture
def mock_gateway():
    """LLM gateway with mock provider."""
    provider = MockProvider()
    return LlmGateway(provider=provider)

@pytest.fixture
def narrator(mock_gateway):
    return AnalystNarrator(gateway=mock_gateway)

async def test_narrator_generates_headline_from_evidence(narrator):
    """Narrator builds evidence and generates headline."""
    answer = AnalystAnswer(
        question="Why did revenue drop?",
        capability="investigate",
        headline="Revenue decreased 15%.",  # Deterministic template
        facts=["Revenue decreased 15% ($1.25M → $1.06M)"],
        inferences=[],
        caveats=[],
    )

    enhanced_headline = await narrator.narrate_investigation(answer)

    # MockProvider returns template-based response
    assert "Revenue decreased 15%" in enhanced_headline
    assert "$1.25M" in enhanced_headline or "$1.06M" in enhanced_headline
```

### Integration Tests with Real API

**File**: `tests/integration/test_llm_narration.py`

```python
@pytest.mark.skipif(
    os.getenv("RM_LLM_PROVIDER") != "anthropic",
    reason="Requires Anthropic API key"
)
async def test_anthropic_provider_narration(db_session):
    """Test real Claude API integration."""
    api_key = os.getenv("RM_LLM_ANTHROPIC_API_KEY")
    if not api_key:
        pytest.skip("No API key configured")

    provider = AnthropicProvider(api_key=api_key)
    gateway = LlmGateway(provider=provider)
    narrator = AnalystNarrator(gateway=gateway)

    answer = AnalystAnswer(
        question="Why did revenue drop 15%?",
        capability="investigate",
        headline="Revenue decreased 15%.",
        facts=[
            "Revenue decreased 15% from $1.25M to $1.06M",
            "Store #42 contributed -$120K (65% of total impact)",
        ],
        inferences=["Store #42 had 30% fewer transactions"],
        caveats=[],
    )

    enhanced_headline = await narrator.narrate_investigation(answer)

    # Verify LLM generated fluent narration
    assert len(enhanced_headline) > len(answer.headline)
    assert "$1.25M" in enhanced_headline  # Must cite evidence
    assert "Store #42" in enhanced_headline
```

### Grounding Validation Tests

**File**: `tests/integration/test_llm_grounding.py`

```python
async def test_llm_does_not_invent_numbers(narrator):
    """Verify LLM only cites evidence numbers."""
    answer = AnalystAnswer(
        facts=["Revenue was $100K", "Orders were 50"],
        inferences=[],
        caveats=[],
        headline="Revenue was $100K across 50 orders.",
    )

    enhanced = await narrator.narrate_investigation(answer)

    # Extract all numbers from LLM response
    numbers_in_response = re.findall(r'\$?[\d,]+(?:\.\d+)?[KMB]?', enhanced)

    # Verify ONLY evidence numbers appear (100K, 50)
    allowed = {"100K", "100", "50"}
    for num in numbers_in_response:
        cleaned = num.replace('$', '').replace(',', '')
        assert cleaned in allowed, f"LLM invented number: {num}"

async def test_llm_does_not_perform_calculations(narrator):
    """Verify LLM doesn't calculate AOV from revenue/orders."""
    answer = AnalystAnswer(
        facts=["Revenue was $100K", "Orders were 50"],
        # AOV = $2K but NOT in evidence
        inferences=[],
        caveats=[],
        headline="Revenue was $100K across 50 orders.",
    )

    enhanced = await narrator.narrate_investigation(answer)

    # LLM should NOT mention AOV or $2K
    assert "AOV" not in enhanced.upper()
    assert "$2K" not in enhanced
    assert "2000" not in enhanced
```

---

## Operational Monitoring

### Health Checks

**File**: `backend/app/api/v1/health.py`

```python
@router.get("/health/llm")
async def llm_health_check():
    """Check LLM provider connectivity."""
    provider = get_llm_provider()

    if isinstance(provider, MockProvider):
        return {"status": "ok", "provider": "mock", "external_api": False}

    # Test Anthropic connectivity
    try:
        test_request = LLMRequest(
            prompt="Test",
            max_tokens=10,
            temperature=0.0,
        )
        response = await provider.generate(test_request)

        return {
            "status": "ok",
            "provider": "anthropic",
            "external_api": True,
            "latency_ms": response.latency_ms,
        }
    except Exception as e:
        return {
            "status": "error",
            "provider": "anthropic",
            "error": str(e),
        }
```

### Metrics & Alerts

**Prometheus Metrics**:

```python
# Request counts
llm_requests_total{provider="anthropic", prompt_version="summarize_rca_v1", status="success"} 1234

# Token usage
llm_tokens_total{provider="anthropic", direction="in"} 345678
llm_tokens_total{provider="anthropic", direction="out"} 234567

# Cost tracking
llm_cost_usd_total{provider="anthropic"} 27.45

# Latency
llm_request_duration_seconds_bucket{le="0.1"} 100
llm_request_duration_seconds_bucket{le="0.5"} 850
llm_request_duration_seconds_bucket{le="1.0"} 950
```

**Alerting Rules** (Prometheus):

```yaml
# alerts/llm.yml
groups:
  - name: llm
    rules:
      - alert: LLMHighErrorRate
        expr: rate(llm_requests_total{status="error"}[5m]) > 0.1
        for: 5m
        annotations:
          summary: "LLM error rate above 10%"

      - alert: LLMBudgetNearLimit
        expr: llm_cost_usd_total > 90  # $90 of $100 monthly budget
        annotations:
          summary: "LLM spending at 90% of monthly budget"

      - alert: LLMHighLatency
        expr: histogram_quantile(0.95, llm_request_duration_seconds) > 2.0
        for: 10m
        annotations:
          summary: "LLM P95 latency above 2s"
```

### Logging

**Structured Logs** (JSON format):

```json
{
  "event": "llm_request_started",
  "request_id": "abc123",
  "prompt_version": "summarize_rca_v1",
  "max_tokens": 512,
  "timestamp": "2026-08-15T10:30:00Z"
}

{
  "event": "narrator.success",
  "request_id": "abc123",
  "tokens_total": 458,
  "cost_usd": 0.00274,
  "latency_ms": 187,
  "timestamp": "2026-08-15T10:30:00.187Z"
}
```

**Log Queries** (for troubleshooting):

```bash
# Recent LLM failures
docker compose logs backend-api | grep llm_request_failed | tail -20

# High-cost requests
docker compose logs backend-api | grep narrator.success | jq 'select(.cost_usd > 0.01)'

# Slow requests
docker compose logs backend-api | grep narrator.success | jq 'select(.latency_ms > 1000)'
```

---

## Troubleshooting

### Common Issues

#### 1. "LLM request failed: API key not configured"

**Symptoms**:
```
LLMProviderError: Anthropic API key not configured
```

**Cause**: `RM_LLM_PROVIDER=anthropic` but no API key set.

**Fix**:
```bash
# .env
RM_LLM_ANTHROPIC_API_KEY_FILE=/run/secrets/anthropic_api_key

# Create secret file
echo "sk-ant-api03-..." > infra/secrets/anthropic_api_key.txt
chmod 600 infra/secrets/anthropic_api_key.txt

# Restart backend
docker compose restart backend-api
```

#### 2. "Budget exceeded" errors

**Symptoms**:
```
BudgetExceededError: Daily budget exceeded: $5.23 >= $5.00
```

**Cause**: LLM usage exceeded configured budget limits.

**Fix** (temporary):
```bash
# Increase budget
RM_LLM_BUDGET_DAILY_USD=10.00

# Or disable budget enforcement
RM_LLM_BUDGET_ENABLED=false
```

**Fix** (permanent):
- Review usage patterns (are prompts too long?)
- Optimize prompts to reduce token usage
- Consider using Haiku model for simple tasks
- Implement request throttling

#### 3. Fallback to deterministic headlines

**Symptoms**: Headlines are template-based, not fluent natural language.

**Possible Causes**:
1. Provider is MockProvider (check `RM_LLM_PROVIDER`)
2. LLM request failed and gracefully degraded
3. Budget limits hit
4. API timeout

**Debug**:
```bash
# Check provider configuration
docker compose exec backend-api env | grep RM_LLM

# Check recent LLM errors
docker compose logs backend-api | grep "narrator.failed"

# Check budget status
docker compose exec backend-api uv run python -c "
from app.infrastructure.db.repositories.llm_usage import LlmUsageRepository
repo = LlmUsageRepository()
print(repo.get_daily_spend())
"
```

#### 4. Response validation failures

**Symptoms**:
```
ResponseValidationError: LLM response contains numbers not in evidence
```

**Cause**: Claude generated numbers not present in EvidencePackage (hallucination).

**Action**: This is EXPECTED behavior (grounding enforcement). The narrator will fall back to deterministic headline. Log the incident for prompt improvement.

**Investigation**:
```python
# Extract validation failure details from logs
{
  "event": "response_validation_failed",
  "request_id": "abc123",
  "invented_numbers": ["$250K", "35%"],
  "allowed_numbers": ["$100K", "50"],
  "evidence_count": 2
}
```

**Fix**: Improve prompt grounding instructions in `PromptRegistry`.

#### 5. High latency (>2s P95)

**Symptoms**: Slow analyst responses, user complaints.

**Investigation**:
```bash
# Check LLM latency breakdown
docker compose logs backend-api | grep narrator.success | \
  jq -r '.latency_ms' | \
  awk '{sum+=$1; count++} END {print "Avg:", sum/count, "ms"}'

# Check Anthropic API status
curl https://status.anthropic.com/api/v2/status.json
```

**Possible Causes**:
1. Anthropic API slow (check status page)
2. Large evidence packages (too many facts)
3. Timeout too high (30s default)

**Mitigations**:
- Reduce `RM_LLM_MAX_TOKENS` (512 → 256)
- Limit evidence items (top 5 most important)
- Reduce `RM_LLM_TIMEOUT_S` (30 → 10)
- Cache LLM responses (future enhancement)

---

## What the LLM Can and Cannot Do

### ✅ LLM CAN

1. **Explain verified facts**
   - "Revenue decreased 15% from $1.25M to $1.06M" → "Revenue fell 15%, dropping from $1.25M to $1.06M..."

2. **Cite evidence sources**
   - "Store #42 contributed -$120K (source: rca_decomposition)"

3. **Use tier-appropriate language**
   - MECHANICAL evidence: "shows", "demonstrates"
   - STATISTICAL evidence: "suggests", "indicates"
   - ASSOCIATIVE evidence: "is associated with"

4. **Generate natural language headlines**
   - Transform template "Revenue decreased 15%." into fluent explanation

5. **Provide confidence levels**
   - "Revenue fell 15% (high confidence based on warehouse data)"

6. **Suggest follow-up questions**
   - "Would you like to investigate which product categories drove the decline?"

### ❌ LLM CANNOT

1. **Generate business numbers**
   - Cannot calculate AOV from revenue/orders
   - Cannot project future trends
   - Cannot estimate impact without explicit evidence

2. **Access database directly**
   - No SQL execution capability
   - No warehouse connection
   - Receives ONLY EvidencePackage objects

3. **Modify analytical truth**
   - Cannot change facts, metrics, confidence scores
   - Cannot adjust forecast values
   - Cannot alter recommendation rankings

4. **Perform calculations**
   - Cannot compute percentages, averages, sums
   - All math comes from analytical engines

5. **Extrapolate beyond data**
   - If evidence has 3 data points, cannot predict point 4
   - Cannot infer trends not explicitly in evidence

6. **Invent recommendations**
   - Can only explain recommendations from RecommendationService
   - Cannot suggest new actions

7. **Make causal claims beyond evidence tier**
   - ASSOCIATIVE evidence: can say "correlates", not "causes"
   - Must respect tier boundaries

### Enforcement Mechanisms

1. **Architecture**: No database connection in LLM context
2. **Evidence-only inputs**: Only EvidencePackage objects passed to LLM
3. **Response validation**: Rejects responses with numbers not in evidence
4. **Prompt instructions**: Explicit "DO NOT calculate/invent" rules
5. **Fallback**: Deterministic template if LLM fails or violates rules
6. **Immutable analytical results**: Facts/inferences/caveats unchanged by LLM

**Result**: LLM acts as a **narration layer only**, explaining verified analytical findings in natural language. All business intelligence comes from deterministic engines.

---

## Appendix

### File Reference

| File | Purpose |
|------|---------|
| `backend/app/infrastructure/llm/gateway.py` | LLM gateway coordinating providers, budgets, validation |
| `backend/app/infrastructure/llm/provider.py` | Abstract provider interface |
| `backend/app/infrastructure/llm/mock_provider.py` | Deterministic testing provider (default) |
| `backend/app/infrastructure/llm/anthropic_provider.py` | Claude API integration |
| `backend/app/infrastructure/llm/models.py` | EvidencePackage, LLMRequest, LLMResponse |
| `backend/app/infrastructure/llm/prompts.py` | Versioned prompt registry |
| `backend/app/infrastructure/llm/scrubbing.py` | PII removal layer |
| `backend/app/infrastructure/llm/validation.py` | Response grounding validation |
| `backend/app/services/analyst/narrator.py` | AnalystNarrator (LLM narration for investigations) |
| `backend/app/infrastructure/db/models/ai.py` | LlmUsage tracking table |

### Environment Variables

```bash
# Provider selection
RM_LLM_PROVIDER=mock|anthropic

# Anthropic configuration
RM_LLM_ANTHROPIC_API_KEY_FILE=/run/secrets/anthropic_api_key
RM_LLM_MODEL_DEFAULT=claude-sonnet-3-5-20240620
RM_LLM_MODEL_CHEAP=claude-haiku-3-5-20240307

# Request parameters
RM_LLM_MAX_TOKENS=512
RM_LLM_TEMPERATURE=0.7
RM_LLM_TIMEOUT_S=30

# Budget enforcement
RM_LLM_BUDGET_ENABLED=true|false
RM_LLM_BUDGET_DAILY_USD=5.00
RM_LLM_BUDGET_MONTHLY_USD=100.00
```

### Further Reading

- [Anthropic Claude API Docs](https://docs.anthropic.com/)
- [Evidence-Based AI (RAG)](https://arxiv.org/abs/2302.00093)
- [Grounding in Retrieval](https://ai.googleblog.com/2022/03/grounding-language-models-with-retrieval.html)

---

**Maintained by**: RetailMind AI Contributors
**License**: MIT
**Last Reviewed**: 2026-08-15
