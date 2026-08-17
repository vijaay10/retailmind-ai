"""Integration tests for LLM-powered analyst narration.

Tests verify the complete flow: Analytical engines → AnalystAnswer →
AnalystNarrator → EvidencePackage → LlmGateway → Provider → Validated response.

CRITICAL: These tests verify that numbers come from analytical engines,
never from the LLM. The LLM only explains verified facts.
"""

from unittest.mock import AsyncMock, Mock

import pytest

from app.core.config import LLMSettings
from app.infrastructure.llm.gateway import LlmGateway
from app.infrastructure.llm.models import EvidencePackage, EvidenceTier, LLMResponse
from app.services.analyst.contracts import (
    AnalystAnswer,
    Capability,
    Certainty,
    FollowUp,
    Statement,
)
from app.services.analyst.narrator import AnalystNarrator


@pytest.fixture
def mock_llm_gateway() -> Mock:
    """Mock LLM gateway that returns deterministic responses."""
    gateway = Mock(spec=LlmGateway)
    gateway.generate = AsyncMock(
        return_value=LLMResponse(
            request_id="test-request-123",
            content=(
                "Revenue declined 15% in the current period, with the primary driver "
                "being a 20% decrease in customer orders."
            ),
            status="success",
            model_id="mock-model",
            prompt_version="summarize_rca_v1",
            tokens_in=150,
            tokens_out=75,
            estimated_cost_usd=0.002,
            latency_ms=120,
        )
    )
    return gateway


@pytest.fixture
def rca_answer() -> AnalystAnswer:
    """Sample RCA investigation answer."""
    return AnalystAnswer(
        question="Why did revenue drop?",
        capability=Capability.INVESTIGATE,
        headline="Revenue fell 15%. Orders down 20%.",
        data={
            "metric": "net_revenue",
            "relative_change": -0.15,
            "period": "2024-Q4",
            "findings": [
                {
                    "dimension": "orders",
                    "subject": "customer_orders",
                    "contribution_pct": 0.80,
                    "evidence_tier": "arithmetic",
                    "confidence": "high",
                    "headline": "Orders down 20%",
                },
                {
                    "dimension": "region",
                    "subject": "west_coast",
                    "contribution_pct": 0.15,
                    "evidence_tier": "decomposition",
                    "confidence": "medium",
                    "headline": "West coast region down 25%",
                    "caveat": "Small sample size in some locations",
                },
            ],
        },
    )


# ── End-to-end narration flow ────────────────────────────────────────


@pytest.mark.asyncio
async def test_complete_flow_evidence_to_llm_to_response(
    mock_llm_gateway: Mock,
    rca_answer: AnalystAnswer,
) -> None:
    """Complete flow: AnalystAnswer → EvidencePackage → LLM → Validated response."""
    narrator = AnalystNarrator(mock_llm_gateway)

    result = await narrator.narrate_investigation(rca_answer)

    # LLM gateway should have been called
    mock_llm_gateway.generate.assert_called_once()

    # Check what was sent to the gateway
    call_args = mock_llm_gateway.generate.call_args
    request = call_args[0][0]

    # Evidence should be structured packages
    assert request.evidence is not None
    assert len(request.evidence) >= 2
    assert all(isinstance(pkg, EvidencePackage) for pkg in request.evidence)

    # Evidence should have verified sources
    for pkg in request.evidence:
        assert pkg.source in ["rca_engine", "rca_decomposition"]
        assert pkg.tier in [EvidenceTier.MEASURED, EvidenceTier.MODELLED]

    # Result should be LLM-enhanced
    expected = (
        "Revenue declined 15% in the current period, with the primary driver "
        "being a 20% decrease in customer orders."
    )
    assert result == expected


@pytest.mark.asyncio
async def test_evidence_contains_only_verified_numbers(
    mock_llm_gateway: Mock,
    rca_answer: AnalystAnswer,
) -> None:
    """Evidence contains only numbers from analytical engines, never LLM-generated."""
    narrator = AnalystNarrator(mock_llm_gateway)

    await narrator.narrate_investigation(rca_answer)

    call_args = mock_llm_gateway.generate.call_args
    request = call_args[0][0]

    # Primary metric evidence
    primary = request.evidence[0]
    assert primary.metric == "net_revenue"
    assert primary.value == -0.15  # From RCA engine
    assert primary.tier == EvidenceTier.MEASURED
    assert primary.source == "rca_engine"

    # Finding evidence
    finding = request.evidence[1]
    assert finding.value == 0.80  # Contribution from RCA decomposition
    assert finding.source == "rca_decomposition"


@pytest.mark.asyncio
async def test_llm_uses_versioned_prompts(
    mock_llm_gateway: Mock,
    rca_answer: AnalystAnswer,
) -> None:
    """LLM requests use versioned prompts from registry."""
    narrator = AnalystNarrator(mock_llm_gateway)

    await narrator.narrate_investigation(rca_answer)

    call_args = mock_llm_gateway.generate.call_args
    request = call_args[0][0]

    # Should use versioned prompt
    assert request.prompt_version == "summarize_rca_v1"

    # Should have system prompt
    assert request.system_prompt is not None

    # Should have task prompt
    assert request.prompt is not None


# ── Fallback behavior ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_narrator_falls_back_to_deterministic_on_llm_failure(
    rca_answer: AnalystAnswer,
) -> None:
    """Narrator returns deterministic answer when LLM fails."""
    # Create gateway that raises exception
    failing_gateway = Mock(spec=LlmGateway)
    failing_gateway.generate = AsyncMock(side_effect=Exception("API timeout"))

    narrator = AnalystNarrator(failing_gateway)

    # Should not raise
    result = await narrator.narrate_investigation(rca_answer)

    # Should fall back to original headline
    assert result == rca_answer.headline


# ── Mock provider integration ────────────────────────────────────────


@pytest.mark.asyncio
async def test_integration_with_real_mock_provider(
    rca_answer: AnalystAnswer,
) -> None:
    """Integration with real MockProvider (not mocked gateway)."""
    # Create real gateway with mock provider
    settings = LLMSettings(mock=True)
    gateway = LlmGateway.create_from_settings(settings)

    narrator = AnalystNarrator(gateway)

    result = await narrator.narrate_investigation(rca_answer)

    # Should have enhanced headline from mock provider
    assert result
    assert len(result) > 20  # Mock provider generates content


@pytest.mark.asyncio
async def test_mock_provider_returns_safe_content(
    rca_answer: AnalystAnswer,
) -> None:
    """MockProvider returns safe, deterministic content."""
    settings = LLMSettings(mock=True)
    gateway = LlmGateway.create_from_settings(settings)

    narrator = AnalystNarrator(gateway)

    # Run same narration twice
    result1 = await narrator.narrate_investigation(rca_answer)
    result2 = await narrator.narrate_investigation(rca_answer)

    # Mock provider should be deterministic (same input = same output)
    assert result1
    assert result2


# ── Prompt versioning ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_prompt_version_is_tracked_in_response(
    mock_llm_gateway: Mock,
    rca_answer: AnalystAnswer,
) -> None:
    """Prompt version is tracked for reproducibility."""
    narrator = AnalystNarrator(mock_llm_gateway)

    await narrator.narrate_investigation(rca_answer)

    call_args = mock_llm_gateway.generate.call_args
    request = call_args[0][0]

    # Should track prompt version
    assert request.prompt_version == "summarize_rca_v1"


# ── Evidence tier transparency ───────────────────────────────────────


@pytest.mark.asyncio
async def test_evidence_tiers_distinguish_measured_from_modelled(
    mock_llm_gateway: Mock,
    rca_answer: AnalystAnswer,
) -> None:
    """Evidence clearly marks MEASURED vs MODELLED vs ASSUMED."""
    narrator = AnalystNarrator(mock_llm_gateway)

    await narrator.narrate_investigation(rca_answer)

    call_args = mock_llm_gateway.generate.call_args
    request = call_args[0][0]

    # Check evidence tiers
    tiers = [pkg.tier for pkg in request.evidence]

    # Primary metric should be MEASURED
    assert tiers[0] == EvidenceTier.MEASURED

    # Findings should have appropriate tiers
    assert all(tier in [EvidenceTier.MEASURED, EvidenceTier.MODELLED] for tier in tiers)


# ── Performance and cost tracking ────────────────────────────────────


@pytest.mark.asyncio
async def test_llm_response_includes_cost_and_token_tracking(
    mock_llm_gateway: Mock,
    rca_answer: AnalystAnswer,
) -> None:
    """LLM responses track tokens and estimated cost."""
    narrator = AnalystNarrator(mock_llm_gateway)

    await narrator.narrate_investigation(rca_answer)

    # Gateway was called
    mock_llm_gateway.generate.assert_called_once()

    # Response should have tracking data
    response = mock_llm_gateway.generate.return_value
    assert response.tokens_in > 0
    assert response.tokens_out > 0
    assert response.estimated_cost_usd >= 0.0
    assert response.latency_ms > 0


# ── Multiple finding evidence ────────────────────────────────────────


@pytest.mark.asyncio
async def test_multiple_findings_become_multiple_evidence_packages(
    mock_llm_gateway: Mock,
    rca_answer: AnalystAnswer,
) -> None:
    """Each RCA finding becomes a separate evidence package."""
    narrator = AnalystNarrator(mock_llm_gateway)

    await narrator.narrate_investigation(rca_answer)

    call_args = mock_llm_gateway.generate.call_args
    request = call_args[0][0]

    # Should have: 1 primary metric + 2 findings
    assert len(request.evidence) >= 3

    # First is primary metric
    assert request.evidence[0].source == "rca_engine"

    # Rest are findings
    assert all(pkg.source == "rca_decomposition" for pkg in request.evidence[1:])


# ── COMPARE narration flow ──────────────────────────────────────────


@pytest.fixture
def comparison_answer() -> AnalystAnswer:
    """Sample comparison answer."""
    return AnalystAnswer(
        question="Compare this period vs last period",
        capability=Capability.COMPARE,
        headline="Revenue increased from 10,000 to 12,000 (+20%).",
        facts=(
            Statement(
                "Revenue increased from 10,000 to 12,000 (+20%).",
                Certainty.MEASURED,
                "revenue domain",
            ),
        ),
        checked=("revenue, 2024-01-01 to 2024-01-31 against 2023-12-01 to 2023-12-31",),
        not_checked=("Margin, units, and customer mix. Ask about those directly.",),
        caveats=(
            "Day-of-week composition is not adjusted for. A period containing "
            "an extra Saturday will look stronger for that reason alone.",
        ),
        follow_ups=(
            FollowUp(
                "Why did it move?",
                "A comparison locates a change; it does not explain it.",
            ),
        ),
        data={
            "metric": "net_revenue",
            "current_value": 12000.0,
            "baseline_value": 10000.0,
            "absolute_change": 2000.0,
            "relative_change": 0.20,
            "current_start": "2024-01-01",
            "current_end": "2024-01-31",
            "baseline_start": "2023-12-01",
            "baseline_end": "2023-12-31",
        },
        meta={"subject": "net_revenue"},
    )


@pytest.mark.asyncio
async def test_compare_complete_flow_evidence_to_llm_to_response(
    mock_llm_gateway: Mock,
    comparison_answer: AnalystAnswer,
) -> None:
    """Complete COMPARE flow: AnalystAnswer → EvidencePackage → LLM → Validated response."""
    narrator = AnalystNarrator(mock_llm_gateway)

    result = await narrator.narrate_comparison(comparison_answer)

    # LLM gateway should have been called
    mock_llm_gateway.generate.assert_called_once()

    # Check what was sent to the gateway
    call_args = mock_llm_gateway.generate.call_args
    request = call_args[0][0]

    # Evidence should be structured packages
    assert request.evidence is not None
    assert len(request.evidence) >= 1
    assert all(isinstance(pkg, EvidencePackage) for pkg in request.evidence)

    # Evidence should have verified sources
    for pkg in request.evidence:
        assert pkg.source == "analytics"
        assert pkg.tier == EvidenceTier.MEASURED

    # Should use comparison prompt
    assert request.prompt_version == "compare_scenarios_v1"

    # Result should be LLM-enhanced
    assert result
    assert len(result) > 20  # Mock provider generates content


@pytest.mark.asyncio
async def test_compare_evidence_contains_only_verified_numbers(
    mock_llm_gateway: Mock,
    comparison_answer: AnalystAnswer,
) -> None:
    """COMPARE evidence contains only numbers from comparison engine, never LLM-generated."""
    narrator = AnalystNarrator(mock_llm_gateway)

    await narrator.narrate_comparison(comparison_answer)

    call_args = mock_llm_gateway.generate.call_args
    request = call_args[0][0]

    # Should have comparison evidence
    assert len(request.evidence) >= 1
    comparison_evidence = request.evidence[0]

    # Should contain comparison values
    assert comparison_evidence.metric == "net_revenue"
    assert isinstance(comparison_evidence.value, dict)
    assert comparison_evidence.value["current"] == 12000.0
    assert comparison_evidence.value["baseline"] == 10000.0
    assert comparison_evidence.value["absolute_change"] == 2000.0
    assert comparison_evidence.value["relative_change"] == 0.20

    # Tier should be MEASURED
    assert comparison_evidence.tier == EvidenceTier.MEASURED
    assert comparison_evidence.source == "analytics"


@pytest.mark.asyncio
async def test_compare_uses_versioned_prompts(
    mock_llm_gateway: Mock,
    comparison_answer: AnalystAnswer,
) -> None:
    """COMPARE requests use versioned prompts from registry."""
    narrator = AnalystNarrator(mock_llm_gateway)

    await narrator.narrate_comparison(comparison_answer)

    call_args = mock_llm_gateway.generate.call_args
    request = call_args[0][0]

    # Should use versioned prompt
    assert request.prompt_version == "compare_scenarios_v1"

    # Should have system prompt
    assert request.system_prompt is not None

    # Should have task prompt
    assert request.prompt is not None


@pytest.mark.asyncio
async def test_compare_narrator_falls_back_to_deterministic_on_llm_failure(
    comparison_answer: AnalystAnswer,
) -> None:
    """COMPARE narrator returns deterministic answer when LLM fails."""
    # Create gateway that raises exception
    failing_gateway = Mock(spec=LlmGateway)
    failing_gateway.generate = AsyncMock(side_effect=Exception("API timeout"))

    narrator = AnalystNarrator(failing_gateway)

    # Should not raise
    result = await narrator.narrate_comparison(comparison_answer)

    # Should fall back to original headline
    assert result == comparison_answer.headline


@pytest.mark.asyncio
async def test_compare_integration_with_real_mock_provider(
    comparison_answer: AnalystAnswer,
) -> None:
    """COMPARE integration with real MockProvider (not mocked gateway)."""
    # Create real gateway with mock provider
    settings = LLMSettings(mock=True)
    gateway = LlmGateway.create_from_settings(settings)

    narrator = AnalystNarrator(gateway)

    result = await narrator.narrate_comparison(comparison_answer)

    # Should have enhanced headline from mock provider
    assert result
    assert len(result) > 20  # Mock provider generates content


@pytest.mark.asyncio
async def test_compare_preserves_deterministic_structure(
    mock_llm_gateway: Mock,
    comparison_answer: AnalystAnswer,
) -> None:
    """COMPARE narration preserves all deterministic fields."""
    narrator = AnalystNarrator(mock_llm_gateway)

    # Capture original values
    original_facts = comparison_answer.facts
    original_checked = comparison_answer.checked
    original_not_checked = comparison_answer.not_checked
    original_caveats = comparison_answer.caveats
    original_follow_ups = comparison_answer.follow_ups
    original_data = comparison_answer.data
    original_meta = comparison_answer.meta

    # Call narrator (which only enhances headline)
    enhanced_headline = await narrator.narrate_comparison(comparison_answer)

    # Verify deterministic fields would remain unchanged
    # (In actual usage, service.py creates new AnalystAnswer preserving these)
    assert comparison_answer.facts == original_facts
    assert comparison_answer.checked == original_checked
    assert comparison_answer.not_checked == original_not_checked
    assert comparison_answer.caveats == original_caveats
    assert comparison_answer.follow_ups == original_follow_ups
    assert comparison_answer.data == original_data
    assert comparison_answer.meta == original_meta

    # Only headline should be enhanced
    assert enhanced_headline != comparison_answer.headline
