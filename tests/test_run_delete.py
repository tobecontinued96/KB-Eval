"""Tests for the run-deletion helper and DELETE /api/runs/{run_id} route.

Drives the new :class:`DBStore` against an in-memory SQLite (via
``tests._db_fixture.make_db_store``) so the SQL is exercised end-to-end.
The on-disk artifacts (results.jsonl / results.csv / console.log) are real
files under ``tmp/reports/<run_id>/``; the soft-delete + file-backup
ordering is asserted against both the SQL columns and the real filesystem.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Force-override (not setdefault) so a real DATABASE_URL inherited
# from the user's shell environment does not bleed into the tests.
os.environ["DIFY_KB_EVAL_TEST_MODE"] = "1"
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

import backend.app as app_module  # noqa: E402
from backend.db.session import get_session_factory  # noqa: E402
from sqlalchemy import select  # noqa: E402
from backend.db.models import Run  # noqa: E402
from tests._db_fixture import make_db_store  # noqa: E402


def _create(store, *, run_id: str, status: str = "completed", config: dict | None = None):
    cfg = {
        "dify_base_url": "http://localhost/v1",
        "dataset_id": "ds-1",
        "eval_file": "eval.jsonl",
        "top_k": 5,
        "include_alternatives": False,
        "limit": 0,
        "sample_ids": [],
    }
    if config:
        cfg.update(config)
    store.create_run(run_id=run_id, name=f"name-{run_id}", config=cfg)
    if status != "queued":
        store.update_manifest(run_id, status=status)


class DeleteRunTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.reports_root = Path(self._tmp.name) / "reports"
        self.store = make_db_store(self.reports_root)
        self.run_id = "20260615-180000-test"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # ---- tests ----

    def test_delete_run_removes_directory_and_creates_backup(self) -> None:
        _create(self.store, run_id=self.run_id, status="completed")
        run_dir = self.reports_root / self.run_id
        (run_dir / "console.log").write_text("hello\n", encoding="utf-8")

        result = self.store.delete_run(self.run_id)
        self.assertFalse(run_dir.exists(), "live artifact dir should be gone")
        backup = Path(result["backup_path"])
        self.assertTrue(backup.exists(), f"backup dir should exist at {backup}")
        self.assertTrue((backup / "console.log").exists())
        self.assertEqual(result["id"], self.run_id)
        self.assertEqual(result["status"], "completed")

        from sqlalchemy import select

        from backend.db.models import Run
        from backend.db.session import get_session_factory

        with get_session_factory()() as session:
            row = session.execute(
                select(Run).where(Run.id == self.run_id)
            ).scalar_one()
        self.assertIsNotNone(row.deleted_at)
        self.assertEqual(row.deleted_backup_path, str(backup))

    def test_delete_run_cancels_running_run(self) -> None:
        """Commit 4 changed the behaviour: a DELETE on a
        ``running`` row transitions it to ``status='canceled'``
        instead of raising. The artifact directory is left in
        place so the runner subprocess can finish its current
        write before a follow-up DELETE cleans up."""

        _create(self.store, run_id=self.run_id, status="running")
        result = self.store.delete_run(self.run_id)
        self.assertEqual(result["status"], "canceled")
        # Artifact directory still exists — the runner is the one
        # that removes it after observing the cancel.
        self.assertTrue((self.reports_root / self.run_id).exists())
        # And the row now reflects the transition.
        with get_session_factory()() as session:
            row = session.execute(
                select(Run).where(Run.id == self.run_id)
            ).scalar_one()
        self.assertEqual(row.status, "canceled")
        self.assertIsNotNone(row.finished_at)
        self.assertEqual(row.error, "user canceled")
        # ``deleted_at`` is NOT set — that's set on the follow-up
        # DELETE that cleans up the directory.
        self.assertIsNone(row.deleted_at)

    def test_delete_run_cancels_queued_run(self) -> None:
        """Same as above but for ``status='queued'`` (a freshly
        created run that the runner hasn't claimed yet)."""

        _create(self.store, run_id=self.run_id, status="queued")
        result = self.store.delete_run(self.run_id)
        self.assertEqual(result["status"], "canceled")
        self.assertTrue((self.reports_root / self.run_id).exists())

    def test_delete_run_is_idempotent_when_missing(self) -> None:
        result = self.store.delete_run("not-a-real-run")
        self.assertEqual(result["id"], "not-a-real-run")
        self.assertEqual(result["status"], "missing")
        self.assertIsNone(result["backup_path"])

    def test_delete_run_route_is_idempotent_for_missing(self) -> None:
        with patch.object(app_module, "store", self.store):
            response = app_module.delete_run_route("not-a-real-run")
        # Success path returns a Pydantic model (FastAPI wraps it in 200).
        self.assertEqual(response.id, "not-a-real-run")
        self.assertEqual(response.status, "missing")
        self.assertIsNone(response.backup_path)

    def test_delete_run_route_cancels_running_run(self) -> None:
        """HTTP route: DELETE on a running run returns 200 with
        ``status='canceled'`` (not a 400 error). The frontend uses
        this to update the row's status badge immediately while
        the runner cleans up the directory in the background.

        ``delete_run_route`` returns a :class:`DeleteRunResponse`
        Pydantic model — FastAPI wraps it in a 200 OK response at
        the HTTP boundary. The Pydantic model itself doesn't
        carry a ``status_code`` attribute; we check ``status``
        (the field) instead."""

        _create(self.store, run_id=self.run_id, status="running")
        with patch.object(app_module, "store", self.store):
            response = app_module.delete_run_route(self.run_id)
        self.assertEqual(response.status, "canceled")
        # Directory is not removed yet.
        self.assertTrue((self.reports_root / self.run_id).exists())

    def test_delete_run_completed_still_works(self) -> None:
        """Pre-commit-4 path: a DELETE on a completed run still
        backs up + soft-deletes + removes the directory."""

        _create(self.store, run_id=self.run_id, status="completed")
        with self.reports_root.joinpath(self.run_id, "results.jsonl").open(
            "w", encoding="utf-8"
        ) as fh:
            fh.write("{}\n")
        result = self.store.delete_run(self.run_id)
        self.assertEqual(result["status"], "completed")
        self.assertIsNotNone(result["backup_path"])
        self.assertFalse((self.reports_root / self.run_id).exists())
        # Backup directory exists.
        self.assertTrue(Path(result["backup_path"]).exists())

    def test_list_runs_hides_soft_deleted_runs(self) -> None:
        _create(self.store, run_id=self.run_id, status="completed")
        items_before, total_before = self.store.list_runs()
        self.assertEqual(total_before, 1)
        self.assertEqual(items_before[0]["id"], self.run_id)

        self.store.delete_run(self.run_id)
        items_after, total_after = self.store.list_runs()
        self.assertEqual(total_after, 0)
        self.assertEqual(items_after, [])


if __name__ == "__main__":
    unittest.main()
