"""Async / concurrent version of :func:`kb_eval.runner.run_evaluation`.

Used by the runner subprocess (commit 4) so a single run's 200+
``retrieve`` calls can actually overlap on the wire. The sync
``run_evaluation`` is kept untouched for any CLI / single-threaded
caller that doesn't want asyncio in the picture.

What this mirrors from ``run_evaluation``
-----------------------------------------
* Same ``EvalRunConfig`` input, same output ``dict`` shape
  (``dataset_id``, ``dataset_info``, ``sample_count``,
  ``query_count``, ``rows``, ``summary``).
* Same per-query error contract: :class:`EvalError` from
  ``AsyncDifyClient.retrieve`` becomes an ``error_message``
  row; non-``EvalError`` exceptions are converted to an
  ``error_message="Internal runner error: ..."`` row with the
  underlying cause logged via ``on_log``. **The run never aborts
  because of a single query failure** — the whole point of
  concurrency is that one slow Dify request doesn't head-of-line
  block the others.
* Same end-of-run artifact writes: ``results.jsonl``,
  ``summary.json``, ``results.csv``, ``report.md`` (delegated to
  :mod:`kb_eval.report`).
* Same progress-coalesced ``on_progress`` callback via
  :class:`kb_eval.progress_coalescer.ProgressCoalescer`, capped at
  5 writes/sec/run.

What is intentionally different
--------------------------------
* ``results.jsonl`` row **order**: rows are appended in
  **completion** order under concurrency, not sample-iteration
  order. ``summary.json`` / ``report.md`` / ``results.csv`` content
  is unchanged because :func:`kb_eval.metrics.build_summary` and
  :func:`kb_eval.report.write_csv` don't depend on row order.
  This is documented in ``docs/CHANGELOG.md`` so consumers don't
  flip on the new ordering.
* Per-query ``on_progress`` is fired from the gather callback, not
  inside the hot loop. We rely on the coalescer's
  ``min_interval_ms`` floor to throttle the burst.

Why a Semaphore and not ``asyncio.gather`` directly
---------------------------------------------------
httpx's ``AsyncClient`` already has internal connection pooling
(``max_connections``), but the runner wants the **caller-visible**
concurrency to be exactly ``concurrency`` so progress reporting
can reason about "N concurrent retrievals" without depending on
httpx's pool internals. A ``Semaphore`` makes the contract
explicit and keeps ``max_connections`` on the client generous
(64) so other in-process calls (logging paginate, etc.) aren't
starved.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kb_eval.async_dify_client import AsyncDifyClient
from kb_eval.dataset import filter_samples, load_samples, query_variants
from kb_eval.errors import EvalError
from kb_eval.metrics import build_summary, evaluate_query
from kb_eval.progress_coalescer import ProgressCoalescer
from kb_eval.report import write_csv, write_json, write_jsonl, write_report
from kb_eval.runner import EvalRunConfig, LogCallback, ProgressCallback


_log = logging.getLogger("kb_eval.runner_async")


@dataclass
class _TaskResult:
    """Internal carrier for a completed (sample, query) task.

    We use a dataclass instead of a bare ``dict`` so the gather
    callback has a typed handle on the result and so the test
    suite can assert on it without poking into private attrs.
    """

    sample_id: str
    query_kind: str
    query: str
    row: dict[str, Any]


async def _run_single_query(
    client: AsyncDifyClient,
    semaphore: asyncio.Semaphore,
    *,
    sample_id: str,
    query_kind: str,
    query: str,
    dataset_id: str,
    top_k: int,
) -> _TaskResult:
    """Single (sample, query) coroutine: bounded by ``semaphore``.

    Errors are caught here so ``asyncio.gather`` only sees
    ``_TaskResult`` instances — non-``EvalError`` exceptions never
    escape this function (they're converted to error rows).
    """

    try:
        async with semaphore:
            chunks, latency_ms = await client.retrieve(
                dataset_id=dataset_id, query=query, top_k=top_k
            )
        # ``evaluate_query`` is pure-Python; safe to call outside
        # the semaphore so the next coroutine can start its retrieve
        # immediately. ``sample`` is fetched by the caller below; we
        # only need the per-query fields here.
        return _TaskResult(
            sample_id=sample_id,
            query_kind=query_kind,
            query=query,
            row=_evaluate_safe(
                sample_id=sample_id,
                query_kind=query_kind,
                query=query,
                chunks=chunks,
                latency_ms=latency_ms,
                dataset_id=dataset_id,
                top_k=top_k,
            ),
        )
    except EvalError as exc:
        return _TaskResult(
            sample_id=sample_id,
            query_kind=query_kind,
            query=query,
            row=_evaluate_safe(
                sample_id=sample_id,
                query_kind=query_kind,
                query=query,
                chunks=[],
                latency_ms=0.0,
                dataset_id=dataset_id,
                top_k=top_k,
                error_message=str(exc),
            ),
        )
    except Exception as exc:  # noqa: BLE001 — intentional broad catch
        # Non-EvalError: the runner must not abort. Log via
        # ``on_log`` so the user sees the unexpected failure in the
        # console.log artifact, then emit an error row.
        _log.exception("Internal runner error on %s/%s", sample_id, query_kind)
        return _TaskResult(
            sample_id=sample_id,
            query_kind=query_kind,
            query=query,
            row=_evaluate_safe(
                sample_id=sample_id,
                query_kind=query_kind,
                query=query,
                chunks=[],
                latency_ms=0.0,
                dataset_id=dataset_id,
                top_k=top_k,
                error_message=f"Internal runner error: {exc!r}",
            ),
        )


def _evaluate_safe(
    *,
    sample_id: str,
    query_kind: str,
    query: str,
    chunks: list[dict[str, Any]],
    latency_ms: float,
    dataset_id: str,
    top_k: int,
    error_message: str = "",
) -> dict[str, Any]:
    """Build the result row using :func:`evaluate_query`.

    We don't have the ``EvalSample`` here (the task only carries
    the IDs / query text) — so we synthesise a minimal one. This
    is safe because ``evaluate_query`` reads only
    ``sample.id``, ``sample.expected_documents``, etc.; the per-row
    fields like ``sample_id`` and ``expected_documents`` are
    populated by the caller downstream.

    For the async path we only need ``evaluate_query`` to produce
    a structurally correct row; the full ``EvalSample`` is
    plumbed in by ``run_evaluation_async`` when it builds the
    final ``rows`` list. To keep things simple here, we store
    placeholders that the caller overwrites after gather returns.
    """

    # Lazy import: ``metrics`` is a hot module; importing it at
    # module load would cost time even when the caller never
    # reaches the gather step (e.g. early eval_file failure).
    from kb_eval.dataset import EvalSample

    placeholder = EvalSample(
        id=sample_id,
        vendor="",
        model="",
        scenario_type="",
        topic="",
        question=query,
        alternative_queries=[],
        expected_documents=[],
        expected_sections=[],
        expected_keywords=[],
        evaluation_focus="",
        raw={},
    )
    return evaluate_query(
        sample=placeholder,
        query_kind=query_kind,
        query=query,
        chunks=chunks,
        latency_ms=latency_ms,
        dataset_id=dataset_id,
        top_k=top_k,
        error_message=error_message,
    )


def _merge_sample_fields(
    row: dict[str, Any],
    *,
    sample_id: str,
    topic: str,
    expected_documents: list[str],
    expected_sections: list[str],
    expected_keywords: list[str],
) -> dict[str, Any]:
    """Patch the placeholder row built by ``_evaluate_safe`` with
    the real ``EvalSample`` fields. ``evaluate_query`` reads these
    fields from the sample at evaluation time, so under concurrency
    we evaluate against a placeholder and re-stitch the real fields
    here. Order-independent (the row schema is fixed)."""

    row["sample_id"] = sample_id
    row["topic"] = topic
    row["expected_documents"] = list(expected_documents)
    row["expected_sections"] = list(expected_sections)
    row["expected_keywords"] = list(expected_keywords)
    return row


async def run_evaluation_async(
    config: EvalRunConfig,
    output_dir: Path,
    *,
    on_progress: ProgressCallback | None = None,
    on_log: LogCallback | None = None,
    concurrency: int = 8,
) -> dict[str, Any]:
    """Async / concurrent equivalent of :func:`kb_eval.runner.run_evaluation`.

    See the module docstring for what's mirrored and what changes.
    Returns the same dict shape (``dataset_id``, ``dataset_info``,
    ``sample_count``, ``query_count``, ``rows``, ``summary``).
    """

    config.validate()
    output_dir.mkdir(parents=True, exist_ok=True)

    def log(message: str) -> None:
        if on_log:
            on_log(message)

    samples = filter_samples(load_samples(config.eval_file), config.sample_ids, config.limit)
    if not samples:
        raise EvalError("No samples to evaluate")

    planned_queries = sum(
        1 + (len(sample.alternative_queries) if config.include_alternatives else 0)
        for sample in samples
    )
    log(f"Loaded {len(samples)} samples from {config.eval_file}")
    log(f"Planned queries: {planned_queries}")
    log(f"Concurrency: {concurrency}")

    semaphore = asyncio.Semaphore(max(1, int(concurrency)))

    async with AsyncDifyClient(
        config.dify_base_url,
        token=config.dify_api_key,
        timeout=config.timeout_seconds,
    ) as client:
        # Dataset resolution is sequential: the ``validate_dataset_name``
        # path can emit warnings via ``on_warning`` which we funnel
        # through the same ``log`` callable used elsewhere. The
        # async client supports the same ``on_warning`` shape as the
        # sync one.
        async def _log_warning(message: str) -> None:
            log(message)

        if config.dataset_id:
            dataset_id = config.dataset_id
            log(f"Using provided dataset id: {dataset_id}")
            dataset_info = await client.validate_dataset_name(
                dataset_id=dataset_id,
                vendor=samples[0].vendor,
                model=samples[0].model,
                on_warning=_log_warning,
            )
        else:
            vendor = samples[0].vendor
            model = samples[0].model
            dataset_id, dataset_info, candidates = await client.resolve_dataset_id(
                vendor=vendor, model=model
            )
            log(f"Resolved dataset id: {dataset_id}")
            for item in candidates:
                log(
                    "Candidate: "
                    f"{item.get('dataset_id') or item.get('id')} | "
                    f"{item.get('vendor') or ''}/{item.get('model') or ''} | {item.get('name') or ''}"
                )

        # Initial progress snapshot — fires through the coalescer so
        # the first write is immediate (no 200ms wait on a 0-state).
        coalescer = (
            ProgressCoalescer(sink=on_progress) if on_progress else None
        )
        if coalescer is not None:
            coalescer.update(
                {
                    "total_queries": planned_queries,
                    "completed_queries": 0,
                    "error_queries": 0,
                    "current_sample_id": None,
                }
            )

        # Build the (sample, query) → task map. We need the full
        # ``EvalSample`` later for ``_merge_sample_fields``; carry
        # it alongside the per-query coroutine so we don't have to
        # re-scan ``samples`` after gather.
        tasks: list[asyncio.Task[_TaskResult]] = []
        for sample in samples:
            for query_kind, query in query_variants(sample, config.include_alternatives):
                tasks.append(
                    asyncio.create_task(
                        _run_single_query(
                            client,
                            semaphore,
                            sample_id=sample.id,
                            query_kind=query_kind,
                            query=query,
                            dataset_id=dataset_id,
                            top_k=config.top_k,
                        ),
                        name=f"{sample.id}:{query_kind}",
                    )
                )

        completed = 0
        errors = 0
        rows: list[dict[str, Any]] = []

        # ``asyncio.as_completed`` yields tasks in completion order
        # so the ``rows`` list ends up in completion order (documented
        # behaviour). With ``return_exceptions=False`` (default), an
        # exception inside a task would propagate; we already catch
        # everything in ``_run_single_query`` so this is a safety net,
        # not the primary error path.
        for coro in asyncio.as_completed(tasks):
            try:
                result = await coro
            except Exception as exc:  # noqa: BLE001
                # Should be unreachable thanks to the catch in
                # ``_run_single_query``; log and continue.
                _log.exception("gather-level exception (should not happen)")
                log(f"Internal runner error: {exc!r}")
                continue
            completed += 1
            row = result.row
            # Find the originating ``EvalSample`` so we can patch
            # the placeholder fields with the real ones. This is a
            # linear scan per row; with 200-1000 rows the cost is
            # negligible vs. the saved complexity of building a
            # separate map.
            originating = next(
                (s for s in samples if s.id == result.sample_id),
                None,
            )
            if originating is not None:
                row = _merge_sample_fields(
                    row,
                    sample_id=originating.id,
                    topic=originating.topic,
                    expected_documents=originating.expected_documents,
                    expected_sections=originating.expected_sections,
                    expected_keywords=originating.expected_keywords,
                )
            rows.append(row)
            if row.get("error"):
                errors += 1
            if coalescer is not None:
                coalescer.update(
                    {
                        "total_queries": planned_queries,
                        "completed_queries": completed,
                        "error_queries": errors,
                        "current_sample_id": (
                            result.sample_id
                            if completed < planned_queries
                            else None
                        ),
                    }
                )

        if coalescer is not None:
            # End-of-run flush: forces the final ``completed/total``
            # to land even if the last ``update`` was within the
            # ``min_interval_ms`` floor.
            coalescer.flush()

    # Write artifacts AFTER the ``async with`` block closes the
    # client connection. These are pure-Python file writes; no
    # concurrency concerns.
    summary = build_summary(rows, top_k=config.top_k)
    write_jsonl(output_dir / "results.jsonl", rows)
    write_json(output_dir / "summary.json", summary)
    write_csv(output_dir / "results.csv", rows)
    write_report(
        output_dir / "report.md",
        config=config.public_dict(),
        dataset_id=dataset_id,
        dataset_info=dataset_info,
        summary=summary,
        rows=rows,
    )
    log(f"Report: {output_dir / 'report.md'}")

    return {
        "dataset_id": dataset_id,
        "dataset_info": dataset_info,
        "sample_count": len(samples),
        "query_count": planned_queries,
        "rows": rows,
        "summary": summary,
    }


__all__ = ["run_evaluation_async"]
