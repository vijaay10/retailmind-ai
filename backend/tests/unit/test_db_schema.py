"""Schema invariants, verified without a database.

Compiles every table's DDL against the Postgres dialect and asserts the
conventions the Database design doc makes mandatory. These tests are the cheap
tripwire; the real-database proof lives in tests/integration/test_migrations.py.
"""

import pytest
from sqlalchemy import CheckConstraint, Table
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

from app.infrastructure.db.models import Base

DIALECT = postgresql.dialect()

# Tables that are deliberately NOT tenant-scoped (global/child/junction tables).
TENANT_EXEMPT = {
    "tenant",
    "role",
    "user_role",
    "refresh_token",
    "api_key",  # tenant FK declared directly (not via mixin)
    "app_user",  # tenant FK declared directly for the composite unique
    "alert_rule",  # scoped via metric_config (CASCADE chain)
    "alert_event",  # scoped via alert
    "channel_pref",  # scoped via user
    "nlq_turn",  # scoped via session
    "dq_result",  # scoped via pipeline_run
    "report_section",  # scoped via report_run
    "recommendation_feedback",
    "recommendation_outcome",
    "scenario_run",  # scoped via scenario
    "dashboard_tile",  # scoped via dashboard
    "insight_feedback",
    "data_snapshot",  # string-PK spine table, tenant FK direct
    "metric_registry_version",
    "feature_flag_state",
    "audit_event",  # partitioned: tenant_id without FK by design
    "auth_event",
    "llm_usage",
}

PARTITIONED = {"llm_usage": "at", "audit_event": "at"}


def _tables() -> list[Table]:
    return list(Base.metadata.tables.values())


def test_metadata_is_nonempty_and_complete() -> None:
    # 41 tables: the Database design set plus `recommendation_decision`, added
    # when the console gained a way to accept a proposal. A dropped model
    # import shows up here first.
    assert len(_tables()) == 41, sorted(Base.metadata.tables)


@pytest.mark.parametrize("table", _tables(), ids=lambda t: t.name)
def test_ddl_compiles_for_postgres(table: Table) -> None:
    """Every table and index must produce valid Postgres DDL strings."""
    assert str(CreateTable(table).compile(dialect=DIALECT))
    for index in table.indexes:
        assert str(CreateIndex(index).compile(dialect=DIALECT))


@pytest.mark.parametrize("table", _tables(), ids=lambda t: t.name)
def test_every_table_has_primary_key(table: Table) -> None:
    assert table.primary_key.columns, f"{table.name} has no primary key"


@pytest.mark.parametrize("table", _tables(), ids=lambda t: t.name)
def test_tenant_scoping_declared(table: Table) -> None:
    """Every non-exempt table carries tenant_id (DB §12 conventions)."""
    if table.name in TENANT_EXEMPT:
        return
    assert "tenant_id" in table.columns, f"{table.name} is missing tenant_id"


def test_partitioned_tables_include_partition_key_in_pk() -> None:
    """Postgres requires the partition column inside the PK (DB §16)."""
    for name, key in PARTITIONED.items():
        table = Base.metadata.tables[name]
        pk_cols = {c.name for c in table.primary_key.columns}
        assert key in pk_cols, f"{name} PK must include partition key '{key}'"
        ddl = str(CreateTable(table).compile(dialect=DIALECT))
        assert "PARTITION BY RANGE" in ddl, f"{name} missing PARTITION BY clause"


def test_enum_checks_are_emitted() -> None:
    """Spot-check that StrEnum CHECK constraints made it into the DDL (DB §11)."""
    alert_ddl = str(CreateTable(Base.metadata.tables["alert"]).compile(dialect=DIALECT))
    assert "severity IN ('info', 'warn', 'critical')" in alert_ddl
    assert "status IN ('open', 'acked', 'resolved')" in alert_ddl


def test_constraint_naming_convention_applied() -> None:
    """Unnamed constraints must resolve through the metadata convention (DB §25)."""
    for table in _tables():
        for constraint in table.constraints:
            if isinstance(constraint, CheckConstraint) and constraint.name:
                assert not str(constraint.name).startswith("_unnamed"), (
                    f"{table.name}: anonymous check constraint"
                )


def test_rec_dedup_is_a_partial_unique_index() -> None:
    """The 'one active rec per subject' rule is structural (DB §40)."""
    table = Base.metadata.tables["recommendation"]
    dedup = next(i for i in table.indexes if i.name == "uq_rec_active_dedup")
    assert dedup.unique
    assert dedup.dialect_options["postgresql"]["where"] is not None
