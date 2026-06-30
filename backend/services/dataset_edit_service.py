"""Edit-in-place support for the on-disk JSONL evaluation datasets.

The frontend's dataset editor needs three things from the backend:

* Read every row of a JSONL file (including extra fields like ``metadata``).
* Validate a candidate row set against the dataset specification rules
  (re-using the same checks the runner performs).
* Persist a row set back to the original file with a defensive backup.

The path resolution deliberately re-uses the same allow-list as
``RunService.resolve_eval_file`` so the editor cannot be tricked into
touching files outside the recognised dataset directories.
"""

from __future__ import annotations

import datetime as dt
import json
import shutil
from pathlib import Path
from typing import Any, Iterable

from kb_eval.dataset import dataset_metadata, load_samples
from kb_eval.errors import EvalError

from backend.services.dataset_review_service import draft_path_for, review_meta_path_for
from backend.services.run_service import RunServiceError


REQUIRED_FIELDS = (
    "id",
    "vendor",
    "model",
    "scenario_type",
    "topic",
    "question",
    "expected_documents",
    "expected_sections",
    "expected_keywords",
    "evaluation_focus",
)


class DatasetEditError(RunServiceError):
    """Raised when the dataset editor cannot complete an operation."""


def _now_iso() -> str:
    return dt.datetime.now(tz=dt.timezone.utc).astimezone().isoformat(timespec="seconds")


def resolve_editable_path(eval_file: str, allowed_roots: list[Path]) -> Path:
    """Resolve a dataset path while honouring the runner's allow-list rules."""

    raw = Path(eval_file)
    candidates: list[Path] = []
    if raw.is_absolute():
        candidates.append(raw)
    else:
        # Avoid the path doubling that happens if we naively join a relative
        # ``datasets/...`` to the already-resolved ``datasets/`` root.
        for root in allowed_roots:
            if raw.parts and root.name == raw.parts[0]:
                candidates.append(root.joinpath(*raw.parts[1:]))
            candidates.append(root / raw)
            candidates.append(root / raw.name)

    for candidate in candidates:
        try:
            path = candidate.resolve(strict=True)
        except (FileNotFoundError, OSError):
            path = candidate.resolve(strict=False)
            draft = draft_path_for(path)
            if path.suffix.lower() == ".jsonl" and draft.exists():
                if not any(root == path or root in path.parents for root in allowed_roots):
                    raise DatasetEditError(
                        "DATASET_PATH_FORBIDDEN",
                        "评测集路径不在允许目录内",
                        {"eval_file": eval_file},
                    )
                return path
            continue
        if path.suffix.lower() != ".jsonl":
            raise DatasetEditError(
                "DATASET_PATH_FORBIDDEN",
                "评测集必须是 JSONL 文件",
                {"eval_file": eval_file},
            )
        if not any(root == path or root in path.parents for root in allowed_roots):
            raise DatasetEditError(
                "DATASET_PATH_FORBIDDEN",
                "评测集路径不在允许目录内",
                {"eval_file": eval_file},
            )
        return path

    raise DatasetEditError(
        "DATASET_NOT_FOUND",
        "评测集文件不存在",
        {"eval_file": eval_file},
    )


def load_dataset_rows(path: Path) -> dict[str, Any]:
    """Read the JSONL dataset and return the rows + summary metadata."""

    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                row = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise DatasetEditError(
                    "DATASET_INVALID_ROWS",
                    f"第 {line_no} 行不是合法 JSON：{exc.msg}",
                    {"line": line_no},
                ) from exc
            if not isinstance(row, dict):
                raise DatasetEditError(
                    "DATASET_INVALID_ROWS",
                    f"第 {line_no} 行必须是 JSON object",
                    {"line": line_no},
                )
            rows.append(row)

    try:
        meta = dataset_metadata(path)
    except EvalError as exc:
        raise DatasetEditError("DATASET_INVALID_ROWS", str(exc), {"path": str(path)}) from exc

    updated_at: str | None = None
    epoch = meta.get("updated_at_epoch")
    if isinstance(epoch, (int, float)):
        updated_at = (
            dt.datetime.fromtimestamp(float(epoch), tz=dt.timezone.utc)
            .astimezone()
            .isoformat(timespec="seconds")
        )

    return {
        "path": str(path),
        "name": path.stem,
        "vendor": meta.get("vendor", ""),
        "model": meta.get("model", ""),
        "version": "v0.1",
        "sample_count": meta.get("sample_count", len(rows)),
        "updated_at": updated_at,
        "scenario_types": meta.get("scenario_types", []),
        "rows": rows,
    }


def _coerce_str_list(value: Any) -> list[str] | None:
    if value is None or value == "":
        return []
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return [item for item in (chunk.strip() for chunk in value) if item]
    if isinstance(value, str):
        # Accept comma / newline separated input from the editor.
        chunks = [chunk.strip() for chunk in value.replace("\n", ",").split(",")]
        return [chunk for chunk in chunks if chunk]
    return None


def _row_value(row: dict[str, Any], field: str) -> Any:
    value = row.get(field)
    if value is None and field == "alternative_queries":
        return row.get(field)  # missing allowed
    return value


def validate_rows(rows: Iterable[dict[str, Any]]) -> list[dict[str, str]]:
    """Validate a candidate row set using the runner's required-field rules."""

    errors: list[dict[str, str]] = []
    seen_ids: dict[str, int] = {}

    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            errors.append({"row_index": str(index), "field": "", "message": "样本必须是 JSON object"})
            continue
        sample_id = str(row.get("id") or f"row-{index + 1}")
        for field in REQUIRED_FIELDS:
            if field in ("expected_documents", "expected_sections", "expected_keywords"):
                if not _row_value(row, field):
                    errors.append(
                        {
                            "row_index": str(index),
                            "sample_id": sample_id,
                            "field": field,
                            "message": f"{field} 至少包含 1 个非空字符串",
                        }
                    )
                    continue
                coerced = _coerce_str_list(row.get(field))
                if coerced is None:
                    errors.append(
                        {
                            "row_index": str(index),
                            "sample_id": sample_id,
                            "field": field,
                            "message": f"{field} 必须是字符串数组",
                        }
                    )
                continue
            value = _row_value(row, field)
            if value is None or (isinstance(value, str) and not value.strip()):
                errors.append(
                    {
                        "row_index": str(index),
                        "sample_id": sample_id,
                        "field": field,
                        "message": f"{field} 不能为空",
                    }
                )
        alt_value = row.get("alternative_queries")
        if alt_value not in (None, ""):
            if not isinstance(alt_value, list) or any(not isinstance(item, str) for item in alt_value):
                errors.append(
                    {
                        "row_index": str(index),
                        "sample_id": sample_id,
                        "field": "alternative_queries",
                        "message": "alternative_queries 必须是字符串数组",
                    }
                )
        current = seen_ids.get(sample_id)
        if current is not None:
            errors.append(
                {
                    "row_index": str(index),
                    "sample_id": sample_id,
                    "field": "id",
                    "message": f"id 重复（与第 {current + 1} 行）",
                }
            )
        else:
            seen_ids[sample_id] = index

    return errors


def normalise_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return a copy of rows with list fields normalised to string lists.

    Empty / whitespace-only strings are stripped from list fields. The on-disk
    representation is always a strict subset of the fields the editor cares
    about — extra keys (``metadata`` etc.) are preserved verbatim.
    """

    list_fields = ("expected_documents", "expected_sections", "expected_keywords", "alternative_queries")
    normalised: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        clone = dict(row)
        for field in list_fields:
            coerced = _coerce_str_list(clone.get(field))
            clone[field] = coerced if coerced is not None else clone.get(field)
        normalised.append(clone)
    return normalised


def serialise_rows(rows: list[dict[str, Any]]) -> str:
    """Render the row set as a UTF-8 JSONL document."""

    lines = [json.dumps(row, ensure_ascii=False, sort_keys=False) for row in rows]
    return "\n".join(lines) + ("\n" if lines else "")


def save_dataset_rows(
    path: Path,
    rows: list[dict[str, Any]],
    *,
    backup: bool = True,
) -> dict[str, Any]:
    """Validate, back up, and persist the row set to ``path``."""

    errors = validate_rows(rows)
    if errors:
        return {
            "errors": errors,
            "backup_path": "",
            "saved_at": "",
        }

    normalised = normalise_rows(rows)
    content = serialise_rows(normalised)

    backup_path = ""
    if backup and path.exists():
        backup_target = path.with_suffix(path.suffix + ".bak")
        counter = 1
        while backup_target.exists():
            counter += 1
            backup_target = path.with_suffix(f"{path.suffix}.bak{counter}")
        shutil.copy2(path, backup_target)
        backup_path = str(backup_target)

    # Atomic-ish write: write to a sibling temp file then replace.
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as fh:
        fh.write(content)
    tmp_path.replace(path)

    # Re-load via the shared loader to surface any remaining issues (e.g. id
    # collision across helper files). We only use this for sanity.
    try:
        load_samples(path)
    except EvalError as exc:
        # If the file we just wrote is invalid (should not happen because we
        # validated above), roll back the backup and re-raise.
        if backup_path:
            shutil.copy2(backup_path, path)
        raise DatasetEditError("DATASET_INVALID_ROWS", str(exc), {"path": str(path)}) from exc

    return {
        "errors": [],
        "backup_path": backup_path,
        "saved_at": _now_iso(),
    }


def export_dataset(path: Path) -> str:
    """Return the on-disk content for an export / download request."""

    return path.read_text(encoding="utf-8")


def delete_dataset(path: Path) -> dict[str, Any]:
    """Delete a dataset (main + draft + review meta) with a one-shot backup.

    The caller is expected to have already validated the path against the
    allow-list via :func:`resolve_editable_path`. We additionally require the
    main JSONL to exist on disk so the operation is idempotent: deleting an
    already-gone dataset returns ``DATASET_NOT_FOUND`` instead of silently
    succeeding.

    Backup naming uses a fixed ``.deleted-<UTC timestamp>`` suffix rather than
    the rotating ``.bak`` series used by save/commit so a deleted dataset
    cannot be silently overwritten by a fresh save.
    """

    if not path.exists():
        raise DatasetEditError(
            "DATASET_NOT_FOUND",
            "评测集文件不存在",
            {"path": str(path)},
        )
    if path.suffix.lower() != ".jsonl":
        raise DatasetEditError(
            "DATASET_PATH_FORBIDDEN",
            "评测集必须是 JSONL 文件",
            {"path": str(path)},
        )

    backup_target = path.with_name(
        f"{path.name}.deleted-{_now_iso().replace(':', '').replace('+', '-')}.bak"
    )
    counter = 1
    while backup_target.exists():
        backup_target = path.with_name(f"{path.name}.deleted-{counter}.bak")
        counter += 1
    shutil.copy2(path, backup_target)

    draft = draft_path_for(path)
    review_meta = review_meta_path_for(path)
    removed: list[str] = []

    for target in (path, draft, review_meta):
        if target.exists():
            try:
                target.unlink()
                removed.append(str(target))
            except OSError:
                # Best effort: 仍记录已删除的文件，不阻断其它文件清理
                pass

    return {
        "path": str(path),
        "backup_path": str(backup_target),
        "removed": removed,
    }
