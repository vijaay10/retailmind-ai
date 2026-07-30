"""Schema registry: loading, validation, fingerprinting, drift detection."""

import pytest
import yaml

from ingestion.core.errors import ConfigError
from ingestion.domain.schema import (
    ColumnClass,
    DataType,
    DriftKind,
    SourceSchema,
    detect_drift,
)

MINIMAL = {
    "source": "pos",
    "table": "sales",
    "version": "1.0",
    "natural_key": ["order_id"],
    "event_time_column": "ts",
    "columns": [
        {"name": "order_id", "type": "string", "class": "business_key"},
        {"name": "ts", "type": "timestamp", "class": "event_time"},
        {"name": "amount", "type": "decimal", "class": "measure"},
    ],
}


def _schema(**overrides: object) -> SourceSchema:
    return SourceSchema.from_dict({**MINIMAL, **overrides})


def test_loads_a_valid_schema() -> None:
    schema = _schema()
    assert schema.source == "pos"
    assert schema.column("amount").dtype is DataType.DECIMAL
    assert schema.column("order_id").column_class is ColumnClass.BUSINESS_KEY


def test_rejects_unknown_natural_key() -> None:
    with pytest.raises(ConfigError, match="not declared"):
        _schema(natural_key=["nope"])


def test_rejects_unsafe_identifiers() -> None:
    """Identifiers become SQL; validation happens at load, not at query time."""
    with pytest.raises(ConfigError, match="snake_case"):
        SourceSchema.from_dict(
            {
                **MINIMAL,
                "columns": [
                    *MINIMAL["columns"],
                    {"name": "drop table", "type": "string", "class": "descriptor"},
                ],
            }
        )


def test_rejects_duplicate_columns() -> None:
    with pytest.raises(ConfigError, match="duplicate"):
        _schema(columns=[*MINIMAL["columns"], MINIMAL["columns"][0]])


def test_rejects_dangling_currency_reference() -> None:
    columns = [*MINIMAL["columns"]]
    columns[2] = {**columns[2], "currency_column": "missing_col"}
    with pytest.raises(ConfigError, match="undeclared currency column"):
        _schema(columns=columns)


def test_rejects_unknown_tiebreaker() -> None:
    with pytest.raises(ConfigError, match="tiebreaker"):
        _schema(dedupe_tiebreaker="not_a_column")


def test_fingerprint_is_stable_and_order_independent() -> None:
    """Reordering columns is noise, not drift."""
    reordered = list(reversed(MINIMAL["columns"]))  # type: ignore[arg-type]
    assert _schema().fingerprint == _schema(columns=reordered).fingerprint


def test_fingerprint_changes_when_a_column_changes() -> None:
    altered = [*MINIMAL["columns"], {"name": "extra", "type": "string", "class": "descriptor"}]
    assert _schema().fingerprint != _schema(columns=altered).fingerprint


def test_pii_columns_are_discoverable() -> None:
    columns = [
        *MINIMAL["columns"],
        {"name": "email", "type": "string", "class": "descriptor", "pii": True},
    ]
    assert _schema(columns=columns).pii_columns == ("email",)


# ── Drift ────────────────────────────────────────────────────────────


def test_missing_column_is_blocking_drift() -> None:
    findings = detect_drift(_schema(), ["order_id", "ts"])
    assert [f.kind for f in findings] == [DriftKind.MISSING_COLUMN]
    assert findings[0].column == "amount"
    assert findings[0].blocking


def test_new_column_is_a_warning_not_a_failure() -> None:
    """Bronze is as-received; undeclared columns land but stay invisible."""
    findings = detect_drift(_schema(), ["order_id", "ts", "amount", "loyalty_id"])
    assert [f.kind for f in findings] == [DriftKind.NEW_COLUMN]
    assert not findings[0].blocking


def test_case_and_order_differences_are_absorbed() -> None:
    assert detect_drift(_schema(), ["TS", "Amount", "ORDER_ID"]) == []


def test_shipped_pos_schema_is_valid() -> None:
    """The schema that actually ships must parse and validate."""
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "ingestion/schemas/pos/sales.yml"
    schema = SourceSchema.from_yaml(path)

    assert schema.natural_key == ("order_id", "line_no")
    assert schema.dedupe_tiebreaker == "updated_at"
    assert "customer_email" in schema.pii_columns
    # The pseudonymous loyalty id must NOT be PII-flagged — it is the join
    # key customer analytics depends on.
    assert "customer_id" not in schema.pii_columns
    # Money columns must all name their currency column, or FX cannot resolve.
    assert all(c.currency_column == "currency" for c in schema.columns if c.is_money)
    # Version is bumped on every contract change; assert it is declared and
    # parseable rather than pinning a literal that every change must chase.
    assert str(yaml.safe_load(path.read_text())["version"]).count(".") == 1
