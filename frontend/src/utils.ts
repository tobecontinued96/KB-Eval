import type { EvalRunStatus } from "./types";

export function formatPercent(value: number | null | undefined) {
  if (typeof value !== "number" || Number.isNaN(value)) return "--";
  return `${(value * 100).toFixed(1)}%`;
}

export function formatDuration(value: number | null | undefined) {
  if (!value) return "--";
  if (value < 1000) return `${value}ms`;
  const seconds = Math.round(value / 1000);
  if (seconds < 60) return `${seconds}s`;
  return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
}

export function formatDateTime(value: string | null | undefined, mode: "full" | "date" = "full") {
  if (!value) return "--";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "--";
  if (mode === "date") {
    return date.toLocaleDateString("zh-CN", {
      month: "2-digit",
      day: "2-digit"
    });
  }
  return date.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit"
  });
}

export function statusLabel(status: EvalRunStatus) {
  const labels: Record<EvalRunStatus, string> = {
    queued: "等待中",
    running: "运行中",
    completed: "已完成",
    failed: "失败",
    canceled: "已取消"
  };
  return labels[status];
}

export function isTerminalRun(status: EvalRunStatus) {
  return status === "completed" || status === "failed" || status === "canceled";
}

export function metricTone(value: number | null | undefined) {
  if (typeof value !== "number") return "neutral";
  if (value >= 0.85) return "good";
  if (value >= 0.7) return "warn";
  return "bad";
}
