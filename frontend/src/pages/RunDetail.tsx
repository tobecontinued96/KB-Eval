import { ReactNode, useEffect, useMemo, useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  ArrowLeft,
  BarChart3,
  CheckCircle2,
  Code as CodeIcon,
  Download,
  ExternalLink,
  FileDown,
  FileText,
  Loader2,
  Pencil,
  RefreshCw,
  Search,
  TableProperties,
  Trash2,
  TriangleAlert,
  X
} from "lucide-react";
import { deleteRun, downloadArtifact, getReport, getRun, renameRun, updateRunLabels } from "../api";
import type { EvalRunDetail, EvalRunMetrics, RetrievalResult } from "../types";
import { formatDateTime, formatPercent, isTerminalRun, metricTone } from "../utils";
import { DeleteDatasetDialog } from "../widgets/DeleteDatasetDialog";
import { DeleteSuccessToast } from "../widgets/DeleteSuccessToast";
import { showErrorToast } from "../widgets/ErrorToast";
import { DatalistInput } from "../widgets/StandardSelect";
import { StatusBadge } from "../widgets/StatusBadge";
import { useRunProgressStream, type RunProgressEvent } from "../widgets/runProgressStream";
import { reportMarkdownComponents } from "./reportMarkdown";

type DetailTab = "failures" | "retrieval" | "report" | "scenario" | "artifacts";
const DEFAULT_METRIC_K = 5;
const EMBEDDING_SUGGESTIONS = [
  "text-embedding-v1",
  "text-embedding-v2",
  "text-embedding-v3",
  "text-embedding-v4",
  "bge-large-zh",
  "bge-m3",
  "m3e"
];
const RERANK_SUGGESTIONS = [
  "qwen3-rerank",
  "bge-reranker-base",
  "bge-reranker-large",
  "cohere-rerank",
  "无"
];

function displayMetricK(run: EvalRunDetail) {
  const configuredK = run.config.top_k || run.top_k || DEFAULT_METRIC_K;
  return Math.max(1, Math.min(DEFAULT_METRIC_K, configuredK));
}

function finiteMetric(value: number | undefined) {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

function rankedMetric(
  metrics: EvalRunMetrics,
  axis: "content" | "document" | "section" | "keyword",
  kind: "recall" | "precision" | "ndcg",
  targetK: number
) {
  const direct = finiteMetric(metrics[`${axis}_${kind}@${targetK}`]);
  if (direct !== undefined) return direct;

  const candidates = Object.entries(metrics)
    .map(([key, value]) => {
      const match = key.match(new RegExp(`^${axis}_${kind}@(\\d+)$`));
      return match && typeof value === "number" && Number.isFinite(value)
        ? { k: Number(match[1]), value }
        : null;
    })
    .filter((item): item is { k: number; value: number } => item !== null)
    .sort((left, right) => left.k - right.k);

  const belowTarget = [...candidates].reverse().find((item) => item.k <= targetK);
  return belowTarget?.value ?? candidates[0]?.value;
}

export function RunDetail() {
  const { runId = "" } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  const fromLocation = (location.state as { from?: { pathname: string; search?: string } } | null)?.from;
  const backTarget = fromLocation?.pathname
    ? `${fromLocation.pathname}${fromLocation.search ?? ""}`
    : "/runs";
  const backLabel = backTarget.startsWith("/compare")
    ? "返回分析对比"
    : backTarget === "/"
      ? "返回评测台"
      : "返回历史评测";
  const [run, setRun] = useState<EvalRunDetail | null>(null);
  const [report, setReport] = useState("");
  const [activeTab, setActiveTab] = useState<DetailTab>("failures");
  const [loading, setLoading] = useState(true);
  const [reportLoading, setReportLoading] = useState(false);
  const [isDeleting, setIsDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState("");
  const [deleteSuccess, setDeleteSuccess] = useState<{ name: string; id: string } | null>(null);
  // inline 改名：铅笔点开 → 编辑模式 → 保存 / 取消
  const [isRenaming, setIsRenaming] = useState(false);
  const [renameDraft, setRenameDraft] = useState("");
  const [renameError, setRenameError] = useState("");
  const [renameSubmitting, setRenameSubmitting] = useState(false);
  const [renameSuccessAt, setRenameSuccessAt] = useState<string | null>(null);

  // inline 改模型标签：和改名一样走"铅笔点开 → 表单 → 保存/取消"，
  // 两个字段（embedding / rerank）一起编辑，避免出现"改了 embedding
  // 还没决定 rerank"的不一致中间态。
  const [isEditingLabels, setIsEditingLabels] = useState(false);
  const [labelDraftEmbedding, setLabelDraftEmbedding] = useState("");
  const [labelDraftRerank, setLabelDraftRerank] = useState("");
  const [labelError, setLabelError] = useState("");
  const [labelSubmitting, setLabelSubmitting] = useState(false);
  const [labelSuccessAt, setLabelSuccessAt] = useState<string | null>(null);

  async function loadRun() {
    if (!runId) return;
    try {
      const detail = await getRun(runId);
      setRun(detail);
    } catch (err) {
      const e = err as Error & { status?: number; code?: string };
      if (e.status === 404) {
        // 资源已被删除（或后端重置），直接清空本地状态，渲染层会切到"运行不存在"占位
        setRun(null);
      } else {
        // Page-banner → toast. The local 404 message above stays as
        // inline guidance for the empty-state UI.
        showErrorToast({ title: e.message || "运行详情加载失败", code: "unknown", status: e.status });
      }
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    setLoading(true);
    void loadRun();
  }, [runId]);

  // SSE-driven progress updates: one long-lived EventSource per
  // tab instead of a 2 s poll. The hook falls back to ``getRun``
  // polling every 5 s if SSE can't reconnect for >5 s (dev proxy
  // bounce, server restart, etc.).
  //
  // We pass ``disabled`` based on the local run status; the hook
  // itself is called unconditionally at the top level so React's
  // rules-of-hooks are satisfied, and the hook short-circuits to
  // a no-op when ``disabled`` is true.
  const sseDisabled =
    !run ||
    deleteSuccess !== null ||
    (run.status !== "queued" && run.status !== "running");
  useRunProgressStream(runId, {
    disabled: sseDisabled,
    onUpdate: (event: RunProgressEvent, currentRun: EvalRunDetail | null) => {
      const shouldReloadDetail =
        event.type === "status" && event.status !== undefined && isTerminalRun(event.status);
      // Merge the pushed event into the local run state. We
      // prefer the most recent snapshot the hook has seen
      // (``currentRun``) as the base so non-progress fields
      // (``metrics``, ``failed_samples``, etc.) don't get wiped
      // by a partial SSE frame.
      setRun((prev) => {
        const base = prev ?? currentRun;
        if (!base) return prev;
        if (event.type === "status") {
          return {
            ...base,
            status: (event.status ?? base.status) as EvalRunDetail["status"],
            finished_at: event.finished_at ?? base.finished_at,
            error: event.error ?? base.error,
          };
        }
        // snapshot / progress: update the progress blob + status
        // (status may transition queued → running on snapshot).
        const nextProgress = {
          total_queries: event.total_queries ?? base.progress.total_queries,
          completed_queries:
            event.completed_queries ?? base.progress.completed_queries,
          error_queries: event.error_queries ?? base.progress.error_queries,
          current_sample_id:
            event.current_sample_id !== undefined
              ? event.current_sample_id
              : base.progress.current_sample_id,
          last_heartbeat_at:
            event.last_heartbeat_at !== undefined
              ? event.last_heartbeat_at
              : base.progress.last_heartbeat_at,
        };
        return {
          ...base,
          status:
            (event.status as EvalRunDetail["status"] | undefined) ??
            base.status,
          progress: nextProgress,
        };
      });
      if (shouldReloadDetail) {
        void loadRun();
      }
    },
  });

  useEffect(() => {
    if (!run || activeTab !== "report" || report) return;
    setReportLoading(true);
    getReport(run.id)
      .then((result) => setReport(result.content))
      .catch((err) =>
        showErrorToast({
          title: err instanceof Error ? err.message : "报告加载失败",
          code: "unknown",
        })
      )
      .finally(() => setReportLoading(false));
  }, [activeTab, report, run]);

  useEffect(() => {
    if (!deleteSuccess) return;
    const timer = window.setTimeout(() => setDeleteSuccess(null), 1500);
    return () => window.clearTimeout(timer);
  }, [deleteSuccess]);

  useEffect(() => {
    // 路由切到另一条 run 时，退出编辑态、清草稿，避免上一个 run 的草稿串到这个 run
    setIsRenaming(false);
    setRenameDraft("");
    setRenameError("");
    setIsEditingLabels(false);
    setLabelDraftEmbedding("");
    setLabelDraftRerank("");
    setLabelError("");
  }, [runId]);

  const progress = useMemo(() => {
    if (!run) return 0;
    const total = run.progress.total_queries || run.query_count || 1;
    return Math.min(100, Math.round((run.progress.completed_queries / total) * 100));
  }, [run]);

  const runInFlight = run ? run.status === "running" || run.status === "queued" : false;

  function handleRequestDelete() {
    if (!run) return;
    setDeleteError("");
    setIsDeleting(true);
  }

  function handleCancelDelete() {
    if (!isDeleting) return;
    setDeleteError("");
    setIsDeleting(false);
  }

  async function handleConfirmDelete() {
    if (!run) return;
    setDeleteError("");
    try {
      await deleteRun(run.id);
      // 删除成功：弹 toast 并立刻返回列表页 —— 不再 600ms 等待，
      // 避免在窗口期内 2s 轮询把已删除的 run_id 再请求一次产生 404。
      setDeleteSuccess({ name: run.name, id: run.id });
      setIsDeleting(false);
      navigate(backTarget);
    } catch (err) {
      const e = err as Error & { status?: number };
      // 如果服务端说 404（很可能这条 run 已经被并发请求删掉了），按"成功"处理，跳转走
      if (e.status === 404) {
        setDeleteSuccess({ name: run.name, id: run.id });
        setIsDeleting(false);
        navigate(backTarget);
        return;
      }
      setDeleteError(e.message || "删除失败");
    }
  }

  function handleStartRename() {
    if (!run) return;
    setRenameDraft(run.name);
    setRenameError("");
    setIsRenaming(true);
  }

  function handleCancelRename() {
    setIsRenaming(false);
    setRenameDraft("");
    setRenameError("");
  }

  async function handleSubmitRename() {
    if (!run) return;
    const next = renameDraft.trim();
    if (!next) {
      setRenameError("运行名称不能为空");
      return;
    }
    if (next === run.name) {
      // 没改，直接退出编辑态
      handleCancelRename();
      return;
    }
    setRenameSubmitting(true);
    setRenameError("");
    try {
      const result = await renameRun(run.id, next);
      setRun({ ...run, name: result.name });
      setRenameSuccessAt(result.updated_at ?? new Date().toISOString());
      setIsRenaming(false);
      setRenameDraft("");
    } catch (err) {
      const e = err as Error & { status?: number };
      if (e.status === 404) {
        // 并发删除：当作"成功"提示，让用户跳回列表
        setRenameError("该运行已被删除");
        return;
      }
      setRenameError(e.message || "重命名失败");
    } finally {
      setRenameSubmitting(false);
    }
  }

  function handleStartEditLabels() {
    if (!run) return;
    setLabelDraftEmbedding(run.embedding_model ?? "");
    setLabelDraftRerank(run.rerank_model ?? "");
    setLabelError("");
    setIsEditingLabels(true);
  }

  function handleCancelEditLabels() {
    setIsEditingLabels(false);
    setLabelDraftEmbedding("");
    setLabelDraftRerank("");
    setLabelError("");
  }

  async function handleSubmitLabels() {
    if (!run) return;
    // 空串 / 全空白 → null，与后端 store 归一化保持一致
    const toNullable = (value: string) => {
      const cleaned = value.trim();
      return cleaned ? cleaned : null;
    };
    const nextEmbedding = toNullable(labelDraftEmbedding);
    const nextRerank = toNullable(labelDraftRerank);
    const currentEmbedding = run.embedding_model ?? null;
    const currentRerank = run.rerank_model ?? null;
    if (nextEmbedding === currentEmbedding && nextRerank === currentRerank) {
      handleCancelEditLabels();
      return;
    }
    setLabelSubmitting(true);
    setLabelError("");
    try {
      const result = await updateRunLabels(run.id, {
        embedding_model: nextEmbedding,
        rerank_model: nextRerank
      });
      setRun({
        ...run,
        embedding_model: result.embedding_model ?? null,
        rerank_model: result.rerank_model ?? null
      });
      setLabelSuccessAt(result.updated_at ?? new Date().toISOString());
      setIsEditingLabels(false);
      setLabelDraftEmbedding("");
      setLabelDraftRerank("");
    } catch (err) {
      const e = err as Error & { status?: number };
      if (e.status === 404) {
        setLabelError("该运行已被删除");
        return;
      }
      setLabelError(e.message || "保存标签失败");
    } finally {
      setLabelSubmitting(false);
    }
  }

  if (loading) {
    return (
      <div className="detail-loading">
        <Loader2 className="spin" size={26} />
        正在加载运行详情...
      </div>
    );
  }

  if (!run) {
    return (
      <div className="panel">
        <div className="error-line">运行不存在</div>
        <div className="detail-actions">
          <Link className="ghost-link" to={backTarget}>
            <ArrowLeft size={16} /> {backLabel}
          </Link>
          <Link className="ghost-link" to="/">
            <ArrowLeft size={16} /> 返回评测台
          </Link>
        </div>
      </div>
    );
  }

  const metricK = displayMetricK(run);
  const overall = run.summary.overall;
  const contentRecall =
    rankedMetric(overall, "content", "recall", metricK) ??
    rankedMetric(overall, "document", "recall", metricK);
  const contentPrecision = rankedMetric(overall, "content", "precision", metricK);
  const contentNdcg = rankedMetric(overall, "content", "ndcg", metricK);
  const contentMrr = overall.content_mrr ?? overall.document_mrr;

  return (
    <div className="detail-page">
      <section className="detail-head panel">
        <div className="detail-title">
          <Link className="icon-button" to={backTarget} aria-label={backLabel} title={backLabel}>
            <ArrowLeft size={18} />
          </Link>
          <div className="detail-title-block">
            <div className="detail-title-row">
              {isRenaming ? (
                <form
                  className="detail-rename-form"
                  onSubmit={(event) => {
                    event.preventDefault();
                    void handleSubmitRename();
                  }}
                >
                  <input
                    className="detail-rename-input"
                    autoFocus
                    value={renameDraft}
                    maxLength={255}
                    disabled={renameSubmitting}
                    onChange={(event) => setRenameDraft(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key === "Escape") {
                        event.preventDefault();
                        handleCancelRename();
                      }
                    }}
                    aria-label="运行名称"
                  />
                  <button
                    className="ghost-button"
                    type="submit"
                    disabled={renameSubmitting || !renameDraft.trim()}
                    title="保存（Enter）"
                  >
                    {renameSubmitting ? <Loader2 className="spin" size={14} /> : "保存"}
                  </button>
                  <button
                    className="icon-button"
                    type="button"
                    onClick={handleCancelRename}
                    disabled={renameSubmitting}
                    title="取消（Esc）"
                    aria-label="取消"
                  >
                    <X size={16} />
                  </button>
                </form>
              ) : (
                <>
                  <h2>{run.name}</h2>
                  <button
                    className="icon-button"
                    type="button"
                    onClick={handleStartRename}
                    title="修改名称"
                    aria-label="修改名称"
                  >
                    <Pencil size={16} />
                  </button>
                </>
              )}
              <StatusBadge status={run.status} />
            </div>
            {renameError && <div className="error-line">{renameError}</div>}
            <p title={`运行 ID：${run.id}`}>
              创建于 {formatDateTime(run.created_at)}
              {run.finished_at ? ` · 完成于 ${formatDateTime(run.finished_at)}` : ""}
              {renameSuccessAt ? ` · 改名于 ${formatDateTime(renameSuccessAt)}` : ""}
              {labelSuccessAt ? ` · 标签修改于 ${formatDateTime(labelSuccessAt)}` : ""}
            </p>
          </div>
        </div>
        <div className="detail-actions">
          {run.langsmith_url && (
            <a className="ghost-button" href={run.langsmith_url} target="_blank" rel="noreferrer">
              <ExternalLink size={16} />
              LangSmith（链路追踪）
            </a>
          )}
          <button className="ghost-button" type="button" onClick={() => void loadRun()}>
            <RefreshCw size={16} />
            刷新
          </button>
          <button
            className="ghost-button danger"
            type="button"
            onClick={handleRequestDelete}
            disabled={runInFlight}
            title={runInFlight ? "运行结束后才能删除" : "删除这次运行（需在弹窗中再次确认）"}
          >
            <Trash2 size={16} />
            删除
          </button>
        </div>
      </section>

      {deleteError && <div className="error-line">{deleteError}</div>}

      <section className="metrics-grid">
        <MetricCard title={`Content Recall@${metricK}（内容召回率）`} value={formatPercent(contentRecall)} tone={metricTone(contentRecall)} />
        <MetricCard title={`Content Precision@${metricK}（前${metricK}正确率）`} value={formatPercent(contentPrecision)} tone={metricTone(contentPrecision)} />
        <MetricCard title={`Content NDCG@${metricK}（排序质量）`} value={contentNdcg?.toFixed(3) || "--"} tone={metricTone(contentNdcg)} />
        <MetricCard title="Content MRR（内容平均倒数排名）" value={contentMrr?.toFixed(3) || "--"} tone={metricTone(contentMrr)} />
        <MetricCard title="空结果率" value={formatPercent(overall.empty_result_rate)} tone={overall.empty_result_rate && overall.empty_result_rate > 0.03 ? "bad" : "good"} />
        <MetricCard title="平均耗时" value={`${overall.avg_latency_ms || "--"} ms`} tone="neutral" />
        <MetricCard title="错误数" value={String(run.progress.error_queries || overall.error_queries || 0)} tone={(run.progress.error_queries || 0) > 0 ? "bad" : "good"} />
      </section>

      <section className="panel progress-panel">
        <div className="run-config-strip">
          <span>Top K（召回条数）：{run.config.top_k || run.top_k}</span>
          <span>样本上限：{run.config.limit || "全量"}</span>
          <span>同义问法：{run.config.include_alternatives ? "开启" : "关闭"}</span>
          {/* 对比分析标签：仅用于历史评测之间的对比分组，不参与检索逻辑。
              走"铅笔点开 → 表单 → 保存/取消"和改名一致的 inline 交互，
              两个字段一起保存，避免出现"改一半"的中间态。 */}
          {isEditingLabels ? (
            <form
              className="run-label-form"
              onSubmit={(event) => {
                event.preventDefault();
                void handleSubmitLabels();
              }}
            >
              <span className="run-label-form-row">
                <label className="run-label-form-label">Embedding 模型</label>
                <DatalistInput
                  className="run-label-form-input"
                  datalistId="run-detail-embedding-suggestions"
                  options={EMBEDDING_SUGGESTIONS}
                  autoFocus
                  value={labelDraftEmbedding}
                  disabled={labelSubmitting}
                  title={labelDraftEmbedding ? `当前 Embedding：${labelDraftEmbedding}` : "选择或输入 Embedding 模型"}
                  onChange={(event) => setLabelDraftEmbedding(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Escape") {
                      event.preventDefault();
                      handleCancelEditLabels();
                    }
                  }}
                  placeholder="例：text-embedding-v3 / bge-large-zh"
                />
              </span>
              <span className="run-label-form-row">
                <label className="run-label-form-label">Rerank 模型</label>
                <DatalistInput
                  className="run-label-form-input"
                  datalistId="run-detail-rerank-suggestions"
                  options={RERANK_SUGGESTIONS}
                  value={labelDraftRerank}
                  disabled={labelSubmitting}
                  title={labelDraftRerank ? `当前 Rerank：${labelDraftRerank}` : "选择或输入 Rerank 模型"}
                  onChange={(event) => setLabelDraftRerank(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Escape") {
                      event.preventDefault();
                      handleCancelEditLabels();
                    }
                  }}
                  placeholder="例：qwen3-rerank / bge-reranker-base"
                />
              </span>
              <button
                className="ghost-button"
                type="submit"
                disabled={labelSubmitting}
                title="保存（Enter）"
              >
                {labelSubmitting ? <Loader2 className="spin" size={14} /> : "保存"}
              </button>
              <button
                className="icon-button"
                type="button"
                onClick={handleCancelEditLabels}
                disabled={labelSubmitting}
                title="取消（Esc）"
                aria-label="取消"
              >
                <X size={16} />
              </button>
            </form>
          ) : (
            <>
              <span className="run-config-label-chip">
                Embedding：{run.embedding_model || "（空）"}
                <button
                  className="icon-button run-config-label-edit"
                  type="button"
                  onClick={handleStartEditLabels}
                  title="修改 Embedding / Rerank 模型标签"
                  aria-label="修改 Embedding / Rerank 模型标签"
                >
                  <Pencil size={12} />
                </button>
              </span>
              <span className="run-config-label-chip">
                Rerank：{run.rerank_model || "（空）"}
              </span>
            </>
          )}
        </div>
        {labelError && <div className="error-line">{labelError}</div>}
        <div className="progress-copy">
          <strong>{progress}%</strong>
          <span>
            已完成 {run.progress.completed_queries} / {run.progress.total_queries || run.query_count} 条 query（问题）
            {run.progress.current_sample_id ? ` · 当前样本 ${run.progress.current_sample_id}` : ""}
          </span>
        </div>
        <div className="progress-track">
          <span style={{ width: `${progress}%` }} />
        </div>
      </section>

      <section className="panel detail-tabs-panel">
        <div className="tabs-list">
          <TabButton active={activeTab === "failures"} onClick={() => setActiveTab("failures")} icon={<TriangleAlert size={16} />} label="失败样本" />
          <TabButton active={activeTab === "retrieval"} onClick={() => setActiveTab("retrieval")} icon={<Search size={16} />} label="召回明细" />
          <TabButton active={activeTab === "report"} onClick={() => setActiveTab("report")} icon={<FileText size={16} />} label="知识库检索评测报告" />
          <TabButton active={activeTab === "scenario"} onClick={() => setActiveTab("scenario")} icon={<BarChart3 size={16} />} label="场景指标" />
          <TabButton active={activeTab === "artifacts"} onClick={() => setActiveTab("artifacts")} icon={<FileDown size={16} />} label="产物下载" />
        </div>
        <div className="tab-body">
          {activeTab === "failures" && <FailedSamples run={run} />}
          {activeTab === "retrieval" && <RetrievalSamples run={run} />}
          {activeTab === "report" && <ReportView loading={reportLoading} report={report} runId={run.id} />}
          {activeTab === "scenario" && <ScenarioMetrics metrics={run.summary.by_scenario_type} metricK={metricK} />}
          {activeTab === "artifacts" && <Artifacts run={run} />}
        </div>
      </section>

      {isDeleting && (
        <DeleteDatasetDialog
          datasetName={run.name || run.id}
          datasetPath={run.id}
          sampleCount={run.query_count || run.sample_count}
          error={deleteError}
          onCancel={handleCancelDelete}
          onConfirm={() => void handleConfirmDelete()}
        />
      )}

      {deleteSuccess && (
        <DeleteSuccessToast
          datasetName={`${deleteSuccess.name}（${deleteSuccess.id}）`}
          onClose={() => setDeleteSuccess(null)}
        />
      )}
    </div>
  );
}

function hitLabels(item: RetrievalResult) {
  return [
    item.content_hit ? "内容" : "",
    item.doc_hit ? "文档" : "",
    item.section_hit ? "章节" : "",
    item.keyword_hit ? "关键词" : ""
  ].filter(Boolean);
}

function RetrievalSamples({ run }: { run: EvalRunDetail }) {
  if (!run.retrieval_samples?.length) {
    return (
      <div className="empty-state">
        <Search size={24} />
        暂无召回明细
      </div>
    );
  }

  return (
    <div className="retrieval-list">
      {run.retrieval_samples.map((sample) => (
        <article className="retrieval-sample" key={`${sample.sample_id}-${sample.query_kind}`}>
          <div className="failure-head">
            <span>{sample.sample_id}</span>
            <b>{sample.topic}</b>
            <em>{sample.content_hit_rank ? `内容命中第 ${sample.content_hit_rank} 位` : "内容未命中"}</em>
          </div>
          <p className="retrieval-query">{sample.query}</p>
          <div className="retrieval-expected">
            <span>期望文档：{sample.expected_documents.join("、") || "-"}</span>
            <span>期望章节：{sample.expected_sections.join("、") || "-"}</span>
          </div>
          <div className="retrieval-results">
            {sample.top_results.map((item) => (
              <div className={`retrieval-result ${item.content_hit ? "hit" : ""}`} key={`${sample.sample_id}-${item.rank}`}>
                <div className="retrieval-result-head">
                  <strong>#{item.rank}</strong>
                  <span>{Number(item.score || 0).toFixed(4)}</span>
                  <div className="hit-tags">
                    {hitLabels(item).length ? hitLabels(item).map((label) => <em key={label}>{label}</em>) : <em className="muted">未命中</em>}
                  </div>
                </div>
                <div className="retrieval-doc">
                  <b>{item.document_name || "-"}</b>
                  <small>{item.document_id || "-"}</small>
                </div>
                {item.keyword_matches.length > 0 && (
                  <div className="keyword-line">关键词：{item.keyword_matches.join("、")}</div>
                )}
                <p>{item.content_preview || "无内容预览"}</p>
              </div>
            ))}
          </div>
        </article>
      ))}
    </div>
  );
}

function MetricCard({ title, value, tone }: { title: string; value: string; tone: string }) {
  return (
    <div className="metric-card">
      <span>{title}</span>
      <strong className={tone}>{value}</strong>
    </div>
  );
}

function FailedSamples({ run }: { run: EvalRunDetail }) {
  if (run.status === "queued" || run.status === "running") {
    return (
      <div className="empty-state">
        <Loader2 className="spin" size={24} />
        评测运行中，终态后展示失败样本
      </div>
    );
  }

  if (run.failed_samples.length === 0) {
    return (
      <div className="empty-state">
        <CheckCircle2 size={24} />
        暂无失败样本
      </div>
    );
  }

  return (
    <div className="failure-list">
      {run.failed_samples.map((sample) => (
        <article className="failure-item" key={sample.sample_id}>
          <div className="failure-head">
            <span>{sample.sample_id}</span>
            <b>{sample.topic}</b>
            <em>{sample.content_hit_rank ? `内容命中第 ${sample.content_hit_rank} 位` : sample.doc_hit_rank ? `文档命中第 ${sample.doc_hit_rank} 位` : "未命中"}</em>
          </div>
          <p>{sample.query}</p>
          <div className="failure-grid">
            <div>
              <span>Top1（第一条）文档</span>
              <strong>{sample.top1_document || "-"}</strong>
            </div>
            <div>
              <span>期望文档</span>
              <strong>{sample.expected_documents.join("、")}</strong>
            </div>
            <div>
              <span>错误</span>
              <strong>{sample.error || "-"}</strong>
            </div>
          </div>
        </article>
      ))}
    </div>
  );
}

function ReportView({ loading, report, runId }: { loading: boolean; report: string; runId: string }) {
  const [sourceView, setSourceView] = useState(false);

  if (loading) {
    return (
      <div className="empty-state">
        <Loader2 className="spin" size={24} />
        正在加载知识库检索评测报告...
      </div>
    );
  }

  const content = report || "暂无报告内容";

  return (
    <div className="report-shell">
      <div className="report-toolbar">
        <button
          className="ghost-button"
          type="button"
          onClick={() => setSourceView((v) => !v)}
          disabled={!report}
        >
          {sourceView ? <FileText size={16} /> : <CodeIcon size={16} />}
          {sourceView ? "渲染视图" : "查看源码"}
        </button>
        <button
          className="ghost-button"
          type="button"
          onClick={() => downloadArtifact(runId, "report.md")}
          disabled={!report}
        >
          <Download size={16} />
          下载报告
        </button>
      </div>
      {sourceView ? (
        <pre className="markdown-report">{content}</pre>
      ) : (
        <div className="report-md">
          <ReactMarkdown remarkPlugins={[remarkGfm]} components={reportMarkdownComponents}>
            {content}
          </ReactMarkdown>
        </div>
      )}
    </div>
  );
}

function ScenarioMetrics({ metrics, metricK }: { metrics: Record<string, EvalRunMetrics>; metricK: number }) {
  const entries = Object.entries(metrics);
  if (entries.length === 0) {
    return (
      <div className="empty-state">
        <TableProperties size={24} />
        暂无场景指标
      </div>
    );
  }

  return (
    <div className="scenario-table">
      <div className="scenario-table-row header">
        <span>场景</span>
        <span>{`Content@${metricK}（内容召回）`}</span>
        <span>{`Doc@${metricK}（文档召回）`}</span>
        <span>MRR（平均倒数排名）</span>
        <span>空结果率</span>
        <span>平均耗时</span>
      </div>
      {entries.map(([name, item]) => {
        const scenarioContentRecall =
          rankedMetric(item, "content", "recall", metricK) ??
          rankedMetric(item, "document", "recall", metricK);
        const scenarioDocumentRecall = rankedMetric(item, "document", "recall", metricK);
        return (
          <div className="scenario-table-row" key={name}>
            <span>{name}</span>
            <span className={metricTone(scenarioContentRecall)}>{formatPercent(scenarioContentRecall)}</span>
            <span className={metricTone(scenarioDocumentRecall)}>{formatPercent(scenarioDocumentRecall)}</span>
            <span>{(item.content_mrr ?? item.document_mrr)?.toFixed(3) || "--"}</span>
            <span>{formatPercent(item.empty_result_rate)}</span>
            <span>{item.avg_latency_ms || "--"} ms</span>
          </div>
        );
      })}
    </div>
  );
}

function Artifacts({ run }: { run: EvalRunDetail }) {
  return (
    <div className="artifact-grid">
      {run.artifacts.map((artifact) => (
        <button className="artifact-card" type="button" key={artifact.name} onClick={() => downloadArtifact(run.id, artifact.name)}>
          <Download size={18} />
          <span>
            <b>{artifact.name}</b>
            <small>{artifact.type}</small>
          </span>
        </button>
      ))}
    </div>
  );
}

function TabButton({
  active,
  icon,
  label,
  onClick
}: {
  active: boolean;
  icon: ReactNode;
  label: string;
  onClick: () => void;
}) {
  return (
    <button className={`tab-button ${active ? "active" : ""}`} type="button" onClick={onClick}>
      {icon}
      {label}
    </button>
  );
}
