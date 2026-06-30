# scripts/migrations/ — LEGACY one-off migration scripts

## Status: superseded by Alembic

The Alembic scaffold landed in `backend/alembic/` on 2026-06-22.
After that point, **all schema changes go through Alembic** —
`alembic revision -m "..."` then commit the generated file under
`backend/alembic/versions/`. Do not add new files to this directory.

## What's still here (and why we keep it)

| File | Superseded by | Notes |
| --- | --- | --- |
| `2026_06_17_add_run_labels.sql` | `backend/alembic/versions/0002_add_run_labels.py` | Identical DDL, kept on disk so anyone who already ran the raw `psql -f ...` on a long-lived DB has a paper trail. |
| `2026_06_17_add_run_labels.down.sql` | (use `alembic downgrade -1`) | Manual rollback is now `alembic downgrade 0001_baseline` — but that only rewinds the *version row*, it doesn't drop the columns. To actually drop them, run this `.down.sql` by hand. |
| `2026_06_17_add_run_labels.py` | (use `alembic upgrade head`) | The Python wrapper is the legacy invocation path. If you were using it in a deployment script, switch to `alembic upgrade head` and drop this script. |

## Onboarding to Alembic — both flavours of existing DB

**Why both flavours converge to the same command:** revision 0001
and 0002 are both empty on the upgrade path. They exist only to
record "this DB is managed by Alembic, and it's already at the
schema that `Base.metadata.create_all` (inside `init_db()`) plus
the legacy SQL would produce". So `alembic upgrade head` from any
state that already has the tables and columns is a pure version-row
bump — no DDL, no risk.

### Case A — DB that went through `backend.db.session.init_db()`

i.e. the backend booted at least once with `RUN_DB_BOOTSTRAP=true`.
The tables (and `runs.embedding_model` / `runs.rerank_model`) are
already present because `Base.metadata.create_all` ran against the
current models.

```bash
DATABASE_URL=... uv run alembic upgrade head
# INFO ... Running upgrade  -> 0001_baseline, ...
# INFO ... Running upgrade 0001_baseline -> 0002_add_run_labels, ...
```

That's it. No `alembic stamp`, no manual ALTER.

### Case B — DB that was patched by the legacy `.py` / `.sql` script

i.e. `init_db()` was bypassed (or ran before commit c44a513 added
the column declarations) and the columns were added by
`scripts/migrations/2026_06_17_add_run_labels.py --apply`.

The columns are already on disk, but `alembic_version` doesn't
exist yet, so `alembic upgrade head` is also the right command
— same reasoning as Case A.

```bash
DATABASE_URL=... uv run alembic upgrade head
```

If the legacy script added the columns but the *tables* themselves
are missing for some reason (very unlikely — `create_all` has been
running since day one), `alembic upgrade head` will fail with
`no such table: runs`. In that case run the backend once with
`RUN_DB_BOOTSTRAP=true` first, *then* `alembic upgrade head`.

### Case C — fresh DB, nothing has ever touched it

This is the only path where the order matters.

```bash
# 1. Boot the backend once with bootstrap on so create_all builds
#    the tables (and the model-declared columns). Then stop it.
RUN_DB_BOOTSTRAP=true DATABASE_URL=... uv run uvicorn backend.app:app
# Ctrl-C once it says "Application startup complete."

# 2. Now alembic can stamp 0001 -> 0002 without DDL.
DATABASE_URL=... uv run alembic upgrade head
```

A future revision (`0000_create_initial_tables.py` or similar)
should automate case C by creating the tables explicitly, at which
point step 1 goes away. Until then, the dev-friendly
`RUN_DB_BOOTSTRAP=true` is the documented bootstrap.

## When can I delete this directory?

Once every documented environment has run `alembic upgrade head`
at least once AND nobody has the legacy `.py` wrapper in any CI /
cron / runbook, this directory can go. Until then it's a safety
net for anyone who remembers the old command but not the new one.
