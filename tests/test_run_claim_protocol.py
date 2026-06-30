"""Unit tests for ``backend.services.runner_claim``.

Three SQL primitives are exercised end-to-end against the in-memory
SQLite backend (via ``tests._db_fixture.make_db_store``):

* ``claim_queued_runs``: claims ``status='queued'`` rows atomically
  and returns their IDs.
* ``requeue_stale_running_runs``: moves rows whose
  ``last_heartbeat_at`` is too old back to ``queued``.
* ``mark_run_canceled``: transitions a ``queued`` / ``running`` row
  to ``canceled`` (or refuses if already terminal).

We exercise the SQL directly (not the subprocess loop) so the test
suite can pin the contract without spinning up ``multiprocessing``.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timedelta, timezone

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Force the test environment BEFORE any backend import.
import tests._db_fixture  # noqa: F401, E402

from sqlalchemy import select  # noqa: E402

from backend.db.models import Run  # noqa: E402
from backend.db.session import get_session_factory  # noqa: E402
from backend.services.runner_claim import (  # noqa: E402
    claim_queued_runs,
    mark_run_canceled,
    requeue_stale_running_runs,
)


def _make_store():
    """Build a fresh DBStore against in-memory SQLite.

    Uses a module-scoped singleton for the SQLite engine (so the
    in-memory DB stays alive across multiple ``_make_store`` calls
    in the same test process). The ``reports_root`` is a real temp
    dir; we leak it (don't ``TemporaryDirectory.cleanup()``) so the
    artifact store's ``mkdir`` calls succeed for the lifetime of
    the test process. The OS reclaims it at exit.
    """

    from tests._db_fixture import make_db_store

    # Note: we deliberately don't use ``with TemporaryDirectory()``
    # — its ``cleanup()`` would wipe the parent dir before the
    # next test's ``create_run`` call. Instead, we allocate a fresh
    # subdir each time and let the OS reclaim at process exit.
    tmp = Path(tempfile.mkdtemp(prefix="kb_eval_claim_"))
    return make_db_store(tmp / "reports")


def _create_queued(store, run_id: str, name: str = "test-run") -> str:
    """Create a queued run via the store's ``create_run`` helper."""

    cfg = {
        "dify_base_url": "http://dify.test/v1",
        "dataset_id": "kb-1",
        "eval_file": "datasets/x.jsonl",
        "top_k": 5,
        "include_alternatives": False,
        "limit": 0,
        "sample_ids": [],
        "timeout_seconds": 60,
        "embedding_model": "",
        "rerank_model": "",
    }
    store.create_run(run_id=run_id, name=name, config=cfg)
    return run_id


def _status_of(run_id: str) -> str | None:
    sm = get_session_factory()
    with sm() as session:
        row = session.execute(select(Run).where(Run.id == run_id)).scalar_one_or_none()
        return row.status if row is not None else None


class ClaimQueuedRunsTests(unittest.TestCase):
    """``claim_queued_runs``: the atomic claim primitive."""

    def test_claim_returns_empty_list_when_no_queued_runs(self) -> None:
        _make_store()
        sm = get_session_factory()
        self.assertEqual(claim_queued_runs(sm, limit=5), [])

    def test_claim_returns_id_and_transitions_to_running(self) -> None:
        store = _make_store()
        sm = get_session_factory()
        _create_queued(store, "r-1", "first")

        claimed = claim_queued_runs(sm, limit=5)
        self.assertEqual(claimed, ["r-1"])
        self.assertEqual(_status_of("r-1"), "running")

    def test_claim_respects_limit(self) -> None:
        store = _make_store()
        sm = get_session_factory()
        for i in range(5):
            _create_queued(store, f"r-{i}", f"run-{i}")

        claimed = claim_queued_runs(sm, limit=3)
        self.assertEqual(len(claimed), 3)
        # Three rows are now ``running``; two remain ``queued``.
        statuses = sorted(
            _status_of(f"r-{i}") for i in range(5)
        )
        self.assertEqual(statuses, ["queued", "queued", "running", "running", "running"])

    def test_claim_is_idempotent(self) -> None:
        """Calling claim twice with no new rows returns empty the
        second time (no double-execution)."""

        store = _make_store()
        sm = get_session_factory()
        _create_queued(store, "r-1")

        first = claim_queued_runs(sm, limit=5)
        second = claim_queued_runs(sm, limit=5)
        self.assertEqual(first, ["r-1"])
        self.assertEqual(second, [])

    def test_claim_skips_running_and_queued_only(self) -> None:
        """``running`` / ``completed`` / ``failed`` / ``canceled``
        rows are not claimed by the queued-claim."""

        store = _make_store()
        sm = get_session_factory()
        _create_queued(store, "r-queued")
        _create_queued(store, "r-completed")
        _create_queued(store, "r-failed")
        _create_queued(store, "r-canceled")

        # Pre-set three of them to non-queued statuses.
        store.update_manifest("r-completed", status="completed", finished_at=datetime.now(timezone.utc).isoformat())
        store.update_manifest("r-failed", status="failed", finished_at=datetime.now(timezone.utc).isoformat())
        store.update_manifest("r-canceled", status="canceled", finished_at=datetime.now(timezone.utc).isoformat())

        claimed = claim_queued_runs(sm, limit=10)
        self.assertEqual(claimed, ["r-queued"])

    def test_claim_skips_soft_deleted_rows(self) -> None:
        """``deleted_at IS NOT NULL`` rows are not claimed."""

        store = _make_store()
        sm = get_session_factory()
        _create_queued(store, "r-alive")
        _create_queued(store, "r-dead")

        from sqlalchemy import update

        # Soft-delete one row directly so the claim ignores it.
        with sm() as session:
            with session.begin():
                session.execute(
                    update(Run)
                    .where(Run.id == "r-dead")
                    .values(deleted_at=datetime.now(timezone.utc))
                )

        claimed = claim_queued_runs(sm, limit=10)
        self.assertEqual(claimed, ["r-alive"])


class RequeueStaleRunningRunsTests(unittest.TestCase):
    """``requeue_stale_running_runs``: the watchdog's recovery primitive."""

    def test_requeues_when_heartbeat_is_stale(self) -> None:
        store = _make_store()
        sm = get_session_factory()
        _create_queued(store, "r-stale")
        # Claim it so it's ``running``.
        claim_queued_runs(sm, limit=5)

        # Backdate the heartbeat past the threshold.
        sm2 = get_session_factory()
        stale_time = datetime.now(timezone.utc) - timedelta(seconds=600)
        with sm2() as session:
            with session.begin():
                session.execute(
                    update_for_test(Run.id == "r-stale")
                    .values(last_heartbeat_at=stale_time)
                )

        requeued = requeue_stale_running_runs(sm, threshold_seconds=300)
        self.assertIn("r-stale", requeued)
        self.assertEqual(_status_of("r-stale"), "queued")

    def test_does_not_requeue_recent_heartbeat(self) -> None:
        store = _make_store()
        sm = get_session_factory()
        _create_queued(store, "r-fresh")
        claim_queued_runs(sm, limit=5)

        requeued = requeue_stale_running_runs(sm, threshold_seconds=300)
        self.assertNotIn("r-fresh", requeued)
        self.assertEqual(_status_of("r-fresh"), "running")


class MarkRunCanceledTests(unittest.TestCase):
    """``mark_run_canceled``: the DELETE route's transition primitive."""

    def test_cancels_running_run(self) -> None:
        store = _make_store()
        sm = get_session_factory()
        _create_queued(store, "r-1")
        claim_queued_runs(sm, limit=5)

        result = mark_run_canceled(sm, "r-1")
        self.assertTrue(result)
        self.assertEqual(_status_of("r-1"), "canceled")

    def test_cancels_queued_run(self) -> None:
        store = _make_store()
        sm = get_session_factory()
        _create_queued(store, "r-q")

        result = mark_run_canceled(sm, "r-q")
        self.assertTrue(result)
        self.assertEqual(_status_of("r-q"), "canceled")

    def test_refuses_terminal_runs(self) -> None:
        store = _make_store()
        sm = get_session_factory()
        _create_queued(store, "r-done")
        store.update_manifest("r-done", status="completed")

        result = mark_run_canceled(sm, "r-done")
        self.assertFalse(result)
        self.assertEqual(_status_of("r-done"), "completed")

    def test_returns_false_for_missing_run(self) -> None:
        sm = get_session_factory()
        self.assertFalse(mark_run_canceled(sm, "nope"))


# Tiny helper because ``update`` is needed twice and we want a
# clean expression form for the test bodies.
def update_for_test(predicate):
    from sqlalchemy import update as _update

    return _update(Run).where(predicate)


if __name__ == "__main__":
    unittest.main()
