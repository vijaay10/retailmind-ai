"""Suggest how an uploaded file's own column names map onto a canonical schema.

Once `detection.detect_dataset_type` has said *what* a file probably is (a
sales extract, an inventory snapshot, ...), this module answers the next
question: *which of my columns is which declared field?* The result is a
renaming plan a caller can apply to a DataFrame (or list of dicts) before
handing it to `validate.validate_mapped_dataset` or a connector.

Matching reuses `matching.match_score` — see that module for the scoring
rules (exact / synonym / fuzzy). The one thing this module adds is
**assignment**: naively taking each uploaded column's single best-scoring
canonical field can map two different uploaded columns onto the same
canonical field when their name overlaps. Instead every (uploaded column,
canonical field) pair with a positive score is a candidate, and candidates
are assigned greedily by descending score — highest-confidence pairs claim
their column and field first, so a field already claimed by a stronger match
is not stolen by a weaker one.
"""

from dataclasses import dataclass

from ingestion.domain.schema import SourceSchema

from .matching import CONFIDENT_THRESHOLD, match_score


@dataclass(frozen=True, slots=True)
class MappingSuggestion:
    source_column: str
    canonical_field: str | None
    confidence: float
    reason: str


def suggest_column_mapping(columns: list[str], schema: SourceSchema) -> list[MappingSuggestion]:
    """Suggest a canonical field for every uploaded column, one schema at a time.

    Returns one `MappingSuggestion` per entry in `columns`, in the same
    order. A column with no confident candidate gets ``canonical_field=None``
    and reason ``"no confident match"`` — the caller (a human review screen)
    decides what to do with it; this function never guesses past its
    confidence.
    """
    candidates: list[tuple[float, str, str, str]] = []
    for source_column in columns:
        for spec in schema.columns:
            score, reason = match_score(source_column, spec.name)
            if score > 0.0:
                candidates.append((score, source_column, spec.name, reason))

    # Highest-confidence pairs are assigned first, so a canonical field
    # already claimed by a strong match cannot be re-claimed by a weaker one.
    candidates.sort(key=lambda c: c[0], reverse=True)

    assigned: dict[str, tuple[str, float, str]] = {}
    used_fields: set[str] = set()
    for score, source_column, field_name, reason in candidates:
        if score < CONFIDENT_THRESHOLD:
            continue
        if source_column in assigned or field_name in used_fields:
            continue
        assigned[source_column] = (field_name, score, reason)
        used_fields.add(field_name)

    suggestions: list[MappingSuggestion] = []
    for source_column in columns:
        if source_column in assigned:
            field_name, score, reason = assigned[source_column]
            suggestions.append(
                MappingSuggestion(
                    source_column=source_column,
                    canonical_field=field_name,
                    confidence=round(score, 4),
                    reason=reason,
                )
            )
        else:
            suggestions.append(
                MappingSuggestion(
                    source_column=source_column,
                    canonical_field=None,
                    confidence=0.0,
                    reason="no confident match",
                )
            )
    return suggestions
