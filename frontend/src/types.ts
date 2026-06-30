export type EvalRunStatus = "queued" | "running" | "completed" | "failed" | "canceled";

export interface HealthStatus {
  ok: boolean;
  status: string;
  service: string;
  version: string;
  error?: string;
}

export interface DatasetInfo {
  id: string;
  name: string;
  path: string;
  sample_count: number;
  vendor: string;
  model: string;
  version: string;
  updated_at: string;
  scenario_distribution?: Record<string, number>;
  // 人工审核：unreviewed | draft | reviewed
  review_status?: "unreviewed" | "draft" | "reviewed" | string;
  draft_path?: string | null;
  reviewed_at?: string | null;
  reviewed_by?: string | null;
  generated_at?: string | null;
}

export interface EvalRunMetrics {
  [key: string]: number | undefined;
  "content_recall@5"?: number;
  "document_recall@5"?: number;
  "section_recall@5"?: number;
  "keyword_recall@5"?: number;
  "content_precision@5"?: number;
  "content_ndcg@5"?: number;
  content_mrr?: number;
  document_mrr?: number;
  empty_result_rate?: number;
  avg_latency_ms?: number;
  p95_latency_ms?: number;
  error_queries?: number;
}

export interface EvalRunListItem {
  id: string;
  name: string;
  status: EvalRunStatus;
  created_at: string;
  finished_at?: string | null;
  duration_ms?: number | null;
  eval_file: string;
  dataset_id?: string;
  top_k: number;
  sample_count: number;
  query_count: number;
  metrics: EvalRunMetrics;
  langsmith_url?: string | null;
  // 对比分析标签：embedding / rerank 模型名。历史 run 这两个字段
  // 可能为 null，统一展示为 "(空)"，不参与检索逻辑。
  embedding_model?: string | null;
  rerank_model?: string | null;
  // 评测发起时连的 Dify API 地址；用于在 RunCompare 里按"当前 Dify"过滤。
  // 旧 run 可能为空。
  dify_base_url?: string | null;
}

export interface EvalRunProgress {
  total_queries: number;
  completed_queries: number;
  error_queries: number;
  current_sample_id?: string | null;
  // Mirrors ``runs.last_heartbeat_at`` in PG. Optional so older
  // snapshots without the field still type-check.
  last_heartbeat_at?: string | null;
}

export interface EvalRunConfig {
  dify_base_url: string;
  dataset_id?: string;
  eval_file: string;
  top_k: number;
  include_alternatives: boolean;
  limit: number;
  sample_ids: string[];
  embedding_model?: string;
  rerank_model?: string;
}

export interface FailedSample {
  sample_id: string;
  topic: string;
  query: string;
  content_hit_rank?: number | null;
  doc_hit_rank: number | null;
  top1_document: string;
  expected_documents: string[];
  error?: string;
}

export interface RetrievalResult {
  rank: number;
  document_id: string;
  document_name: string;
  score: number;
  doc_hit: boolean;
  section_hit: boolean;
  keyword_hit: boolean;
  content_hit: boolean;
  keyword_matches: string[];
  content_preview: string;
}

export interface RetrievalSample {
  sample_id: string;
  topic: string;
  query: string;
  query_kind: string;
  expected_documents: string[];
  expected_sections: string[];
  content_hit_rank?: number | null;
  doc_hit_rank?: number | null;
  section_hit_rank?: number | null;
  keyword_hit_rank?: number | null;
  top_results: RetrievalResult[];
  error?: string;
}

export interface EvalArtifact {
  name: string;
  type: string;
  url: string;
}

export interface EvalRunDetail extends EvalRunListItem {
  started_at?: string | null;
  progress: EvalRunProgress;
  config: EvalRunConfig;
  summary: {
    overall: EvalRunMetrics & {
      total_queries?: number;
      completed_queries?: number;
      error_queries?: number;
    };
    by_scenario_type: Record<string, EvalRunMetrics>;
  };
  failed_samples: FailedSample[];
  retrieval_samples: RetrievalSample[];
  artifacts: EvalArtifact[];
  error?: string;
}

export interface CreateRunPayload {
  name: string;
  dify_base_url: string;
  dify_api_key: string;
  dataset_id: string;
  eval_file: string;
  top_k: number;
  include_alternatives: boolean;
  limit: number;
  sample_ids: string[];
  timeout_seconds: number;
  langsmith_enabled: boolean;
  langsmith_project: string;
  // 对比分析标签：空串表示不参与对比分组（后端归一化为 NULL）。
  embedding_model: string;
  rerank_model: string;
}

export interface CreateRunResponse {
  id: string;
  status: EvalRunStatus;
  created_at: string;
  links: {
    detail: string;
  };
}

export interface DifyConnectionConfigItem {
  id: string;
  dify_base_url: string;
  dify_api_key: string;
  dify_api_key_masked: string;
  created_at?: string | null;
  last_used_at?: string | null;
  use_count: number;
}

export interface DifyConnectionConfigListResponse {
  items: DifyConnectionConfigItem[];
  total: number;
}

export interface GenerateDatasetPayload {
  source_directory: string;
  source_files: string[];
  vendor: string;
  model: string;
  output_name: string;
  document_name: string;
  max_samples: number;
  min_section_chars: number;
  reuse_existing_markdown: boolean;
  markitdown_command: string;
  markitdown_timeout_seconds: number;
  overwrite: boolean;
}

export interface GenerateDatasetResponse {
  dataset: {
    path: string;
    name: string;
    sample_count: number;
    vendor: string;
    model: string;
    knowledge_base_name: string;
  };
  output_file: string;
  draft_path?: string | null;
  review_meta_path?: string | null;
  review_status?: "unreviewed" | "draft" | "reviewed" | string;
  knowledge_base_name: string;
  sample_count: number;
  source_directory: string;
  markdown_output_dir: string;
  preview_samples: Array<Record<string, unknown>>;
  source_files: string[];
  markdown_files: string[];
  mineru_conversions: Array<{
    source_file: string;
    markdown_file: string;
    command: string;
    stderr_tail?: string;
    status?: "converted" | "skipped";
    message?: string;
  }>;
  pdf_parser_used?: "mineru" | "markitdown" | "auto" | string;
}

export type DatasetRow = Record<string, unknown> & {
  id: string;
  vendor: string;
  model: string;
  scenario_type: string;
  topic: string;
  question: string;
  expected_documents: string[];
  expected_sections: string[];
  expected_keywords: string[];
  evaluation_focus: string;
  difficulty?: string;
  alternative_queries?: string[];
};

export interface DatasetRowsResponse {
  path: string;
  name: string;
  vendor: string;
  model: string;
  version: string;
  sample_count: number;
  updated_at?: string | null;
  scenario_types: string[];
  rows: DatasetRow[];
  review_status?: "unreviewed" | "draft" | "reviewed" | string;
  draft_path?: string | null;
  reviewed_at?: string | null;
  reviewed_by?: string | null;
  generated_at?: string | null;
  draft_rows?: DatasetRow[] | null;
}

export interface DatasetRowValidationError {
  row_index: number;
  sample_id?: string;
  field?: string;
  message: string;
}

export interface DatasetSaveResponse {
  path: string;
  sample_count: number;
  backup_path: string;
  saved_at: string;
  validation_errors: DatasetRowValidationError[];
  target?: "main" | "draft";
}

export interface DatasetCommitReviewResponse {
  path: string;
  sample_count: number;
  backup_path: string;
  reviewed_at: string;
  reviewed_by?: string | null;
}

export interface DatasetExportResponse {
  path: string;
  name: string;
  content: string;
}

export interface DeleteDatasetResponse {
  path: string;
  backup_path: string;
  removed: string[];
}

export interface DeleteRunResponse {
  id: string;
  status: string;
  backup_path: string | null;
}

export interface RenameRunResponse {
  id: string;
  name: string;
  updated_at?: string | null;
}

// Dify 上的知识库条目。前端在创建运行页用它做"选 KB"下拉。
// embedding_model / embedding_model_provider / retrieval_model_dict 三个字段由
// 上游服务返回自 Dify 知识库真实绑定配置；前端在"选完 KB"后用它们自动回填
// Run 表单里的对比标签，避免手填错字、对比页分组错乱。三个字段都是 optional：
// 旧记录不带、纯展示用 KB 也能正常显示；UI 在"未选 KB"时把这两个输入禁用。
export interface KnowledgeBaseItem {
  dataset_id: string;
  name: string;
  display_name: string;
  vendor: string;
  model: string;
  description: string;
  document_count: number;
  embedding_model?: string;
  embedding_model_provider?: string;
  retrieval_model_dict?: {
    reranking_enable?: boolean;
    reranking_model?: {
      reranking_provider_name?: string;
      reranking_model_name?: string;
    };
    [key: string]: unknown;
  };
}

export interface KnowledgeBaseListResponse {
  items: KnowledgeBaseItem[];
  total: number;
  limit: number;
  offset: number;
}
