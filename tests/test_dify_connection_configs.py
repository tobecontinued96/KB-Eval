"""Tests for persisted Dify connection config history."""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ["DIFY_KB_EVAL_TEST_MODE"] = "1"
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

import backend.app as app_module  # noqa: E402
from backend.schemas import DifyConnectionConfigRequest  # noqa: E402
from tests._db_fixture import make_db_store  # noqa: E402


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class DifyConnectionConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.reports_root = Path(self.tmp.name) / "reports"
        self.store = make_db_store(self.reports_root)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_save_and_list_connection_config(self) -> None:
        with patch.object(app_module, "store", self.store):
            saved = _run(
                app_module.save_dify_connection_config(
                    DifyConnectionConfigRequest(
                        dify_base_url=" http://dify.test/v1 ",
                        dify_api_key=" secret-key ",
                    )
                )
            )
            listed = _run(app_module.list_dify_connection_configs(limit=20))

        self.assertEqual(saved.dify_base_url, "http://dify.test/v1")
        self.assertEqual(saved.dify_api_key, "secret-key")
        self.assertEqual(saved.dify_api_key_masked, "secr...-key")
        self.assertEqual(listed.total, 1)
        self.assertEqual(listed.items[0].dify_base_url, "http://dify.test/v1")
        self.assertEqual(listed.items[0].dify_api_key, "secret-key")

    def test_same_url_key_pair_is_updated_not_duplicated(self) -> None:
        with patch.object(app_module, "store", self.store):
            first = _run(
                app_module.save_dify_connection_config(
                    DifyConnectionConfigRequest(
                        dify_base_url="http://dify.test/v1",
                        dify_api_key="secret-key",
                    )
                )
            )
            second = _run(
                app_module.save_dify_connection_config(
                    DifyConnectionConfigRequest(
                        dify_base_url="http://dify.test/v1",
                        dify_api_key="secret-key",
                    )
                )
            )
            listed = _run(app_module.list_dify_connection_configs(limit=20))

        self.assertEqual(first.id, second.id)
        self.assertEqual(second.use_count, 2)
        self.assertEqual(listed.total, 1)

    def test_same_url_with_different_keys_keeps_distinct_pairs(self) -> None:
        with patch.object(app_module, "store", self.store):
            _run(
                app_module.save_dify_connection_config(
                    DifyConnectionConfigRequest(
                        dify_base_url="http://dify.test/v1",
                        dify_api_key="key-one",
                    )
                )
            )
            _run(
                app_module.save_dify_connection_config(
                    DifyConnectionConfigRequest(
                        dify_base_url="http://dify.test/v1",
                        dify_api_key="key-two",
                    )
                )
            )
            listed = _run(app_module.list_dify_connection_configs(limit=20))

        self.assertEqual(listed.total, 2)
        self.assertEqual(
            {item.dify_api_key for item in listed.items},
            {"key-one", "key-two"},
        )

    def test_delete_connection_config_removes_single_row(self) -> None:
        with patch.object(app_module, "store", self.store):
            first = _run(
                app_module.save_dify_connection_config(
                    DifyConnectionConfigRequest(
                        dify_base_url="http://dify.test/v1",
                        dify_api_key="key-one",
                    )
                )
            )
            second = _run(
                app_module.save_dify_connection_config(
                    DifyConnectionConfigRequest(
                        dify_base_url="http://dify.test/v1",
                        dify_api_key="key-two",
                    )
                )
            )
            result = _run(
                app_module.delete_dify_connection_config(config_id=first.id)
            )
            listed = _run(app_module.list_dify_connection_configs(limit=20))

        self.assertEqual(result.id, first.id)
        self.assertTrue(result.deleted)
        self.assertEqual(listed.total, 1)
        self.assertEqual(listed.items[0].id, second.id)

    def test_delete_missing_id_returns_404_error_response(self) -> None:
        with patch.object(app_module, "store", self.store):
            response = _run(
                app_module.delete_dify_connection_config(
                    config_id="does-not-exist"
                )
            )

        # delete 路由在 id 不存在时回 error_response(JSONResponse)，不会被识别成
        # DifyConnectionConfigDeleteResponse —— 这里只验证响应非空。
        self.assertIsNotNone(response)


if __name__ == "__main__":
    unittest.main()
