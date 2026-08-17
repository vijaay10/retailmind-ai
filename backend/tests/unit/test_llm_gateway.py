"""LLM Gateway — integrated coordination of all LLM components."""

from unittest.mock import AsyncMock, Mock

import pytest

from app.core.config import LLMSettings
from app.infrastructure.llm.gateway import LlmGateway
from app.infrastructure.llm.mock_provider import MockProvider
from app.infrastructure.llm.models import EvidencePackage, EvidenceTier, LLMRequest


@pytest.fixture
def mock_provider() -> MockProvider:
    return MockProvider()


@pytest.fixture
def mock_usage_repo() -> Mock:
    """Mock usage repository."""
    repo = Mock()
    repo.check_budget_limit = AsyncMock(return_value=(True, None))
    repo.record_request = AsyncMock()
    return repo


@pytest.fixture
def llm_settings() -> LLMSettings:
    """Test LLM settings."""
    return LLMSettings(
        mock=True,
        daily_token_budget=1_000_000,
        max_cost_per_request_usd=1.0,
    )


@pytest.fixture
def gateway(
    mock_provider: MockProvider,
    mock_usage_repo: Mock,
    llm_settings: LLMSettings,
) -> LlmGateway:
    return LlmGateway(
        provider=mock_provider,
        usage_repo=mock_usage_repo,
        settings=llm_settings,
    )


# ── Basic generation ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_gateway_generate_success(gateway: LlmGateway) -> None:
    request = LLMRequest(prompt="Explain the revenue trend")
    response = await gateway.generate(request)

    assert response.status == "success"
    assert response.content != ""


@pytest.mark.asyncio
async def test_gateway_tracks_usage(
    gateway: LlmGateway,
    mock_usage_repo: Mock,
) -> None:
    """Gateway should record usage after successful generation."""
    request = LLMRequest(prompt="Test prompt")
    response = await gateway.generate(request)

    # Usage should be recorded
    mock_usage_repo.record_request.assert_called_once()
    call_args = mock_usage_repo.record_request.call_args
    assert call_args[0][0].request_id == response.request_id


@pytest.mark.asyncio
async def test_gateway_checks_budget_before_request(
    gateway: LlmGateway,
    mock_usage_repo: Mock,
) -> None:
    """Gateway should check budget limits before making request."""
    request = LLMRequest(prompt="Test prompt")
    await gateway.generate(request)

    # Budget check should be called
    mock_usage_repo.check_budget_limit.assert_called_once()


@pytest.mark.asyncio
async def test_gateway_without_usage_repo(
    mock_provider: MockProvider,
    llm_settings: LLMSettings,
) -> None:
    """Gateway should work without usage repository."""
    gateway = LlmGateway(
        provider=mock_provider,
        usage_repo=None,
        settings=llm_settings,
    )

    request = LLMRequest(prompt="Test prompt")
    response = await gateway.generate(request)

    assert response.status == "success"


# ── Budget enforcement ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_gateway_enforces_budget_limit(
    mock_provider: MockProvider,
    llm_settings: LLMSettings,
) -> None:
    """Gateway should reject requests if budget exceeded."""
    # Mock usage repo that returns budget exceeded
    usage_repo = Mock()
    usage_repo.check_budget_limit = AsyncMock(return_value=(False, "Daily token budget exceeded"))

    gateway = LlmGateway(
        provider=mock_provider,
        usage_repo=usage_repo,
        settings=llm_settings,
    )

    request = LLMRequest(prompt="Test prompt")

    with pytest.raises(ValueError, match="LLM budget exceeded"):
        await gateway.generate(request)


# ── Response validation ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_gateway_validates_citations(
    gateway: LlmGateway,
) -> None:
    """Gateway should validate citations against evidence."""
    evidence = [
        EvidencePackage(
            metric="revenue",
            value=125000,
            period="Q4",
            source="analytics",
            tier=EvidenceTier.MEASURED,
        )
    ]

    request = LLMRequest(
        prompt="Explain revenue [source: analytics]",
        evidence=evidence,
    )

    # Should not raise
    response = await gateway.generate(request)
    assert response.status == "success"


@pytest.mark.asyncio
async def test_gateway_validates_safe_content(
    gateway: LlmGateway,
) -> None:
    """Gateway should validate content safety."""
    request = LLMRequest(prompt="Safe business analysis")

    # Should not raise
    response = await gateway.generate(request)
    assert response.status == "success"


# ── Factory method ───────────────────────────────────────────────────


def test_create_from_settings_mock_mode() -> None:
    """Factory should create gateway with mock provider when mock=True."""
    settings = LLMSettings(mock=True)
    gateway = LlmGateway.create_from_settings(settings)

    assert gateway is not None
    assert isinstance(gateway._provider, MockProvider)


def test_create_from_settings_no_api_key_falls_back_to_mock() -> None:
    """Factory should use mock when no API key configured."""
    settings = LLMSettings(
        provider="anthropic",
        anthropic_api_key=None,
        mock=False,
    )
    gateway = LlmGateway.create_from_settings(settings)

    # Should fall back to mock
    assert isinstance(gateway._provider, MockProvider)


def test_create_from_settings_anthropic_with_key() -> None:
    """Factory should create Anthropic provider when key configured."""
    from app.infrastructure.llm.anthropic_provider import AnthropicProvider

    settings = LLMSettings(
        provider="anthropic",
        anthropic_api_key="test-api-key",
        mock=False,
    )
    gateway = LlmGateway.create_from_settings(settings)

    assert isinstance(gateway._provider, AnthropicProvider)


def test_create_from_settings_unknown_provider_raises() -> None:
    """Factory should raise for unknown provider."""
    settings = LLMSettings(provider="unknown-provider", mock=False)

    with pytest.raises(ValueError, match="Unknown LLM provider"):
        LlmGateway.create_from_settings(settings)


# ── Health check ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_gateway_health_check(gateway: LlmGateway) -> None:
    """Gateway should proxy health check to provider."""
    result = await gateway.health_check()
    assert result is True


@pytest.mark.asyncio
async def test_gateway_health_check_failure() -> None:
    """Gateway should handle provider health check failures."""
    # Mock provider that fails health check
    provider = Mock()
    provider.health_check = AsyncMock(side_effect=Exception("Connection failed"))

    gateway = LlmGateway(provider=provider)
    result = await gateway.health_check()
    assert result is False


# ── User tracking ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_gateway_tracks_user_id(
    gateway: LlmGateway,
    mock_usage_repo: Mock,
) -> None:
    """Gateway should track which user made the request."""
    import uuid

    user_id = uuid.uuid4()
    request = LLMRequest(prompt="Test prompt")

    await gateway.generate(request, user_id=user_id)

    # Usage should include user_id
    call_args = mock_usage_repo.record_request.call_args
    assert call_args[1]["user_id"] == user_id


# ── Error handling ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_gateway_handles_provider_errors() -> None:
    """Gateway should propagate provider errors."""
    from app.infrastructure.llm.provider import LLMProviderError

    # Mock provider that raises error
    provider = Mock()
    provider.generate = AsyncMock(side_effect=LLMProviderError("API error"))

    gateway = LlmGateway(provider=provider)
    request = LLMRequest(prompt="Test prompt")

    with pytest.raises(LLMProviderError, match="API error"):
        await gateway.generate(request)


# ── Real-world scenarios ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_gateway_full_flow_with_evidence(
    gateway: LlmGateway,
    mock_usage_repo: Mock,
) -> None:
    """Real-world flow: request with evidence, validation, tracking."""
    import uuid

    user_id = uuid.uuid4()

    evidence = [
        EvidencePackage(
            metric="revenue",
            value=125000.0,
            period="2024-Q4",
            source="analytics",
            query_id="query-123",
            tier=EvidenceTier.MEASURED,
        ),
        EvidencePackage(
            metric="transactions",
            value=1500,
            period="2024-Q4",
            source="warehouse",
            tier=EvidenceTier.MEASURED,
        ),
    ]

    request = LLMRequest(
        prompt="Explain the revenue increase in Q4 [source: analytics]",
        system_prompt="You are a business analyst",
        evidence=evidence,
        prompt_version="explain_v1",
        max_tokens=1024,
        temperature=0.7,
    )

    response = await gateway.generate(request, user_id=user_id)

    # Response should be successful
    assert response.status == "success"
    assert response.content != ""
    assert response.prompt_version == "explain_v1"

    # Budget should be checked
    mock_usage_repo.check_budget_limit.assert_called_once()

    # Usage should be recorded
    mock_usage_repo.record_request.assert_called_once()
