"""Throttle the per-query ``on_progress`` callback so we don't write the
``runs.progress`` JSON column on every single retrieve.

Why
---
The runner hits ``on_progress`` once per (sample, query) iteration. With
100 samples × 2 query kinds that's 200 writes per run — and once we move
to 8-way concurrent retrieve inside a single run, 8 × that.

The coalescer holds the latest progress snapshot in memory and only
forwards to the sink (the caller-provided ``on_progress`` callable) at
most once every ``min_interval_ms`` and at least once every
``max_interval_ms``. The final ``flush()`` always emits the most recent
state so the last ``completed/total`` is never lost.

Thread / coroutine model
------------------------
The coalescer is **not** thread-safe and **not** coroutine-safe — it is
designed for a single coroutine to call ``update`` from. The runner
``run_evaluation`` is single-coroutine for sync mode and is wrapped in a
single ``asyncio.gather`` for async mode (the runner coalesces the
post-gather updates, not the in-flight ones). If you need to coalesce
updates from multiple concurrent producers, put an ``asyncio.Lock``
around ``update`` at the call site, or wrap the coalescer in a per-call
``call_soon_threadsafe`` hop.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable


ProgressSink = Callable[[dict[str, Any]], None]


@dataclass
class ProgressCoalescer:
    """Time-bounded throttle for ``on_progress`` callbacks.

    Parameters
    ----------
    sink
        The actual callback (typically ``DBStore.update_progress``).
    min_interval_ms
        Floor between two writes. Updates arriving inside this window
        are dropped (state is held in memory; the *latest* counts win
        because every ``update`` overwrites ``self._latest``).
    max_interval_ms
        Ceiling between two writes. Even if updates keep arriving faster
        than ``min_interval_ms``, the sink is forced to fire at least
        once per ``max_interval_ms`` so a long stream of updates doesn't
        get coalesced into nothing.
    clock
        Override for tests; defaults to ``time.monotonic``.
    """

    sink: ProgressSink
    min_interval_ms: int = 200
    max_interval_ms: int = 1000
    clock: Callable[[], float] = time.monotonic

    _latest: dict[str, Any] | None = field(default=None, init=False)
    _last_write_at: float | None = field(default=None, init=False)
    _ever_written: bool = field(default=False, init=False)

    def update(self, progress: dict[str, Any]) -> None:
        """Record a new progress snapshot. Emit to ``sink`` if the
        min_interval window has passed, or if this is the first call,
        or if the max_interval ceiling has elapsed since the last write.
        """

        now = self.clock()
        self._latest = dict(progress)

        if self._last_write_at is None:
            # First update: always emit so the UI / DB sees the initial
            # snapshot immediately.
            self._emit(now)
            return

        elapsed_ms = (now - self._last_write_at) * 1000.0
        if elapsed_ms >= self.min_interval_ms:
            self._emit(now)
            return

        if elapsed_ms >= self.max_interval_ms:
            # Should never happen given elapsed_ms < min_interval_ms, but
            # belt-and-suspenders for clock skew / negative deltas.
            self._emit(now)

    def flush(self) -> None:
        """Force-emit the most recent snapshot. Call this at the very
        end of a run so the final ``completed/total`` is never lost."""

        if self._latest is None:
            return
        if not self._ever_written:
            # Nothing has been emitted yet — emit the latest state so
            # callers see *something* even if they only ever call
            # ``flush`` without a prior ``update``.
            pass
        self._emit(self.clock())

    def _emit(self, now: float) -> None:
        assert self._latest is not None
        self.sink(self._latest)
        self._last_write_at = now
        self._ever_written = True


__all__ = ["ProgressCoalescer", "ProgressSink"]
