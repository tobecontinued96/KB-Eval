import test from "node:test";
import assert from "node:assert/strict";
import { buildHomeInsights } from "../../.tmp/test-build/pages/homeInsights.js";

const datasets = [
  {
    id: "draft",
    name: "草稿评测集",
    path: "datasets/draft.jsonl",
    review_status: "draft",
    sample_count: 12,
    vendor: "华为",
    model: "S1720",
    version: "v0.1",
    updated_at: "2026-06-14T10:00:00Z"
  },
  {
    id: "reviewed",
    name: "已审核评测集",
    path: "datasets/reviewed.jsonl",
    review_status: "reviewed",
    sample_count: 20,
    vendor: "思科",
    model: "Catalyst 1200",
    version: "v0.1",
    updated_at: "2026-06-15T10:00:00Z"
  }
];

const runs = [
  {
    id: "running",
    name: "运行中",
    status: "running",
    created_at: "2026-06-15T12:00:00Z",
    eval_file: "datasets/reviewed.jsonl",
    top_k: 5,
    sample_count: 20,
    query_count: 20,
    metrics: {}
  },
  {
    id: "completed",
    name: "已完成",
    status: "completed",
    created_at: "2026-06-15T11:00:00Z",
    eval_file: "datasets/reviewed.jsonl",
    top_k: 5,
    sample_count: 20,
    query_count: 20,
    metrics: { "content_recall@5": 0.92 }
  },
  {
    id: "failed",
    name: "失败",
    status: "failed",
    created_at: "2026-06-15T09:00:00Z",
    eval_file: "datasets/draft.jsonl",
    top_k: 5,
    sample_count: 12,
    query_count: 12,
    metrics: {}
  }
];

test("buildHomeInsights selects requested dataset and summarizes latest runs", () => {
  const insight = buildHomeInsights(datasets, runs, "datasets/reviewed.jsonl");

  assert.equal(insight.selectedDataset?.id, "reviewed");
  assert.equal(insight.blockedByReview, false);
  assert.equal(insight.latestRun?.id, "running");
  assert.equal(insight.latestCompletedRun?.id, "completed");
  assert.equal(insight.runningCount, 1);
  assert.equal(insight.failedCount, 1);
});

test("buildHomeInsights falls back to first dataset and blocks unreviewed input", () => {
  const insight = buildHomeInsights(datasets, runs, "missing.jsonl");

  assert.equal(insight.selectedDataset?.id, "draft");
  assert.equal(insight.blockedByReview, true);
});
