import test from "node:test";
import assert from "node:assert/strict";
import {
  buildDatasetSummaryTiles,
  buildScenarioDistributionPreview,
  buildGeneratorStepItems
} from "../../.tmp/test-build/pages/datasetGenerateHelpers.js";

const baseForm = {
  vendor: "",
  model: "",
  output_name: "",
  max_samples: 80
};

test("buildGeneratorStepItems marks source identity and generation readiness", () => {
  assert.deepEqual(buildGeneratorStepItems(baseForm, 0).map((item) => item.state), [
    "active",
    "blocked",
    "blocked"
  ]);

  assert.deepEqual(
    buildGeneratorStepItems({ ...baseForm, vendor: "华为", model: "S1720", output_name: "huawei_s1720.jsonl" }, 3)
      .map((item) => item.state),
    ["done", "done", "active"]
  );
});

test("buildScenarioDistributionPreview keeps top scenarios and folds the rest", () => {
  const preview = buildScenarioDistributionPreview({
    "DHCP 运维": 1,
    "ARP 配置": 2,
    "ACL 配置": 3,
    "QoS 配置": 2,
    "AAA 配置": 1,
    "路由配置": 1,
    "VLAN 配置": 4
  }, 5);

  assert.deepEqual(preview.items.map((item) => item.name), [
    "VLAN 配置",
    "ACL 配置",
    "ARP 配置",
    "QoS 配置",
    "DHCP 运维"
  ]);
  assert.equal(preview.hiddenCount, 2);
  assert.equal(preview.hiddenSampleTotal, 2);
  assert.equal(preview.total, 14);
});

test("buildScenarioDistributionPreview returns all scenarios by default for a scrollable viewport", () => {
  const preview = buildScenarioDistributionPreview({
    "Scenario A": 6,
    "Scenario B": 5,
    "Scenario C": 4,
    "Scenario D": 3,
    "Scenario E": 2,
    "Scenario F": 1
  });

  assert.equal(preview.items.length, 6);
  assert.equal(preview.hiddenCount, 0);
  assert.equal(preview.hiddenSampleTotal, 0);
});

test("buildDatasetSummaryTiles returns compact overview tiles in display order", () => {
  assert.deepEqual(
    buildDatasetSummaryTiles({
      sampleCount: 70,
      scenarioCount: 20,
      updatedAtLabel: "06/12"
    }),
    [
      { key: "samples", label: "样本", value: "70" },
      { key: "scenarios", label: "场景", value: "20" },
      { key: "updated", label: "更新", value: "06/12" }
    ]
  );
});
