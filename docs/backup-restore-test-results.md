# Backup & Restore Testing Results

**Test date:** 2026-08-15
**Tester:** Prompt 10.5 remediation pass
**Environment:** Disposable `postgres:16.4` Docker containers, isolated from
the `rmprod-*` stack. No production container was started, stopped, or
connected to at any point in this test.

This document previously contained only a static code review ("script exists
and is executable", "no syntax errors") and concluded "APPROVED FOR
PRODUCTION" without ever running either script. That review is gone — replaced
below with what actually happened when the scripts were executed against real
data. The static review's conclusion was wrong: both scripts had bugs that
made them fail on every real invocation, described below.

---

## Method

1. Started a disposable `postgres:16.4` container (`rm-backup-test-pg`,
   port 15434, not exposed beyond the test), independent of `rmprod-postgres-1`.
2. Ran `alembic upgrade head` against it (all 5 migrations), then
   `app.infrastructure.db.seeds.reference` and `app.infrastructure.db.seeds.sample`
   — real schema, real seed data: 7 users, 5 metric configs, 1 tenant
   (`northwind-threads`), alert rules, recommendations, etc.
3. Copied `scripts/backup-postgres.sh` into the container (it has no local
   `pg_dump`/`psql` client on the host running this session) and ran it for
   real against the seeded database.
4. Copied `scripts/restore-postgres.sh` into the same container and ran it
   for real against the backup file it produced.
5. Verified table/row counts and the Alembic revision after restore.
6. Removed the container (`docker rm -f`) once testing was done.

---

## Result 1: Backup — FAILED on first run, FIXED, then PASSED

**First run:** `scripts/backup-postgres.sh` exited 1. `pg_dump` itself
succeeded (the `.sql.gz` file was written, 20K, valid gzip, and did contain a
real dump), but the script's own post-backup sanity check reported failure:

```
Backup complete: /tmp/backups/retailmind_retailmind_backup_test_20260815_182059.sql.gz (20K)
WARNING: Backup file is valid gzip but does not look like a pg_dump
```

**Root cause:** `zcat "${BACKUP_FILE}" | head -n 20 | grep -q "PostgreSQL database dump"`
under `set -euo pipefail`. `grep -q` (and `head -n 20`) stop reading as soon
as they're satisfied; the still-writing `zcat` then receives SIGPIPE and exits
141. Under `pipefail`, that counts as pipeline failure even though `grep`
found its match — so this check failed **on every successful backup,
unconditionally**, for any dump longer than 20 lines (i.e. always). This is
why backup had never been observed to succeed: it can't, as originally
written, regardless of whether the underlying `pg_dump` worked.

**Fix:** `scripts/backup-postgres.sh` — disable `pipefail` locally around
this one check, capture the pipeline's real (post-`grep`) exit status
explicitly, then re-enable it.

**Re-run after fix:**

```
Starting backup: /tmp/backups/retailmind_retailmind_backup_test_20260815_182232.sql.gz
Database: retailmind_app@localhost/retailmind_backup_test
Backup complete: /tmp/backups/retailmind_retailmind_backup_test_20260815_182232.sql.gz (20K)
No expired backups to delete (retention: 30 days)
Backup verification: OK (valid gzip, contains SQL dump)

Backup Summary
──────────────
Latest backup:  /tmp/backups/retailmind_retailmind_backup_test_20260815_182232.sql.gz
Size:           20K
Total backups:  1
Total size:     24K
Retention:      30 days
```

- **Backup succeeded:** yes (exit 0)
- **Backup size:** 20K (seed-scale data; production will be larger)
- **Elapsed time:** ~0.15s (seed-scale data, local disposable container)
- **Errors:** none, after the fix

---

## Result 2: Restore — FAILED twice on different bugs, FIXED, then PASSED

**First run** (before any fix): exited 3.

```
ERROR:  DROP DATABASE cannot run inside a transaction block
```

**Root cause:** `psql --single-transaction ...` wraps the whole restore in
`BEGIN; ... COMMIT;`, but the dump (produced with `pg_dump --clean --create`)
contains a top-level `DROP DATABASE IF EXISTS ...` / `CREATE DATABASE ...`,
and Postgres refuses to run either inside a transaction block, full stop.
Restore could not have worked as originally written for *any* backup taken
by the paired `backup-postgres.sh`, which always uses `--create`.

**Fix:** `scripts/restore-postgres.sh` — drop `--single-transaction`.
`--set ON_ERROR_STOP=on` is what actually protects the restore (abort on the
first failing statement); it was already present and is sufficient.

**Second run** (after fix 1, before fix 2): exited 0, but:

```
Verification
────────────
Tables restored: 0
WARNING: No tables found in public schema after restore
```

**Root cause:** a second, independent bug, exposed only once the first one
was fixed. `pg_dump --create` bakes the *source* database's name into both
the `CREATE DATABASE` statement and a `\connect <source-db-name>` directive
right after it. `psql` honors that embedded `\connect` and switches onto the
source-named database mid-script — **not** onto whatever database
`RM_DB_NAME` says the target is. Restoring `retailmind_backup_test`'s backup
while `RM_DB_NAME=retailmind_restore_test` created and populated
`retailmind_backup_test` (dropping and recreating it in place, correctly) and
left `retailmind_restore_test` empty. The script's own verification step then
correctly reported 0 tables — it queried the (untouched) target name, not the
database the SQL had actually written to.

**This is a real, load-bearing limitation, not something I patched over:**
restoring a `--create`-format dump into a database with a **different name**
than the one it was backed up from does not work, and can't without either
changing the dump format (`pg_restore`/custom format, which supports
`--dbname` remapping) or post-processing the SQL to rewrite the embedded
name — both are dump-format changes, which is an architectural trade-off
(simplicity of a single self-contained SQL file vs. rename-on-restore
flexibility) I'm not making unilaterally. Documented here and in the final
report instead of silently worked around.

**What *is* proven to work, and is the realistic disaster-recovery path**
(restore a backup back into a database with the same name it was taken
from — the actual DR scenario: prod's own backup, restored into prod after
data loss, same environment): dropped and recreated `retailmind_backup_test`
via its own backup, in place, in the same disposable container:

```
DROP DATABASE
CREATE DATABASE   (via the embedded pg_dump SQL, run by psql)
```

Verified after restore, querying `retailmind_backup_test` (the name the SQL
actually wrote to):

| Check | Before backup | After restore |
|---|---:|---:|
| Tables in `public` (base tables) | 45 | 45 |
| `app_user` rows | 7 | 7 |
| `alert_rule` rows | 5 | 5 |
| `alembic_version.version_num` | `0005_llm_usage_tracking` | `0005_llm_usage_tracking` |

- **Restore succeeded:** yes, for same-name restore (exit 0)
- **Restore succeeded for differently-named target:** no — documented
  limitation above, not fixed in this pass
- **Elapsed time:** ~0.5s (seed-scale data, local disposable container)
- **Data integrity:** exact match on every count checked above
- **Errors:** none, after both fixes, for the same-name case

---

## Result 3: Confirmation-prompt date display — minor bug, fixed

Unrelated to the restore's actual correctness, but found while reading the
confirmation banner output during testing:

```
Date: /tmp/backups/retailmind_retailmind_backup_test_...sql.gz a6914fcd1e67aad7 255 794c7630 4096 4096 118523922 ...
```

**Root cause:** `stat -f "%Sm" ... 2>/dev/null || stat -c "%y" ...` — on
macOS/BSD, `-f` means "custom format"; on GNU/Linux (the container, and any
Linux host), `-f` means "show filesystem info instead of file info". The
GNU branch doesn't error, so the `||` fallback never triggers, and the
confirmation banner shows raw filesystem statistics instead of the backup's
timestamp.

**Fix:** use `date -r "${BACKUP_FILE}" "+%Y-%m-%d %H:%M:%S"`, which reads a
file's mtime identically on both BSD and GNU `date`.

**Re-verified:** banner now shows `Date: 2026-08-15 18:22:32`, matching the
backup's actual creation time.

---

## What was NOT tested

- **Production backup/restore, against `rmprod-postgres-1`:** deliberately
  not touched, per explicit instruction. Nothing in this test connected to
  the production stack.
- **The cron schedule** in `compose.prod.yml`'s `backup` service (daily
  02:00 UTC): not exercised — would require running the profile for 24h+ or
  manually invoking `crond` inside that specific container, which this pass
  didn't do. Recommended as a follow-up, not a blocker (the underlying script
  it invokes is now proven to work).
- **Backup rotation past `RETENTION_DAYS`:** the rotation logic itself
  (`find ... -mtime +N`) wasn't exercised with artificially-aged files.
- **Restore into a differently-named database:** proven broken, not fixed —
  see Result 2.
- **Offsite backup (S3/rsync):** not implemented in this pass; still only a
  suggestion in `docs/backup-restore.md`, unchanged from before.

---

## Conclusion

| Item | Before this pass | After this pass |
|---|---|---|
| Backup script, real execution | Never run; would have failed every time | Runs and verifies correctly |
| Restore script, real execution | Never run; would have failed every time | Runs and verifies correctly for same-name restore |
| Restore into a renamed disposable DB | Untested, assumed to work | Tested; confirmed **not supported** by the current `--create` dump format |
| Confirmation banner backup date | Silently wrong on Linux | Correct on both BSD and Linux |

**Backup:** 🟢 PASS — executed for real, against real seeded data, verified.
**Restore (same database name):** 🟢 PASS — executed for real, verified
table/row counts and Alembic revision match exactly.
**Restore (renamed target database):** 🔴 NOT SUPPORTED — real, documented
limitation of the `--create`-format dump; not something this pass silently
worked around.
**Cron schedule / rotation / offsite:** ⚪ NOT TESTED — see above.

**Production readiness verdict:** the previous doc's blanket "✅ APPROVED FOR
PRODUCTION" conclusion was written without ever running either script,
and both scripts contained bugs that made them fail on every real
invocation. Both are now fixed and proven, for the disaster-recovery
scenario that actually matters in production (restoring a database's own
backup back into itself). Restoring into a renamed sandbox database — useful
for DR drills that don't want to touch the original name — does not work and
would need a dump-format change to support; flagged as follow-up work, not
silently claimed as done.
