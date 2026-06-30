"""Regression tests for reading Markdown reports from the run store."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.services.run_service import RunService, RunServiceError  # noqa: E402
from tests._db_fixture import make_db_store  # noqa: E402


def _create(store, *, run_id: str) -> None:
    store.create_run(
        run_id=run_id,
        name="report-regression",
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


class RunReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.reports_root = Path(self._tmp.name) / "reports"
        self.store = make_db_store(self.reports_root)
        self.service = RunService(project_root=ROOT, store=self.store)
        self.run_id = "20260625-164021-report-regression"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_service_reads_report_from_db_store_after_disk_report_removed(self) -> None:
        _create(self.store, run_id=self.run_id)
        run_dir = self.reports_root / self.run_id
        (run_dir / "summary.json").write_text('{"overall": {}}', encoding="utf-8")
        (run_dir / "report.md").write_text("# Report\n\nok\n", encoding="utf-8")

        self.store.persist_run_artifacts(self.run_id)

        self.assertFalse((run_dir / "report.md").exists())
        self.assertEqual(self.service.get_report(self.run_id), "# Report\n\nok\n")

    def test_service_maps_missing_report_to_domain_error(self) -> None:
        with self.assertRaises(RunServiceError) as ctx:
            self.service.get_report("not-a-real-run")

        self.assertEqual(ctx.exception.code, "REPORT_NOT_FOUND")


if __name__ == "__main__":
    unittest.main()
