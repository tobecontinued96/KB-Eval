"""Unified error code catalogue for the backend HTTP layer.

Single source of truth for:
  * ``ErrorCode`` enum — every code the backend can return to clients.
  * Default user-facing ``message`` per code (Chinese).
  * HTTP ``status_code`` per code, with an optional override.

Routes (``backend/app.py``) use ``http_status_for(exc.code, override=exc.http_status)``
to pick the wire status instead of writing ``404 if exc.code == "RUN_NOT_FOUND"`` everywhere.

Frontend mirrors this catalogue in ``frontend/src/errorCodes.ts`` — keep the two in sync.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final


class ErrorCode(StrEnum):
    """All backend error codes. Values must stay equal to the JSON wire format."""

    # Request-shape validation
    VALIDATION_ERROR = "VALIDATION_ERROR"

    # Run lifecycle
    RUN_NOT_FOUND = "RUN_NOT_FOUND"
    REPORT_NOT_FOUND = "REPORT_NOT_FOUND"
    ARTIFACT_NOT_FOUND = "ARTIFACT_NOT_FOUND"
    RUN_DELETE_FAILED = "RUN_DELETE_FAILED"
    RUN_NAME_REQUIRED = "RUN_NAME_REQUIRED"
    INVALID_RUN_CONFIG = "INVALID_RUN_CONFIG"

    # Dify upstream
    DIFY_URL_REQUIRED = "DIFY_URL_REQUIRED"
    DIFY_API_KEY_REQUIRED = "DIFY_API_KEY_REQUIRED"
    DIFY_LIST_FAILED = "DIFY_LIST_FAILED"
    DIFY_CONNECTION_CONFIG_NOT_FOUND = "DIFY_CONNECTION_CONFIG_NOT_FOUND"

    # Dataset / file IO
    DATASET_REVIEW_REQUIRED = "DATASET_REVIEW_REQUIRED"
    DATASET_ID_REQUIRED = "DATASET_ID_REQUIRED"
    DATASET_NOT_FOUND = "DATASET_NOT_FOUND"
    DATASET_INVALID_ROWS = "DATASET_INVALID_ROWS"
    DATASET_PATH_FORBIDDEN = "DATASET_PATH_FORBIDDEN"
    DATASET_ALREADY_EXISTS = "DATASET_ALREADY_EXISTS"
    INVALID_EVAL_FILE = "INVALID_EVAL_FILE"
    EVAL_FILE_NOT_FOUND = "EVAL_FILE_NOT_FOUND"

    # Source-file / generation
    NO_SOURCE_FILES = "NO_SOURCE_FILES"
    PDF_REQUIRES_MINERU = "PDF_REQUIRES_MINERU"
    UNSUPPORTED_SOURCE_FILE = "UNSUPPORTED_SOURCE_FILE"
    NO_AVAILABLE_MARKDOWN = "NO_AVAILABLE_MARKDOWN"
    NO_GENERATED_SAMPLES = "NO_GENERATED_SAMPLES"
    GENERATED_DATASET_INVALID = "GENERATED_DATASET_INVALID"
    VENDOR_MODEL_REQUIRED = "VENDOR_MODEL_REQUIRED"
    MULTIPLE_SOURCE_DIRECTORIES = "MULTIPLE_SOURCE_DIRECTORIES"
    INVALID_SOURCE_PATH = "INVALID_SOURCE_PATH"
    SOURCE_DIRECTORY_NOT_FOUND = "SOURCE_DIRECTORY_NOT_FOUND"
    SOURCE_FILE_NOT_FOUND = "SOURCE_FILE_NOT_FOUND"
    INVALID_OUTPUT_PATH = "INVALID_OUTPUT_PATH"


# Default user-facing Chinese message per code. The frontend mirrors this in
# ``frontend/src/errorCodes.ts`` — keep both files in sync.
_DEFAULT_MESSAGES: Final[dict[ErrorCode, str]] = {
    ErrorCode.VALIDATION_ERROR: "请求参数校验失败",
    ErrorCode.RUN_NOT_FOUND: "评测运行不存在或已删除",
    ErrorCode.REPORT_NOT_FOUND: "运行报告不存在",
    ErrorCode.ARTIFACT_NOT_FOUND: "运行产物不存在",
    ErrorCode.RUN_DELETE_FAILED: "运行删除失败",
    ErrorCode.RUN_NAME_REQUIRED: "运行名称不能为空",
    ErrorCode.INVALID_RUN_CONFIG: "运行配置不合法",
    ErrorCode.DIFY_URL_REQUIRED: "缺少 Dify 接口地址",
    ErrorCode.DIFY_API_KEY_REQUIRED: "缺少 Dify API Key",
    ErrorCode.DIFY_LIST_FAILED: "无法从 Dify 拉取知识库列表",
    ErrorCode.DIFY_CONNECTION_CONFIG_NOT_FOUND: "历史 Dify 连接配置不存在或已被删除",
    ErrorCode.DATASET_REVIEW_REQUIRED: "请先完成人工审核再运行评测",
    ErrorCode.DATASET_ID_REQUIRED: "缺少数据集 ID",
    ErrorCode.DATASET_NOT_FOUND: "数据集不存在",
    ErrorCode.DATASET_INVALID_ROWS: "数据集行格式不合法",
    ErrorCode.DATASET_PATH_FORBIDDEN: "数据集路径不在允许范围内",
    ErrorCode.DATASET_ALREADY_EXISTS: "数据集已存在",
    ErrorCode.INVALID_EVAL_FILE: "评测文件名称不合法",
    ErrorCode.EVAL_FILE_NOT_FOUND: "评测文件不存在",
    ErrorCode.NO_SOURCE_FILES: "未找到可用的源文件",
    ErrorCode.PDF_REQUIRES_MINERU: "PDF 文件需要先配置 MinerU",
    ErrorCode.UNSUPPORTED_SOURCE_FILE: "存在不支持的源文件类型",
    ErrorCode.NO_AVAILABLE_MARKDOWN: "未解析到可用的 Markdown 内容",
    ErrorCode.NO_GENERATED_SAMPLES: "未生成任何样本",
    ErrorCode.GENERATED_DATASET_INVALID: "生成的数据集结构不合法",
    ErrorCode.VENDOR_MODEL_REQUIRED: "无法从所选目录解析厂商和型号",
    ErrorCode.MULTIPLE_SOURCE_DIRECTORIES: "源目录不唯一",
    ErrorCode.INVALID_SOURCE_PATH: "源路径不合法",
    ErrorCode.SOURCE_DIRECTORY_NOT_FOUND: "源目录不存在",
    ErrorCode.SOURCE_FILE_NOT_FOUND: "源文件不存在",
    ErrorCode.INVALID_OUTPUT_PATH: "输出路径不合法",
}


# HTTP status per code. Anything not listed falls back to ``DEFAULT_HTTP_STATUS``
# (typically 400). Callers can override with ``http_status_for(code, override=...)``.
_HTTP_STATUS: Final[dict[ErrorCode, int]] = {
    ErrorCode.VALIDATION_ERROR: 422,
    ErrorCode.DATASET_INVALID_ROWS: 422,
    ErrorCode.RUN_NOT_FOUND: 404,
    ErrorCode.REPORT_NOT_FOUND: 404,
    ErrorCode.ARTIFACT_NOT_FOUND: 404,
    ErrorCode.DATASET_NOT_FOUND: 404,
    ErrorCode.EVAL_FILE_NOT_FOUND: 404,
    ErrorCode.DIFY_CONNECTION_CONFIG_NOT_FOUND: 404,
    # The /api/knowledge-bases route always forces 502 on upstream failure to
    # signal "Dify is unreachable", so DIFY_LIST_FAILED has no entry here and
    # falls through to the route's default.
}


DEFAULT_HTTP_STATUS: Final[int] = 400


def default_message(code: ErrorCode | str) -> str:
    """Return the canonical Chinese message for ``code``, or a generic fallback."""
    try:
        ec = ErrorCode(code)
    except ValueError:
        return "操作失败"
    return _DEFAULT_MESSAGES.get(ec, "操作失败")


def http_status_for(code: str | ErrorCode, *, override: int | None = None) -> int:
    """Resolve the wire HTTP status for ``code``.

    ``override`` wins when provided — used for codes that need different
    statuses per call site (e.g. DIFY_LIST_FAILED → 502 only on the
    /api/knowledge-bases route).
    """
    if override is not None:
        return override
    try:
        ec = ErrorCode(code)
    except ValueError:
        return DEFAULT_HTTP_STATUS
    return _HTTP_STATUS.get(ec, DEFAULT_HTTP_STATUS)