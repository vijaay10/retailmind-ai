"""LLM-powered narration for analyst findings.

Tests verify that the narrator converts verified analytical findings into
natural language explanations without generating new numbers. The narrator
receives only structured evidence and fails gracefully when the LLM is
unavailable.
"""

from unittest.mock import AsyncMock, Mock

import pytest

from app.infrastructure.llm.gateway import LlmGateway
from app.infrastructure.llm.models import EvidencePackage, EvidenceTier, LLMResponse
from app.services.analyst.contracts import AnalystAnswer, Capability
from app.services.analyst.narrator import AnalystNarrator


@pytest.fixture
def mock_gateway() -> Mock:
    """Mock LLM gateway."""
    gateway = Mock(spec=LlmGateway)
    gateway.generate = AsyncMock(
        return_value=LLMResponse(
            request_id="test-123",
            content="Revenue decreased 15% in Q4 primarily due to reduced customer orders.",
            status="success",
            model_id="mock-model",
            prompt_version="summarize_rca_v1",
            tokens_in=100,
            tokens_out=50,
            estimated_cost_usd=0.001,
            latency_ms=100,
        )
    )
    return gateway


@pytest.fixture
def narrator(mock_gateway: Mock) -> AnalystNarrator:
    """Narrator with mock gateway."""
    return AnalystNarrator(mock_gateway)


@pytest.fixture
def rca_answer() -> AnalystAnswer:
    """Sample RCA investigation answer."""
    return AnalystAnswer(
        question="Why did revenue drop?",
        capability=Capability.INVESTIGATE,
        headline="Revenue fell 15%. Orders down 20%.",
        data={
            "metric": "revenue",
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


# ── Evidence package construction ───────────────────────────────────


def test_narrator_builds_evidence_from_rca_findings(
    narrator: AnalystNarrator,
    rca_answer: AnalystAnswer,
) -> None:
    """Narrator converts RCA findings into structured evidence packages."""
    evidence = narrator._build_evidence(rca_answer)

    # Should have primary metric change + findings
    assert len(evidence) >= 2

    # Primary metric evidence
    primary = evidence[0]
    assert primary.metric == "revenue"
    assert primary.value == -0.15
    assert primary.tier == EvidenceTier.MEASURED
    assert primary.source == "rca_engine"

    # Finding evidence
    finding = evidence[1]
    assert finding.metric == "orders"
    assert finding.value == 0.80
    assert finding.tier == EvidenceTier.MEASURED
    assert finding.confidence == "high"


def test_narrator_limits_findings_to_top_five(
    narrator: AnalystNarrator,
) -> None:
    """Narrator limits evidence to top 5 findings to keep prompts focused."""
    answer = AnalystAnswer(
        question="test",
        capability=Capability.INVESTIGATE,
        headline="test",
        data={
            "metric": "revenue",
            "relative_change": -0.10,
            "findings": [{"dimension": f"dim_{i}", "contribution_pct": 0.1} for i in range(10)],
        },
    )

    evidence = narrator._build_evidence(answer)

    # 1 primary metric + 5 findings = 6 total
    assert len(evidence) == 6


def test_narrator_includes_caveats_in_limitations(
    narrator: AnalystNarrator,
    rca_answer: AnalystAnswer,
) -> None:
    """Narrator preserves caveats as evidence limitations."""
    evidence = narrator._build_evidence(rca_answer)

    # Second finding has caveat
    finding_with_caveat = evidence[2]
    assert finding_with_caveat.limitations
    assert "Small sample size" in finding_with_caveat.limitations[0]


def test_narrator_maps_evidence_tiers_correctly(
    narrator: AnalystNarrator,
) -> None:
    """Narrator maps arithmetic evidence to MEASURED, others to MODELLED."""
    answer = AnalystAnswer(
        question="test",
        capability=Capability.INVESTIGATE,
        headline="test",
        data={
            "metric": "revenue",
            "relative_change": -0.10,
            "findings": [
                {"dimension": "a", "contribution_pct": 0.5, "evidence_tier": "arithmetic"},
                {"dimension": "b", "contribution_pct": 0.3, "evidence_tier": "decomposition"},
            ],
        },
    )

    evidence = narrator._build_evidence(answer)

    # First is primary metric (MEASURED)
    assert evidence[0].tier == EvidenceTier.MEASURED

    # Second finding should be MEASURED (arithmetic)
    assert evidence[1].tier == EvidenceTier.MEASURED

    # Third finding should be MODELLED (decomposition)
    assert evidence[2].tier == EvidenceTier.MODELLED


def test_narrator_handles_empty_findings(
    narrator: AnalystNarrator,
) -> None:
    """Narrator handles answers with no findings gracefully."""
    answer = AnalystAnswer(
        question="test",
        capability=Capability.INVESTIGATE,
        headline="test",
        data={"metric": "revenue", "relative_change": -0.05, "findings": []},
    )

    evidence = narrator._build_evidence(answer)

    # Should still have primary metric
    assert len(evidence) == 1
    assert evidence[0].metric == "revenue"


# ── Narration generation ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_narrator_generates_enhanced_headline(
    narrator: AnalystNarrator,
    mock_gateway: Mock,
    rca_answer: AnalystAnswer,
) -> None:
    """Narrator calls LLM gateway with evidence and returns enhanced headline."""
    result = await narrator.narrate_investigation(rca_answer)

    # Should have called gateway
    mock_gateway.generate.assert_called_once()

    # Should return LLM-generated content
    assert result == "Revenue decreased 15% in Q4 primarily due to reduced customer orders."


@pytest.mark.asyncio
async def test_narrator_uses_correct_prompts(
    narrator: AnalystNarrator,
    mock_gateway: Mock,
    rca_answer: AnalystAnswer,
) -> None:
    """Narrator uses versioned prompts from registry."""
    await narrator.narrate_investigation(rca_answer)

    # Check the request passed to gateway
    call_args = mock_gateway.generate.call_args
    request = call_args[0][0]

    assert request.prompt_version == "summarize_rca_v1"
    assert request.system_prompt is not None
    prompt_lower = request.system_prompt.lower()
    assert "business" in prompt_lower and "analyst" in prompt_lower


@pytest.mark.asyncio
async def test_narrator_passes_evidence_to_gateway(
    narrator: AnalystNarrator,
    mock_gateway: Mock,
    rca_answer: AnalystAnswer,
) -> None:
    """Narrator passes structured evidence to LLM gateway."""
    await narrator.narrate_investigation(rca_answer)

    call_args = mock_gateway.generate.call_args
    request = call_args[0][0]

    # Should have evidence attached
    assert request.evidence is not None
    assert len(request.evidence) >= 2

    # Evidence should be EvidencePackage instances
    assert all(isinstance(pkg, EvidencePackage) for pkg in request.evidence)


@pytest.mark.asyncio
async def test_narrator_includes_request_id_if_provided(
    narrator: AnalystNarrator,
    mock_gateway: Mock,
    rca_answer: AnalystAnswer,
) -> None:
    """Narrator forwards request_id for tracking."""
    await narrator.narrate_investigation(rca_answer, request_id="custom-id-123")

    call_args = mock_gateway.generate.call_args
    request = call_args[0][0]

    assert request.request_id == "custom-id-123"


# ── Graceful fallback ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_narrator_falls_back_on_llm_failure(
    narrator: AnalystNarrator,
    mock_gateway: Mock,
    rca_answer: AnalystAnswer,
) -> None:
    """Narrator returns deterministic headline when LLM fails."""
    # Gateway raises exception
    mock_gateway.generate = AsyncMock(side_effect=Exception("API timeout"))

    result = await narrator.narrate_investigation(rca_answer)

    # Should fall back to original headline
    assert result == rca_answer.headline


@pytest.mark.asyncio
async def test_narrator_falls_back_on_llm_error_status(
    narrator: AnalystNarrator,
    mock_gateway: Mock,
    rca_answer: AnalystAnswer,
) -> None:
    """Narrator falls back when LLM returns error status."""
    # Gateway returns error response
    mock_gateway.generate = AsyncMock(
        return_value=LLMResponse(
            request_id="test-123",
            content="",
            status="error",
            error="Rate limit exceeded",
            model_id="mock-model",
            prompt_version="summarize_rca_v1",
            tokens_in=0,
            tokens_out=0,
            estimated_cost_usd=0.0,
            latency_ms=10,
        )
    )

    result = await narrator.narrate_investigation(rca_answer)

    # Should fall back to original headline
    assert result == rca_answer.headline


@pytest.mark.asyncio
async def test_narrator_falls_back_on_empty_evidence(
    narrator: AnalystNarrator,
    mock_gateway: Mock,
) -> None:
    """Narrator skips LLM call if no evidence available."""
    answer = AnalystAnswer(
        question="test",
        capability=Capability.INVESTIGATE,
        headline="No data available",
        data={},  # No metric, no findings
    )

    result = await narrator.narrate_investigation(answer)

    # Should not call gateway
    mock_gateway.generate.assert_not_called()

    # Should return original headline
    assert result == "No data available"


# ── Comparison narration ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_narrator_handles_period_comparison(
    narrator: AnalystNarrator,
    mock_gateway: Mock,
) -> None:
    """Narrator generates narration for period comparisons."""
    mock_gateway.generate = AsyncMock(
        return_value=LLMResponse(
            request_id="test-123",
            content="Revenue increased from 10,000 to 12,000, a 20% gain.",
            status="success",
            model_id="mock-model",
            prompt_version="compare_scenarios_v1",
            tokens_in=50,
            tokens_out=30,
            estimated_cost_usd=0.0005,
            latency_ms=80,
        )
    )

    answer = AnalystAnswer(
        question="Compare this month vs last",
        capability=Capability.COMPARE,
        headline="Revenue up 20%",
        data={
            "metric": "revenue",
            "current_value": 12000,
            "baseline_value": 10000,
            "relative_change": 0.20,
            "current_start": "2024-11-01",
            "current_end": "2024-11-30",
        },
    )

    result = await narrator.narrate_comparison(answer)

    # Should call gateway with comparison prompt
    mock_gateway.generate.assert_called_once()
    call_args = mock_gateway.generate.call_args
    request = call_args[0][0]
    assert request.prompt_version == "compare_scenarios_v1"

    # Should return enhanced narration
    assert result == "Revenue increased from 10,000 to 12,000, a 20% gain."


@pytest.mark.asyncio
async def test_narrator_comparison_falls_back_gracefully(
    narrator: AnalystNarrator,
    mock_gateway: Mock,
) -> None:
    """Narrator falls back on comparison failure."""
    mock_gateway.generate = AsyncMock(side_effect=Exception("Network error"))

    answer = AnalystAnswer(
        question="Compare periods",
        capability=Capability.COMPARE,
        headline="Revenue up 10%",
        data={
            "metric": "revenue",
            "current_value": 11000,
            "baseline_value": 10000,
        },
    )

    result = await narrator.narrate_comparison(answer)

    # Should fall back to original headline
    assert result == "Revenue up 10%"


# ── Evidence formatting ──────────────────────────────────────────────


def test_narrator_formats_numeric_evidence_as_percentage(
    narrator: AnalystNarrator,
) -> None:
    """Narrator formats numeric values as percentages in evidence text."""
    evidence = [
        EvidencePackage(
            metric="revenue_change",
            value=-0.15,
            period="Q4",
            source="analytics",
            tier=EvidenceTier.MEASURED,
            confidence="high",
        )
    ]

    text = narrator._format_evidence(evidence)

    # Should format as percentage
    assert "-15.00%" in text
    assert "measured evidence" in text
    assert "high confidence" in text


def test_narrator_formats_non_numeric_evidence_as_string(
    narrator: AnalystNarrator,
) -> None:
    """Narrator formats non-numeric values as strings."""
    evidence = [
        EvidencePackage(
            metric="status",
            value="declining",
            period="Q4",
            source="analysis",
            tier=EvidenceTier.ASSUMED,
            confidence="medium",
        )
    ]

    text = narrator._format_evidence(evidence)

    # Should format as string
    assert "declining" in text
    assert "assumed evidence" in text
    assert "medium confidence" in text
