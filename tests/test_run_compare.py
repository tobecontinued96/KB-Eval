"""Tests for embedding_model / rerank_model field round-trip on the run store.

PR 1 only covers the storage layer: new fields are nullable so old rows
don't need migration, the schema serializes them, and the store normalizes
empty strings to NULL on write. The actual ``compare_runs`` API lives in
PR 2 and gets its own test file then.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Force-override (not setdefault) so a real DATABASE_URL inherited
# from the user's shell environment does not bleed into the tests.
os.environ["DIFY_KB_EVAL_TEST_MODE"] = "1"
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

from sqlalchemy import select  # noqa: E402

from backend.db.models import Run  # noqa: E402
from backend.db.session import get_session_factory  # noqa: E402
from backend.schemas import RunDetailResponse, RunListItem  # noqa: E402
from tests._db_fixture import make_db_store  # noqa: E402


def _create(store, *, run_id: str, config: dict | None = None) -> None:
    cfg = {
        "dify_base_url": "http://localhost/v1",
        "dataset_id": "ds-1",
        "eval_file": "eval.jsonl",
        "top_k": 5,
        "include_alternatives": False,
        "limit": 0,
        "sample_ids": [],
    }
    if config:
        cfg.update(config)
    store.create_run(run_id=run_id, name=f"name-{run_id}", config=cfg)
    store.update_manifest(run_id, status="completed")


class EmbeddingRerankFieldTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.reports_root = Path(self._tmp.name) / "reports"
        self.store = make_db_store(self.reports_root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_create_run_persists_both_fields(self) -> None:
        _create(
            self.store,
            run_id="20260617-100000-bge",
            config={
                "embedding_model": "bge-large-zh",
                "rerank_model": "bge-reranker-base",
            },
        )

        with get_session_factory()() as session:
            row = session.execute(
                select(Run).where(Run.id == "20260617-100000-bge")
            ).scalar_one()
        self.assertEqual(row.embedding_model, "bge-large-zh")
        self.assertEqual(row.rerank_model, "bge-reranker-base")

    def test_create_run_persists_dify_base_url(self) -> None:
        _create(
            self.store,
            run_id="20260617-100050-dify",
            config={"dify_base_url": "http://dify.local/v1"},
        )

        with get_session_factory()() as session:
            row = session.execute(
                select(Run).where(Run.id == "20260617-100050-dify")
            ).scalar_one()
        self.assertEqual(row.dify_base_url, "http://dify.local/v1")

    def test_create_run_normalizes_empty_strings_to_null(self) -> None:
        _create(
            self.store,
            run_id="20260617-100100-empty",
            config={"embedding_model": "   ", "rerank_model": ""},
        )

        with get_session_factory()() as session:
            row = session.execute(
                select(Run).where(Run.id == "20260617-100100-empty")
            ).scalar_one()
        # 空串 / 纯空白被归一为 NULL，而不是存空字符串。
        self.assertIsNone(row.embedding_model)
        self.assertIsNone(row.rerank_model)

    def test_create_run_without_fields_leaves_them_null(self) -> None:
        # 旧 run 没填这两个字段，应该不报错，DB 列保持 NULL。
        _create(self.store, run_id="20260617-100200-legacy")

        with get_session_factory()() as session:
            row = session.execute(
                select(Run).where(Run.id == "20260617-100200-legacy")
            ).scalar_one()
        self.assertIsNone(row.embedding_model)
        self.assertIsNone(row.rerank_model)

    def test_list_runs_manifest_exposes_both_fields(self) -> None:
        _create(
            self.store,
            run_id="20260617-100300-listed",
            config={
                "embedding_model": "text-embedding-v3",
                "rerank_model": "qwen3-rerank",
            },
        )
        # 加一条不带字段的旧 run，验证 manifest 同时支持 NULL 与真实值。
        _create(self.store, run_id="20260617-100301-legacy")

        items, total = self.store.list_runs()
        self.assertEqual(total, 2)
        by_id = {item["id"]: item for item in items}

        new_item = by_id["20260617-100300-listed"]
        self.assertEqual(new_item["embedding_model"], "text-embedding-v3")
        self.assertEqual(new_item["rerank_model"], "qwen3-rerank")

        legacy_item = by_id["20260617-100301-legacy"]
        self.assertIsNone(legacy_item["embedding_model"])
        self.assertIsNone(legacy_item["rerank_model"])

    def test_build_detail_manifest_exposes_both_fields(self) -> None:
        _create(
            self.store,
            run_id="20260617-100400-detail",
            config={
                "embedding_model": "m3e",
                "rerank_model": "无",
            },
        )
        detail = self.store.build_detail("20260617-100400-detail")
        self.assertEqual(detail["embedding_model"], "m3e")
        self.assertEqual(detail["rerank_model"], "无")
        # config dict 也得带上，方便详情页展示。
        self.assertEqual(detail["config"]["embedding_model"], "m3e")
        self.assertEqual(detail["config"]["rerank_model"], "无")

    def test_list_runs_filters_by_dify_base_url(self) -> None:
        # RunCompare 的核心契约：传 dify_base_url 时只返回该 Dify 下的 run。
        _create(
            self.store,
            run_id="20260617-100500-A1",
            config={"dify_base_url": "http://dify-A/v1"},
        )
        _create(
            self.store,
            run_id="20260617-100501-A2",
            config={"dify_base_url": "http://dify-A/v1"},
        )
        _create(
            self.store,
            run_id="20260617-100502-B1",
            config={"dify_base_url": "http://dify-B/v1"},
        )

        only_a, total_a = self.store.list_runs(dify_base_url="http://dify-A/v1")
        self.assertEqual(total_a, 2)
        self.assertEqual(
            sorted(item["id"] for item in only_a),
            ["20260617-100500-A1", "20260617-100501-A2"],
        )
        for item in only_a:
            self.assertEqual(item["dify_base_url"], "http://dify-A/v1")

        # 不传 = 全部。
        all_items, total_all = self.store.list_runs()
        self.assertEqual(total_all, 3)

        # 空白视为"未传"。
        no_filter_items, _ = self.store.list_runs(dify_base_url="   ")
        self.assertEqual(len(no_filter_items), 3)

    def test_compare_runs_filters_by_dify_base_url(self) -> None:
        # compare_runs 也按 dify_base_url 隔离。
        _create(
            self.store,
            run_id="20260617-100600-A1",
            config={
                "dify_base_url": "http://dify-A/v1",
                "embedding_model": "emb-A",
                "rerank_model": "rank-A",
            },
        )
        _create(
            self.store,
            run_id="20260617-100601-A2",
            config={
                "dify_base_url": "http://dify-A/v1",
                "embedding_model": "emb-A",
                "rerank_model": "rank-A",
            },
        )
        _create(
            self.store,
            run_id="20260617-100602-B1",
            config={
                "dify_base_url": "http://dify-B/v1",
                "embedding_model": "emb-B",
                "rerank_model": "rank-B",
            },
        )

        only_a = self.store.compare_runs(
            dataset_id="ds-1",
            dify_base_url="http://dify-A/v1",
        )
        self.assertEqual(only_a["total_runs"], 2)
        # B 的 group 不会出现在 A 的对比里。
        embedding_models = {g["embedding_model"] for g in only_a["groups"]}
        self.assertIn("emb-A", embedding_models)
        self.assertNotIn("emb-B", embedding_models)

        everything = self.store.compare_runs(dataset_id="ds-1")
        self.assertEqual(everything["total_runs"], 3)

    def test_build_detail_manifest_exposes_dify_base_url(self) -> None:
        _create(
            self.store,
            run_id="20260617-100450-dify-detail",
            config={"dify_base_url": "http://dify.local/v1"},
        )

        detail = self.store.build_detail("20260617-100450-dify-detail")

        self.assertEqual(detail["config"]["dify_base_url"], "http://dify.local/v1")

    def test_run_list_item_pydantic_accepts_null_fields(self) -> None:
        # 旧 run 走 RunListItem 序列化时不能因 None 崩；列表接口要对
        # NULL 字段宽容，与后端 store 行为一致。
        item = RunListItem(
            id="r-1",
            name="r",
            status="completed",
            created_at="2026-06-17T10:00:00",
            eval_file="eval.jsonl",
            dataset_id="ds-1",
            top_k=5,
            embedding_model=None,
            rerank_model=None,
        )
        self.assertIsNone(item.embedding_model)
        self.assertIsNone(item.rerank_model)

    def test_run_detail_response_pydantic_accepts_null_fields(self) -> None:
        # 旧 run 走 RunDetailResponse 序列化时同样要宽容。
        detail = RunDetailResponse(
            id="r-1",
            name="r",
            status="completed",
            created_at="2026-06-17T10:00:00",
            eval_file="eval.jsonl",
            dataset_id="ds-1",
            top_k=5,
            progress={"total_queries": 0, "completed_queries": 0, "error_queries": 0, "current_sample_id": None},
            config={"embedding_model": "", "rerank_model": ""},
            embedding_model=None,
            rerank_model=None,
        )
        self.assertIsNone(detail.embedding_model)
        self.assertIsNone(detail.rerank_model)


if __name__ == "__main__":
    unittest.main()
