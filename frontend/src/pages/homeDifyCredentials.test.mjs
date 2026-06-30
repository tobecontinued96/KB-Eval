import test from "node:test";
import assert from "node:assert/strict";
import {
  applyDifyConnectionConfig,
  findMatchingDifyConnection,
  formatDifyConnectionOption,
  hasCompleteDifyConnection,
  isSameDifyConnection,
  resolveDifyConnectionCredentials
} from "../../.tmp/test-build/pages/homeDifyCredentials.js";

const baseForm = {
  name: "",
  dify_base_url: "",
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
  embedding_model: "",
  rerank_model: ""
};

const savedConnection = {
  id: "conn-1",
  dify_base_url: "http://saved:5001/v1",
  dify_api_key: "saved-secret-key",
  dify_api_key_masked: "save...-key",
  created_at: "2026-06-26T08:00:00+08:00",
  last_used_at: "2026-06-26T09:00:00+08:00",
  use_count: 2
};

test("applyDifyConnectionConfig fills url/key as a pair and clears stale KB state", () => {
  const next = applyDifyConnectionConfig(
    {
      ...baseForm,
      dataset_id: "old-kb",
      embedding_model: "old-embedding",
      rerank_model: "old-rerank"
    },
    savedConnection
  );
  assert.equal(next.dify_base_url, "http://saved:5001/v1");
  assert.equal(next.dify_api_key, "saved-secret-key");
  assert.equal(next.dataset_id, "");
  assert.equal(next.embedding_model, "");
  assert.equal(next.rerank_model, "");
  assert.equal(next.eval_file, "datasets/huawei_s1720.jsonl");
});

test("applyDifyConnectionConfig returns original form when config is null", () => {
  const next = applyDifyConnectionConfig(baseForm, null);
  assert.equal(next, baseForm);
});

test("hasCompleteDifyConnection requires both url and key", () => {
  assert.equal(hasCompleteDifyConnection({ dify_base_url: "http://a/v1", dify_api_key: "k" }), true);
  assert.equal(hasCompleteDifyConnection({ dify_base_url: "http://a/v1", dify_api_key: "" }), false);
  assert.equal(hasCompleteDifyConnection({ dify_base_url: "", dify_api_key: "k" }), false);
  assert.equal(hasCompleteDifyConnection({ dify_base_url: "  ", dify_api_key: "  " }), false);
});

test("isSameDifyConnection matches the exact url/key pair after trimming", () => {
  assert.equal(
    isSameDifyConnection(
      { dify_base_url: " http://saved:5001/v1 ", dify_api_key: " saved-secret-key " },
      savedConnection
    ),
    true
  );
  assert.equal(
    isSameDifyConnection(
      { dify_base_url: "http://saved:5001/v1", dify_api_key: "other-key" },
      savedConnection
    ),
    false
  );
  assert.equal(isSameDifyConnection(baseForm, null), false);
});

test("findMatchingDifyConnection returns the saved config for the current pair", () => {
  const other = {
    ...savedConnection,
    id: "conn-2",
    dify_base_url: "http://other/v1",
    dify_api_key: "other-key"
  };
  const matched = findMatchingDifyConnection(
    { dify_base_url: "http://saved:5001/v1", dify_api_key: "saved-secret-key" },
    [other, savedConnection]
  );
  assert.equal(matched?.id, "conn-1");
});

test("formatDifyConnectionOption keeps url and masked key together", () => {
  assert.equal(formatDifyConnectionOption(savedConnection), "http://saved:5001/v1 · save...-key");
});

test("resolveDifyConnectionCredentials lets a selected history item override stale form state", () => {
  const credentials = resolveDifyConnectionCredentials(
    {
      ...baseForm,
      dify_base_url: "http://stale/v1",
      dify_api_key: "stale-key"
    },
    savedConnection
  );

  assert.deepEqual(credentials, {
    difyBaseUrl: "http://saved:5001/v1",
    difyApiKey: "saved-secret-key"
  });
});
