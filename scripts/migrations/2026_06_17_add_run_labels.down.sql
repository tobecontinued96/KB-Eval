-- Rollback for 2026_06_17_add_run_labels.sql
--
-- Drops the two label columns added for run comparison grouping.
-- WARNING: this destroys any non-NULL embedding_model / rerank_model
-- values that users have entered on the run detail page since the up
-- migration ran. Only run this if you're sure no one cares about
-- those values (e.g. on a dev/staging DB right after a fresh apply).
--
-- Apply:
--   psql "$DATABASE_URL" -f scripts/migrations/2026_06_17_add_run_labels.down.sql

BEGIN;

ALTER TABLE runs DROP COLUMN IF EXISTS embedding_model;
ALTER TABLE runs DROP COLUMN IF EXISTS rerank_model;

COMMIT;
