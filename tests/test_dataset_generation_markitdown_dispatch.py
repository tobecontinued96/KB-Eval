from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from backend.schemas import GenerateDatasetRequest
from backend.services.dataset_generation_service import DatasetGenerationService


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


class MarkItDownDispatchTests(unittest.TestCase):
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

    def test_pdf_parser_markitdown_dispatches_to_markitdown(self) -> None:
        pdf_path = self.source_dir / "manual.pdf"
        pdf_path.write_bytes(b"%PDF-1.4\n%mock\n")
        result_path = self.markdown_dir / "manual.md"

        def fake_convert(pdf, output_dir, **kwargs):
            output_dir.mkdir(parents=True, exist_ok=True)
            result_path.write_text(markdown_body(), encoding="utf-8")
            return SimpleNamespace(
                markdown_path=result_path,
                command=["markitdown", "(python)", str(pdf)],
                stdout="ok",
                stderr="",
            )

        request = GenerateDatasetRequest(
            source_directory=str(self.source_dir),
            vendor="Cisco",
            model="C1200",
            output_name="cisco_c1200_generated.jsonl",
            max_samples=10,
            min_section_chars=20,
            use_mineru=False,
            reuse_existing_markdown=False,
            pdf_parser="markitdown",
            overwrite=True,
        )

        with patch("backend.services.dataset_generation_service.convert_pdf_with_markitdown", side_effect=fake_convert) as md_call:
            result = self.service.generate_dataset(request)

        self.assertEqual(md_call.call_count, 1)
        self.assertEqual(result["pdf_parser_used"], "markitdown")
        self.assertGreater(result["sample_count"], 0)
        self.assertEqual(len(result["mineru_conversions"]), 1)
        self.assertEqual(result["mineru_conversions"][0]["status"], "converted")
        self.assertIn("markitdown", result["mineru_conversions"][0]["command"])

    def test_legacy_pdf_parser_mineru_is_ignored(self) -> None:
        pdf_path = self.source_dir / "manual.pdf"
        pdf_path.write_bytes(b"%PDF-1.4\n%mock\n")
        result_path = self.markdown_dir / "manual.md"

        def fake_markitdown(pdf, output_dir, **kwargs):
            output_dir.mkdir(parents=True, exist_ok=True)
            result_path.write_text(markdown_body(), encoding="utf-8")
            return SimpleNamespace(
                markdown_path=result_path,
                command=["markitdown", str(pdf)],
                stdout="ok",
                stderr="",
            )

        request = GenerateDatasetRequest(
            source_directory=str(self.source_dir),
            vendor="Cisco",
            model="C1200",
            output_name="cisco_c1200_generated.jsonl",
            max_samples=10,
            min_section_chars=20,
            use_mineru=True,
            reuse_existing_markdown=False,
            pdf_parser="mineru",
            mineru_provider="api",
            overwrite=True,
        )

        with patch("backend.services.dataset_generation_service.convert_pdf_with_markitdown", side_effect=fake_markitdown) as md_call:
            result = self.service.generate_dataset(request)

        self.assertEqual(md_call.call_count, 1)
        self.assertEqual(result["pdf_parser_used"], "markitdown")
        self.assertEqual(result["mineru_conversions"][0]["status"], "converted")
        self.assertIn("markitdown", result["mineru_conversions"][0]["command"])


if __name__ == "__main__":
    unittest.main()
