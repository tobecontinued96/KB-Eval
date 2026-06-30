import test from "node:test";
import assert from "node:assert/strict";
import {
  compareRunDecision,
  formatDecisionScore,
  scoreRunDecision
} from "../../.tmp/test-build/pages/runCompareScoring.js";

function run(overrides = {}) {
  return {
    id: "run",
    name: "评测运行",
    status: "completed",
    created_at: "2026-06-24T10:00:00Z",
    eval_file: "datasets/eval.jsonl",
    top_k: 5,
    sample_count: 80,
    query_count: 80,
    metrics: {},
    ...overrides
  };
}

test("scoreRunDecision grades strong content metrics as excellent", () => {
  const decision = scoreRunDecision(
    run({
      metrics: {
        "content_recall@5": 0.92,
        content_mrr: 0.9,
        "content_ndcg@5": 0.91,
        "section_recall@5": 0.88,
        "document_recall@5": 0.95,
        "keyword_recall@5": 0.8,
        empty_result_rate: 0,
        p95_latency_ms: 1800,
        total_queries: 80,
        completed_queries: 80,
        error_queries: 0
      }
    })
  );

  assert.equal(decision.grade, "优秀");
  assert.equal(decision.riskLabel, "通过");
  assert.equal(decision.weakness, "无明显短板");
  assert.equal(formatDecisionScore(decision.score), "90.5");
});

test("scoreRunDecision surfaces hard risks before a pretty average", () => {
  const decision = scoreRunDecision(
    run({
      metrics: {
        "content_recall@5": 0.58,
        content_mrr: 0.82,
        "content_ndcg@5": 0.84,
        "section_recall@5": 0.8,
        "document_recall@5": 0.88,
        "keyword_recall@5": 0.75,
        empty_result_rate: 0.12,
        p95_latency_ms: 5200,
        total_queries: 50,
        completed_queries: 49,
        error_queries: 1
      }
    })
  );

  assert.equal(decision.grade, "不可用");
  assert.equal(decision.riskLabel, "阻断");
  assert.equal(decision.weakness, "内容召回不足");
  assert.match(decision.riskItems.join("；"), /错误 1 条/);
});

test("scoreRunDecision marks small runs as quick-reference evidence", () => {
  const decision = scoreRunDecision(
    run({
      sample_count: 12,
      query_count: 12,
      metrics: {
        "content_recall@5": 0.9,
        content_mrr: 0.87,
        "content_ndcg@5": 0.86,
        "section_recall@5": 0.82,
        "document_recall@5": 0.9,
        "keyword_recall@5": 0.78,
        total_queries: 12,
        completed_queries: 12,
        error_queries: 0,
        empty_result_rate: 0,
        p95_latency_ms: 1500
      }
    })
  );

  assert.equal(decision.confidence, "快测参考");
  assert.equal(decision.riskLabel, "需复核");
  assert.match(decision.riskItems.join("；"), /样本偏少 12 条/);
});

test("compareRunDecision sorts by comprehensive score before tie-breakers", () => {
  const fasterButWeaker = run({
    id: "fast",
    created_at: "2026-06-24T09:00:00Z",
    metrics: {
      "content_recall@5": 0.8,
      content_mrr: 0.8,
      "content_ndcg@5": 0.8,
      "section_recall@5": 0.8,
      "document_recall@5": 0.8,
      "keyword_recall@5": 0.8,
      total_queries: 60,
      completed_queries: 60,
      p95_latency_ms: 600
    }
  });
  const slowerButBetter = run({
    id: "better",
    created_at: "2026-06-24T08:00:00Z",
    metrics: {
      "content_recall@5": 0.9,
      content_mrr: 0.9,
      "content_ndcg@5": 0.9,
      "section_recall@5": 0.9,
      "document_recall@5": 0.9,
      "keyword_recall@5": 0.9,
      total_queries: 60,
      completed_queries: 60,
      p95_latency_ms: 1800
    }
  });

  assert.equal([fasterButWeaker, slowerButBetter].sort(compareRunDecision)[0].id, "better");
});
