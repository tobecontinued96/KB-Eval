"""Pydantic schemas for the Dify knowledge-base evaluation backend API."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


RunStatus = Literal["queued", "running", "completed", "failed", "canceled"]


class ErrorResponse(BaseModel):
    code: str
    message: str
    detail: dict[str, Any] = Field(default_factory=dict)


class HealthResponse(BaseModel):
    status: str = "ok"
    service: str = "dify-kb-eval"
    version: str = "0.1.0"


class DatasetInfo(BaseModel):
    id: str
    name: str
    path: str
    sample_count: int
    vendor: str = ""
    model: str = ""
    version: str = "v0.1"
    updated_at: str | None = None
    scenario_types: list[str] = Field(default_factory=list)
    scenario_distribution: dict[str, int] = Field(default_factory=dict)
    # 人工审核状态：
    #   unreviewed - 老样本，未走审核流程
    #   draft      - 旁边有 *.draft.jsonl 等待人工审核
    #   reviewed   - 已通过审核（旁边有 *.review.json）
    review_status: str = "unreviewed"
    draft_path: str | None = None
    reviewed_at: str | None = None
    reviewed_by: str | None = None
    generated_at: str | None = None


class DatasetListResponse(BaseModel):
    items: list[DatasetInfo]


class GenerateDatasetRequest(BaseModel):
    source_directory: str = ""
    source_files: list[str] = Field(default_factory=list)
    vendor: str = ""
    model: str = ""
    output_name: str = ""
    document_name: str = ""
    max_samples: int = Field(default=50, ge=1, le=300)
    min_section_chars: int = Field(default=80, ge=20, le=3000)
    # Backwards-compatible fields from the old MinerU UI. The generation
    # service ignores them now; PDF parsing is fixed to MarkItDown.
    use_mineru: bool = False
    reuse_existing_markdown: bool = True
    mineru_provider: Literal["auto", "api", "local"] = "auto"
    mineru_api_token: str = ""
    mineru_model_version: Literal["pipeline", "vlm"] = "vlm"
    mineru_command: str = ""
    mineru_timeout_seconds: int = Field(default=900, ge=30, le=7200)
    # Backwards-compatible request field; accepted but ignored.
    pdf_parser: Literal["mineru", "markitdown", "auto"] = "markitdown"
    markitdown_command: str = ""
    markitdown_timeout_seconds: int = Field(default=300, ge=30, le=7200)
    overwrite: bool = False


class GeneratedDatasetInfo(BaseModel):
    path: str
    name: str
    sample_count: int
    vendor: str
    model: str
    knowledge_base_name: str


class MinerUConversionInfo(BaseModel):
    source_file: str
    markdown_file: str
    command: str
    stderr_tail: str = ""
    status: Literal["converted", "skipped"] = "converted"
    message: str = ""


class GenerateDatasetResponse(BaseModel):
    dataset: GeneratedDatasetInfo
    output_file: str
    draft_path: str | None = None
    review_meta_path: str | None = None
    review_status: str = "draft"
    knowledge_base_name: str
    sample_count: int
    source_directory: str = ""
    markdown_output_dir: str = ""
    preview_samples: list[dict[str, Any]] = Field(default_factory=list)
    source_files: list[str] = Field(default_factory=list)
    markdown_files: list[str] = Field(default_factory=list)
    mineru_conversions: list[MinerUConversionInfo] = Field(default_factory=list)
    pdf_parser_used: str = "markitdown"


class CreateRunRequest(BaseModel):
    name: str = ""
    dify_base_url: str
    dify_api_key: str = ""
    dataset_id: str = ""
    eval_file: str
    top_k: int = Field(default=5, ge=1, le=20)
    include_alternatives: bool = False
    limit: int = Field(default=0, ge=0)
    sample_ids: list[str] = Field(default_factory=list)
    timeout_seconds: float = Field(default=60, gt=0)
    langsmith_enabled: bool = False
    langsmith_project: str = "dify-kb-eval"
    # 仅用作对比分析的标签：embedding / rerank 模型名。空串在 store 层
    # 归一化为 NULL（旧 run 不填也不报错）。
    embedding_model: str = ""
    rerank_model: str = ""


class CreateRunResponse(BaseModel):
    id: str
    status: RunStatus
    created_at: str
    links: dict[str, str]


class RunListItem(BaseModel):
    id: str
    name: str
    status: RunStatus
    created_at: str
    finished_at: str | None = None
    duration_ms: int | None = None
    eval_file: str
    dataset_id: str
    top_k: int
    sample_count: int = 0
    query_count: int = 0
    metrics: dict[str, Any] = Field(default_factory=dict)
    langsmith_url: str | None = None
    embedding_model: str | None = None
    rerank_model: str | None = None
    # 评测发起时连的 Dify API 地址；用于"按当前 Dify 过滤 run 列表"。
    # 旧 run 可能为空（schema 在 d8d02a8 之前没有），保持可选。
    dify_base_url: str | None = None


class RunListResponse(BaseModel):
    items: list[RunListItem]
    total: int


class RunProgress(BaseModel):
    total_queries: int = 0
    completed_queries: int = 0
    error_queries: int = 0
    current_sample_id: str | None = None


class ArtifactInfo(BaseModel):
    name: str
    type: str
    url: str


class RunDetailResponse(BaseModel):
    id: str
    name: str
    status: RunStatus
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    duration_ms: int | None = None
    eval_file: str
    dataset_id: str
    top_k: int
    sample_count: int = 0
    query_count: int = 0
    metrics: dict[str, Any] = Field(default_factory=dict)
    progress: RunProgress
    config: dict[str, Any]
    summary: dict[str, Any] = Field(default_factory=dict)
    failed_samples: list[dict[str, Any]] = Field(default_factory=list)
    retrieval_samples: list[dict[str, Any]] = Field(default_factory=list)
    artifacts: list[ArtifactInfo] = Field(default_factory=list)
    langsmith_url: str | None = None
    error: str = ""
    embedding_model: str | None = None
    rerank_model: str | None = None


class ReportResponse(BaseModel):
    run_id: str
    content: str


class DeleteRunResponse(BaseModel):
    id: str
    status: str = ""
    backup_path: str | None = None


class RenameRunRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)


class RenameRunResponse(BaseModel):
    id: str
    name: str
    updated_at: str | None = None


# 对比分析用的两个标签（embedding / rerank 模型名）走专用端点，
# 不复用 PATCH /runs/{id}，避免 PATCH 演变成万能更新接口。请求里
# 两个字段都允许显式传 null/空串 → 后端归一化为 NULL。
class UpdateRunLabelsRequest(BaseModel):
    embedding_model: str | None = None
    rerank_model: str | None = None


class UpdateRunLabelsResponse(BaseModel):
    id: str
    embedding_model: str | None = None
    rerank_model: str | None = None
    updated_at: str | None = None


class DatasetRowValidationError(BaseModel):
    row_index: int
    sample_id: str = ""
    field: str = ""
    message: str


class DatasetRowsResponse(BaseModel):
    path: str
    name: str
    vendor: str = ""
    model: str = ""
    version: str = "v0.1"
    sample_count: int
    updated_at: str | None = None
    scenario_types: list[str] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    review_status: str = "unreviewed"
    draft_path: str | None = None
    reviewed_at: str | None = None
    reviewed_by: str | None = None
    generated_at: str | None = None
    # 草稿内容（若存在）：编辑时优先展示草稿
    draft_rows: list[dict[str, Any]] | None = None


class DatasetSaveRequest(BaseModel):
    rows: list[dict[str, Any]] = Field(default_factory=list)


class DatasetSaveResponse(BaseModel):
    path: str
    sample_count: int
    backup_path: str = ""
    saved_at: str
    validation_errors: list[DatasetRowValidationError] = Field(default_factory=list)
    # 保存动作落到哪里：main = 正式 jsonl，draft = 草稿
    target: str = "main"


class DatasetCommitReviewRequest(BaseModel):
    rows: list[dict[str, Any]] = Field(default_factory=list)
    reviewed_by: str = ""


class DatasetCommitReviewResponse(BaseModel):
    path: str
    sample_count: int
    backup_path: str = ""
    reviewed_at: str
    reviewed_by: str | None = None


class DatasetExportResponse(BaseModel):
    path: str
    name: str
    content: str


class LangSmithSyncRequest(BaseModel):
    eval_file: str
    dataset_name: str
    description: str = ""


class LangSmithSyncResponse(BaseModel):
    dataset_name: str
    example_count: int
    langsmith_url: str | None = None
    status: str = "disabled"


class LangSmithExperimentRequest(CreateRunRequest):
    pass


class LangSmithExperimentResponse(BaseModel):
    run_id: str
    experiment_name: str
    langsmith_url: str | None = None
    status: str = "disabled"


class KnowledgeBaseItem(BaseModel):
    """Remote Dify knowledge base entry.

    ``dataset_id`` is the primary identifier and the value that should be
    bound to CreateRunRequest.
    """

    dataset_id: str
    name: str = ""
    display_name: str = ""
    vendor: str = ""
    model: str = ""
    description: str = ""
    document_count: int = 0
    # 由上游服务返回的 Dify 知识库真实绑定配置。前端在"选完 KB"后用
    # embedding_model / retrieval_model_dict 自动写回 run 表单里的两个
    # 对比标签，避免用户手填错字、对比页分组错乱。三个字段均为可选：
    # 旧记录不带时，Dify-KB-Eval 仍能正常显示与检索。
    embedding_model: str = ""
    embedding_model_provider: str = ""
    retrieval_model_dict: dict[str, Any] = Field(default_factory=dict)


class KnowledgeBaseListResponse(BaseModel):
    items: list[KnowledgeBaseItem]
    total: int = 0
    limit: int = 0
    offset: int = 0


class DifyConnectionConfigRequest(BaseModel):
    dify_base_url: str = Field(min_length=1, max_length=512)
    dify_api_key: str = Field(min_length=1, max_length=4096)


class DifyConnectionConfigItem(BaseModel):
    id: str
    dify_base_url: str
    dify_api_key: str
    dify_api_key_masked: str
    created_at: str | None = None
    last_used_at: str | None = None
    use_count: int = 0


class DifyConnectionConfigListResponse(BaseModel):
    items: list[DifyConnectionConfigItem]
    total: int = 0


class DifyConnectionConfigDeleteResponse(BaseModel):
    id: str
    deleted: bool = True


class CompareRunGroup(BaseModel):
    """对比表里的一行：同一 (embedding, rerank, sample_count) 配置下的所有 run。

    ``runs`` 是该组下所有 RunListItem 列表；``best_run_id`` 是该组里
    Recall@5 最高（并列时 MRR 高、再并列耗时短）的 run，便于前端整行高亮。
    """

    embedding_model: str
    rerank_model: str
    sample_count: int
    runs: list[RunListItem]
    best_run_id: str | None = None


class CompareRunResponse(BaseModel):
    dataset_id: str
    top_k: int | None = None
    groups: list[CompareRunGroup] = Field(default_factory=list)
    total_runs: int = 0
