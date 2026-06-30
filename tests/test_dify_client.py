"""Unit tests for the Dify direct-connection adapter."""

from __future__ import annotations

import asyncio
import json
import sys
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

from kb_eval.async_dify_client import AsyncDifyClient  # noqa: E402
from kb_eval.dify_client import (  # noqa: E402
    DifyClient,
    build_dify_retrieval_model,
    normalize_dify_record,
)
from kb_eval.errors import EvalError  # noqa: E402


def _run(coro: Any) -> Any:
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


class DifyMappingTests(unittest.TestCase):
    def test_normalize_dify_record_maps_segment_shape_to_eval_chunk(self) -> None:
        chunk = normalize_dify_record(
            {
                "score": 0.91,
                "segment": {
                    "id": "seg-1",
                    "document_id": "doc-1",
                    "content": "hello chunk",
                    "keywords": ["hello"],
                    "document": {"id": "doc-1", "name": "manual.pdf"},
                },
            }
        )

        self.assertEqual(chunk["document_id"], "doc-1")
        self.assertEqual(chunk["document_name"], "manual.pdf")
        self.assertEqual(chunk["content"], "hello chunk")
        self.assertEqual(chunk["score"], 0.91)
        self.assertEqual(chunk["keywords"], ["hello"])
        self.assertEqual(chunk["source"], {"document_name": "manual.pdf"})

    def test_build_retrieval_model_overrides_top_k_and_preserves_config(self) -> None:
        model = build_dify_retrieval_model(
            {"search_method": "hybrid_search", "reranking_enable": True, "top_k": 3},
            8,
        )

        self.assertEqual(model["search_method"], "hybrid_search")
        self.assertTrue(model["reranking_enable"])
        self.assertEqual(model["top_k"], 8)

    def test_dify_client_requires_api_key(self) -> None:
        with self.assertRaises(EvalError):
            DifyClient("http://dify.test", token="")


class AsyncDifyClientTests(unittest.TestCase):
    def test_list_knowledge_bases_uses_dify_auth_and_normalizes_items(self) -> None:
        captured: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["method"] = request.method
            captured["url"] = str(request.url)
            captured["headers"] = dict(request.headers)
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "kb-1",
                            "name": "Huawei S1720",
                            "description": "test kb",
                            "document_count": 2,
                            "embedding_model": "bge-large-zh",
                            "retrieval_model_dict": {"search_method": "semantic_search", "top_k": 4},
                        }
                    ],
                    "total": 1,
                    "page": 1,
                    "limit": 100,
                    "has_more": False,
                },
            )

        client = AsyncDifyClient("http://dify.test", token="dify-key")
        client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))  # type: ignore[assignment]

        items = _run(client.list_knowledge_bases())

        self.assertEqual(captured["method"], "GET")
        self.assertIn("/v1/datasets", captured["url"])
        self.assertEqual(captured["headers"].get("authorization"), "Bearer dify-key")
        self.assertEqual(items[0]["dataset_id"], "kb-1")
        self.assertEqual(items[0]["embedding_model"], "bge-large-zh")
        self.assertEqual(client._retrieval_models["kb-1"]["top_k"], 4)

    def test_retrieve_sends_dify_payload_and_maps_records(self) -> None:
        captured: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["method"] = request.method
            captured["url"] = str(request.url)
            captured["body"] = json.loads(request.content)
            captured["headers"] = dict(request.headers)
            return httpx.Response(
                200,
                json={
                    "records": [
                        {
                            "score": 0.8,
                            "segment": {
                                "document_id": "doc-2",
                                "content": "answer text",
                                "document": {"id": "doc-2", "name": "guide.pdf"},
                            },
                        }
                    ]
                },
            )

        client = AsyncDifyClient("http://dify.test/v1", token="dify-key")
        client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))  # type: ignore[assignment]
        client._retrieval_models["kb-2"] = {"search_method": "hybrid_search", "top_k": 1}

        chunks, latency_ms = _run(client.retrieve(dataset_id="kb-2", query="question", top_k=5))

        self.assertEqual(captured["method"], "POST")
        self.assertIn("/v1/datasets/kb-2/retrieve", captured["url"])
        self.assertEqual(captured["headers"].get("authorization"), "Bearer dify-key")
        self.assertEqual(captured["body"]["query"], "question")
        self.assertEqual(captured["body"]["retrieval_model"]["search_method"], "hybrid_search")
        self.assertEqual(captured["body"]["retrieval_model"]["top_k"], 5)
        self.assertEqual(chunks[0]["document_name"], "guide.pdf")
        self.assertEqual(chunks[0]["content"], "answer text")
        self.assertGreaterEqual(latency_ms, 0.0)


if __name__ == "__main__":
    unittest.main()
