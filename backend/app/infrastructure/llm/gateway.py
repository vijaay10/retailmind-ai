"""LLM Gateway — the unified interface for all LLM interactions.

Coordinates: provider selection, PII scrubbing, cost control, usage tracking,
response validation. Application code calls this gateway, not providers directly.
"""

import uuid

import structlog

from app.core.config import LLMSettings
from app.infrastructure.db.repositories.llm_usage import LlmUsageRepository
from app.infrastructure.llm.anthropic_provider import AnthropicProvider
from app.infrastructure.llm.mock_provider import MockProvider
from app.infrastructure.llm.models import LLMRequest, LLMResponse
from app.infrastructure.llm.provider import LLMProvider
from app.infrastructure.llm.validation import ResponseValidator

log = structlog.get_logger(__name__)


class LlmGateway:
    """High-level LLM gateway coordinating all LLM interactions.

    Architecture:
        Application → LlmGateway → Provider → External API

    The gateway handles:
    - Provider selection (Anthropic, Mock, etc.)
    - Budget enforcement
    - Usage tracking
    - Response validation
    - Error handling

    Example:
        gateway = LlmGateway(
            provider=anthropic_provider,
            usage_repo=usage_repo,
            settings=llm_settings,
        )

        response = await gateway.generate(
            LLMRequest(
                prompt="Explain this metric trend...",
                evidence=[...],
            )
        )
    """

    def __init__(
        self,
        provider: LLMProvider,
        usage_repo: LlmUsageRepository | None = None,
        settings: LLMSettings | None = None,
        validator: ResponseValidator | None = None,
    ):
        """Initialize LLM gateway.

        Args:
            provider: LLM provider (Anthropic, Mock, etc.)
            usage_repo: Optional repository for usage tracking
            settings: Optional LLM settings for budget enforcement
            validator: Optional response validator
        """
        self._provider = provider
        self._usage_repo = usage_repo
        self._settings = settings or LLMSettings()
        self._validator = validator or ResponseValidator()

    async def generate(self, request: LLMRequest, user_id: uuid.UUID | None = None) -> LLMResponse:
        """Generate a response from the LLM.

        Args:
            request: LLM request with prompt and configuration
            user_id: Optional user who initiated the request

        Returns:
            LLM response with generated content and usage data

        Raises:
            LLMProviderError: If generation fails
            ValueError: If budget limits exceeded
        """
        # Check budget limits before making request
        if self._usage_repo:
            await self._check_budget_limits()

        log.info(
            "llm_request_started",
            request_id=request.request_id,
            prompt_version=request.prompt_version,
            max_tokens=request.max_tokens,
        )

        try:
            # Call provider
            response = await self._provider.generate(request)

            # Validate response
            if request.evidence:
                self._validator.validate_citations(response, request.evidence)

            self._validator.validate_safe_content(response)

            # Track usage
            if self._usage_repo:
                await self._usage_repo.record_request(response, user_id=user_id)

            log.info(
                "llm_request_completed",
                request_id=response.request_id,
                status=response.status,
                tokens_total=response.tokens_in + response.tokens_out,
                cost_usd=response.estimated_cost_usd,
            )

            return response

        except Exception as error:
            log.error(
                "llm_request_failed",
                request_id=request.request_id,
                error=str(error),
                error_type=type(error).__name__,
            )
            raise

    async def _check_budget_limits(self) -> None:
        """Check if tenant is within budget limits.

        Raises:
            ValueError: If budget exceeded
        """
        if not self._usage_repo:
            return

        within_budget, reason = await self._usage_repo.check_budget_limit(
            daily_token_budget=self._settings.daily_token_budget,
            max_cost_per_request=self._settings.max_cost_per_request_usd,
        )

        if not within_budget:
            log.warning("llm_budget_exceeded", reason=reason)
            raise ValueError(f"LLM budget exceeded: {reason}")

    async def health_check(self) -> bool:
        """Check if the LLM provider is accessible.

        Returns:
            True if provider is healthy
        """
        try:
            return await self._provider.health_check()
        except Exception as error:
            log.error("llm_health_check_failed", error=str(error))
            return False

    @classmethod
    def create_from_settings(
        cls,
        settings: LLMSettings,
        usage_repo: LlmUsageRepository | None = None,
    ) -> "LlmGateway":
        """Factory method to create gateway from settings.

        Args:
            settings: LLM configuration settings
            usage_repo: Optional usage repository

        Returns:
            Configured LLM gateway
        """
        # Select provider based on settings
        provider: LLMProvider
        if settings.mock:
            log.info("llm_gateway_using_mock_provider")
            provider = MockProvider()
        elif settings.provider == "anthropic":
            if not settings.anthropic_api_key:
                log.warning(
                    "llm_no_api_key_configured",
                    message="No Anthropic API key configured, using mock mode",
                )
                provider = MockProvider()
            else:
                log.info(
                    "llm_gateway_using_anthropic",
                    model=settings.anthropic_model,
                )
                provider = AnthropicProvider(
                    api_key=settings.anthropic_api_key,
                    model_id=settings.anthropic_model,
                    timeout_seconds=settings.timeout_seconds,
                    max_retries=settings.max_retries,
                    scrub_pii=settings.scrub_pii,
                )
        else:
            raise ValueError(f"Unknown LLM provider: {settings.provider}")

        return cls(
            provider=provider,
            usage_repo=usage_repo,
            settings=settings,
        )
