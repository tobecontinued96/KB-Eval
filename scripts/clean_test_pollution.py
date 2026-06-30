"""One-shot cleanup: soft-delete test-leaked rows in the runs table.

Background
----------
Before the test isolation was tightened, ``python -m unittest discover``
ran with the user's shell environment (which already had ``DATABASE_URL``
pointing at the real Postgres via ``.env``). A handful of rows leaked
into the production DB, e.g. ``20260615-180000-test``,
``20260615-182033-review-gate-test``. This script soft-deletes them so
``GET /api/runs`` returns only the real, on-disk-backed history.

Safety
------
* Only matches IDs that are *clearly* test pollution:

  - IDs starting with ``review-gate-test`` or matching ``*-test`` /
    ``*-test-run`` suffixes.
  - IDs that contain the string ``test`` in the name (e.g.
    ``review-gate-test``, ``gate-test``).

* Uses **soft delete** (``deleted_at = NOW()``), never hard delete.
* Runs pre-flight + post-flight count checks; refuses to delete if
  more than ``--max-deletions`` rows would be affected (default 50).

Usage::

    uv run python scripts/clean_test_pollution.py
    uv run python scripts/clean_test_pollution.py --dry-run
    uv run python scripts/clean_test_pollution.py --max-deletions 5
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# Conservative patterns. We err on the side of *not* matching rather
# than nuking a real run.
# Matches run IDs whose last path segment is exactly ``test`` /
# ``review-gate-test`` (after a ``/`` or ``-`` separator), OR whose name
# contains the literal ``-test`` or ``_test`` suffix. The separator
# before ``test`` is required so that names like ``contest`` or
# ``latest`` are not matched.
TEST_PATTERN = re.compile(
    r"(?:^|[/\-_])(test|review-gate-test)(?:$|[/\-_])",
    re.IGNORECASE,
)


def _is_test_id(run_id: str) -> bool:
    return bool(TEST_PATTERN.search(run_id))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List what would be deleted, but do not modify the DB.",
    )
    parser.add_argument(
        "--max-deletions",
        type=int,
        default=50,
        help="Refuse to proceed if more than this many rows would be "
        "soft-deleted (safety belt; default 50).",
    )
    args = parser.parse_args()

    try:
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env")
    except ImportError:
        pass

    if not os.environ.get("DATABASE_URL"):
        print("ERROR: DATABASE_URL is not set. Copy .env.example to .env.", file=sys.stderr)
        return 1
    # Force real DB (not test mode).
    os.environ.pop("DIFY_KB_EVAL_TEST_MODE", None)

    from sqlalchemy import select, update

    from backend.db.models import Run
    from backend.db.session import get_session_factory

    factory = get_session_factory()
    with factory() as session:
        # Pre-flight: list candidates.
        candidate_ids = session.execute(
            select(Run.id).where(Run.deleted_at.is_(None))
        ).scalars().all()
    candidates = [cid for cid in candidate_ids if _is_test_id(cid)]

    if not candidates:
        print("OK: no test pollution rows found; nothing to do.")
        return 0

    if len(candidates) > args.max_deletions:
        print(
            f"ERROR: {len(candidates)} rows would be soft-deleted, which is "
            f"more than the --max-deletions safety limit of "
            f"{args.max_deletions}. Re-run with a higher limit if you really "
            f"mean it.",
            file=sys.stderr,
        )
        for cid in candidates:
            print(f"  candidate: {cid}", file=sys.stderr)
        return 2

    print(f"Found {len(candidates)} test pollution row(s):")
    for cid in candidates:
        print(f"  - {cid}")

    if args.dry_run:
        print("\n--dry-run: not modifying the DB.")
        return 0

    with factory() as session:
        with session.begin():
            result = session.execute(
                update(Run)
                .where(Run.id.in_(candidates), Run.deleted_at.is_(None))
                .values(
                    deleted_at=dt.datetime.now(dt.timezone.utc),
                    deleted_backup_path=None,
                )
            )
            print(f"\nSoft-deleted {result.rowcount} row(s).")

    with factory() as session:
        live = session.execute(
            select(Run.id).where(Run.deleted_at.is_(None))
        ).scalars().all()
        print(f"Remaining live runs: {len(live)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
