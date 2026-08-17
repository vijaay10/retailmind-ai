"""LLM Provider interface — provider-independent abstraction.

All LLM providers must implement this interface. The application code
depends on this abstraction, not on any specific provider.
"""

from abc import ABC, abstractmethod

from app.infrastructure.llm.models import LLMRequest, LLMResponse


class LLMProvider(ABC):
    """Abstract interface for LLM providers.

    Implementations: AnthropicProvider, OpenAIProvider, MockProvider, etc.
    """

    @abstractmethod
    async def generate(self, request: LLMRequest) -> LLMResponse:
        """Generate a response from the LLM.

        Args:
            request: LLMRequest with prompt, evidence, and configuration

        Returns:
            LLMResponse with generated content and usage metadata

        Raises:
            LLMProviderError: On provider-specific errors
            LLMRateLimitError: When rate limited (retryable)
            LLMTimeoutError: When request times out (retryable)
        """
        ...

    @abstractmethod
    async def health_check(self) -> bool:
        """Check if the provider is accessible.

        Returns:
            True if provider is healthy, False otherwise
        """
        ...


class LLMProviderError(Exception):
    """Base exception for LLM provider errors."""

    pass


class LLMRateLimitError(LLMProviderError):
    """Provider rate limit exceeded (retryable)."""

    pass


class LLMTimeoutError(LLMProviderError):
    """Request timed out (retryable)."""

    pass


class LLMValidationError(LLMProviderError):
    """Response validation failed (not retryable)."""

    pass
