from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from kb_eval.errors import EvalError
from kb_eval.markitdown_converter import (
    convert_pdf_with_markitdown,
    markitdown_available,
)


def write_pdf(path: Path) -> None:
    path.write_bytes(b"%PDF-1.4\n%mock\n")


def write_md(target: Path, content: str) -> None:
    target.write_text(content, encoding="utf-8")


class MarkItDownPackagePathTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.pdf_path = Path(self.temp_dir.name) / "manual.pdf"
        write_pdf(self.pdf_path)
        self.output_dir = Path(self.temp_dir.name) / "out"

    def test_writes_markdown_via_python_package(self) -> None:
        target = self.output_dir / "manual.md"
        fake_module = SimpleNamespace(
            MarkItDown=lambda: SimpleNamespace(
                convert=lambda _pdf: SimpleNamespace(text_content="# Title\n\nhello")
            )
        )

        with patch.dict("sys.modules", {"markitdown": fake_module}):
            result = convert_pdf_with_markitdown(
                self.pdf_path, self.output_dir, markitdown_command="", timeout_seconds=30
            )

        self.assertEqual(result.markdown_path, target)
        self.assertEqual(target.read_text(encoding="utf-8"), "# Title\n\nhello")
        self.assertIn("markitdown", " ".join(result.command))

    def test_falls_back_to_cli_when_package_missing(self) -> None:
        # markitdown 包不存在；用占位 {input}/{output} 验证回退路径
        # 真实 subprocess 会执行命令，但模板里不会有 Windows 路径里的 \U 转义问题：
        # 命令要求 input/output 占位，没有占位时会自动追加 -- 占位替换在内部完成。
        captured: dict[str, object] = {}

        def fake_run(command, **_):
            captured["command"] = command
            # 模拟"成功"的 CLI：自己写一份 markdown
            Path(command[command.index("-o") + 1]).write_text("# from cli\n", encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, "", "")

        with patch.dict("sys.modules", {"markitdown": None}), patch("shutil.which", return_value="markitdown.exe"), patch("subprocess.run", side_effect=fake_run):
            cli_template = "markitdown {input} -o {output}"
            convert_pdf_with_markitdown(
                self.pdf_path,
                self.output_dir,
                markitdown_command=cli_template,
                timeout_seconds=30,
            )

        self.assertIn("command", captured)
        command = captured["command"]  # type: ignore[assignment]
        self.assertNotIn("{input}", command)
        self.assertNotIn("{output}", command)
        self.assertEqual(Path(self.output_dir / "manual.md").read_text(encoding="utf-8"), "# from cli\n")

    def test_raises_when_both_package_and_cli_missing(self) -> None:
        with patch.dict("sys.modules", {"markitdown": None}):
            with patch("shutil.which", return_value=None):
                with self.assertRaises(EvalError) as ctx:
                    convert_pdf_with_markitdown(
                        self.pdf_path, self.output_dir, markitdown_command="", timeout_seconds=30
                    )
        self.assertIn("MarkItDown", str(ctx.exception))

    def test_input_pdf_must_exist(self) -> None:
        with self.assertRaises(EvalError):
            convert_pdf_with_markitdown(
                Path(self.temp_dir.name) / "missing.pdf", self.output_dir, timeout_seconds=30
            )


class MarkItDownCliFailureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.pdf_path = Path(self.temp_dir.name) / "manual.pdf"
        write_pdf(self.pdf_path)
        self.output_dir = Path(self.temp_dir.name) / "out"
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def test_cli_exits_nonzero_surfaces_error(self) -> None:
        def fake_run(command, **_):
            return subprocess.CompletedProcess(command, 2, "", "boom")

        with patch.dict("sys.modules", {"markitdown": None}), patch("shutil.which", return_value="markitdown.exe"), patch("subprocess.run", side_effect=fake_run):
            with self.assertRaises(EvalError) as ctx:
                convert_pdf_with_markitdown(
                    self.pdf_path, self.output_dir, markitdown_command="", timeout_seconds=30
                )
        self.assertIn("MarkItDown", str(ctx.exception))

    def test_cli_stdout_is_persisted_when_returncode_zero(self) -> None:
        def fake_run(command, **_):
            # 模拟某些 CLI 不写文件只输出到 stdout 的情况
            return subprocess.CompletedProcess(command, 0, "# from stdout\n", "")

        with patch.dict("sys.modules", {"markitdown": None}), patch("shutil.which", return_value="markitdown.exe"), patch("subprocess.run", side_effect=fake_run):
            convert_pdf_with_markitdown(
                self.pdf_path, self.output_dir, markitdown_command="", timeout_seconds=30
            )
        target = self.output_dir / "manual.md"
        self.assertEqual(target.read_text(encoding="utf-8"), "# from stdout\n")


class MarkItDownAvailabilityTests(unittest.TestCase):
    def test_available_when_package_importable(self) -> None:
        fake_module = SimpleNamespace(MarkItDown=lambda: None)
        with patch.dict("sys.modules", {"markitdown": fake_module}):
            self.assertTrue(markitdown_available())

    def test_available_when_cli_on_path(self) -> None:
        with patch.dict("sys.modules", {"markitdown": None}):
            with patch("shutil.which", return_value="markitdown"):
                self.assertTrue(markitdown_available())

    def test_unavailable_when_neither(self) -> None:
        with patch.dict("sys.modules", {"markitdown": None}):
            with patch("shutil.which", return_value=None):
                self.assertFalse(markitdown_available())


if __name__ == "__main__":
    unittest.main()
