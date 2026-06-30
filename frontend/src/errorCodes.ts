/**
 * Unified error code catalogue for the frontend.
 *
 * Mirrors ``backend/error_codes.py`` — keep the two in sync.
 *
 * Provides:
 *   - ``ErrorCode`` literal type derived from the ``as const`` object.
 *   - ``describeError(code, status?, fallbackMessage?)`` — resolves title
 *     by code first, then by status, falling back to ``fallbackMessage``.
 *   - ``errorPrefix(action)`` — returns the existing "拉取失败：" style
 *     prefix so every catch site uses the same wording.
 */

// ---------------------------------------------------------------------------
// Error code constants (must stay in sync with ``backend/error_codes.py``)
// ---------------------------------------------------------------------------

export const ErrorCode = {
  // Request-shape validation
  VALIDATION_ERROR: "VALIDATION_ERROR",

  // Run lifecycle
  RUN_NOT_FOUND: "RUN_NOT_FOUND",
  REPORT_NOT_FOUND: "REPORT_NOT_FOUND",
  ARTIFACT_NOT_FOUND: "ARTIFACT_NOT_FOUND",
  RUN_DELETE_FAILED: "RUN_DELETE_FAILED",
  RUN_NAME_REQUIRED: "RUN_NAME_REQUIRED",
  INVALID_RUN_CONFIG: "INVALID_RUN_CONFIG",

  // Dify upstream
  DIFY_URL_REQUIRED: "DIFY_URL_REQUIRED",
  DIFY_API_KEY_REQUIRED: "DIFY_API_KEY_REQUIRED",
  DIFY_LIST_FAILED: "DIFY_LIST_FAILED",
  DIFY_CONNECTION_CONFIG_NOT_FOUND: "DIFY_CONNECTION_CONFIG_NOT_FOUND",

  // Dataset / file IO
  DATASET_REVIEW_REQUIRED: "DATASET_REVIEW_REQUIRED",
  DATASET_ID_REQUIRED: "DATASET_ID_REQUIRED",
  DATASET_NOT_FOUND: "DATASET_NOT_FOUND",
  DATASET_INVALID_ROWS: "DATASET_INVALID_ROWS",
  DATASET_PATH_FORBIDDEN: "DATASET_PATH_FORBIDDEN",
  DATASET_ALREADY_EXISTS: "DATASET_ALREADY_EXISTS",
  INVALID_EVAL_FILE: "INVALID_EVAL_FILE",
  EVAL_FILE_NOT_FOUND: "EVAL_FILE_NOT_FOUND",

  // Source-file / generation
  NO_SOURCE_FILES: "NO_SOURCE_FILES",
  PDF_REQUIRES_MINERU: "PDF_REQUIRES_MINERU",
  UNSUPPORTED_SOURCE_FILE: "UNSUPPORTED_SOURCE_FILE",
  NO_AVAILABLE_MARKDOWN: "NO_AVAILABLE_MARKDOWN",
  NO_GENERATED_SAMPLES: "NO_GENERATED_SAMPLES",
  GENERATED_DATASET_INVALID: "GENERATED_DATASET_INVALID",
  VENDOR_MODEL_REQUIRED: "VENDOR_MODEL_REQUIRED",
  MULTIPLE_SOURCE_DIRECTORIES: "MULTIPLE_SOURCE_DIRECTORIES",
  INVALID_SOURCE_PATH: "INVALID_SOURCE_PATH",
  SOURCE_DIRECTORY_NOT_FOUND: "SOURCE_DIRECTORY_NOT_FOUND",
  SOURCE_FILE_NOT_FOUND: "SOURCE_FILE_NOT_FOUND",
  INVALID_OUTPUT_PATH: "INVALID_OUTPUT_PATH",
} as const;

export type ErrorCode = (typeof ErrorCode)[keyof typeof ErrorCode];

// ---------------------------------------------------------------------------
// Title table — Chinese user-facing text per code
// ---------------------------------------------------------------------------

const TITLES: Record<ErrorCode, string> = {
  [ErrorCode.VALIDATION_ERROR]: "请求参数校验失败",
  [ErrorCode.RUN_NOT_FOUND]: "评测运行不存在或已删除",
  [ErrorCode.REPORT_NOT_FOUND]: "运行报告不存在",
  [ErrorCode.ARTIFACT_NOT_FOUND]: "运行产物不存在",
  [ErrorCode.RUN_DELETE_FAILED]: "运行删除失败",
  [ErrorCode.RUN_NAME_REQUIRED]: "运行名称不能为空",
  [ErrorCode.INVALID_RUN_CONFIG]: "运行配置不合法",
  [ErrorCode.DIFY_URL_REQUIRED]: "缺少 Dify 接口地址",
  [ErrorCode.DIFY_API_KEY_REQUIRED]: "缺少 Dify API Key",
  [ErrorCode.DIFY_LIST_FAILED]: "无法从 Dify 拉取知识库列表，请检查API 地址和 Key 是否正确",
  [ErrorCode.DIFY_CONNECTION_CONFIG_NOT_FOUND]: "历史 Dify 连接配置不存在或已被删除",
  [ErrorCode.DATASET_REVIEW_REQUIRED]: "请先完成人工审核再运行评测",
  [ErrorCode.DATASET_ID_REQUIRED]: "缺少数据集 ID",
  [ErrorCode.DATASET_NOT_FOUND]: "数据集不存在",
  [ErrorCode.DATASET_INVALID_ROWS]: "数据集行格式不合法",
  [ErrorCode.DATASET_PATH_FORBIDDEN]: "数据集路径不在允许范围内",
  [ErrorCode.DATASET_ALREADY_EXISTS]: "数据集已存在",
  [ErrorCode.INVALID_EVAL_FILE]: "评测文件名称不合法",
  [ErrorCode.EVAL_FILE_NOT_FOUND]: "评测文件不存在",
  [ErrorCode.NO_SOURCE_FILES]: "未找到可用的源文件",
  [ErrorCode.PDF_REQUIRES_MINERU]: "PDF 文件需要先配置 MarkItDown",
  [ErrorCode.UNSUPPORTED_SOURCE_FILE]: "存在不支持的源文件类型",
  [ErrorCode.NO_AVAILABLE_MARKDOWN]: "未解析到可用的 Markdown 内容",
  [ErrorCode.NO_GENERATED_SAMPLES]: "未生成任何样本",
  [ErrorCode.GENERATED_DATASET_INVALID]: "生成的数据集结构不合法",
  [ErrorCode.VENDOR_MODEL_REQUIRED]: "无法从所选目录解析厂商和型号",
  [ErrorCode.MULTIPLE_SOURCE_DIRECTORIES]: "源目录不唯一",
  [ErrorCode.INVALID_SOURCE_PATH]: "源路径不合法",
  [ErrorCode.SOURCE_DIRECTORY_NOT_FOUND]: "源目录不存在",
  [ErrorCode.SOURCE_FILE_NOT_FOUND]: "源文件不存在",
  [ErrorCode.INVALID_OUTPUT_PATH]: "输出路径不合法",
};

// ---------------------------------------------------------------------------
// Public helpers
// ---------------------------------------------------------------------------

export interface ErrorView {
  /** Short human-readable title */
  title: string;
  /** Resolved code (``"unknown"`` when the backend didn't include one). */
  code: ErrorCode | "unknown";
  /** Optional HTTP status echoed for debugging. */
  status?: number;
}

export type ErrorAction =
  | "fetch"
  | "load"
  | "save"
  | "validate"
  | "delete"
  | "run"
  | "sync"
  | "other";

/**
 * Resolve a user-facing error title.
 *
 * Priority:
 *   1. ``code`` lookup in ``TITLES``
 *   2. ``status``-based default:
 *      - 401/403 → "没有访问权限"
 *      - 404     → "资源不存在"
 *      - 409     → "资源状态冲突，请刷新后重试"
 *      - 422     → "请求参数校验失败"
 *      - 429     → "请求过于频繁，请稍后再试"
 *      - >=500   → "服务异常，请稍后再试"
 *   3. ``fallbackMessage`` or "操作失败"
 */
export function describeError(
  code: string | undefined,
  status?: number,
  fallbackMessage?: string,
): ErrorView {
  if (code && (code as ErrorCode) in TITLES) {
    return {
      title: TITLES[code as ErrorCode],
      code: code as ErrorCode,
      status,
    };
  }
  if (status === 401 || status === 403) {
    return { title: "没有访问权限", code: "unknown", status };
  }
  if (status === 404) {
    return { title: "资源不存在", code: "unknown", status };
  }
  if (status === 409) {
    return { title: "资源状态冲突，请刷新后重试", code: "unknown", status };
  }
  if (status === 422) {
    return { title: "请求参数校验失败", code: "unknown", status };
  }
  if (status === 429) {
    return { title: "请求过于频繁，请稍后再试", code: "unknown", status };
  }
  if (status !== undefined && status >= 500) {
    return { title: "服务异常，请稍后再试", code: "unknown", status };
  }
  return {
    title: fallbackMessage || "操作失败",
    code: "unknown",
    status,
  };
}

/** Action-specific Chinese prefix for error banners. */
export function errorPrefix(action: ErrorAction = "other"): string {
  switch (action) {
    case "fetch":
      return "拉取失败：";
    case "load":
      return "加载失败：";
    case "save":
      return "保存失败：";
    case "validate":
      return "校验失败：";
    case "delete":
      return "删除失败：";
    case "run":
      return "运行失败：";
    case "sync":
      return "同步失败：";
    default:
      return "";
  }
}
