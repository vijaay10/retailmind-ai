"""Anthropic Claude provider implementation.

Implements the LLMProvider interface using Anthropic's Claude API.
"""

import time

import anthropic
import structlog

from app.infrastructure.llm.models import LLMRequest, LLMResponse
from app.infrastructure.llm.provider import (
    LLMProvider,
    LLMProviderError,
    LLMRateLimitError,
    LLMTimeoutError,
)
from app.infrastructure.llm.scrubbing import PIIScrubber

log = structlog.get_logger(__name__)

# Anthropic pricing (as of 2025, in USD per million tokens)
# https://www.anthropic.com/pricing
ANTHROPIC_PRICING = {
    "claude-sonnet-4-5-20250929": {"input": 3.00, "output": 15.00},
    "claude-opus-4-5-20251101": {"input": 15.00, "output": 75.00},
    "claude-3-5-sonnet-20241022": {"input": 3.00, "output": 15.00},
    "claude-3-opus-20240229": {"input": 15.00, "output": 75.00},
}


class AnthropicProvider(LLMProvider):
    """Anthropic Claude API provider.

    Handles API communication, retries, rate limiting, cost tracking.
    """

    def __init__(
        self,
        api_key: str,
        model_id: str = "claude-sonnet-4-5-20250929",
        timeout_seconds: int = 60,
        max_retries: int = 3,
        scrub_pii: bool = True,
    ):
        """Initialize Anthropic provider.

        Args:
            api_key: Anthropic API key
            model_id: Model identifier (default: Claude Sonnet 4.5)
            timeout_seconds: Request timeout in seconds
            max_retries: Max retries for transient errors
            scrub_pii: Whether to scrub PII from prompts
        """
        self._client = anthropic.AsyncAnthropic(
            api_key=api_key,
            timeout=timeout_seconds,
        )
        self._model_id = model_id
        self._max_retries = max_retries
        self._scrubber = PIIScrubber() if scrub_pii else None

    async def generate(self, request: LLMRequest) -> LLMResponse:
        """Generate response from Claude.

        Args:
            request: LLMRequest with prompt and configuration

        Returns:
            LLMResponse with generated content and usage data

        Raises:
            LLMProviderError: On permanent errors
            LLMRateLimitError: On rate limit (retryable)
            LLMTimeoutError: On timeout (retryable)
        """
        start_time = time.time()

        # Scrub PII if enabled
        prompt = request.prompt
        if self._scrubber and request.scrub_pii and self._scrubber.contains_pii(prompt):
            log.warning(
                "pii_detected_in_prompt",
                request_id=request.request_id,
                scrubbed=True,
            )
            prompt = self._scrubber.scrub(prompt)

        # Build messages for Claude
        messages = [{"role": "user", "content": prompt}]

        # Attempt request with retries
        for attempt in range(1, self._max_retries + 1):
            try:
                response = await self._client.messages.create(
                    model=self._model_id,
                    max_tokens=request.max_tokens,
                    temperature=request.temperature,
                    system=request.system_prompt if request.system_prompt else None,  # type: ignore[arg-type]
                    messages=messages,  # type: ignore[arg-type]
                )

                # Extract response
                content = response.content[0].text if response.content else ""  # type: ignore[union-attr]
                tokens_in = response.usage.input_tokens
                tokens_out = response.usage.output_tokens

                # Calculate cost
                cost_usd = self._calculate_cost(tokens_in, tokens_out, self._model_id)

                # Calculate latency
                latency_ms = int((time.time() - start_time) * 1000)

                log.info(
                    "llm_request_success",
                    request_id=request.request_id,
                    model=self._model_id,
                    tokens_in=tokens_in,
                    tokens_out=tokens_out,
                    cost_usd=cost_usd,
                    latency_ms=latency_ms,
                )

                return LLMResponse(
                    content=content,
                    request_id=request.request_id,
                    model_id=self._model_id,
                    prompt_version=request.prompt_version,
                    tokens_in=tokens_in,
                    tokens_out=tokens_out,
                    estimated_cost_usd=cost_usd,
                    latency_ms=latency_ms,
                    status="success",
                )

            except anthropic.RateLimitError as error:
                log.warning(
                    "llm_rate_limited",
                    request_id=request.request_id,
                    attempt=attempt,
                    max_retries=self._max_retries,
                )
                if attempt >= self._max_retries:
                    raise LLMRateLimitError(str(error)) from error

                # Exponential backoff
                await self._backoff(attempt)

            except anthropic.APITimeoutError as error:
                log.warning(
                    "llm_timeout",
                    request_id=request.request_id,
                    attempt=attempt,
                    max_retries=self._max_retries,
                )
                if attempt >= self._max_retries:
                    raise LLMTimeoutError(str(error)) from error

                await self._backoff(attempt)

            except anthropic.APIStatusError as error:
                # 5xx errors are retryable, 4xx are not
                if error.status_code >= 500:
                    log.warning(
                        "llm_server_error",
                        request_id=request.request_id,
                        status_code=error.status_code,
                        attempt=attempt,
                    )
                    if attempt >= self._max_retries:
                        raise LLMProviderError(str(error)) from error
                    await self._backoff(attempt)
                else:
                    # 4xx errors are permanent
                    log.error(
                        "llm_client_error",
                        request_id=request.request_id,
                        status_code=error.status_code,
                        error=str(error),
                    )
                    raise LLMProviderError(str(error)) from error

            except Exception as error:
                log.error(
                    "llm_unexpected_error",
                    request_id=request.request_id,
                    error=str(error),
                    error_type=type(error).__name__,
                )
                raise LLMProviderError(f"Unexpected error: {error}") from error

        # Should not reach here due to raises above, but satisfy type checker
        raise LLMProviderError("Max retries exceeded")

    async def health_check(self) -> bool:
        """Check if Anthropic API is accessible.

        Returns:
            True if API is reachable
        """
        try:
            # Make a minimal request to check connectivity
            response = await self._client.messages.create(
                model=self._model_id,
                max_tokens=10,
                messages=[{"role": "user", "content": "test"}],
            )
            return bool(response)
        except Exception as error:
            log.error("llm_health_check_failed", error=str(error))
            return False

    def _calculate_cost(self, tokens_in: int, tokens_out: int, model_id: str) -> float:
        """Calculate estimated cost in USD.

        Args:
            tokens_in: Input tokens
            tokens_out: Output tokens
            model_id: Model identifier

        Returns:
            Estimated cost in USD
        """
        pricing = ANTHROPIC_PRICING.get(model_id)
        if not pricing:
            log.warning("unknown_model_pricing", model_id=model_id)
            # Default to Sonnet pricing
            pricing = ANTHROPIC_PRICING["claude-sonnet-4-5-20250929"]

        cost_in = (tokens_in / 1_000_000) * pricing["input"]
        cost_out = (tokens_out / 1_000_000) * pricing["output"]

        return round(cost_in + cost_out, 6)

    async def _backoff(self, attempt: int) -> None:
        """Exponential backoff between retries.

        Args:
            attempt: Current attempt number
        """
        import asyncio

        delay = min(2**attempt, 30)  # Cap at 30 seconds
        log.info("llm_retry_backoff", attempt=attempt, delay_seconds=delay)
        await asyncio.sleep(delay)
