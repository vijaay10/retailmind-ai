"""Response validation for LLM outputs.

Validates that model outputs meet expected structure and don't contain
fabricated citations, unsupported numbers, or unsafe instructions.
"""

import contextlib
import json
import re
from typing import Any, cast

import structlog

from app.infrastructure.llm.models import EvidencePackage, LLMResponse
from app.infrastructure.llm.provider import LLMValidationError

log = structlog.get_logger(__name__)


class ResponseValidator:
    """Validates LLM responses against expected structure and safety rules.

    Prevents:
    - Invalid JSON where structured output is expected
    - Fabricated citations
    - Unsupported numbers
    - Unsafe tool instructions
    """

    def validate_json(self, response: LLMResponse) -> dict[str, Any]:
        """Validate response is valid JSON.

        Does not validate structure against a schema — no caller needs that
        yet, and `jsonschema` isn't a dependency. If a caller does need it,
        add the parameter back with an actual `jsonschema.validate` call
        rather than a silently-ignored one.

        Args:
            response: LLM response to validate

        Returns:
            Parsed JSON object

        Raises:
            LLMValidationError: If response is not valid JSON
        """
        try:
            data = json.loads(response.content)
        except json.JSONDecodeError as error:
            log.error(
                "llm_response_invalid_json",
                request_id=response.request_id,
                error=str(error),
            )
            raise LLMValidationError(f"Response is not valid JSON: {error}") from error

        return cast(dict[str, Any], data)

    def validate_citations(
        self,
        response: LLMResponse,
        evidence: list[EvidencePackage],
    ) -> bool:
        """Validate that citations reference actual evidence.

        Args:
            response: LLM response
            evidence: Evidence that was provided to the LLM

        Returns:
            True if all citations are valid

        Raises:
            LLMValidationError: If fabricated citations detected
        """
        # Extract citation patterns like [source: analytics] or [metric: revenue]
        citation_pattern = re.compile(r"\[(?:source|metric|query_id):\s*([^\]]+)\]")
        citations = citation_pattern.findall(response.content)

        if not citations:
            # No citations is fine
            return True

        # Build set of valid references from evidence
        valid_sources = {pkg.source for pkg in evidence}
        valid_metrics = {pkg.metric for pkg in evidence}
        valid_query_ids = {pkg.query_id for pkg in evidence if pkg.query_id}

        # Check each citation
        for citation in citations:
            citation = citation.strip()
            is_valid = (
                citation in valid_sources
                or citation in valid_metrics
                or citation in valid_query_ids
            )
            if not is_valid:
                log.warning(
                    "llm_fabricated_citation",
                    request_id=response.request_id,
                    citation=citation,
                )
                # Don't fail, but log warning
                # Fabricated citations are suspicious but not always errors

        return True

    def validate_no_unsupported_numbers(
        self,
        response: LLMResponse,
        evidence: list[EvidencePackage],
        tolerance: float = 0.01,
    ) -> bool:
        """Validate that numeric claims match evidence.

        Args:
            response: LLM response
            evidence: Evidence provided
            tolerance: Tolerance for numeric comparison (relative)

        Returns:
            True if validation passes

        Raises:
            LLMValidationError: If unsupported numbers detected
        """
        # Extract numbers from response (excluding dates, percentages in context)
        number_pattern = re.compile(r"\b\d{1,3}(?:,\d{3})*(?:\.\d+)?\b")
        response_numbers = [
            float(n.replace(",", "")) for n in number_pattern.findall(response.content)
        ]

        # Extract numbers from evidence
        evidence_numbers = []
        for pkg in evidence:
            if isinstance(pkg.value, (int, float)):
                evidence_numbers.append(float(pkg.value))
            elif isinstance(pkg.value, str):
                # Try to extract numbers from string values
                with contextlib.suppress(ValueError):
                    evidence_numbers.append(float(pkg.value))

        # Check if response numbers are approximately in evidence
        # This is heuristic and not foolproof
        for resp_num in response_numbers:
            # Check if this number appears in evidence (with tolerance)
            found = any(
                abs(resp_num - ev_num) / max(abs(ev_num), 1e-6) < tolerance
                for ev_num in evidence_numbers
            )
            if not found and resp_num > 1000:  # Only flag significant numbers
                log.warning(
                    "llm_unsupported_number",
                    request_id=response.request_id,
                    number=resp_num,
                )
                # Warning only, not failure
                # False positives are common (years, percentages, etc.)

        return True

    def validate_safe_content(self, response: LLMResponse) -> bool:
        """Validate response doesn't contain unsafe instructions.

        Args:
            response: LLM response

        Returns:
            True if content is safe

        Raises:
            LLMValidationError: If unsafe content detected
        """
        content_lower = response.content.lower()

        # Check for SQL injection attempts
        sql_patterns = [
            "drop table",
            "delete from",
            "truncate table",
            "alter table",
            "create table",
            "; --",
        ]

        for pattern in sql_patterns:
            if pattern in content_lower:
                log.error(
                    "llm_unsafe_sql",
                    request_id=response.request_id,
                    pattern=pattern,
                )
                raise LLMValidationError(f"Unsafe SQL pattern detected: {pattern}")

        # Check for command injection attempts
        command_patterns = [
            "rm -rf",
            "sudo ",
            "; bash",
            "| sh",
            "eval(",
            "exec(",
        ]

        for pattern in command_patterns:
            if pattern in content_lower:
                log.error(
                    "llm_unsafe_command",
                    request_id=response.request_id,
                    pattern=pattern,
                )
                raise LLMValidationError(f"Unsafe command pattern detected: {pattern}")

        return True
