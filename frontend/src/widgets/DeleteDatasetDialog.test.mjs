import test from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { DeleteDatasetDialog } from "../../.tmp/test-build/widgets/DeleteDatasetDialog.js";

test("DeleteDatasetDialog renders request errors inside the visible dialog", () => {
  const markup = renderToStaticMarkup(
    React.createElement(DeleteDatasetDialog, {
      datasetName: "sample",
      datasetPath: "datasets/generated/sample.jsonl",
      sampleCount: 12,
      error: "评测集文件不存在",
      onCancel() {},
      onConfirm() {}
    })
  );

  assert.match(markup, /评测集文件不存在/);
  assert.match(markup, /role="alert"/);
});
