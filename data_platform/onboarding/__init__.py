"""Tenant onboarding — map an arbitrary uploaded file onto the canonical schema.

Different retailers name the same concept differently: one calls it `sku`,
another `product_code`, a third `item_code`. The declared `SourceSchema`
contracts under `ingestion/schemas/` (`data_platform.ingestion.domain.schema`)
already define the canonical shape every pipeline downstream expects; this
package is the layer that gets an arbitrary upload *to* that shape, in three
steps that mirror the onboarding flow:

1. `detection.detect_dataset_type` — "what is this file?"
2. `mapping.suggest_column_mapping` — "which of my columns is which field?"
3. `validate.validate_mapped_dataset` — "is the data usable, once renamed?"

`matching` holds the column-name scoring both `detection` and `mapping`
share, so the alias table and fuzzy-match rules exist in exactly one place.
"""

from .detection import DetectionResult, detect_dataset_type
from .mapping import MappingSuggestion, suggest_column_mapping
from .validate import ValidationIssue, ValidationReport, validate_mapped_dataset

__all__ = [
    "DetectionResult",
    "MappingSuggestion",
    "ValidationIssue",
    "ValidationReport",
    "detect_dataset_type",
    "suggest_column_mapping",
    "validate_mapped_dataset",
]
