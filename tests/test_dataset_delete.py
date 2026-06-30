"""Tests for the dataset deletion helper.

The helper is reachable via ``backend.services.dataset_edit_service.delete_dataset``
and is exposed to the FastAPI layer through ``DELETE /api/datasets/{path:path}``.

The tests cover:

* The path allow-list is enforced (caller is expected to use
  ``resolve_editable_path`` first; we reuse it in the API layer).
* A backup file with a timestamp suffix is created.
* The main JSONL, the draft JSONL, and the review meta JSON are all removed.
* A second delete on the same path returns ``DATASET_NOT_FOUND``.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# Make ``backend`` importable when running via unittest.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.services.dataset_edit_service import (  # noqa: E402
    DatasetEditError,
    delete_dataset,
    resolve_editable_path,
)
from backend.services.dataset_review_service import (  # noqa: E402
    draft_path_for,
    review_meta_path_for,
)
import backend.app as app_module  # noqa: E402


class DeleteDatasetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.dataset_dir = self.root / "datasets" / "generated"
        self.dataset_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dataset_dir / "sample.jsonl"
        rows = [
            {"id": "S-1", "question": "q1"},
            {"id": "S-2", "question": "q2"},
        ]
        self.path.write_text(
            "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
            encoding="utf-8",
        )
        # 草稿和审核元信息
        self.draft = draft_path_for(self.path)
        self.draft.write_text(
            json.dumps({"id": "DRAFT-1", "question": "draft"}, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        self.review = review_meta_path_for(self.path)
        self.review.write_text(
            json.dumps({"status": "draft", "generated_at": "2026-06-15T00:00:00+08:00"}, ensure_ascii=False),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)
        self.tmp.cleanup()

    def test_resolve_path_then_delete_removes_main_draft_and_review(self) -> None:
        allowed = [self.root / "datasets"]
        resolved = resolve_editable_path(str(self.path.relative_to(self.root)), allowed)
        result = delete_dataset(resolved)
        self.assertTrue(Path(result["backup_path"]).exists())
        self.assertFalse(self.path.exists())
        self.assertFalse(self.draft.exists())
        self.assertFalse(self.review.exists())
        # 备份内容应当是主文件未删前的内容
        backup_lines = Path(result["backup_path"]).read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(backup_lines), 2)

    def test_delete_twice_raises_not_found(self) -> None:
        allowed = [self.root / "datasets"]
        resolved = resolve_editable_path(str(self.path.relative_to(self.root)), allowed)
        delete_dataset(resolved)
        with self.assertRaises(DatasetEditError) as ctx:
            delete_dataset(resolved)
        self.assertEqual(ctx.exception.code, "DATASET_NOT_FOUND")

    def test_delete_path_outside_allow_list_is_rejected(self) -> None:
        # Allow-list enforcement is the responsibility of ``resolve_editable_path``
        # in the API layer, not of ``delete_dataset`` itself. We assert that
        # here by feeding a forbidden path through the resolver first.
        other = self.root / "outside.jsonl"
        other.write_text("{}", encoding="utf-8")
        with self.assertRaises(DatasetEditError) as ctx:
            resolve_editable_path(str(other), [self.root / "datasets"])
        # 路径存在但不在白名单里：resolve_editable_path 报 DATASET_PATH_FORBIDDEN
        self.assertIn(ctx.exception.code, {"DATASET_PATH_FORBIDDEN", "DATASET_NOT_FOUND"})
        self.assertTrue(other.exists())

    def test_delete_route_returns_success_after_removing_dataset(self) -> None:
        with patch.object(app_module, "DATASET_ROOTS", [self.root / "datasets"]):
            response = asyncio.run(
                app_module.delete_dataset_route("datasets/generated/sample.jsonl")
            )

        self.assertEqual(response.status_code, 200, response.body.decode("utf-8"))
        payload = json.loads(response.body)
        # path 字段经过 _display_path 走 DATASET_ROOTS 回退，
        # 相对补丁后的 root 会得到 "generated/sample.jsonl" 这类相对路径。
        self.assertTrue(payload["path"].endswith("generated/sample.jsonl"))
        self.assertTrue(Path(payload["backup_path"]).exists())
        self.assertFalse(self.path.exists())


if __name__ == "__main__":
    unittest.main()
