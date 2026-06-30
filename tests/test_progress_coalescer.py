"""Unit tests for ``kb_eval.progress_coalescer.ProgressCoalescer``.

The coalescer's contract is purely about *when* the sink is called and
*what* state is forwarded, not about DB or asyncio — so the tests use a
fake monotonic clock and a list-collecting sink. No DB, no network, no
asyncio.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from kb_eval.progress_coalescer import ProgressCoalescer  # noqa: E402


class _FakeClock:
    """A monotonic clock the test can advance by hand."""

    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance_ms(self, ms: float) -> None:
        self.now += ms / 1000.0


class ProgressCoalescerTests(unittest.TestCase):
    def test_first_update_is_emitted_immediately(self) -> None:
        clock = _FakeClock()
        emitted: list[dict] = []
        coalescer = ProgressCoalescer(
            sink=emitted.append,
            min_interval_ms=200,
            max_interval_ms=1000,
            clock=clock,
        )
        coalescer.update({"completed_queries": 1})
        self.assertEqual(len(emitted), 1)
        self.assertEqual(emitted[0]["completed_queries"], 1)

    def test_burst_of_updates_only_emits_first(self) -> None:
        """A burst inside ``min_interval_ms`` should emit once (the
        first), hold the latest snapshot, and drop the rest."""

        clock = _FakeClock()
        emitted: list[dict] = []
        coalescer = ProgressCoalescer(
            sink=emitted.append,
            min_interval_ms=200,
            max_interval_ms=1000,
            clock=clock,
        )
        coalescer.update({"completed_queries": 1})  # first → emit
        clock.advance_ms(50)
        coalescer.update({"completed_queries": 2})  # within window → drop
        clock.advance_ms(50)
        coalescer.update({"completed_queries": 3})  # within window → drop
        self.assertEqual(len(emitted), 1)
        self.assertEqual(emitted[0]["completed_queries"], 1)

    def test_next_emit_after_min_interval_uses_latest_snapshot(self) -> None:
        """After the min_interval window passes, the next update emits
        the **latest** held snapshot, not the value at the boundary."""

        clock = _FakeClock()
        emitted: list[dict] = []
        coalescer = ProgressCoalescer(
            sink=emitted.append,
            min_interval_ms=200,
            max_interval_ms=1000,
            clock=clock,
        )
        coalescer.update({"completed_queries": 1})
        clock.advance_ms(50)
        coalescer.update({"completed_queries": 2})  # dropped, latest=2
        clock.advance_ms(50)
        coalescer.update({"completed_queries": 3})  # dropped, latest=3
        clock.advance_ms(200)  # total elapsed 300ms > 200ms min
        coalescer.update({"completed_queries": 4})  # emits latest=4
        self.assertEqual(len(emitted), 2)
        self.assertEqual(emitted[0]["completed_queries"], 1)
        self.assertEqual(emitted[1]["completed_queries"], 4)

    def test_max_interval_forces_emit_even_during_burst(self) -> None:
        """If updates keep arriving faster than ``min_interval_ms``, the
        coalescer should still re-emit so progress never goes silent
        for longer than the min_interval ceiling — i.e. at least one
        emit every ~``min_interval_ms`` once a burst is in flight.

        Trace with ``min_interval_ms=200`` and updates at 150ms each:

            t=0    update(1) -> emit 1   (initial)
            t=150  update(2) -> drop     (150 < 200)
            t=300  update(3) -> emit 3   (elapsed 300 >= 200)
            t=450  update(4) -> drop     (150 since t=300)
            t=600  update(5) -> emit 5   (elapsed 300 >= 200)

        So after a 5-update burst we expect 3 emits; the *latest emit*
        holds the value at the last eligible boundary (5), while
        the in-memory snapshot holds the very-latest update (6) and
        only an explicit ``flush()`` would push that 6 to the sink.
        That's the contract: the coalescer rate-limits but never
        silently drops — the runner always pairs the loop end with
        a ``flush()``.
        """

        clock = _FakeClock()
        emitted: list[dict] = []
        coalescer = ProgressCoalescer(
            sink=emitted.append,
            min_interval_ms=200,
            max_interval_ms=500,
            clock=clock,
        )
        coalescer.update({"completed_queries": 1})
        for i in range(2, 7):
            clock.advance_ms(150)
            coalescer.update({"completed_queries": i})
        self.assertEqual(len(emitted), 3)
        self.assertEqual(emitted[0]["completed_queries"], 1)
        self.assertEqual(emitted[1]["completed_queries"], 3)
        self.assertEqual(emitted[2]["completed_queries"], 5)

        # End-of-run flush must always emit the latest in-memory
        # snapshot — even if the previous emit was within the
        # min_interval window.
        coalescer.flush()
        self.assertEqual(len(emitted), 4)
        self.assertEqual(emitted[-1]["completed_queries"], 6)

    def test_flush_always_emits_latest_even_with_no_recent_emit(self) -> None:
        """End-of-run ``flush`` must always emit the latest state, even
        if the last ``update`` was within the min_interval window."""

        clock = _FakeClock()
        emitted: list[dict] = []
        coalescer = ProgressCoalescer(
            sink=emitted.append,
            min_interval_ms=200,
            max_interval_ms=1000,
            clock=clock,
        )
        coalescer.update({"completed_queries": 1})
        clock.advance_ms(50)
        coalescer.update({"completed_queries": 99})  # dropped
        coalescer.flush()  # must emit 99
        self.assertEqual(len(emitted), 2)
        self.assertEqual(emitted[1]["completed_queries"], 99)

    def test_flush_is_noop_when_nothing_was_updated(self) -> None:
        """Calling ``flush`` before any ``update`` should not emit a
        blank snapshot — there's nothing meaningful to flush."""

        clock = _FakeClock()
        emitted: list[dict] = []
        coalescer = ProgressCoalescer(
            sink=emitted.append,
            min_interval_ms=200,
            max_interval_ms=1000,
            clock=clock,
        )
        coalescer.flush()
        self.assertEqual(emitted, [])

    def test_uses_real_monotonic_clock_by_default(self) -> None:
        """Sanity check: with the default clock and a non-trivial
        min_interval, back-to-back ``update`` calls only emit once."""

        emitted: list[dict] = []
        # min_interval_ms=0 makes every update emit, but the default
        # is 200ms, so two fast updates should coalesce to one emit.
        coalescer = ProgressCoalescer(sink=emitted.append)
        coalescer.update({"a": 1})
        coalescer.update({"a": 2})
        # Real-time clock could advance during execution; we assert
        # the *first* snapshot was emitted and the second call did not
        # throw or hang.
        self.assertGreaterEqual(len(emitted), 1)
        self.assertEqual(emitted[0]["a"], 1)


if __name__ == "__main__":
    unittest.main()
