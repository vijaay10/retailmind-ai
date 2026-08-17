"""Validate an already-mapped dataset against its declared `SourceSchema`.

Runs after `mapping.suggest_column_mapping` has been used to rename an
uploaded file's columns to canonical field names — this module assumes the
keys in `rows` already match `schema.column_names` and checks that the
*values* are usable: required fields present, dates parseable and sane,
measures numeric and within their declared bounds, and business keys not
repeated.

This is deliberately independent of `quality.gate.QualityGate` /
`quality.rules`. Those operate on `BatchStats` — row counts, a precomputed
duplicate rate, a precomputed reject rate — produced *after* the DuckDB-based
ETL pipeline has already parsed and cast a landed batch. There is no landed
batch here, just a raw list of uploaded dicts a tenant is previewing before
onboarding, so reshaping it into `BatchStats` would mean re-deriving the same
null/duplicate counts by hand anyway just to hand them back to the gate.
Rather than build a fake `BatchStats` to route through rules built for a
different stage, this module computes the equivalent checks directly against
the rows it is given. It mirrors the same policy choices `quality/rules.py`
documents — a missing business key is as serious as `BUSINESS_KEY_PRESENT`
treats it (an error here), a repeated business key is a warning exactly as
`DUPLICATE_RATE` treats it — but uses its own codes, because these are
pre-load, single-file checks, not the post-load, whole-batch rule ids in
`quality.rules.CATALOG`.
"""

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Literal

from ingestion.domain.schema import ColumnClass, ColumnSpec, DataType, SourceSchema

Severity = Literal["error", "warning"]

#: A file with a date from before electronic POS systems existed, or a
#: thousand years in the future, is a parsing fault, not real retail data.
_MIN_SANE_YEAR = 1990
_MAX_SANE_YEAR = 2100

#: Issues carry a handful of example identifiers, not the whole offending set.
_MAX_SAMPLES = 5


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    severity: Severity
    code: str
    message: str
    count: int
    sample_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ValidationReport:
    total_records: int
    valid_records: int
    valid_pct: float
    issues: list[ValidationIssue] = field(default_factory=list)


def _is_null(value: object, schema: SourceSchema) -> bool:
    if value is None:
        return True
    return isinstance(value, str) and value.strip() in schema.sentinel_nulls


def _row_identifier(row: dict[str, object], schema: SourceSchema, index: int) -> str:
    key_values = [str(row.get(k)) for k in schema.natural_key if not _is_null(row.get(k), schema)]
    return "/".join(key_values) if key_values else f"row {index}"


def _label(column_name: str) -> str:
    """Business-language rendering of a snake_case column for issue messages."""
    return column_name.replace("_", " ")


def _parse_date(value: object, spec: ColumnSpec) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    if not isinstance(value, str):
        return None

    text = value.strip()
    if spec.date_formats:
        for fmt in spec.date_formats:
            try:
                return datetime.strptime(text, fmt)
            except ValueError:
                continue
        # Formats were declared explicitly, so a fuzzy ISO fallback is not
        # attempted — schema.py's own design note: guessing between date
        # orderings is how an unauditable warehouse starts.
        return None

    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _parse_number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _outside_bounds(number: float, column: ColumnSpec) -> bool:
    below_min = column.min_value is not None and number < column.min_value
    above_max = column.max_value is not None and number > column.max_value
    return below_min or above_max


def validate_mapped_dataset(
    rows: list[dict[str, object]], schema: SourceSchema
) -> ValidationReport:
    """Run real, computed checks against a mapped dataset.

    Checks implemented:

    * required columns are not null / sentinel-null (error)
    * date/timestamp columns parse under the schema's declared formats (or
      ISO-8601 when none are declared) and fall in a sane year range (error)
    * measure columns are numeric, and within `ColumnSpec.min_value` /
      `max_value` when the schema declares bounds (error)
    * business-key (`natural_key`) values are not repeated (warning — see
      module docstring for why this is a warning, not an error)

    A record counts as valid iff it triggered none of the error-severity
    checks above; duplicate business keys (warning) do not by themselves
    invalidate a record.
    """
    total = len(rows)
    invalid_rows: set[int] = set()
    issues: list[ValidationIssue] = []

    def report(severity: Severity, code: str, message: str, indices: list[int]) -> None:
        if not indices:
            return
        if severity == "error":
            invalid_rows.update(indices)
        samples = [_row_identifier(rows[i], schema, i) for i in indices[:_MAX_SAMPLES]]
        issues.append(
            ValidationIssue(
                severity=severity,
                code=code,
                message=message,
                count=len(indices),
                sample_ids=samples,
            )
        )

    # ── Required columns present ──
    for column in schema.columns:
        if not column.required:
            continue
        missing = [i for i, row in enumerate(rows) if _is_null(row.get(column.name), schema)]
        report(
            "error",
            f"MISSING_REQUIRED_{column.name.upper()}",
            f"{len(missing)} records have a missing {_label(column.name)}",
            missing,
        )

    # ── Dates ──
    for column in schema.columns:
        if column.dtype not in (DataType.DATE, DataType.TIMESTAMP):
            continue
        bad: list[int] = []
        for i, row in enumerate(rows):
            value = row.get(column.name)
            if _is_null(value, schema):
                continue
            parsed = _parse_date(value, column)
            if parsed is None or not (_MIN_SANE_YEAR <= parsed.year <= _MAX_SANE_YEAR):
                bad.append(i)
        report(
            "error",
            f"INVALID_DATE_{column.name.upper()}",
            f"{len(bad)} records have an unparseable or out-of-range {_label(column.name)}",
            bad,
        )

    # ── Numeric measures ──
    for column in schema.columns:
        if column.column_class is not ColumnClass.MEASURE:
            continue
        non_numeric: list[int] = []
        out_of_bounds: list[int] = []
        for i, row in enumerate(rows):
            value = row.get(column.name)
            if _is_null(value, schema):
                continue
            number = _parse_number(value)
            if number is None:
                non_numeric.append(i)
                continue
            if _outside_bounds(number, column):
                out_of_bounds.append(i)
        report(
            "error",
            f"INVALID_NUMBER_{column.name.upper()}",
            f"{len(non_numeric)} records have a non-numeric {_label(column.name)}",
            non_numeric,
        )
        range_desc = f"{column.min_value}–{column.max_value}"
        out_of_range_message = (
            f"{len(out_of_bounds)} records have a {_label(column.name)} "
            f"outside the allowed range ({range_desc})"
        )
        report("error", f"OUT_OF_RANGE_{column.name.upper()}", out_of_range_message, out_of_bounds)

    # ── Duplicate business keys (warning — see module docstring) ──
    if schema.natural_key:
        key_groups: dict[tuple[object, ...], list[int]] = {}
        for i, row in enumerate(rows):
            key = tuple(row.get(k) for k in schema.natural_key)
            if any(_is_null(v, schema) for v in key):
                continue  # already reported as a missing-required-field issue
            key_groups.setdefault(key, []).append(i)
        duplicates = [i for indices in key_groups.values() if len(indices) > 1 for i in indices[1:]]
        report(
            "warning",
            "DUPLICATE_BUSINESS_KEY",
            f"{len(duplicates)} records repeat a business key "
            f"({', '.join(schema.natural_key)}) already seen in this file",
            duplicates,
        )

    valid_records = total - len(invalid_rows)
    valid_pct = round((valid_records / total) * 100, 2) if total else 0.0

    return ValidationReport(
        total_records=total,
        valid_records=valid_records,
        valid_pct=valid_pct,
        issues=issues,
    )
