"""Genesis schema: extensions, all tables, partitions, triggers, grants, views.

Revision ID: 0001_genesis
Revises: None

GENESIS PATTERN — read before touching:
    This one revision creates the schema *from the model registry's metadata*.
    That is deliberate and applies to this revision only: models and DDL have a
    single source at birth, and ``alembic check`` in CI fails the build the
    moment models drift from migrations. Every subsequent revision is
    handwritten (expand → migrate → contract, DB design). Do not edit this
    file after the v0.2 tag — schema changes go in new revisions.

Beyond the metadata, this revision owns the pure-SQL surface area:
    * extensions: citext (case-insensitive email), btree_gist (mute exclusion),
      pgcrypto (gen_random_uuid on PG < 13 compat paths);
    * ``uuid_generate_v7()`` — DB-side time-ordered UUIDs (DB);
    * DEFAULT partitions for the monthly-partitioned tables (llm_usage,
      audit_event); the maintenance job creates monthly partitions ahead and
      will detach the default (DB);
    * ``set_updated_at()`` trigger on every table with an ``updated_at`` column
      — raw-SQL updates cannot forget it;
    * append-only grants on audit tables (INSERT-only for app roles) — applied
      only where the roles exist, so dev single-role setups still migrate;
    * plain views (v_alert_inbox, v_pipeline_health) and materialized views
      (mv_llm_spend_month, mv_alert_quality, mv_pipeline_sla) with the unique
      indexes REFRESH CONCURRENTLY requires (DB).
"""

from collections.abc import Sequence

from alembic import op

from app.infrastructure.db.models import Base

revision: str = "0001_genesis"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


UUID_V7_FN = """
-- Time-ordered UUIDv7 (unix-millis in the top 48 bits + version/variant bits).
-- Canonical SQL implementation; replaced by native uuidv7() when we move to PG 18.
CREATE OR REPLACE FUNCTION uuid_generate_v7() RETURNS uuid
LANGUAGE sql VOLATILE PARALLEL SAFE AS $$
SELECT encode(
  set_bit(
    set_bit(
      overlay(
        uuid_send(gen_random_uuid())
        PLACING substring(
          int8send(floor(extract(epoch FROM clock_timestamp()) * 1000)::bigint)
          FROM 3)
        FROM 1 FOR 6
      ),
      52, 1),
    53, 1),
  'hex')::uuid;
$$;
"""

UPDATED_AT_FN = """
CREATE OR REPLACE FUNCTION set_updated_at() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$;
"""

ATTACH_UPDATED_AT_TRIGGERS = """
-- Attach the trigger to every public table that carries updated_at.
DO $$
DECLARE t text;
BEGIN
  FOR t IN
    SELECT c.table_name
    FROM information_schema.columns c
    JOIN information_schema.tables tb
      ON tb.table_name = c.table_name AND tb.table_schema = c.table_schema
    WHERE c.table_schema = 'public'
      AND c.column_name = 'updated_at'
      AND tb.table_type = 'BASE TABLE'
  LOOP
    EXECUTE format(
      'CREATE TRIGGER trg_%s_updated_at BEFORE UPDATE ON %I
       FOR EACH ROW EXECUTE FUNCTION set_updated_at()', t, t);
  END LOOP;
END $$;
"""

APPEND_ONLY_GRANTS = """
-- Audit is append-only BY GRANTS, not convention (DB). Applied only where
-- the production role split exists; dev single-role databases skip silently.
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'api_rw') THEN
    REVOKE UPDATE, DELETE ON audit_event, auth_event FROM api_rw;
  END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'worker_rw') THEN
    REVOKE UPDATE, DELETE ON audit_event, auth_event FROM worker_rw;
  END IF;
END $$;
"""

# asyncpg prepares one statement per execute — each DDL string stands alone.
VIEW_ALERT_INBOX = """
-- Inbox view: alert + rule + metric display context in one governed shape.
-- Business math stays in the semantic layer; views join, filter, project only.
CREATE VIEW v_alert_inbox AS
SELECT
    a.id,
    a.tenant_id,
    a.severity,
    a.status,
    a.series_key,
    a.observed,
    a.expected_low,
    a.expected_high,
    a.narration,
    a.detected_at,
    a.acked_at,
    a.data_snapshot_id,
    r.detector,
    mc.metric_key,
    mc.display_name AS metric_display_name
FROM alert a
JOIN alert_rule r    ON r.id = a.rule_id
JOIN metric_config mc ON mc.id = r.metric_config_id
"""

VIEW_PIPELINE_HEALTH = """
-- Pipeline health: latest run per connector (Chen's screen, PRD Journey D).
CREATE VIEW v_pipeline_health AS
SELECT DISTINCT ON (cc.id)
    cc.id            AS connector_id,
    cc.tenant_id,
    cc.source_key,
    cc.display_name,
    cc.enabled,
    pr.id            AS last_run_id,
    pr.status        AS last_status,
    pr.window        AS last_window,
    pr.rows_written  AS last_rows_written,
    pr.rows_rejected AS last_rows_rejected,
    pr.started_at    AS last_started_at,
    pr.ended_at      AS last_ended_at
FROM connector_config cc
LEFT JOIN pipeline_run pr ON pr.connector_id = cc.id
ORDER BY cc.id, pr.started_at DESC NULLS LAST
"""

MV_LLM_SPEND_MONTH = """
-- Monthly LLM spend rollup: budget checks read this, never the raw ledger
-- (DB mv_llm_spend_month). Refreshed CONCURRENTLY on schedule.
CREATE MATERIALIZED VIEW mv_llm_spend_month AS
SELECT
    tenant_id,
    date_trunc('month', at)  AS month,
    module,
    count(*)                 AS calls,
    sum(tokens_in)           AS tokens_in,
    sum(tokens_out)          AS tokens_out,
    sum(cost_usd)            AS cost_usd
FROM llm_usage
GROUP BY tenant_id, date_trunc('month', at), module
"""

MV_ALERT_QUALITY = """
-- Alert quality per rule/week: ack ÷ (ack + mute-proxy) feeds sensitivity
-- retuning (DB mv_alert_quality; fatigue loop).
CREATE MATERIALIZED VIEW mv_alert_quality AS
SELECT
    a.tenant_id,
    a.rule_id,
    date_trunc('week', a.detected_at)              AS week,
    count(*)                                       AS alerts_total,
    count(*) FILTER (WHERE a.acked_at IS NOT NULL) AS alerts_acked,
    count(*) FILTER (WHERE a.status = 'resolved')  AS alerts_resolved,
    avg(EXTRACT(EPOCH FROM (a.acked_at - a.detected_at)) / 3600.0)
        FILTER (WHERE a.acked_at IS NOT NULL)      AS avg_hours_to_ack
FROM alert a
GROUP BY a.tenant_id, a.rule_id, date_trunc('week', a.detected_at)
"""

MV_PIPELINE_SLA = """
-- Pipeline SLA attainment per connector/day (DB mv_pipeline_sla).
CREATE MATERIALIZED VIEW mv_pipeline_sla AS
SELECT
    pr.tenant_id,
    pr.connector_id,
    date_trunc('day', pr.started_at)                    AS day,
    count(*)                                            AS runs,
    count(*) FILTER (WHERE pr.status = 'succeeded')     AS runs_succeeded,
    count(*) FILTER (WHERE pr.status = 'quarantined')   AS runs_quarantined,
    max(pr.ended_at)                                    AS last_ended_at
FROM pipeline_run pr
GROUP BY pr.tenant_id, pr.connector_id, date_trunc('day', pr.started_at)
"""

# Unique indexes are what make REFRESH CONCURRENTLY possible (DB).
MV_UNIQUE_INDEXES = [
    "CREATE UNIQUE INDEX uq_mv_llm_spend_month ON mv_llm_spend_month (tenant_id, month, module)",
    "CREATE UNIQUE INDEX uq_mv_alert_quality ON mv_alert_quality (tenant_id, rule_id, week)",
    "CREATE UNIQUE INDEX uq_mv_pipeline_sla ON mv_pipeline_sla (tenant_id, connector_id, day)",
]


def upgrade() -> None:
    bind = op.get_bind()

    # 1 — extensions and functions the metadata depends on
    op.execute("CREATE EXTENSION IF NOT EXISTS citext")
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.execute(UUID_V7_FN)

    # 2 — all tables, constraints, and indexes from the model registry
    Base.metadata.create_all(bind)

    # 3 — DEFAULT partitions so the partitioned tables accept writes immediately;
    #     the maintenance job pre-creates monthly partitions ahead (DB)
    op.execute("CREATE TABLE llm_usage_default PARTITION OF llm_usage DEFAULT")
    op.execute("CREATE TABLE audit_event_default PARTITION OF audit_event DEFAULT")

    # 4 — updated_at trigger, attached generically
    op.execute(UPDATED_AT_FN)
    op.execute(ATTACH_UPDATED_AT_TRIGGERS)

    # 5 — append-only audit enforcement (role-conditional)
    op.execute(APPEND_ONLY_GRANTS)

    # 6 — views and materialized views (one statement per execute — asyncpg contract)
    for statement in (
        VIEW_ALERT_INBOX,
        VIEW_PIPELINE_HEALTH,
        MV_LLM_SPEND_MONTH,
        MV_ALERT_QUALITY,
        MV_PIPELINE_SLA,
        *MV_UNIQUE_INDEXES,
    ):
        op.execute(statement)


def downgrade() -> None:
    bind = op.get_bind()
    op.execute("DROP MATERIALIZED VIEW IF EXISTS mv_pipeline_sla")
    op.execute("DROP MATERIALIZED VIEW IF EXISTS mv_alert_quality")
    op.execute("DROP MATERIALIZED VIEW IF EXISTS mv_llm_spend_month")
    op.execute("DROP VIEW IF EXISTS v_pipeline_health")
    op.execute("DROP VIEW IF EXISTS v_alert_inbox")
    Base.metadata.drop_all(bind)
    op.execute("DROP FUNCTION IF EXISTS set_updated_at()")
    op.execute("DROP FUNCTION IF EXISTS uuid_generate_v7()")
