"""MarkItDown adapter for PDF to Markdown conversion.

可选 PDF 解析后端,与 MinerU 并存。优先使用 Python 包 ``markitdown``；
包不可用时回退到 ``markitdown`` CLI。两条路径都不通时抛 ``EvalError``。
"""

from __future__ import annotations

import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kb_eval.errors import EvalError


@dataclass(frozen=True)
class MarkItDownConversionResult:
    markdown_path: Path
    command: list[str]
    stdout: str
    stderr: str


def convert_pdf_with_markitdown(
    pdf_path: Path,
    output_root: Path,
    *,
    markitdown_command: str = "",
    timeout_seconds: int = 300,
) -> MarkItDownConversionResult:
    """将单个 PDF 转换为 Markdown,落盘到 ``output_root`` 下。

    - ``markitdown_command`` 为空:优先 Python 包,失败回退 CLI。
    - ``markitdown_command`` 非空:当作 CLI 模板,``{input}`` / ``{output}`` 可占位,
      缺占位时把 PDF 路径与输出文件追加在末尾。
    """
    if not pdf_path.exists() or not pdf_path.is_file():
        raise EvalError(f"MarkItDown input not found: {pdf_path}")
    output_root.mkdir(parents=True, exist_ok=True)

    target_path = output_root / f"{pdf_path.stem}.md"

    if markitdown_command.strip():
        return _run_cli_template(
            pdf_path,
            target_path,
            markitdown_command=markitdown_command,
            timeout_seconds=timeout_seconds,
        )

    # 路径 1:Python 包
    try:
        return _run_python_package(pdf_path, target_path, timeout_seconds=timeout_seconds)
    except EvalError as exc:
        package_error = exc
    # 路径 2:CLI 回退
    cli_path = shutil.which("markitdown") or shutil.which("markitdown.exe")
    if not cli_path:
        raise EvalError(
            "MarkItDown 不可用：未安装 markitdown Python 包，且系统找不到 markitdown 命令。"
            f"原始错误：{package_error}"
        ) from package_error
    try:
        return _run_cli(pdf_path, target_path, cli_path, timeout_seconds=timeout_seconds)
    except EvalError as exc:
        raise EvalError(
            f"MarkItDown CLI 也失败了：{exc}（包式错误：{package_error}）"
        ) from exc


# -------- Python package path --------


def _run_python_package(
    pdf_path: Path,
    target_path: Path,
    *,
    timeout_seconds: int,
) -> MarkItDownConversionResult:
    try:
        from markitdown import MarkItDown  # type: ignore[import-not-found]
    except Exception as exc:  # ImportError 或其他环境错误
        raise EvalError(f"markitdown Python 包不可用：{exc}") from exc

    try:
        converter = MarkItDown()
        result: Any = converter.convert(str(pdf_path))
    except Exception as exc:
        raise EvalError(f"MarkItDown(Python) 转换失败：{exc}") from exc

    text = _extract_text(result)
    if not text.strip():
        raise EvalError("MarkItDown(Python) 转换结果为空")
    try:
        target_path.write_text(text, encoding="utf-8")
    except OSError as exc:
        raise EvalError(f"MarkItDown(Python) 写入失败：{exc}") from exc
    return MarkItDownConversionResult(
        markdown_path=target_path,
        command=["markitdown", "(python)", str(pdf_path)],
        stdout=f"wrote {target_path} ({len(text)} chars)",
        stderr="",
    )


def _extract_text(result: Any) -> str:
    """MarkItDown 0.x 的 convert() 返回带 .text_content 的对象；老版本返回 str。"""
    text = getattr(result, "text_content", None)
    if isinstance(text, str):
        return text
    if isinstance(result, str):
        return result
    # 兜底：尝试常见字段
    for attr in ("markdown", "content", "text"):
        value = getattr(result, attr, None)
        if isinstance(value, str):
            return value
    raise EvalError(
        f"MarkItDown 返回了未知结构（{type(result).__name__}），无法提取 Markdown 文本"
    )


# -------- CLI path --------


def _run_cli(
    pdf_path: Path,
    target_path: Path,
    cli_path: str,
    *,
    timeout_seconds: int,
) -> MarkItDownConversionResult:
    command = [cli_path, str(pdf_path), "-o", str(target_path)]
    return _run_cli_command(command, timeout_seconds=timeout_seconds, target_path=target_path)


def _run_cli_template(
    pdf_path: Path,
    target_path: Path,
    *,
    markitdown_command: str,
    timeout_seconds: int,
) -> MarkItDownConversionResult:
    parts = shlex.split(markitdown_command, posix=False)
    rendered = [
        part.replace("{input}", str(pdf_path)).replace("{output}", str(target_path))
        for part in parts
    ]
    if any("{input}" in part or "{output}" in part for part in parts):
        return _run_cli_command(rendered, timeout_seconds=timeout_seconds, target_path=target_path)
    # 模板没有占位 → 追加输入与输出
    return _run_cli_command(
        rendered + [str(pdf_path), "-o", str(target_path)],
        timeout_seconds=timeout_seconds,
        target_path=target_path,
    )


def _run_cli_command(
    command: list[str],
    *,
    timeout_seconds: int,
    target_path: Path,
) -> MarkItDownConversionResult:
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
        )
    except FileNotFoundError as exc:
        raise EvalError(f"{command[0]}: command not found") from exc
    except subprocess.TimeoutExpired as exc:
        raise EvalError(f"{' '.join(command)}: timeout after {timeout_seconds}s") from exc
    except OSError as exc:
        raise EvalError(f"MarkItDown CLI 启动失败：{exc}") from exc

    if completed.returncode != 0 or not target_path.exists():
        # CLI 有时把 markdown 写到 stdout，需要二次落盘
        stdout = completed.stdout or ""
        if completed.returncode == 0 and stdout.strip():
            try:
                target_path.write_text(stdout, encoding="utf-8")
            except OSError as exc:
                raise EvalError(f"MarkItDown CLI 写入 stdout 失败：{exc}") from exc
        else:
            raise EvalError(
                "\n".join(
                    [
                        f"{' '.join(command)}: exit {completed.returncode}",
                        completed.stdout[-1000:],
                        completed.stderr[-1000:],
                    ],
                ).strip()
            )

    return MarkItDownConversionResult(
        markdown_path=target_path,
        command=command,
        stdout=completed.stdout[-4000:],
        stderr=completed.stderr[-4000:],
    )


def markitdown_available() -> bool:
    """快速判断 MarkItDown 是否可用（包或 CLI）。"""
    try:
        from markitdown import MarkItDown  # noqa: F401
        return True
    except Exception:
        pass
    return bool(shutil.which("markitdown") or shutil.which("markitdown.exe"))


__all__ = [
    "MarkItDownConversionResult",
    "convert_pdf_with_markitdown",
    "markitdown_available",
]
