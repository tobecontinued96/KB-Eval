import test from "node:test";
import assert from "node:assert/strict";
import {
  areAllManageableRunsSelected,
  buildBulkDeleteMessage,
  canManageRun,
  getManageableRunIds,
  getRunHistoryMetricK,
  getRunHistoryRecall,
  getRunHistoryRecallMetric,
  getSelectedRuns,
  pruneManagedSelection,
  rankedRunMetric,
  toggleManagedSelection
} from "../../.tmp/test-build/pages/runHistoryHelpers.js";

const runs = [
  {
    id: "queued-run",
    name: "排队中的运行",
    status: "queued",
    created_at: "2026-06-17T10:00:00Z",
    eval_file: "datasets/a.jsonl",
    top_k: 5,
    sample_count: 10,
    query_count: 10,
    metrics: {}
  },
  {
    id: "completed-run",
    name: "已完成运行",
    status: "completed",
    created_at: "2026-06-17T09:00:00Z",
    eval_file: "datasets/a.jsonl",
    top_k: 5,
    sample_count: 10,
    query_count: 10,
    metrics: {}
  },
  {
    id: "failed-run",
    name: "失败运行",
    status: "failed",
    created_at: "2026-06-17T08:00:00Z",
    eval_file: "datasets/a.jsonl",
    top_k: 5,
    sample_count: 10,
    query_count: 10,
    metrics: {}
  }
];

test("canManageRun excludes queued and running runs", () => {
  assert.equal(canManageRun(runs[0]), false);
  assert.equal(canManageRun({ ...runs[0], id: "running-run", status: "running" }), false);
  assert.equal(canManageRun(runs[1]), true);
});

test("getManageableRunIds and pruneManagedSelection keep only deletable runs", () => {
  assert.deepEqual(getManageableRunIds(runs), ["completed-run", "failed-run"]);
  assert.deepEqual(
    pruneManagedSelection(["queued-run", "completed-run", "missing-run"], runs),
    ["completed-run"]
  );
});

test("toggleManagedSelection and areAllManageableRunsSelected work together", () => {
  const once = toggleManagedSelection([], "completed-run");
  assert.deepEqual(once, ["completed-run"]);
  const twice = toggleManagedSelection(once, "failed-run");
  assert.equal(areAllManageableRunsSelected(runs, twice), true);
  assert.equal(areAllManageableRunsSelected(runs, once), false);
});

test("getSelectedRuns returns rows in table order", () => {
  assert.deepEqual(
    getSelectedRuns(runs, ["failed-run", "completed-run"]).map((run) => run.id),
    ["completed-run", "failed-run"]
  );
});

test("getRunHistoryRecall follows the same k fallback as run detail", () => {
  const top1Run = {
    ...runs[1],
    top_k: 1,
    metrics: {
      "content_recall@1": 0.73,
      content_mrr: 0.7
    }
  };
  const top3Run = {
    ...runs[1],
    top_k: 3,
    metrics: {
      "content_recall@1": 0.2,
      "content_recall@3": 0.8,
      content_mrr: 0.7
    }
  };
  const legacyRun = {
    ...runs[1],
    top_k: 3,
    metrics: {
      "document_recall@3": 0.6,
      document_mrr: 0.5
    }
  };

  assert.equal(getRunHistoryMetricK(top1Run), 1);
  assert.equal(getRunHistoryMetricK(top3Run), 3);
  assert.equal(getRunHistoryMetricK({ ...runs[1], top_k: 10 }), 5);
  assert.deepEqual(getRunHistoryRecallMetric(top1Run), { axis: "content", k: 1, value: 0.73 });
  assert.deepEqual(getRunHistoryRecallMetric(legacyRun), { axis: "document", k: 3, value: 0.6 });
  assert.equal(getRunHistoryRecall(top3Run), 0.8);
  assert.equal(getRunHistoryRecall(legacyRun), 0.6);
  assert.equal(rankedRunMetric({ "content_recall@1": 0.4 }, "content", "recall", 3), 0.4);
});

test("buildBulkDeleteMessage reflects selection size", () => {
  assert.match(buildBulkDeleteMessage([]), /没有选中/);
  assert.match(buildBulkDeleteMessage([runs[1]]), /1 条历史记录/);
  assert.match(buildBulkDeleteMessage([runs[1], runs[2]]), /2 条历史记录/);
});
