import { mockApi } from "./mockData";
import type {
  CreateRunPayload,
  CreateRunResponse,
  DatasetCommitReviewResponse,
  DatasetExportResponse,
  DatasetInfo,
  DatasetRow,
  DatasetRowsResponse,
  DatasetSaveResponse,
  DeleteDatasetResponse,
  DeleteRunResponse,
  DifyConnectionConfigItem,
  DifyConnectionConfigListResponse,
  EvalRunDetail,
  EvalRunListItem,
  GenerateDatasetPayload,
  GenerateDatasetResponse,
  HealthStatus,
  KnowledgeBaseListResponse
} from "./types";

const useMock = import.meta.env.VITE_USE_MOCK === "true";

export interface RequestError extends Error {
  status?: number;
  code?: string;
  detail?: Record<string, unknown>;
  validation_errors?: unknown[];
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  if (!(init?.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const response = await fetch(path, {
    ...init,
    headers
  });
  if (!response.ok) {
    let message = `请求失败：${response.status}`;
    let code = "";
    let detail: Record<string, unknown> | undefined;
    let validationErrors: unknown[] | undefined;
    try {
      const error = await response.json();
      if (error?.message) message = error.message;
      if (error?.code) code = String(error.code);
      if (error?.detail && typeof error.detail === "object" && !Array.isArray(error.detail)) {
        detail = error.detail as Record<string, unknown>;
      }
      if (Array.isArray(error?.detail?.validation_errors)) {
        validationErrors = error.detail.validation_errors;
      } else if (Array.isArray(error?.validation_errors)) {
        validationErrors = error.validation_errors;
      }
    } catch {
      // Ignore non-json errors from dev proxies.
    }
    const err = new Error(message) as RequestError;
    err.status = response.status;
    if (code) err.code = code;
    if (detail) err.detail = detail;
    if (validationErrors) err.validation_errors = validationErrors;
    throw err;
  }
  return response.json() as Promise<T>;
}

export async function listDatasets(): Promise<{ items: DatasetInfo[] }> {
  if (useMock) return mockApi.listDatasets();
  return request("/api/datasets");
}

export async function listRuns(params: {
  difyBaseUrl?: string;
} = {}): Promise<{ items: EvalRunListItem[]; total: number }> {
  if (useMock) return mockApi.listRuns(params);
  const query = new URLSearchParams();
  query.set("limit", "50");
  query.set("offset", "0");
  if (params.difyBaseUrl && params.difyBaseUrl.trim()) {
    query.set("dify_base_url", params.difyBaseUrl.trim());
  }
  return request(`/api/runs?${query.toString()}`);
}

export async function listKnowledgeBases(params: {
  dify_base_url: string;
  dify_api_key?: string;
  keyword?: string;
  limit?: number;
  offset?: number;
  signal?: AbortSignal;
}): Promise<KnowledgeBaseListResponse> {
  if (useMock) return mockApi.listKnowledgeBases(params);
  const query = new URLSearchParams();
  query.set("dify_base_url", params.dify_base_url);
  if (params.dify_api_key) query.set("dify_api_key", params.dify_api_key);
  if (params.keyword) query.set("keyword", params.keyword);
  if (params.limit) query.set("limit", String(params.limit));
  if (params.offset) query.set("offset", String(params.offset));
  return request(`/api/knowledge-bases?${query.toString()}`, { signal: params.signal });
}

export async function createRun(payload: CreateRunPayload): Promise<CreateRunResponse> {
  if (useMock) return mockApi.createRun(payload);
  return request("/api/runs", {
    method: "POST",
    body: JSON.stringify(payload)
  });
}

export async function listDifyConnectionConfigs(limit = 20): Promise<DifyConnectionConfigListResponse> {
  if (useMock) return mockApi.listDifyConnectionConfigs(limit);
  const query = new URLSearchParams();
  query.set("limit", String(limit));
  return request(`/api/dify-connections?${query.toString()}`);
}

export async function saveDifyConnectionConfig(payload: {
  dify_base_url: string;
  dify_api_key: string;
}): Promise<DifyConnectionConfigItem> {
  if (useMock) return mockApi.saveDifyConnectionConfig(payload);
  return request("/api/dify-connections", {
    method: "POST",
    body: JSON.stringify(payload)
  });
}

export async function deleteDifyConnectionConfig(
  configId: string
): Promise<DifyConnectionConfigItem | null> {
  if (useMock) return mockApi.deleteDifyConnectionConfig(configId);
  // 走 ErrorCode.NOT_FOUND → 404；mock 同样返回 null 表示"被前端列表过滤掉"。
  await request(`/api/dify-connections/${encodeURIComponent(configId)}`, {
    method: "DELETE"
  });
  return null;
}

export async function generateDataset(payload: GenerateDatasetPayload): Promise<GenerateDatasetResponse> {
  if (useMock) return mockApi.generateDataset(payload);
  return request("/api/datasets/generate", {
    method: "POST",
    body: JSON.stringify(payload)
  });
}

export async function generateDatasetFromFiles(
  payload: GenerateDatasetPayload,
  files: File[]
): Promise<GenerateDatasetResponse> {
  if (useMock) return mockApi.generateDataset(payload);
  const formData = new FormData();
  files.forEach((file) => {
    formData.append("files", file);
    // 优先取合成的相对路径(单 PDF 上传场景),否则取 webkitRelativePath(目录上传),最后退回文件名
    const synthetic = (file as File & { _syntheticRelativePath?: string })._syntheticRelativePath;
    formData.append("relative_paths", synthetic || file.webkitRelativePath || file.name);
  });
  formData.set("options", JSON.stringify(payload));
  return request("/api/datasets/generate/upload", {
    method: "POST",
    body: formData
  });
}

export async function getRun(runId: string): Promise<EvalRunDetail> {
  if (useMock) return mockApi.getRun(runId);
  return request(`/api/runs/${encodeURIComponent(runId)}`);
}

export async function getReport(runId: string): Promise<{ run_id: string; content: string }> {
  if (useMock) return mockApi.getReport(runId);
  return request(`/api/runs/${encodeURIComponent(runId)}/report`);
}

export function downloadArtifact(runId: string, name: string) {
  if (useMock) {
    const blob = new Blob([mockApi.artifactContent(name)], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = name;
    link.click();
    URL.revokeObjectURL(url);
    return;
  }
  window.open(`/api/runs/${encodeURIComponent(runId)}/artifacts/${encodeURIComponent(name)}`, "_blank");
}

function encodeDatasetPath(path: string) {
  // Dataset paths may contain non-ASCII characters; we need to URL-encode each
  // segment individually so that slashes remain slashes for FastAPI's {path:path}.
  return path
    .split("/")
    .map((segment) => encodeURIComponent(segment))
    .join("/");
}

export async function getDatasetRows(path: string): Promise<DatasetRowsResponse> {
  if (useMock) return mockApi.getDatasetRows(path);
  return request(`/api/dataset-rows/${encodeDatasetPath(path)}`);
}

export async function saveDatasetRows(
  path: string,
  rows: DatasetRow[],
  options: { target?: "main" | "draft" } = {}
): Promise<DatasetSaveResponse> {
  if (useMock) return mockApi.saveDatasetRows(path, rows);
  const target = options.target || "main";
  return request(
    `/api/dataset-rows/${encodeDatasetPath(path)}?target=${target}`,
    {
      method: "PUT",
      body: JSON.stringify({ rows })
    }
  );
}

export async function commitDatasetReview(
  path: string,
  rows: DatasetRow[],
  reviewedBy: string = ""
): Promise<DatasetCommitReviewResponse> {
  if (useMock) {
    // mock 模式：mockApi 不区分 main/draft，直接复用 saveDatasetRows 行为近似
    const saved = await mockApi.saveDatasetRows(path, rows);
    return {
      path: saved.path,
      sample_count: saved.sample_count,
      backup_path: saved.backup_path,
      reviewed_at: new Date().toISOString(),
      reviewed_by: reviewedBy || null
    };
  }
  return request(`/api/dataset-rows/${encodeDatasetPath(path)}/review`, {
    method: "POST",
    body: JSON.stringify({ rows, reviewed_by: reviewedBy })
  });
}

export async function exportDataset(path: string): Promise<DatasetExportResponse> {
  if (useMock) return mockApi.exportDataset(path);
  return request(`/api/dataset-rows/${encodeDatasetPath(path)}/export`);
}

export async function deleteDataset(path: string): Promise<DeleteDatasetResponse> {
  if (useMock) {
    return mockApi.deleteDataset(path);
  }
  return request(`/api/datasets/${encodeDatasetPath(path)}`, {
    method: "DELETE"
  });
}

export async function deleteRun(runId: string): Promise<DeleteRunResponse> {
  if (useMock) {
    return mockApi.deleteRun(runId);
  }
  return request(`/api/runs/${encodeURIComponent(runId)}`, {
    method: "DELETE"
  });
}

export interface RenameRunResponse {
  id: string;
  name: string;
  updated_at?: string | null;
}

// 修改对比分析用的模型标签（embedding / rerank 模型名）。
// 走专用 POST 端点，不复用 PATCH /runs/{id}，保持后端"窄接口"原则。
// 两个字段都允许显式传 null/"" 清空（后端归一化为 NULL），
// 前端固定两个字段一起送，避免出现"改一半"的中间态。
export interface UpdateRunLabelsRequest {
  embedding_model: string | null;
  rerank_model: string | null;
}

export interface UpdateRunLabelsResponse {
  id: string;
  embedding_model?: string | null;
  rerank_model?: string | null;
  updated_at?: string | null;
}

// 仅改 name，其它字段一律拒绝，防止 PATCH 被当成万能更新入口。
export async function renameRun(runId: string, name: string): Promise<RenameRunResponse> {
  if (useMock) {
    // mock 模式无后端，直接在本地 state 里 echo 回去，调用方自己负责写回列表
    return { id: runId, name };
  }
  return request(`/api/runs/${encodeURIComponent(runId)}`, {
    method: "PATCH",
    body: JSON.stringify({ name })
  });
}

// 修改对比分析用的模型标签（embedding / rerank 模型名）。
// 走专用 POST 端点，不复用 PATCH /runs/{id}，保持后端"窄接口"原则。
export async function updateRunLabels(
  runId: string,
  labels: { embedding_model: string | null; rerank_model: string | null }
): Promise<UpdateRunLabelsResponse> {
  if (useMock) {
    return mockApi.updateRunLabels(runId, labels);
  }
  return request(`/api/runs/${encodeURIComponent(runId)}/labels`, {
    method: "POST",
    body: JSON.stringify(labels)
  });
}

export async function checkHealth(): Promise<HealthStatus> {
  // 始终走真实接口：mock 模式也得能感知后端活着，因为顶部状态条是平台信号而非业务数据
  try {
    const data = await request<{ status: string; service: string; version: string }>("/api/health");
    return {
      ok: data?.status === "ok",
      status: data?.status || "unknown",
      service: data?.service || "dify-kb-eval",
      version: data?.version || "0.0.0"
    };
  } catch (err) {
    return {
      ok: false,
      status: "unreachable",
      service: "dify-kb-eval",
      version: "0.0.0",
      error: err instanceof Error ? err.message : "健康检查失败"
    };
  }
}
