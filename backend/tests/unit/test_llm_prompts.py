"""Prompt registry — versioned prompts for reproducibility and audit."""

import pytest

from app.infrastructure.llm.prompts import PromptRegistry

# ── System prompts ───────────────────────────────────────────────────


def test_get_system_prompt_business_analyst() -> None:
    prompt = PromptRegistry.get_system_prompt("business_analyst_v1")
    assert "business intelligence analyst" in prompt.lower()
    assert "CRITICAL CONSTRAINTS" in prompt
    assert "evidence" in prompt.lower()


def test_get_system_prompt_investigation_assistant() -> None:
    prompt = PromptRegistry.get_system_prompt("investigation_assistant_v1")
    assert "investigation assistant" in prompt.lower()
    assert "CRITICAL CONSTRAINTS" in prompt


def test_get_system_prompt_unknown_version_raises() -> None:
    with pytest.raises(KeyError, match="Unknown system prompt version"):
        PromptRegistry.get_system_prompt("nonexistent_v99")


def test_system_prompts_contain_grounding_constraints() -> None:
    """All system prompts must enforce evidence-only constraint."""
    for version in ["business_analyst_v1", "investigation_assistant_v1"]:
        prompt = PromptRegistry.get_system_prompt(version)
        # Check for grounding constraints
        assert "ONLY reference numbers explicitly provided" in prompt or "may NOT" in prompt


# ── Task prompts ─────────────────────────────────────────────────────


def test_get_task_prompt_explain_metric() -> None:
    prompt = PromptRegistry.get_task_prompt(
        "explain_metric_v1",
        metric="revenue",
        dimension="store_id=42",
        evidence="Revenue: $125,000 (Q4 2024)",
    )
    assert "revenue" in prompt
    assert "store_id=42" in prompt
    assert "explain" in prompt.lower()


def test_get_task_prompt_suggest_investigation() -> None:
    prompt = PromptRegistry.get_task_prompt(
        "suggest_investigation_v1",
        topic="sudden revenue drop",
        evidence="Revenue down 15% vs prior quarter",
    )
    assert "sudden revenue drop" in prompt
    assert "investigating" in prompt.lower()


def test_get_task_prompt_summarize_rca() -> None:
    prompt = PromptRegistry.get_task_prompt(
        "summarize_rca_v1",
        evidence="Primary driver: Store closures in Region A",
    )
    assert "summarize" in prompt.lower() or "summary" in prompt.lower()
    assert "root cause" in prompt.lower()


def test_get_task_prompt_compare_scenarios() -> None:
    prompt = PromptRegistry.get_task_prompt(
        "compare_scenarios_v1",
        evidence="Scenario A: +12% revenue, Scenario B: +6% revenue",
    )
    assert "compare" in prompt.lower() or "scenario" in prompt.lower()


def test_get_task_prompt_unknown_version_raises() -> None:
    with pytest.raises(KeyError, match="Unknown task prompt version"):
        PromptRegistry.get_task_prompt("nonexistent_task_v99")


def test_get_task_prompt_missing_variable_raises() -> None:
    """Missing template variable should raise KeyError."""
    with pytest.raises(KeyError):
        PromptRegistry.get_task_prompt(
            "explain_metric_v1"
            # Missing required 'metric' and 'dimension' kwargs
        )


# ── Prompt versioning ────────────────────────────────────────────────


def test_list_system_prompt_versions() -> None:
    versions = PromptRegistry.list_system_prompt_versions()
    assert "business_analyst_v1" in versions
    assert "investigation_assistant_v1" in versions
    assert len(versions) >= 2


def test_list_task_prompt_versions() -> None:
    versions = PromptRegistry.list_task_prompt_versions()
    assert "explain_metric_v1" in versions
    assert "suggest_investigation_v1" in versions
    assert "summarize_rca_v1" in versions
    assert "compare_scenarios_v1" in versions
    assert len(versions) >= 4


def test_prompt_versions_follow_naming_convention() -> None:
    """All prompts should follow pattern: {name}_v{number}."""
    all_versions = (
        PromptRegistry.list_system_prompt_versions() + PromptRegistry.list_task_prompt_versions()
    )

    for version in all_versions:
        assert "_v" in version, f"Version {version} doesn't follow naming convention"
        # Extract version number
        version_suffix = version.split("_v")[-1]
        assert version_suffix.isdigit(), f"Version suffix '{version_suffix}' is not numeric"


# ── Prompt immutability ──────────────────────────────────────────────


def test_prompts_are_immutable() -> None:
    """Prompts should be class attributes, not modified at runtime."""
    # Get prompt twice
    first = PromptRegistry.get_system_prompt("business_analyst_v1")
    second = PromptRegistry.get_system_prompt("business_analyst_v1")

    # Should be identical
    assert first == second
    assert first is second  # Same object reference


# ── Real-world usage patterns ────────────────────────────────────────


def test_format_explain_metric_prompt_with_evidence() -> None:
    """Real-world example: format prompt with actual metric data."""
    prompt = PromptRegistry.get_task_prompt(
        "explain_metric_v1",
        metric="gross_margin",
        dimension="category=Electronics, region=North",
        evidence="Gross margin: 32.5% (Electronics, North region, Q4 2024)",
    )

    assert "gross_margin" in prompt
    assert "Electronics" in prompt
    assert "North" in prompt


def test_format_investigation_prompt_with_topic() -> None:
    """Real-world example: format investigation prompt."""
    prompt = PromptRegistry.get_task_prompt(
        "suggest_investigation_v1",
        topic="15% revenue decline in Q4",
        evidence="Revenue: $850,000 (Q4 2024) vs $1,000,000 (Q3 2024)",
    )

    assert "15% revenue decline" in prompt
    assert "Q4" in prompt
