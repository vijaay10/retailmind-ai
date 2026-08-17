"""Mock LLM provider for testing and development.

Returns deterministic responses without making external API calls.
Used when RM_LLM_MOCK=1 or when no API key is configured.
"""

import time

import structlog

from app.infrastructure.llm.models import LLMRequest, LLMResponse
from app.infrastructure.llm.provider import LLMProvider

log = structlog.get_logger(__name__)


class MockProvider(LLMProvider):
    """Mock LLM provider for testing.

    Returns deterministic responses based on prompt keywords.
    No external API calls. Useful for tests and local development.
    """

    def __init__(self, fail_mode: bool = False):
        """Initialize mock provider.

        Args:
            fail_mode: If True, simulate errors
        """
        self._fail_mode = fail_mode

    async def generate(self, request: LLMRequest) -> LLMResponse:
        """Generate mock response.

        Args:
            request: LLMRequest

        Returns:
            LLMResponse with mock content
        """
        start_time = time.time()

        if self._fail_mode:
            return LLMResponse(
                content="",
                request_id=request.request_id,
                model_id="mock-model",
                prompt_version=request.prompt_version,
                tokens_in=0,
                tokens_out=0,
                estimated_cost_usd=0.0,
                latency_ms=0,
                status="error",
                error="Mock provider in fail mode",
            )

        # Generate deterministic response based on prompt keywords
        content = self._generate_mock_content(request.prompt)

        # Simulate token counts
        tokens_in = len(request.prompt.split())
        tokens_out = len(content.split())

        latency_ms = int((time.time() - start_time) * 1000)

        log.info(
            "mock_llm_response",
            request_id=request.request_id,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
        )

        return LLMResponse(
            content=content,
            request_id=request.request_id,
            model_id="mock-model-v1",
            prompt_version=request.prompt_version,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            estimated_cost_usd=0.0,  # Mock has no cost
            latency_ms=latency_ms,
            status="success",
        )

    async def health_check(self) -> bool:
        """Mock provider is always healthy."""
        return True

    def _generate_mock_content(self, prompt: str) -> str:
        """Generate deterministic mock content based on prompt.

        Args:
            prompt: Input prompt

        Returns:
            Mock response text
        """
        prompt_lower = prompt.lower()

        # Pattern matching for common tasks
        if "explain" in prompt_lower and "metric" in prompt_lower:
            return (
                "Based on the provided evidence, the metric shows a notable trend. "
                "The current state indicates performance within expected ranges. "
                "Key contributing factors include seasonal patterns and operational changes."
            )

        if "investigate" in prompt_lower or "next step" in prompt_lower:
            return (
                "Suggested investigation steps:\n"
                "1. Review the historical trend for this metric over the past 12 months\n"
                "2. Compare performance across different segments (stores, categories, regions)\n"
                "3. Examine any concurrent operational changes or external factors\n"
                "4. Validate data quality and completeness for the period in question\n"
                "5. Conduct a root cause analysis if the pattern persists"
            )

        if "summarize" in prompt_lower or "rca" in prompt_lower:
            return (
                "Summary: The analysis identifies seasonal demand variation as "
                "the primary driver. Contributing factors include: (1) Store "
                "operational changes, (2) Regional weather patterns, (3) Competitor "
                "activity. Confidence level: Medium. Limitations: Limited historical "
                "data for new product categories."
            )

        if "compare" in prompt_lower and "scenario" in prompt_lower:
            return (
                "Scenario Comparison:\n"
                "Scenario A shows higher revenue potential (+12%) but requires "
                "increased inventory investment. Scenario B optimizes for margin "
                "preservation with lower revenue growth (+6%). Trade-off: Short-term "
                "revenue growth vs. long-term profitability. Recommendation depends on "
                "current business priorities and available capital."
            )

        # Default response
        return (
            "Based on the provided evidence, this analysis requires careful "
            "consideration of multiple factors. The data suggests opportunities for "
            "further investigation. Please refer to the specific metrics and evidence "
            "provided for detailed insights."
        )
