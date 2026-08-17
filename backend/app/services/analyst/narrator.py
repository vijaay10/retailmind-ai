"""LLM-powered narration layer for analyst findings.

Converts verified analytical findings into natural language explanations using
the LLM gateway. The LLM receives only structured evidence packages — it
explains verified facts, it does not generate new numbers.

CRITICAL: The LLM is an explanation layer, not a source of truth. All numbers
must come from the analytical engines (warehouse, RCA, forecasting, etc).
"""

import structlog

from app.infrastructure.llm.gateway import LlmGateway
from app.infrastructure.llm.models import EvidencePackage, EvidenceTier, LLMRequest
from app.infrastructure.llm.prompts import PromptRegistry
from app.services.analyst.contracts import AnalystAnswer

log = structlog.get_logger(__name__)


class AnalystNarrator:
    """Enhances analyst findings with fluent LLM-generated narration.

    The narrator receives deterministic findings from the analytical engines
    and generates natural language explanations. It does NOT generate numbers
    or make analytical decisions.
    """

    def __init__(self, gateway: LlmGateway):
        self._gateway = gateway

    async def narrate_investigation(
        self,
        answer: AnalystAnswer,
        *,
        request_id: str | None = None,
    ) -> str:
        """Generate fluent narration for an RCA investigation.

        Args:
            answer: The analyst's deterministic answer with verified facts
            request_id: Optional request ID for tracking

        Returns:
            Natural language explanation of the findings
        """
        # Convert facts and inferences into evidence packages
        evidence = self._build_evidence(answer)

        if not evidence:
            log.info("narrator.skipped", reason="no_evidence")
            return answer.headline

        # Build prompt with evidence
        evidence_text = self._format_evidence(evidence)
        task_prompt = PromptRegistry.get_task_prompt(
            "summarize_rca_v1",
            evidence=evidence_text,
        )

        # Get system prompt
        system_prompt = PromptRegistry.get_system_prompt("business_analyst_v1")

        # Create LLM request
        if request_id:
            llm_request = LLMRequest(
                prompt=task_prompt,
                system_prompt=system_prompt,
                evidence=evidence,
                prompt_version="summarize_rca_v1",
                max_tokens=512,
                temperature=0.7,
                request_id=request_id,
            )
        else:
            llm_request = LLMRequest(
                prompt=task_prompt,
                system_prompt=system_prompt,
                evidence=evidence,
                prompt_version="summarize_rca_v1",
                max_tokens=512,
                temperature=0.7,
            )

        try:
            response = await self._gateway.generate(llm_request)

            if response.status == "success":
                log.info(
                    "narrator.success",
                    request_id=response.request_id,
                    tokens_total=response.tokens_in + response.tokens_out,
                    cost_usd=response.estimated_cost_usd,
                )
                return response.content
            else:
                log.warning(
                    "narrator.failed",
                    status=response.status,
                    error=response.error,
                )
                return answer.headline

        except Exception as error:
            log.error(
                "narrator.error",
                error=str(error),
                error_type=type(error).__name__,
            )
            # Graceful fallback to deterministic headline
            return answer.headline

    def _build_evidence(self, answer: AnalystAnswer) -> list[EvidencePackage]:
        """Convert analyst answer into evidence packages.

        Only facts and inferences with explicit values are converted.
        The LLM receives structured evidence, not raw text.
        """
        evidence: list[EvidencePackage] = []

        # Extract numeric facts from the answer
        payload = answer.data
        metric = payload.get("metric")
        if metric:
            # Add metric change as primary evidence
            relative_change = payload.get("relative_change")
            if relative_change is not None:
                evidence.append(
                    EvidencePackage(
                        metric=metric,
                        value=relative_change,
                        period=payload.get("period", "current"),
                        source="rca_engine",
                        tier=EvidenceTier.MEASURED,
                        confidence="high",
                    )
                )

        # Add findings as evidence
        findings = payload.get("findings", [])
        for finding in findings[:5]:  # Limit to top 5 findings
            evidence.append(
                EvidencePackage(
                    metric=finding.get("dimension", "unknown"),
                    value=finding.get("contribution_pct", 0.0),
                    period=payload.get("period", "current"),
                    dimension={"subject": finding.get("subject", "")},
                    source="rca_decomposition",
                    tier=(
                        EvidenceTier.MEASURED
                        if finding.get("evidence_tier") == "arithmetic"
                        else EvidenceTier.MODELLED
                    ),
                    confidence=finding.get("confidence", "medium"),
                    limitations=[finding.get("caveat", "")] if finding.get("caveat") else [],
                )
            )

        return evidence

    def _format_evidence(self, evidence: list[EvidencePackage]) -> str:
        """Format evidence packages as text for the prompt.

        This provides context to the LLM about what facts are available.
        """
        lines = []
        for pkg in evidence:
            if isinstance(pkg.value, (int, float)):
                lines.append(
                    f"- {pkg.metric}: {pkg.value:.2%} ({pkg.tier.value} evidence, "
                    f"{pkg.confidence} confidence)"
                )
            else:
                lines.append(
                    f"- {pkg.metric}: {pkg.value} ({pkg.tier.value} evidence, "
                    f"{pkg.confidence} confidence)"
                )

        return "\n".join(lines)

    async def narrate_comparison(
        self,
        answer: AnalystAnswer,
        *,
        request_id: str | None = None,
    ) -> str:
        """Generate fluent narration for a period comparison.

        Args:
            answer: The analyst's deterministic answer with comparison results
            request_id: Optional request ID for tracking

        Returns:
            Natural language explanation of the comparison
        """
        # Build evidence from comparison data
        data = answer.data
        evidence = []

        if "current_value" in data and "baseline_value" in data:
            evidence.append(
                EvidencePackage(
                    metric=data.get("metric", "metric"),
                    value={
                        "current": data["current_value"],
                        "baseline": data["baseline_value"],
                        "absolute_change": data.get("absolute_change", 0.0),
                        "relative_change": data.get("relative_change", 0.0),
                    },
                    period=f"{data.get('current_start')} to {data.get('current_end')}",
                    source="analytics",
                    tier=EvidenceTier.MEASURED,
                    confidence="high",
                )
            )

        if not evidence:
            return answer.headline

        evidence_text = "\n".join(
            f"- Current: {data['current_value']:,.0f}, "
            f"Baseline: {data['baseline_value']:,.0f}, "
            f"Change: {data.get('relative_change', 0.0):+.1%}"
        )

        task_prompt = PromptRegistry.get_task_prompt(
            "compare_scenarios_v1",
            evidence=evidence_text,
        )

        system_prompt = PromptRegistry.get_system_prompt("business_analyst_v1")

        if request_id:
            llm_request = LLMRequest(
                prompt=task_prompt,
                system_prompt=system_prompt,
                evidence=evidence,
                prompt_version="compare_scenarios_v1",
                max_tokens=256,
                temperature=0.7,
                request_id=request_id,
            )
        else:
            llm_request = LLMRequest(
                prompt=task_prompt,
                system_prompt=system_prompt,
                evidence=evidence,
                prompt_version="compare_scenarios_v1",
                max_tokens=256,
                temperature=0.7,
            )

        try:
            response = await self._gateway.generate(llm_request)
            if response.status == "success":
                return response.content
            else:
                return answer.headline
        except Exception:
            return answer.headline
