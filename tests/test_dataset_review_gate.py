from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Test mode is needed because RunService.create_run writes the run row to PG.
# Force-override (not setdefault) so a real DATABASE_URL inherited from the
# user's shell does not bleed into the test run.
os.environ["DIFY_KB_EVAL_TEST_MODE"] = "1"
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

from backend.schemas import CreateRunRequest  # noqa: E402
from backend.services.dataset_edit_service import resolve_editable_path  # noqa: E402
from backend.services.dataset_review_service import write_draft  # noqa: E402
from backend.services.run_service import RunService, RunServiceError  # noqa: E402
from tests._db_fixture import make_db_store  # noqa: E402


def valid_row(sample_id: str = "sample-1") -> dict[str, object]:
    return {
        "id": sample_id,
        "vendor": "Cisco",
        "model": "Catalyst 1200",
        "scenario_type": "config",
        "topic": "VLAN",
        "difficulty": "basic",
        "question": "How do I view VLAN configuration?",
        "alternative_queries": ["Which command shows VLAN configuration?"],
        "expected_documents": ["Catalyst 1200 User Guide.pdf"],
        "expected_sections": ["VLAN configuration"],
        "expected_keywords": ["VLAN", "show"],
        "evaluation_focus": "The result should retrieve the VLAN configuration section.",
    }


class DatasetReviewPathTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.project_root = Path(self._tmp.name) / "Dify-KB-Eval"
        self.generated_dir = self.project_root / "datasets" / "generated"
        self.generated_dir.mkdir(parents=True)
        (self.project_root / "reports").mkdir(parents=True)
        self.output_path = self.generated_dir / "cisco_c1200_generated.jsonl"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_resolve_editable_path_accepts_canonical_path_when_only_draft_exists(self) -> None:
        write_draft(self.output_path, [valid_row()])

        resolved = resolve_editable_path(
            "datasets/generated/cisco_c1200_generated.jsonl",
            [self.project_root / "datasets"],
        )

        self.assertEqual(resolved, self.output_path.resolve())

    def test_list_datasets_reports_draft_under_canonical_main_path(self) -> None:
        write_draft(self.output_path, [valid_row()])
        # list_datasets doesn't touch the store; pass None.
        service = RunService(self.project_root, None)

        items = service.list_datasets()

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["id"], "cisco_c1200_generated")
        self.assertEqual(items[0]["path"], "datasets/generated/cisco_c1200_generated.jsonl")
        self.assertEqual(items[0]["draft_path"], "datasets/generated/cisco_c1200_generated.draft.jsonl")
        self.assertEqual(items[0]["review_status"], "draft")
        self.assertEqual(items[0]["sample_count"], 1)


class DatasetReviewRunGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.project_root = Path(self._tmp.name) / "Dify-KB-Eval"
        self.generated_dir = self.project_root / "datasets" / "generated"
        self.generated_dir.mkdir(parents=True)
        self.reports_root = self.project_root / "reports"
        self.reports_root.mkdir(parents=True)
        self.output_path = self.generated_dir / "cisco_c1200_generated.jsonl"
        store = make_db_store(self.reports_root)
        self.service = RunService(self.project_root, store)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def request(self) -> CreateRunRequest:
        return CreateRunRequest(
            name="review gate test",
            dify_base_url="http://localhost/v1",
            dify_api_key="kb-secret",
            dataset_id="kb-1",
            eval_file="datasets/generated/cisco_c1200_generated.jsonl",
            top_k=5,
            include_alternatives=False,
            limit=0,
            sample_ids=[],
            timeout_seconds=60,
            langsmith_enabled=False,
            langsmith_project="dify-kb-eval",
        )

    def write_main_dataset(self) -> None:
        self.output_path.write_text(json.dumps(valid_row(), ensure_ascii=False) + "\n", encoding="utf-8")

    def mark_reviewed(self) -> None:
        review_meta = self.output_path.with_name("cisco_c1200_generated.review.json")
        review_meta.write_text(
            json.dumps(
                {
                    "status": "reviewed",
                    "reviewed_at": "2026-06-12T15:00:00+08:00",
                    "reviewed_by": "tester",
                    "generated_at": "2026-06-12T14:30:00+08:00",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def test_create_run_rejects_draft_dataset(self) -> None:
        write_draft(self.output_path, [valid_row()])

        with self.assertRaises(RunServiceError) as ctx:
            self.service.create_run(self.request())

        self.assertEqual(ctx.exception.code, "DATASET_REVIEW_REQUIRED")
        self.assertEqual(ctx.exception.detail["review_status"], "draft")
        self.assertEqual(
            ctx.exception.detail["draft_path"],
            "datasets/generated/cisco_c1200_generated.draft.jsonl",
        )

    def test_create_run_rejects_unreviewed_dataset(self) -> None:
        self.write_main_dataset()

        with self.assertRaises(RunServiceError) as ctx:
            self.service.create_run(self.request())

        self.assertEqual(ctx.exception.code, "DATASET_REVIEW_REQUIRED")
        self.assertEqual(ctx.exception.detail["review_status"], "unreviewed")

    def test_create_run_allows_reviewed_dataset(self) -> None:
        self.write_main_dataset()
        self.mark_reviewed()

        manifest, config = self.service.create_run(self.request())

        self.assertEqual(manifest["status"], "queued")
        self.assertEqual(config.eval_file, self.output_path.resolve())
        self.assertEqual(config.dataset_id, "kb-1")

    def test_create_run_rejects_blank_dataset_id(self) -> None:
        self.write_main_dataset()
        self.mark_reviewed()

        request = self.request().model_copy(update={"dataset_id": "   "})

        with self.assertRaises(RunServiceError) as ctx:
            self.service.create_run(request)

        self.assertEqual(ctx.exception.code, "DATASET_ID_REQUIRED")
        self.assertEqual(ctx.exception.detail["field"], "dataset_id")

    def test_create_dify_run_builds_dify_config(self) -> None:
        self.write_main_dataset()
        self.mark_reviewed()
        request = self.request().model_copy(
            update={
                "dify_base_url": "http://localhost/v1",
                "dify_api_key": "kb-secret",
                "dataset_id": "kb-1",
            }
        )

        manifest, config = self.service.create_run(request)

        self.assertEqual(manifest["status"], "queued")
        self.assertEqual(manifest["dify_base_url"], "http://localhost/v1")
        self.assertEqual(config.dify_api_key, "kb-secret")


if __name__ == "__main__":
    unittest.main()
