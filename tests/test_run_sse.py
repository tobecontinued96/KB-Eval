"""Unit tests for ``backend.services.run_event_stream``.

We drive the endpoint through FastAPI's ``TestClient`` (sync variant
via ``starlette.testclient``) so the full middleware stack
(request headers, response headers, chunked transfer encoding) is
exercised end-to-end. The store is a stub so we control
``build_detail`` return values without spinning up a real PG.

The test for ``stream_tick_ms`` uses a tiny tick so a multi-frame
scenario completes inside the test deadline. We also break out of
the response iterator as soon as we've collected enough frames
— the streaming generator otherwise keeps polling forever.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch, MagicMock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import backend.app as app_module  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402


def _parse_sse_frames(raw: str) -> list[dict[str, Any]]:
    """Parse the raw SSE wire format into a list of
    ``{"id": int, "event": str, "data": dict}`` frames.

    Handles ``id:``, ``event:``, ``data:`` lines, skips comment
    lines (starting with ``:``), and tolerates CR/LF variations.
    """

    frames: list[dict[str, Any]] = []
    cur: dict[str, Any] = {}
    for raw_line in raw.splitlines():
        line = raw_line.rstrip("\r")
        if not line:
            if cur:
                frames.append(cur)
                cur = {}
            continue
        if line.startswith(":"):
            # SSE comment (retry hint, keep-alive nudge).
            continue
        if ":" not in line:
            continue
        field, _, value = line.partition(":")
        # Per spec, a single leading space after the colon is stripped.
        if value.startswith(" "):
            value = value[1:]
        if field == "id":
            cur["id"] = int(value)
        elif field == "event":
            cur["event"] = value
        elif field == "data":
            try:
                cur["data"] = json.loads(value)
            except json.JSONDecodeError:
                cur["data"] = value
    if cur:
        frames.append(cur)
    return frames


class _FakeStore:
    """Minimal store stub with the methods ``stream_run_events``
    calls: ``build_detail`` (sync) and the interface ``RunStore``
    declares (we don't actually call the others)."""

    def __init__(self, snapshots: list[dict[str, Any]]) -> None:
        # ``snapshots`` is a list of ``build_detail`` return values;
        # the stream will yield one snapshot per tick (capped at
        # the length of the list).
        self._snapshots = list(snapshots)
        self._index = 0
        self.build_detail_calls = 0

    def build_detail(self, run_id: str) -> dict[str, Any]:
        self.build_detail_calls += 1
        if self._index >= len(self._snapshots):
            return self._snapshots[-1]
        snap = self._snapshots[self._index]
        self._index += 1
        return snap


class StreamRunEventsTests(unittest.TestCase):
    """Drive ``GET /api/runs/{run_id}/events`` through TestClient.

    All tests cap how long they drain the stream — the SSE generator
    otherwise runs forever waiting for the next DB tick. We pass
    a terminal-status snapshot so the generator's ``break`` fires
    naturally.
    """

    def _drain_until(self, response, *, until_event: str, max_bytes: int = 64_000) -> bytes:
        """Read SSE frames until we see ``until_event`` or hit
        ``max_bytes``. The TestClient respects ``response.close()``
        which propagates a ``CancelledError`` into the generator so
        it exits cleanly."""

        raw = b""
        try:
            for chunk in response.iter_bytes():
                raw += chunk
                if raw.endswith(b"\n\n") and (
                    f"event: {until_event}".encode("utf-8") in raw
                ):
                    break
                if len(raw) > max_bytes:
                    break
        finally:
            response.close()
        return raw

    def test_snapshot_emitted_on_connect(self) -> None:
        store = _FakeStore(
            [
                {
                    "id": "r1",
                    "status": "running",
                    "progress": {"total_queries": 100, "completed_queries": 50, "error_queries": 0, "current_sample_id": "S025"},
                    "finished_at": None,
                    "error": "",
                },
                # Terminal status so the generator breaks out after
                # the snapshot is emitted.
                {
                    "id": "r1",
                    "status": "completed",
                    "progress": {"total_queries": 100, "completed_queries": 100, "error_queries": 0, "current_sample_id": None},
                    "finished_at": "2026-06-22T12:00:00",
                    "error": "",
                },
            ]
        )

        with patch.object(app_module, "store", store):
            client = TestClient(app_module.app)
            with client.stream("GET", "/api/runs/r1/events") as response:
                self.assertEqual(response.status_code, 200)
                raw = self._drain_until(response, until_event="status")

        frames = _parse_sse_frames(raw.decode("utf-8"))
        events = [f["event"] for f in frames]
        self.assertIn("snapshot", events)
        # First frame must be the snapshot.
        self.assertEqual(frames[0]["event"], "snapshot")
        self.assertEqual(frames[0]["data"]["run_id"], "r1")
        self.assertEqual(frames[0]["data"]["status"], "running")
        self.assertEqual(frames[0]["data"]["progress"]["completed_queries"], 50)

    def test_progress_emitted_on_diff(self) -> None:
        store = _FakeStore(
            [
                # snapshot
                {
                    "id": "r1",
                    "status": "running",
                    "progress": {"total_queries": 10, "completed_queries": 0, "error_queries": 0, "current_sample_id": None, "last_heartbeat_at": "2026-06-22T12:00:00"},
                    "finished_at": None,
                    "error": "",
                },
                # tick 1: progress changes
                {
                    "id": "r1",
                    "status": "running",
                    "progress": {"total_queries": 10, "completed_queries": 5, "error_queries": 0, "current_sample_id": "S004", "last_heartbeat_at": "2026-06-22T12:00:01"},
                    "finished_at": None,
                    "error": "",
                },
                # tick 2: terminal status
                {
                    "id": "r1",
                    "status": "completed",
                    "progress": {"total_queries": 10, "completed_queries": 10, "error_queries": 0, "current_sample_id": None, "last_heartbeat_at": "2026-06-22T12:00:02"},
                    "finished_at": "2026-06-22T12:00:02",
                    "error": "",
                },
            ]
        )

        with patch.object(app_module, "store", store):
            client = TestClient(app_module.app)
            with client.stream("GET", "/api/runs/r1/events") as response:
                raw = self._drain_until(response, until_event="status")

        frames = _parse_sse_frames(raw.decode("utf-8"))
        events = [f["event"] for f in frames]
        # The terminal tick emits both a ``progress`` (because
        # the completed_queries count changed) and a ``status``
        # frame. The order matters: progress first, then status.
        self.assertEqual(events, ["snapshot", "progress", "progress", "status"])

        # First progress frame carries the running snapshot's
        # progress (5/10). The second progress frame carries the
        # completed snapshot's progress (10/10).
        self.assertEqual(frames[1]["data"]["completed_queries"], 5)
        self.assertEqual(frames[1]["data"]["current_sample_id"], "S004")
        self.assertEqual(
            frames[1]["data"]["last_heartbeat_at"],
            "2026-06-22T12:00:01",
        )
        self.assertEqual(frames[2]["data"]["completed_queries"], 10)
        self.assertIsNone(frames[2]["data"]["current_sample_id"])

        # The status frame is last, carrying the terminal status.
        status_frame = frames[3]
        self.assertEqual(status_frame["data"]["status"], "completed")
        self.assertEqual(
            status_frame["data"]["finished_at"], "2026-06-22T12:00:02"
        )
        self.assertEqual(status_frame["data"]["error"], "")

    def test_response_headers(self) -> None:
        store = _FakeStore(
            [
                {
                    "id": "r1",
                    "status": "completed",
                    "progress": {},
                    "finished_at": None,
                    "error": "",
                }
            ]
        )
        with patch.object(app_module, "store", store):
            client = TestClient(app_module.app)
            with client.stream("GET", "/api/runs/r1/events") as response:
                self.assertEqual(response.status_code, 200)
                # starlette appends ``; charset=utf-8`` to text
                # media types — assert on the prefix instead.
                self.assertTrue(
                    response.headers["content-type"].startswith(
                        "text/event-stream"
                    )
                )
                self.assertEqual(
                    response.headers["cache-control"], "no-cache, no-transform"
                )
                self.assertEqual(
                    response.headers["x-accel-buffering"], "no"
                )
                # Drain so the server-side generator closes.
                self._drain_until(response, until_event="status")

    def test_404_for_missing_run(self) -> None:
        """``build_detail`` raising ``ReportStoreError`` should
        surface as 404, not an empty stream."""

        from backend.services.artifact_store import ArtifactStoreError

        store = MagicMock()
        store.build_detail.side_effect = ArtifactStoreError("Run not found")

        with patch.object(app_module, "store", store):
            client = TestClient(app_module.app)
            response = client.get("/api/runs/missing/events")
        self.assertEqual(response.status_code, 404)
        body = response.json()
        self.assertEqual(body["code"], "RUN_NOT_FOUND")


class FormatSseTests(unittest.TestCase):
    """The wire format helper."""

    def test_format_includes_id_event_data_blank(self) -> None:
        from backend.services.run_event_stream import _format_sse

        out = _format_sse(42, "progress", {"a": 1, "b": "x"}).decode("utf-8")
        # Order is per spec: id, event, data, blank line.
        self.assertTrue(out.startswith("id: 42\n"))
        self.assertIn("event: progress\n", out)
        self.assertIn('data: {"a": 1, "b": "x"}', out)
        # Frame terminator is the blank line.
        self.assertTrue(out.endswith("\n\n"))

    def test_format_comment_starts_with_colon(self) -> None:
        from backend.services.run_event_stream import _format_comment

        out = _format_comment("retry: 3000").decode("utf-8")
        self.assertTrue(out.startswith(": "))


class ReplayBufferTests(unittest.TestCase):
    """``Last-Event-ID`` resume via the in-memory ring buffer."""

    def test_replay_returns_events_after_seen_id(self) -> None:
        """Verify the in-memory replay buffer honours
        ``Last-Event-ID``: only events with id > last-seen are
        re-emitted on reconnect.

        We don't drive the full ``stream_run_events`` generator
        here (the stream loops forever on a non-terminal status);
        instead we inspect the ``_record_event`` /
        ``_replay_buffers`` interaction directly and assert the
        replay slice excludes events up to and including the
        last-seen id.
        """

        from backend.services.run_event_stream import (
            _event_counters,
            _next_event_id,
            _record_event,
            _replay_buffers,
        )

        run_id = "r-replay-test"
        # Pre-seed the buffer with three events (ids 1, 2, 3).
        for i in range(3):
            ev_id = _next_event_id(run_id)
            _record_event(
                run_id,
                {"id": ev_id, "event": "progress", "data": {"i": i}},
            )

        # Simulate the replay slice the generator builds when
        # ``Last-Event-ID: 1`` arrives: keep events with id > 1.
        buf = _replay_buffers[run_id]
        replayed = [ev for ev in buf if ev["id"] > 1]
        ids = [ev["id"] for ev in replayed]
        self.assertEqual(ids, [2, 3])
        # The first event (id=1) was already seen, so it must
        # not be replayed.
        self.assertNotIn(1, ids)

        # Cleanup the buffer so other tests start fresh.
        _replay_buffers.pop(run_id, None)
        _event_counters.pop(run_id, None)


if __name__ == "__main__":
    unittest.main()
