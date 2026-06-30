-- Migration: add embedding_model / rerank_model label columns to runs
--
-- Background:
--   2026-06-15 PR "Run 加 embedding/rerank 模型标签字段" (c44a513) added these
--   two columns to backend/db/models.py so the new "compare runs by config"
--   feature can group runs by (embedding, rerank). The model declaration is
--   in place, but the actual PostgreSQL schema on production was never ALTERed,
--   so the columns are missing → all reads return NULL, all writes hit
--   "UndefinedColumn" at runtime.
--
-- This migration is idempotent (uses IF NOT EXISTS) and safe to re-run.
-- The columns are nullable; existing rows will simply get NULL, which the
-- compare endpoint already maps to "(空)" — no data backfill required.
--
-- Apply:
--   psql "$DATABASE_URL" -f scripts/migrations/2026_06_17_add_run_labels.sql
-- Rollback:
--   psql "$DATABASE_URL" -f scripts/migrations/2026_06_17_add_run_labels.down.sql

BEGIN;

ALTER TABLE runs ADD COLUMN IF NOT EXISTS embedding_model VARCHAR(128);
ALTER TABLE runs ADD COLUMN IF NOT EXISTS rerank_model   VARCHAR(128);

COMMIT;
