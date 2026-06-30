// ``useRunProgressStream`` — React hook around the SSE endpoint
// ``GET /api/runs/{run_id}/events`` introduced in commit 5.
//
// Replaces the 2-second ``setInterval(loadRun, 2_000)`` poll that
// used to hammer the backend every time someone opened a RunDetail
// tab (and 2x or 3x more if they opened the same run in multiple
// tabs). With SSE the browser holds one long-lived connection per
// open tab and gets push notifications as the runner writes
// progress.
//
// Behaviour summary
// -----------------
// * On mount: opens an ``EventSource`` to the events endpoint.
// * On ``snapshot`` / ``progress`` events: merges the payload into
//   the local ``run`` state via ``onUpdate`` (the caller is
//   responsible for keeping the rest of the RunDetail state in
//   sync — we only push the progress + status fields here).
// * On ``status`` events with a terminal status (completed /
//   failed / canceled): calls ``onUpdate`` once more then closes
//   the EventSource; no further polls.
// * On disconnect / error: backs off with exponential delay
//   (capped at 5 s) and reconnects. If we can't reopen within
//   ``FALLBACK_TIMEOUT_MS`` (default 5 s of being disconnected),
//   the hook falls back to ``getRun`` polling every 5 s so the UI
//   still gets updates when the SSE endpoint is unreachable
//   (dev proxy misconfig, server restart, etc.).
// * On unmount: closes the EventSource and clears any pending
//   timers.
//
// EventSource is browser-native; no dep needed. The hook accepts
// the ``EventSource`` constructor as an optional parameter so
// tests can drive it with a fake.

import { useEffect, useRef } from "react";
import type { EvalRunDetail, EvalRunStatus } from "../types";

export interface RunProgressEvent {
  type: "snapshot" | "progress" | "status" | "ping" | "error";
  // ``progress`` payload fields (also populated for ``snapshot``
  // for convenience).
  total_queries?: number;
  completed_queries?: number;
  error_queries?: number;
  current_sample_id?: string | null;
  last_heartbeat_at?: string | null;
  // ``status`` payload fields.
  status?: EvalRunStatus;
  finished_at?: string | null;
  error?: string;
  // ``error`` payload fields.
  code?: string;
  message?: string;
}

export interface UseRunProgressStreamOptions {
  /** Called whenever a snapshot / progress / status event arrives. */
  onUpdate: (event: RunProgressEvent, currentRun: EvalRunDetail | null) => void;
  /**
   * Skip the SSE connection entirely (e.g. for terminal runs that
   * don't need push updates). Default ``false``.
   */
  disabled?: boolean;
  /**
   * Override the ``EventSource`` constructor for tests. Production
   * code leaves this ``undefined``.
   */
  EventSourceImpl?: typeof EventSource;
  /**
   * Fallback poll interval (ms) when SSE can't connect for >5 s.
   * Default 5000.
   */
  fallbackPollMs?: number;
  /**
   * Threshold after which SSE failure triggers fallback. Default 5000.
   */
  fallbackTimeoutMs?: number;
}

export interface UseRunProgressStreamHandle {
  /** Force-close the EventSource and the fallback poller. */
  close: () => void;
}

/**
 * Open an SSE connection to ``/api/runs/{runId}/events`` and merge
 * the pushed updates into the local state via ``onUpdate``. Returns
 * a handle with a ``close()`` method for explicit teardown.
 *
 * Used by ``RunDetail.tsx`` to replace the 2-second ``setInterval``
 * poll that drove backend load before commit 6.
 */
export function useRunProgressStream(
  runId: string,
  options: UseRunProgressStreamOptions,
): UseRunProgressStreamHandle {
  const { onUpdate, disabled, EventSourceImpl, fallbackPollMs, fallbackTimeoutMs } =
    options;
  const handleRef = useRef<UseRunProgressStreamHandle | null>(null);
  const onUpdateRef = useRef(onUpdate);
  onUpdateRef.current = onUpdate;

  useEffect(() => {
    if (!runId || disabled) return undefined;

    const ES = EventSourceImpl ?? (window.EventSource as typeof EventSource);
    const pollMs = fallbackPollMs ?? 5_000;
    const timeoutMs = fallbackTimeoutMs ?? 5_000;

    let closed = false;
    let es: EventSource | null = null;
    let fallbackTimer: number | null = null;
    let lastConnectedAt = 0;
    let connectionLostTimer: number | null = null;
    let backoffAttempt = 0;
    const currentRunRef: { current: EvalRunDetail | null } = { current: null };

    function parseEvent(ev: MessageEvent): RunProgressEvent | null {
      try {
        return JSON.parse(ev.data) as RunProgressEvent;
      } catch {
        return null;
      }
    }

    function mergeEvent(event: RunProgressEvent) {
      // Forward the raw event plus the caller's current view so
      // handlers can decide how to splice it in. Most callers just
      // ``setRun(...)`` with a shallow-merged copy.
      onUpdateRef.current(event, currentRunRef.current);
    }

    function openStream() {
      if (closed) return;
      // SSE connection. EventSource handles auto-reconnect internally;
      // we only need to manage backoff when it fails to open at all.
      es = new ES(`/api/runs/${encodeURIComponent(runId)}/events`);
      lastConnectedAt = Date.now();

      es.addEventListener("open", () => {
        backoffAttempt = 0;
        if (connectionLostTimer !== null) {
          window.clearTimeout(connectionLostTimer);
          connectionLostTimer = null;
        }
        if (fallbackTimer !== null) {
          window.clearInterval(fallbackTimer);
          fallbackTimer = null;
        }
      });

      es.addEventListener("snapshot", (ev) => {
        const data = parseEvent(ev as MessageEvent);
        if (data) mergeEvent({ ...data, type: "snapshot" });
      });
      es.addEventListener("progress", (ev) => {
        const data = parseEvent(ev as MessageEvent);
        if (data) mergeEvent({ ...data, type: "progress" });
      });
      es.addEventListener("status", (ev) => {
        const data = parseEvent(ev as MessageEvent);
        if (data) {
          mergeEvent({ ...data, type: "status" });
          // Terminal status — close so we stop receiving frames.
          if (
            data.status === "completed" ||
            data.status === "failed" ||
            data.status === "canceled"
          ) {
            window.setTimeout(() => close(), 50);
          }
        }
      });
      es.addEventListener("ping", () => {
        // No-op: the keep-alive frame is just to defeat proxy idle
        // timeouts. We don't need to render it.
      });
      es.addEventListener("error", () => {
        // EventSource doesn't give us a typed error event; any
        // error means the connection died. EventSource auto-retries
        // every ~3 s; if it's been more than ``timeoutMs`` since
        // last successful connection, fall back to polling.
        if (es && es.readyState === EventSource.CLOSED) {
          scheduleReconnectOrFallback();
        }
      });
    }

    function scheduleReconnectOrFallback() {
      if (closed) return;
      // If we have been without a working connection for >5 s, fall
      // back to ``getRun`` polling. Otherwise just let EventSource
      // retry on its default schedule.
      if (Date.now() - lastConnectedAt > timeoutMs) {
        startFallbackPolling();
      } else {
        // Light backoff in case the connection fails repeatedly on
        // open (e.g. server in the middle of restarting).
        const delay = Math.min(5_000, 500 * Math.pow(2, backoffAttempt));
        backoffAttempt++;
        window.setTimeout(() => {
          if (!closed) openStream();
        }, delay);
      }
    }

    function startFallbackPolling() {
      if (fallbackTimer !== null) return;
      // Lazy import to avoid pulling ``api.ts`` into every page
      // that uses this hook. ``getRun`` is the same function
      // RunDetail already uses; the fallback just calls it on a
      // timer when SSE is broken.
      import("../api").then(({ getRun }) => {
        if (closed) return;
        const tick = async () => {
          if (closed) return;
          try {
            const detail = await getRun(runId);
            currentRunRef.current = detail;
            onUpdateRef.current(
              {
                type: "snapshot",
                status: detail.status,
                total_queries: detail.progress.total_queries,
                completed_queries: detail.progress.completed_queries,
                error_queries: detail.progress.error_queries,
                current_sample_id: detail.progress.current_sample_id,
                last_heartbeat_at: detail.progress.last_heartbeat_at,
              },
              detail,
            );
          } catch {
            // Network blip — try again next tick.
          }
        };
        void tick();
        fallbackTimer = window.setInterval(() => void tick(), pollMs);
      });
    }

    function close() {
      if (closed) return;
      closed = true;
      if (es) {
        try {
          es.close();
        } catch {
          // Ignore — EventSource.close() is idempotent but in
          // dev environments with proxy intercepts it can throw.
        }
        es = null;
      }
      if (fallbackTimer !== null) {
        window.clearInterval(fallbackTimer);
        fallbackTimer = null;
      }
      if (connectionLostTimer !== null) {
        window.clearTimeout(connectionLostTimer);
        connectionLostTimer = null;
      }
    }

    openStream();
    handleRef.current = { close };
    return () => {
      close();
      handleRef.current = null;
    };
  }, [runId, disabled, EventSourceImpl, fallbackPollMs, fallbackTimeoutMs]);

  // Stable handle so callers can call ``close()`` from effects.
  return {
    close: () => handleRef.current?.close(),
  };
}

export type { EvalRunDetail };
