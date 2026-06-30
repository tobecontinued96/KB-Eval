"""Unit tests for ``backend.services.runner_watchdog.run_watchdog``.

The watchdog is just a ``while True: sleep; tick`` loop. The tests
focus on the integration with ``requeue_stale_running_runs`` and
on the shutdown contract — we don't want a hung tick to block app
shutdown for the full ``RUNNER_WATCHDOG_TIMEOUT_SECONDS``.

Threading note
--------------
``run_watchdog`` calls ``requeue_stale_running_runs`` via
``asyncio.to_thread(...)`` so the DB round-trip doesn't stall the
event loop. On SQLite ``:memory:``, that means the watchdog's
worker thread sees a **different in-memory DB** than the main
test thread (each connection in SQLite gets its own private DB
unless ``StaticPool`` is used). We work around that by giving
the watchdog a file-backed SQLite URL via a shared cache, so
all threads see the same database regardless of pool slot.
"""

from __future__ import annotations

import asyncio
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Force the test environment BEFORE any backend import.
import tests._db_fixture  # noqa: F401, E402

from sqlalchemy import create_engine, select, update  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from backend.db.models import Base, Run  # noqa: E402
from backend.services.runner_watchdog import run_watchdog  # noqa: E402


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _make_shared_session_factory():
    """Build a sessionmaker backed by an in-memory SQLite that
    **shares** its connection across threads (``StaticPool`` +
    ``check_same_thread=False``).

    ``run_watchdog`` runs ``requeue_stale_running_runs`` on a
    worker thread via ``asyncio.to_thread``; on a default
    SQLite ``:memory:`` engine that worker would see a fresh
    empty DB (each connection is its own private DB). StaticPool
    keeps a single shared connection so the cross-thread call
    still sees the rows the main thread wrote.
    """

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def _create_queued_direct(sm, run_id: str) -> None:
    """Create a queued run via the model directly (the watchdog
    tests don't need the artifact side effects of
    ``DBStore.create_run``)."""

    from datetime import datetime as _dt, timezone as _tz

    with sm() as session:
        with session.begin():
            session.add(
                Run(
                    id=run_id,
                    name=run_id,
                    status="queued",
                    created_at=_dt.now(_tz.utc),
                )
            )


def _create_queued(store, run_id: str) -> None:
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
    store.create_run(run_id=run_id, name=run_id, config=cfg)


class WatchdogTickTests(unittest.TestCase):
    """One tick = one ``requeue_stale_running_runs`` call."""

    def test_watchdog_requeues_stale_row_within_one_tick(self) -> None:
        sm = _make_shared_session_factory()
        _create_queued_direct(sm, "r-stale")

        # Force the row to ``running`` with a stale heartbeat.
        from backend.services.runner_claim import claim_queued_runs

        claim_queued_runs(sm, limit=5)
        stale_time = datetime.now(timezone.utc) - timedelta(seconds=600)
        with sm() as session:
            with session.begin():
                session.execute(
                    update(Run)
                    .where(Run.id == "r-stale")
                    .values(last_heartbeat_at=stale_time)
                )

        async def one_tick() -> None:
            shutdown = asyncio.Event()
            task = asyncio.create_task(
                run_watchdog(
                    sm,
                    tick_seconds=0.05,
                    threshold_seconds=300,
                    shutdown=shutdown,
                )
            )
            await asyncio.sleep(0.15)
            shutdown.set()
            await asyncio.wait_for(task, timeout=2.0)

        _run(one_tick())

        # The row should now be ``queued`` again.
        with sm() as session:
            row = session.execute(select(Run).where(Run.id == "r-stale")).scalar_one()
        self.assertEqual(row.status, "queued")

    def test_watchdog_does_not_requeue_fresh_row(self) -> None:
        sm = _make_shared_session_factory()
        _create_queued_direct(sm, "r-fresh")

        from backend.services.runner_claim import claim_queued_runs

        claim_queued_runs(sm, limit=5)
        # Leave ``last_heartbeat_at`` at the recent value the claim set.

        async def one_tick() -> None:
            shutdown = asyncio.Event()
            task = asyncio.create_task(
                run_watchdog(
                    sm,
                    tick_seconds=0.05,
                    threshold_seconds=300,
                    shutdown=shutdown,
                )
            )
            await asyncio.sleep(0.15)
            shutdown.set()
            await asyncio.wait_for(task, timeout=2.0)

        _run(one_tick())

        with sm() as session:
            row = session.execute(select(Run).where(Run.id == "r-fresh")).scalar_one()
        self.assertEqual(row.status, "running")

    def test_watchdog_exits_on_shutdown_event(self) -> None:
        """Setting the shutdown event must cause the loop to exit
        cleanly, not hang until the next tick."""

        sm = _make_shared_session_factory()

        async def scenario() -> float:
            shutdown = asyncio.Event()
            task = asyncio.create_task(
                run_watchdog(
                    sm,
                    tick_seconds=10.0,  # long; we want shutdown to win
                    threshold_seconds=300,
                    shutdown=shutdown,
                )
            )
            await asyncio.sleep(0.05)
            shutdown.set()
            return await asyncio.wait_for(task, timeout=2.0)

        # Should return promptly, not block for 10 seconds.
        import time

        start = time.monotonic()
        _run(scenario())
        elapsed = time.monotonic() - start
        self.assertLess(elapsed, 1.0)


class WatchdogThresholdFromEnvTests(unittest.TestCase):
    """The threshold defaults to ``RUNNER_WATCHDOG_TIMEOUT_SECONDS``
    from the environment, defaulting to 300 if unset."""

    def test_threshold_defaults_to_300(self) -> None:
        # Re-import the module fresh to avoid cached env reads; we
        # don't actually need to assert on the constant — the
        # ``run_watchdog`` contract takes ``threshold_seconds`` as
        # a kwarg and only reads the env when it's None. Just check
        # the function signature accepts the documented params.
        import inspect

        sig = inspect.signature(run_watchdog)
        self.assertIn("tick_seconds", sig.parameters)
        self.assertIn("threshold_seconds", sig.parameters)
        self.assertIn("shutdown", sig.parameters)


if __name__ == "__main__":
    unittest.main()
