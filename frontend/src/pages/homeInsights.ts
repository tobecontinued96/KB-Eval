import type { DatasetInfo, EvalRunListItem } from "../types";

export interface HomeInsights {
  selectedDataset: DatasetInfo | null;
  blockedByReview: boolean;
  latestRun: EvalRunListItem | null;
  latestCompletedRun: EvalRunListItem | null;
  runningCount: number;
  failedCount: number;
}

function byCreatedAtDesc(left: EvalRunListItem, right: EvalRunListItem) {
  return new Date(right.created_at).getTime() - new Date(left.created_at).getTime();
}

export function buildHomeInsights(
  datasets: DatasetInfo[],
  runs: EvalRunListItem[],
  selectedPath: string
): HomeInsights {
  const selectedDataset =
    datasets.find((dataset) => dataset.path === selectedPath) || datasets[0] || null;
  const sortedRuns = [...runs].sort(byCreatedAtDesc);

  return {
    selectedDataset,
    blockedByReview: Boolean(selectedDataset && selectedDataset.review_status !== "reviewed"),
    latestRun: sortedRuns[0] || null,
    latestCompletedRun: sortedRuns.find((run) => run.status === "completed") || null,
    runningCount: runs.filter((run) => run.status === "running").length,
    failedCount: runs.filter((run) => run.status === "failed").length
  };
}
