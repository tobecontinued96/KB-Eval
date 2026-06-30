-- Add runs.last_heartbeat_at for the concurrent-runner watchdog.
--
-- Background
-- ----------
-- Commit 1 of the concurrency refactor adds a new nullable column
-- ``runs.last_heartbeat_at TIMESTAMPTZ`` plus a covering composite
-- index ``ix_runs_status_last_heartbeat_at``. The column is updated
-- by the runner subprocess on every coalesced progress flush, and
-- the parent-process watchdog (commit 4) uses it to detect a stuck
-- runner: any ``status='running'`` row whose ``last_heartbeat_at`` is
-- older than ``RUNNER_WATCHDOG_TIMEOUT_SECONDS`` (default 300s) is
-- re-queued for re-claim.
--
-- Why this SQL file exists alongside the alembic revision
-- ------------------------------------------------------
-- ``backend/alembic/versions/0003_add_last_heartbeat_at.py`` is a
-- no-op stamp (matches the existing 0002_add_run_labels.py pattern)
-- because the column ships via ``Base.metadata.create_all`` inside
-- ``backend.db.session.init_db()`` on first backend start. For
-- environments where ``RUN_DB_BOOTSTRAP=false`` (i.e. alembic is the
-- sole source of truth for schema), the column would only arrive via
-- an actual ``ALTER TABLE`` — and this is that ALTER TABLE.
--
-- When to run
-- -----------
-- * If ``RUN_DB_BOOTSTRAP=true`` and you start the backend once, the
--   column is created automatically. This file is unnecessary.
-- * If ``RUN_DB_BOOTSTRAP=false``, run this script once:
--     psql "$DATABASE_URL" -f scripts/migrations/2026_06_22_add_run_last_heartbeat_at.sql
--   (after replacing the URL with the actual production URL — note
--    the ``asyncpg`` -> ``psycopg`` normalisation happens in
--    ``backend/db/session.py``, not here.)
-- * The ``IF NOT EXISTS`` guards make the script idempotent: re-running
--   it on a DB that already has the column is a no-op rather than an
--   error.

BEGIN;

ALTER TABLE runs
    ADD COLUMN IF NOT EXISTS last_heartbeat_at TIMESTAMPTZ;

-- Covering index for the watchdog query:
--   SELECT id FROM runs
--    WHERE status = 'running' AND last_heartbeat_at < :threshold
-- ``CREATE INDEX IF NOT EXISTS`` is supported on Postgres 9.5+; the
-- ``docker-compose.yml`` pins 16+, so this is always safe.
CREATE INDEX IF NOT EXISTS ix_runs_status_last_heartbeat_at
    ON runs (status, last_heartbeat_at);

COMMIT;
