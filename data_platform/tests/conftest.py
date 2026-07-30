"""Shared ETL test fixtures."""

from pathlib import Path

import pytest

from ingestion.core.config import EtlSettings
from ingestion.domain.schema import SourceSchema

REPO_SCHEMAS = Path(__file__).resolve().parents[1] / "ingestion/schemas"


@pytest.fixture
def minimal_schema() -> SourceSchema:
    """A three-column schema — enough to exercise policy without noise."""
    return SourceSchema.from_dict(
        {
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
    )


@pytest.fixture
def pos_schema() -> SourceSchema:
    """The schema that actually ships."""
    return SourceSchema.from_yaml(REPO_SCHEMAS / "pos" / "sales.yml")


@pytest.fixture
def settings(tmp_path: Path) -> EtlSettings:
    """Settings rooted in a temp directory — no test touches a shared lake."""
    return EtlSettings(
        landing_root=tmp_path / "lake",
        inbox_root=tmp_path / "inbox",
        warehouse_path=tmp_path / "warehouse.duckdb",
    )
