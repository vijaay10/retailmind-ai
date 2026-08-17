---
title: "Data Pipeline Orchestration with Dagster"
description: "Production-grade orchestration for RetailMind data platform"
---

# Data Pipeline Orchestration

RetailMind's data platform uses **Dagster** for production orchestration while preserving the existing CLI-based pipeline for local development.

## Architecture

### Design Principles

1. **Wrap, Don't Rewrite**: Orchestration calls existing CLI commands rather than duplicating pipeline logic
2. **Asset-Centric**: Models data as assets (bronze partitions, warehouse tables, dbt models) rather than tasks
3. **Idempotent by Default**: All assets use partition overwrite semantics from the existing pipeline
4. **Quality Gates Preserved**: Dagster enforces the same quality gates as CLI (blocking failures prevent downstream assets)
5. **CLI Remains Functional**: Operators can still use `retailmind-etl` for manual operations and local development

### Asset Lineage

```
External Sources (CSV files)
    ↓
Bronze Partitions (parquet + manifest)
    ↓
RAW Tables (warehouse)
    ↓
dbt Seeds & Snapshots
    ↓
Staging Views (stg_*)
    ↓
Analytics Tables (dim_*, fct_*, mart_*)
    ↓
Semantic Views (v_*)
    ↓
ML Models & Forecasts
```

## Getting Started

### Installation

Install Dagster dependencies:

```bash
cd data_platform
uv pip install -e ".[dagster]"
```

This installs:
- `dagster>=1.8` - Core orchestration framework
- `dagster-webserver>=1.8` - UI for monitoring and backfills
- `dagster-dbt>=0.24` - dbt integration
- `dagster-duckdb>=0.24` - DuckDB resource

### Local Development

Start Dagster UI:

```bash
cd data_platform
dagster dev -w orchestration/workspace.yaml
```

This starts:
- **Dagster UI** at http://localhost:3000
- **Asset materializations** (run assets manually)
- **Schedules** (automated daily/weekly runs)
- **Sensors** (failure retry, quality alerts)
- **Backfills** (re-process date ranges)

### First Run

1. **Materialize seeds** (reference data):
   ```bash
   # In Dagster UI: Assets → dbt_seeds → Materialize
   ```

2. **Run ingestion for one day**:
   ```bash
   # In Dagster UI: Assets → bronze_pos_sales → Materialize (select partition)
   ```

3. **Build warehouse**:
   ```bash
   # In Dagster UI: Jobs → dbt_build → Launch Run
   ```

## Scheduled Runs

Dagster runs pipelines automatically on schedules.

### Daily Ingestion (2 AM UTC)

Ingests all 5 sources for the current business date:
- pos.sales
- inventory.positions
- purchasing.orders
- weather.observations
- fulfilment.deliveries

**Schedule**: `daily_ingestion_schedule`
**Cron**: `0 2 * * *` (2 AM UTC daily)
**Status**: RUNNING (enabled)

### Daily Warehouse Build (3 AM UTC)

Runs dbt build (seeds → snapshots → models → tests):
- Staging views
- Core tables (dimensions + facts)
- Metrics tables
- Semantic views

**Schedule**: `daily_dbt_schedule`
**Cron**: `0 3 * * *` (3 AM UTC daily)
**Status**: RUNNING (enabled)

### Weekly Forecast Training (Monday 4 AM UTC)

Trains forecast models and publishes predictions:
- Backtest candidate models (12 folds)
- Gate winner based on MAPE/WAPE/MASE
- Publish champion forecasts

**Schedule**: `weekly_forecast_schedule`
**Cron**: `0 4 * * 1` (Monday 4 AM UTC)
**Status**: RUNNING (enabled)

### Enabling/Disabling Schedules

In Dagster UI:
1. Go to **Automation** → **Schedules**
2. Toggle schedule on/off
3. View run history

Or via CLI:
```bash
dagster schedule start daily_ingestion_schedule
dagster schedule stop daily_ingestion_schedule
```

## Manual Runs

### Materialize Single Asset

Run one asset for one partition:

1. **In UI**:
   - Assets → Select asset (e.g., `bronze_pos_sales`)
   - Click "Materialize"
   - Select partition (business date)
   - Launch run

2. **Via CLI**:
   ```bash
   dagster asset materialize \
     -m orchestration.dagster \
     --select bronze_pos_sales \
     --partition 2026-07-21
   ```

### Run Job

Run a collection of assets:

1. **In UI**:
   - Jobs → Select job (e.g., `daily_ingestion`)
   - Click "Launch Run"
   - Configure partition/tags
   - Launch

2. **Via CLI**:
   ```bash
   dagster job execute \
     -m orchestration.dagster \
     -j daily_ingestion \
     --partition 2026-07-21
   ```

### Dry Run

Test a run without executing:

```bash
dagster job launch \
  -m orchestration.dagster \
  -j daily_ingestion \
  --partition 2026-07-21 \
  --op-selection bronze_pos_sales \
  --dry-run
```

## Backfills

Backfills re-process historical date ranges.

### Full Backfill (All Sources)

Re-ingest and rebuild warehouse for a date range:

1. **In UI**:
   - Assets → Select `bronze_pos_sales`
   - Click "Backfill"
   - Select date range (e.g., 2026-06-01 to 2026-06-30)
   - Launch backfill

2. **Via CLI**:
   ```bash
   dagster asset backfill \
     -m orchestration.dagster \
     --asset bronze_pos_sales \
     --from 2026-06-01 \
     --to 2026-06-30
   ```

### Selective Backfill

Backfill only specific source:

```bash
# Backfill only pos.sales
dagster asset backfill \
  -m orchestration.dagster \
  --asset bronze_pos_sales \
  --asset raw_pos_sales \
  --from 2026-06-01 \
  --to 2026-06-30
```

### Downstream Backfill

Backfill asset + all downstream assets:

```bash
dagster asset backfill \
  -m orchestration.dagster \
  --asset bronze_pos_sales+ \
  --from 2026-06-01 \
  --to 2026-06-30
```

The `+` suffix includes all downstream dependencies.

### Backfill Monitoring

Track backfill progress:

1. **In UI**:
   - Runs → Backfills
   - View partition grid (green = success, red = failure)
   - Cancel in-progress backfills

2. **Via CLI**:
   ```bash
   dagster backfill list
   dagster backfill status <backfill_id>
   dagster backfill cancel <backfill_id>
   ```

## Failure Handling

### Automatic Retry (Sensor)

The `failed_partition_retry` sensor automatically retries failed partitions:

- **Trigger**: Failed ingestion runs
- **Delay**: 1 hour after failure
- **Max Retries**: 3 attempts per partition
- **Status**: STOPPED (manual enable recommended)

**Enable**:
```bash
dagster sensor start failed_partition_retry
```

### Manual Retry

Retry a failed partition:

1. **In UI**:
   - Runs → Find failed run
   - Click "Re-execute from Failure"
   - Or select partition and materialize again

2. **Via CLI**:
   ```bash
   # Re-materialize failed partition
   dagster asset materialize \
     -m orchestration.dagster \
     --select bronze_pos_sales \
     --partition 2026-07-21
   ```

### Quality Gate Failures

When quality gates fail (quarantine):

1. **Alert**: `quality_quarantine_alert` sensor logs ERROR
2. **Operator Action**:
   - Check quarantine reason in logs
   - Fix upstream data issue
   - Replay partition via CLI or Dagster

**Replay Quarantined Partition**:
```bash
# Fix source data, then re-run
retailmind-etl run --source pos --table sales --day 2026-07-21
```

Or in Dagster UI: Materialize the partition again.

## Observability

### Run Status

View run status in Dagster UI:

1. **Runs** → All Runs
   - Filter by job, status, date range
   - View run details, logs, metadata

2. **Assets** → Select asset
   - View materialization history
   - See latest partition status

### Asset Metadata

Each asset reports metadata:

**Bronze Partitions**:
- `rows_read` - Total rows extracted
- `rows_landed` - Rows written to bronze
- `rows_rejected` - Rows failing validation
- `quality_passed` - Quality gate result
- `reject_rate` - Percentage rejected

**RAW Tables**:
- `row_count` - Rows loaded to warehouse
- `partition_loaded` - Verification status

**dbt Models**:
- `rows_affected` - Rows updated
- `execution_time` - Model runtime

**Forecasts**:
- `models_trained` - Number of models
- `champion_published` - Whether forecast published
- `prediction_count` - Forecast rows written

### Asset Checks

Asset checks verify data quality:

**Row Count Checks**:
- Severity: ERROR
- Condition: Partition has > 0 rows
- Failure: Partition is empty (all rows rejected)

**Freshness Checks**:
- Severity: WARN (≤7 days) / ERROR (>7 days)
- Condition: Latest partition within 2 days
- Failure: Data is stale

**Forecast Coverage**:
- Severity: ERROR
- Condition: Forecasts cover all targets (revenue, units)
- Failure: Missing targets

**View Checks**:
1. Assets → Select asset
2. Checks tab
3. View pass/fail status

### Data Freshness

Monitor data freshness:

```sql
-- Query latest partition per source
SELECT
    source_key,
    table_name,
    MAX(business_date) as latest_partition,
    CURRENT_DATE - MAX(business_date) as days_behind
FROM (
    SELECT 'pos' as source_key, 'sales' as table_name, MAX(business_date) as business_date
    FROM raw.pos__sales
    UNION ALL
    SELECT 'inventory', 'positions', MAX(business_date) FROM raw.inventory__positions
    -- ...
) t
GROUP BY source_key, table_name
```

Or in Dagster UI: Assets → Freshness Checks

## Late-Arriving Data

Late-arriving data is handled by the existing CLI logic:

**Late Arrival Window**: 35 days (configurable in `EtlSettings`)

**How It Works**:
1. Nightly ingestion runs for current business date
2. CLI automatically re-processes trailing 35-day window
3. Partition overwrite semantics absorb late files
4. Quality gate re-validates

**No Dagster Changes Needed**: The CLI already handles late arrivals. Dagster just calls the CLI daily.

**Manual Replay** (if needed):
```bash
# Re-process specific partition to absorb late file
dagster asset materialize \
  -m orchestration.dagster \
  --select bronze_pos_sales \
  --partition 2026-07-21
```

## Idempotency

All assets are idempotent (safe to re-run):

**Bronze Partitions**:
- Partition overwrite semantics
- Same files → byte-identical output
- Manifest-based commit (atomic)

**RAW Tables**:
- DELETE + INSERT in single transaction
- Re-running loads same data

**dbt Models**:
- Incremental models use `is_incremental()` macro
- Full-refresh via `--full-refresh` flag
- Seeds always overwrite

**Forecasts**:
- Predictions table truncated before insert
- Same date range → identical forecasts (deterministic models)

**Verification**:
```bash
# Run twice, verify identical output
dagster asset materialize -m orchestration.dagster --select bronze_pos_sales --partition 2026-07-21
dagster asset materialize -m orchestration.dagster --select bronze_pos_sales --partition 2026-07-21
```

Metadata (rows, timestamps) should match.

## CLI vs Orchestration

### When to Use CLI

Use `retailmind-etl` CLI for:
- **Local development**: Test ingestion logic changes
- **Manual replay**: Fix quarantined partitions
- **Debugging**: Inspect rejects, quality failures
- **One-off tasks**: Load historical data once

**Example**:
```bash
# Test new schema change locally
retailmind-etl run --source pos --table sales --day 2026-07-21

# Check rejects
retailmind-etl rejects --partition 2026-07-21 --source pos --table sales

# Backfill historical window
retailmind-etl backfill 2026-06-01 2026-06-30 --source pos --table sales
```

### When to Use Dagster

Use Dagster for:
- **Scheduled runs**: Automated daily/weekly pipelines
- **Backfills**: Re-process date ranges with UI
- **Monitoring**: Track run status, data freshness
- **Failure retry**: Automatic retry on transient failures
- **Asset lineage**: Visualize dependencies

**Example**:
```bash
# Schedule daily ingestion
dagster schedule start daily_ingestion_schedule

# Backfill with UI progress tracking
dagster asset backfill --asset bronze_pos_sales --from 2026-06-01 --to 2026-06-30

# Monitor run status
dagster runs list --job daily_ingestion --limit 10
```

### They Work Together

Dagster calls CLI commands under the hood:

```python
# In orchestration/dagster/assets/ingestion.py
result = cli.run_ingestion(
    source="pos",
    table="sales",
    business_date="2026-07-21",
)
```

This executes:
```bash
uv run retailmind-etl run --source pos --table sales --day 2026-07-21
```

## Configuration

### Resource Configuration

Resources are configured in `orchestration/dagster/__init__.py`:

```python
resources = {
    "warehouse": DuckDBWarehouse(
        database_path=".local/retailmind.duckdb"
    ),
    "cli": CliExecutor(
        working_directory="/path/to/data_platform"
    ),
    "dbt": DbtCliResource(
        project_dir="dbt",
        profiles_dir=".",
    ),
    "audit_ledger": AuditLedger(
        postgres_dsn="postgresql://localhost/retailmind",
        enabled=False,  # Enable when Postgres available
    ),
}
```

**Override via Environment**:
```bash
export DAGSTER_WAREHOUSE_PATH=/path/to/warehouse.duckdb
export DAGSTER_CLI_WORKING_DIR=/path/to/data_platform
```

### Dagster Instance Config

Configure Dagster in `orchestration/dagster.yaml`:

**Run Storage**: Where run history is stored (SQLite locally, Postgres in production)
**Event Log**: Where asset materializations are tracked
**Run Launcher**: How runs are executed (local, Kubernetes, etc.)
**Run Coordinator**: Controls run concurrency

**Production Settings**:
```yaml
run_storage:
  module: dagster_postgres.run_storage
  class: PostgresRunStorage
  config:
    postgres_url: postgresql://user:pass@host/dbname

event_log_storage:
  module: dagster_postgres.event_log
  class: PostgresEventLogStorage
  config:
    postgres_url: postgresql://user:pass@host/dbname

run_coordinator:
  module: dagster.core.run_coordinator
  class: QueuedRunCoordinator
  config:
    max_concurrent_runs: 10
```

## Testing

### Unit Tests

Test orchestration components:

```bash
cd data_platform
uv run pytest tests/unit/test_dagster_orchestration.py -v
```

**Coverage**:
- Resource configuration
- Asset factory functions
- CLI command building
- Dependency ordering
- Partition handling
- Failure recovery
- Idempotency

### Integration Tests

Test full pipeline runs (requires Dagster instance):

```bash
# Test single asset materialization
dagster asset materialize \
  -m orchestration.dagster \
  --select bronze_pos_sales \
  --partition 2026-07-21

# Test job execution
dagster job execute \
  -m orchestration.dagster \
  -j daily_ingestion \
  --partition 2026-07-21
```

### Dry Run Tests

Validate job configuration without executing:

```bash
dagster job launch \
  -m orchestration.dagster \
  -j full_pipeline \
  --partition 2026-07-21 \
  --dry-run
```

## Production Deployment

### Dagster Cloud (Recommended)

1. **Sign up**: https://dagster.cloud
2. **Deploy code**:
   ```bash
   dagster-cloud serverless deploy \
     --location-name retailmind_data_platform \
     --python-file orchestration/dagster/__init__.py
   ```

3. **Configure resources**:
   - Set production warehouse path
   - Enable audit ledger (Postgres)
   - Configure alerts (Slack, PagerDuty)

4. **Enable schedules**:
   - Daily ingestion
   - Daily dbt build
   - Weekly forecast training

### Self-Hosted

Deploy Dagster on Kubernetes:

1. **Install Dagster Helm chart**:
   ```bash
   helm repo add dagster https://dagster-io.github.io/helm
   helm install dagster dagster/dagster \
     --namespace dagster \
     --values orchestration/helm-values.yaml
   ```

2. **Configure Postgres**:
   - Run storage
   - Event log storage
   - Schedule storage

3. **Deploy code**:
   - Build Docker image with data_platform
   - Push to registry
   - Update deployment

4. **Configure secrets**:
   - Warehouse credentials
   - Audit ledger DSN
   - Notification webhooks

### Run Launchers

**Local**: Runs execute in same process (development only)
**Docker**: Runs execute in Docker containers (better isolation)
**Kubernetes**: Runs execute as Kubernetes jobs (production)

**Configure**:
```yaml
# dagster.yaml
run_launcher:
  module: dagster_k8s
  class: K8sRunLauncher
  config:
    image_pull_policy: Always
    service_account_name: dagster-user-deployments
```

## Monitoring and Alerts

### Run Alerts

Configure alerts in Dagster Cloud:

1. **Failure Alerts**:
   - Send to Slack/PagerDuty when runs fail
   - Include run_id, failure reason, logs

2. **SLA Alerts**:
   - Alert if ingestion doesn't complete by 3 AM
   - Alert if dbt doesn't complete by 4 AM

3. **Data Quality Alerts**:
   - Alert on quality gate failures
   - Include partition, rule_id, observed values

### Metrics

Track pipeline metrics:

- **Run Duration**: How long each job takes
- **Asset Materialization Rate**: Assets per hour
- **Failure Rate**: % of runs failing
- **Retry Rate**: % of partitions requiring retry
- **Data Freshness**: Days since latest partition

**Query via GraphQL**:
```graphql
{
  runStatsOverTime(limit: 30) {
    succeeded
    failed
    inProgress
    totalDuration
  }
}
```

### Dashboards

Build dashboards in Dagster UI or external tools:

**Grafana + Prometheus**:
- Export Dagster metrics to Prometheus
- Build Grafana dashboards
- Alert on SLOs

**Datadog**:
- Send Dagster events to Datadog
- Build APM dashboards
- Monitor pipeline latency

## Troubleshooting

### Asset Won't Materialize

**Symptoms**: Asset stuck in "Materializing" state

**Diagnoses**:
1. Check run logs: Runs → Select run → Logs
2. Verify resource connectivity: Can Dagster reach warehouse/CLI?
3. Check upstream dependencies: Are parent assets materialized?

**Solutions**:
- Terminate stuck run: Runs → Terminate
- Retry: Materialize asset again
- Check resource config: Verify paths, credentials

### Quality Gate Failures

**Symptoms**: Bronze assets fail during materialization

**Diagnoses**:
1. Check run logs for quality rule failures
2. Use CLI to inspect rejects:
   ```bash
   retailmind-etl rejects --partition 2026-07-21 --source pos --table sales
   ```

**Solutions**:
- Fix upstream data quality
- Adjust quality thresholds (if appropriate)
- Replay partition after fix

### Backfill Stuck

**Symptoms**: Backfill shows some partitions stuck

**Diagnoses**:
1. Backfills → View partition grid
2. Identify failed partitions (red)
3. Check failure reason in logs

**Solutions**:
- Cancel backfill: Backfills → Cancel
- Fix failures manually
- Re-launch backfill for failed partitions only

### Schedule Not Running

**Symptoms**: Scheduled runs not executing

**Diagnoses**:
1. Automation → Schedules → Check status (RUNNING?)
2. Verify cron expression
3. Check run history: Any runs created?

**Solutions**:
- Start schedule: `dagster schedule start <schedule_name>`
- Verify time zone (UTC)
- Check run coordinator (is max_concurrent_runs reached?)

## Best Practices

### Asset Design

1. **Keep assets idempotent**: Safe to re-run
2. **Small asset granularity**: One source table = one asset
3. **Use partitions**: Enable backfills
4. **Include metadata**: Rows, timestamps, quality scores

### Dependency Management

1. **Explicit dependencies**: Don't rely on implicit ordering
2. **Avoid circular dependencies**: Use staging tables
3. **Group related assets**: Use groups for organization

### Backfills

1. **Test first**: Run single partition before full backfill
2. **Monitor progress**: Check partition grid regularly
3. **Limit parallelism**: Don't overwhelm warehouse
4. **Verify quality**: Check asset checks after backfill

### Failure Handling

1. **Enable retry sensor**: For transient failures
2. **Set max retries**: Prevent infinite retry loops
3. **Alert on failures**: Don't rely on manual checks
4. **Document recovery**: SOP for quarantine replay

## Migration from CLI

### Phase 1: Parallel Operation

1. Keep CLI scheduled via cron
2. Deploy Dagster alongside (STOPPED schedules)
3. Test Dagster runs manually
4. Verify output matches CLI

### Phase 2: Partial Migration

1. Enable Dagster schedules for non-critical sources
2. Monitor for 1 week
3. Compare data freshness, quality, latency
4. Disable cron for migrated sources

### Phase 3: Full Migration

1. Enable all Dagster schedules
2. Disable all cron jobs
3. Update runbooks to use Dagster
4. Train operators on Dagster UI

### Phase 4: Decommission CLI Scheduling

1. Remove cron jobs
2. Keep CLI for manual operations
3. Archive cron scripts
4. Update documentation

## FAQs

**Q: Can I still use `retailmind-etl` CLI?**
A: Yes! CLI remains fully functional for local development and manual operations.

**Q: How do I backfill a date range?**
A: Assets → Select asset → Backfill → Choose date range

**Q: What happens if quality gate fails?**
A: Bronze asset fails, partition is quarantined, downstream assets are NOT run.

**Q: How do I retry a failed partition?**
A: Materialize the asset again with the same partition key.

**Q: Can I run specific dbt models?**
A: Yes, use dbt CLI directly or select specific dbt assets in Dagster UI.

**Q: How do I monitor data freshness?**
A: Check asset freshness checks or query latest partitions in warehouse.

**Q: Can I run backfills in parallel?**
A: Yes, Dagster parallelizes partition materializations (up to max_concurrent_runs).

**Q: How do I disable a schedule?**
A: Automation → Schedules → Toggle OFF.

## Resources

- **Dagster Docs**: https://docs.dagster.io
- **Dagster University**: https://dagster.io/university
- **Dagster Cloud**: https://dagster.cloud
- **Dagster Slack**: https://dagster.io/slack

## Support

For issues with orchestration:

1. **Check Dagster UI**: Runs → Logs
2. **Check CLI logs**: Verify CLI commands work
3. **GitHub Issues**: https://github.com/anthropics/retailmind-ai/issues
4. **Dagster Community**: https://dagster.io/slack
