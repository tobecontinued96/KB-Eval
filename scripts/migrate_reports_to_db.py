"""One-shot migration: walk ``reports/`` and copy each run into PostgreSQL.

Usage:
    uv run python scripts/migrate_reports_to_db.py
    uv run python scripts/migrate_reports_to_db.py --reports-root reports
    uv run python scripts/migrate_reports_to_db.py --database-url postgresql+psycopg://...

The script is **idempotent**: re-running it is safe (the inserts catch
``IntegrityError`` and skip duplicates). It exits 0 on a clean migration,
2 when the filesystem count and DB count disagree, and 1 on connection
error.

Run this **once** before switching ``backend.app`` to use ``DBStore``.
The data on disk remains untouched (it's the on-disk backup).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _parse_iso(value: str | None) -> Any:
    import datetime as dt

    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None


def _is_backup_dir(name: str) -> bool:
    return name.endswith(".deleted") or ".deleted-" in name


def _count_live_runs(engine: Any) -> int:
    from sqlalchemy import text

    with engine.connect() as conn:
        result = conn.execute(
            text("SELECT COUNT(*) FROM runs WHERE deleted_at IS NULL")
        )
        return int(result.scalar_one())


def _migrate_one(
    session: Any,
    run_dir: Path,
) -> str:
    from sqlalchemy.exc import IntegrityError

    from backend.db.models import Run, RunReport, RunSummary

    manifest_path = run_dir / "manifest.json"
    if not manifest_path.exists():
        return "skipped (no manifest.json)"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return f"skipped (manifest.json unreadable: {exc})"
    if not isinstance(manifest, dict):
        return "skipped (manifest.json is not an object)"

    run_id = str(manifest.get("id") or run_dir.name)

    summary_path = run_dir / "summary.json"
    summary_obj: dict[str, Any] = {}
    if summary_path.exists():
        try:
            parsed = json.loads(summary_path.read_text(encoding="utf-8"))
            if isinstance(parsed, dict):
                summary_obj = parsed
        except (OSError, json.JSONDecodeError):
            pass

    report_path = run_dir / "report.md"
    report_text = ""
    if report_path.exists():
        try:
            report_text = report_path.read_text(encoding="utf-8")
        except OSError:
            pass

    sample_ids = manifest.get("sample_ids") or []
    if not isinstance(sample_ids, list):
        sample_ids = list(sample_ids)
    metrics = manifest.get("metrics") or {}
    if not isinstance(metrics, dict):
        metrics = {}
    progress = manifest.get("progress") or {}
    if not isinstance(progress, dict):
        progress = {}

    row = Run(
        id=run_id,
        name=str(manifest.get("name") or ""),
        status=str(manifest.get("status") or "unknown"),
        created_at=_parse_iso(manifest.get("created_at")),
        started_at=_parse_iso(manifest.get("started_at")),
        finished_at=_parse_iso(manifest.get("finished_at")),
        duration_ms=(
            int(manifest["duration_ms"])
            if isinstance(manifest.get("duration_ms"), int)
            else None
        ),
        dify_base_url=str(manifest.get("dify_base_url") or ""),
        dataset_id=str(manifest.get("dataset_id") or ""),
        eval_file=str(manifest.get("eval_file") or ""),
        top_k=int(manifest.get("top_k") or 5),
        include_alternatives=bool(manifest.get("include_alternatives", False)),
        limit=int(manifest.get("limit") or 0),
        sample_ids=list(sample_ids),
        timeout_seconds=(
            int(manifest["timeout_seconds"])
            if isinstance(manifest.get("timeout_seconds"), int)
            else None
        ),
        sample_count=int(manifest.get("sample_count") or 0),
        query_count=int(manifest.get("query_count") or 0),
        progress=dict(progress),
        metrics=dict(metrics),
        langsmith_url=manifest.get("langsmith_url"),
        error=str(manifest.get("error") or ""),
    )

    try:
        session.add(row)
        session.flush()
    except IntegrityError:
        session.rollback()
        return "skipped (already in DB)"

    summary = RunSummary(
        run_id=run_id,
        top_k=int(summary_obj.get("top_k") or row.top_k or 5),
        ks=list(summary_obj.get("ks") or []),
        overall=dict(summary_obj.get("overall") or {}),
        by_scenario_type=dict(summary_obj.get("by_scenario_type") or {}),
    )
    session.add(summary)

    report = RunReport(run_id=run_id, content=report_text or "")
    session.add(report)

    return "inserted"


def _run(args: argparse.Namespace) -> int:
    # Load .env (if present) before reading DATABASE_URL.
    try:
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env")
    except ImportError:
        pass

    if args.database_url:
        os.environ["DATABASE_URL"] = args.database_url

    reports_root = Path(args.reports_root).resolve()
    if not reports_root.exists():
        print(f"ERROR: reports root not found: {reports_root}", file=sys.stderr)
        return 1

    from backend.db import models  # noqa: F401
    from backend.db.base import Base
    from backend.db.session import get_engine, get_session_factory

    # Force the test-mode swap OFF — this script always targets real PG.
    os.environ.pop("DIFY_KB_EVAL_TEST_MODE", None)
    engine = get_engine()
    try:
        Base.metadata.create_all(bind=engine)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: cannot connect to PG / create tables: {exc}", file=sys.stderr)
        return 1

    live_dirs = [
        child
        for child in reports_root.iterdir()
        if child.is_dir() and not _is_backup_dir(child.name)
    ]
    pre_count = len(live_dirs)
    print(f"Found {pre_count} live run dirs in {reports_root}")

    sm = get_session_factory()
    inserted = 0
    skipped = 0
    with sm() as session:
        for run_dir in live_dirs:
            try:
                result = _migrate_one(session, run_dir)
                if result == "inserted":
                    session.commit()
                    inserted += 1
                else:
                    session.rollback()
                    skipped += 1
            except Exception as exc:  # noqa: BLE001
                print(f"  ! {run_dir.name}: error: {exc}")
                session.rollback()
                skipped += 1

    db_count = _count_live_runs(engine)
    print(f"Inserted: {inserted}, skipped: {skipped}, DB total: {db_count}")

    if pre_count != db_count:
        print(
            f"WARNING: filesystem has {pre_count} live runs, DB has {db_count}. "
            "Inspect the differences before switching the backend to DBStore.",
            file=sys.stderr,
        )
        return 2
    print(f"OK: migrated {db_count} runs")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--reports-root",
        default=str(ROOT / "reports"),
        help="Path to the reports/ directory to migrate (default: <repo>/reports)",
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help="SQLAlchemy URL (overrides DATABASE_URL env var)",
    )
    args = parser.parse_args()
    raise SystemExit(_run(args))


if __name__ == "__main__":
    main()
