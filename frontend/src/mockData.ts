import type {
  CreateRunPayload,
  CreateRunResponse,
  DatasetExportResponse,
  DatasetInfo,
  DatasetRow,
  DatasetRowValidationError,
  DatasetRowsResponse,
  DatasetSaveResponse,
  DeleteDatasetResponse,
  DeleteRunResponse,
  DifyConnectionConfigItem,
  DifyConnectionConfigListResponse,
  EvalArtifact,
  EvalRunDetail,
  EvalRunListItem,
  EvalRunMetrics,
  EvalRunStatus,
  FailedSample,
  GenerateDatasetPayload,
  GenerateDatasetResponse,
  KnowledgeBaseItem,
  KnowledgeBaseListResponse,
  RetrievalSample
} from "./types";

const now = new Date("2026-06-09T11:30:00+08:00");

export const mockDatasets: DatasetInfo[] = [
  {
    id: "huawei_s1720",
    name: "华为 S1720 知识库评测集",
    path: "datasets/huawei_s1720.jsonl",
    sample_count: 6,
    vendor: "华为",
    model: "S1720",
    version: "v0.1",
    updated_at: "2026-06-09T00:00:00+08:00",
    scenario_distribution: {
      配置操作: 2,
      查询诊断: 2,
      故障恢复: 2
    }
  }
];

const mockDatasetRows: Record<string, DatasetRow[]> = {
  "datasets/huawei_s1720.jsonl": [
    {
      id: "HW-S1720-EVAL-001",
      vendor: "华为",
      model: "S1720",
      scenario_type: "故障恢复",
      topic: "Console 密码恢复",
      difficulty: "中等",
      question: "华为 S1720 Console 口登录密码忘了，应该怎么恢复？",
      alternative_queries: [
        "S1720 忘记 console 密码怎么清除？",
        "华为交换机 Console 登录不了，密码丢失后如何处理？"
      ],
      expected_documents: ["01-01 常见系统操作.pdf"],
      expected_sections: ["1.1 Console 口登录密码丢失后如何恢复"],
      expected_keywords: ["Console", "BootROM", "BootLoad", "Clear password for console user"],
      evaluation_focus: "应命中 Console 口密码恢复章节，并区分远程登录修改密码与 BootROM/BootLoad 清除密码两类路径。"
    },
    {
      id: "HW-S1720-EVAL-002",
      vendor: "华为",
      model: "S1720",
      scenario_type: "配置操作",
      topic: "VLAN 创建",
      difficulty: "基础",
      question: "S1720 怎样创建一个新的 VLAN 并把接口加入？",
      alternative_queries: [
        "华为 S1720 新建 VLAN 步骤？",
        "S1720 vlan batch 命令怎么用？"
      ],
      expected_documents: ["01-02 常见VLAN操作.pdf"],
      expected_sections: ["创建 VLAN 并加入接口"],
      expected_keywords: ["vlan", "vlan batch", "port default vlan"],
      evaluation_focus: "应能命中 VLAN 基础配置章节，覆盖批量创建与接口绑定。"
    },
    {
      id: "HW-S1720-EVAL-003",
      vendor: "华为",
      model: "S1720",
      scenario_type: "查询诊断",
      topic: "接口状态查看",
      difficulty: "基础",
      question: "S1720 怎么查看接口当前是 up 还是 down？",
      alternative_queries: ["display interface brief 怎么看？"],
      expected_documents: ["01-03 常见接口操作.pdf"],
      expected_sections: ["查看接口状态"],
      expected_keywords: ["display interface", "up", "down"],
      evaluation_focus: "应命中 display interface brief 章节，并提示物理/协议状态差异。"
    },
    {
      id: "HW-S1720-EVAL-004",
      vendor: "华为",
      model: "S1720",
      scenario_type: "故障恢复",
      topic: "清空配置",
      difficulty: "中等",
      question: "如何把华为 S1720 恢复到出厂配置？",
      alternative_queries: ["S1720 reset saved-configuration 怎么操作？"],
      expected_documents: ["01-01 常见系统操作.pdf"],
      expected_sections: ["清空配置文件"],
      expected_keywords: ["reset saved-configuration", "reboot", "display startup"],
      evaluation_focus: "应命中清空配置章节，覆盖 reset saved-configuration 与 reboot 的执行顺序。"
    },
    {
      id: "HW-S1720-EVAL-005",
      vendor: "华为",
      model: "S1720",
      scenario_type: "查询诊断",
      topic: "MAC 表查看",
      difficulty: "基础",
      question: "S1720 怎么查看交换机的 MAC 地址表？",
      alternative_queries: ["display mac-address 用法？"],
      expected_documents: ["01-04 常见MAC表操作.pdf"],
      expected_sections: ["查看 MAC 地址表"],
      expected_keywords: ["display mac-address", "MAC", "VLAN"],
      evaluation_focus: "应能命中 MAC 表章节并提示按 VLAN 过滤。"
    },
    {
      id: "HW-S1720-EVAL-006",
      vendor: "华为",
      model: "S1720",
      scenario_type: "配置操作",
      topic: "链路聚合",
      difficulty: "高级",
      question: "S1720 怎样配置两条物理链路做链路聚合？",
      alternative_queries: ["eth-trunk 配置示例？", "S1720 链路聚合命令？"],
      expected_documents: ["01-05 常见链路聚合操作.pdf"],
      expected_sections: ["配置手工负载分担链路聚合"],
      expected_keywords: ["interface eth-trunk", "trunkport", "load-balance"],
      evaluation_focus: "应命中链路聚合章节，覆盖 Eth-Trunk 创建、加入成员接口与负载分担模式。"
    }
  ]
};

// 模拟"同一型号、不同 embedding/rerank"的多知识库场景，方便前端联调。
export const mockKnowledgeBases: KnowledgeBaseItem[] = [
  {
    dataset_id: "kb-huawei-s1720-embed-bge",
    name: "Huawei S1720 知识库 (BGE-Embedding + BGE-Rerank)",
    display_name: "Huawei S1720 KB · bge",
    vendor: "华为",
    model: "S1720",
    description: "默认配置：bge-large-zh embedding + bge-reranker-base",
    document_count: 42
  },
  {
    dataset_id: "kb-huawei-s1720-embed-m3e",
    name: "Huawei S1720 知识库 (M3E-Embedding + 不重排)",
    display_name: "Huawei S1720 KB · m3e",
    vendor: "华为",
    model: "S1720",
    description: "对比组：m3e 嵌入，无 rerank",
    document_count: 42
  },
  {
    dataset_id: "kb-huawei-s1720-embed-bge-rerank-cohere",
    name: "Huawei S1720 知识库 (BGE + Cohere Rerank)",
    display_name: "Huawei S1720 KB · bge+cohere",
    vendor: "华为",
    model: "S1720",
    description: "对比组：bge embedding + cohere rerank",
    document_count: 42
  },
  {
    dataset_id: "kb-h3c-s6850-embed-bge",
    name: "H3C S6850 知识库 (BGE-Embedding)",
    display_name: "H3C S6850 KB · bge",
    vendor: "新华三",
    model: "S6850",
    description: "另一型号对照组",
    document_count: 38
  },
  {
    dataset_id: "kb-ruijie-rg-s6510-embed-bge",
    name: "Ruijie RG-S6510 知识库 (BGE-Embedding)",
    display_name: "Ruijie RG-S6510 KB · bge",
    vendor: "锐捷",
    model: "RG-S6510",
    description: "另一厂商对照组",
    document_count: 27
  }
];

function maskDifyApiKey(value: string) {
  const cleaned = value.trim();
  if (!cleaned) return "";
  if (cleaned.length <= 8) return "****";
  return `${cleaned.slice(0, 4)}...${cleaned.slice(-4)}`;
}

let mockDifyConnectionConfigs: DifyConnectionConfigItem[] = [
  {
    id: "mock-dify-connection-local",
    dify_base_url: "http://localhost/v1",
    dify_api_key: "mock-dify-api-key",
    dify_api_key_masked: maskDifyApiKey("mock-dify-api-key"),
    created_at: new Date(now.getTime() - 60 * 60_000).toISOString(),
    last_used_at: new Date(now.getTime() - 10 * 60_000).toISOString(),
    use_count: 1
  }
];

function sortMockDifyConnections() {
  mockDifyConnectionConfigs = [...mockDifyConnectionConfigs].sort(
    (a, b) =>
      new Date(b.last_used_at || 0).getTime() - new Date(a.last_used_at || 0).getTime()
  );
}

function buildMockDatasetResponse(path: string): DatasetRowsResponse {
  const rows = mockDatasetRows[path] || [];
  const info = mockDatasets.find((item) => item.path === path);
  const distribution = info?.scenario_distribution || {};
  const scenarioTypes = Object.keys(distribution);
  return {
    path,
    name: info?.name || path,
    vendor: info?.vendor || (rows[0]?.vendor as string) || "",
    model: info?.model || (rows[0]?.model as string) || "",
    version: info?.version || "v0.1",
    sample_count: rows.length,
    updated_at: info?.updated_at || new Date().toISOString(),
    scenario_types: scenarioTypes,
    rows
  };
}

const REQUIRED_STRING_FIELDS = [
  "id",
  "vendor",
  "model",
  "scenario_type",
  "topic",
  "question",
  "evaluation_focus"
] as const;
const REQUIRED_LIST_FIELDS = [
  "expected_documents",
  "expected_sections",
  "expected_keywords"
] as const;

function validateMockRows(rows: DatasetRow[]): DatasetRowValidationError[] {
  const errors: DatasetRowValidationError[] = [];
  const seen = new Map<string, number>();
  rows.forEach((row, index) => {
    const id = (row?.id as string) || `row-${index + 1}`;
    for (const field of REQUIRED_STRING_FIELDS) {
      const value = row?.[field];
      if (typeof value !== "string" || !value.trim()) {
        errors.push({ row_index: index, sample_id: id, field, message: `${field} 不能为空` });
      }
    }
    for (const field of REQUIRED_LIST_FIELDS) {
      const value = row?.[field];
      if (!Array.isArray(value) || value.length === 0 || value.some((item) => !String(item).trim())) {
        errors.push({ row_index: index, sample_id: id, field, message: `${field} 至少 1 个非空字符串` });
      }
    }
    const alt = row?.alternative_queries;
    if (alt !== undefined && alt !== null) {
      if (!Array.isArray(alt) || alt.some((item) => typeof item !== "string")) {
        errors.push({ row_index: index, sample_id: id, field: "alternative_queries", message: "alternative_queries 必须是字符串数组" });
      }
    }
    if (seen.has(id)) {
      errors.push({ row_index: index, sample_id: id, field: "id", message: `id 重复（与第 ${(seen.get(id) ?? 0) + 1} 行）` });
    } else {
      seen.set(id, index);
    }
  });
  return errors;
}

const artifacts = (runId: string): EvalArtifact[] => [
  { name: "report.md", type: "markdown", url: `/api/runs/${runId}/artifacts/report.md` },
  { name: "summary.json", type: "json", url: `/api/runs/${runId}/artifacts/summary.json` },
  { name: "results.jsonl", type: "jsonl", url: `/api/runs/${runId}/artifacts/results.jsonl` },
  { name: "results.csv", type: "csv", url: `/api/runs/${runId}/artifacts/results.csv` },
  { name: "console.log", type: "log", url: `/api/runs/${runId}/artifacts/console.log` }
];

const failedSamples: FailedSample[] = [
  {
    sample_id: "HW-S1720-EVAL-004",
    topic: "清空配置",
    query: "如何把华为 S1720 恢复到出厂配置？",
    doc_hit_rank: null,
    top1_document: "01-02 常见堆叠操作.pdf",
    expected_documents: ["01-01 常见系统操作.pdf"],
    error: ""
  },
  {
    sample_id: "HW-S1720-EVAL-019",
    topic: "DHCP Snooping",
    query: "S1720 怎么确认 DHCP Snooping 是否已经在 VLAN 里生效？",
    doc_hit_rank: 7,
    top1_document: "01-13 常见DHCP操作.pdf",
    expected_documents: ["01-13 常见DHCP操作.pdf"],
    error: ""
  },
  {
    sample_id: "HW-S1720-EVAL-031",
    topic: "STP 根桥",
    query: "怎样查看当前交换机是不是 STP 根桥？",
    doc_hit_rank: null,
    top1_document: "",
    expected_documents: ["01-12 常见STP RSTP操作.pdf"],
    error: "知识库检索返回空结果"
  }
];

const retrievalSamples: RetrievalSample[] = [
  {
    sample_id: "HW-S1720-EVAL-001",
    topic: "恢复出厂配置",
    query: "华为 S1720 如何清空配置并恢复出厂设置？",
    query_kind: "original",
    expected_documents: ["01-01 常见系统操作.pdf"],
    expected_sections: ["清空配置文件"],
    content_hit_rank: 1,
    doc_hit_rank: null,
    section_hit_rank: 1,
    keyword_hit_rank: 1,
    top_results: [
      {
        rank: 1,
        document_id: "mock-doc-001",
        document_name: "S1720 系列交换机 常用操作指南.pdf",
        score: 0.8421,
        doc_hit: false,
        section_hit: true,
        keyword_hit: true,
        content_hit: true,
        keyword_matches: ["reset saved-configuration", "reboot"],
        content_preview: "执行 reset saved-configuration 清空下次启动使用的配置文件，然后重启设备使配置恢复为缺省状态。"
      },
      {
        rank: 2,
        document_id: "mock-doc-002",
        document_name: "S1720 系列交换机 常用操作指南.pdf",
        score: 0.7314,
        doc_hit: false,
        section_hit: false,
        keyword_hit: true,
        content_hit: true,
        keyword_matches: ["display startup"],
        content_preview: "可通过 display startup 查看当前启动配置文件和下次启动配置文件，确认清空配置后的启动状态。"
      }
    ],
    error: ""
  },
  {
    sample_id: "HW-S1720-EVAL-019",
    topic: "DHCP Snooping",
    query: "S1720 怎么确认 DHCP Snooping 是否已经在 VLAN 里生效？",
    query_kind: "original",
    expected_documents: ["01-13 常见DHCP操作.pdf"],
    expected_sections: ["DHCP Snooping 配置检查"],
    content_hit_rank: 2,
    doc_hit_rank: null,
    section_hit_rank: 2,
    keyword_hit_rank: 1,
    top_results: [
      {
        rank: 1,
        document_id: "mock-doc-101",
        document_name: "S1720 系列交换机 常用操作指南.pdf",
        score: 0.8093,
        doc_hit: false,
        section_hit: false,
        keyword_hit: true,
        content_hit: true,
        keyword_matches: ["DHCP Snooping"],
        content_preview: "DHCP Snooping 用于防止私设 DHCP Server，可在 VLAN 视图下启用并查看绑定表状态。"
      },
      {
        rank: 2,
        document_id: "mock-doc-102",
        document_name: "S1720 系列交换机 常用操作指南.pdf",
        score: 0.7728,
        doc_hit: false,
        section_hit: true,
        keyword_hit: true,
        content_hit: true,
        keyword_matches: ["display dhcp snooping"],
        content_preview: "使用 display dhcp snooping 命令可以查看 DHCP Snooping 的全局、接口或 VLAN 使能状态。"
      }
    ],
    error: ""
  }
];

const byScenario: Record<string, EvalRunMetrics> = {
  配置操作: {
    "document_recall@5": 0.92,
    document_mrr: 0.81,
    empty_result_rate: 0,
    avg_latency_ms: 1180
  },
  查询诊断: {
    "document_recall@5": 0.94,
    document_mrr: 0.83,
    empty_result_rate: 0,
    avg_latency_ms: 1030
  },
  故障恢复: {
    "document_recall@5": 0.86,
    document_mrr: 0.71,
    empty_result_rate: 0.04,
    avg_latency_ms: 1320
  },
  安全与准入: {
    "document_recall@5": 0.88,
    document_mrr: 0.73,
    empty_result_rate: 0,
    avg_latency_ms: 1490
  }
};

function iso(offsetMinutes: number) {
  return new Date(now.getTime() - offsetMinutes * 60_000).toISOString();
}

function listItem(
  id: string,
  name: string,
  status: EvalRunStatus,
  minutesAgo: number,
  metrics: EvalRunMetrics,
  topK = 5
): EvalRunListItem {
  return {
    id,
    name,
    status,
    created_at: iso(minutesAgo),
    finished_at: status === "completed" ? iso(minutesAgo - 2) : null,
    duration_ms: status === "completed" ? 72_000 : null,
    eval_file: "datasets/huawei_s1720.jsonl",
    dataset_id: "dify-dataset-id",
    top_k: topK,
    sample_count: 20,
    query_count: 20,
    metrics,
    langsmith_url: status === "completed" ? "https://smith.langchain.com/o/mock/projects/p/dify-kb-eval" : null
  };
}

const seedRuns: EvalRunListItem[] = [
  listItem(
    "20260609-113000-s1720-top5",
    "Huawei S1720 知识库 (BGE-Embedding + BGE-Rerank) Top5 基线评测",
    "completed",
    34,
    {
      "document_recall@5": 0.91,
      "section_recall@5": 0.76,
      "keyword_recall@5": 0.82,
      document_mrr: 0.78,
      empty_result_rate: 0,
      avg_latency_ms: 1230,
      p95_latency_ms: 2100,
      error_queries: 0
    }
  ),
  listItem(
    "20260609-105300-s1720-top3",
    "Huawei S1720 知识库 (BGE-Embedding + BGE-Rerank) Top3 收紧召回评测",
    "completed",
    72,
    {
      "document_recall@5": 0.84,
      "section_recall@5": 0.68,
      "keyword_recall@5": 0.74,
      document_mrr: 0.72,
      empty_result_rate: 0.02,
      avg_latency_ms: 960,
      p95_latency_ms: 1660,
      error_queries: 1
    },
    3
  ),
  listItem(
    "20260609-101500-s1720-alt",
    "Huawei S1720 知识库 (BGE-Embedding + BGE-Rerank) 同义问法鲁棒性",
    "failed",
    110,
    {
      "document_recall@5": 0.62,
      document_mrr: 0.51,
      empty_result_rate: 0.08,
      avg_latency_ms: 1800,
      error_queries: 4
    }
  )
];

const report = `# 知识库检索评测报告

## 运行配置

- 评测集：华为 S1720 知识库评测集
- Dify API：\`http://127.0.0.1:5001\`
- Top K：5
- 样本上限：20
- 同义问法：关闭

## 指标总览

| 指标 | 结果 |
|---|---:|
| Document Recall@5 | 91.0% |
| Section Recall@5 | 76.0% |
| Keyword Recall@5 | 82.0% |
| Document MRR | 0.780 |
| Empty Result Rate | 0.0% |
| Avg Latency | 1230ms |

## 复盘建议

清空配置、DHCP Snooping、STP 根桥三个主题需要补充更明确的章节标题和关键词。当前 Top5 基线达到一期门槛，建议后续用同义问法跑一次鲁棒性对比。
`;

const createdAtByRun = new Map<string, number>();
let runs = [...seedRuns];

function toDetail(item: EvalRunListItem): EvalRunDetail {
  const total = item.query_count || 20;
  const createdAt = createdAtByRun.get(item.id);
  const elapsed = createdAt ? Date.now() - createdAt : 99_000;
  const generatedStatus = item.status === "queued" || item.status === "running"
    ? elapsed > 7500
      ? "completed"
      : elapsed > 1500
        ? "running"
        : "queued"
    : item.status;
  const completed = generatedStatus === "completed"
    ? total
    : generatedStatus === "running"
      ? Math.max(2, Math.min(total - 1, Math.floor(elapsed / 600)))
      : 0;

  const metrics = generatedStatus === "completed"
    ? {
        "document_recall@5": 0.91,
        "section_recall@5": 0.76,
        "keyword_recall@5": 0.82,
        document_mrr: 0.78,
        empty_result_rate: 0,
        avg_latency_ms: 1230,
        p95_latency_ms: 2100,
        error_queries: 0
      }
    : item.metrics;

  return {
    ...item,
    status: generatedStatus,
    finished_at: generatedStatus === "completed" ? item.finished_at || new Date().toISOString() : item.finished_at,
    duration_ms: generatedStatus === "completed" ? item.duration_ms || 8200 : item.duration_ms,
    progress: {
      total_queries: total,
      completed_queries: completed,
      error_queries: generatedStatus === "failed" ? 4 : 0,
      current_sample_id: generatedStatus === "running" ? `HW-S1720-EVAL-${String(completed + 1).padStart(3, "0")}` : null
    },
    config: {
      dify_base_url: "http://127.0.0.1/v1",
      dataset_id: item.dataset_id,
      eval_file: item.eval_file,
      top_k: item.top_k,
      include_alternatives: item.name.includes("同义"),
      limit: item.sample_count,
      sample_ids: []
    },
    summary: {
      overall: metrics,
      by_scenario_type: byScenario
    },
    failed_samples: generatedStatus === "completed" || generatedStatus === "failed" ? failedSamples : [],
    retrieval_samples: generatedStatus === "completed" || generatedStatus === "failed" ? retrievalSamples : [],
    artifacts: artifacts(item.id),
    error: generatedStatus === "failed" ? "知识库检索接口返回 502，请确认 Dify 服务状态" : "",
    langsmith_url: generatedStatus === "completed" ? item.langsmith_url : null
  };
}

function updateRunFromDetail(detail: EvalRunDetail) {
  runs = runs.map((item) => {
    if (item.id !== detail.id) return item;
    return {
      ...item,
      status: detail.status,
      finished_at: detail.finished_at,
      duration_ms: detail.duration_ms,
      metrics: detail.summary.overall,
      langsmith_url: detail.langsmith_url
    };
  });
}

function slug(text: string) {
  const compact = text.trim().toLowerCase().replace(/[^a-z0-9\u4e00-\u9fa5]+/g, "-").replace(/^-|-$/g, "");
  return compact || "dify-kb-eval";
}

export const mockApi = {
  async listDatasets() {
    await delay(180);
    return { items: mockDatasets };
  },

  async listRuns(params: { difyBaseUrl?: string } = {}) {
    await delay(220);
    runs = runs.map((item) => {
      const detail = toDetail(item);
      return {
        ...item,
        status: detail.status,
        finished_at: detail.finished_at,
        duration_ms: detail.duration_ms,
        metrics: detail.summary.overall
      };
    });
    const target = (params.difyBaseUrl || "").trim();
    const filtered = target
      ? runs.filter((item) => (item.dify_base_url || "").trim() === target)
      : runs;
    return { items: filtered, total: filtered.length };
  },

  async listDifyConnectionConfigs(limit = 20): Promise<DifyConnectionConfigListResponse> {
    await delay(120);
    sortMockDifyConnections();
    return {
      items: mockDifyConnectionConfigs.slice(0, limit),
      total: mockDifyConnectionConfigs.length
    };
  },

  async saveDifyConnectionConfig(payload: {
    dify_base_url: string;
    dify_api_key: string;
  }): Promise<DifyConnectionConfigItem> {
    await delay(160);
    const difyBaseUrl = payload.dify_base_url.trim();
    const difyApiKey = payload.dify_api_key.trim();
    if (!difyBaseUrl || !difyApiKey) {
      throw new Error("Dify API 地址和 API Key 都不能为空");
    }
    const nowIso = new Date().toISOString();
    const existing = mockDifyConnectionConfigs.find(
      (item) => item.dify_base_url === difyBaseUrl && item.dify_api_key === difyApiKey
    );
    if (existing) {
      existing.last_used_at = nowIso;
      existing.use_count += 1;
      sortMockDifyConnections();
      return existing;
    }
    const item: DifyConnectionConfigItem = {
      id: `mock-dify-connection-${Date.now()}`,
      dify_base_url: difyBaseUrl,
      dify_api_key: difyApiKey,
      dify_api_key_masked: maskDifyApiKey(difyApiKey),
      created_at: nowIso,
      last_used_at: nowIso,
      use_count: 1
    };
    mockDifyConnectionConfigs.unshift(item);
    sortMockDifyConnections();
    return item;
  },

  async deleteDifyConnectionConfig(
    configId: string
  ): Promise<DifyConnectionConfigItem | null> {
    await delay(160);
    const index = mockDifyConnectionConfigs.findIndex(
      (item) => item.id === configId
    );
    if (index < 0) return null;
    const [removed] = mockDifyConnectionConfigs.splice(index, 1);
    return removed;
  },

  async createRun(payload: CreateRunPayload): Promise<CreateRunResponse> {
    await delay(420);
    const createdAt = new Date().toISOString();
    const id = `${createdAt.slice(0, 10).replaceAll("-", "")}-${createdAt.slice(11, 19).replaceAll(":", "")}-${slug(payload.name)}`;
    createdAtByRun.set(id, Date.now());
    const item: EvalRunListItem = {
      id,
      name: payload.name || "知识库检索评测",
      status: "queued",
      created_at: createdAt,
      finished_at: null,
      duration_ms: null,
      eval_file: payload.eval_file,
      dataset_id: payload.dataset_id,
      top_k: payload.top_k,
      sample_count: payload.limit || mockDatasets[0].sample_count,
      query_count: payload.limit || mockDatasets[0].sample_count,
      metrics: {},
      langsmith_url: null
    };
    runs = [item, ...runs];
    return {
      id,
      status: "queued",
      created_at: createdAt,
      links: {
        detail: `/api/runs/${id}`
      }
    };
  },

  async generateDataset(payload: GenerateDatasetPayload): Promise<GenerateDatasetResponse> {
    await delay(520);
    const path = `datasets/generated/${payload.output_name.replace(/\.jsonl$/i, "") || "generated_eval_dataset"}.jsonl`;
    const knowledgeBaseName = `${payload.vendor} ${payload.model}`.trim();
    const dataset: DatasetInfo = {
      id: path.split("/").pop()?.replace(/\.jsonl$/i, "") || "generated_eval_dataset",
      name: `${payload.vendor} ${payload.model} 自动生成评测集`,
      path,
      sample_count: Math.min(payload.max_samples, 12),
      vendor: payload.vendor,
      model: payload.model,
      version: "v0.1",
      updated_at: new Date().toISOString(),
      scenario_distribution: {
        配置操作: 5,
        查询诊断: 4,
        故障恢复: 3
      }
    };
    if (!mockDatasets.some((item) => item.path === path)) {
      mockDatasets.unshift(dataset);
    }
    return {
      dataset: {
        path,
        name: dataset.name,
        sample_count: dataset.sample_count,
        vendor: payload.vendor,
        model: payload.model,
        knowledge_base_name: knowledgeBaseName
      },
      output_file: path,
      knowledge_base_name: knowledgeBaseName,
      sample_count: dataset.sample_count,
      source_directory: payload.source_directory,
      markdown_output_dir: `${payload.source_directory || `${payload.vendor}/${payload.model}`}/md`,
      source_files: payload.source_files.length ? payload.source_files : [`${payload.source_directory}/example.pdf`],
      markdown_files: (payload.source_files.length ? payload.source_files : [`${payload.source_directory}/example.pdf`]).map((item) => item.replace(/\.pdf$/i, ".md")),
      mineru_conversions: (payload.source_files.length ? payload.source_files : [`${payload.source_directory}/example.pdf`])
        .filter((item) => item.toLowerCase().endsWith(".pdf"))
        .map((item) => ({
          source_file: item,
          markdown_file: item.replace(/([^/\\]+)\.pdf$/i, "md/$1.md"),
          command: payload.markitdown_command || "markitdown <pdf> -o <output>"
        })),
      pdf_parser_used: "markitdown",
      preview_samples: [
        {
          id: "AUTO-SAMPLE-001",
          question: `${payload.vendor} ${payload.model} 如何配置示例章节？`,
          expected_documents: [payload.document_name || "source.pdf"],
          expected_sections: ["示例章节"],
          expected_keywords: ["display", "配置", payload.model]
        }
      ]
    };
  },

  async getRun(runId: string) {
    await delay(180);
    const item = runs.find((run) => run.id === runId);
    if (!item) throw new Error("评测运行不存在");
    const detail = toDetail(item);
    updateRunFromDetail(detail);
    return detail;
  },

  async getReport(runId: string) {
    await delay(120);
    return {
      run_id: runId,
      content: report
    };
  },

  artifactContent(name: string) {
    if (name === "report.md") return report;
    if (name === "summary.json") {
      return JSON.stringify(toDetail(runs[0]).summary, null, 2);
    }
    if (name === "results.csv") {
      return "sample_id,query_kind,scenario_type,topic,query,result_count,doc_hit_rank,latency_ms,error\nHW-S1720-EVAL-004,primary,配置操作,清空配置,如何把华为 S1720 恢复到出厂配置？,5,,1230,\n";
    }
    if (name === "results.jsonl") {
      return JSON.stringify({
        sample_id: "HW-S1720-EVAL-004",
        query_kind: "primary",
        result_count: 5,
        doc_hit_rank: null,
        latency_ms: 1230
      });
    }
    return "[11:30:02] run started\n[11:31:12] report written\n";
  },

  async getDatasetRows(path: string): Promise<DatasetRowsResponse> {
    await delay(160);
    if (!mockDatasetRows[path]) {
      throw new Error(`评测集不存在：${path}`);
    }
    return buildMockDatasetResponse(path);
  },

  async saveDatasetRows(path: string, rows: DatasetRow[]): Promise<DatasetSaveResponse> {
    await delay(200);
    if (!mockDatasetRows[path] && !mockDatasets.some((item) => item.path === path)) {
      throw new Error(`评测集不存在：${path}`);
    }
    const errors = validateMockRows(rows);
    if (errors.length > 0) {
      // Throw to align with the real backend, which returns 422.
      const error = new Error("保存失败：存在校验错误") as Error & { validation_errors?: DatasetRowValidationError[] };
      (error as { validation_errors?: DatasetRowValidationError[] }).validation_errors = errors;
      throw error;
    }
    mockDatasetRows[path] = rows;
    const distribution: Record<string, number> = {};
    rows.forEach((row) => {
      const key = (row.scenario_type as string) || "未分类";
      distribution[key] = (distribution[key] || 0) + 1;
    });
    const dataset = mockDatasets.find((item) => item.path === path);
    if (dataset) {
      dataset.sample_count = rows.length;
      dataset.scenario_distribution = distribution;
      dataset.updated_at = new Date().toISOString();
    }
    return {
      path,
      sample_count: rows.length,
      backup_path: `${path}.bak`,
      saved_at: new Date().toISOString(),
      validation_errors: []
    };
  },

  async exportDataset(path: string): Promise<DatasetExportResponse> {
    await delay(80);
    const rows = mockDatasetRows[path] || [];
    const content = rows.map((row) => JSON.stringify(row)).join("\n") + (rows.length ? "\n" : "");
    return { path, name: path.split("/").pop() || path, content };
  },

  async deleteDataset(path: string): Promise<DeleteDatasetResponse> {
    await delay(180);
    const existsInList = mockDatasets.some((item) => item.path === path);
    const hasRows = Boolean(mockDatasetRows[path]);
    if (!existsInList && !hasRows) {
      throw new Error(`评测集不存在：${path}`);
    }
    // mock：不真正写文件，把行/列表项清理掉即可
    delete mockDatasetRows[path];
    const idx = mockDatasets.findIndex((item) => item.path === path);
    if (idx >= 0) mockDatasets.splice(idx, 1);
    return {
      path,
      backup_path: `${path}.deleted-mock.bak`,
      removed: [path]
    };
  },

  async deleteRun(runId: string): Promise<DeleteRunResponse> {
    await delay(180);
    const idx = runs.findIndex((item) => item.id === runId);
    if (idx < 0) {
      throw new Error(`运行不存在：${runId}`);
    }
    const removed = runs[idx];
    // mock 模式下进行中的运行也允许"删掉"以便演练 UI；真实后端会拦截。
    runs.splice(idx, 1);
    createdAtByRun.delete(runId);
    return {
      id: runId,
      status: removed.status,
      backup_path: `reports/${runId}.deleted-mock`
    };
  },

  async updateRunLabels(
    runId: string,
    labels: { embedding_model: string | null; rerank_model: string | null }
  ) {
    await delay(180);
    const idx = runs.findIndex((item) => item.id === runId);
    if (idx < 0) {
      throw new Error(`运行不存在：${runId}`);
    }
    // mock 模式下：空串/全空白 → null，与后端 store 归一化保持一致
    const normalize = (value: string | null) => {
      if (value === null) return null;
      const cleaned = value.trim();
      return cleaned ? cleaned : null;
    };
    const nextEmbedding = normalize(labels.embedding_model);
    const nextRerank = normalize(labels.rerank_model);
    runs[idx] = {
      ...runs[idx],
      embedding_model: nextEmbedding,
      rerank_model: nextRerank
    };
    return {
      id: runId,
      embedding_model: nextEmbedding,
      rerank_model: nextRerank,
      updated_at: new Date().toISOString()
    };
  },

  async listKnowledgeBases(params: {
    dify_base_url: string;
    dify_api_key?: string;
    keyword?: string;
    limit?: number;
    offset?: number;
  }): Promise<KnowledgeBaseListResponse> {
    await delay(150);
    let items = mockKnowledgeBases.slice();
    if (params.keyword) {
      const needle = params.keyword.trim().toLowerCase();
      if (needle) {
        items = items.filter((kb) =>
          [kb.name, kb.display_name, kb.vendor, kb.model, kb.description, kb.dataset_id]
            .some((value) => (value || "").toLowerCase().includes(needle))
        );
      }
    }
    const total = items.length;
    const offset = params.offset ?? 0;
    const limit = params.limit ?? 50;
    return {
      items: items.slice(offset, offset + limit),
      total,
      limit,
      offset
    };
  }
};

function delay(ms: number) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}
