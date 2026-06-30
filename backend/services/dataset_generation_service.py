"""Service layer for generating JSONL evaluation datasets."""

from __future__ import annotations

import logging
import os
import re
import shutil
from pathlib import Path
from typing import Any

from backend.schemas import GenerateDatasetRequest
from backend.services.dataset_edit_service import DatasetEditError
from backend.services.dataset_review_service import (
    draft_path_for,
    review_meta_path_for,
    write_draft,
)
from backend.services.run_service import RunServiceError
from kb_eval.dataset_generator import (
    generate_samples_from_markdown,
    read_markdown,
)
from kb_eval.errors import EvalError
from kb_eval.markitdown_converter import convert_pdf_with_markitdown


_log = logging.getLogger("backend.dataset_generation")


class DatasetGenerationService:
    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root
        self.workspace_root = project_root.parent
        self.datasets_dir = project_root / "datasets"
        self.generated_datasets_dir = self.datasets_dir / "generated"
        self.allowed_source_roots = [
            self.project_root.resolve(),
            self.workspace_root.resolve(),
            (self.workspace_root / ".uploads").resolve(),
            (self.workspace_root / "docs").resolve(),
        ]

    def generate_dataset(self, request: GenerateDatasetRequest) -> dict[str, Any]:
        source_dir = self.resolve_source_directory(request.source_directory) if request.source_directory.strip() else None
        vendor, model = self.resolve_vendor_model(request, source_dir)
        source_paths = self.collect_source_paths(request, source_dir)
        if not source_paths:
            raise RunServiceError("NO_SOURCE_FILES", "请至少提供一个 PDF 或 Markdown 源文件")

        _log.info(
            "dataset generation starting vendor=%s model=%s source_count=%d parser=%s",
            vendor,
            model,
            len(source_paths),
            "markitdown",
        )
        markdown_output_dir = self.markdown_output_dir(source_dir, vendor, model)
        markdown_output_dir.mkdir(parents=True, exist_ok=True)

        markdown_sources = []
        conversions: list[dict[str, Any]] = []
        for source_path in source_paths:
            suffix = source_path.suffix.lower()
            if suffix == ".pdf":
                existing_markdown = markdown_output_dir / f"{source_path.stem}.md"
                if request.reuse_existing_markdown and existing_markdown.exists():
                    markdown_sources.append(read_markdown(existing_markdown, document_name=source_path.name))
                    continue
                try:
                    conversion_info = self.convert_pdf(source_path, markdown_output_dir, request)
                except EvalError as exc:
                    _log.warning(
                        "pdf conversion skipped source=%s parser=%s error=%s",
                        self.display_path(source_path),
                        "markitdown",
                        exc,
                    )
                    conversions.append(
                        {
                            "source_file": self.display_path(source_path),
                            "markdown_file": "",
                            "command": "",
                            "stderr_tail": str(exc)[-1000:],
                            "status": "skipped",
                            "message": str(exc),
                        },
                    )
                    continue
                saved_markdown = self.normalize_markdown_location(conversion_info["markdown_path"], existing_markdown)
                conversions.append(
                    {
                        "source_file": self.display_path(source_path),
                        "markdown_file": self.display_path(saved_markdown),
                        "command": conversion_info["command"],
                        "stderr_tail": conversion_info.get("stderr_tail", ""),
                        "status": "converted",
                        "message": "",
                    },
                )
                markdown_sources.append(read_markdown(saved_markdown, document_name=source_path.name))
            elif suffix in {".md", ".markdown"}:
                document_name = request.document_name or source_path.with_suffix(".pdf").name
                markdown_sources.append(read_markdown(source_path, document_name=document_name))
            else:
                raise RunServiceError(
                    "UNSUPPORTED_SOURCE_FILE",
                    "源文件仅支持 PDF、MD、Markdown",
                    {"source_file": str(source_path)},
                )

        if not markdown_sources:
            raise RunServiceError(
                "NO_AVAILABLE_MARKDOWN",
                "没有在等待上限内解析出可用 Markdown",
                {"mineru_conversions": conversions, "pdf_parser": "markitdown"},
            )

        samples = generate_samples_from_markdown(
            markdown_sources,
            vendor=vendor,
            model=model,
            max_samples=request.max_samples,
            min_section_chars=request.min_section_chars,
        )
        if not samples:
            raise RunServiceError(
                "NO_GENERATED_SAMPLES",
                "没有从源文件中生成可用样本，请检查 Markdown 标题层级或降低最小章节长度",
            )

        output_path = self.resolve_output_file(request, vendor, model)
        # 生成器只写草稿 + 初始化 review meta；正式 jsonl 由 commit_review 落盘。
        try:
            write_draft(output_path, samples)
        except (EvalError, DatasetEditError) as exc:
            raise RunServiceError(
                "GENERATED_DATASET_INVALID",
                str(exc),
                {"output_file": self.display_path(output_path)},
            ) from exc

        knowledge_base_name = self.knowledge_base_name(vendor, model)
        draft_file = draft_path_for(output_path)
        review_meta_file = review_meta_path_for(output_path)
        _log.info(
            "dataset generation complete output=%s draft=%s sample_count=%d",
            self.display_path(output_path),
            self.display_path(draft_file),
            len(samples),
        )
        return {
            "dataset": {
                "path": self.display_path(output_path),
                "name": f"{knowledge_base_name} 自动生成评测集",
                "sample_count": len(samples),
                "vendor": vendor,
                "model": model,
                "knowledge_base_name": knowledge_base_name,
            },
            "output_file": self.display_path(output_path),
            "draft_path": self.display_path(draft_file),
            "review_meta_path": self.display_path(review_meta_file),
            "review_status": "draft",
            "knowledge_base_name": knowledge_base_name,
            "sample_count": len(samples),
            "source_directory": self.display_path(source_dir) if source_dir else "",
            "markdown_output_dir": self.display_path(markdown_output_dir),
            "preview_samples": samples[: min(5, len(samples))],
            "source_files": [self.display_path(path) for path in source_paths],
            "markdown_files": [self.display_path(source.path) for source in markdown_sources],
            "mineru_conversions": conversions,
            "pdf_parser_used": "markitdown",
        }

    def resolve_vendor_model(self, request: GenerateDatasetRequest, source_dir: Path | None) -> tuple[str, str]:
        vendor = request.vendor.strip()
        model = request.model.strip()
        if vendor and model:
            return vendor, model
        if source_dir:
            inferred_vendor, inferred_model = self.infer_vendor_model_from_path(source_dir)
            vendor = vendor or inferred_vendor
            model = model or inferred_model
        if not vendor or not model:
            raise RunServiceError(
                "VENDOR_MODEL_REQUIRED",
                "无法从源文档目录解析厂商/型号，请使用类似 华为/S1720 的目录结构",
                {"source_directory": request.source_directory},
            )
        return vendor, model

    def infer_vendor_model_from_path(self, path: Path) -> tuple[str, str]:
        resolved = path.resolve()
        return resolved.parent.name.strip(), resolved.name.strip()

    def infer_vendor_model_from_relative_paths(self, values: list[str]) -> tuple[str, str]:
        candidates: set[tuple[str, str]] = set()
        models: set[str] = set()
        for value in values:
            parts = [part for part in re.split(r"[\\/]+", value.strip()) if part and part not in {".", ".."}]
            if not parts:
                continue
            directories = parts[:-1]
            if directories and directories[-1].lower() == "md":
                directories = directories[:-1]
            if not directories:
                continue
            models.add(directories[-1])
            if len(directories) >= 2:
                candidates.add((directories[-2], directories[-1]))

        if len(candidates) == 1:
            return next(iter(candidates))
        if len(candidates) > 1 or len(models) > 1:
            raise RunServiceError(
                "MULTIPLE_SOURCE_DIRECTORIES",
                "所选目录包含多个厂商或型号，请一次只选择一个知识库源目录",
                {"relative_paths": values[:20]},
            )
        return "", next(iter(models), "")

    def uploaded_source_directory(self, vendor: str, model: str) -> Path:
        source_dir = (self.project_root / "generated_sources" / safe_name(vendor) / safe_name(model)).resolve()
        generated_root = (self.project_root / "generated_sources").resolve()
        if generated_root not in source_dir.parents:
            raise RunServiceError("INVALID_SOURCE_PATH", "上传目录不在 generated_sources 目录内")
        source_dir.mkdir(parents=True, exist_ok=True)
        return source_dir

    def uploaded_file_path(self, source_dir: Path, relative_path: str, filename: str) -> Path:
        suffix = Path(filename).suffix.lower()
        if suffix not in {".pdf", ".md", ".markdown"}:
            raise RunServiceError(
                "UNSUPPORTED_SOURCE_FILE",
                "源文件仅支持 PDF、MD、Markdown",
                {"source_file": filename},
            )
        parts = [part for part in re.split(r"[\\/]+", relative_path.strip()) if part and part not in {".", ".."}]
        in_markdown_dir = len(parts) >= 2 and parts[-2].lower() == "md"
        target_dir = source_dir / "md" if suffix in {".md", ".markdown"} or in_markdown_dir else source_dir
        target_dir.mkdir(parents=True, exist_ok=True)
        return target_dir / Path(filename).name

    def collect_source_paths(self, request: GenerateDatasetRequest, source_dir: Path | None) -> list[Path]:
        source_paths: list[Path] = []
        if source_dir:
            pdf_paths = sorted(source_dir.glob("*.pdf"))
            source_paths.extend(pdf_paths)
            md_dir = source_dir / "md"
            if md_dir.exists() and not pdf_paths:
                source_paths.extend(sorted(md_dir.glob("*.md")))
                source_paths.extend(sorted(md_dir.glob("*.markdown")))
            if not pdf_paths and not source_paths:
                source_paths.extend(sorted(source_dir.glob("*.md")))
                source_paths.extend(sorted(source_dir.glob("*.markdown")))

        source_paths.extend(self.resolve_source_file(value) for value in request.source_files if value.strip())

        seen: set[Path] = set()
        unique_paths: list[Path] = []
        for path in source_paths:
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            unique_paths.append(path)
        return unique_paths

    def convert_pdf(self, source_path: Path, markdown_output_dir: Path, request: GenerateDatasetRequest) -> dict[str, Any]:
        """Convert PDF sources with MarkItDown only."""
        return self._convert_pdf_with_markitdown(source_path, markdown_output_dir, request)

    def _convert_pdf_with_markitdown(
        self, source_path: Path, markdown_output_dir: Path, request: GenerateDatasetRequest
    ) -> dict[str, Any]:
        command = request.markitdown_command.strip() or os.getenv("MARKITDOWN_COMMAND", "").strip()
        conversion = convert_pdf_with_markitdown(
            source_path,
            markdown_output_dir,
            markitdown_command=command,
            timeout_seconds=request.markitdown_timeout_seconds,
        )
        return {
            "markdown_path": conversion.markdown_path,
            "command": " ".join(conversion.command),
            "stderr_tail": conversion.stderr[-1000:],
        }

    def resolve_source_directory(self, value: str) -> Path:
        raw = Path(value.strip())
        candidates: list[Path] = []
        if raw.is_absolute():
            candidates.append(raw)
        else:
            candidates.extend(
                [
                    self.workspace_root / raw,
                    self.project_root / raw,
                    self.workspace_root / "docs" / raw,
                    self.workspace_root / ".uploads" / raw,
                ],
            )
        for candidate in candidates:
            path = candidate.resolve()
            if not path.exists() or not path.is_dir():
                continue
            if not any(root == path or root in path.parents for root in self.allowed_source_roots):
                raise RunServiceError("INVALID_SOURCE_PATH", "源文档目录不在允许目录内", {"source_directory": value})
            return path
        raise RunServiceError("SOURCE_DIRECTORY_NOT_FOUND", "源文档目录不存在", {"source_directory": value})

    def resolve_source_file(self, value: str) -> Path:
        raw = Path(value.strip())
        candidates: list[Path] = []
        if raw.is_absolute():
            candidates.append(raw)
        else:
            candidates.extend(
                [
                    self.workspace_root / raw,
                    self.project_root / raw,
                    self.workspace_root / "docs" / raw.name,
                    self.workspace_root / ".uploads" / raw.name,
                ],
            )

        for candidate in candidates:
            path = candidate.resolve()
            if not path.exists() or not path.is_file():
                continue
            if not any(root == path or root in path.parents for root in self.allowed_source_roots):
                raise RunServiceError("INVALID_SOURCE_PATH", "源文件路径不在允许目录内", {"source_file": value})
            return path
        raise RunServiceError("SOURCE_FILE_NOT_FOUND", "源文件不存在", {"source_file": value})

    def markdown_output_dir(self, source_dir: Path | None, vendor: str, model: str) -> Path:
        if source_dir:
            return source_dir / "md"
        return self.project_root / "generated_sources" / safe_name(vendor) / safe_name(model) / "md"

    def normalize_markdown_location(self, actual_path: Path, target_path: Path) -> Path:
        actual = actual_path.resolve()
        target = target_path.resolve()
        if actual == target:
            return target
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(actual, target)
        return target

    def resolve_output_file(self, request: GenerateDatasetRequest, vendor: str | None = None, model: str | None = None) -> Path:
        output_name = request.output_name.strip()
        if not output_name:
            output_name = f"{safe_name(vendor or request.vendor)}_{safe_name(model or request.model)}_generated.jsonl"
        cleaned = output_name.replace("\\", "/").split("/")[-1]
        if not cleaned.endswith(".jsonl"):
            cleaned = f"{cleaned}.jsonl"
        stem = re.sub(r"[^a-zA-Z0-9_\-\u4e00-\u9fff.]+", "_", cleaned).strip("._")
        if not stem:
            stem = "generated_eval_dataset.jsonl"
        output_path = (self.generated_datasets_dir / stem).resolve()
        if self.generated_datasets_dir.resolve() not in output_path.parents:
            raise RunServiceError("INVALID_OUTPUT_PATH", "输出文件路径不在 datasets/generated 目录内")
        if output_path.exists() and not request.overwrite:
            raise RunServiceError(
                "DATASET_ALREADY_EXISTS",
                "评测集已存在，如需覆盖请启用覆盖选项",
                {"output_file": self.display_path(output_path)},
            )
        return output_path

    def knowledge_base_name(self, vendor: str, model: str) -> str:
        return " ".join(item for item in [vendor.strip(), model.strip()] if item)

    def display_path(self, path: Path | None) -> str:
        if path is None:
            return ""
        resolved = path.resolve()
        for base in [self.project_root.resolve(), self.workspace_root.resolve()]:
            try:
                return str(resolved.relative_to(base)).replace("\\", "/")
            except ValueError:
                continue
        return str(resolved)


def safe_name(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_\-\u4e00-\u9fff]+", "_", value.strip()).strip("_")
    return cleaned or "kb"
