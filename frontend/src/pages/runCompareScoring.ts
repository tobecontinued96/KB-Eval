import type { EvalRunListItem, EvalRunMetrics } from "../types";

export type DecisionTone = "good" | "warn" | "bad" | "neutral";

export interface RunDecisionScore {
  score: number | null;
  grade: string;
  tone: DecisionTone;
  riskLabel: string;
  riskTone: DecisionTone;
  riskItems: string[];
  weakness: string;
  reason: string;
  confidence: string;
  contentRecall?: number;
  contentMrr?: number;
  p95LatencyMs?: number;
}

const TARGET_K = 5;

function finiteMetric(value: number | undefined) {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

function metricValue(metrics: EvalRunMetrics, key: string) {
  return finiteMetric(metrics[key]);
}

function clamp01(value: number) {
  return Math.max(0, Math.min(1, value));
}

function formatPercentShort(value: number | undefined) {
  return typeof value === "number" ? `${(value * 100).toFixed(1)}%` : "--";
}

function formatLatencyShort(value: number | undefined) {
  if (typeof value !== "number") return "--";
  if (value < 1000) return `${Math.round(value)}ms`;
  return `${(value / 1000).toFixed(1)}s`;
}

function rankedMetric(metrics: EvalRunMetrics, axis: string, kind: string, targetK = TARGET_K) {
  const direct = metricValue(metrics, `${axis}_${kind}@${targetK}`);
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

function firstMetric(metrics: EvalRunMetrics, keys: string[]) {
  for (const key of keys) {
    const value = metricValue(metrics, key);
    if (value !== undefined) return value;
  }
  return undefined;
}

function queryCount(run: EvalRunListItem) {
  return (
    metricValue(run.metrics, "total_queries") ??
    finiteMetric(run.query_count) ??
    finiteMetric(run.sample_count) ??
    0
  );
}

function completedCount(run: EvalRunListItem, totalQueries: number) {
  return metricValue(run.metrics, "completed_queries") ?? totalQueries;
}

function buildRiskItems({
  totalQueries,
  completedQueries,
  errorQueries,
  emptyRate,
  contentRecall,
  p95LatencyMs
}: {
  totalQueries: number;
  completedQueries: number;
  errorQueries: number;
  emptyRate: number;
  contentRecall?: number;
  p95LatencyMs?: number;
}) {
  const items: string[] = [];
  if (errorQueries > 0) items.push(`错误 ${Math.round(errorQueries)} 条`);
  if (completedQueries < totalQueries) items.push("存在未完成 query");
  if (emptyRate > 0.03) items.push(`空结果率 ${formatPercentShort(emptyRate)}`);
  if (contentRecall === undefined) items.push("缺少 Content Recall");
  else if (contentRecall < 0.75) items.push(`Content Recall ${formatPercentShort(contentRecall)}`);
  if (p95LatencyMs !== undefined && p95LatencyMs > 4000) items.push(`P95 ${formatLatencyShort(p95LatencyMs)}`);
  if (totalQueries > 0 && totalQueries < 30) items.push(`样本偏少 ${Math.round(totalQueries)} 条`);
  return items;
}

function classifyRisk(
  score: number | null,
  {
    totalQueries,
    completedQueries,
    errorQueries,
    emptyRate,
    contentRecall,
    p95LatencyMs
  }: {
    totalQueries: number;
    completedQueries: number;
    errorQueries: number;
    emptyRate: number;
    contentRecall?: number;
    p95LatencyMs?: number;
  }
) {
  if (score === null) return { label: "缺指标", tone: "neutral" as const, blocking: true };
  const blocking =
    errorQueries > 0 ||
    completedQueries < totalQueries ||
    emptyRate > 0.1 ||
    (contentRecall !== undefined && contentRecall < 0.6);
  if (blocking) return { label: "阻断", tone: "bad" as const, blocking };
  const needsReview =
    (contentRecall !== undefined && contentRecall < 0.75) ||
    emptyRate > 0.03 ||
    (p95LatencyMs !== undefined && p95LatencyMs > 4000) ||
    (totalQueries > 0 && totalQueries < 30);
  if (needsReview) return { label: "需复核", tone: "warn" as const, blocking: false };
  return { label: "通过", tone: "good" as const, blocking: false };
}

function gradeFor(score: number | null, blocking: boolean, riskLabel: string) {
  if (score === null) return { grade: "缺指标", tone: "neutral" as const };
  if (blocking || score < 60) return { grade: "不可用", tone: "bad" as const };
  if (score >= 85 && riskLabel === "通过") return { grade: "优秀", tone: "good" as const };
  if (score >= 75) return { grade: "可用", tone: "good" as const };
  return { grade: "风险可用", tone: "warn" as const };
}

function weaknessFor({
  contentRecall,
  contentMrr,
  contentNdcg,
  sectionRecall,
  documentRecall,
  keywordSignal,
  p95LatencyMs
}: {
  contentRecall?: number;
  contentMrr?: number;
  contentNdcg?: number;
  sectionRecall?: number;
  documentRecall?: number;
  keywordSignal?: number;
  p95LatencyMs?: number;
}) {
  if (contentRecall === undefined) return "缺少内容召回指标";
  if (contentRecall < 0.75) return "内容召回不足";
  if (contentMrr !== undefined && contentMrr < 0.75) return "首个命中偏后";
  if (contentNdcg !== undefined && contentNdcg < 0.75) return "排序质量一般";
  if (sectionRecall !== undefined && sectionRecall < 0.7) return "章节定位偏弱";
  if (documentRecall !== undefined && documentRecall < 0.75) return "文档命中偏弱";
  if (keywordSignal !== undefined && keywordSignal < 0.7) return "关键词匹配偏弱";
  if (p95LatencyMs !== undefined && p95LatencyMs > 3000) return "尾延迟偏高";
  return "无明显短板";
}

export function scoreRunDecision(run: EvalRunListItem): RunDecisionScore {
  const metrics = run.metrics || {};
  const contentRecall =
    rankedMetric(metrics, "content", "recall") ?? rankedMetric(metrics, "document", "recall");
  const contentMrr = firstMetric(metrics, ["content_mrr", "document_mrr"]);
  const contentNdcg =
    rankedMetric(metrics, "content", "ndcg") ?? rankedMetric(metrics, "document", "ndcg");
  const sectionRecall = rankedMetric(metrics, "section", "recall");
  const documentRecall = rankedMetric(metrics, "document", "recall");
  const keywordSignal =
    rankedMetric(metrics, "keyword", "recall") ??
    rankedMetric(metrics, "keyword", "ndcg") ??
    metricValue(metrics, "keyword_mrr");

  const components = [
    { value: contentRecall, weight: 40 },
    { value: contentMrr, weight: 25 },
    { value: contentNdcg, weight: 15 },
    { value: sectionRecall, weight: 10 },
    { value: documentRecall, weight: 5 },
    { value: keywordSignal, weight: 5 }
  ];
  const hasQualitySignal = components.some((component) => component.value !== undefined);

  const totalQueries = queryCount(run);
  const completedQueries = completedCount(run, totalQueries);
  const errorQueries = metricValue(metrics, "error_queries") ?? 0;
  const emptyRate = metricValue(metrics, "empty_result_rate") ?? 0;
  const p95LatencyMs = metricValue(metrics, "p95_latency_ms");
  const incompleteRate = totalQueries > 0 ? Math.max(0, totalQueries - completedQueries) / totalQueries : 0;
  const errorRate = totalQueries > 0 ? errorQueries / totalQueries : 0;

  const qualityScore = components.reduce(
    (total, component) => total + component.weight * clamp01(component.value ?? 0),
    0
  );
  const latencyPenalty =
    p95LatencyMs === undefined || p95LatencyMs <= 2000
      ? 0
      : Math.min(12, ((p95LatencyMs - 2000) / 4000) * 12);
  const samplePenalty = totalQueries > 0 && totalQueries < 10 ? 8 : totalQueries > 0 && totalQueries < 30 ? 4 : 0;
  const penalty = emptyRate * 30 + errorRate * 50 + incompleteRate * 40 + latencyPenalty + samplePenalty;
  const score = hasQualitySignal ? Math.max(0, Math.min(100, qualityScore - penalty)) : null;

  const riskItems = buildRiskItems({
    totalQueries,
    completedQueries,
    errorQueries,
    emptyRate,
    contentRecall,
    p95LatencyMs
  });
  const risk = classifyRisk(score, {
    totalQueries,
    completedQueries,
    errorQueries,
    emptyRate,
    contentRecall,
    p95LatencyMs
  });
  const grade = gradeFor(score, risk.blocking, risk.label);
  const weakness = weaknessFor({
    contentRecall,
    contentMrr,
    contentNdcg,
    sectionRecall,
    documentRecall,
    keywordSignal,
    p95LatencyMs
  });
  const confidence = totalQueries >= 50 ? "正式评测" : totalQueries >= 30 ? "小样本" : "快测参考";

  return {
    score,
    grade: grade.grade,
    tone: grade.tone,
    riskLabel: risk.label,
    riskTone: risk.tone,
    riskItems,
    weakness,
    confidence,
    reason: `召回 ${formatPercentShort(contentRecall)} / MRR ${
      contentMrr !== undefined ? contentMrr.toFixed(3) : "--"
    } / P95 ${formatLatencyShort(p95LatencyMs)}`,
    contentRecall,
    contentMrr,
    p95LatencyMs
  };
}

export function formatDecisionScore(score: number | null) {
  return score === null ? "--" : score.toFixed(1);
}

export function compareRunDecision(left: EvalRunListItem, right: EvalRunListItem) {
  const leftDecision = scoreRunDecision(left);
  const rightDecision = scoreRunDecision(right);
  const scoreDelta = (rightDecision.score ?? -1) - (leftDecision.score ?? -1);
  if (scoreDelta !== 0) return scoreDelta;
  const recallDelta = (rightDecision.contentRecall ?? -1) - (leftDecision.contentRecall ?? -1);
  if (recallDelta !== 0) return recallDelta;
  const mrrDelta = (rightDecision.contentMrr ?? -1) - (leftDecision.contentMrr ?? -1);
  if (mrrDelta !== 0) return mrrDelta;
  const latencyDelta =
    (leftDecision.p95LatencyMs ?? Number.MAX_SAFE_INTEGER) -
    (rightDecision.p95LatencyMs ?? Number.MAX_SAFE_INTEGER);
  if (latencyDelta !== 0) return latencyDelta;
  return new Date(right.created_at).getTime() - new Date(left.created_at).getTime();
}
