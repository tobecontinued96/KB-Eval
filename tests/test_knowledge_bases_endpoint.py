"""Tests for the GET /api/knowledge-bases Dify route.

The route forwards ``dify_base_url``/``dify_api_key`` from the query
string to ``RunService.list_knowledge_bases``, which talks to Dify.
To stay hermetic these tests stub the service layer directly and assert
the route contract: required ``dify_base_url``, keyword passthrough,
and RunServiceError → 502 conversion.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Avoid leaking a real DATABASE_URL into the test process.
os.environ["DIFY_KB_EVAL_TEST_MODE"] = "1"
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

import backend.app as app_module  # noqa: E402
from backend.services.run_service import RunServiceError  # noqa: E402


def _ok_service(items, *, total=None):
    """Build a fake service method that records its kwargs and returns canned data."""
    captured = {}

    def _list(*, dify_base_url, dify_api_key="", keyword=None, limit=50, offset=0):
        captured.update(
            dify_base_url=dify_base_url,
            dify_api_key=dify_api_key,
            keyword=keyword,
            limit=limit,
            offset=offset,
        )
        return {
            "items": items,
            "total": total if total is not None else len(items),
            "limit": limit,
            "offset": offset,
        }

    _list.captured = captured  # type: ignore[attr-defined]
    return _list


def _run(coro):
    """Drive an async route function synchronously from a unit test."""
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


class KnowledgeBasesEndpointTests(unittest.TestCase):
    # ---- happy path ----

    def test_returns_items_passthrough(self) -> None:
        items = [
            {
                "dataset_id": "kb-1",
                "name": "Huawei S1720 知识库",
                "display_name": "Huawei S1720 KB",
                "vendor": "华为",
                "model": "S1720",
                "description": "",
                "document_count": 10,
            }
        ]
        with patch.object(app_module.run_service, "list_knowledge_bases", _ok_service(items)):
            response = _run(
                app_module.list_knowledge_bases(
                    dify_base_url="http://dify.test/v1",
                    dify_api_key="tkn",
                    keyword=None,
                    limit=50,
                    offset=0,
                )
            )
        self.assertEqual(response.items[0].dataset_id, "kb-1")
        self.assertEqual(response.items[0].vendor, "华为")
        self.assertEqual(response.total, 1)

    def test_passes_query_kwargs_to_service(self) -> None:
        service = _ok_service([])
        with patch.object(app_module.run_service, "list_knowledge_bases", service):
            _run(
                app_module.list_knowledge_bases(
                    dify_base_url="http://dify.test/v1",
                    dify_api_key="abc",
                    keyword="huawei",
                    limit=25,
                    offset=10,
                )
            )
        self.assertEqual(service.captured["dify_base_url"], "http://dify.test/v1")
        self.assertEqual(service.captured["dify_api_key"], "abc")
        self.assertEqual(service.captured["keyword"], "huawei")
        self.assertEqual(service.captured["limit"], 25)
        self.assertEqual(service.captured["offset"], 10)

    # ---- error path ----

    def test_run_service_error_returns_502(self) -> None:
        def _raise(**_kwargs):
            raise RunServiceError("DIFY_LIST_FAILED", "Cannot reach Dify", {})

        with patch.object(app_module.run_service, "list_knowledge_bases", _raise):
            response = _run(
                app_module.list_knowledge_bases(
                    dify_base_url="http://dify.test/v1",
                    dify_api_key="",
                    keyword=None,
                    limit=50,
                    offset=0,
                )
            )
        # error_response(...) returns a JSONResponse
        self.assertEqual(response.status_code, 502)
        body = json.loads(response.body)
        self.assertEqual(body["code"], "DIFY_LIST_FAILED")
        self.assertIn("Cannot reach Dify", body["message"])


if __name__ == "__main__":
    unittest.main()
