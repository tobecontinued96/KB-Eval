-- Revert runs.last_heartbeat_at addition.
--
-- Mirror of 2026_06_22_add_run_last_heartbeat_at.sql. Use ONLY if you
-- need to roll back commit 1 of the concurrency refactor; otherwise
-- leave the column in place — it's nullable, costs ~16 bytes per
-- row, and SQLAlchemy ignores extra columns it doesn't know about.
--
-- Running order matters: drop the index first so the column drop
-- doesn't leave a dangling index reference behind.

BEGIN;

DROP INDEX IF EXISTS ix_runs_status_last_heartbeat_at;

ALTER TABLE runs
    DROP COLUMN IF EXISTS last_heartbeat_at;

COMMIT;
