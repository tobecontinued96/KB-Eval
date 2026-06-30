# Dify-KB-Eval API 契约

## 1. 约定

后端独立运行在 `Dify-KB-Eval` 内部，推荐默认地址：

```text
http://127.0.0.1:8200
```

所有业务接口统一使用 `/api` 前缀。前端只调用本评测后端，不直接调用 Dify。

状态枚举：

| 状态 | 含义 |
|---|---|
| `queued` | 已创建，等待执行 |
| `running` | 正在调用上游知识库接口并计算指标 |
| `completed` | 执行完成，报告已落盘 |
| `failed` | 执行失败，错误写入 `manifest.json` |
| `canceled` | 预留，已取消 |

错误响应统一格式：

```json
{
  "code": "RUN_NOT_FOUND",
  "message": "评测运行不存在",
  "detail": {}
}
```

## 2. 健康检查

```http
GET /api/health
```

响应：

```json
{
  "status": "ok",
  "service": "dify-kb-eval",
  "version": "0.1.0"
}
```

## 3. 评测集列表

```http
GET /api/datasets
```

后端扫描 `datasets/*.jsonl`，并可额外暴露项目根目录已有的 S1720 评测集。

响应：

```json
{
  "items": [
    {
      "id": "huawei_s1720",
      "name": "华为 S1720 知识库评测集",
      "path": "datasets/huawei_s1720.jsonl",
      "sample_count": 70,
      "vendor": "华为",
      "model": "S1720",
      "version": "v0.1",
      "updated_at": "2026-06-09T00:00:00+08:00"
    }
  ]
}
```

## 4. 创建评测运行

```http
POST /api/runs
```

请求：

页面上对应的两个输入项叫“Dify API 地址”和“Dify API Key”。请求体直接使用 `dify_base_url` / `dify_api_key`。

```json
{
  "name": "Huawei S1720 知识库 (BGE-Embedding + BGE-Rerank) Top5 基线评测",
  "dify_base_url": "http://localhost/v1",
  "dify_api_key": "kb-secret",
  "dataset_id": "dify-dataset-id",
  "eval_file": "datasets/huawei_s1720.jsonl",
  "top_k": 5,
  "include_alternatives": false,
  "limit": 20,
  "sample_ids": [],
  "timeout_seconds": 60,
  "embedding_model": "bge-large-zh-v1.5",
  "rerank_model": "bge-reranker-v2-m3"
}
```

字段说明：

| 字段 | 必填 | 说明 |
|---|---|---|
| `name` | 否 | 运行名称，空值时后端生成 |
| `dify_base_url` | 是 | Dify API 地址，可带或不带 `/v1` |
| `dify_api_key` | 是 | Dify Knowledge Base API Key，仅内存使用，不写入报告明文 |
| `dataset_id` | 是 | Dify 知识库 ID，必须由页面“选 KB”下拉或接口调用方显式指定 |
| `eval_file` | 是 | JSONL 评测集路径 |
| `top_k` | 是 | 召回数量，范围 `1-20` |
| `include_alternatives` | 否 | 是否纳入同义问法 |
| `limit` | 否 | 只执行前 N 条样本，`0` 表示不限制 |
| `sample_ids` | 否 | 只执行指定样本 |
| `timeout_seconds` | 否 | 单次上游请求超时 |
| `embedding_model` | 否 | 对比分析用的 embedding 标签，后端会把空串归一为 NULL |
| `rerank_model` | 否 | 对比分析用的 rerank 标签，后端会把空串归一为 NULL |

创建运行前会强制检查评测集审核状态和 `dataset_id`。只有 `reviewed` 状态且已显式指定目标知识库时允许创建运行；`draft` 和 `unreviewed` 会返回 `DATASET_REVIEW_REQUIRED`，未选择知识库会返回 `DATASET_ID_REQUIRED`。

响应：

```json
{
  "id": "20260609-113000-s1720-top5",
  "status": "queued",
  "created_at": "2026-06-09T11:30:00+08:00",
  "links": {
    "detail": "/api/runs/20260609-113000-s1720-top5"
  }
}
```

校验规则：

- `top_k` 必须在 `1-20`。
- `limit` 不能小于 `0`。
- `eval_file` 必须位于 `Dify-KB-Eval/datasets` 或显式允许的项目文档目录，避免任意文件读取。
- `dify_api_key` 不写入 `manifest.json`、`summary.json`、`report.md`。Dify API 地址和 Key 会成对写入后端历史连接配置表，用于后续下拉复用。

### 历史连接配置

```http
GET /api/dify-connections?limit=20
POST /api/dify-connections
DELETE /api/dify-connections/{config_id}
```

`POST` 请求体：

```json
{
  "dify_base_url": "http://localhost/v1",
  "dify_api_key": "kb-secret"
}
```

同一组 `dify_base_url` + `dify_api_key` 只保留一条记录，重复保存会刷新 `last_used_at` 和 `use_count`。`GET` 响应会返回完整 Key 供前端回填，同时返回 `dify_api_key_masked` 供下拉展示。

`DELETE` 按 `config_id` 删除一条历史记录；id 不存在返回 `DIFY_CONNECTION_CONFIG_NOT_FOUND`（HTTP 404），空 id 返回 `VALIDATION_ERROR`（HTTP 422）。前端在下拉项的悬停 × 上触发，调用前会先弹 `ConfirmDialog` 二次确认。

## 5. 运行列表

```http
GET /api/runs?status=completed&limit=20&offset=0&dify_base_url=http%3A%2F%2Fdify-A%2Fv1
```

`dify_base_url` 可选：传了之后只返回该 Dify 地址下的 run（前端"分析对比"页用此参数按当前 Dify 隔离列表，避免不同 Dify 的 run 混在同一矩阵里）。空串或未传则不按 Dify 过滤。响应里每条 `RunListItem` 都带 `dify_base_url` 字段（历史可能为空）。

响应：

```json
{
  "items": [
    {
      "id": "20260609-113000-s1720-top5",
      "name": "Huawei S1720 知识库 (BGE-Embedding + BGE-Rerank) Top5 基线评测",
      "status": "completed",
      "created_at": "2026-06-09T11:30:00+08:00",
      "finished_at": "2026-06-09T11:31:12+08:00",
      "duration_ms": 72000,
      "eval_file": "datasets/huawei_s1720.jsonl",
      "dataset_id": "dify-dataset-id",
      "top_k": 5,
      "sample_count": 20,
      "query_count": 20,
      "metrics": {
        "document_recall@5": 0.91,
        "document_mrr": 0.78,
        "empty_result_rate": 0.0,
        "avg_latency_ms": 1230
      },
      "embedding_model": "bge-large-zh-v1.5",
      "rerank_model": "bge-reranker-v2-m3"
    }
  ],
  "total": 1
}
```

## 6. 运行详情

```http
GET /api/runs/{run_id}
```

响应：

```json
{
  "id": "20260609-113000-s1720-top5",
  "name": "Huawei S1720 知识库 (BGE-Embedding + BGE-Rerank) Top5 基线评测",
  "status": "completed",
  "created_at": "2026-06-09T11:30:00+08:00",
  "started_at": "2026-06-09T11:30:02+08:00",
  "finished_at": "2026-06-09T11:31:12+08:00",
  "duration_ms": 70000,
  "progress": {
    "total_queries": 20,
    "completed_queries": 20,
    "error_queries": 0,
    "current_sample_id": null
  },
  "config": {
    "dify_base_url": "http://localhost/v1",
    "dataset_id": "dify-dataset-id",
    "eval_file": "datasets/huawei_s1720.jsonl",
    "top_k": 5,
    "include_alternatives": false,
    "limit": 20,
    "sample_ids": []
  },
  "summary": {
    "overall": {
      "total_queries": 20,
      "completed_queries": 20,
      "error_queries": 0,
      "document_recall@5": 0.91,
      "section_recall@5": 0.76,
      "keyword_recall@5": 0.82,
      "document_mrr": 0.78,
      "empty_result_rate": 0.0,
      "avg_latency_ms": 1230,
      "p95_latency_ms": 2100
    },
    "by_scenario_type": {}
  },
  "failed_samples": [
    {
      "sample_id": "HW-S1720-EVAL-004",
      "topic": "清空配置",
      "query": "如何把华为 S1720 恢复到出厂配置？",
      "doc_hit_rank": null,
      "top1_document": "01-02 常见堆叠操作.pdf",
      "expected_documents": ["01-01 常见系统操作.pdf"],
      "error": ""
    }
  ],
  "artifacts": [
    {
      "name": "results.jsonl",
      "type": "jsonl",
      "url": "/api/runs/20260609-113000-s1720-top5/artifacts/results.jsonl"
    },
    {
      "name": "results.csv",
      "type": "csv",
      "url": "/api/runs/20260609-113000-s1720-top5/artifacts/results.csv"
    },
    {
      "name": "console.log",
      "type": "log",
      "url": "/api/runs/20260609-113000-s1720-top5/artifacts/console.log"
    }
  ],
  "error": ""
}
```

## 7. Markdown 报告

```http
GET /api/runs/{run_id}/report
```

响应：

```json
{
  "run_id": "20260609-113000-s1720-top5",
  "content": "# 知识库检索评测报告\n..."
}
```

前端用 Markdown 组件渲染。若报告不存在，返回 `404 REPORT_NOT_FOUND`。

## 8. 产物下载

```http
GET /api/runs/{run_id}/artifacts/{name}
```

允许下载：

| 文件名 | Content-Type |
| --- | --- |
| `results.jsonl` | `application/x-ndjson` |
| `results.csv` | `text/csv; charset=utf-8` |
| `console.log` | `text/plain; charset=utf-8` |

`manifest.json` / `summary.json` / `report.md` 跑完后会被搬到 PostgreSQL 的 `runs` / `run_summaries` / `run_reports` 表，磁盘副本被清理，不再作为可下载产物。

不允许使用 `../` 等路径穿越。

## 9. 轮询策略

当前使用轮询，避免过早引入 SSE/WebSocket：

- 创建运行后，前端每 `2s` 请求一次 `GET /api/runs/{run_id}`。
- `completed`、`failed`、`canceled` 为终态，停止轮询。
- 查询列表页时，每 `10s` 轻量刷新一次即可。

如后续评测耗时变长，可增加：

```http
GET /api/runs/{run_id}/events
```

通过 SSE 推送 `progress`、`log`、`completed`。

## 12. 生成评测集

```http
POST /api/datasets/generate
```

前端选择文件夹时使用：

```http
POST /api/datasets/generate/upload
Content-Type: multipart/form-data
```

表单字段：

- `files`：浏览器目录选择器返回的 PDF、MD、Markdown 文件，可重复提交。
- `relative_paths`：与 `files` 一一对应的 `webkitRelativePath`，用于解析 `厂商/型号` 目录层级。
- `options`：与下方 JSON 请求结构一致的 JSON 字符串。

推荐源文档结构：

```text
华为/S1720/
  01-01 常见系统操作.pdf
  01-02 常见堆叠操作.pdf
  md/
    01-01 常见系统操作.md
    01-02 常见堆叠操作.md
```

请求：

```json
{
  "source_directory": "华为/S1720",
  "vendor": "华为",
  "model": "S1720",
  "output_name": "huawei_s1720_generated.jsonl",
  "max_samples": 80,
  "min_section_chars": 80,
  "reuse_existing_markdown": true,
  "markitdown_command": "",
  "markitdown_timeout_seconds": 300,
  "overwrite": false
}
```

规则：

- 后端优先扫描 `source_directory/*.pdf`，并复用 `source_directory/md/<同名>.md`。
- 浏览器上传模式使用 `FormData` 直接上传文件，不再由后端弹出 Windows 目录选择框。
- 浏览器上传的源文件保存到 `Dify-KB-Eval/generated_sources/<厂商>/<型号>/`，处理后的 Markdown 保存到其 `md/` 子目录。
- 浏览器不允许网页读取任意本地绝对路径，因此型号从目录选择器返回的相对路径解析，厂商由用户从候选下拉中选择或输入自定义值；不要求手工填写源目录路径。
- PDF 解析固定调用本地 MarkItDown。优先使用 Python 包，缺失时回退到 `markitdown` CLI；都不可用时返回 `EvalError`。
- 可选环境变量 `MARKITDOWN_COMMAND` 等价于请求字段 `markitdown_command`，支持 `{input}` / `{output}` 占位。
- 当没有同名 Markdown 时，后端调用 MarkItDown，并把处理后的 Markdown 保存到 `source_directory/md/`。
- 为兼容旧客户端，后端仍接受 `use_mineru` / `mineru_*` / `pdf_parser` 字段，但生成服务会忽略这些字段并始终使用 MarkItDown。
- 响应体 `pdf_parser_used` 固定返回 `markitdown`。
- JSONL 评测集保存到 `Dify-KB-Eval/datasets/generated/`，生成后会出现在 `GET /api/datasets` 列表中。
- 返回的 `knowledge_base_name` 为 `vendor + " " + model`，例如 `华为 S1720`。
- **审核工作流**：生成器不再直接覆盖 `<stem>.jsonl`，而是把样本写入 `<stem>.draft.jsonl` 并把状态写为 `draft`。
  - 返回体额外字段：`draft_path`（草稿文件相对路径）、`review_meta_path`（`<stem>.review.json` 元信息路径）、`review_status`（`draft`）。
  - 草稿经过人工在编辑器里审核后调用 `POST /api/dataset-rows/{path}/review`，后端把内容覆盖到主 JSONL、删除草稿、写入审核元信息（`reviewed_at` / `reviewed_by`）。

## 评测集编辑接口

评测集编辑器（前端 `/datasets/<path>/editor`）通过以下接口读取、改写并下载 JSONL 评测集。`path` 为相对 `datasets/` 或 `docs/` 的路径，例如 `datasets/generated/思科_Catalyst_1200_generated.jsonl`，URL 中需要按段 `encodeURIComponent` 编码。

### GET /api/dataset-rows/{path}

读取指定 JSONL 的全部样本行，包含原始 `metadata` 等额外字段。

- 若同目录下存在 `<stem>.draft.jsonl` 草稿，则 `rows` 返回草稿内容（编辑器优先编辑草稿），同时在 `draft_rows` 字段返回主文件的旧样本（用于"恢复旧版"对比）。
- `review_status` 取值：
  - `unreviewed`：旧版样本，未走审核流程。
  - `draft`：存在草稿，等待人工审核。
  - `reviewed`：已通过审核（`reviewed_at` / `reviewed_by` 非空）。

```json
{
  "path": "datasets/generated/思科_Catalyst_1200_generated.jsonl",
  "name": "思科_Catalyst_1200_generated",
  "vendor": "思科",
  "model": "Catalyst 1200",
  "version": "v0.1",
  "sample_count": 16,
  "updated_at": "2026-06-12T10:47:24+08:00",
  "scenario_types": ["配置操作", "故障恢复", "查询诊断"],
  "rows": [
    { "id": "AUTO-CATALYST1200-EVAL-001", "...": "..." }
  ]
}
```

### PUT /api/dataset-rows/{path}

校验后写回 JSONL；保存前先在同目录生成 `<file>.jsonl.bak`（已存在则累加 `.bak2`、`.bak3`）。校验失败返回 422 并附带行级错误列表。

请求：

```json
{ "rows": [ { "id": "AUTO-...", "...": "..." } ] }
```

成功响应：

```json
{
  "path": "datasets/generated/思科_Catalyst_1200_generated.jsonl",
  "sample_count": 16,
  "backup_path": "datasets/generated/思科_Catalyst_1200_generated.jsonl.bak",
  "saved_at": "2026-06-12T10:47:24+08:00",
  "validation_errors": [],
  "target": "main"
}
```

`?target=` 查询参数：

- `target=main`（默认）：写到主 JSONL（与旧版语义一致）。
- `target=draft`：写到 `<stem>.draft.jsonl` 草稿，备份也以 `*.draft.jsonl.bak` 命名。响应中 `path` 返回草稿路径、`target="draft"`。

校验失败响应（HTTP 422）：

```json
{
  "code": "VALIDATION_ERROR",
  "message": "请求参数校验失败",
  "detail": {
    "errors": [],
    "validation_errors": [
      { "row_index": 5, "sample_id": "AUTO-...", "field": "id", "message": "id 重复（与第 1 行）" }
    ]
  }
}
```

校验规则与 `kb_eval.dataset.load_samples` 一致：必填字段、列表类型、ID 唯一性。`alternative_queries` 可缺失但必须是字符串数组；`metadata` 等额外字段会原样保留。

### GET /api/dataset-rows/{path}/export

返回评测集原始 JSONL 文本，便于前端"下载原文件"或离线备份。

```json
{
  "path": "datasets/generated/思科_Catalyst_1200_generated.jsonl",
  "name": "思科_Catalyst_1200_generated",
  "content": "{\"id\":\"AUTO-...\"}\n"
}
```

### POST /api/dataset-rows/{path}/review

把当前审核后的样本提交为已审核状态。请求体内的 `rows` 为审核人最终确认要落盘的样本，服务端会再次校验；通过后：

1. 把原 `<stem>.jsonl` 备份为 `*.jsonl.bak`（已存在累加 `.bak2` 等）。
2. 用请求体内容覆盖 `<stem>.jsonl`。
3. 删除 `<stem>.draft.jsonl` 草稿。
4. 在 `<stem>.review.json` 中写入 `status=reviewed`、`reviewed_at`、`reviewed_by`、`generated_at`、`backup_path`。

请求：

```json
{
  "rows": [
    { "id": "AUTO-CATALYST1200-EVAL-001", "...": "..." }
  ],
  "reviewed_by": "张三"
}
```

响应：

```json
{
  "path": "datasets/generated/思科_Catalyst_1200_generated.jsonl",
  "sample_count": 16,
  "backup_path": "datasets/generated/思科_Catalyst_1200_generated.jsonl.bak",
  "reviewed_at": "2026-06-12T11:30:00+08:00",
  "reviewed_by": "张三"
}
```

校验失败返回 400 + 行级错误列表（与 PUT 一致）。

### 错误码

| 状态码 | code | 说明 |
| --- | --- | --- |
| 404 | `DATASET_NOT_FOUND` | 评测集文件不存在或路径超出白名单 |
| 400 / 422 | `DATASET_INVALID_ROWS` / `VALIDATION_ERROR` | 行级校验失败（详见 `validation_errors`） |
| 400 | `DATASET_PATH_FORBIDDEN` | 路径不是 JSONL 或不在 `datasets/` / `docs/` 允许目录内 |

### DELETE /api/datasets/{path:path}

删除一个评测集（主 JSONL + 草稿 + 审核元信息）。

- 主 JSONL 会被复制为 `<name>.deleted-<UTC 时间戳>.bak` 作为一次性备份，备份不会被后续 `PUT` 覆盖。
- 同目录下的 `<stem>.draft.jsonl` 草稿与 `<stem>.review.json` 审核元信息会被一并清理。
- 路径必须落在 `_allowed_roots()` 白名单内，否则返回 `400 DATASET_PATH_FORBIDDEN`。
- 不存在的文件返回 `404 DATASET_NOT_FOUND`（重复删除或删后被覆盖均视为这种情况）。

成功响应（HTTP 200）：

```json
{
  "path": "datasets/generated/思科_Catalyst_1200_generated.jsonl",
  "backup_path": "datasets/generated/思科_Catalyst_1200_generated.jsonl.deleted-20260615T180030+0800.bak",
  "removed": [
    "datasets/generated/思科_Catalyst_1200_generated.jsonl",
    "datasets/generated/思科_Catalyst_1200_generated.draft.jsonl",
    "datasets/generated/思科_Catalyst_1200_generated.review.json"
  ]
}
```

前端在删除后应把对应 `id` 从本地列表中移除，并保留 `backup_path` 以便误删后恢复。

### DELETE /api/runs/{run_id}

删除一次历史评测的整份目录（含 `manifest.json` / `summary.json` / `results.jsonl` / `results.csv` / `report.md` / `console.log` 等所有产物），删除前自动备份。

- 整份目录会被复制到 `reports/<id>.deleted-<UTC 时间戳>` 作为一次性备份，备份不会被后续运行覆盖；如需恢复，把备份目录重命名/合并回 `reports/<id>/` 即可。
- 进行中（`running` / `queued`）的运行不允许直接删除，必须先等待终态；前端在按钮上会同步禁用。
- 目录不存在返回 `404 RUN_NOT_FOUND`。

成功响应（HTTP 200）：

```json
{
  "id": "20260615-180000-s1720-top5",
  "status": "completed",
  "backup_path": "reports/20260615-180000-s1720-top5.deleted-20260615T180030+0800"
}
```

错误响应：

| 状态码 | code | 说明 |
| --- | --- | --- |
| 400 | `RUN_DELETE_FAILED` | 运行正在进行中（`running` / `queued`） |
| 404 | `RUN_NOT_FOUND` | 目录不存在或 `run_id` 非法 |
