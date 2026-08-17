#!/usr/bin/env bash
#
# Automated Postgres backup via pg_dump.
#
# Creates timestamped backups with automatic rotation based on retention policy.
# Backups are compressed and include schema + data.
#
# Usage:
#   ./scripts/backup-postgres.sh [backup_dir]
#
# Environment:
#   RM_DB_HOST      - Database host (default: postgres)
#   RM_DB_NAME      - Database name (required)
#   RM_DB_USER      - Database user (required)
#   PGPASSWORD      - Database password (required, or via password file)
#   BACKUP_DIR      - Where backups are written (default: ./backups)
#   RETENTION_DAYS  - How long to keep backups (default: 30)

set -euo pipefail

# ── Configuration ────────────────────────────────────────────────────

DB_HOST="${RM_DB_HOST:-postgres}"
DB_NAME="${RM_DB_NAME:?RM_DB_NAME must be set}"
DB_USER="${RM_DB_USER:?RM_DB_USER must be set}"
BACKUP_DIR="${1:-${BACKUP_DIR:-./backups}}"
RETENTION_DAYS="${RETENTION_DAYS:-30}"

TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
BACKUP_FILE="${BACKUP_DIR}/retailmind_${DB_NAME}_${TIMESTAMP}.sql.gz"

# ── Pre-flight ───────────────────────────────────────────────────────

if [[ -z "${PGPASSWORD:-}" ]]; then
  if [[ -f "/run/secrets/db_password" ]]; then
    PGPASSWORD="$(cat /run/secrets/db_password)"
    export PGPASSWORD
  else
    echo "ERROR: PGPASSWORD not set and /run/secrets/db_password does not exist" >&2
    exit 1
  fi
fi

mkdir -p "${BACKUP_DIR}"

# ── Backup ───────────────────────────────────────────────────────────

echo "Starting backup: ${BACKUP_FILE}"
echo "Database: ${DB_USER}@${DB_HOST}/${DB_NAME}"

# pg_dump options:
#   --no-owner, --no-acl: ownership and grants are environment-specific
#   --clean: add DROP statements for restore over existing schema
#   --if-exists: don't error if DROP targets don't exist
#   --create: include CREATE DATABASE statement
#   --format=plain: SQL text, compressible and readable

pg_dump \
  --host="${DB_HOST}" \
  --username="${DB_USER}" \
  --dbname="${DB_NAME}" \
  --no-owner \
  --no-acl \
  --clean \
  --if-exists \
  --create \
  --format=plain \
  | gzip > "${BACKUP_FILE}"

BACKUP_SIZE="$(du -h "${BACKUP_FILE}" | cut -f1)"
echo "Backup complete: ${BACKUP_FILE} (${BACKUP_SIZE})"

# ── Rotation ─────────────────────────────────────────────────────────

# Delete backups older than RETENTION_DAYS.
# -mtime +N matches files modified more than N days ago.

DELETED_COUNT=0
while IFS= read -r old_backup; do
  echo "Deleting expired backup: ${old_backup}"
  rm -f "${old_backup}"
  ((DELETED_COUNT++))
done < <(find "${BACKUP_DIR}" -name "retailmind_*.sql.gz" -type f -mtime +"${RETENTION_DAYS}")

if [[ ${DELETED_COUNT} -gt 0 ]]; then
  echo "Deleted ${DELETED_COUNT} backup(s) older than ${RETENTION_DAYS} days"
else
  echo "No expired backups to delete (retention: ${RETENTION_DAYS} days)"
fi

# ── Verification ─────────────────────────────────────────────────────

# Verify the backup is a valid gzip file containing SQL.
# Does not restore it (that is a separate test), just checks it is not corrupted.

if gzip -t "${BACKUP_FILE}" 2>/dev/null; then
  # `head -n 20` and `grep -q` both stop reading as soon as they're
  # satisfied, which sends SIGPIPE back to zcat once it tries to write past
  # that point. Under `set -o pipefail` that SIGPIPE (128+13) is a nonzero
  # exit status for zcat, and pipefail reports the pipeline as failed even
  # though grep found its match — meaning this check failed on every
  # successful backup, unconditionally, until this was disabled locally.
  set +o pipefail
  zcat "${BACKUP_FILE}" | head -n 20 | grep -q "PostgreSQL database dump"
  HAS_DUMP_HEADER=$?
  set -o pipefail

  if [[ ${HAS_DUMP_HEADER} -eq 0 ]]; then
    echo "Backup verification: OK (valid gzip, contains SQL dump)"
  else
    echo "WARNING: Backup file is valid gzip but does not look like a pg_dump" >&2
    exit 1
  fi
else
  echo "ERROR: Backup file is not a valid gzip" >&2
  exit 1
fi

# ── Summary ──────────────────────────────────────────────────────────

BACKUP_COUNT="$(find "${BACKUP_DIR}" -name "retailmind_*.sql.gz" -type f | wc -l | tr -d ' ')"
TOTAL_SIZE="$(du -sh "${BACKUP_DIR}" | cut -f1)"

echo ""
echo "Backup Summary"
echo "──────────────"
echo "Latest backup:  ${BACKUP_FILE}"
echo "Size:           ${BACKUP_SIZE}"
echo "Total backups:  ${BACKUP_COUNT}"
echo "Total size:     ${TOTAL_SIZE}"
echo "Retention:      ${RETENTION_DAYS} days"
