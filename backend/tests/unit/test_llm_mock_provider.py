"""Mock LLM provider — deterministic responses for testing."""

import pytest

from app.infrastructure.llm.mock_provider import MockProvider
from app.infrastructure.llm.models import LLMRequest


@pytest.fixture
def provider() -> MockProvider:
    return MockProvider()


@pytest.fixture
def fail_provider() -> MockProvider:
    return MockProvider(fail_mode=True)


# ── Basic functionality ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_mock_provider_generates_response(provider: MockProvider) -> None:
    request = LLMRequest(
        prompt="Explain the revenue trend",
        prompt_version="test_v1",
    )

    response = await provider.generate(request)

    assert response.content != ""
    assert response.status == "success"
    assert response.request_id == request.request_id
    assert response.model_id == "mock-model-v1"
    assert response.prompt_version == "test_v1"


@pytest.mark.asyncio
async def test_mock_provider_deterministic(provider: MockProvider) -> None:
    """Same prompt should produce same response."""
    prompt = "Explain the revenue metric"

    request1 = LLMRequest(prompt=prompt)
    request2 = LLMRequest(prompt=prompt)

    response1 = await provider.generate(request1)
    response2 = await provider.generate(request2)

    # Content should be identical for same prompt
    assert response1.content == response2.content


@pytest.mark.asyncio
async def test_mock_provider_no_cost(provider: MockProvider) -> None:
    """Mock provider should have zero cost."""
    request = LLMRequest(prompt="Test prompt")
    response = await provider.generate(request)

    assert response.estimated_cost_usd == 0.0


@pytest.mark.asyncio
async def test_mock_provider_token_counting(provider: MockProvider) -> None:
    """Mock provider should estimate token counts."""
    request = LLMRequest(prompt="This is a test prompt with multiple words")
    response = await provider.generate(request)

    # Should count tokens (roughly word count)
    assert response.tokens_in > 0
    assert response.tokens_out > 0


# ── Pattern matching ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_mock_explain_metric_pattern(provider: MockProvider) -> None:
    """Mock should recognize 'explain metric' pattern."""
    request = LLMRequest(prompt="Please explain the metric revenue for Q4")
    response = await provider.generate(request)

    assert "metric" in response.content.lower()
    assert "trend" in response.content.lower() or "evidence" in response.content.lower()


@pytest.mark.asyncio
async def test_mock_investigate_pattern(provider: MockProvider) -> None:
    """Mock should recognize 'investigate' pattern."""
    request = LLMRequest(prompt="Investigate the revenue decline")
    response = await provider.generate(request)

    assert "step" in response.content.lower() or "investigate" in response.content.lower()


@pytest.mark.asyncio
async def test_mock_summarize_pattern(provider: MockProvider) -> None:
    """Mock should recognize 'summarize' pattern."""
    request = LLMRequest(prompt="Summarize the RCA results")
    response = await provider.generate(request)

    assert "summary" in response.content.lower() or "analysis" in response.content.lower()


@pytest.mark.asyncio
async def test_mock_compare_pattern(provider: MockProvider) -> None:
    """Mock should recognize 'compare scenario' pattern."""
    request = LLMRequest(prompt="Compare scenario A and scenario B")
    response = await provider.generate(request)

    assert "scenario" in response.content.lower() or "comparison" in response.content.lower()


@pytest.mark.asyncio
async def test_mock_default_response(provider: MockProvider) -> None:
    """Unknown pattern should return default response."""
    request = LLMRequest(prompt="Some random unrecognized prompt")
    response = await provider.generate(request)

    assert "evidence" in response.content.lower()
    assert response.status == "success"


# ── Fail mode ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fail_mode_returns_error(fail_provider: MockProvider) -> None:
    """Fail mode should return error response."""
    request = LLMRequest(prompt="Test prompt")
    response = await fail_provider.generate(request)

    assert response.status == "error"
    assert response.error == "Mock provider in fail mode"
    assert response.content == ""
    assert response.tokens_in == 0
    assert response.tokens_out == 0


# ── Health check ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_health_check_always_healthy(provider: MockProvider) -> None:
    """Mock provider is always healthy."""
    assert await provider.health_check() is True


@pytest.mark.asyncio
async def test_health_check_even_in_fail_mode(fail_provider: MockProvider) -> None:
    """Health check passes even in fail mode."""
    assert await fail_provider.health_check() is True


# ── Real-world usage ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_mock_with_evidence(provider: MockProvider) -> None:
    """Mock provider should handle requests with evidence."""
    from app.infrastructure.llm.models import EvidencePackage, EvidenceTier

    request = LLMRequest(
        prompt="Explain this trend",
        evidence=[
            EvidencePackage(
                metric="revenue",
                value=125000,
                period="Q4",
                tier=EvidenceTier.MEASURED,
            )
        ],
    )

    response = await provider.generate(request)
    assert response.status == "success"


@pytest.mark.asyncio
async def test_mock_with_system_prompt(provider: MockProvider) -> None:
    """Mock provider should handle system prompts."""
    request = LLMRequest(
        prompt="Analyze the data",
        system_prompt="You are a business analyst",
    )

    response = await provider.generate(request)
    assert response.status == "success"


@pytest.mark.asyncio
async def test_mock_respects_request_id(provider: MockProvider) -> None:
    """Mock should preserve request_id."""
    request_id = "custom-request-id-123"
    request = LLMRequest(prompt="Test", request_id=request_id)

    response = await provider.generate(request)
    assert response.request_id == request_id


@pytest.mark.asyncio
async def test_mock_multiple_requests_independent(provider: MockProvider) -> None:
    """Multiple requests should be independent."""
    request1 = LLMRequest(prompt="Explain metric")
    request2 = LLMRequest(prompt="Investigate issue")

    response1 = await provider.generate(request1)
    response2 = await provider.generate(request2)

    # Different prompts should produce different responses
    assert response1.content != response2.content
    assert response1.request_id != response2.request_id
