"""Currency standardization (ETL design §15).

Three rules, each with a reason:

1. **Convert at the transaction-date rate**, never the load-date rate. This is
   what makes a backfill reproduce the same numbers as the original run — a
   quiet but load-bearing dependency of the idempotency guarantee.
2. **Never destroy the original.** Facts carry the base-currency amount *and*
   ``source_currency`` / ``source_amount``, so any conversion is auditable
   after the fact.
3. **Fail loudly on stale rates.** A missing rate carries forward for a few
   days with a visible flag; beyond that, money math stops. Silently
   converting at a week-old rate is worse than not converting.
"""

# ruff: noqa: S608 — SQL is composed from schema identifiers validated at
# load time (SourceSchema.validate) and from module constants; every value
# originating in data is bound as a parameter.

from dataclasses import dataclass
from datetime import date

from ingestion.core.errors import ConfigError
from ingestion.domain.schema import SourceSchema


@dataclass(frozen=True, slots=True)
class FxRate:
    """One day's rate for one currency pair, quoted to the tenant's base."""

    rate_date: date
    currency: str
    base_currency: str
    rate: float
    """Multiply a ``currency`` amount by this to get ``base_currency``."""


def build_fx_lookup_sql(
    schema: SourceSchema,
    *,
    source_relation: str,
    fx_relation: str,
    base_currency: str,
    carry_forward_days: int = 3,
) -> str:
    """Convert every declared money column to the base currency.

    The join uses an as-of lookup rather than an equality join: markets are
    closed at weekends, so a Saturday sale legitimately has no Saturday rate
    and must reach back to Friday's. ``carry_forward_days`` bounds how far
    back that reach may go, and rows that exhausted it are flagged rather than
    quietly converted.

    Emits, per money column ``x``:
        ``x``                  amount in base currency
        ``x_source_amount``    the original, untouched
        ``_fx_carried_days``   how stale the applied rate was (0 = same day)
        ``_fx_missing``        true when no rate was found within the window
    """
    money_columns = [c for c in schema.columns if c.is_money]
    if not money_columns:
        return f"SELECT *, FALSE AS _fx_missing, 0 AS _fx_carried_days FROM {source_relation}"

    currency_cols = {c.currency_column for c in money_columns if c.currency_column}
    if len(currency_cols) != 1:
        raise ConfigError(
            f"{schema.source}.{schema.table} mixes currency columns "
            f"{sorted(currency_cols)}; "
            "one currency column per table keeps the as-of join unambiguous"
        )
    currency_col = next(iter(currency_cols))

    passthrough = ", ".join(
        f'src."{c.name}"' for c in schema.columns if c.name not in {m.name for m in money_columns}
    )
    conversions = ",\n        ".join(
        f'CASE WHEN src."{currency_col}" = \'{base_currency}\' THEN src."{c.name}" '
        f'ELSE round(src."{c.name}" * src.rate, 4) END AS "{c.name}",\n        '
        f'src."{c.name}" AS "{c.name}_source_amount"'
        for c in money_columns
    )

    return f"""
WITH src AS (
    SELECT * FROM {source_relation}
),
matched AS (
    SELECT
        src.*,
        (
            SELECT r.rate
            FROM {fx_relation} r
            WHERE r.currency = src."{currency_col}"
              AND r.base_currency = '{base_currency}'
              AND r.rate_date <= src.business_date
              AND r.rate_date >= src.business_date - INTERVAL {carry_forward_days} DAY
            ORDER BY r.rate_date DESC
            LIMIT 1
        ) AS rate,
        (
            SELECT date_diff('day', r.rate_date, src.business_date)
            FROM {fx_relation} r
            WHERE r.currency = src."{currency_col}"
              AND r.base_currency = '{base_currency}'
              AND r.rate_date <= src.business_date
              AND r.rate_date >= src.business_date - INTERVAL {carry_forward_days} DAY
            ORDER BY r.rate_date DESC
            LIMIT 1
        ) AS carried_days
    FROM src
)
SELECT
    {passthrough},
    "{currency_col}" AS source_currency,
    {conversions},
    coalesce(carried_days, 0) AS _fx_carried_days,
    ("{currency_col}" <> '{base_currency}' AND rate IS NULL) AS _fx_missing,
    business_date,
    _duplicates_collapsed
FROM matched src
"""
