"""Compile a declared schema into the conform pipeline (SQL, executed by DuckDB).

Why SQL rather than row-by-row Python: at ten million rows a night, per-row
Python is not a pipeline, it is a queue. Pushing parse, reject routing,
dedup, and standardization into the engine keeps the work vectorized and
memory-bounded, and it keeps the *rules* declarative — the schema is the only
input, so what runs always matches what was reviewed.

The compiled query is a chain of CTEs, each one a stage from the ETL design:

    raw          read as text — no coercion yet, nothing lost
    normalized   trim, canonical nulls, case conformance          (§13)
    typed        TRY_CAST per column, reject reason computed      (§8, §11)
    deduped      natural-key resolution with a declared tiebreaker (§10)
    standardized business date in the source's timezone,           (§16)
                 money converted at the transaction-date rate      (§15)

Identifiers are validated when the schema loads (``SourceSchema.validate``),
so interpolating them here cannot inject: they are matched against
``^[a-z][a-z0-9_]{0,62}$`` before ever reaching this module. Literals coming
from data are parameterized.
"""

# ruff: noqa: S608 — SQL is composed from schema identifiers validated at
# load time (SourceSchema.validate) and from module constants; every value
# originating in data is bound as a parameter.

from ingestion.domain.schema import ColumnClass, ColumnSpec, DataType, SourceSchema

#: Reject reason codes. Stable strings: they are written into reject files,
#: surfaced in the UI, and counted in quality scores.
REASON_MISSING_KEY = "missing_business_key"
REASON_BAD_TYPE = "unparseable_type"
REASON_BAD_DATE = "unparseable_event_time"
REASON_MISSING_MEASURE = "missing_required_measure"
REASON_IMPOSSIBLE = "impossible_value"
REASON_BAD_ENUM = "unmapped_enum_value"
REASON_OUT_OF_WINDOW = "event_time_outside_window"
REASON_AMBIGUOUS_DUP = "ambiguous_duplicate"

UNKNOWN_MEMBER = "UNKNOWN"
"""Dimension fallback. Visible and reconcilable — never a silent blank."""


def _quote(identifier: str) -> str:
    return f'"{identifier}"'


def _literal(value: str) -> str:
    """Single-quoted SQL literal with escaping, for schema-derived constants."""
    escaped = value.replace("'", "''")
    return f"'{escaped}'"


def _normalize_expr(spec: ColumnSpec, sentinels: tuple[str, ...]) -> str:
    """Trim, strip control characters, canonicalize sentinel nulls, case-fold.

    Ordering matters and is fixed (ETL §13): trim before comparing against
    sentinels, or ``" NULL "`` survives as a literal string.
    """
    col = _quote(spec.name)
    expr = f"regexp_replace(trim(CAST({col} AS VARCHAR)), '[\\x00-\\x1F]', '', 'g')"

    sentinel_list = ", ".join(_literal(s) for s in sentinels if s)
    if sentinel_list:
        expr = f"NULLIF(NULLIF({expr}, ''), {sentinel_list.split(', ')[0]})"
        for sentinel in [_literal(s) for s in sentinels if s][1:]:
            expr = f"NULLIF({expr}, {sentinel})"
    else:
        expr = f"NULLIF({expr}, '')"

    if spec.normalize == "upper":
        expr = f"upper({expr})"
    elif spec.normalize == "lower":
        expr = f"lower({expr})"
    elif spec.normalize == "title":
        expr = f"initcap({expr})"

    return f"{expr} AS {col}"


def _cast_expr(spec: ColumnSpec) -> str:
    """TRY_CAST so a bad value yields NULL instead of aborting the batch.

    The NULL is then distinguishable from a genuinely absent value by
    comparing against the normalized text column, which is how
    ``unparseable_type`` is told apart from ``missing`` downstream.
    """
    col = _quote(spec.name)

    if spec.dtype in (DataType.DATE, DataType.TIMESTAMP) and spec.date_formats:
        formats = ", ".join(_literal(f) for f in spec.date_formats)
        # try_strptime returns TIMESTAMPTZ when given a format list. Cast to
        # a naive TIMESTAMP so the value means "UTC instant" (the session is
        # pinned to UTC by ingestion.core.duck) and the business-date
        # expression owns the conversion to store-local.
        parsed = f"CAST(try_strptime({col}, [{formats}]) AS {spec.sql_type})"
        return f"{parsed} AS {col}"

    return f"TRY_CAST({col} AS {spec.sql_type}) AS {col}"


def _reject_reason_expr(schema: SourceSchema) -> str:
    """One CASE deciding whether a row is usable, and why not if it is not.

    Order encodes severity: a row missing its key is unusable regardless of
    whatever else is wrong with it, so that check comes first and the more
    specific diagnoses follow.
    """
    branches: list[str] = []

    for spec in schema.columns_of_class(ColumnClass.BUSINESS_KEY):
        col = _quote(spec.name)
        branches.append(f"WHEN {col} IS NULL THEN {_literal(REASON_MISSING_KEY)}")

    event = _quote(schema.event_time_column)
    branches.append(
        f"WHEN {event} IS NULL AND _raw_{schema.event_time_column} IS NOT NULL "
        f"THEN {_literal(REASON_BAD_DATE)}"
    )
    branches.append(f"WHEN {event} IS NULL THEN {_literal(REASON_BAD_DATE)}")

    for spec in schema.columns:
        col = _quote(spec.name)
        raw = f"_raw_{spec.name}"

        # Present in the source but uncastable → a type problem, not absence.
        if spec.dtype is not DataType.STRING:
            branches.append(
                f"WHEN {col} IS NULL AND {raw} IS NOT NULL THEN {_literal(REASON_BAD_TYPE)}"
            )

        if spec.column_class is ColumnClass.MEASURE and spec.required:
            branches.append(f"WHEN {col} IS NULL THEN {_literal(REASON_MISSING_MEASURE)}")

        # Corruption screening only — impossible values, never merely unusual
        # ones. A $40k order might be Black Friday (ETL §11).
        if spec.min_value is not None:
            branches.append(f"WHEN {col} < {spec.min_value} THEN {_literal(REASON_IMPOSSIBLE)}")
        if spec.max_value is not None:
            branches.append(f"WHEN {col} > {spec.max_value} THEN {_literal(REASON_IMPOSSIBLE)}")

    return "CASE\n        " + "\n        ".join(branches) + "\n        ELSE NULL\n    END"


def _dedupe_order_by(schema: SourceSchema) -> str:
    """Which duplicate wins.

    With a declared tiebreaker (usually ``updated_at``), latest wins. Without
    one, ordering is stable but arbitrary — so the compiled query flags those
    groups instead of silently picking, and money-bearing rows get rejected
    rather than guessed (ETL §10).
    """
    if schema.dedupe_tiebreaker:
        return f"{_quote(schema.dedupe_tiebreaker)} DESC NULLS LAST"
    return f"{_quote(schema.event_time_column)} DESC NULLS LAST"


def build_conform_sql(
    schema: SourceSchema,
    *,
    source_relation: str,
    sentinels: tuple[str, ...] | None = None,
) -> str:
    """Return the full conform query over ``source_relation``.

    ``source_relation`` is a DuckDB relation expression — a ``read_csv(...)``
    call or a registered view. Every column arrives as VARCHAR; this query is
    what gives it types, meaning, and a verdict.
    """
    sentinels = sentinels or schema.sentinel_nulls
    keys = ", ".join(_quote(k) for k in schema.natural_key)

    normalized = ",\n        ".join(_normalize_expr(spec, sentinels) for spec in schema.columns)
    # Keep the normalized text alongside the cast value so the reason CASE can
    # distinguish "absent" from "present but uncastable".
    raw_passthrough = ",\n        ".join(
        f"{_quote(spec.name)} AS _raw_{spec.name}" for spec in schema.columns
    )
    casts = ",\n        ".join(_cast_expr(spec) for spec in schema.columns)
    raw_cols = ",\n        ".join(f"_raw_{spec.name}" for spec in schema.columns)
    final_cols = ",\n        ".join(_quote(spec.name) for spec in schema.columns)
    # Missing-value policy, applied per column class (ETL §9): dimensions fall
    # back to a visible UNKNOWN member so the row stays analyzable and the gap
    # stays reconcilable; measures and keys were already rejected upstream;
    # descriptors keep their honest NULL.
    output_cols = ",\n        ".join(
        (
            f"coalesce({_quote(spec.name)}, {_literal(UNKNOWN_MEMBER)}) AS {_quote(spec.name)}"
            if spec.column_class is ColumnClass.DIMENSION and spec.dtype is DataType.STRING
            else _quote(spec.name)
        )
        for spec in schema.columns
    )

    # Business date in the store's own timezone (ETL §16).
    #
    # Two conversions, both explicit. Source timestamps are naive UTC
    # instants, so the first `AT TIME ZONE 'UTC'` states what the wall-clock
    # value *means*, and the second re-expresses that instant in the store's
    # zone. Casting the result to DATE then yields the local business date:
    # a 23:30 UTC sale is 16:30 in Los Angeles and belongs to that same day,
    # while an 08:00 UTC sale belongs to the previous local day.
    #
    # Doing this in one step would silently inherit the session timezone,
    # making output depend on where the job ran.
    tz_expr = (
        f"coalesce({_quote(schema.timezone_column)}, {_literal(schema.default_timezone)})"
        if schema.timezone_column
        else _literal(schema.default_timezone)
    )
    event = _quote(schema.event_time_column)
    business_date = f"CAST(({event} AT TIME ZONE 'UTC') AT TIME ZONE {tz_expr} AS DATE)"

    return f"""
WITH raw AS (
    SELECT * FROM {source_relation}
),
normalized AS (
    SELECT
        {normalized}
    FROM raw
),
carried AS (
    SELECT
        {final_cols},
        {raw_passthrough}
    FROM normalized
),
typed AS (
    SELECT
        {casts},
        {raw_cols}
    FROM carried
),
judged AS (
    SELECT
        *,
        {_reject_reason_expr(schema)} AS _reject_reason
    FROM typed
),
valid AS (
    SELECT * FROM judged WHERE _reject_reason IS NULL
),
ranked AS (
    SELECT
        *,
        row_number() OVER (
            PARTITION BY {keys}
            ORDER BY {_dedupe_order_by(schema)}
        ) AS _dup_rank,
        count(*) OVER (PARTITION BY {keys}) AS _dup_count
    FROM valid
),
deduped AS (
    SELECT * FROM ranked WHERE _dup_rank = 1
),
standardized AS (
    SELECT
        {output_cols},
        {business_date} AS business_date,
        _dup_count - 1 AS _duplicates_collapsed
    FROM deduped
)
SELECT * FROM standardized
"""


def build_rejects_sql(schema: SourceSchema, *, source_relation: str) -> str:
    """Companion query returning the rows the conform query dropped.

    Rejects keep their original text values plus a machine-readable reason:
    they are *data* to be triaged, not log lines to be grepped (ETL §21, §23).
    """
    sentinels = schema.sentinel_nulls
    normalized = ",\n        ".join(_normalize_expr(spec, sentinels) for spec in schema.columns)
    raw_passthrough = ",\n        ".join(
        f"{_quote(spec.name)} AS _raw_{spec.name}" for spec in schema.columns
    )
    casts = ",\n        ".join(_cast_expr(spec) for spec in schema.columns)
    raw_cols = ",\n        ".join(f"_raw_{spec.name}" for spec in schema.columns)
    final_cols = ",\n        ".join(_quote(spec.name) for spec in schema.columns)

    return f"""
WITH raw AS (
    SELECT * FROM {source_relation}
),
normalized AS (
    SELECT {normalized} FROM raw
),
carried AS (
    SELECT {final_cols}, {raw_passthrough} FROM normalized
),
typed AS (
    SELECT {casts}, {raw_cols} FROM carried
),
judged AS (
    SELECT *, {_reject_reason_expr(schema)} AS _reject_reason FROM typed
)
SELECT {raw_cols}, _reject_reason
FROM judged
WHERE _reject_reason IS NOT NULL
"""
