from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.schemas import GenerateDatasetRequest
from backend.services.dataset_generation_service import DatasetGenerationService
from kb_eval.errors import EvalError


def markdown_body() -> str:
    return "\n".join(
        [
            "# VLAN configuration",
            "",
            "display vlan shows the VLAN table and current VLAN membership.",
            "interface GigabitEthernet0/0/1 can be assigned to an access VLAN.",
            "port link-type access configures the access mode for the port.",
            "port default vlan 10 places the interface into VLAN 10.",
            "save persists the configuration after verification.",
        ],
    )


class DatasetGenerationPartialMinerUTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.project_root = Path(self.temp_dir.name) / "Dify-KB-Eval"
        self.source_dir = self.project_root / "generated_sources" / "Cisco" / "C1200"
        self.markdown_dir = self.source_dir / "md"
        self.markdown_dir.mkdir(parents=True)
        (self.project_root / "datasets" / "generated").mkdir(parents=True)
        self.service = DatasetGenerationService(self.project_root)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_skips_timed_out_pdf_and_generates_from_available_markdown(self) -> None:
        timed_out_pdf = self.source_dir / "timed-out.pdf"
        completed_pdf = self.source_dir / "completed.pdf"
        timed_out_pdf.write_bytes(b"%PDF timed out")
        completed_pdf.write_bytes(b"%PDF completed")
        (self.markdown_dir / "completed.md").write_text(markdown_body(), encoding="utf-8")

        request = GenerateDatasetRequest(
            source_directory=str(self.source_dir),
            vendor="Cisco",
            model="C1200",
            output_name="cisco_c1200_generated.jsonl",
            max_samples=10,
            min_section_chars=20,
            use_mineru=True,
            reuse_existing_markdown=True,
            mineru_provider="api",
            mineru_api_token="token",
            mineru_timeout_seconds=30,
            overwrite=True,
        )

        with patch.object(
            self.service,
            "convert_pdf",
            side_effect=EvalError("MinerU API extraction timed out after 30s. Last progress: running"),
        ):
            result = self.service.generate_dataset(request)

        self.assertGreater(result["sample_count"], 0)
        self.assertEqual(result["markdown_files"], ["generated_sources/Cisco/C1200/md/completed.md"])
        self.assertEqual(len(result["mineru_conversions"]), 1)
        skipped = result["mineru_conversions"][0]
        self.assertEqual(skipped["source_file"], "generated_sources/Cisco/C1200/timed-out.pdf")
        self.assertEqual(skipped["status"], "skipped")
        self.assertIn("timed out", skipped["message"])


if __name__ == "__main__":
    unittest.main()
