"""Connector protocol.

Every connector implements the same four-step contract, and the framework owns
everything else — retries, checkpoints, manifests, metrics, reject routing.
That split is what makes a new source roughly two hundred lines of
source-specific logic instead of a new pipeline.

    discover(window)  → what is available for this window
    extract(plan)     → a DuckDB relation over the raw text
    (framework conforms, validates, lands, loads)
"""

from dataclasses import dataclass, field
from typing import Protocol

from ingestion.domain.manifest import SourceFile
from ingestion.domain.schema import SourceSchema
from ingestion.domain.window import Window


@dataclass(frozen=True, slots=True)
class ExtractionPlan:
    """What a connector found for one partition."""

    partition: str
    files: list[SourceFile] = field(default_factory=list)
    relation: str = ""
    """DuckDB relation expression over the raw source (e.g. a read_csv call)."""
    observed_columns: list[str] = field(default_factory=list)
    files_expected: int = 0
    warnings: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.files


class Connector(Protocol):
    """The interface the pipeline orchestrator programs against."""

    version: str
    schema: SourceSchema

    @property
    def name(self) -> str:
        """Human-readable identity for logs and retry labels."""
        ...

    def discover(self, window: Window) -> list[ExtractionPlan]:
        """Find source data for each partition in the window.

        Returns one plan per partition — including empty ones, so the caller
        can tell "nothing arrived" (a completeness problem) apart from "we
        never looked" (a scheduling problem).
        """
        ...
