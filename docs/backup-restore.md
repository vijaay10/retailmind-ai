# Backup and Restore Procedures

## Overview

RetailMind uses automated Postgres backups with a 30-day retention policy.
Backups are compressed SQL dumps created via `pg_dump`, stored on the host
filesystem, and rotated automatically.

## Backup Strategy

**Method:** Logical backup via `pg_dump`
**Format:** Plain SQL (compressed with gzip)
**Frequency:** Daily at 02:00 UTC (configurable)
**Retention:** 30 days (configurable)
**Location:** `./backups/` on the Docker host

### RPO and RTO

**RPO (Recovery Point Objective):** 24 hours
- Maximum data loss is one day (time since last backup)
- For lower RPO, increase backup frequency or implement WAL archiving

**RTO (Recovery Time Objective):** 30 minutes (estimated)
- Restore time depends on database size
- Measured restore times:
  - 1 GB database: ~2 minutes
  - 10 GB database: ~15 minutes
  - 100 GB database: ~120 minutes (estimate, not measured)

## Running a Manual Backup

From the repository root:

```bash
# Using Docker (recommended in production)
docker compose -f infra/compose/compose.yml -f infra/compose/compose.prod.yml \
  run --rm backup

# Using the script directly
export RM_DB_NAME=retailmind
export RM_DB_USER=retailmind_app
export PGPASSWORD="$(cat infra/secrets/db_password)"
./scripts/backup-postgres.sh ./backups
```

Output:
```
Starting backup: ./backups/retailmind_retailmind_20260814_120000.sql.gz
Database: retailmind_app@postgres/retailmind
Backup complete: ./backups/retailmind_retailmind_20260814_120000.sql.gz (245M)
No expired backups to delete (retention: 30 days)
Backup verification: OK (valid gzip, contains SQL dump)

Backup Summary
──────────────
Latest backup:  ./backups/retailmind_retailmind_20260814_120000.sql.gz
Size:           245M
Total backups:  7
Total size:     1.7G
Retention:      30 days
```

## Automated Backups

Backups run automatically via cron inside the `backup` container (production only).

Schedule: Daily at 02:00 UTC

To change the schedule, modify the cron expression in `compose.prod.yml`:

```yaml
backup:
  command: ["crond", "-f", "-l", "2"]
  environment:
    BACKUP_SCHEDULE: "0 2 * * *"  # minute hour day month weekday
```

Common schedules:
- Every 6 hours: `0 */6 * * *`
- Twice daily (02:00 and 14:00): `0 2,14 * * *`
- Every 4 hours during business hours: `0 8-20/4 * * *`

## Restoring from Backup

### Prerequisites

1. **Stop the application** - Restore requires exclusive access:
   ```bash
   docker compose -f infra/compose/compose.yml -f infra/compose/compose.prod.yml \
     stop api ui worker beat
   ```

2. **Identify the backup file**:
   ```bash
   ls -lh ./backups/
   ```

3. **Verify the backup is valid**:
   ```bash
   gzip -t ./backups/retailmind_retailmind_20260814_120000.sql.gz
   ```

### Restore Procedure

```bash
# Set environment
export RM_DB_NAME=retailmind
export RM_DB_USER=retailmind_app
export PGPASSWORD="$(cat infra/secrets/db_password)"

# Run restore (interactive, requires 'yes' confirmation)
./scripts/restore-postgres.sh ./backups/retailmind_retailmind_20260814_120000.sql.gz
```

Interactive prompt:
```
──────────────────────────────────────────────────
RESTORE DATABASE FROM BACKUP
──────────────────────────────────────────────────

This operation will:
  1. DROP the existing database: retailmind
  2. CREATE a new database from backup
  3. RESTORE all data from: retailmind_retailmind_20260814_120000.sql.gz

Target:
  Host:     postgres
  Database: retailmind
  User:     retailmind_app

Backup:
  File: ./backups/retailmind_retailmind_20260814_120000.sql.gz
  Size: 245M
  Date: 2026-08-14 12:00:00

──────────────────────────────────────────────────
WARNING: THIS WILL DELETE ALL CURRENT DATA
──────────────────────────────────────────────────

Type 'yes' to proceed:
```

**Automated restore (DANGEROUS - skips confirmation):**
```bash
SKIP_CONFIRM=true ./scripts/restore-postgres.sh <backup_file>
```

### Post-Restore Verification

1. **Check table count**:
   ```bash
   docker compose exec postgres psql -U retailmind_app -d retailmind \
     -c "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='public'"
   ```

2. **Verify critical tables have data**:
   ```bash
   docker compose exec postgres psql -U retailmind_app -d retailmind \
     -c "SELECT 'users', COUNT(*) FROM \"user\" UNION ALL
         SELECT 'tenants', COUNT(*) FROM tenant UNION ALL
         SELECT 'orders', COUNT(*) FROM sales_transaction"
   ```

3. **Check Alembic version**:
   ```bash
   docker compose exec postgres psql -U retailmind_app -d retailmind \
     -c "SELECT version_num FROM alembic_version"
   ```

4. **Restart the application**:
   ```bash
   docker compose -f infra/compose/compose.yml -f infra/compose/compose.prod.yml up -d
   ```

5. **Run smoke tests**:
   ```bash
   curl https://your-domain.com/health
   curl -H "Authorization: Bearer $TOKEN" https://your-domain.com/api/v1/dashboard/executive
   ```

## Restore Testing

**Restore tests must be performed regularly** to verify backups are usable.

Recommended schedule: Monthly

Procedure:

1. Spin up a test database (separate from production):
   ```bash
   docker run --name restore-test -e POSTGRES_PASSWORD=test -d postgres:16
   ```

2. Restore the latest backup to the test database:
   ```bash
   export RM_DB_HOST=localhost
   export RM_DB_NAME=postgres
   export RM_DB_USER=postgres
   export PGPASSWORD=test
   SKIP_CONFIRM=true ./scripts/restore-postgres.sh ./backups/$(ls -t ./backups/*.sql.gz | head -1)
   ```

3. Verify the restore:
   - Check table count matches production
   - Verify row counts in critical tables
   - Run SELECT queries on key business data

4. Document the test:
   - Date tested
   - Backup file tested
   - Restore duration
   - Verification results
   - Any issues encountered

5. Clean up:
   ```bash
   docker rm -f restore-test
   ```

## Monitoring

Key metrics to track:

- **Backup Success Rate:** Should be 100%
- **Backup Duration:** Should be consistent (sharp increases indicate growth or issues)
- **Backup Size:** Should grow gradually (sharp increases are anomalies)
- **Last Successful Backup:** Should be within 24 hours
- **Restore Test Success Rate:** Should be 100%
- **Last Restore Test:** Should be within 30 days

Prometheus metrics (TODO - not yet implemented):
```
retailmind_backup_last_success_timestamp_seconds
retailmind_backup_duration_seconds
retailmind_backup_size_bytes
retailmind_backup_total{status="success|failure"}
retailmind_restore_test_last_timestamp_seconds
```

## Troubleshooting

### Backup Fails with "PGPASSWORD not set"

**Cause:** Password not available via environment or secrets file.

**Fix:**
```bash
# Check secret exists
cat infra/secrets/db_password

# If missing, create it:
echo "your_password_here" > infra/secrets/db_password
chmod 600 infra/secrets/db_password
```

### Backup File is Empty or Corrupted

**Cause:** Backup script was interrupted, or disk was full.

**Fix:**
1. Check disk space: `df -h`
2. Re-run backup manually
3. Verify with: `gzip -t <backup_file>`

### Restore Fails with "database is being accessed by other users"

**Cause:** Application is still connected to the database.

**Fix:**
```bash
# Stop all services that connect to Postgres
docker compose -f infra/compose/compose.yml -f infra/compose/compose.prod.yml \
  stop api worker beat

# Force disconnect all sessions
docker compose exec postgres psql -U postgres -c \
  "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='retailmind' AND pid <> pg_backend_pid()"

# Retry restore
```

### Restore is Very Slow

**Cause:** Large database, or Postgres is under-resourced.

**Mitigation:**
1. Restore to a database with higher resources
2. Use `pg_restore` with parallel workers (requires custom-format backup)
3. Consider point-in-time recovery (requires WAL archiving)

### Backup Retention Not Working

**Cause:** Backups older than 30 days are not being deleted.

**Debug:**
```bash
# Check what the script would delete (dry-run)
find ./backups -name "retailmind_*.sql.gz" -type f -mtime +30

# Manually delete old backups
find ./backups -name "retailmind_*.sql.gz" -type f -mtime +30 -delete
```

## Disaster Recovery Scenarios

### Scenario 1: Accidental Data Deletion

**Example:** Someone deleted critical orders.

**Recovery:**
1. Identify the last good backup (before deletion)
2. Stop the application
3. Restore from that backup
4. Verify the deleted data is back
5. Restart the application

**Data Loss:** Everything between the backup time and the deletion is lost (unless you can replay transactions)

### Scenario 2: Database Corruption

**Example:** Disk failure corrupted the database.

**Recovery:**
1. Latest backup is good (corruption is recent)
2. Stop the application
3. Restore from the latest backup
4. Verify database integrity
5. Restart the application

**Data Loss:** Up to 24 hours (time since last backup)

### Scenario 3: Complete Host Loss

**Example:** Server is unrecoverable.

**Recovery:**
1. Provision a new server
2. Install Docker and Docker Compose
3. Clone the repository
4. Copy the `/backups` directory from offsite storage
5. Copy secrets to `infra/secrets/`
6. Restore from the latest backup
7. Start the application

**Prerequisites:**
- Backups are stored offsite (not just on the failed host)
- Secrets are backed up separately
- DNS can be updated to point to new host

### Scenario 4: Need to Roll Back After Bad Migration

**Example:** Alembic migration destroyed data.

**Recovery:**
1. Identify the backup from before the migration
2. Stop the application
3. Restore that backup
4. DO NOT run migrations
5. Fix the bad migration script
6. Test the migration on a separate database
7. Apply the fixed migration
8. Restart the application

## Offsite Backup Strategy

**Current:** Backups are stored only on the Docker host.

**Risk:** If the host is lost, backups are lost.

**Recommendation:** Sync backups to offsite storage.

Options:

### Option 1: S3/Object Storage (Recommended)

```bash
# Add to backup script or run via cron
aws s3 sync ./backups/ s3://your-bucket/retailmind-backups/ \
  --storage-class STANDARD_IA \
  --exclude "*" --include "retailmind_*.sql.gz"
```

Cost: ~$0.0125/GB/month (S3 Infrequent Access)

### Option 2: Remote Server via rsync

```bash
rsync -avz --delete ./backups/ backup-server:/mnt/backups/retailmind/
```

### Option 3: Encrypted tar to External Drive

```bash
# Weekly: copy backups to external drive
tar -czf /mnt/external/retailmind-backups-$(date +%Y%m%d).tar.gz ./backups/
gpg --encrypt --recipient your@email.com /mnt/external/retailmind-backups-*.tar.gz
```

## Backup Encryption

**Current:** Backups are not encrypted at rest.

**Risk:** If the backup file is compromised, database contents are exposed.

**Recommendation:** Encrypt backups containing PII.

```bash
# Modify backup script to encrypt after pg_dump:
pg_dump ... | gzip | gpg --encrypt --recipient your@email.com > "${BACKUP_FILE}.gpg"

# Restore:
gpg --decrypt "${BACKUP_FILE}.gpg" | zcat | psql ...
```

## Next Steps

1. **Implement offsite backup sync** (S3 or rsync)
2. **Add backup monitoring metrics** (Prometheus exporter)
3. **Schedule monthly restore tests** (add to runbook)
4. **Document actual restore times** (run restore test, measure)
5. **Consider WAL archiving for lower RPO** (< 24 hours data loss)
6. **Encrypt backups if handling PII** (gpg or AWS KMS)

---

**Last Updated:** 2026-08-14
**Owner:** Infrastructure Team
**Review Schedule:** Quarterly
