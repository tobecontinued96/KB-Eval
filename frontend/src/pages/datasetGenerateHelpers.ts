import type { GenerateDatasetPayload } from "../types";

export type GeneratorStepState = "done" | "active" | "blocked";

export interface GeneratorStepItem {
  key: "source" | "identity" | "generate";
  label: string;
  description: string;
  state: GeneratorStepState;
}

export interface ScenarioDistributionPreviewItem {
  name: string;
  count: number;
  percent: number;
}

export interface ScenarioDistributionPreview {
  items: ScenarioDistributionPreviewItem[];
  hiddenCount: number;
  hiddenSampleTotal: number;
  total: number;
}

export interface DatasetSummaryTileInput {
  sampleCount: number;
  scenarioCount: number;
  updatedAtLabel: string;
}

export interface DatasetSummaryTile {
  key: "samples" | "scenarios" | "updated";
  label: string;
  value: string;
}

export function buildGeneratorStepItems(
  form: Pick<GenerateDatasetPayload, "vendor" | "model" | "output_name" | "max_samples">,
  selectedFileCount: number
): GeneratorStepItem[] {
  const hasSource = selectedFileCount > 0;
  const hasIdentity = Boolean(form.vendor.trim() && form.model.trim());
  const hasOutput = Boolean(form.output_name.trim() && form.max_samples > 0);

  return [
    {
      key: "source",
      label: "选择源文档",
      description: hasSource ? `已选择 ${selectedFileCount} 个源文件` : "先选择 PDF 或 Markdown 源文件",
      state: hasSource ? "done" : "active"
    },
    {
      key: "identity",
      label: "校准知识库",
      description: hasIdentity ? `${form.vendor} / ${form.model}` : "确认厂商与型号",
      state: hasIdentity ? "done" : hasSource ? "active" : "blocked"
    },
    {
      key: "generate",
      label: "生成并审核",
      description: hasOutput ? form.output_name : "补齐输出文件名和样本上限",
      state: hasSource && hasIdentity && hasOutput ? "active" : "blocked"
    }
  ];
}

export function buildScenarioDistributionPreview(
  distribution: Record<string, number>,
  limit = Number.POSITIVE_INFINITY
): ScenarioDistributionPreview {
  const entries = Object.entries(distribution)
    .map(([name, count], index) => ({ name, count, index }))
    .filter((item) => item.count > 0)
    .sort((a, b) => {
      if (a.count !== b.count) return b.count - a.count;
      return a.index - b.index;
    });
  const total = entries.reduce((sum, item) => sum + item.count, 0);
  const visible = entries.slice(0, limit);
  const hidden = entries.slice(limit);

  return {
    items: visible.map((item) => ({
      name: item.name,
      count: item.count,
      percent: total > 0 ? Math.round((item.count / total) * 100) : 0
    })),
    hiddenCount: hidden.length,
    hiddenSampleTotal: hidden.reduce((sum, item) => sum + item.count, 0),
    total
  };
}

export function buildDatasetSummaryTiles({
  sampleCount,
  scenarioCount,
  updatedAtLabel
}: DatasetSummaryTileInput): DatasetSummaryTile[] {
  return [
    { key: "samples", label: "样本", value: String(sampleCount) },
    { key: "scenarios", label: "场景", value: String(scenarioCount) },
    { key: "updated", label: "更新", value: updatedAtLabel }
  ];
}
