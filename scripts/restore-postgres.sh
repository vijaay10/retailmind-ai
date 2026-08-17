#!/usr/bin/env bash
#
# Restore Postgres database from backup.
#
# **DESTRUCTIVE OPERATION** - drops and recreates the database.
# Use with caution. Requires confirmation.
#
# Usage:
#   ./scripts/restore-postgres.sh <backup_file>
#
# Environment:
#   RM_DB_HOST      - Database host (default: postgres)
#   RM_DB_NAME      - Database name (required)
#   RM_DB_USER      - Database user (required)
#   PGPASSWORD      - Database password (required, or via password file)
#   SKIP_CONFIRM    - Skip confirmation prompt (default: false, DANGEROUS)

set -euo pipefail

# ── Configuration ────────────────────────────────────────────────────

DB_HOST="${RM_DB_HOST:-postgres}"
DB_NAME="${RM_DB_NAME:?RM_DB_NAME must be set}"
DB_USER="${RM_DB_USER:?RM_DB_USER must be set}"
SKIP_CONFIRM="${SKIP_CONFIRM:-false}"

BACKUP_FILE="${1:-}"

# ── Pre-flight ───────────────────────────────────────────────────────

if [[ -z "${BACKUP_FILE}" ]]; then
  echo "Usage: $0 <backup_file>" >&2
  echo "" >&2
  echo "Example:" >&2
  echo "  $0 ./backups/retailmind_retailmind_20260814_120000.sql.gz" >&2
  exit 1
fi

if [[ ! -f "${BACKUP_FILE}" ]]; then
  echo "ERROR: Backup file not found: ${BACKUP_FILE}" >&2
  exit 1
fi

if ! gzip -t "${BACKUP_FILE}" 2>/dev/null; then
  echo "ERROR: Backup file is not a valid gzip: ${BACKUP_FILE}" >&2
  exit 1
fi

if [[ -z "${PGPASSWORD:-}" ]]; then
  if [[ -f "/run/secrets/db_password" ]]; then
    PGPASSWORD="$(cat /run/secrets/db_password)"
    export PGPASSWORD
  else
    echo "ERROR: PGPASSWORD not set and /run/secrets/db_password does not exist" >&2
    exit 1
  fi
fi

# ── Confirmation ─────────────────────────────────────────────────────

echo "──────────────────────────────────────────────────"
echo "RESTORE DATABASE FROM BACKUP"
echo "──────────────────────────────────────────────────"
echo ""
echo "This operation will:"
echo "  1. DROP the existing database: ${DB_NAME}"
echo "  2. CREATE a new database from backup"
echo "  3. RESTORE all data from: $(basename "${BACKUP_FILE}")"
echo ""
echo "Target:"
echo "  Host:     ${DB_HOST}"
echo "  Database: ${DB_NAME}"
echo "  User:     ${DB_USER}"
echo ""
echo "Backup:"
echo "  File: ${BACKUP_FILE}"
echo "  Size: $(du -h "${BACKUP_FILE}" | cut -f1)"
# `date -r` reads a file's mtime on both BSD (macOS) and GNU (Linux) date —
# unlike `stat`, whose -f flag means "custom format" on BSD but "filesystem
# info" on GNU, so a `stat -f ... || stat -c ...` fallback never reaches the
# GNU branch: GNU `stat -f` "succeeds" with the wrong (filesystem) output
# instead of erroring.
echo "  Date: $(date -r "${BACKUP_FILE}" "+%Y-%m-%d %H:%M:%S" 2>/dev/null || echo unknown)"
echo ""
echo "──────────────────────────────────────────────────"
echo "WARNING: THIS WILL DELETE ALL CURRENT DATA"
echo "──────────────────────────────────────────────────"
echo ""

if [[ "${SKIP_CONFIRM}" != "true" ]]; then
  read -r -p "Type 'yes' to proceed: " confirmation
  if [[ "${confirmation}" != "yes" ]]; then
    echo "Restore cancelled." >&2
    exit 1
  fi
fi

# ── Restore ──────────────────────────────────────────────────────────

echo ""
echo "Starting restore from: ${BACKUP_FILE}"

# The backup includes DROP DATABASE and CREATE DATABASE statements (--clean --create),
# so we restore to the postgres maintenance database and let the SQL handle it.
#
# No --single-transaction: DROP DATABASE / CREATE DATABASE cannot run inside
# a transaction block, and this dump always contains both — psql fails
# immediately with "DROP DATABASE cannot run inside a transaction block" if
# you wrap it in one. --set ON_ERROR_STOP=on is what actually protects us:
# any statement failure aborts the whole restore instead of pressing on.

zcat "${BACKUP_FILE}" | psql \
  --host="${DB_HOST}" \
  --username="${DB_USER}" \
  --dbname=postgres \
  --set ON_ERROR_STOP=on \
  --quiet

echo "Restore complete."

# ── Verification ─────────────────────────────────────────────────────

# Basic smoke test: count tables in the public schema.

TABLE_COUNT=$(psql \
  --host="${DB_HOST}" \
  --username="${DB_USER}" \
  --dbname="${DB_NAME}" \
  --tuples-only \
  --no-align \
  --command="SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE'")

echo ""
echo "Verification"
echo "────────────"
echo "Tables restored: ${TABLE_COUNT}"

if [[ ${TABLE_COUNT} -eq 0 ]]; then
  echo "WARNING: No tables found in public schema after restore" >&2
  exit 1
fi

# Check that alembic_version exists and has a current revision.
ALEMBIC_VERSION=$(psql \
  --host="${DB_HOST}" \
  --username="${DB_USER}" \
  --dbname="${DB_NAME}" \
  --tuples-only \
  --no-align \
  --command="SELECT version_num FROM alembic_version LIMIT 1" 2>/dev/null || echo "NOT_FOUND")

if [[ "${ALEMBIC_VERSION}" == "NOT_FOUND" ]]; then
  echo "WARNING: alembic_version table not found or empty" >&2
else
  echo "Alembic revision: ${ALEMBIC_VERSION}"
fi

echo ""
echo "Restore successful. Database: ${DB_NAME} @ ${DB_HOST}"
echo ""
echo "Next steps:"
echo "  1. Verify application can connect"
echo "  2. Run smoke tests"
echo "  3. Check critical tables have expected row counts"
