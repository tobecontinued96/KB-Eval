import type { EvalRunListItem } from "../types";

const DEFAULT_METRIC_K = 5;

type RankedMetricAxis = "content" | "document" | "section" | "keyword";
type RankedMetricKind = "recall" | "precision" | "ndcg";
type RankedMetricResult = { k: number; value: number };

function finiteMetric(value: number | undefined) {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

export function getRunHistoryMetricK(run: EvalRunListItem): number {
  const configuredK = run.top_k || DEFAULT_METRIC_K;
  return Math.max(1, Math.min(DEFAULT_METRIC_K, configuredK));
}

export function rankedRunMetric(
  metrics: EvalRunListItem["metrics"],
  axis: RankedMetricAxis,
  kind: RankedMetricKind,
  targetK: number
) {
  return rankedRunMetricWithK(metrics, axis, kind, targetK)?.value;
}

export function rankedRunMetricWithK(
  metrics: EvalRunListItem["metrics"],
  axis: RankedMetricAxis,
  kind: RankedMetricKind,
  targetK: number
): RankedMetricResult | undefined {
  const direct = finiteMetric(metrics[`${axis}_${kind}@${targetK}`]);
  if (direct !== undefined) return { k: targetK, value: direct };

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
  const fallback = belowTarget ?? candidates[0];
  return fallback ? { k: fallback.k, value: fallback.value } : undefined;
}

export function getRunHistoryRecallMetric(
  run: EvalRunListItem
): (RankedMetricResult & { axis: "content" | "document" }) | undefined {
  const metricK = getRunHistoryMetricK(run);
  const content = rankedRunMetricWithK(run.metrics, "content", "recall", metricK);
  if (content) return { ...content, axis: "content" };

  const document = rankedRunMetricWithK(run.metrics, "document", "recall", metricK);
  if (document) return { ...document, axis: "document" };
  return undefined;
}

export function getRunHistoryRecall(run: EvalRunListItem): number | undefined {
  return getRunHistoryRecallMetric(run)?.value;
}

export function canManageRun(run: EvalRunListItem): boolean {
  return run.status !== "queued" && run.status !== "running";
}

export function getManageableRunIds(runs: EvalRunListItem[]): string[] {
  return runs.filter(canManageRun).map((run) => run.id);
}

export function pruneManagedSelection(selectedRunIds: string[], runs: EvalRunListItem[]): string[] {
  const manageableIds = new Set(getManageableRunIds(runs));
  return selectedRunIds.filter((runId) => manageableIds.has(runId));
}

export function toggleManagedSelection(selectedRunIds: string[], runId: string): string[] {
  const selected = new Set(selectedRunIds);
  if (selected.has(runId)) {
    selected.delete(runId);
  } else {
    selected.add(runId);
  }
  return [...selected];
}

export function areAllManageableRunsSelected(runs: EvalRunListItem[], selectedRunIds: string[]): boolean {
  const manageableRunIds = getManageableRunIds(runs);
  if (manageableRunIds.length === 0) return false;
  const selected = new Set(selectedRunIds);
  return manageableRunIds.every((runId) => selected.has(runId));
}

export function getSelectedRuns(runs: EvalRunListItem[], selectedRunIds: string[]): EvalRunListItem[] {
  const selected = new Set(selectedRunIds);
  return runs.filter((run) => selected.has(run.id));
}

export function buildBulkDeleteMessage(runs: EvalRunListItem[]): string {
  if (runs.length === 0) {
    return "当前没有选中的历史记录。";
  }
  if (runs.length === 1) {
    return "将删除 1 条历史记录，并保留一份备份。\n删除后这条运行会从列表中移除。";
  }
  return `将删除 ${runs.length} 条历史记录，并分别保留备份。\n删除后这些运行会从列表中移除，请确认后继续。`;
}
