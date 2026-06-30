"""Server-Sent Events stream for run progress.

Replaces the 2-second ``GET /api/runs/{run_id}`` poll the frontend
did before commit 5. With SSE the browser opens one long-lived
connection and gets deltas pushed by the server as the runner
writes progress, status, and console.log entries.

Endpoint contract
-----------------
``GET /api/runs/{run_id}/events``

Response headers::

    Content-Type: text/event-stream
    Cache-Control: no-cache, no-transform
    Connection: keep-alive
    X-Accel-Buffering: no

Event types (``event:`` line; ``data:`` is JSON)
------------------------------------------------
* ``event: snapshot`` — once on connect, the current full state.
* ``event: progress`` — emitted on every coalesced DB progress
  flush the parent process observes (or every ``stream_tick_ms``,
  whichever is later).
* ``event: status`` — once, when the run reaches a terminal status
  (``completed`` / ``failed`` / ``canceled``). Stream closes after.
* ``event: ping`` — every ``stream_ping_ms`` (default 15 s). Keeps
  idle proxies (Vite dev, nginx, corporate) from killing the
  connection on their read timeout.
* ``event: error`` — once, on unrecoverable error. Stream closes
  after.

``Last-Event-ID`` resume
------------------------
Each frame has ``id: <monotonic int>``. On reconnect the client
sends ``Last-Event-ID: <id>`` and the server replays buffered
events from an in-memory ring buffer (≤ ``MAX_REPLAY`` events,
default 64). The buffer is per-``run_id`` and only lives for the
duration of the server process — restart loses the buffer, which
is fine because the next snapshot is the current state anyway.

Why not ``sse-starlette``
-------------------------
Adds a dep we don't need. ``StreamingResponse`` + a hand-rolled
``async def event_generator()`` is ~30 lines and matches exactly
what sse-starlette does internally. The only thing we lose is the
EventSourceResponse helper's automatic keep-alive; we wire our
own ``ping`` frame.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from typing import Any, AsyncIterator

from fastapi.responses import StreamingResponse

from backend.services.store_protocol import RunStore


_log = logging.getLogger("backend.run_event_stream")


# Module-level replay buffers keyed by run_id. Bounded by
# ``MAX_REPLAY`` to avoid leaking memory for runs that were
# subscribed to but never finished.
_MAX_REPLAY = 64
_replay_buffers: dict[str, deque[dict[str, Any]]] = {}
_event_counters: dict[str, int] = {}


def _next_event_id(run_id: str) -> int:
    """Return the next monotonic event ID for a run.

    Counter resets across server restarts — the replay buffer is
    in-memory only and disappears with the process.
    """

    counter = _event_counters.get(run_id, 0) + 1
    _event_counters[run_id] = counter
    return counter


def _record_event(run_id: str, event: dict[str, Any]) -> None:
    """Append an event to the replay buffer (capped at ``MAX_REPLAY``)."""

    buf = _replay_buffers.setdefault(run_id, deque(maxlen=_MAX_REPLAY))
    buf.append(event)


def _format_sse(event_id: int, event_type: str, data: dict[str, Any]) -> bytes:
    """Render one SSE frame.

    The format is line-based:
        id: <int>\\n
        event: <type>\\n
        data: <json>\\n
        \\n
    Newlines inside the JSON data are escaped so the browser can
    split frames on blank lines.
    """

    payload = json.dumps(data, ensure_ascii=False, default=str)
    return (
        f"id: {event_id}\n"
        f"event: {event_type}\n"
        f"data: {payload}\n"
        "\n"
    ).encode("utf-8")


def _format_comment(text: str) -> bytes:
    """SSE comment line (starts with ``:``). Used for the initial
    ``retry:`` hint and keep-alive nudges."""

    return f": {text}\n\n".encode("utf-8")


async def stream_run_events(
    run_id: str,
    store: RunStore,
    *,
    stream_tick_ms: int = 250,
    stream_ping_ms: int = 15_000,
    request: Any | None = None,
) -> StreamingResponse:
    """Build a ``StreamingResponse`` that streams SSE frames for one run.

    Parameters
    ----------
    run_id
        The run to follow.
    store
        A :class:`backend.services.store_protocol.RunStore`
        implementation (``DBStore`` in production).
    stream_tick_ms
        How often the server polls the DB for diffs. Default 250 ms
        — fast enough that the UI sees progress within a quarter
        second of the runner's coalesced flush, slow enough that we
        don't add noticeable DB load per connected browser.
    stream_ping_ms
        Heartbeat interval. Default 15 s; corporate proxies often
        kill idle connections after 30-60 s.
    request
        Optional ``fastapi.Request``. If provided, the server uses
        its ``Last-Event-ID`` header to seed the replay buffer.
    """

    # ``Last-Event-ID`` resume header parsing. We don't replay
    # events we don't have buffered (the buffer is per-process and
    # bounded), so a too-old ID is just ignored — the client gets
    # the current snapshot as the first frame after reconnect.
    last_seen_id: int | None = None
    if request is not None:
        header = request.headers.get("last-event-id")
        if header and header.isdigit():
            last_seen_id = int(header)

    # Validate the run exists before opening the stream. If the
    # client asked for a non-existent run, return 404 through the
    # normal HTTP layer instead of an empty stream.
    initial_detail = store.build_detail(run_id)
    initial_status = initial_detail.get("status", "failed")
    initial_progress = initial_detail.get("progress", {})

    async def event_generator() -> AsyncIterator[bytes]:
        # Initial ``retry:`` hint tells the browser how long to wait
        # before reconnecting on disconnect. 3s is a friendlier
        # default than the 5s the EventSource spec suggests.
        yield _format_comment("retry: 3000")

        # Replay any buffered events the client missed (per
        # ``Last-Event-ID``). The buffer is in-memory only; a
        # server restart drops it.
        if last_seen_id is not None:
            buf = _replay_buffers.get(run_id)
            if buf is not None:
                for ev in list(buf):
                    if ev["id"] > last_seen_id:
                        yield _format_sse(
                            ev["id"], ev["event"], ev["data"]
                        )

        # Initial snapshot — always emit so a fresh client has the
        # current state even if no progress events have arrived yet.
        snapshot_id = _next_event_id(run_id)
        snapshot_payload = {
            "run_id": run_id,
            "status": initial_status,
            "progress": initial_progress,
        }
        _record_event(
            run_id,
            {
                "id": snapshot_id,
                "event": "snapshot",
                "data": snapshot_payload,
            },
        )
        yield _format_sse(snapshot_id, "snapshot", snapshot_payload)

        last_progress = initial_progress
        last_status = initial_status
        last_emit_ms = time.monotonic() * 1000.0

        # If the run is already terminal when the client connects
        # (e.g. reconnecting to a finished run), emit a status
        # frame immediately and close. Without this branch the
        # generator would loop forever waiting for a transition
        # that already happened.
        if initial_status in {"completed", "failed", "canceled"}:
            status_id = _next_event_id(run_id)
            status_payload = {
                "status": initial_status,
                "finished_at": initial_detail.get("finished_at"),
                "error": initial_detail.get("error", "") or "",
            }
            _record_event(
                run_id,
                {
                    "id": status_id,
                    "event": "status",
                    "data": status_payload,
                },
            )
            yield _format_sse(status_id, "status", status_payload)
            return

        # Main loop. Exit when the run reaches a terminal status,
        # the client disconnects, or the server shuts down.
        while True:
            try:
                await asyncio.sleep(stream_tick_ms / 1000.0)
                detail = await asyncio.to_thread(store.build_detail, run_id)
            except asyncio.CancelledError:
                # Client disconnected.
                break
            except Exception as exc:  # noqa: BLE001
                _log.exception("event stream tick failed: %s", exc)
                err_id = _next_event_id(run_id)
                err_payload = {
                    "code": "STREAM_FAILED",
                    "message": f"event stream tick failed: {exc!r}",
                }
                _record_event(
                    run_id,
                    {
                        "id": err_id,
                        "event": "error",
                        "data": err_payload,
                    },
                )
                yield _format_sse(err_id, "error", err_payload)
                break

            current_progress = detail.get("progress", {})
            current_status = detail.get("status", "failed")

            # Emit progress only when something actually changed.
            # The runner coalesces its writes (commit 1's
            # ``ProgressCoalescer``) so the DB state changes at most
            # a handful of times per second — diffing here avoids
            # pushing redundant frames over the wire.
            if current_progress != last_progress:
                progress_id = _next_event_id(run_id)
                progress_payload = {
                    "total_queries": current_progress.get(
                        "total_queries", 0
                    ),
                    "completed_queries": current_progress.get(
                        "completed_queries", 0
                    ),
                    "error_queries": current_progress.get(
                        "error_queries", 0
                    ),
                    "current_sample_id": current_progress.get(
                        "current_sample_id"
                    ),
                    "last_heartbeat_at": current_progress.get(
                        "last_heartbeat_at"
                    ),
                }
                _record_event(
                    run_id,
                    {
                        "id": progress_id,
                        "event": "progress",
                        "data": progress_payload,
                    },
                )
                yield _format_sse(
                    progress_id, "progress", progress_payload
                )
                last_progress = current_progress
                last_emit_ms = time.monotonic() * 1000.0

            # Heartbeat ping. Keeps idle proxies from killing the
            # connection. We emit on a separate timer from the
            # ``stream_tick_ms`` DB poll.
            now_ms = time.monotonic() * 1000.0
            if now_ms - last_emit_ms >= stream_ping_ms:
                ping_id = _next_event_id(run_id)
                ping_payload = {"ts": int(now_ms / 1000.0)}
                _record_event(
                    run_id,
                    {
                        "id": ping_id,
                        "event": "ping",
                        "data": ping_payload,
                    },
                )
                yield _format_sse(ping_id, "ping", ping_payload)
                last_emit_ms = now_ms

            # Terminal status: emit a final ``status`` frame and
            # close the stream. The browser will see ``onmessage``
            # ``event:status`` and stop reconnecting.
            if current_status != last_status and current_status in {
                "completed",
                "failed",
                "canceled",
            }:
                status_id = _next_event_id(run_id)
                status_payload = {
                    "status": current_status,
                    "finished_at": detail.get("finished_at"),
                    "error": detail.get("error", "") or "",
                }
                _record_event(
                    run_id,
                    {
                        "id": status_id,
                        "event": "status",
                        "data": status_payload,
                    },
                )
                yield _format_sse(status_id, "status", status_payload)
                last_status = current_status
                break
            last_status = current_status

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


__all__ = ["stream_run_events"]
