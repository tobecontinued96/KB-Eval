"""Owns the runner subprocess lifecycle.

The supervisor holds a single :class:`multiprocessing.Process` that
runs :func:`kb_eval.runner_subprocess._runner_main`. It's started by
the FastAPI ``lifespan`` and shut down before app shutdown.

Why a child process at all
--------------------------
The runner does 8-way concurrent HTTP I/O against Dify and runs
synchronously inside ``BackgroundTasks`` today.
Keeping it in the FastAPI event loop means:

* One stuck ``urlopen`` blocks every health check and progress
  poll until the OS TCP timeout fires (often 60–120s).
* A second queued run can't make progress because the first one
  owns the BackgroundTasks thread.
* The ``asyncio.to_thread`` pool is finite; long-running tasks
  eventually hit ``anyio``'s default 40-token limit.

Forking the runner into a separate process gives it:

* Its own asyncio event loop with its own 8-way concurrency budget
  per run.
* Isolation from the HTTP request path: a wedged retrieve no
  longer blocks ``/api/health`` or ``/api/runs/<id>``.
* A clean kill boundary (``process.terminate`` then ``process.kill``
  on Windows; the watchdog will re-queue any rows the runner
  didn't finish).

Why ``spawn`` and not ``fork``
------------------------------
Windows + ``fork`` is broken (asyncio + threading + ``fork`` is a
portability minefield). The runner subprocess is launched via
``multiprocessing.get_context("spawn")`` which re-imports the
target module in a fresh Python interpreter. The entry-point
function takes only pickle-able primitive args; everything else
is re-imported inside the child process.

Rollback anchor (``EVAL_RUNNER_SUBPROCESS=disabled``)
----------------------------------------------------
Setting this env var causes :meth:`start` to return ``False``
without spawning. The FastAPI ``create_run`` route is responsible
for falling back to ``BackgroundTasks.add_task(execute_run_inline,
...)`` so the user-visible behaviour matches the pre-commit-4
system. This gives operators a one-env-var rollback without
touching code.
"""

from __future__ import annotations

import logging
import multiprocessing as mp
import os
from pathlib import Path


_log = logging.getLogger("backend.runner_supervisor")


class RunnerSupervisor:
    """Single-process supervisor for the runner subprocess.

    The class is deliberately small. We could grow it to manage
    multiple subprocesses (one per worker) or to expose a richer
    health-check API, but the current contract is just
    ``start`` / ``stop`` / ``is_alive``.
    """

    def __init__(
        self,
        *,
        database_url: str,
        reports_root: Path,
        concurrency: int = 8,
        tick_ms: int = 500,
        enabled: bool | None = None,
    ) -> None:
        self._database_url = database_url
        self._reports_root = Path(reports_root)
        self._concurrency = max(1, int(concurrency))
        self._tick_ms = max(50, int(tick_ms))
        # ``None`` means "consult the env var"; explicit True/False
        # wins. This lets tests force-enable / force-disable without
        # mutating the environment.
        if enabled is None:
            env = os.environ.get("EVAL_RUNNER_SUBPROCESS", "enabled").lower()
            enabled = env not in {"disabled", "off", "false", "0", "no"}
        self._enabled = bool(enabled)
        self._process: mp.Process | None = None

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def concurrency(self) -> int:
        return self._concurrency

    def start(self) -> bool:
        """Spawn the runner subprocess. Returns ``True`` if it was
        started, ``False`` if the supervisor is disabled (or
        ``enabled=False`` was passed)."""

        if not self._enabled:
            _log.info(
                "runner supervisor disabled (EVAL_RUNNER_SUBPROCESS=%s); skipping spawn",
                os.environ.get("EVAL_RUNNER_SUBPROCESS", "enabled"),
            )
            return False
        if self._process is not None and self._process.is_alive():
            _log.warning("runner subprocess already alive (pid=%s)", self._process.pid)
            return True

        # Late import: ``runner_subprocess`` is the actual entry
        # point. Importing it at module load would pull in
        # ``kb_eval`` / httpx / asyncio into every process that
        # just imports the supervisor (e.g. Alembic).
        from kb_eval.runner_subprocess import _runner_main

        ctx = mp.get_context("spawn")
        # Pass primitive args only; ``_runner_main`` re-imports its
        # own engine / DBStore / AsyncDifyClient inside the
        # child process.
        self._process = ctx.Process(
            target=_runner_main,
            args=(
                self._database_url,
                str(self._reports_root),
                int(self._tick_ms),
                int(self._concurrency),
            ),
            name="kb-eval-runner",
            daemon=False,
        )
        self._process.start()
        _log.info(
            "runner subprocess spawned: pid=%s concurrency=%d tick=%dms",
            self._process.pid,
            self._concurrency,
            self._tick_ms,
        )
        return True

    def is_alive(self) -> bool:
        return self._process is not None and self._process.is_alive()

    def stop(self, *, timeout_seconds: float = 5.0) -> None:
        """Stop the runner subprocess. Tries ``terminate`` then
        ``kill`` if it doesn't exit within ``timeout_seconds``.

        Idempotent: safe to call multiple times or before
        :meth:`start`. Logs but does not raise on errors so the
        FastAPI ``lifespan`` shutdown path stays clean even if the
        subprocess is already dead or Windows refuses the signal.
        """

        if self._process is None:
            return
        proc = self._process
        if not proc.is_alive():
            self._process = None
            return
        try:
            proc.terminate()
            proc.join(timeout=timeout_seconds)
            if proc.is_alive():
                _log.warning(
                    "runner subprocess did not exit after terminate; killing"
                )
                proc.kill()
                proc.join(timeout=timeout_seconds)
        except Exception as exc:  # noqa: BLE001
            _log.exception("error stopping runner subprocess: %s", exc)
        finally:
            self._process = None
        _log.info("runner subprocess stopped")


__all__ = ["RunnerSupervisor"]
