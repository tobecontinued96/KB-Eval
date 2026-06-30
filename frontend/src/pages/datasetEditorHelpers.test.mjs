import test from "node:test";
import assert from "node:assert/strict";
import {
  buildFilterOptions,
  buildDatasetDiscardConfirmation,
  buildDatasetSaveConfirmation,
  highlightSegments,
  nextActiveIndexAfterDelete,
  pageForIndex,
  rowMatchesFilters,
  toStringList
} from "../../.tmp/test-build/pages/datasetEditorHelpers.js";

const row = {
  id: "HW-S1720-EV-001",
  vendor: "华为",
  model: "S1720",
  scenario_type: "故障恢复",
  topic: "关于本章",
  difficulty: "基础",
  question: "华为 S1720 遇到关于本章相关问题时应该如何处理？",
  evaluation_focus: "应命中 MinERU_markdown_01-01 常见系统操作章节",
  expected_documents: ["MinERU_markdown_01-01.pdf"],
  expected_sections: ["关于本章"],
  expected_keywords: ["本章", "告警"],
  alternative_queries: ["如何处理本章问题"]
};

test("toStringList normalizes arrays and delimited strings", () => {
  assert.deepEqual(toStringList(["a", 1, "b"]), ["a", "b"]);
  assert.deepEqual(toStringList("a, b\nc"), ["a", "b", "c"]);
  assert.deepEqual(toStringList(null), []);
});

test("buildFilterOptions returns sorted unique vendor scenario and difficulty options", () => {
  const options = buildFilterOptions([
    row,
    { ...row, vendor: "中兴", scenario_type: "安装部署", difficulty: "高级" },
    { ...row, vendor: "华为", scenario_type: "故障恢复", difficulty: "基础" }
  ]);

  assert.deepEqual(options.vendors, ["华为", "中兴"]);
  assert.deepEqual(options.scenarios, ["安装部署", "故障恢复"]);
  assert.deepEqual(options.difficulties, ["基础", "高级"]);
});

test("rowMatchesFilters combines keyword dropdown toggles modified and errors", () => {
  const modifiedRows = new Set([0]);
  const errorCounts = new Map([[0, 2]]);

  assert.equal(rowMatchesFilters(row, 0, {
    keyword: "本章",
    scenario: "故障恢复",
    difficulty: "基础",
    vendor: "华为",
    modifiedOnly: true,
    errorOnly: true
  }, modifiedRows, errorCounts), true);

  assert.equal(rowMatchesFilters(row, 0, {
    keyword: "不存在",
    scenario: "故障恢复",
    difficulty: "基础",
    vendor: "华为",
    modifiedOnly: true,
    errorOnly: true
  }, modifiedRows, errorCounts), false);

  assert.equal(rowMatchesFilters(row, 1, {
    keyword: "本章",
    scenario: "",
    difficulty: "",
    vendor: "",
    modifiedOnly: true,
    errorOnly: false
  }, modifiedRows, errorCounts), false);
});

test("highlightSegments marks case insensitive matches without dropping text", () => {
  assert.deepEqual(highlightSegments("S1720 本章 S1720", "s1720"), [
    { text: "S1720", match: true },
    { text: " 本章 ", match: false },
    { text: "S1720", match: true }
  ]);
  assert.deepEqual(highlightSegments("无关键词", ""), [{ text: "无关键词", match: false }]);
});

test("nextActiveIndexAfterDelete keeps row detail anchored to a neighbor", () => {
  assert.equal(nextActiveIndexAfterDelete(0, 1), null);
  assert.equal(nextActiveIndexAfterDelete(0, 3), 0);
  assert.equal(nextActiveIndexAfterDelete(2, 3), 1);
});

test("pageForIndex returns one based page numbers", () => {
  assert.equal(pageForIndex(0, 20), 1);
  assert.equal(pageForIndex(20, 20), 2);
  assert.equal(pageForIndex(79, 20), 4);
});

test("buildDatasetSaveConfirmation explains draft versus main target", () => {
  assert.equal(
    buildDatasetSaveConfirmation({
      target: "draft",
      changeCount: 3,
      rowCount: 20,
      localErrorCount: 2
    }),
    "确认把当前 3 / 20 行的修改保存到草稿？\n（注意：仍有 2 条本地校验告警，服务端会再次校验。）"
  );

  assert.equal(
    buildDatasetSaveConfirmation({
      target: "main",
      changeCount: 1,
      rowCount: 5,
      localErrorCount: 0
    }),
    "确认把当前 1 / 5 行的修改直接覆盖到主评测集？此操作会覆盖原文件。"
  );
});

test("buildDatasetDiscardConfirmation mentions local changes only when present", () => {
  assert.equal(
    buildDatasetDiscardConfirmation(4),
    "确认放弃当前 4 行的本地修改，并重新加载评测集？未保存的改动会丢失。"
  );
  assert.equal(buildDatasetDiscardConfirmation(0), "确认重新加载评测集吗？");
});
