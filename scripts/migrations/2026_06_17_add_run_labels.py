"""Apply the 2026_06_17_add_run_labels migration in a single command.

What it does
------------
Adds ``embedding_model`` / ``rerank_model`` VARCHAR(128) columns to the
``runs`` table. Both are nullable, so existing rows are unaffected (they
just read as NULL, which the compare endpoint already normalises to
``"(空)"``).

Why this is needed
------------------
The 2026-06-15 commit c44a513 added the two columns to
``backend/db/models.py`` so the new "compare runs by config" feature can
group runs by ``(embedding, rerank)``. The SQLAlchemy model is in place
but the actual PostgreSQL schema on production was never ``ALTER``ed,
so the columns are missing. At runtime that surfaces as
``psycopg.errors.UndefinedColumn: column runs.embedding_model does not
exist`` on every read / write, which makes both the historical
"embedding / rerank" labels on the run detail page and the new
``POST /api/runs/{id}/labels`` endpoint return NULL / 500.

Usage
-----

    # Show what would run, no DB writes (default)
    python scripts/migrations/2026_06_17_add_run_labels.py

    # Actually apply
    python scripts/migrations/2026_06_17_add_run_labels.py --apply

    # Roll back (drops the two columns)
    python scripts/migrations/2026_06_17_add_run_labels.py --rollback --apply

    # Override the DB (otherwise honours $DATABASE_URL from the env, like
    # the rest of the backend does)
    python scripts/migrations/2026_06_17_add_run_labels.py --database-url postgresql://...

The script honours the same ``DATABASE_URL`` resolution rules as
``backend/db/session.py`` (env var first, then ``.env`` in the project
root), so the same env you use to run the backend is the one this
script will connect to.

Safety
------
- Uses ``ADD COLUMN IF NOT EXISTS`` / ``DROP COLUMN IF EXISTS``, so it's
  idempotent and safe to re-run.
- The Python wrapper defaults to **dry-run** and prints the SQL that
  would run. You must pass ``--apply`` to actually execute.
- Prints a "before / after" of the relevant columns via
  ``information_schema.columns`` so you can see the state change.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

# Make ``backend.*`` importable when run as a plain script.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

# Reuse the project's DATABASE_URL resolution so we never silently
# connect to the wrong DB.
from backend.db.session import _try_load_dotenv, _normalize_url  # type: ignore  # noqa: E402

UP_STATEMENTS = (
    'ALTER TABLE runs ADD COLUMN IF NOT EXISTS embedding_model VARCHAR(128);',
    'ALTER TABLE runs ADD COLUMN IF NOT EXISTS rerank_model   VARCHAR(128);',
)

DOWN_STATEMENTS = (
    'ALTER TABLE runs DROP COLUMN IF EXISTS embedding_model;',
    'ALTER TABLE runs DROP COLUMN IF EXISTS rerank_model;',
)


def _resolve_database_url() -> str:
    """Mirror ``backend.db.session.get_engine``'s resolution: env first,
    then ``.env`` in the project root. Returns the sync ``postgresql+psycopg://``
    form (or whatever the user set) — ``psycopg.connect`` understands
    both ``postgresql://`` and ``postgresql+psycopg://`` so we just hand
    it back as-is.
    """
    _try_load_dotenv()
    return os.environ.get("DATABASE_URL", "")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="Actually run the SQL (default: dry-run, just prints).")
    parser.add_argument(
        "--rollback",
        action="store_true",
        help="Drop the columns instead of adding them. Used to undo the migration.",
    )
    parser.add_argument(
        "--database-url",
        help="Override $DATABASE_URL. Defaults to the same resolution as the backend.",
    )
    return parser.parse_args()


def _mask_url(url: str) -> str:
    """Hide the password in ``postgresql://user:pass@host/db`` for printing."""
    try:
        parsed = urlparse(url)
    except Exception:
        return url
    if parsed.password is None:
        return url
    netloc = parsed.netloc.replace(f":{parsed.password}@", ":***@")
    return parsed._replace(netloc=netloc).geturl()


def _connect(database_url: str):
    import psycopg  # type: ignore

    return psycopg.connect(database_url, autocommit=False)


def _show_columns(conn, label: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT column_name, data_type, character_maximum_length, is_nullable
            FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = 'runs'
              AND column_name IN ('embedding_model', 'rerank_model')
            ORDER BY column_name
            """
        )
        rows = cur.fetchall()
    print(f"  [{label}] runs.embedding_model / rerank_model columns:")
    if not rows:
        print("    (none — both columns missing)")
        return
    for name, dtype, length, nullable in rows:
        size = f"({length})" if length else ""
        print(f"    {name}: {dtype}{size} nullable={nullable}")


def main() -> int:
    args = _parse_args()
    statements = DOWN_STATEMENTS if args.rollback else UP_STATEMENTS
    direction = "ROLLBACK" if args.rollback else "UP"
    mode = "APPLY" if args.apply else "DRY-RUN"

    database_url = args.database_url or _resolve_database_url()
    if not database_url:
        print("ERROR: DATABASE_URL is not set and could not be resolved from .env.", file=sys.stderr)
        return 2
    # 同样的 URL 可能是 asyncpg / psycopg 形式（项目用 SQLAlchemy async 入口），
    # psycopg 同步驱动只认纯 postgresql://，过一遍 normalize 再剥掉 +psycopg 后缀。
    database_url = _normalize_url(database_url).replace("postgresql+psycopg://", "postgresql://")

    print("== migration 2026_06_17_add_run_labels ==")
    print(f"  direction : {direction}")
    print(f"  mode      : {mode}")
    print(f"  database  : {_mask_url(database_url)}")
    print("  statements:")
    for stmt in statements:
        print(f"    {stmt}")

    conn = _connect(database_url)
    try:
        # Always show "before" so the user can see the current state.
        _show_columns(conn, "BEFORE")

        if not args.apply:
            print()
            print("Dry-run only. Pass --apply to execute.")
            return 0

        with conn.cursor() as cur:
            for stmt in statements:
                cur.execute(stmt)
        conn.commit()
        print()
        print("Migration applied.")
        _show_columns(conn, "AFTER")
    except Exception as exc:
        conn.rollback()
        print(f"ERROR: migration failed: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
