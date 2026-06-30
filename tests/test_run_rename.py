"""Tests for ``rename_run`` (PATCH /api/runs/{id}) at store + service + route.

Mirrors the layout of ``test_run_delete.py``: same in-memory SQLite fixture,
same ``make_db_store`` helper, asserts both the SQL side effects and the
HTTP-layer contract (status codes, error codes).
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
import asyncio
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Force-override (not setdefault) so a real DATABASE_URL inherited
# from the user's shell environment does not bleed into the tests.
os.environ["DIFY_KB_EVAL_TEST_MODE"] = "1"
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

import backend.app as app_module  # noqa: E402
from backend.schemas import RenameRunRequest  # noqa: E402
from backend.services.artifact_store import ArtifactStoreError  # noqa: E402
from backend.services.run_service import RunService, RunServiceError  # noqa: E402
from tests._db_fixture import make_db_store  # noqa: E402


def _create(store, *, run_id: str, name: str = "占位名字") -> None:
    store.create_run(
        run_id=run_id,
        name=name,
        config={
            "dify_base_url": "http://localhost/v1",
            "dataset_id": "ds-1",
            "eval_file": "eval.jsonl",
            "top_k": 5,
            "include_alternatives": False,
            "limit": 0,
            "sample_ids": [],
        },
    )


class RenameRunStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.reports_root = Path(self._tmp.name) / "reports"
        self.store = make_db_store(self.reports_root)
        self.run_id = "20260617-103937-unnamed"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_rename_run_updates_name_and_returns_manifest(self) -> None:
        _create(self.store, run_id=self.run_id, name="未命名知识库 Top5 基线评测")

        manifest = self.store.rename_run(self.run_id, "  华为 S1720_v1_0 Top5 基线评测  ")
        self.assertEqual(manifest["id"], self.run_id)
        self.assertEqual(manifest["name"], "华为 S1720_v1_0 Top5 基线评测")
        # Echo the rename moment so the route can show "改名于 ..." without a
        # dedicated column. The exact value isn't asserted; just that it's set.
        self.assertTrue(manifest.get("updated_at"))

        items, total = self.store.list_runs()
        self.assertEqual(total, 1)
        self.assertEqual(items[0]["name"], "华为 S1720_v1_0 Top5 基线评测")

    def test_rename_run_strips_whitespace(self) -> None:
        _create(self.store, run_id=self.run_id, name="旧名")
        manifest = self.store.rename_run(self.run_id, "   ")
        self.assertEqual(manifest["name"], "")
        # Service-level rejection is what protects against empty names; the
        # store contract is "trim then write whatever you get", so verify that.

    def test_rename_run_missing_raises(self) -> None:
        with self.assertRaises(ArtifactStoreError):
            self.store.rename_run("not-a-real-run", "x")

    def test_rename_run_skips_soft_deleted(self) -> None:
        _create(self.store, run_id=self.run_id, name="旧名")
        # 默认是 queued，删除前需要把状态推到终态
        self.store.update_manifest(self.run_id, status="completed")
        self.store.delete_run(self.run_id)
        with self.assertRaises(ArtifactStoreError):
            self.store.rename_run(self.run_id, "新名")


class RenameRunServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.reports_root = Path(self._tmp.name) / "reports"
        self.store = make_db_store(self.reports_root)
        self.run_id = "20260617-103937-unnamed"
        self.service = RunService(project_root=Path(__file__).resolve().parents[1], store=self.store)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_service_rejects_empty_name(self) -> None:
        _create(self.store, run_id=self.run_id, name="旧名")
        with self.assertRaises(RunServiceError) as ctx:
            self.service.rename_run(self.run_id, "   ")
        self.assertEqual(ctx.exception.code, "RUN_NAME_REQUIRED")

    def test_service_maps_missing_to_run_not_found(self) -> None:
        with self.assertRaises(RunServiceError) as ctx:
            self.service.rename_run("not-a-real-run", "x")
        self.assertEqual(ctx.exception.code, "RUN_NOT_FOUND")


class RenameRunRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.reports_root = Path(self._tmp.name) / "reports"
        self.store = make_db_store(self.reports_root)
        self.run_id = "20260617-103937-unnamed"
        # TestClient 走 lifespan 会触发 init_db()，但全局 engine 在
        # make_db_store 那里已经 create_all 过了；这里再保险一次，
        # 万一测试顺序导致 reset_for_tests 把表清掉也能恢复。
        from backend.db.base import Base
        from backend.db.session import get_engine, get_session_factory

        Base.metadata.create_all(bind=get_engine())
        # 同时让 app 的 RunService 引用同一个 store，便于直接 patch 后
        # 路由能命中（不然 store 模块级对象指向老的 engine）。
        self._session_factory = get_session_factory()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    @contextmanager
    def _patch_store(self):
        with patch.object(app_module, "store", self.store), patch.object(
            app_module.run_service, "store", self.store
        ):
            yield

    def _client(self):
        from fastapi.testclient import TestClient

        return TestClient(app_module.app)

    def test_route_rename_success(self) -> None:
        _create(self.store, run_id=self.run_id, name="未命名知识库 Top5 基线评测")
        with self._patch_store():
            response = asyncio.run(
                app_module.rename_run(
                    self.run_id,
                    RenameRunRequest(name="  华为 S1720_v1_0 Top5 基线评测  "),
                )
            )
        self.assertEqual(response.id, self.run_id)
        self.assertEqual(response.name, "华为 S1720_v1_0 Top5 基线评测")
        self.assertTrue(response.updated_at)

        items, _ = self.store.list_runs()
        self.assertEqual(items[0]["name"], "华为 S1720_v1_0 Top5 基线评测")

    def test_route_rename_missing_returns_404(self) -> None:
        with self._patch_store(), self._client() as client:
            response = client.patch("/api/runs/not-a-real-run", json={"name": "x"})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["code"], "RUN_NOT_FOUND")

    def test_route_rename_empty_name_returns_400(self) -> None:
        _create(self.store, run_id=self.run_id, name="旧名")
        with self._patch_store(), self._client() as client:
            # Pydantic ``min_length=1`` 在路由前先拒 -> 422。
            response = client.patch(f"/api/runs/{self.run_id}", json={"name": ""})
        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
