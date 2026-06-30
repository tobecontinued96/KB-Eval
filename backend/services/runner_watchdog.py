"""Background watchdog that re-queues runs whose runner subprocess
has stopped heartbeating.

The runner subprocess writes ``runs.last_heartbeat_at = NOW()`` on
every coalesced progress flush (commit 1's ``DBStore.update_progress``).
If that value stops advancing, the watchdog assumes the runner is
wedged (crashed, OOM-killed, stuck in a ``urlopen`` forever) and
moves the row back to ``status='queued'`` so the next claim picks
it up.

This loop runs **inside the FastAPI parent process** — it's a cheap
DB poll every few seconds, not a separate process. The cost is
one indexed SELECT + zero or one UPDATE per tick (the ``(status,
last_heartbeat_at)`` index from commit 1 keeps both cheap).

Lifecycle
---------
Started by ``backend.app.lifespan`` after the runner supervisor
starts, stopped before shutdown. The watchdog never raises — any
DB error is logged and the next tick retries.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from backend.services.runner_claim import requeue_stale_running_runs


_log = logging.getLogger("backend.runner_watchdog")


async def run_watchdog(
    session_factory: Any,
    *,
    tick_seconds: float = 5.0,
    threshold_seconds: int | None = None,
    shutdown: asyncio.Event | None = None,
) -> None:
    """Re-queue stuck ``running`` runs forever (until ``shutdown`` is set).

    Parameters
    ----------
    session_factory
        A SQLAlchemy ``sessionmaker[Session]`` from
        ``backend.db.session.get_session_factory()``.
    tick_seconds
        How often to poll. Default 5s; long enough that the DB isn't
        spammed, short enough that a stuck run is recovered in under
        ``threshold_seconds`` (default 300s).
    threshold_seconds
        How stale ``last_heartbeat_at`` must be before we re-queue.
        ``None`` means "read ``RUNNER_WATCHDOG_TIMEOUT_SECONDS`` from
        the environment, default 300s".
    shutdown
        An optional ``asyncio.Event`` that, when set, causes the
        loop to exit cleanly. ``backend.app.lifespan`` passes the
        app's shutdown event.
    """

    if threshold_seconds is None:
        try:
            threshold_seconds = int(
                os.environ.get("RUNNER_WATCHDOG_TIMEOUT_SECONDS", "300")
            )
        except (TypeError, ValueError):
            threshold_seconds = 300

    interval = max(0.5, float(tick_seconds))
    _log.info(
        "runner watchdog started: tick=%.1fs threshold=%ds",
        interval,
        int(threshold_seconds),
    )

    # Lazy import so unit tests that import this module without a
    # full app context don't fail.
    try:
        while True:
            if shutdown is not None and shutdown.is_set():
                break
            try:
                requeued = await asyncio.to_thread(
                    requeue_stale_running_runs,
                    session_factory,
                    int(threshold_seconds),
                )
                if requeued:
                    _log.warning(
                        "watchdog re-queued %d stale run(s): %s",
                        len(requeued),
                        ", ".join(requeued[:5]) + ("..." if len(requeued) > 5 else ""),
                    )
            except Exception as exc:  # noqa: BLE001
                # DB blip or restart; log and continue. The next tick
                # will retry.
                _log.exception("watchdog tick failed: %s", exc)
            try:
                if shutdown is not None:
                    await asyncio.wait_for(shutdown.wait(), timeout=interval)
                    break
                else:
                    await asyncio.sleep(interval)
            except asyncio.TimeoutError:
                continue
    finally:
        _log.info("runner watchdog stopped")


__all__ = ["run_watchdog"]
