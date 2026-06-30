"""Human-review workflow for the on-disk JSONL evaluation datasets.

状态约定：
- ``unreviewed``: 文件没有 draft 也没有 .review.json，状态由 generator 写入"草稿"
  之后才进入 draft 流程；老样本则按历史数据保持 "unreviewed"。
- ``draft``: 同目录下存在 ``<stem>.draft.jsonl`` 草稿文件，样本待人工审核。
- ``reviewed``: 不存在 draft，但存在 ``<stem>.review.json`` 元信息，
  内含 ``reviewed_at`` / ``reviewed_by``。
- 状态切换：generator 写样本到 draft（同时初始化 review meta 为 draft）；
  审核人提交时，commit_review 把 draft 内容写回原 jsonl、删除 draft、
  写 review.json。
"""
from __future__ import annotations

import datetime as dt
import io
import json
import shutil
from pathlib import Path
from typing import Any

from kb_eval.dataset import load_samples
from kb_eval.errors import EvalError


class DatasetEditError(RuntimeError):
    """审核流程中遇到的行级错误。本地定义以避免与 ``dataset_edit_service`` 循环导入。"""

    def __init__(self, code: str, message: str, detail: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail or {}


def _validate_rows(rows: list[dict[str, Any]]) -> None:
    """把 rows 临时写到一个内存 buffer，再用 ``load_samples`` 走一遍真实校验。"""

    buf = io.StringIO()
    for row in rows:
        buf.write(json.dumps(row, ensure_ascii=False))
        buf.write("\n")

    class _BufferedJsonl:
        def __init__(self, content: str) -> None:
            self._content = content

        def open(self, *args, **kwargs):  # pragma: no cover - load_samples 不调
            raise NotImplementedError

        def exists(self) -> bool:
            return True

        def __fspath__(self) -> str:
            return "<draft-buffer>"

    class _Path:
        def __init__(self, content: str) -> None:
            self._content = content

        def exists(self) -> bool:
            return True

        def open(self, *args, **kwargs):
            from io import StringIO
            return StringIO(self._content)

        @property
        def name(self) -> str:
            return "draft.jsonl"

        @property
        def stem(self) -> str:
            return "draft"

        @property
        def suffix(self) -> str:
            return ".jsonl"

    try:
        load_samples(_Path(buf.getvalue()))  # type: ignore[arg-type]
    except EvalError as exc:
        raise DatasetEditError(
            "DATASET_INVALID_ROWS",
            str(exc),
        ) from exc


def _serialise_rows(rows: list[dict[str, Any]]) -> str:
    return "\n".join(json.dumps(row, ensure_ascii=False, sort_keys=False) for row in rows) + (
        "\n" if rows else ""
    )


def _now_iso() -> str:
    return dt.datetime.now(tz=dt.timezone.utc).astimezone().isoformat(timespec="seconds")


def _file_iso(path: Path) -> str | None:
    try:
        epoch = path.stat().st_mtime
    except OSError:
        return None
    return (
        dt.datetime.fromtimestamp(epoch, tz=dt.timezone.utc)
        .astimezone()
        .isoformat(timespec="seconds")
    )


def draft_path_for(path: Path) -> Path:
    """返回给定 jsonl 对应的草稿路径：``<stem>.draft.jsonl``。"""

    return path.with_name(f"{path.stem}.draft.jsonl")


def review_meta_path_for(path: Path) -> Path:
    """返回给定 jsonl 对应的 review 元信息路径：``<stem>.review.json``。"""

    return path.with_name(f"{path.stem}.review.json")


def read_review_state(path: Path) -> dict[str, Any]:
    """读取数据集的审核状态。

    返回字段：
    - ``status``: ``unreviewed`` / ``draft`` / ``reviewed``
    - ``draft_path``: 若存在草稿，给出相对路径
    - ``reviewed_at`` / ``reviewed_by``: 仅在 ``reviewed`` 时存在
    - ``generated_at``: 仅在 ``draft`` 时存在
    """

    draft = draft_path_for(path)
    meta_file = review_meta_path_for(path)
    if draft.exists():
        return {
            "status": "draft",
            "draft_path": str(draft.name),
            "generated_at": _file_iso(draft),
        }
    if meta_file.exists():
        try:
            payload = json.loads(meta_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = {}
        status = str(payload.get("status") or "reviewed")
        if status not in ("reviewed", "draft"):
            status = "reviewed"
        return {
            "status": status,
            "draft_path": None,
            "reviewed_at": payload.get("reviewed_at"),
            "reviewed_by": payload.get("reviewed_by") or None,
            "generated_at": payload.get("generated_at"),
        }
    return {"status": "unreviewed", "draft_path": None}


def write_draft(path: Path, rows: list[dict[str, Any]]) -> Path:
    """把样本写入 ``<stem>.draft.jsonl`` 并初始化 review meta 为 draft。

    旧 ``<stem>.jsonl`` 不动；旧 draft 若存在会覆盖。
    """

    _validate_rows(rows)

    draft = draft_path_for(path)
    draft.parent.mkdir(parents=True, exist_ok=True)
    tmp = draft.with_suffix(draft.suffix + ".tmp")
    tmp.write_text(_serialise_rows(rows), encoding="utf-8")
    tmp.replace(draft)

    # 在 .review.json 里写一条"草稿生成时间"的元信息，状态为 draft。
    meta_file = review_meta_path_for(path)
    meta_file.write_text(
        json.dumps(
            {
                "status": "draft",
                "generated_at": _now_iso(),
                "source_path": str(path.name),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return draft


def commit_review(
    path: Path,
    rows: list[dict[str, Any]],
    *,
    reviewed_by: str = "",
) -> dict[str, Any]:
    """把当前 rows 写回原 jsonl、删除草稿、落 review 元信息为 reviewed。

    rows 应为审核后的最终样本；操作之前会再次校验。
    """

    _validate_rows(rows)

    # 备份原 jsonl（如果存在）
    backup_path = ""
    if path.exists():
        backup_target = path.with_suffix(path.suffix + ".bak")
        counter = 1
        while backup_target.exists():
            counter += 1
            backup_target = path.with_suffix(f"{path.suffix}.bak{counter}")
        shutil.copy2(path, backup_target)
        backup_path = str(backup_target)

    # 写样本到原 jsonl
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(_serialise_rows(rows), encoding="utf-8")
    tmp.replace(path)

    # 删除 draft、写出 review.json
    draft = draft_path_for(path)
    draft_meta = review_meta_path_for(path)
    generated_at = None
    if draft.exists():
        generated_at = _file_iso(draft)
        try:
            draft.unlink()
        except OSError:
            pass

    payload = {
        "status": "reviewed",
        "reviewed_at": _now_iso(),
        "reviewed_by": (reviewed_by or "").strip() or None,
        "generated_at": generated_at,
        "backup_path": backup_path,
    }
    draft_meta.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return {
        "path": str(path),
        "sample_count": len(rows),
        "backup_path": backup_path,
        "reviewed_at": payload["reviewed_at"],
        "reviewed_by": payload["reviewed_by"],
    }


def discard_draft(path: Path) -> bool:
    """删除 draft 文件与 draft 元信息，但不写回原 jsonl。返回是否真的删了。"""

    removed = False
    draft = draft_path_for(path)
    if draft.exists():
        try:
            draft.unlink()
            removed = True
        except OSError:
            pass

    # 如果没有 reviewed 元信息，把 review.json 也清掉，
    # 防止 list_datasets 看到一份"半截"状态文件。
    meta_file = review_meta_path_for(path)
    if meta_file.exists() and not path.exists():
        try:
            meta_file.unlink()
        except OSError:
            pass
    return removed
