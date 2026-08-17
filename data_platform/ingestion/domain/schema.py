"""Declared source schemas — the contract every batch is measured against.

Every source has a versioned YAML schema (``ingestion/schemas/{source}/{table}.yml``)
declaring columns, types, nullability, semantic class, and PII flags. That file
is the single input to four things that would otherwise drift apart:

* **parsing** — which casts to attempt and what to do when they fail;
* **missing-value policy** — decided per column *class*, never ad hoc;
* **drift detection** — the fingerprint compared on every batch;
* **PII governance** — flagged columns cannot reach silver un-reviewed.

Changing a schema is a reviewed pull request. That review *is* the governance.
"""

import hashlib
import json
import re
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Self

import yaml

from ingestion.core.errors import ConfigError

#: SQL identifiers are built from these names, so they are validated on load
#: rather than trusted. Config is repo-controlled, but a typo that produces
#: invalid SQL should fail at parse time with a clear message.
_IDENTIFIER_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")


class ColumnClass(StrEnum):
    """Semantic role — decides the missing-value policy (ETL)."""

    BUSINESS_KEY = "business_key"
    """Unjoinable without it: a missing value makes the row unusable → reject."""

    MEASURE = "measure"
    """Money or quantity. Never imputed — a guessed number is a lie in a ledger."""

    DIMENSION = "dimension"
    """Categorical attribute. Missing → 'UNKNOWN', visible and reconcilable."""

    DESCRIPTOR = "descriptor"
    """Optional detail. Missing is honest; only the null *rate* is monitored."""

    EVENT_TIME = "event_time"
    """Defines the grain. Missing → reject; the row has no place in a window."""

    ENRICHMENT = "enrichment"
    """External additions (weather, FX). Never load-bearing, never blocking."""


class DataType(StrEnum):
    STRING = "string"
    INTEGER = "integer"
    DECIMAL = "decimal"
    BOOLEAN = "boolean"
    DATE = "date"
    TIMESTAMP = "timestamp"


#: Declared type → the DuckDB type used for TRY_CAST during parsing.
_DUCKDB_TYPES: dict[DataType, str] = {
    DataType.STRING: "VARCHAR",
    DataType.INTEGER: "BIGINT",
    DataType.DECIMAL: "DECIMAL(18,4)",
    DataType.BOOLEAN: "BOOLEAN",
    DataType.DATE: "DATE",
    # Naive on purpose: source timestamps are UTC instants, and the
    # business-date expression converts to store-local explicitly. Letting
    # the session timezone decide would make output depend on where the
    # job ran, which is the opposite of reproducible.
    DataType.TIMESTAMP: "TIMESTAMP",
}


@dataclass(frozen=True, slots=True)
class ColumnSpec:
    name: str
    dtype: DataType
    column_class: ColumnClass
    required: bool = True
    pii: bool = False
    enum_values: tuple[str, ...] | None = None
    min_value: float | None = None
    max_value: float | None = None
    normalize: str | None = None
    """``upper`` | ``lower`` | ``title`` — case conformance (ETL step 3)."""
    date_formats: tuple[str, ...] = ()
    """Accepted input formats, tried in order. Empty means ISO-8601 only.

    Enumerating formats is deliberate: fuzzy date parsing silently guesses
    between ``03/04`` as March 4th and April 3rd, and a warehouse that guesses
    is a warehouse nobody can audit (ETL).
    """
    currency_column: str | None = None
    """For money columns: which column carries the ISO currency code."""

    @property
    def sql_type(self) -> str:
        return _DUCKDB_TYPES[self.dtype]

    @property
    def is_money(self) -> bool:
        return self.currency_column is not None


@dataclass(frozen=True, slots=True)
class SourceSchema:
    """A versioned contract for one source table."""

    source: str
    table: str
    version: str
    columns: tuple[ColumnSpec, ...]
    natural_key: tuple[str, ...]
    event_time_column: str
    timezone_column: str | None = None
    """Column holding the IANA zone for business-date derivation. When absent,
    ``default_timezone`` applies to every row."""
    default_timezone: str = "UTC"
    dedupe_tiebreaker: str | None = None
    """Column whose highest value wins when the natural key repeats. Without
    one, natural-key duplicates on money-bearing rows are rejected rather than
    resolved arbitrarily (ETL)."""
    sentinel_nulls: tuple[str, ...] = ("", "NULL", "null", "N/A", "n/a", "-", "\\N")
    metadata: dict[str, Any] = field(default_factory=dict)

    # ── Lookups ──────────────────────────────────────────────────────

    def column(self, name: str) -> ColumnSpec:
        for spec in self.columns:
            if spec.name == name:
                return spec
        raise ConfigError(f"column '{name}' is not declared in {self.source}.{self.table}")

    @property
    def column_names(self) -> tuple[str, ...]:
        return tuple(c.name for c in self.columns)

    @property
    def pii_columns(self) -> tuple[str, ...]:
        return tuple(c.name for c in self.columns if c.pii)

    def columns_of_class(self, column_class: ColumnClass) -> tuple[ColumnSpec, ...]:
        return tuple(c for c in self.columns if c.column_class is column_class)

    @property
    def fingerprint(self) -> str:
        """Stable digest of the declared shape (ETL.1 schema check).

        Column *order* is excluded: reordering is noise, not drift.
        """
        payload = sorted(
            ({"name": c.name, "type": c.dtype.value, "required": c.required} for c in self.columns),
            key=lambda entry: entry["name"],
        )
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()[:16]

    # ── Loading ──────────────────────────────────────────────────────

    @classmethod
    def from_yaml(cls, path: Path) -> Self:
        try:
            raw = yaml.safe_load(path.read_text())
        except (OSError, yaml.YAMLError) as exc:
            raise ConfigError(f"cannot read schema {path}: {exc}") from exc
        return cls.from_dict(raw)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Self:
        try:
            columns = tuple(
                ColumnSpec(
                    name=col["name"],
                    dtype=DataType(col["type"]),
                    column_class=ColumnClass(col["class"]),
                    required=col.get("required", True),
                    pii=col.get("pii", False),
                    enum_values=tuple(col["enum"]) if col.get("enum") else None,
                    min_value=col.get("min"),
                    max_value=col.get("max"),
                    normalize=col.get("normalize"),
                    date_formats=tuple(col.get("formats", ())),
                    currency_column=col.get("currency_column"),
                )
                for col in raw["columns"]
            )
            schema = cls(
                source=raw["source"],
                table=raw["table"],
                version=str(raw["version"]),
                columns=columns,
                natural_key=tuple(raw["natural_key"]),
                event_time_column=raw["event_time_column"],
                timezone_column=raw.get("timezone_column"),
                default_timezone=raw.get("default_timezone", "UTC"),
                dedupe_tiebreaker=raw.get("dedupe_tiebreaker"),
                metadata=raw.get("metadata", {}),
            )
        except (KeyError, ValueError) as exc:
            raise ConfigError(f"invalid schema definition: {exc}") from exc

        schema.validate()
        return schema

    def validate(self) -> None:
        """Fail at load time on anything that would produce broken SQL later."""
        for identifier in (self.source, self.table, *self.column_names):
            if not _IDENTIFIER_RE.match(identifier):
                raise ConfigError(f"'{identifier}' is not a valid snake_case identifier")

        declared = set(self.column_names)
        if len(declared) != len(self.columns):
            raise ConfigError(f"{self.source}.{self.table} declares duplicate columns")

        for name in (*self.natural_key, self.event_time_column):
            if name not in declared:
                raise ConfigError(f"'{name}' referenced but not declared")

        if self.dedupe_tiebreaker and self.dedupe_tiebreaker not in declared:
            raise ConfigError(f"tiebreaker '{self.dedupe_tiebreaker}' is not a declared column")

        for spec in self.columns:
            if spec.currency_column and spec.currency_column not in declared:
                raise ConfigError(
                    f"'{spec.name}' references undeclared currency column '{spec.currency_column}'"
                )


# ── Drift detection (ETL) ────────────────────────────────────────


class DriftKind(StrEnum):
    NEW_COLUMN = "new_column"
    MISSING_COLUMN = "missing_column"
    NONE = "none"


@dataclass(frozen=True, slots=True)
class DriftFinding:
    kind: DriftKind
    column: str
    blocking: bool
    detail: str


def detect_drift(schema: SourceSchema, observed_columns: list[str]) -> list[DriftFinding]:
    """Compare an arriving file's header against the declared contract.

    The asymmetry is the whole point:

    * a **missing** column is the upstream breaking its contract → blocking;
    * a **new** column is landed (bronze is as-received) but stays invisible
      downstream until someone declares it → warning.

    That second rule is also the PII safety valve: an unreviewed column cannot
    leak into silver, because staging selects declared columns explicitly.
    Case and ordering differences are absorbed silently — they are noise.
    """
    declared = {name.lower() for name in schema.column_names}
    observed = {name.strip().lower() for name in observed_columns}

    findings = [
        DriftFinding(
            kind=DriftKind.MISSING_COLUMN,
            column=name,
            blocking=True,
            detail="declared column absent from source file",
        )
        for name in sorted(declared - observed)
    ]
    findings += [
        DriftFinding(
            kind=DriftKind.NEW_COLUMN,
            column=name,
            blocking=False,
            detail="undeclared column landed to bronze; declare it to use downstream",
        )
        for name in sorted(observed - declared)
    ]
    return findings
