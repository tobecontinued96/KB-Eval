"""Unit tests for ``kb_eval.runner_async``.

The hard part of these tests is exercising the concurrency contract
without spinning up a real Dify service. We use ``unittest.mock.AsyncMock``
+ ``asyncio.Event`` to fake an async client whose ``retrieve`` blocks
on an event, so we can assert that 8 concurrent calls actually
overlap (peak in-flight >= 8 with concurrency=8) instead of
serialising on the semaphore.

The artifact-writing tests use ``tmp_path`` for the output dir;
nothing about the on-disk files needs a DB.
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from kb_eval.async_dify_client import AsyncDifyClient  # noqa: E402
from kb_eval.errors import EvalError  # noqa: E402
from kb_eval.runner import EvalRunConfig  # noqa: E402
from kb_eval.runner_async import (  # noqa: E402
    _merge_sample_fields,
    _run_single_query,
    run_evaluation_async,
)


def _run(coro: Any) -> Any:
    """Drive an async coroutine from sync test code.

    ``asyncio.get_event_loop().run_until_complete`` works but
    warns under Python 3.12+; we only need it inside the test
    fixtures, not at module load.
    """

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _make_eval_file(tmp: Path) -> Path:
    """Build a minimal ``.jsonl`` eval file with 8 samples so the
    runner has enough work to expose concurrency behaviour without
    being so big the test gets slow.

    All list fields are populated with a non-empty placeholder so
    ``load_samples`` doesn't reject them as ``missing`` (its check
    is ``if not row.get(field)``, which treats ``[]`` as missing)."""

    samples: list[dict[str, Any]] = []
    for i in range(8):
        samples.append(
            {
                "id": f"S{i:03d}",
                "vendor": "TestCo",
                "model": f"Model-{i}",
                "scenario_type": "single_doc",
                "topic": f"topic-{i}",
                "question": f"query {i}",
                "alternative_queries": [],
                "expected_documents": [f"doc-{i}"],
                "expected_sections": ["sec-x"],
                "expected_keywords": ["kw-x"],
                "evaluation_focus": "recall",
            }
        )
    p = tmp / "eval.jsonl"
    p.write_text("\n".join(json.dumps(s) for s in samples) + "\n", encoding="utf-8")
    return p


def _make_fake_client(retrieve_mock: AsyncMock) -> AsyncDifyClient:
    """Build an ``AsyncDifyClient`` whose ``retrieve`` is the
    supplied ``AsyncMock``. ``list_knowledge_bases`` returns a
    single dataset so dataset resolution succeeds without network."""

    client = AsyncDifyClient("http://dify.test/v1", token="dify-key", timeout=2.0)
    retrieve_mock.return_value = (
        [{"document_id": "doc", "score": 0.5, "content": "x"}],
        10.0,
    )
    client._client = AsyncMock()  # type: ignore[assignment]
    # Patch the resolution methods too so the runner doesn't try to
    # call them on the mocked httpx client.
    client.list_knowledge_bases = AsyncMock(  # type: ignore[method-assign]
        return_value=[
            {
                "dataset_id": "kb-1",
                "name": "TestCo Model-0",
                "vendor": "TestCo",
                "model": "Model-0",
                "documents": [],
            }
        ]
    )
    client.resolve_dataset_id = AsyncMock(  # type: ignore[method-assign]
        return_value=(
            "kb-1",
            {"dataset_id": "kb-1", "name": "TestCo Model-0"},
            [{"dataset_id": "kb-1", "name": "TestCo Model-0"}],
        )
    )
    client.validate_dataset_name = AsyncMock(  # type: ignore[method-assign]
        return_value={"dataset_id": "kb-1", "name": "TestCo Model-0"}
    )
    client.retrieve = retrieve_mock  # type: ignore[method-assign]
    return client


class ConcurrencyTests(unittest.TestCase):
    """The headline guarantee: 8 concurrent retrievals overlap."""

    def test_eight_retrievals_overlap_with_concurrency_eight(self) -> None:
        """When concurrency=8, all 8 retrievals should be in flight
        at once. We block on an event the test releases after a
        short delay, then assert that the peak in-flight count
        reaches 8 (i.e. the semaphore didn't gate to <8)."""

        in_flight = 0
        peak_in_flight = 0
        release = asyncio.Event()
        lock = asyncio.Lock()

        async def fake_retrieve(*args: Any, **kwargs: Any) -> tuple[list[dict[str, Any]], float]:
            nonlocal in_flight, peak_in_flight
            async with lock:
                in_flight += 1
                peak_in_flight = max(peak_in_flight, in_flight)
            # Wait for the test to release everyone simultaneously.
            await release.wait()
            async with lock:
                in_flight -= 1
            return [{"document_id": "x"}], 1.0

        async def scenario() -> dict[str, Any]:
            with tempfile.TemporaryDirectory() as tmp_str:
                tmp = Path(tmp_str)
                eval_file = _make_eval_file(tmp)
                cfg = EvalRunConfig(
                    name="concurrency-test",
                    dify_base_url="http://dify.test/v1",
                    dify_api_key="dify-key",
                    dataset_id="kb-1",
                    eval_file=eval_file,
                    top_k=5,
                )
                retrieve_mock = AsyncMock(side_effect=fake_retrieve)
                client = _make_fake_client(retrieve_mock)
                # Inject the fake client into the runner via monkeypatch
                # of ``AsyncDifyClient.__aenter__``.
                original_aenter = AsyncDifyClient.__aenter__

                def make_aenter(c: AsyncDifyClient):
                    async def aenter(self) -> AsyncDifyClient:
                        return c

                    return aenter

                AsyncDifyClient.__aenter__ = make_aenter(client)  # type: ignore[method-assign]
                try:
                    async def kick_release() -> None:
                        await asyncio.sleep(0.05)
                        release.set()

                    kicker = asyncio.create_task(kick_release())
                    try:
                        result = await run_evaluation_async(
                            cfg, tmp / "out", concurrency=8
                        )
                    finally:
                        await kicker
                    return result
                finally:
                    AsyncDifyClient.__aenter__ = original_aenter  # type: ignore[method-assign]

        result = _run(scenario())
        # 8 samples × 1 query each = 8 retrievals. With concurrency=8
        # the peak in-flight must reach 8 (or at least the configured
        # concurrency) — anything less means we serialised on something.
        self.assertGreaterEqual(
            peak_in_flight,
            8,
            f"expected 8 concurrent retrievals; peak in-flight was only {peak_in_flight}",
        )
        self.assertEqual(result["sample_count"], 8)
        self.assertEqual(result["query_count"], 8)
        self.assertEqual(len(result["rows"]), 8)

    def test_concurrency_two_caps_inflight_at_two(self) -> None:
        """Lowering ``concurrency`` to 2 must throttle peak in-flight
        even when the underlying client would happily serve all 8."""

        in_flight = 0
        peak_in_flight = 0
        lock = asyncio.Lock()
        gate = asyncio.Event()

        async def fake_retrieve(*args: Any, **kwargs: Any) -> tuple[list[dict[str, Any]], float]:
            nonlocal in_flight, peak_in_flight
            async with lock:
                in_flight += 1
                peak_in_flight = max(peak_in_flight, in_flight)
            await gate.wait()
            async with lock:
                in_flight -= 1
            return [{"document_id": "x"}], 1.0

        async def scenario() -> dict[str, Any]:
            with tempfile.TemporaryDirectory() as tmp_str:
                tmp = Path(tmp_str)
                eval_file = _make_eval_file(tmp)
                cfg = EvalRunConfig(
                    name="throttle-test",
                    dify_base_url="http://dify.test/v1",
                    dify_api_key="dify-key",
                    dataset_id="kb-1",
                    eval_file=eval_file,
                    top_k=5,
                )
                retrieve_mock = AsyncMock(side_effect=fake_retrieve)
                client = _make_fake_client(retrieve_mock)
                original_aenter = AsyncDifyClient.__aenter__

                def make_aenter(c: AsyncDifyClient):
                    async def aenter(self) -> AsyncDifyClient:
                        return c

                    return aenter

                AsyncDifyClient.__aenter__ = make_aenter(client)  # type: ignore[method-assign]
                try:
                    async def release_all() -> None:
                        await asyncio.sleep(0.05)
                        gate.set()

                    kicker = asyncio.create_task(release_all())
                    try:
                        result = await run_evaluation_async(
                            cfg, tmp / "out", concurrency=2
                        )
                    finally:
                        await kicker
                    return result
                finally:
                    AsyncDifyClient.__aenter__ = original_aenter  # type: ignore[method-assign]

        _run(scenario())
        self.assertLessEqual(peak_in_flight, 2)
        self.assertGreaterEqual(
            peak_in_flight, 2, "concurrency=2 should allow at least 2 in flight"
        )


class ErrorHandlingTests(unittest.TestCase):
    """Per-query failures must not abort the whole run."""

    def test_eval_error_per_query_becomes_error_row(self) -> None:
        """``retrieve`` raising ``EvalError`` for some samples must
        not stop the others — those samples become ``error_message``
        rows and the rest succeed normally."""

        call_count = 0

        async def fake_retrieve(*args: Any, **kwargs: Any) -> tuple[list[dict[str, Any]], float]:
            nonlocal call_count
            call_count += 1
            if call_count % 2 == 0:
                raise EvalError("simulated Dify error")
            return [{"document_id": "x"}], 5.0

        async def scenario() -> dict[str, Any]:
            with tempfile.TemporaryDirectory() as tmp_str:
                tmp = Path(tmp_str)
                eval_file = _make_eval_file(tmp)
                cfg = EvalRunConfig(
                    name="error-row-test",
                    dify_base_url="http://dify.test/v1",
                    dify_api_key="dify-key",
                    dataset_id="kb-1",
                    eval_file=eval_file,
                    top_k=5,
                )
                retrieve_mock = AsyncMock(side_effect=fake_retrieve)
                client = _make_fake_client(retrieve_mock)
                original_aenter = AsyncDifyClient.__aenter__

                def make_aenter(c: AsyncDifyClient):
                    async def aenter(self) -> AsyncDifyClient:
                        return c

                    return aenter

                AsyncDifyClient.__aenter__ = make_aenter(client)  # type: ignore[method-assign]
                try:
                    return await run_evaluation_async(
                        cfg, tmp / "out", concurrency=4
                    )
                finally:
                    AsyncDifyClient.__aenter__ = original_aenter  # type: ignore[method-assign]

        result = _run(scenario())
        rows = result["rows"]
        # All 8 samples must produce a row, even the ones whose
        # retrieve raised.
        self.assertEqual(len(rows), 8)
        errors = [r for r in rows if r.get("error")]
        successes = [r for r in rows if not r.get("error")]
        self.assertEqual(len(errors), 4)
        self.assertEqual(len(successes), 4)
        for err_row in errors:
            self.assertIn("simulated Dify error", err_row["error"])

    def test_non_eval_error_becomes_internal_error_row(self) -> None:
        """A bare ``RuntimeError`` from Dify must also be
        captured as a row, not crash the whole run."""

        async def fake_retrieve(*args: Any, **kwargs: Any) -> tuple[list[dict[str, Any]], float]:
            raise RuntimeError("upstream blew up")

        async def scenario() -> dict[str, Any]:
            with tempfile.TemporaryDirectory() as tmp_str:
                tmp = Path(tmp_str)
                eval_file = _make_eval_file(tmp)
                cfg = EvalRunConfig(
                    name="runtime-error-test",
                    dify_base_url="http://dify.test/v1",
                    dify_api_key="dify-key",
                    dataset_id="kb-1",
                    eval_file=eval_file,
                    top_k=5,
                )
                retrieve_mock = AsyncMock(side_effect=fake_retrieve)
                client = _make_fake_client(retrieve_mock)
                original_aenter = AsyncDifyClient.__aenter__

                def make_aenter(c: AsyncDifyClient):
                    async def aenter(self) -> AsyncDifyClient:
                        return c

                    return aenter

                AsyncDifyClient.__aenter__ = make_aenter(client)  # type: ignore[method-assign]
                try:
                    return await run_evaluation_async(
                        cfg, tmp / "out", concurrency=2
                    )
                finally:
                    AsyncDifyClient.__aenter__ = original_aenter  # type: ignore[method-assign]

        result = _run(scenario())
        self.assertEqual(len(result["rows"]), 8)
        for row in result["rows"]:
            self.assertIn("Internal runner error", row["error"])
            self.assertIn("upstream blew up", row["error"])


class ArtifactWritingTests(unittest.TestCase):
    """End-of-run artifact shape must match the sync runner's."""

    def test_writes_results_jsonl_summary_csv_report(self) -> None:
        """The async runner must write the same four files the sync
        runner writes (results.jsonl, summary.json, results.csv,
        report.md) so callers that consume ``reports/<run_id>/``
        downstream don't need to special-case which runner produced
        them."""

        async def scenario(tmp: Path) -> None:
            eval_file = _make_eval_file(tmp)
            cfg = EvalRunConfig(
                name="artifact-test",
                dify_base_url="http://dify.test/v1",
                dify_api_key="dify-key",
                dataset_id="kb-1",
                eval_file=eval_file,
                top_k=5,
            )
            retrieve_mock = AsyncMock(
                return_value=([{"document_id": "doc-0"}], 1.0)
            )
            client = _make_fake_client(retrieve_mock)
            original_aenter = AsyncDifyClient.__aenter__

            def make_aenter(c: AsyncDifyClient):
                async def aenter(self) -> AsyncDifyClient:
                    return c

                return aenter

            AsyncDifyClient.__aenter__ = make_aenter(client)  # type: ignore[method-assign]
            try:
                await run_evaluation_async(cfg, tmp / "out", concurrency=4)
            finally:
                AsyncDifyClient.__aenter__ = original_aenter  # type: ignore[method-assign]

        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)

            async def runner() -> None:
                await scenario(tmp)

            _run(runner())

            out_dir = tmp / "out"
            self.assertTrue((out_dir / "results.jsonl").exists())
            self.assertTrue((out_dir / "summary.json").exists())
            self.assertTrue((out_dir / "results.csv").exists())
            self.assertTrue((out_dir / "report.md").exists())
            # ``results.jsonl`` should have one JSON object per line,
            # one per (sample, query) task.
            jsonl_lines = (out_dir / "results.jsonl").read_text(
                encoding="utf-8"
            ).splitlines()
            self.assertEqual(len(jsonl_lines), 8)


class ProgressCoalescingTests(unittest.TestCase):
    """``on_progress`` must be throttled by the coalescer."""

    def test_progress_callback_throttled(self) -> None:
        """The async runner wires the same ``ProgressCoalescer`` as
        the sync runner, so we just need to assert that
        ``on_progress`` is called at least once (initial snapshot)
        and that the final snapshot carries ``completed_queries ==
        query_count`` (final flush). Full coalescer unit tests live
        in ``tests/test_progress_coalescer.py``."""

        captured: list[dict[str, Any]] = []

        async def scenario() -> int:
            with tempfile.TemporaryDirectory() as tmp_str:
                tmp = Path(tmp_str)
                eval_file = _make_eval_file(tmp)
                cfg = EvalRunConfig(
                    name="progress-test",
                    dify_base_url="http://dify.test/v1",
                    dify_api_key="dify-key",
                    dataset_id="kb-1",
                    eval_file=eval_file,
                    top_k=5,
                )
                retrieve_mock = AsyncMock(
                    return_value=([{"document_id": "x"}], 1.0)
                )
                client = _make_fake_client(retrieve_mock)
                original_aenter = AsyncDifyClient.__aenter__

                def make_aenter(c: AsyncDifyClient):
                    async def aenter(self) -> AsyncDifyClient:
                        return c

                    return aenter

                AsyncDifyClient.__aenter__ = make_aenter(client)  # type: ignore[method-assign]

                def on_progress(snapshot: dict[str, Any]) -> None:
                    captured.append(snapshot)

                try:
                    result = await run_evaluation_async(
                        cfg,
                        tmp / "out",
                        concurrency=8,
                        on_progress=on_progress,
                    )
                    return result["query_count"]
                finally:
                    AsyncDifyClient.__aenter__ = original_aenter  # type: ignore[method-assign]

        query_count = _run(scenario())
        # Initial emit + final flush = at least 2 emits for an 8-query run.
        self.assertGreaterEqual(len(captured), 2)
        # The last emitted snapshot must reflect the final state.
        self.assertEqual(captured[-1]["completed_queries"], query_count)
        self.assertEqual(captured[-1]["total_queries"], query_count)
        self.assertEqual(captured[-1]["error_queries"], 0)
        # ``current_sample_id`` should be ``None`` once everything is
        # done (the runner sets it to ``None`` after the final query).
        self.assertIsNone(captured[-1]["current_sample_id"])


class RunSingleQueryHelperTests(unittest.TestCase):
    """Direct tests for ``_run_single_query`` so future refactors of
    the gather loop don't have to round-trip through the whole
    runner to verify the error-handling contract."""

    def test_eval_error_returns_error_row_with_zero_latency(self) -> None:
        async def scenario() -> Any:
            client = AsyncDifyClient("http://dify.test/v1", token="dify-key")
            semaphore = asyncio.Semaphore(1)

            async def boom(*args: Any, **kwargs: Any) -> Any:
                raise EvalError("nope")

            client.retrieve = boom  # type: ignore[method-assign]
            return await _run_single_query(
                client,
                semaphore,
                sample_id="S000",
                query_kind="primary",
                query="q",
                dataset_id="kb",
                top_k=5,
            )

        result = _run(scenario())
        self.assertEqual(result.sample_id, "S000")
        self.assertEqual(result.query_kind, "primary")
        self.assertIn("nope", result.row["error"])
        self.assertEqual(result.row["latency_ms"], 0.0)

    def test_non_eval_error_returns_internal_error_row(self) -> None:
        async def scenario() -> Any:
            client = AsyncDifyClient("http://dify.test/v1", token="dify-key")
            semaphore = asyncio.Semaphore(1)

            async def boom(*args: Any, **kwargs: Any) -> Any:
                raise RuntimeError("kaboom")

            client.retrieve = boom  # type: ignore[method-assign]
            return await _run_single_query(
                client,
                semaphore,
                sample_id="S000",
                query_kind="primary",
                query="q",
                dataset_id="kb",
                top_k=5,
            )

        result = _run(scenario())
        self.assertIn("Internal runner error", result.row["error"])
        self.assertIn("kaboom", result.row["error"])


class MergeSampleFieldsTests(unittest.TestCase):
    def test_overwrites_placeholder_fields(self) -> None:
        row: dict[str, Any] = {
            "sample_id": "PLACEHOLDER",
            "topic": "",
            "expected_documents": [],
        }
        merged = _merge_sample_fields(
            row,
            sample_id="S001",
            topic="real-topic",
            expected_documents=["doc-a", "doc-b"],
            expected_sections=["sec-1"],
            expected_keywords=["kw-1"],
        )
        self.assertEqual(merged["sample_id"], "S001")
        self.assertEqual(merged["topic"], "real-topic")
        self.assertEqual(merged["expected_documents"], ["doc-a", "doc-b"])
        self.assertEqual(merged["expected_sections"], ["sec-1"])
        self.assertEqual(merged["expected_keywords"], ["kw-1"])


if __name__ == "__main__":
    unittest.main()
