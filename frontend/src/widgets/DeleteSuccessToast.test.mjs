import test from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import {
  DELETE_SUCCESS_TOAST_DURATION_MS,
  DeleteSuccessToast
} from "../../.tmp/test-build/widgets/DeleteSuccessToast.js";

test("DeleteSuccessToast announces deletion success for 1.5 seconds", () => {
  assert.equal(DELETE_SUCCESS_TOAST_DURATION_MS, 1500);

  const markup = renderToStaticMarkup(
    React.createElement(DeleteSuccessToast, {
      datasetName: "华为 S1720 知识库评测集",
      onClose() {}
    })
  );

  assert.match(markup, /role="status"/);
  assert.match(markup, /删除成功/);
  assert.match(markup, /华为 S1720 知识库评测集/);
  assert.match(markup, /备份已保留/);
});
