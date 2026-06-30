import { ReactNode, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { AlertTriangle, BarChart3, FileSpreadsheet, FileText, Gauge, Layers3, Loader2, RefreshCw, ShieldCheck } from "lucide-react";
import { listDatasets, listRuns } from "../api";
import { readCurrentDifyUrl } from "../difySource";
import { downloadCsv, downloadXlsx, safeFileStem, type ExportColumn } from "../exportTable";
import type { DatasetInfo, EvalRunListItem } from "../types";
import { formatDateTime, formatPercent, metricTone } from "../utils";
import { PanelHeader } from "../widgets/PanelHeader";
import { StandardSelect } from "../widgets/StandardSelect";
import { showErrorToast } from "../widgets/ErrorToast";
import {
  compareRunDecision,
  formatDecisionScore,
  scoreRunDecision,
  type RunDecisionScore
} from "./runCompareScoring";

interface EvalDatasetOption {
  path: string;
  label: string;
  reviewStatus: string;
  runCount: number;
  latestCreatedAt: string;
}

interface CompareRow {
  run: EvalRunListItem;
  embedding: string;
  rerank: string;
  metrics: Record<string, number>;
  decision: RunDecisionScore;
  isBest: boolean;
}

interface CompareConditionGroup {
  key: string;
  sampleCount: number;
  topK: number;
  rows: CompareRow[];
}

interface CompareExportRow {
  condition: string;
  sampleCount: number | null;
  topK: number | null;
  embedding: string;
  rerank: string;
  runTime: string;
  isBest: boolean;
  decision: RunDecisionScore;
  metrics: Record<string, number | undefined>;
}

const METRIC_PREFIX_ORDER = ["content", "document", "section", "keyword"];
const METRIC_KIND_ORDER = ["recall", "precision", "ndcg", "mrr"];
const SYSTEM_METRIC_ORDER = ["empty_result_rate", "avg_latency_ms", "p95_latency_ms", "total_queries", "completed_queries", "error_queries"];

function numericMetrics(run: EvalRunListItem) {
  return Object.fromEntries(
    Object.entries(run.metrics || {}).filter(([, value]) => typeof value === "number" && Number.isFinite(value))
  ) as Record<string, number>;
}

function isPercentMetric(key: string) {
  return /_(recall|precision)@\d+$/.test(key) || key === "empty_result_rate";
}

function isDecimal3Metric(key: string) {
  return /_(ndcg@\d+|mrr)$/.test(key);
}

function isLatencyMetric(key: string) {
  return key === "avg_latency_ms" || key === "p95_latency_ms";
}

function isIntegerMetric(key: string) {
  return key === "total_queries" || key === "completed_queries" || key === "error_queries";
}

function metricStyle(key: string): ExportColumn<CompareExportRow>["style"] {
  if (isPercentMetric(key)) return "percent1";
  if (isDecimal3Metric(key)) return "decimal3";
  if (isLatencyMetric(key)) return "decimal2";
  if (isIntegerMetric(key)) return "integer";
  return "decimal3";
}

function formatMetricValue(key: string, value: number | null | undefined) {
  if (typeof value !== "number" || Number.isNaN(value)) return "--";
  if (isPercentMetric(key)) return formatPercent(value);
  if (isLatencyMetric(key)) return value.toFixed(2);
  if (isIntegerMetric(key)) return String(Math.round(value));
  if (isDecimal3Metric(key)) return value.toFixed(3);
  return value.toFixed(4);
}

function metricToneClass(key: string, value: number | null | undefined) {
  if (typeof value !== "number" || Number.isNaN(value)) return "";
  if (key === "empty_result_rate") return value > 0.03 ? "bad" : "good";
  if (key === "error_queries") return value > 0 ? "bad" : "good";
  if (/_(recall|precision)@\d+$/.test(key) || isDecimal3Metric(key)) return metricTone(value);
  return "";
}

function metricLabel(key: string) {
  const axisLabels: Record<string, string> = {
    content: "Content",
    document: "Document",
    section: "Section",
    keyword: "Keyword"
  };
  const ranked = key.match(/^(content|document|section|keyword)_(recall|precision|ndcg)@(\d+)$/);
  if (ranked) {
    const [, axis, kind, k] = ranked;
    const kindLabel = kind === "ndcg" ? "NDCG" : kind[0].toUpperCase() + kind.slice(1);
    return `${axisLabels[axis]} ${kindLabel}@${k}`;
  }
  const mrr = key.match(/^(content|document|section|keyword)_mrr$/);
  if (mrr) return `${axisLabels[mrr[1]]} MRR`;
  const systemLabels: Record<string, string> = {
    empty_result_rate: "空结果率",
    avg_latency_ms: "平均耗时(ms)",
    p95_latency_ms: "P95耗时(ms)",
    total_queries: "总 query",
    completed_queries: "完成 query",
    error_queries: "错误数"
  };
  return systemLabels[key] || key;
}

function metricSortKey(key: string) {
  const ranked = key.match(/^(content|document|section|keyword)_(recall|precision|ndcg)@(\d+)$/);
  if (ranked) {
    const [, axis, kind, k] = ranked;
    return [
      METRIC_PREFIX_ORDER.indexOf(axis),
      METRIC_KIND_ORDER.indexOf(kind),
      Number(k),
      key
    ] as const;
  }
  const mrr = key.match(/^(content|document|section|keyword)_mrr$/);
  if (mrr) {
    return [
      METRIC_PREFIX_ORDER.indexOf(mrr[1]),
      METRIC_KIND_ORDER.indexOf("mrr"),
      0,
      key
    ] as const;
  }
  const systemIndex = SYSTEM_METRIC_ORDER.indexOf(key);
  if (systemIndex >= 0) return [10, systemIndex, 0, key] as const;
  return [99, 99, 0, key] as const;
}

function compareMetricKeys(left: string, right: string) {
  const leftKey = metricSortKey(left);
  const rightKey = metricSortKey(right);
  for (let index = 0; index < leftKey.length; index += 1) {
    if (leftKey[index] < rightKey[index]) return -1;
    if (leftKey[index] > rightKey[index]) return 1;
  }
  return left.localeCompare(right, "zh-CN", { numeric: true });
}

function buildMetricKeys(groups: CompareConditionGroup[]) {
  const keys = new Set<string>();
  groups.forEach((group) => {
    group.rows.forEach((row) => {
      Object.keys(row.metrics).forEach((key) => keys.add(key));
    });
  });
  return Array.from(keys).sort(compareMetricKeys);
}

function buildCompareExportColumns(metricKeys: string[]): ExportColumn<CompareExportRow>[] {
  return [
    { header: "测试条件", value: (row) => row.condition, width: 18 },
    { header: "样本数", value: (row) => row.sampleCount, width: 10, style: "integer" },
    { header: "TopK", value: (row) => row.topK, width: 8, style: "integer" },
    { header: "Embedding", value: (row) => row.embedding, width: 24 },
    { header: "Rerank", value: (row) => row.rerank, width: 18 },
    { header: "运行时间", value: (row) => row.runTime, width: 16 },
    {
      header: "综合分",
      value: (row) => row.decision.score,
      csvValue: (row) => formatDecisionScore(row.decision.score).replace("--", ""),
      width: 10,
      style: "decimal2"
    },
    { header: "等级", value: (row) => row.decision.grade, width: 10 },
    { header: "风险", value: (row) => row.decision.riskLabel, width: 12 },
    { header: "短板", value: (row) => row.decision.weakness, width: 16 },
    { header: "判断依据", value: (row) => row.decision.reason, width: 28 },
    ...metricKeys.map((key): ExportColumn<CompareExportRow> => ({
      header: metricLabel(key),
      value: (row) => row.metrics[key] ?? null,
      csvValue: (row) => {
        const value = row.metrics[key];
        return typeof value === "number" ? formatMetricValue(key, value).replace("--", "") : "";
      },
      width: metricLabel(key).includes("耗时") ? 14 : Math.max(12, Math.min(22, metricLabel(key).length + 4)),
      style: metricStyle(key)
    }))
  ];
}

function normalizeEmbedding(value: string | null | undefined) {
  const cleaned = (value || "").trim();
  return cleaned || "未标注";
}

function normalizeRerank(value: string | null | undefined) {
  const cleaned = (value || "").trim();
  return cleaned || "无";
}

function conditionKey(run: EvalRunListItem) {
  return `${run.sample_count || run.query_count || 0}::${run.top_k || 0}`;
}

function compareRunQuality(left: EvalRunListItem, right: EvalRunListItem) {
  return compareRunDecision(left, right);
}

function buildDatasetOptions(datasets: DatasetInfo[], runs: EvalRunListItem[]): EvalDatasetOption[] {
  const byPath = new Map<string, EvalDatasetOption>();
  const datasetByPath = new Map(datasets.map((dataset) => [dataset.path, dataset]));
  runs
    .filter((run) => run.status === "completed" && run.eval_file)
    .forEach((run) => {
      const dataset = datasetByPath.get(run.eval_file);
      const current = byPath.get(run.eval_file);
      const nextTime = new Date(run.created_at).getTime();
      const currentTime = current ? new Date(current.latestCreatedAt).getTime() : -1;
      byPath.set(run.eval_file, {
        path: run.eval_file,
        label: dataset?.name || run.eval_file,
        reviewStatus: dataset?.review_status || "",
        runCount: (current?.runCount || 0) + 1,
        latestCreatedAt: nextTime >= currentTime ? run.created_at : current?.latestCreatedAt || run.created_at
      });
    });
  return Array.from(byPath.values()).sort(
    (left, right) => new Date(right.latestCreatedAt).getTime() - new Date(left.latestCreatedAt).getTime()
  );
}

function buildConditionGroups(runs: EvalRunListItem[]): CompareConditionGroup[] {
  const byCondition = new Map<string, EvalRunListItem[]>();
  runs.forEach((run) => {
    const key = conditionKey(run);
    byCondition.set(key, [...(byCondition.get(key) || []), run]);
  });

  return Array.from(byCondition.entries())
    .map(([key, conditionRuns]) => {
      const rows = conditionRuns.map<CompareRow>((run) => ({
        run,
        embedding: normalizeEmbedding(run.embedding_model),
        rerank: normalizeRerank(run.rerank_model),
        metrics: numericMetrics(run),
        decision: scoreRunDecision(run),
        isBest: false
      }));
      const bestRun = [...conditionRuns].sort(compareRunQuality)[0];
      const sortedRows = rows
        .map((row) => ({ ...row, isBest: row.run.id === bestRun?.id }))
        .sort((left, right) => {
          const embeddingSort = left.embedding.localeCompare(right.embedding, "zh-CN", { numeric: true });
          if (embeddingSort !== 0) return embeddingSort;
          const rerankSort = left.rerank.localeCompare(right.rerank, "zh-CN", { numeric: true });
          if (rerankSort !== 0) return rerankSort;
          return new Date(right.run.created_at).getTime() - new Date(left.run.created_at).getTime();
        });
      const [sampleCount, topK] = key.split("::").map((item) => Number(item));
      return { key, sampleCount, topK, rows: sortedRows };
    })
    .sort((left, right) => {
      if (left.sampleCount !== right.sampleCount) return left.sampleCount - right.sampleCount;
      return left.topK - right.topK;
    });
}

function reviewLabel(status: string) {
  if (status === "reviewed") return " · 已审核";
  if (status === "draft") return " · 草稿待审";
  if (status) return " · 未审核";
  return "";
}

function buildCompareExportRows(groups: CompareConditionGroup[]): CompareExportRow[] {
  return groups.flatMap((group) => {
    return group.rows.map((row) => {
      const sampleCount = group.sampleCount || null;
      const topK = group.topK || null;
      return {
        condition: `样本数=${sampleCount || "--"} TopK=${topK || "--"}`,
        sampleCount,
        topK,
        embedding: row.embedding,
        rerank: row.rerank,
        runTime: formatDateTime(row.run.created_at),
        isBest: row.isBest,
        decision: row.decision,
        metrics: row.metrics
      };
    });
  });
}

function summarizeDecisionRows(rows: CompareRow[]) {
  const usableCount = rows.filter((row) => row.decision.grade === "优秀" || row.decision.grade === "可用").length;
  const blockingCount = rows.filter((row) => row.decision.riskLabel === "阻断").length;
  const riskCounts = new Map<string, number>();
  rows.forEach((row) => {
    row.decision.riskItems.forEach((item) => riskCounts.set(item, (riskCounts.get(item) || 0) + 1));
  });
  const primaryRisk = Array.from(riskCounts.entries()).sort((left, right) => {
    if (right[1] !== left[1]) return right[1] - left[1];
    return left[0].localeCompare(right[0], "zh-CN", { numeric: true });
  })[0]?.[0];
  return {
    usableCount,
    blockingCount,
    primaryRisk: primaryRisk || "暂无明显风险"
  };
}

function exportFileStem(selectedDataset: EvalDatasetOption | undefined) {
  const date = new Date().toISOString().slice(0, 10).replaceAll("-", "");
  return safeFileStem(`${selectedDataset?.label || "评测矩阵"}-评测矩阵-${date}`);
}

export function RunCompare() {
  const navigate = useNavigate();
  const [datasets, setDatasets] = useState<DatasetInfo[]>([]);
  const [runs, setRuns] = useState<EvalRunListItem[]>([]);
  const [selectedEvalFile, setSelectedEvalFile] = useState("");
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  // 从首页成功连接后记录的"当前 Dify URL"，用于按 Dify 隔离列表。
  // 只在 mount 时读一次 —— 用户在 RunCompare 内不修改它，要换回首页改。
  // 读不到（未在首页填过）就保持 null：查全部，不阻断用户。
  // 用 lazy initializer 避免"先查全部、再按 URL 查"的双请求闪烁。
  const [currentDifyUrl] = useState<string | null>(() => readCurrentDifyUrl());

  async function load() {
    const difyBaseUrl = currentDifyUrl || undefined;
    const [datasetResult, runResult] = await Promise.all([
      listDatasets(),
      listRuns(difyBaseUrl ? { difyBaseUrl } : {})
    ]);
    setDatasets(datasetResult.items);
    setRuns(runResult.items);
    const options = buildDatasetOptions(datasetResult.items, runResult.items);
    setSelectedEvalFile((current) => {
      if (current && options.some((option) => option.path === current)) return current;
      return options[0]?.path || "";
    });
  }

  useEffect(() => {
    void (async () => {
      try {
        setLoading(true);
        await load();
      } catch (err) {
        showErrorToast({ title: err instanceof Error ? err.message : "对比数据加载失败", code: "unknown" });
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  const datasetOptions = useMemo(() => buildDatasetOptions(datasets, runs), [datasets, runs]);

  const selectedRuns = useMemo(
    () => runs.filter((run) => run.status === "completed" && run.eval_file === selectedEvalFile),
    [runs, selectedEvalFile]
  );

  const groups = useMemo(() => buildConditionGroups(selectedRuns), [selectedRuns]);
  const bestRows = groups.flatMap((group) => group.rows.filter((row) => row.isBest));
  const topRow = [...bestRows].sort((left, right) => compareRunQuality(left.run, right.run))[0];
  const decisionRows = groups.flatMap((group) => group.rows);
  const decisionSummary = summarizeDecisionRows(decisionRows);
  const selectedDataset = datasetOptions.find((option) => option.path === selectedEvalFile);
  const metricKeys = useMemo(() => buildMetricKeys(groups), [groups]);
  const exportRows = useMemo(() => buildCompareExportRows(groups), [groups]);
  const exportColumns = useMemo(() => buildCompareExportColumns(metricKeys), [metricKeys]);
  const exportDisabled = exportRows.length === 0;

  async function handleRefresh() {
    setRefreshing(true);
    try {
      await load();
    } catch (err) {
      showErrorToast({ title: err instanceof Error ? err.message : "刷新失败", code: "unknown" });
    } finally {
      setRefreshing(false);
      setLoading(false);
    }
  }

  function handleExportCsv() {
    try {
      downloadCsv(`${exportFileStem(selectedDataset)}.csv`, exportColumns, exportRows);
    } catch (err) {
      showErrorToast({ title: err instanceof Error ? err.message : "CSV 导出失败", code: "unknown" });
    }
  }

  function handleExportXlsx() {
    try {
      downloadXlsx(`${exportFileStem(selectedDataset)}.xlsx`, "评测矩阵", exportColumns, exportRows, {
        rowStyle: (row) => (row.isBest ? "bold" : undefined)
      });
    } catch (err) {
      showErrorToast({ title: err instanceof Error ? err.message : "Excel 导出失败", code: "unknown" });
    }
  }

  function handleOpenRun(runId: string) {
    navigate(`/runs/${runId}`, {
      state: { from: { pathname: "/compare" } }
    });
  }

  return (
    <div className="compare-page">
      <section className="panel compare-toolbar-panel">
        <PanelHeader
          icon={<BarChart3 size={18} />}
          title="分析对比"
          subtitle="选择一个评测集，对比同文档知识库在不同 Embedding / Rerank 与测试条件下的表现"
          action={
            <button className="ghost-button" type="button" onClick={() => void handleRefresh()} disabled={refreshing}>
              <RefreshCw size={16} className={refreshing ? "spin" : ""} />
              刷新
            </button>
          }
        />
        <div className="compare-controls compare-controls-single">
          <label className="compare-control">
            <span>评测集</span>
            <StandardSelect
              value={selectedEvalFile}
              title={selectedDataset ? `当前评测集：${selectedDataset.label}${reviewLabel(selectedDataset.reviewStatus)}` : "请选择评测集"}
              onChange={(event) => setSelectedEvalFile(event.target.value)}
              disabled={loading || datasetOptions.length === 0}
            >
              {datasetOptions.map((option) => (
                <option key={option.path} value={option.path}>
                  {option.label}{reviewLabel(option.reviewStatus)}
                </option>
              ))}
            </StandardSelect>
            {selectedDataset && (
              <small className="compare-control-meta">
                最近运行 {formatDateTime(selectedDataset.latestCreatedAt)}
              </small>
            )}
          </label>
        </div>
      </section>

      {loading ? (
        <div className="panel">
          <div className="empty-state">
            <Loader2 size={24} className="spin" />
            正在加载对比数据...
          </div>
        </div>
      ) : datasetOptions.length === 0 ? (
        <div className="panel">
          <div className="empty-state">
            <Layers3 size={24} />
            暂无可对比的完成运行
          </div>
        </div>
      ) : (
        <>
          <section className="compare-summary-grid" aria-label="对比摘要">
            <SummaryTile
              icon={<Gauge size={18} />}
              label="全局最佳综合分"
              value={formatDecisionScore(topRow?.decision.score ?? null)}
              detail={topRow ? `${topRow.embedding} / ${topRow.rerank}` : "--"}
              tone={topRow?.decision.tone || "neutral"}
            />
            <SummaryTile
              icon={<ShieldCheck size={18} />}
              label="可用候选"
              value={`${decisionSummary.usableCount}/${selectedRuns.length}`}
              detail={decisionSummary.blockingCount > 0 ? `${decisionSummary.blockingCount} 个阻断风险` : `${groups.length} 组测试条件`}
              tone={decisionSummary.blockingCount > 0 ? "warn" : "good"}
            />
            <SummaryTile
              icon={<AlertTriangle size={18} />}
              label="主要风险"
              value={decisionSummary.primaryRisk}
              detail="综合空结果、错误、召回、P95 和样本数"
              tone={decisionSummary.primaryRisk === "暂无明显风险" ? "good" : "warn"}
            />
          </section>

          <section className="panel compare-matrix-panel">
            <PanelHeader
              icon={<BarChart3 size={18} />}
              title="评测矩阵"
              subtitle="每个测试条件内，粗体行为综合分优先、召回与 MRR 次之、P95 更低的最佳配置"
              action={
                <div className="compare-export-actions" aria-label="导出评测矩阵">
                  <button
                    className="ghost-button"
                    type="button"
                    onClick={handleExportXlsx}
                    disabled={exportDisabled}
                    title="导出为 Excel"
                  >
                    <FileSpreadsheet size={16} />
                    Excel
                  </button>
                  <button
                    className="ghost-button"
                    type="button"
                    onClick={handleExportCsv}
                    disabled={exportDisabled}
                    title="导出为 CSV"
                  >
                    <FileText size={16} />
                    CSV
                  </button>
                </div>
              }
            />
            {groups.length === 0 ? (
              <div className="empty-state compact">
                <Layers3 size={20} />
                当前评测集暂无完成运行
              </div>
            ) : (
              <div className="compare-sheet-wrap">
                <table className="compare-sheet-table" style={{ minWidth: `${Math.max(1180, 820 + metricKeys.length * 128)}px` }}>
                  <thead>
                    <tr>
                      <th>测试条件</th>
                      <th>Embedding</th>
                      <th>Rerank</th>
                      <th>运行时间</th>
                      <th>综合分</th>
                      <th>等级</th>
                      <th>风险</th>
                      <th>短板</th>
                      <th>判断依据</th>
                      {metricKeys.map((key) => (
                        <th key={key}>{metricLabel(key)}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {groups.map((group) =>
                      group.rows.map((row, rowIndex) => (
                        <CompareSheetRow
                          key={`${group.key}-${row.run.id}`}
                          group={group}
                          row={row}
                          rowIndex={rowIndex}
                          metricKeys={metricKeys}
                          onOpenRun={handleOpenRun}
                        />
                      ))
                    )}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        </>
      )}
    </div>
  );
}

function SummaryTile({
  icon,
  label,
  value,
  detail,
  tone
}: {
  icon: ReactNode;
  label: string;
  value: string;
  detail: string;
  tone: string;
}) {
  return (
    <div className="compare-summary-tile">
      <div className="compare-summary-icon">{icon}</div>
      <span>{label}</span>
      <strong className={tone}>{value}</strong>
      <small>{detail}</small>
    </div>
  );
}

function CompareSheetRow({
  group,
  row,
  rowIndex,
  metricKeys,
  onOpenRun
}: {
  group: CompareConditionGroup;
  row: CompareRow;
  rowIndex: number;
  metricKeys: string[];
  onOpenRun: (runId: string) => void;
}) {
  const runTime = formatDateTime(row.run.created_at);
  return (
    <tr
      className={`compare-clickable-row ${row.isBest ? "is-best" : ""}`}
      role="link"
      tabIndex={0}
      aria-label={`查看评测详情：${row.run.name}，综合分 ${formatDecisionScore(row.decision.score)}，运行时间 ${runTime}`}
      onClick={() => onOpenRun(row.run.id)}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onOpenRun(row.run.id);
        }
      }}
    >
      {rowIndex === 0 && (
        <td className="compare-condition-cell" rowSpan={group.rows.length} onClick={(event) => event.stopPropagation()}>
          <span>样本数={group.sampleCount || "--"}</span>
          <span>TopK={group.topK || "--"}</span>
        </td>
      )}
      <td>{row.embedding}</td>
      <td>{row.rerank}</td>
      <td className="compare-run-time-cell" title={row.run.name}>{runTime}</td>
      <td className={`compare-number-cell compare-score-cell ${row.decision.tone}`}>
        {formatDecisionScore(row.decision.score)}
      </td>
      <td className="compare-decision-cell">
        <span className={`ui-badge compare-decision-pill ${row.decision.tone}`}>{row.decision.grade}</span>
        <small>{row.decision.confidence}</small>
      </td>
      <td className="compare-risk-cell" title={row.decision.riskItems.join("；") || row.decision.riskLabel}>
        <span className={`ui-badge compare-risk-pill ${row.decision.riskTone}`}>{row.decision.riskLabel}</span>
      </td>
      <td className="compare-text-cell" title={row.decision.weakness}>{row.decision.weakness}</td>
      <td className="compare-text-cell compare-reason-cell" title={row.decision.riskItems.join("；") || row.decision.reason}>
        {row.decision.reason}
      </td>
      {metricKeys.map((key) => {
        const value = row.metrics[key];
        return (
          <td className={`compare-number-cell compare-metric-cell ${metricToneClass(key, value)}`} key={key}>
            {formatMetricValue(key, value)}
          </td>
        );
      })}
    </tr>
  );
}
