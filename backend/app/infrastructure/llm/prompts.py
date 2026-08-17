"""Versioned prompt registry.

Each prompt is versioned and tracked. Changes to prompts create new versions
rather than modifying existing ones, preserving the audit trail.
"""

from typing import Any


class PromptRegistry:
    """Registry of versioned prompts for LLM interactions.

    Prompts are versioned explicitly. Each request records which prompt
    version was used for reproducibility and auditing.
    """

    # System prompts define the LLM's role and constraints
    SYSTEM_PROMPTS = {
        "business_analyst_v1": """You are a business intelligence analyst for RetailMind,
a retail analytics platform. Your role is to interpret data, explain patterns,
and answer business questions.

CRITICAL CONSTRAINTS:
- You may ONLY reference numbers explicitly provided in the evidence
- You may NOT invent, estimate, or extrapolate numbers
- If asked for a number not in the evidence, say "That information is not available"
- Always cite the evidence source when referencing data
- Be concise and business-focused
- Use British English spelling

Your outputs will be shown to retail executives and store managers.""",
        "investigation_assistant_v1": """You are an investigation assistant for RetailMind.
Your role is to help analysts explore data, suggest investigation paths,
and identify potential root causes.

CRITICAL CONSTRAINTS:
- You may ONLY reference numbers explicitly provided in the evidence
- You may NOT generate SQL, database queries, or access data directly
- You may suggest what to investigate, but not claim what the data says
- Focus on reasoning and methodology, not on inventing results
- If evidence is insufficient, suggest what additional evidence would help

Your outputs guide human analysts through investigation workflows.""",
    }

    # Task-specific prompts
    TASK_PROMPTS = {
        "explain_metric_v1": """Based on the provided evidence, explain what
is happening with {metric} for {dimension}.

Evidence provided:
{evidence}

Provide a concise explanation focusing on:
1. What the current state is
2. How it compares to the baseline or expectation
3. Potential contributing factors visible in the evidence

Only reference numbers explicitly shown in the evidence above.""",
        "suggest_investigation_v1": """A business user is investigating {topic}.

Current evidence available:
{evidence}

Based on this evidence, suggest 3-5 specific next steps they should take
to understand what's happening. Focus on:
- What additional data to review
- What comparisons to make
- What filters or segments to examine

Do not invent numbers or claim what the data will show.""",
        "summarize_rca_v1": """Summarize the following root cause analysis results
in plain business language.

RCA Results:
{evidence}

Provide:
1. A one-sentence summary of the primary driver
2. A brief explanation of the top 3 contributing factors
3. The confidence level and any limitations

Use business terminology, not technical jargon.""",
        "compare_scenarios_v1": """Compare the following scenarios:

{evidence}

Explain:
1. How they differ
2. Which scenario performs better on key metrics
3. What trade-offs exist between them

Only reference metrics explicitly provided in the evidence.""",
    }

    @classmethod
    def get_system_prompt(cls, version: str = "business_analyst_v1") -> str:
        """Get a system prompt by version.

        Args:
            version: Prompt version identifier

        Returns:
            System prompt text

        Raises:
            KeyError: If version doesn't exist
        """
        if version not in cls.SYSTEM_PROMPTS:
            raise KeyError(f"Unknown system prompt version: {version}")
        return cls.SYSTEM_PROMPTS[version]

    @classmethod
    def get_task_prompt(cls, version: str, **kwargs: Any) -> str:
        """Get a task prompt by version with variable substitution.

        Args:
            version: Prompt version identifier
            **kwargs: Variables to substitute into the prompt template

        Returns:
            Formatted prompt text

        Raises:
            KeyError: If version doesn't exist
        """
        if version not in cls.TASK_PROMPTS:
            raise KeyError(f"Unknown task prompt version: {version}")

        template = cls.TASK_PROMPTS[version]
        return template.format(**kwargs)

    @classmethod
    def list_versions(cls) -> dict[str, list[str]]:
        """List all available prompt versions.

        Returns:
            Dict with 'system' and 'task' keys listing versions
        """
        return {
            "system": list(cls.SYSTEM_PROMPTS.keys()),
            "task": list(cls.TASK_PROMPTS.keys()),
        }

    @classmethod
    def list_system_prompt_versions(cls) -> list[str]:
        """List all available system prompt versions.

        Returns:
            List of system prompt version identifiers
        """
        return list(cls.SYSTEM_PROMPTS.keys())

    @classmethod
    def list_task_prompt_versions(cls) -> list[str]:
        """List all available task prompt versions.

        Returns:
            List of task prompt version identifiers
        """
        return list(cls.TASK_PROMPTS.keys())
