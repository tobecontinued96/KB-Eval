"""Entry point for the runner subprocess (commit 4's spawn target).

The supervisor (:class:`backend.services.runner_supervisor.RunnerSupervisor`)
launches this as a separate Python process so the FastAPI parent
event loop stays free of long-running ``urlopen`` calls. This
module is intentionally tiny: a top-level function that takes
primitive args (so it's pickle-able under ``spawn``) and an
import block that re-establishes DB and store connections inside
the child.

Why a top-level function and not a method
-----------------------------------------
``multiprocessing.spawn`` pickles the target and its args to hand
them to a fresh Python interpreter. Class methods can't be pickled
directly (the class itself isn't passed). A top-level function
is the simplest pickle-able shape and the easiest to test (call
the function directly in a unit test, no supervisor needed).

Why the main loop is ``async``
-------------------------------
``_execute_one_run`` calls :func:`kb_eval.runner_async.run_evaluation_async`
which runs 8-way concurrent ``httpx.AsyncClient`` retrieves. Those
need a running event loop, so ``asyncio.create_task(...)`` is used
to dispatch in-flight runs from the orchestrator. A sync ``while
True`` would raise ``RuntimeError: no running event loop`` —
which is what bit us in commit 4 production. The orchestrator
has to be a coroutine; the *spawn target* has to be sync (because
``spawn`` can't pickle a coroutine). Hence the split:
:func:`_runner_main` is the sync entry, it wraps
:func:`_runner_main_async` via ``asyncio.run``.

What this loop does
-------------------
1. **Tick** every ``tick_ms`` milliseconds (default 500).
2. **Claim** up to ``concurrency - in_flight`` queued runs.
3. **Spawn** an ``asyncio.Task`` per claimed run; each run uses
   the same shared event loop and finishes before the next tick.
4. **Detect cancel** on every coalesced progress flush: if the
   row's status changed (e.g. user DELETEd the run), the
   in-flight coroutine aborts on the next flush.
5. **Reap** finished runs: any completed/failed/canceled row in
   the in-flight set is replaced with a new claim on the next
   tick.

Cancellation contract
---------------------
Cancellation is **cooperative**: the runner checks the row's
status on every coalesced progress flush. For the common case of
"user clicked Delete on a run that's mid-evaluate", worst-case
latency is ``coalescer.max_interval_ms = 1000ms`` before the
runner notices — acceptable for a human-driven action.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

_log = logging.getLogger("kb_eval.runner_subprocess")

if TYPE_CHECKING:
    from kb_eval.runner import EvalRunConfig


def _now_iso() -> str:
    import datetime as dt

    return dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="seconds")


def _build_db_store(database_url: str, reports_root: Path):
    """Construct the SQLAlchemy engine + DBStore fresh inside the
    child process.

    This is the critical bit that ``spawn`` requires: anything
    module-level (engine pool, session factory) belongs to the
    parent process and can't be inherited — we have to rebuild
    it from the primitive ``database_url`` argument.
    """

    # Late imports so the module is importable without a configured
    # DB (e.g. for ``python -c "import kb_eval.runner_subprocess"``
    # sanity checks in CI).
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from backend.db.models import Base  # noqa: F401 — register tables
    from backend.db.session import (
        DB_CONNECT_TIMEOUT_SECONDS,
        _normalize_url,
    )
    from backend.services.artifact_store import ArtifactStore
    from backend.services.db_store import DBStore

    url = _normalize_url(database_url)
    connect_args: dict[str, Any] = {}
    if url.startswith("postgresql"):
        connect_args["connect_timeout"] = DB_CONNECT_TIMEOUT_SECONDS
    engine = create_engine(
        url,
        pool_pre_ping=True,
        future=True,
        connect_args=connect_args,
    )
    # ``Base.metadata.create_all`` is a no-op on a DB that's already
    # up to schema; running it here covers the dev case where the
    # child starts before any ``alembic upgrade head`` has fired.
    # We deliberately do NOT call ``require_alembic_head`` in the
    # child — that's the parent's job at ``lifespan`` startup.
    Base.metadata.create_all(bind=engine)
    sm = sessionmaker(bind=engine, expire_on_commit=False)

    artifact_store = ArtifactStore(reports_root)
    store = DBStore(artifact_store=artifact_store, session_factory=sm)
    return store, sm, engine


def _build_config_from_row(row: Any) -> "EvalRunConfig":
    """Build an ``EvalRunConfig`` from a SQLAlchemy ``Run`` row.

    The config columns are already on ``Run`` (commit 4 doesn't
    add any); this just plumbs them into the frozen dataclass.
    """

    from kb_eval.runner import EvalRunConfig

    return EvalRunConfig(
        name=row.name or "",
        dify_base_url=row.dify_base_url or "",
        dify_api_key="",  # intentionally not stored in DB; user supplies via UI
        dataset_id=row.dataset_id or "",
        eval_file=Path(row.eval_file or ""),
        top_k=int(row.top_k or 5),
        include_alternatives=bool(row.include_alternatives),
        limit=int(row.limit or 0),
        sample_ids=list(row.sample_ids or []),
        timeout_seconds=float(row.timeout_seconds or 60),
        embedding_model=row.embedding_model or "",
        rerank_model=row.rerank_model or "",
    )


async def _execute_one_run(
    run_id: str,
    config: Any,
    store: Any,
    concurrency: int,
    cancellation_event: asyncio.Event,
) -> None:
    """Run a single evaluation end-to-end, with progress/heartbeat
    callbacks wired into the existing ``DBStore`` and a
    cancellation check on every flush.

    Cancellation
    ------------
    The ``on_progress`` callback is wrapped to (a) write to DB via
    the existing ``update_progress`` path (which also writes
    ``last_heartbeat_at``, feeding the watchdog) and (b) check
    whether the row's status has changed away from ``running``; if
    so, it sets ``cancellation_event`` so the gather loop's next
    iteration aborts.
    """

    from kb_eval.logging_config import reset_run_id, set_run_id
    from kb_eval.runner_async import run_evaluation_async

    token = set_run_id(run_id)
    try:
        run_dir = store.run_dir(run_id)
        started_at = _now_iso()
        store.update_manifest(run_id, status="running", started_at=started_at, error="")
        store.append_log(run_id, "Run started (subprocess)")
        _log.info("run started")

        def _on_progress(progress: dict[str, Any]) -> None:
            # Always update the DB (this is also the heartbeat).
            store.update_progress(run_id, progress)
            # Check for cancel: if the row was transitioned to
            # ``canceled`` by the DELETE handler, bail out.
            status = store.get_status(run_id)
            if status is None or status != "running":
                cancellation_event.set()

        def _on_log(line: str) -> None:
            store.append_log(run_id, line)

        try:
            result = await run_evaluation_async(
                config,
                run_dir,
                on_progress=_on_progress,
                on_log=_on_log,
                concurrency=concurrency,
            )
            status = store.get_status(run_id)
            if status == "running":
                summary = result.get("summary") or {}
                overall = summary.get("overall") if isinstance(summary, dict) else {}
                store.update_manifest(
                    run_id,
                    status="completed",
                    finished_at=_now_iso(),
                    dataset_id=result.get("dataset_id") or config.dataset_id,
                    sample_count=result.get("sample_count", 0),
                    query_count=result.get("query_count", 0),
                    metrics=overall if isinstance(overall, dict) else {},
                )
                store.persist_run_artifacts(run_id)
                store.append_log(run_id, "Run completed")
                _log.info("run completed")
        except Exception as exc:  # noqa: BLE001
            store.append_log(run_id, f"Run failed: {exc}")
            _log.exception("run failed: %s", exc)
            status = store.get_status(run_id)
            if status == "running":
                store.update_manifest(
                    run_id,
                    status="failed",
                    finished_at=_now_iso(),
                    error=str(exc),
                )
    finally:
        reset_run_id(token)


async def _runner_main_async(
    database_url: str,
    reports_root: str,
    tick_ms: int = 500,
    concurrency: int = 8,
) -> None:
    """Async main loop. Runs forever (until cancelled).

    See :func:`_runner_main` for the sync spawn-target wrapper
    that calls ``asyncio.run`` on this. ``spawn`` can't pickle a
    coroutine directly, so the spawn target has to be synchronous;
    inside it we hand control to this async coroutine.

    Why this is async, not ``while True: time.sleep``
    ----------------------------------------------
    The actual work happens in ``_execute_one_run`` which calls
    :func:`kb_eval.runner_async.run_evaluation_async` (8-way
    concurrent httpx). That code needs a running asyncio event
    loop. ``asyncio.create_task(...)`` from inside a sync ``while
    True`` raises ``RuntimeError: no running event loop`` — which
    is what bit us in commit 4 production. The whole orchestrator
    has to be a coroutine so the in-flight tasks can be created
    and reaped in the same loop.

    Signature must stay pickle-compatible: only primitive args, no
    default mutable objects, no lambdas.
    """

    # Re-import everything we need inside the child. The parent
    # passes us only strings/ints; we build the engine ourselves.
    from backend.services.runner_claim import claim_queued_runs

    reports_path = Path(reports_root)
    from kb_eval.logging_config import configure_logging

    configure_logging(
        app_name="runner",
        project_root=reports_path.parent,
        force=True,
    )
    store, session_factory, engine = _build_db_store(database_url, reports_path)

    tick_seconds = max(0.05, float(tick_ms) / 1000.0)
    in_flight: dict[str, asyncio.Task[Any]] = {}

    _log.info(
        "runner subprocess starting: pid=%d reports_root=%s concurrency=%d tick=%.2fs",
        os.getpid(),
        reports_root,
        concurrency,
        tick_seconds,
    )

    try:
        while True:
            try:
                # Reap finished in-flight runs. ``task.done()`` is
                # safe to call from the loop's thread.
                finished = [
                    rid
                    for rid, task in in_flight.items()
                    if task.done()
                ]
                for rid in finished:
                    task = in_flight.pop(rid)
                    try:
                        task.result()
                    except Exception as exc:  # noqa: BLE001
                        _log.exception("run %s raised: %s", rid, exc)

                # Claim more work to fill the concurrency budget.
                remaining = max(0, concurrency - len(in_flight))
                if remaining > 0:
                    # ``claim_queued_runs`` is sync SQLAlchemy; run
                    # it on a worker thread so we don't block the
                    # event loop while waiting for PG.
                    claimed = await asyncio.to_thread(
                        claim_queued_runs, session_factory, remaining
                    )
                    for rid in claimed:
                        # Fetch the row to build the config.
                        try:
                            from backend.db.models import Run
                            from sqlalchemy import select

                            with session_factory() as session:
                                run_row = session.execute(
                                    select(Run).where(Run.id == rid)
                                ).scalar_one_or_none()
                            if run_row is None:
                                _log.warning("claimed %s but row vanished", rid)
                                continue
                            config = _build_config_from_row(run_row)
                            cancel_event: asyncio.Event = asyncio.Event()
                            task = asyncio.create_task(
                                _execute_one_run(
                                    rid,
                                    config,
                                    store,
                                    concurrency=concurrency,
                                    cancellation_event=cancel_event,
                                ),
                                name=f"run:{rid}",
                            )
                            in_flight[rid] = task
                            _log.info(
                                "claimed run %s (in_flight=%d)", rid, len(in_flight)
                            )
                        except Exception as exc:  # noqa: BLE001
                            _log.exception("failed to start run %s: %s", rid, exc)
                            store.update_manifest(
                                rid,
                                status="failed",
                                finished_at=_now_iso(),
                                error=f"startup error: {exc!r}",
                            )

            except Exception as exc:  # noqa: BLE001
                # Defensive: a DB blip shouldn't kill the loop.
                _log.exception("tick failed: %s", exc)

            # Yield to the event loop so the in-flight tasks can
            # progress. ``asyncio.sleep`` is the async equivalent
            # of ``time.sleep``; using ``time.sleep`` here would
            # block the loop and stall every in-flight retrieve.
            await asyncio.sleep(tick_seconds)
    except asyncio.CancelledError:
        _log.info(
            "runner subprocess cancelled; cancelling %d runs",
            len(in_flight),
        )
        raise
    finally:
        # Best-effort shutdown of in-flight coroutines.
        for rid, task in in_flight.items():
            task.cancel()
        for rid, task in in_flight.items():
            try:
                await asyncio.wait_for(task, timeout=5.0)
            except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
                pass
        try:
            engine.dispose()
        except Exception:  # noqa: BLE001
            pass
        _log.info("runner subprocess exiting")


def _runner_main(
    database_url: str,
    reports_root: str,
    tick_ms: int = 500,
    concurrency: int = 8,
) -> None:
    """Synchronous spawn target wrapping :func:`_runner_main_async`.

    ``multiprocessing.spawn`` requires a synchronous callable —
    it pickles the target and the args, then execs them in a
    fresh interpreter. ``asyncio.run`` here establishes the event
    loop the orchestrator needs.
    """

    asyncio.run(
        _runner_main_async(
            database_url=database_url,
            reports_root=reports_root,
            tick_ms=tick_ms,
            concurrency=concurrency,
        )
    )


__all__ = [
    "_runner_main",
    "_runner_main_async",
    "_build_db_store",
    "_build_config_from_row",
]


def _runner_main_sync_wrapper(*args: Any, **kwargs: Any) -> None:
    """Deprecated shim. Use :func:`_runner_main` (which is itself
    a sync wrapper around :func:`_runner_main_async`). Kept for
    any out-of-tree caller that might still import this name."""

    _runner_main(*args, **kwargs)
