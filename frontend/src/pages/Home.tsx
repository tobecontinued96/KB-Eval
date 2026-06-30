import { type FormEvent, type InvalidEvent, type KeyboardEvent, useEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import {
  Activity,
  ArrowRight,
  Check,
  ChevronDown,
  Eye,
  EyeOff,
  Loader2,
  Play,
  RefreshCw,
  Search,
  Settings2,
  ShieldCheck,
  Trash2,
  TriangleAlert
} from "lucide-react";
import {
  createRun,
  deleteDifyConnectionConfig,
  listDatasets,
  listDifyConnectionConfigs,
  listKnowledgeBases,
  listRuns,
  saveDifyConnectionConfig
} from "../api";
import type {
  CreateRunPayload,
  DatasetInfo,
  DifyConnectionConfigItem,
  EvalRunListItem,
  KnowledgeBaseItem
} from "../types";
import { describeError } from "../errorCodes";
import { writeCurrentDifyUrl } from "../difySource";
import { showErrorToast } from "../widgets/ErrorToast";
import { formatPercent, metricTone } from "../utils";
import { Field } from "../widgets/Field";
import { PanelHeader } from "../widgets/PanelHeader";
import { StatusBadge } from "../widgets/StatusBadge";
import { ConfirmDialog } from "../widgets/ConfirmDialog";
import { StandardSelect } from "../widgets/StandardSelect";
import { buildHomeInsights, type HomeInsights } from "./homeInsights";
import { getRunHistoryRecallMetric } from "./runHistoryHelpers";
import {
  applyDifyConnectionConfig,
  findMatchingDifyConnection,
  formatDifyConnectionOption,
  isSameDifyConnection,
  resolveDifyConnectionCredentials
} from "./homeDifyCredentials";

function formatReviewTime(iso: string | null | undefined): string {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(
    date.getHours()
  )}:${pad(date.getMinutes())}`;
}

// 运行名 = 目标知识库名称 + Top<K> 基线评测。
// 一个评测集常被用来跑多个知识库，所以命名以 KB 为锚而不是 dataset，
// 否则切换 KB 时名字不变，列表里会分不清。
function defaultRunName(kb: Pick<KnowledgeBaseItem, "name" | "display_name"> | null, topK: number) {
  const baseName = (kb?.name || kb?.display_name || "").trim() || "未命名知识库";
  return `${baseName} Top${topK} 基线评测`;
}

// 知识库下拉里按名称升序展示。display_name 优先，回落到 name，
// 都没有时落到 dataset_id 兜底，避免出现 "(未命名)" 这种打乱顺序的项。
function kbSortKey(kb: KnowledgeBaseItem): string {
  return (kb.display_name || kb.name || kb.dataset_id || "").trim().toLowerCase();
}

function sortKnowledgeBases(items: KnowledgeBaseItem[]): KnowledgeBaseItem[] {
  return [...items].sort((a, b) => {
    const diff = kbSortKey(a).localeCompare(kbSortKey(b), "zh-Hans-CN");
    if (diff !== 0) return diff;
    return a.dataset_id.localeCompare(b.dataset_id);
  });
}

function knowledgeBaseLabel(kb: KnowledgeBaseItem): string {
  const baseName = (kb.name || kb.display_name || "").trim();
  const vendorModel = [kb.vendor, kb.model].filter(Boolean).join(" ").trim();
  const includeVendorModel =
    vendorModel &&
    baseName &&
    !baseName.toLowerCase().includes(vendorModel.toLowerCase());
  if (baseName) return includeVendorModel ? `${baseName} · ${vendorModel}` : baseName;
  return vendorModel || "(未命名知识库)";
}

function knowledgeBaseMatchesKeyword(kb: KnowledgeBaseItem, keyword: string): boolean {
  const query = keyword.trim().toLowerCase();
  if (!query) return true;
  return [kb.name, kb.display_name, kb.vendor, kb.model, kb.description, kb.dataset_id]
    .filter(Boolean)
    .some((value) => value.toLowerCase().includes(query));
}

const defaultForm: CreateRunPayload = {
  name: "",
  dify_base_url: "http://localhost/v1",
  dify_api_key: "",
  dataset_id: "",
  eval_file: "datasets/huawei_s1720.jsonl",
  top_k: 5,
  include_alternatives: false,
  limit: 20,
  sample_ids: [],
  timeout_seconds: 60,
  langsmith_enabled: false,
  langsmith_project: "dify-kb-eval",
  // 对比分析标签：可空。空串表示"未指定"，后端归一化为 NULL，
  // 对比接口统一按 "(空)" 展示，不参与检索逻辑。
  embedding_model: "",
  rerank_model: ""
};

// 用户上次设过的评测参数，刷新或重新进入 Home 时自动套用。
// 只持久化"用户偏好"字段：top_k / limit / timeout_seconds。
// 其他字段（dataset、name、labels）每次从远端 / 不存；连接地址和 token
// 由后端历史连接配置按一对值保存，避免和运行偏好混在一起。
// 单独记一份"已验证的当前 Dify URL"给历史 / 分析页用 —— 列表按它过滤，
// 让用户看到的是"当前已连接 Dify 下的 run"，而不是全量历史。
const RUN_PREFERENCES_STORAGE_KEY = "dify-kb-eval:home:run-preferences:v1";

interface RunPreferences {
  top_k: number;
  limit: number;
  timeout_seconds: number;
}

function readRunPreferences(): Partial<RunPreferences> {
  if (typeof window === "undefined") return {};
  try {
    const raw = window.localStorage.getItem(RUN_PREFERENCES_STORAGE_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw) as Partial<RunPreferences>;
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    // 解析失败 / quota 异常一律忽略，回落到 defaultForm。
    return {};
  }
}

function writeRunPreferences(prefs: RunPreferences): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(RUN_PREFERENCES_STORAGE_KEY, JSON.stringify(prefs));
  } catch {
    // 写入失败（quota / 隐私模式）静默吞掉，不阻塞主流程。
  }
}

function applyRunPreferences(base: CreateRunPayload, prefs: Partial<RunPreferences>): CreateRunPayload {
  const next: CreateRunPayload = { ...base };
  if (typeof prefs.top_k === "number" && Number.isFinite(prefs.top_k) && prefs.top_k > 0) {
    next.top_k = prefs.top_k;
  }
  if (typeof prefs.limit === "number" && Number.isFinite(prefs.limit) && prefs.limit >= 0) {
    next.limit = prefs.limit;
  }
  if (
    typeof prefs.timeout_seconds === "number" &&
    Number.isFinite(prefs.timeout_seconds) &&
    prefs.timeout_seconds >= 5
  ) {
    next.timeout_seconds = prefs.timeout_seconds;
  }
  return next;
}

interface HomeLocationState {
  pendingDatasetPath?: string;
}

interface FetchKnowledgeBaseOptions {
  difyBaseUrl?: string;
  difyApiKey?: string;
  keyword?: string;
}

export function Home() {
  const navigate = useNavigate();
  const location = useLocation();
  const pendingState = (location.state as HomeLocationState | null) ?? null;
  const [datasets, setDatasets] = useState<DatasetInfo[]>([]);
  const [runs, setRuns] = useState<EvalRunListItem[]>([]);
  const [form, setForm] = useState<CreateRunPayload>(() =>
    applyRunPreferences(defaultForm, readRunPreferences())
  );
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [showDifyApiKey, setShowDifyApiKey] = useState(false);
  const [difyConnections, setDifyConnections] = useState<DifyConnectionConfigItem[]>([]);
  const [difyConnectionsLoading, setDifyConnectionsLoading] = useState(false);
  const [difyConnectionSaving, setDifyConnectionSaving] = useState(false);
  const [difyConnectionPickerOpen, setDifyConnectionPickerOpen] = useState(false);
  const [pendingDifyConnectionAutoLoad, setPendingDifyConnectionAutoLoad] =
    useState<DifyConnectionConfigItem | null>(null);
  // 待二次确认要删除的连接配置 id；非 null 时弹 ConfirmDialog。
  const [difyConnectionPendingDelete, setDifyConnectionPendingDelete] = useState<
    DifyConnectionConfigItem | null
  >(null);
  const [difyConnectionDeleting, setDifyConnectionDeleting] = useState(false);
  // 远端 KB 下拉状态
  const [kbItems, setKbItems] = useState<KnowledgeBaseItem[]>([]);
  const [kbKeyword, setKbKeyword] = useState("");
  const [kbLoading, setKbLoading] = useState(false);
  const [kbPickerOpen, setKbPickerOpen] = useState(false);
  const [kbPickerInvalid, setKbPickerInvalid] = useState(false);
  const [kbHighlightedIndex, setKbHighlightedIndex] = useState(0);
  // 厂商 chip 过滤：空 = 不过滤，点中后只保留该 vendor 的 KB。
  // 与 keyword 是 AND 关系（搜索框可以再在 chip 命中的子集里二次过滤）。
  const [kbVendorFilter, setKbVendorFilter] = useState("");
  const [kbFetchedAt, setKbFetchedAt] = useState<string | null>(null);
  const [kbFetchedKeyword, setKbFetchedKeyword] = useState("");
  const runNameAutomaticRef = useRef(true);
  // ``load()`` 会被 mount-effect 和 10s 轮询 effect 共用同一个初始引用,
  // 闭包里的 ``kbItems`` 会停在 ``[]``,导致轮询时拿不到最新 KB,
  // 进而把用户已选好的运行名覆盖成"未命名知识库 Top..."。
  // 用 ref 跟踪最新值,``load()`` 内部读 ref 即可拿到当前 KB。
  const kbItemsRef = useRef<KnowledgeBaseItem[]>([]);
  const kbRequestSeqRef = useRef(0);
  const kbAbortRef = useRef<AbortController | null>(null);
  const kbPickerRef = useRef<HTMLDivElement | null>(null);
  const difyConnectionPickerRef = useRef<HTMLDivElement | null>(null);
  const kbSearchInputRef = useRef<HTMLInputElement | null>(null);
  const difyBaseUrlInputRef = useRef<HTMLInputElement | null>(null);
  const difyApiKeyInputRef = useRef<HTMLInputElement | null>(null);
  const targetKnowledgeBaseInputRef = useRef<HTMLInputElement | null>(null);

  // 当前选中的目标知识库（用 form.dataset_id 在 kbItems 里反查）。命名以它为锚。
  const selectedKnowledgeBase = useMemo(
    () => kbItems.find((kb) => kb.dataset_id === form.dataset_id) || null,
    [kbItems, form.dataset_id]
  );
  const selectedDifyConnection = useMemo(
    () => findMatchingDifyConnection(form, difyConnections),
    [form.dify_base_url, form.dify_api_key, difyConnections]
  );

  useEffect(() => {
    if (!kbPickerOpen) return;
    const focusTimer = window.setTimeout(() => kbSearchInputRef.current?.focus(), 0);
    function handlePointerDown(event: MouseEvent) {
      if (!kbPickerRef.current?.contains(event.target as Node)) {
        setKbPickerOpen(false);
      }
    }
    window.addEventListener("mousedown", handlePointerDown);
    return () => {
      window.clearTimeout(focusTimer);
      window.removeEventListener("mousedown", handlePointerDown);
    };
  }, [kbPickerOpen]);

  useEffect(() => {
    if (!difyConnectionPickerOpen) return;
    function handlePointerDown(event: MouseEvent) {
      if (!difyConnectionPickerRef.current?.contains(event.target as Node)) {
        setDifyConnectionPickerOpen(false);
      }
    }
    window.addEventListener("mousedown", handlePointerDown);
    return () => {
      window.removeEventListener("mousedown", handlePointerDown);
    };
  }, [difyConnectionPickerOpen]);

  useEffect(() => {
    const targetKnowledgeBaseInput = targetKnowledgeBaseInputRef.current;
    if (!targetKnowledgeBaseInput) return;
    if (form.dataset_id.trim()) {
      targetKnowledgeBaseInput.setCustomValidity("");
      setKbPickerInvalid(false);
      return;
    }
    targetKnowledgeBaseInput.setCustomValidity("请选择目标知识库");
  }, [form.dataset_id]);

  // 跨页回填：评测集页生成完成后跳过来时，把 eval_file 预填到表单。
  // 一次性：读完后立即清掉 state，避免刷新页面再次触发。
  useEffect(() => {
    if (!pendingState?.pendingDatasetPath) return;
    setForm((current) => ({ ...current, eval_file: pendingState.pendingDatasetPath! }));
    navigate(location.pathname, { replace: true, state: null });
  }, [pendingState, location.pathname, navigate]);

  async function load() {
    try {
      const [datasetResult, runResult] = await Promise.all([listDatasets(), listRuns()]);
      setDatasets(datasetResult.items);
      setRuns(runResult.items);
      setForm((current) => {
        // 如果 state 给了 pendingDatasetPath，本次不自动覆盖（让 useEffect 走）
        if (pendingState?.pendingDatasetPath) return current;
        if (datasetResult.items.length === 0) return current;
        const selected = datasetResult.items.find((dataset) => dataset.path === current.eval_file);
        const nextDataset = selected || datasetResult.items[0];
        // 用 ref 读最新 KB,避免 mount 时闭包把 selectedKnowledgeBase 冻在 null。
        const liveKb =
          kbItemsRef.current.find((kb) => kb.dataset_id === current.dataset_id) || null;
        return {
          ...current,
          eval_file: nextDataset.path,
          name: runNameAutomaticRef.current
            ? defaultRunName(liveKb, current.top_k)
            : current.name
        };
      });
    } catch (err) {
      const e = err as Error & { code?: string; status?: number };
      showErrorToast(describeError(e.code, e.status, err instanceof Error ? err.message : undefined));
    } finally {
      setLoading(false);
    }
  }

  async function loadDifyConnections(options: { applyLatest?: boolean } = {}) {
    setDifyConnectionsLoading(true);
    try {
      const result = await listDifyConnectionConfigs(20);
      setDifyConnections(result.items);
      if (options.applyLatest && result.items.length > 0) {
        const latest = result.items[0];
        setForm((current) => {
          const hasUserEditedConnection =
            Boolean(current.dify_api_key.trim()) ||
            current.dify_base_url.trim() !== defaultForm.dify_base_url;
          if (hasUserEditedConnection) return current;
          return applyDifyConnectionConfig(current, latest);
        });
      }
    } catch {
      // 历史连接是便捷能力，加载失败不阻塞评测台主流程。
    } finally {
      setDifyConnectionsLoading(false);
    }
  }

  useEffect(() => {
    void load();
    void loadDifyConnections({ applyLatest: true });
  }, []);

  useEffect(() => {
    const timer = window.setInterval(() => {
      void load();
    }, 10_000);
    return () => window.clearInterval(timer);
  }, []);

  // API 地址或 Key 改动后不再自动请求；用户点击"加载知识库"或面板内"刷新"时才拉取。
  // 同时撤销旧请求，避免输入过程中的旧超时错误覆盖当前表单。
  useEffect(() => {
    kbRequestSeqRef.current += 1;
    kbAbortRef.current?.abort();
    kbAbortRef.current = null;
    setKbLoading(false);
    setKbItems([]);
    kbItemsRef.current = [];
    setKbVendorFilter("");
    setKbPickerOpen(false);
    setKbFetchedAt(null);
    setKbFetchedKeyword("");
    setForm((current) => {
      if (!current.dataset_id && !current.embedding_model && !current.rerank_model) return current;
      return {
        ...current,
        dataset_id: "",
        embedding_model: "",
        rerank_model: ""
      };
    });
  }, [form.dify_base_url, form.dify_api_key]);

  useEffect(() => {
    if (!pendingDifyConnectionAutoLoad) return;
    const credentials = resolveDifyConnectionCredentials(form, pendingDifyConnectionAutoLoad);
    if (!credentials.difyBaseUrl || !credentials.difyApiKey) {
      setPendingDifyConnectionAutoLoad(null);
      return;
    }
    if (
      form.dify_base_url.trim() !== credentials.difyBaseUrl ||
      form.dify_api_key.trim() !== credentials.difyApiKey
    ) {
      return;
    }
    setPendingDifyConnectionAutoLoad(null);
    void fetchKnowledgeBases({ ...credentials, keyword: "" });
  }, [pendingDifyConnectionAutoLoad, form.dify_base_url, form.dify_api_key]);

  useEffect(() => {
    return () => {
      kbAbortRef.current?.abort();
    };
  }, []);

  // 保存 Dify 连接配置：仅在 Dify 真的响应成功后才调用 —— 也就是拉过知识库
  // 列表 / 真起过评测 —— 才把这一对值写进历史里。失败静默（保存本身不影响主流程）。
  // 去重由后端按 URL + API Key 哈希做；这里只负责把"已验证可用"的那一对值记下来。
  async function persistVerifiedDifyConnection(
    difyBaseUrl: string,
    difyApiKey: string
  ): Promise<DifyConnectionConfigItem | null> {
    if (!difyBaseUrl || !difyApiKey) return null;
    setDifyConnectionSaving(true);
    try {
      const saved = await saveDifyConnectionConfig({
        dify_base_url: difyBaseUrl,
        dify_api_key: difyApiKey
      });
      setDifyConnections((current) => {
        const withoutSaved = current.filter(
          (item) => item.id !== saved.id && !isSameDifyConnection(saved, item)
        );
        return [saved, ...withoutSaved];
      });
      return saved;
    } catch {
      // 历史连接是便捷能力，保存失败不阻塞主流程（评测已成功发起）。
      return null;
    } finally {
      setDifyConnectionSaving(false);
    }
  }

  function handleDifyConnectionSelect(connectionId: string) {
    const selected = difyConnections.find((item) => item.id === connectionId);
    if (!selected) return;
    setError("");
    setKbKeyword("");
    setPendingDifyConnectionAutoLoad(selected);
    setForm((current) => applyDifyConnectionConfig(current, selected));
    setDifyConnectionPickerOpen(false);
  }

  function handleDifyConnectionDeleteRequest(connectionId: string) {
    // mousedown.preventDefault 已经在选项按钮上，避免点 × 时下拉先关闭。
    const target = difyConnections.find((item) => item.id === connectionId);
    if (!target) return;
    setDifyConnectionPendingDelete(target);
  }

  function handleDifyConnectionDeleteCancel() {
    if (difyConnectionDeleting) return;
    setDifyConnectionPendingDelete(null);
  }

  async function handleDifyConnectionDeleteConfirm() {
    const target = difyConnectionPendingDelete;
    if (!target) return;
    setDifyConnectionDeleting(true);
    try {
      await deleteDifyConnectionConfig(target.id);
      setDifyConnections((current) =>
        current.filter((item) => item.id !== target.id)
      );
      // 当前表单正在用这一对值时，让用户感知到历史里少了一条，但不要偷偷清空输入框。
      if (
        form.dify_base_url.trim() === target.dify_base_url.trim() &&
        form.dify_api_key.trim() === target.dify_api_key.trim()
      ) {
        setDifyConnectionPickerOpen(false);
      }
      setDifyConnectionPendingDelete(null);
    } catch (err) {
      const e = err as Error & { code?: string; status?: number };
      showErrorToast(
        describeError(e.code, e.status, err instanceof Error ? err.message : undefined)
      );
    } finally {
      setDifyConnectionDeleting(false);
    }
  }

  async function fetchKnowledgeBases(options: FetchKnowledgeBaseOptions = {}) {
    const difyUrl = (options.difyBaseUrl ?? form.dify_base_url).trim();
    const difyApiKey = (options.difyApiKey ?? form.dify_api_key).trim();
    const keyword = (options.keyword ?? kbKeyword).trim();
    if (!difyUrl) {
      setError("请先填写 Dify API 地址");
      return;
    }
    if (!difyApiKey) {
      setError("请先填写 Dify API Key");
      return;
    }
    const requestSeq = kbRequestSeqRef.current + 1;
    kbRequestSeqRef.current = requestSeq;
    kbAbortRef.current?.abort();
    const controller = new AbortController();
    kbAbortRef.current = controller;
    setKbLoading(true);
    try {
      const result = await listKnowledgeBases({
        dify_base_url: difyUrl,
        dify_api_key: difyApiKey,
        keyword: keyword || undefined,
        limit: 50,
        signal: controller.signal
      });
      if (kbRequestSeqRef.current !== requestSeq) return;
      setKbItems(result.items);
      kbItemsRef.current = result.items;
      setKbFetchedAt(new Date().toISOString());
      setKbFetchedKeyword(keyword);
      // 拉 KB 成功 = Dify 这一对值是可达且通过鉴权的，才标记为当前数据源。
      writeCurrentDifyUrl(difyUrl);
      void persistVerifiedDifyConnection(difyUrl, difyApiKey);
    } catch (err) {
      if (controller.signal.aborted || kbRequestSeqRef.current !== requestSeq) return;
      setKbItems([]);
      kbItemsRef.current = [];
      const e = err as Error & { code?: string; status?: number };
      showErrorToast(describeError(e.code, e.status, err instanceof Error ? err.message : undefined));
    } finally {
      if (kbRequestSeqRef.current === requestSeq) {
        setKbLoading(false);
        if (kbAbortRef.current === controller) {
          kbAbortRef.current = null;
        }
      }
    }
  }

  const insights = useMemo(() => buildHomeInsights(datasets, runs, form.eval_file), [datasets, runs, form.eval_file]);
  const selectedDataset = insights.selectedDataset;
  const selectedReviewStatus = selectedDataset?.review_status || "unreviewed";
  const selectedDatasetNeedsReview = Boolean(selectedDataset && selectedReviewStatus !== "reviewed");
  const selectedDatasetReviewText =
    selectedReviewStatus === "draft"
      ? `该评测集存在待审核草稿${selectedDataset?.draft_path ? `（${selectedDataset.draft_path}）` : ""}，请先完成人工审核。`
      : "该评测集尚未写入人工审核记录，请先打开编辑器确认并标记为已审核。";

  // 客户端排序：按名称升序展示，不依赖所选评测集。
  const sortedKnowledgeBases = useMemo(
    () => sortKnowledgeBases(kbItems),
    [kbItems]
  );

  // 厂商 chip 列表：按当前 KB 聚合（不持久化），按出现次数从高到低、空值排末。
  // 重新拉 KB 时自然跟着变；点中后会通过下方 vendorFilter 过滤出子集。
  const kbVendorOptions = useMemo(() => {
    const counts = new Map<string, number>();
    for (const kb of kbItems) {
      const v = (kb.vendor || "").trim();
      if (!v) continue;
      counts.set(v, (counts.get(v) ?? 0) + 1);
    }
    return Array.from(counts.entries())
      .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
      .map(([vendor, count]) => ({ vendor, count }));
  }, [kbItems]);

  // 下拉面板里的候选项：先按当前评测集相关度排序，再叠加厂商和关键词过滤。
  const filteredKnowledgeBases = useMemo(
    () =>
      sortedKnowledgeBases.filter((kb) => {
        if (kbVendorFilter && (kb.vendor || "").trim() !== kbVendorFilter) return false;
        return knowledgeBaseMatchesKeyword(kb, kbKeyword);
      }),
    [sortedKnowledgeBases, kbVendorFilter, kbKeyword]
  );

  const knowledgeBaseInputsReady =
    Boolean(form.dify_base_url.trim()) && Boolean(form.dify_api_key.trim());
  const kbKeywordPending = Boolean(kbFetchedAt && kbKeyword.trim() !== kbFetchedKeyword);
  const targetKnowledgeBaseSelected = Boolean(form.dataset_id.trim());
  const knowledgeBasePickerTitle = !form.dify_base_url.trim()
    ? "请先填写 Dify API 地址"
    : !form.dify_api_key.trim()
      ? "请先填写 Dify API Key"
      : selectedKnowledgeBase
        ? knowledgeBaseLabel(selectedKnowledgeBase)
        : "请选择目标知识库";
  const runSubmitDisabled = submitting || loading || selectedDatasetNeedsReview;
  const runSubmitTitle = selectedDatasetNeedsReview
    ? "请先完成人工审核"
    : !targetKnowledgeBaseSelected
      ? "请先选择目标知识库"
      : "开始评测";
  const runSubmitLabel = selectedDatasetNeedsReview
    ? "待审核，不能评测"
    : !targetKnowledgeBaseSelected
      ? "请选择目标知识库"
      : submitting
        ? "正在创建评测"
        : "开始评测";

  useEffect(() => {
    setKbHighlightedIndex(0);
  }, [kbKeyword, kbVendorFilter, kbPickerOpen]);

  useEffect(() => {
    setKbHighlightedIndex((current) =>
      filteredKnowledgeBases.length === 0
        ? 0
        : Math.min(current, filteredKnowledgeBases.length - 1)
    );
  }, [filteredKnowledgeBases.length]);

  function reportKnowledgeBasePrerequisite() {
    if (form.dify_base_url.trim() && form.dify_api_key.trim()) return true;
    const input = form.dify_base_url.trim()
      ? difyApiKeyInputRef.current
      : difyBaseUrlInputRef.current;
    input?.setCustomValidity("请填写此字段。");
    input?.focus();
    input?.reportValidity();
    return false;
  }

  function handleKnowledgeBasePickerOpen() {
    if (!reportKnowledgeBasePrerequisite() || kbLoading) return;
    setKbPickerInvalid(false);
    setKbPickerOpen(true);
    if (!kbFetchedAt || kbKeywordPending || kbItems.length === 0) {
      void fetchKnowledgeBases();
    }
  }

  function handleKnowledgeBasePickerToggle() {
    if (!reportKnowledgeBasePrerequisite()) return;
    setKbPickerInvalid(false);
    if (kbPickerOpen) {
      setKbPickerOpen(false);
      return;
    }
    handleKnowledgeBasePickerOpen();
  }

  function handleKnowledgeBaseSelect(kb: KnowledgeBaseItem) {
    // 切 KB 视为一次"系统建议的新名字"，重新打开自动命名。
    runNameAutomaticRef.current = true;
    // embedding / rerank 标签是绑死在每个 KB 上的：选完 KB 后
    // 由知识库服务返回的 Dify 真实配置自动回填，避免手填错字、
    // 对比页分组错乱。
    const nextEmbedding = kb.embedding_model?.trim() ?? "";
    const rerankCfg = kb.retrieval_model_dict?.reranking_model;
    const nextRerank =
      kb.retrieval_model_dict?.reranking_enable === false
        ? "无"
        : rerankCfg?.reranking_model_name?.trim() ?? "";
    setForm((current) => ({
      ...current,
      dataset_id: kb.dataset_id,
      name: defaultRunName(kb, current.top_k),
      embedding_model: nextEmbedding,
      rerank_model: nextRerank
    }));
    setKbPickerOpen(false);
  }

  function handleTargetKnowledgeBaseInvalid(event: InvalidEvent<HTMLInputElement>) {
    event.currentTarget.setCustomValidity("请选择目标知识库");
    setKbPickerInvalid(true);
    kbPickerRef.current?.scrollIntoView({ block: "center", behavior: "smooth" });
  }

  function handleKnowledgeBasePickerKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (!knowledgeBaseInputsReady) {
      if (event.key === "ArrowDown" || event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        reportKnowledgeBasePrerequisite();
      }
      return;
    }
    if (!kbPickerOpen && (event.key === "ArrowDown" || event.key === "Enter" || event.key === " ")) {
      event.preventDefault();
      handleKnowledgeBasePickerOpen();
      return;
    }
    if (!kbPickerOpen) return;
    if (event.key === "Escape") {
      event.preventDefault();
      setKbPickerOpen(false);
      return;
    }
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setKbHighlightedIndex((current) =>
        filteredKnowledgeBases.length === 0
          ? 0
          : Math.min(current + 1, filteredKnowledgeBases.length - 1)
      );
      return;
    }
    if (event.key === "ArrowUp") {
      event.preventDefault();
      setKbHighlightedIndex((current) => Math.max(current - 1, 0));
      return;
    }
    if (event.key === "Enter") {
      const kb = filteredKnowledgeBases[kbHighlightedIndex];
      if (!kb) return;
      event.preventDefault();
      handleKnowledgeBaseSelect(kb);
    }
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (selectedDatasetNeedsReview) {
      setError("评测集尚未通过人工审核，请先在评测集编辑器中标记为已审核。");
      return;
    }
    if (!form.dataset_id.trim()) {
      targetKnowledgeBaseInputRef.current?.setCustomValidity("请选择目标知识库");
      targetKnowledgeBaseInputRef.current?.reportValidity();
      setKbPickerInvalid(true);
      setError("请先手动选择目标知识库，不能留空自动匹配。");
      return;
    }
    setSubmitting(true);
    setError("");
    try {
      const payload = {
        ...form,
        dataset_id: form.dataset_id.trim(),
        name: form.name.trim() || defaultRunName(selectedKnowledgeBase, form.top_k)
      };
      const response = await createRun(payload);
      // 评测被后端受理 = Dify 这一对值能跑通，才标记为当前数据源。
      writeCurrentDifyUrl(payload.dify_base_url.trim());
      void persistVerifiedDifyConnection(
        payload.dify_base_url.trim(),
        payload.dify_api_key.trim()
      );
      // 提交成功后才落盘偏好——只把"用户真的跑过、确认有效"的参数存下来。
      writeRunPreferences({
        top_k: payload.top_k,
        limit: payload.limit,
        timeout_seconds: payload.timeout_seconds
      });
      navigate(`/runs/${response.id}`, {
        state: { from: { pathname: "/", search: location.search } }
      });
    } catch (err) {
      const e = err as Error & { code?: string; status?: number };
      showErrorToast(describeError(e.code, e.status, err instanceof Error ? err.message : undefined));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="dashboard-grid">
      <div className="workspace-stack">
        <section className="panel config-panel">
          <PanelHeader
            icon={<Settings2 size={18} />}
            title="评测配置"
            subtitle="连接 Dify 知识库检索 API，生成本地 reports（报告目录）产物"
          />
          <form className="run-form" onSubmit={handleSubmit}>
            <Field label="运行名称">
              <input
                value={form.name}
                onChange={(event) => {
                  runNameAutomaticRef.current = false;
                  setForm({ ...form, name: event.target.value });
                }}
                placeholder={defaultRunName(selectedKnowledgeBase, form.top_k)}
              />
            </Field>
            <div className="form-row dify-credentials-row">
              <Field label="Dify API 地址">
                <div
                  ref={difyConnectionPickerRef}
                  className={`dify-url-input-row${difyConnectionPickerOpen ? " is-open" : ""}`}
                  data-saving={difyConnectionSaving ? "true" : "false"}
                >
                  <input
                    ref={difyBaseUrlInputRef}
                    required
                    value={form.dify_base_url}
                    title={form.dify_base_url ? `当前 Dify API 地址：${form.dify_base_url}` : "输入或选择 Dify API 地址"}
                    onChange={(event) => {
                      event.currentTarget.setCustomValidity("");
                      setForm({ ...form, dify_base_url: event.target.value });
                    }}
                  />
                  <button
                    type="button"
                    className="dify-url-history-button"
                    onClick={() => setDifyConnectionPickerOpen((open) => !open)}
                    onMouseDown={(event) => event.preventDefault()}
                    disabled={difyConnectionsLoading || difyConnections.length === 0}
                    aria-label="历史连接配置"
                    aria-haspopup="listbox"
                    aria-expanded={difyConnectionPickerOpen}
                    title={
                      difyConnections.length > 0
                        ? "选择历史连接配置"
                        : difyConnectionsLoading
                          ? "正在加载历史连接"
                          : "暂无历史连接"
                    }
                  >
                    {difyConnectionsLoading ? (
                      <Loader2 size={15} className="spin" />
                    ) : (
                      <ChevronDown size={15} />
                    )}
                  </button>
                  {difyConnectionPickerOpen && difyConnections.length > 0 && (
                    <div className="dify-url-history-panel" role="listbox">
                      {difyConnections.map((connection) => {
                        const selected = connection.id === selectedDifyConnection?.id;
                        return (
                          <div
                            key={connection.id}
                            className={`dify-url-history-option${selected ? " is-selected" : ""}`}
                            role="option"
                            aria-selected={selected}
                          >
                            <button
                              type="button"
                              className="dify-url-history-option-pick"
                              onMouseDown={(event) => event.preventDefault()}
                              onClick={() => handleDifyConnectionSelect(connection.id)}
                              title={formatDifyConnectionOption(connection)}
                            >
                              <span>{connection.dify_base_url}</span>
                              <small>{connection.dify_api_key_masked || "****"}</small>
                            </button>
                            <button
                              type="button"
                              className="dify-url-history-option-remove"
                              onMouseDown={(event) => event.preventDefault()}
                              onClick={() => handleDifyConnectionDeleteRequest(connection.id)}
                              aria-label={`删除历史连接 ${connection.dify_base_url}`}
                              title="删除此条历史连接"
                            >
                              <Trash2 size={13} />
                            </button>
                          </div>
                        );
                      })}
                    </div>
                  )}
                </div>
              </Field>
              <Field label="Dify API Key">
                <div className="secret-input-wrapper">
                  <input
                    ref={difyApiKeyInputRef}
                    type={showDifyApiKey ? "text" : "password"}
                    required
                    value={form.dify_api_key}
                    onChange={(event) => {
                      event.currentTarget.setCustomValidity("");
                      setForm({ ...form, dify_api_key: event.target.value });
                    }}
                    placeholder="请输入Dify API Key"
                  />
                  <button
                    type="button"
                    className="secret-input-toggle"
                    onClick={() => setShowDifyApiKey((visible) => !visible)}
                    onMouseDown={(event) => event.preventDefault()}
                    aria-label={showDifyApiKey ? "隐藏 Dify API Key" : "显示 Dify API Key"}
                    title={showDifyApiKey ? "隐藏 Dify API Key" : "显示 Dify API Key"}
                  >
                    {showDifyApiKey ? <EyeOff size={16} /> : <Eye size={16} />}
                  </button>
                </div>
              </Field>
              <div className="dify-confirm-slot">
                <span aria-hidden="true">&nbsp;</span>
                <button
                  type="button"
                  className="ghost-button dify-confirm-button"
                  formNoValidate
                  onClick={() => {
                    if (!reportKnowledgeBasePrerequisite()) return;
                    void fetchKnowledgeBases();
                  }}
                  disabled={kbLoading}
                  title="使用当前 Dify 地址和 API Key 拉取知识库列表"
                >
                  {kbLoading ? <Loader2 size={16} className="spin" /> : <RefreshCw size={16} />}
                  {kbLoading ? "加载中" : "加载知识库"}
                </button>
              </div>
            </div>
            <div className="form-row">
              <Field label="评测集">
                <StandardSelect
                  value={form.eval_file}
                  title={selectedDataset ? `当前评测集：${selectedDataset.name}` : "请选择评测集"}
                  onChange={(event) => {
                    const evalFile = event.target.value;
                    const dataset = datasets.find((item) => item.path === evalFile) || null;
                    runNameAutomaticRef.current = true;
                    setForm({
                      ...form,
                      eval_file: evalFile,
                      name: defaultRunName(selectedKnowledgeBase, form.top_k)
                    });
                  }}
                >
                  {datasets.map((dataset) => (
                    <option key={dataset.id} value={dataset.path}>
                      {dataset.name}
                    </option>
                  ))}
                </StandardSelect>
                {selectedDataset && selectedDataset.review_status === "reviewed" && (
                  <span className="ui-badge review-hint review-hint-inline" data-status="reviewed">
                    ✓ 已审核 · {formatReviewTime(selectedDataset.reviewed_at)}
                  </span>
                )}
              </Field>
              <Field label="目标知识库">
                <div
                  className={`kb-picker${kbPickerOpen ? " is-open" : ""}${kbPickerInvalid ? " is-invalid" : ""}`}
                  ref={kbPickerRef}
                  onKeyDown={handleKnowledgeBasePickerKeyDown}
                >
                  <div className="kb-picker-control-row">
                    <input
                      ref={targetKnowledgeBaseInputRef}
                      className="kb-picker-required-input"
                      required
                      name="dataset_id"
                      value={form.dataset_id}
                      onChange={() => undefined}
                      onInvalid={handleTargetKnowledgeBaseInvalid}
                      aria-label="目标知识库"
                    />
                    <button
                      type="button"
                      className="kb-picker-trigger"
                      aria-haspopup="listbox"
                      aria-expanded={kbPickerOpen}
                      aria-controls="kb-picker-options"
                      aria-disabled={!knowledgeBaseInputsReady}
                      onClick={handleKnowledgeBasePickerToggle}
                      disabled={kbLoading}
                      title={knowledgeBasePickerTitle}
                    >
                      <span className={`kb-picker-trigger-text${selectedKnowledgeBase ? "" : " is-placeholder"}`}>
                        {selectedKnowledgeBase
                          ? knowledgeBaseLabel(selectedKnowledgeBase)
                          : kbLoading
                            ? "正在加载知识库..."
                            : "请选择目标知识库"}
                      </span>
                      <span className="kb-picker-trigger-arrow" aria-hidden="true">
                        {kbLoading ? <Loader2 size={14} className="spin" /> : <ChevronDown size={15} />}
                      </span>
                    </button>
                  </div>
                  {kbPickerOpen && (
                    <div className="kb-picker-panel">
                      <div className="kb-picker-search-row">
                        <Search size={15} />
                        <input
                          ref={kbSearchInputRef}
                          className="kb-picker-keyword"
                          value={kbKeyword}
                          onChange={(event) => setKbKeyword(event.target.value)}
                          placeholder="搜索名称 / 厂商 / 型号 / 编号"
                          disabled={kbLoading}
                          aria-label="搜索目标知识库"
                        />
                        <button
                          type="button"
                          className="kb-picker-panel-action"
                          onClick={() => void fetchKnowledgeBases()}
                          disabled={kbLoading}
                        >
                          {kbLoading ? <Loader2 size={13} className="spin" /> : <RefreshCw size={13} />}
                          {kbKeywordPending ? "搜索" : "刷新"}
                        </button>
                      </div>
                      {kbVendorOptions.length > 0 && (
                        <div
                          className="kb-vendor-chips"
                          role="toolbar"
                          aria-label="按厂商过滤知识库"
                        >
                          <button
                            type="button"
                            className={`kb-vendor-chip${kbVendorFilter === "" ? " is-active" : ""}`}
                            onClick={() => setKbVendorFilter("")}
                          >
                            全部
                          </button>
                          {kbVendorOptions.map(({ vendor, count }) => (
                            <button
                              key={vendor}
                              type="button"
                              className={`kb-vendor-chip${kbVendorFilter === vendor ? " is-active" : ""}`}
                              onClick={() => setKbVendorFilter(vendor)}
                              title={`仅显示 ${vendor}（共 ${count} 个）`}
                            >
                              {vendor}
                              <span className="kb-vendor-chip-count">{count}</span>
                            </button>
                          ))}
                        </div>
                      )}
                      <div className="kb-picker-result-meta">
                        <span>
                          {filteredKnowledgeBases.length} 个匹配 · 共 {kbItems.length} 个知识库
                        </span>
                        {kbFetchedAt && <span>最后更新 {new Date(kbFetchedAt).toLocaleTimeString()}</span>}
                      </div>
                      <div id="kb-picker-options" className="kb-picker-options" role="listbox">
                        {kbLoading ? (
                          <div className="kb-picker-empty">正在拉取知识库…</div>
                        ) : filteredKnowledgeBases.length === 0 ? (
                          <div className="kb-picker-empty">没有匹配的知识库，请调整搜索条件</div>
                        ) : (
                          filteredKnowledgeBases.map((kb, index) => {
                            const selected = kb.dataset_id === form.dataset_id;
                            const highlighted = index === kbHighlightedIndex;
                            const rerankName =
                              kb.retrieval_model_dict?.reranking_enable === false
                                ? "无 Rerank"
                                : kb.retrieval_model_dict?.reranking_model?.reranking_model_name || "";
                            return (
                              <button
                                key={kb.dataset_id}
                                type="button"
                                role="option"
                                aria-selected={selected}
                                className={`kb-picker-option${selected ? " is-selected" : ""}${
                                  highlighted ? " is-highlighted" : ""
                                }`}
                                onMouseEnter={() => setKbHighlightedIndex(index)}
                                onClick={() => handleKnowledgeBaseSelect(kb)}
                                title={kb.dataset_id}
                              >
                                <span className="kb-picker-option-main">
                                  <span className="kb-picker-option-title">
                                    {selected && <Check size={14} />}
                                    <strong>{knowledgeBaseLabel(kb)}</strong>
                                  </span>
                                  <span className="kb-picker-option-tags">
                                    {kb.vendor && <span>{kb.vendor}</span>}
                                    {kb.model && <span>{kb.model}</span>}
                                    {kb.embedding_model && <span>{kb.embedding_model}</span>}
                                    {rerankName && <span>{rerankName}</span>}
                                  </span>
                                </span>
                              </button>
                            );
                          })
                        )}
                      </div>
                    </div>
                  )}
                  <div className="kb-picker-status" data-state={kbLoading ? "loading" : "idle"}>
                    {!form.dify_base_url.trim() ? (
                      <span>填写 API 地址后点击加载知识库</span>
                    ) : !form.dify_api_key.trim() ? (
                      <span>填写 Dify API Key 后点击加载知识库</span>
                    ) : kbLoading ? (
                      <span>正在拉取知识库…</span>
                    ) : kbKeywordPending ? (
                      <span>搜索条件已修改，可在下拉面板内点击搜索同步远端列表</span>
                    ) : kbFetchedAt && filteredKnowledgeBases.length === 0 ? (
                      <span>没有匹配的知识库，请调整搜索条件后重新加载</span>
                    ) : kbFetchedAt ? (
                      <span>
                        共 {kbItems.length} 条 · {kbVendorFilter ? `厂商=${kbVendorFilter}` : "全厂商"} ·
                        {kbFetchedKeyword ? " 已搜索" : ""} · 最后更新{" "}
                        {new Date(kbFetchedAt).toLocaleTimeString()}
                      </span>
                    ) : (
                      <span>点击加载知识库，或打开下拉自动加载列表</span>
                    )}
                  </div>
                </div>
              </Field>
            </div>
            {/* 对比分析标签：实际由所选 Dify 知识库绑定的配置决定，Dify-KB-Eval 只是
                给对比页分组用。"选完 KB"后两个输入自动回填、置为只读，避免
                手填错字导致对比页分组错乱；选 KB 前禁用 + 占位文字。
                选完 KB 后字段仍为空 → 该 KB 在 Dify 端没返回 embedding/检索
                信息，常见于旧代理链路或 Dify indexing_technique=economy，
                此时给红字提示，避免用户以为是 bug。 */}
            <div className="form-row kb-model-fields-row">
              <Field label="Embedding 模型（仅对比标签）">
                <input
                  value={form.embedding_model}
                  readOnly={Boolean(form.dataset_id)}
                  disabled={!form.dataset_id}
                  placeholder={form.dataset_id ? "由所选知识库决定" : "请先选择目标知识库"}
                  title="由所选知识库自动决定；如需变更，请在对应知识库服务端调整该知识库配置。"
                />
                {form.dataset_id && !form.embedding_model.trim() && (
                  <div className="warning-line field-warning">
                    ⚠️ 该知识库未返回 embedding 信息，请检查知识库 indexing_technique。
                  </div>
                )}
              </Field>
              <Field label="Rerank 模型（仅对比标签）">
                <input
                  value={form.rerank_model}
                  readOnly={Boolean(form.dataset_id)}
                  disabled={!form.dataset_id}
                  placeholder={form.dataset_id ? "由所选知识库决定" : "请先选择目标知识库"}
                  title="由所选知识库自动决定；Dify 未启用 rerank 时显示「无」。"
                />
                {form.dataset_id && !form.rerank_model.trim() && (
                  <div className="warning-line field-warning">
                    ⚠️ 该知识库未返回 rerank 信息，请检查 Dify 知识库 retrieval 配置。
                  </div>
                )}
              </Field>
            </div>
            {selectedDatasetNeedsReview && selectedDataset && (
              <div className="review-gate-callout" data-status={selectedReviewStatus}>
                <div>
                  <strong>需要人工审核</strong>
                  <span>{selectedDatasetReviewText}</span>
                </div>
                <Link
                  className="ghost-button inline"
                  to={`/datasets/${encodeURIComponent(selectedDataset.path)}/editor`}
                >
                  去审核
                </Link>
              </div>
            )}
            {selectedDataset && selectedDataset.review_status === "draft" && !selectedDatasetNeedsReview && (
              <div className="warning-line field-warning">
                该评测集存在未审核草稿（{selectedDataset.draft_path}）。提交时会再次询问是否继续。
              </div>
            )}
            <div className="form-row three">
              <Field label="Top K（召回条数）">
                <StandardSelect
                  value={form.top_k}
                  title={`当前 Top K：${form.top_k}`}
                  onChange={(event) => {
                    const topK = Number(event.target.value);
                    setForm({
                      ...form,
                      top_k: topK,
                      name: runNameAutomaticRef.current
                        ? defaultRunName(selectedKnowledgeBase, topK)
                        : form.name
                    });
                  }}
                >
                  {[1, 3, 5, 10, 20].map((value) => (
                    <option key={value} value={value}>
                      {value}
                    </option>
                  ))}
                </StandardSelect>
              </Field>
              <Field label="样本上限">
                <input
                  type="number"
                  min={0}
                  value={form.limit}
                  onChange={(event) => setForm({ ...form, limit: Number(event.target.value) })}
                />
              </Field>
              <Field label="超时秒数">
                <input
                  type="number"
                  min={5}
                  value={form.timeout_seconds}
                  onChange={(event) => setForm({ ...form, timeout_seconds: Number(event.target.value) })}
                />
              </Field>
            </div>
            {/* TEMP-HIDDEN: 纳入同义问法 / 同步 LangSmith 暂时不开放给前端
                字段保留在 form state 与提交载荷中（默认 false / ""），需要恢复时取消下方注释。
            <div className="switch-row">
              <label className="switch-item">
                <input
                  type="checkbox"
                  checked={form.include_alternatives}
                  onChange={(event) => setForm({ ...form, include_alternatives: event.target.checked })}
                />
                <span>纳入同义问法</span>
              </label>
              <label className="switch-item">
                <input
                  type="checkbox"
                  checked={form.langsmith_enabled}
                  onChange={(event) => setForm({ ...form, langsmith_enabled: event.target.checked })}
                />
                <span>同步 LangSmith（链路追踪）</span>
              </label>
            </div>
            {form.langsmith_enabled && (
              <Field label="LangSmith Project（项目名）">
                <input
                  value={form.langsmith_project}
                  onChange={(event) => setForm({ ...form, langsmith_project: event.target.value })}
                />
              </Field>
            )}
            */}
            {error && <div className="error-line">{error}</div>}
            <button
              className="primary-button"
              type="submit"
              disabled={runSubmitDisabled}
              title={runSubmitTitle}
            >
              {submitting ? <Loader2 size={18} className="spin" /> : <Play size={18} />}
              {runSubmitLabel}
            </button>
          </form>
          {difyConnectionPendingDelete && (
            <ConfirmDialog
              tone="danger"
              title="删除历史连接配置"
              message={
                `确认要从历史连接列表里删除 “${difyConnectionPendingDelete.dify_base_url}” 这一条吗？\n` +
                `删除后该 API Key 将从历史下拉里消失，再次使用需重新连接并通过验证。`
              }
              confirmText={difyConnectionDeleting ? "正在删除..." : "确认删除"}
              cancelText="取消"
              onCancel={handleDifyConnectionDeleteCancel}
              onConfirm={handleDifyConnectionDeleteConfirm}
            />
          )}
        </section>
      </div>

      <div className="workspace-stack">
        <HomeInsightsPanel insights={insights} loading={loading} />
      </div>
    </div>
  );
}

function HomeInsightsPanel({ insights, loading }: { insights: HomeInsights; loading: boolean }) {
  const latest = insights.latestRun;
  const latestCompleted = insights.latestCompletedRun;
  const latestRecallMetric = latestCompleted ? getRunHistoryRecallMetric(latestCompleted) : undefined;
  const latestRecall = latestRecallMetric?.value;
  const latestRecallLabel = latestRecallMetric?.axis === "document" ? "Document Recall" : "Content Recall";
  const latestRecallDescription = latestRecallMetric?.axis === "document" ? "文档召回率" : "内容召回率";
  const latestResultPrefix = latestCompleted?.id === latest?.id ? "本次结果" : "上次完成";
  const latestResultText = latestRecallMetric
    ? `${latestResultPrefix} ${latestRecallLabel}@${latestRecallMetric.k}（${latestRecallDescription}）`
    : `${latestResultPrefix} 暂无召回指标`;
  const selected = insights.selectedDataset;

  return (
    <section className="panel home-insight-panel">
      <PanelHeader
        icon={<Activity size={18} />}
        title="运行概览"
        subtitle="桌面工作流状态与最近结果"
        action={
          <Link className="ghost-link" to="/runs">
            历史评测 <ArrowRight size={14} />
          </Link>
        }
      />
      {loading ? (
        <div className="empty-state compact">
          <Loader2 size={20} className="spin" />
          正在加载运行状态...
        </div>
      ) : (
        <>
          <div className="home-insight-grid">
            <div>
              <strong>{insights.runningCount}</strong>
              <span>排队/运行中</span>
            </div>
            <div>
              <strong className={insights.failedCount > 0 ? "bad" : "good"}>{insights.failedCount}</strong>
              <span>失败运行</span>
            </div>
            <div>
              <strong className={insights.blockedByReview ? "warn" : "good"}>
                {insights.blockedByReview ? "待审" : "可评测"}
              </strong>
              <span>当前评测集</span>
            </div>
          </div>

          <div className="home-current-dataset">
            <div className="home-current-dataset-head">
              {insights.blockedByReview ? <TriangleAlert size={16} /> : <ShieldCheck size={16} />}
              <strong>{selected?.name || "暂无评测集"}</strong>
            </div>
            <span>
              {selected
                ? `${selected.vendor || "未知厂商"} / ${selected.model || "未知型号"} · ${selected.sample_count} 条样本`
                : "请先在评测集页面生成或导入评测集"}
            </span>
          </div>

          <div className="home-latest-run">
            <div className="home-latest-run-head">
              <strong>最近运行</strong>
              {latest && <StatusBadge status={latest.status} />}
            </div>
            {latest ? (
              <Link
                to={`/runs/${latest.id}`}
                state={{ from: { pathname: "/", search: location.search } }}
                className="home-run-link"
              >
                <span>
                  <b>{latest.name}</b>
                  <small>{latest.id}</small>
                </span>
                <ArrowRight size={16} />
              </Link>
            ) : (
              <div className="empty-state compact">暂无运行记录</div>
            )}

            {latestCompleted && (
              <div className="home-run-result">
                <div>
                  <span>{latestResultText}</span>
                  <strong className={metricTone(latestRecall)}>{formatPercent(latestRecall)}</strong>
                </div>
                <Link
                  to={`/runs/${latestCompleted.id}`}
                  state={{ from: { pathname: "/", search: location.search } }}
                  className="ghost-link"
                >
                  查看报告 <ArrowRight size={14} />
                </Link>
              </div>
            )}
          </div>
        </>
      )}
    </section>
  );
}
