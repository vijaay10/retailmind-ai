"""Identify which declared source schema an uploaded file most likely is.

A tenant uploads a CSV with its own column names. Before anything can be
mapped or validated, RetailMind has to guess *what it is* — a sales extract,
an inventory snapshot, a product master file, and so on — from the header
row (and, optionally, a peek at the values) alone.

## Scoring formula

For every declared `SourceSchema` in the registry (`matching.load_known_schemas`,
which reads every ``ingestion/schemas/{source}/{table}.yml``):

1. Every declared column gets a **weight**:

   ``weight = base * required_multiplier``

   * ``base = 2.0`` for ``business_key`` and ``event_time`` columns — these
     define the grain and the join key, so their presence is the strongest
     signal of *what a file is*.
   * ``base = 1.0`` for everything else (``measure``, ``dimension``,
     ``descriptor``, ``enrichment``).
   * ``required_multiplier = 1.0`` if the column is required, ``0.15`` if it
     is optional. A missing optional column (e.g. ``discount_amount``,
     ``customer_email``) says almost nothing about whether a file matches —
     most real uploads omit optional fields — so it barely moves the score.

2. For each declared column, find the **best-matching uploaded column** via
   `matching.match_score` (checked against every uploaded column name; the
   highest score wins). A match counts only if it clears
   `matching.CONFIDENT_THRESHOLD`.

3. When `sample_rows` is supplied, a matched column's score is further
   scaled by how many of its *sample values* actually look like the
   declared dtype (see `_value_plausibility`) — a column named "date" whose
   values are all text is less convincing evidence than one whose values
   parse as dates. Without samples, this factor is a no-op (``1.0``).

4. ``confidence = sum(weight * score for matched columns) / sum(weight for
   all declared columns)`` — a weighted fraction of the schema's columns
   that were found, in ``[0.0, 1.0]``.

Results are sorted by confidence, descending. The formula is intentionally
simple and fully inspectable: nothing here is a trained model or a magic
constant pulled from nowhere — every weight and threshold is named and
justified above.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime

from ingestion.domain.schema import ColumnClass, ColumnSpec, DataType

from .matching import CONFIDENT_THRESHOLD, load_known_schemas, match_score

#: Business-key and event-time columns define the grain and the join key —
#: the strongest signal of what a file *is*. Everything else is corroborating.
_IDENTITY_CLASSES = frozenset({ColumnClass.BUSINESS_KEY, ColumnClass.EVENT_TIME})

_IDENTITY_WEIGHT = 2.0
_OTHER_WEIGHT = 1.0
_REQUIRED_MULTIPLIER = 1.0
_OPTIONAL_MULTIPLIER = 0.15

_DATE_LIKE = re.compile(r"^\d{1,4}[-/]\d{1,2}[-/]\d{1,4}([ T]\d{1,2}:\d{2}(:\d{2})?)?$")
_NUMBER_LIKE = re.compile(r"^-?\d+(\.\d+)?$")


@dataclass(frozen=True, slots=True)
class DetectionResult:
    source: str
    table: str
    confidence: float
    matched_columns: list[str] = field(default_factory=list)
    missing_required: list[str] = field(default_factory=list)


def _column_weight(column: ColumnSpec) -> float:
    base = _IDENTITY_WEIGHT if column.column_class in _IDENTITY_CLASSES else _OTHER_WEIGHT
    multiplier = _REQUIRED_MULTIPLIER if column.required else _OPTIONAL_MULTIPLIER
    return base * multiplier


def _value_plausibility(
    column_name: str, dtype: DataType, sample_rows: list[dict[str, object]]
) -> float:
    """How much a column's sample values corroborate its declared dtype.

    Returns a multiplier in ``[0.5, 1.0]``. ``1.0`` when there is no evidence
    either way (no non-null samples) or the dtype has no plausibility check
    (plain strings match anything). Never drops below ``0.5`` — a name match
    is still real evidence even when a handful of sample values look odd
    (e.g. a currency-formatted amount column with a stray "$").
    """
    values = [
        str(row[column_name]) for row in sample_rows if row.get(column_name) not in (None, "")
    ]
    if not values:
        return 1.0

    if dtype in (DataType.DATE, DataType.TIMESTAMP):
        checker = _looks_like_date
    elif dtype in (DataType.INTEGER, DataType.DECIMAL):
        checker = _looks_like_number
    else:
        return 1.0

    plausible = sum(1 for v in values if checker(v))
    return 0.5 + 0.5 * (plausible / len(values))


def _looks_like_date(value: str) -> bool:
    text = value.strip()
    if _DATE_LIKE.match(text):
        return True
    try:
        datetime.fromisoformat(text)
        return True
    except ValueError:
        return False


def _looks_like_number(value: str) -> bool:
    return bool(_NUMBER_LIKE.match(value.strip()))


def detect_dataset_type(
    columns: list[str], sample_rows: list[dict[str, object]] | None = None
) -> list[DetectionResult]:
    """Score every declared schema against an uploaded header (and optionally
    sample rows), sorted by confidence descending. See the module docstring
    for the scoring formula.
    """
    results: list[DetectionResult] = []

    for schema in load_known_schemas():
        matched_columns: list[str] = []
        missing_required: list[str] = []
        weighted_sum = 0.0
        weight_total = 0.0

        for column in schema.columns:
            weight = _column_weight(column)
            weight_total += weight

            best_score = 0.0
            best_source: str | None = None
            for source_column in columns:
                score, _reason = match_score(source_column, column.name)
                if score > best_score:
                    best_score, best_source = score, source_column

            if best_score > 0.0 and sample_rows and best_source is not None:
                best_score *= _value_plausibility(best_source, column.dtype, sample_rows)

            if best_score >= CONFIDENT_THRESHOLD:
                matched_columns.append(column.name)
                weighted_sum += weight * best_score
            elif column.required:
                missing_required.append(column.name)

        confidence = weighted_sum / weight_total if weight_total else 0.0
        results.append(
            DetectionResult(
                source=schema.source,
                table=schema.table,
                confidence=round(confidence, 4),
                matched_columns=sorted(matched_columns),
                missing_required=sorted(missing_required),
            )
        )

    results.sort(key=lambda r: r.confidence, reverse=True)
    return results
