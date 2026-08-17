"""Response validation — prevent hallucination and unsafe output."""

import pytest

from app.infrastructure.llm.models import EvidencePackage, EvidenceTier, LLMResponse
from app.infrastructure.llm.provider import LLMValidationError
from app.infrastructure.llm.validation import ResponseValidator


@pytest.fixture(scope="module")
def validator() -> ResponseValidator:
    return ResponseValidator()


@pytest.fixture
def sample_response() -> LLMResponse:
    """Sample successful LLM response."""
    return LLMResponse(
        content="Revenue increased by 15%",
        request_id="test-request-123",
        model_id="test-model",
        prompt_version="v1",
        tokens_in=100,
        tokens_out=50,
        estimated_cost_usd=0.01,
        latency_ms=500,
        status="success",
    )


@pytest.fixture
def sample_evidence() -> list[EvidencePackage]:
    """Sample evidence package."""
    return [
        EvidencePackage(
            metric="revenue",
            value=125000.0,
            period="2024-Q4",
            source="analytics",
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


# ── JSON validation ──────────────────────────────────────────────────


def test_validate_json_success(validator: ResponseValidator) -> None:
    response = LLMResponse(
        content='{"status": "ok", "value": 42}',
        request_id="test-123",
        model_id="test",
        prompt_version="v1",
        tokens_in=10,
        tokens_out=10,
        estimated_cost_usd=0.001,
        latency_ms=100,
        status="success",
    )

    data = validator.validate_json(response)
    assert data == {"status": "ok", "value": 42}


def test_validate_json_invalid_raises(validator: ResponseValidator) -> None:
    response = LLMResponse(
        content='{"invalid json missing closing brace',
        request_id="test-123",
        model_id="test",
        prompt_version="v1",
        tokens_in=10,
        tokens_out=10,
        estimated_cost_usd=0.001,
        latency_ms=100,
        status="success",
    )

    with pytest.raises(LLMValidationError, match="not valid JSON"):
        validator.validate_json(response)


def test_validate_json_empty_object(validator: ResponseValidator) -> None:
    response = LLMResponse(
        content="{}",
        request_id="test-123",
        model_id="test",
        prompt_version="v1",
        tokens_in=10,
        tokens_out=10,
        estimated_cost_usd=0.001,
        latency_ms=100,
        status="success",
    )

    data = validator.validate_json(response)
    assert data == {}


# ── Citation validation ──────────────────────────────────────────────


def test_validate_citations_valid(
    validator: ResponseValidator,
    sample_response: LLMResponse,
    sample_evidence: list[EvidencePackage],
) -> None:
    """Citations that reference actual evidence should pass."""
    response = LLMResponse(
        content="Revenue [source: analytics] increased to 125000 [metric: revenue]",
        request_id="test-123",
        model_id="test",
        prompt_version="v1",
        tokens_in=10,
        tokens_out=20,
        estimated_cost_usd=0.001,
        latency_ms=100,
        status="success",
    )

    # Should not raise
    assert validator.validate_citations(response, sample_evidence) is True


def test_validate_citations_no_citations(
    validator: ResponseValidator,
    sample_response: LLMResponse,
    sample_evidence: list[EvidencePackage],
) -> None:
    """Response without citations is valid."""
    assert validator.validate_citations(sample_response, sample_evidence) is True


def test_validate_citations_fabricated_warns(
    validator: ResponseValidator,
    sample_evidence: list[EvidencePackage],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Fabricated citations should log warning but not fail."""
    response = LLMResponse(
        content="Data from [source: nonexistent_source] shows trend",
        request_id="test-123",
        model_id="test",
        prompt_version="v1",
        tokens_in=10,
        tokens_out=15,
        estimated_cost_usd=0.001,
        latency_ms=100,
        status="success",
    )

    # Should not raise, just warn
    result = validator.validate_citations(response, sample_evidence)
    assert result is True


def test_validate_citations_empty_evidence(
    validator: ResponseValidator,
    sample_response: LLMResponse,
) -> None:
    """Empty evidence list should not cause errors."""
    assert validator.validate_citations(sample_response, []) is True


# ── Safe content validation ──────────────────────────────────────────


def test_validate_safe_content_clean(
    validator: ResponseValidator,
    sample_response: LLMResponse,
) -> None:
    """Clean business content should pass."""
    assert validator.validate_safe_content(sample_response) is True


def test_validate_safe_content_sql_injection_detected(
    validator: ResponseValidator,
) -> None:
    """SQL injection patterns should be detected and rejected."""
    dangerous_patterns = [
        "DROP TABLE users",
        "DELETE FROM orders",
        "TRUNCATE TABLE sales",
        "ALTER TABLE users ADD admin",
        "'; DROP TABLE--;",
    ]

    for pattern in dangerous_patterns:
        response = LLMResponse(
            content=f"Analysis shows: {pattern}",
            request_id="test-123",
            model_id="test",
            prompt_version="v1",
            tokens_in=10,
            tokens_out=20,
            estimated_cost_usd=0.001,
            latency_ms=100,
            status="success",
        )

        with pytest.raises(LLMValidationError, match="Unsafe SQL pattern"):
            validator.validate_safe_content(response)


def test_validate_safe_content_command_injection_detected(
    validator: ResponseValidator,
) -> None:
    """Command injection patterns should be detected and rejected."""
    dangerous_patterns = [
        "rm -rf /",
        "sudo rm -rf",
        "; bash -c 'malicious'",
        "| sh",
        "eval(malicious_code)",
        "exec(dangerous)",
    ]

    for pattern in dangerous_patterns:
        response = LLMResponse(
            content=f"Run this: {pattern}",
            request_id="test-123",
            model_id="test",
            prompt_version="v1",
            tokens_in=10,
            tokens_out=20,
            estimated_cost_usd=0.001,
            latency_ms=100,
            status="success",
        )

        with pytest.raises(LLMValidationError, match="Unsafe command pattern"):
            validator.validate_safe_content(response)


def test_validate_safe_content_case_insensitive(
    validator: ResponseValidator,
) -> None:
    """Unsafe patterns should be detected regardless of case."""
    response = LLMResponse(
        content="Analysis: DrOp TaBlE users",
        request_id="test-123",
        model_id="test",
        prompt_version="v1",
        tokens_in=10,
        tokens_out=20,
        estimated_cost_usd=0.001,
        latency_ms=100,
        status="success",
    )

    with pytest.raises(LLMValidationError, match="Unsafe SQL pattern"):
        validator.validate_safe_content(response)


# ── Unsupported numbers validation ───────────────────────────────────


def test_validate_no_unsupported_numbers_with_evidence(
    validator: ResponseValidator,
    sample_evidence: list[EvidencePackage],
) -> None:
    """Numbers that match evidence should pass."""
    response = LLMResponse(
        content="Revenue was 125000 with 1500 transactions",
        request_id="test-123",
        model_id="test",
        prompt_version="v1",
        tokens_in=10,
        tokens_out=20,
        estimated_cost_usd=0.001,
        latency_ms=100,
        status="success",
    )

    # Should not raise
    assert validator.validate_no_unsupported_numbers(response, sample_evidence) is True


def test_validate_no_unsupported_numbers_warns_on_mismatch(
    validator: ResponseValidator,
    sample_evidence: list[EvidencePackage],
) -> None:
    """Numbers not in evidence should log warning but not fail."""
    response = LLMResponse(
        content="Revenue was 999999 (not in evidence)",
        request_id="test-123",
        model_id="test",
        prompt_version="v1",
        tokens_in=10,
        tokens_out=20,
        estimated_cost_usd=0.001,
        latency_ms=100,
        status="success",
    )

    # Should not raise, just warn
    result = validator.validate_no_unsupported_numbers(response, sample_evidence)
    assert result is True


def test_validate_no_unsupported_numbers_ignores_small_numbers(
    validator: ResponseValidator,
    sample_evidence: list[EvidencePackage],
) -> None:
    """Small numbers (< 1000) should not trigger warnings."""
    response = LLMResponse(
        content="Q4 2024 analysis shows 42 stores",
        request_id="test-123",
        model_id="test",
        prompt_version="v1",
        tokens_in=10,
        tokens_out=20,
        estimated_cost_usd=0.001,
        latency_ms=100,
        status="success",
    )

    # Years and small counts should not warn
    result = validator.validate_no_unsupported_numbers(response, sample_evidence)
    assert result is True


# ── Integration: multiple validations ────────────────────────────────


def test_validate_all_checks_pass(
    validator: ResponseValidator,
    sample_evidence: list[EvidencePackage],
) -> None:
    """Response passing all validations."""
    response = LLMResponse(
        content="Revenue [source: analytics] was 125000 in Q4",
        request_id="test-123",
        model_id="test",
        prompt_version="v1",
        tokens_in=10,
        tokens_out=20,
        estimated_cost_usd=0.001,
        latency_ms=100,
        status="success",
    )

    # All validations should pass
    assert validator.validate_citations(response, sample_evidence) is True
    assert validator.validate_safe_content(response) is True
    assert validator.validate_no_unsupported_numbers(response, sample_evidence) is True
