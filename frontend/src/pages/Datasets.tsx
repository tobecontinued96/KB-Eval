import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import {
  Database,
  Edit3,
  FileText,
  FolderOpen,
  Loader2,
  RefreshCw,
  Trash2,
  Wand2
} from "lucide-react";
import { deleteDataset, generateDatasetFromFiles, listDatasets } from "../api";
import type { DatasetInfo, GenerateDatasetPayload, GenerateDatasetResponse } from "../types";
import { describeError, errorPrefix } from "../errorCodes";
import { showErrorToast } from "../widgets/ErrorToast";
import { formatDateTime } from "../utils";
import { DeleteDatasetDialog } from "../widgets/DeleteDatasetDialog";
import {
  DELETE_SUCCESS_TOAST_DURATION_MS,
  DeleteSuccessToast
} from "../widgets/DeleteSuccessToast";
import { Field } from "../widgets/Field";
import { PanelHeader } from "../widgets/PanelHeader";
import { DatalistInput, StandardSelect } from "../widgets/StandardSelect";
import {
  buildDatasetSummaryTiles,
  buildGeneratorStepItems,
  buildScenarioDistributionPreview
} from "./datasetGenerateHelpers";

function parseSourceDirectory(value: string) {
  const parts = value.split(/[\\/]+/).map((item) => item.trim()).filter(Boolean);
  const model = parts[parts.length - 1] || "";
  const vendor = parts[parts.length - 2] || "";
  return { vendor, model };
}

function defaultOutputName(vendor: string, model: string) {
  const value = [vendor, model].filter(Boolean).join("_") || "generated";
  return `${value}_generated.jsonl`;
}

type DesktopFile = File & { path?: string };

const commonVendors = ["华为", "思科", "新华三", "锐捷", "中兴", "Aruba", "Juniper", "HPE", "Dell", "TP-Link", "Fortinet"];

function sourceFilePath(file: File) {
  return (file as DesktopFile).path || file.webkitRelativePath || file.name;
}

function inferSourceSelection(files: File[]) {
  const candidates = new Map<string, { vendor: string; model: string; directory: string }>();
  const modelOnly = new Set<string>();

  files.forEach((file) => {
    const rawPath = sourceFilePath(file);
    const parts = rawPath.split(/[\\/]+/).map((part) => part.trim()).filter(Boolean);
    if (parts.length > 0) parts.pop();
    if (parts.at(-1)?.toLowerCase() === "md") parts.pop();
    if (parts.length >= 2) {
      const vendor = parts.at(-2) || "";
      const model = parts.at(-1) || "";
      candidates.set(`${vendor}::${model}`, {
        vendor,
        model,
        directory: `${vendor}/${model}`
      });
    } else if (parts.length === 1) {
      modelOnly.add(parts[0]);
    }
  });

  if (candidates.size > 1 || modelOnly.size > 1) {
    throw new Error("所选目录包含多个厂商或型号，请一次只选择一个知识库源目录");
  }
  if (candidates.size === 1) {
    const selection = [...candidates.values()][0];
    return { vendor: "", model: selection.model, directory: selection.model };
  }

  const model = [...modelOnly][0] || "";
  return {
    vendor: "",
    model,
    directory: model
  };
}

const defaultGenerateForm: GenerateDatasetPayload = {
  source_directory: "",
  source_files: [],
  vendor: "",
  model: "",
  output_name: "",
  document_name: "",
  max_samples: 80,
  min_section_chars: 80,
  reuse_existing_markdown: true,
  markitdown_command: "",
  markitdown_timeout_seconds: 300,
  overwrite: false
};

export function Datasets() {
  const navigate = useNavigate();
  const [datasets, setDatasets] = useState<DatasetInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [generating, setGenerating] = useState(false);
  const [generateForm, setGenerateForm] = useState<GenerateDatasetPayload>(defaultGenerateForm);
  const [selectedSourceFiles, setSelectedSourceFiles] = useState<File[]>([]);
  const [generateResult, setGenerateResult] = useState<GenerateDatasetResponse | null>(null);
  const [generateError, setGenerateError] = useState("");
  // 生成成功后记录新草稿，用于展示"去审核草稿"入口。
  const [pendingDataset, setPendingDataset] = useState<{ path: string; name: string } | null>(null);
  // 当前在"概览"中查看的评测集 id；列表为空时为 null
  const [selectedDatasetId, setSelectedDatasetId] = useState<string | null>(null);
  // 待删除的评测集信息（被设置后弹出"输入名称确认"对话框）
  const [pendingDelete, setPendingDelete] = useState<DatasetInfo | null>(null);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const [deleteError, setDeleteError] = useState("");
  const [lastDeleted, setLastDeleted] = useState<{ name: string } | null>(null);

  async function loadDatasets() {
    try {
      setError("");
      const result = await listDatasets();
      setDatasets(result.items);
      // 当前选中 id 失效时回退到列表第一项
      setSelectedDatasetId((current) => {
        if (current && result.items.some((item) => item.id === current)) return current;
        return result.items[0]?.id ?? null;
      });
    } catch (err) {
      const e = err as Error & { code?: string; status?: number };
      showErrorToast(describeError(e.code, e.status, e.message));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void loadDatasets();
  }, []);

  useEffect(() => {
    if (!lastDeleted) return;
    const timer = window.setTimeout(() => setLastDeleted(null), DELETE_SUCCESS_TOAST_DURATION_MS);
    return () => window.clearTimeout(timer);
  }, [lastDeleted]);

  // 当前"概览"中展示的评测集：根据 selectedDatasetId 选取；失效时回退到第一项
  const featuredDataset =
    datasets.find((dataset) => dataset.id === selectedDatasetId) ?? datasets[0] ?? null;
  const vendorOptions = useMemo(
    () => Array.from(new Set([...commonVendors, ...datasets.map((d) => d.vendor).filter(Boolean)])),
    [datasets]
  );

  async function handleGenerateDataset(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (selectedSourceFiles.length === 0) {
      setGenerateError("请先选择包含 PDF 的源文档目录");
      return;
    }
    setGenerating(true);
    setGenerateError("");
    setGenerateResult(null);
    setPendingDataset(null);
    try {
      const payload = {
        ...generateForm,
        vendor: generateForm.vendor,
        model: generateForm.model || parseSourceDirectory(generateForm.source_directory).model,
        source_files: generateForm.source_files.filter((item) => item.trim()),
        output_name: generateForm.output_name.trim()
      };
      if (!payload.vendor || !payload.model) {
        throw new Error("无法从所选目录解析厂商和型号，请校正厂商与型号后再生成");
      }
      const result = await generateDatasetFromFiles(payload, selectedSourceFiles);
      setGenerateResult(result);
      // 刷新本地评测集列表，让"概览"区出现新数据集
      const refreshed = await listDatasets();
      setDatasets(refreshed.items);
      // 准备审核入口，生成后的下一步必须是人工复核。
      setPendingDataset({ path: result.dataset.path, name: result.dataset.name });
      navigate(`/datasets/${encodeURIComponent(result.dataset.path)}/editor`);
    } catch (err) {
      const e = err as Error & { code?: string; status?: number };
      setGenerateError(errorPrefix("run") + describeError(e.code, e.status, e.message).title);
    } finally {
      setGenerating(false);
    }
  }

  function handleSelectDirectory(files: File[]) {
    setGenerateError("");
    setGenerateResult(null);
    setPendingDataset(null);
    try {
      const acceptedFiles = files.filter((file) => /\.(pdf|md|markdown)$/i.test(file.name));
      if (acceptedFiles.length === 0) {
        throw new Error("所选目录中没有 PDF 或 Markdown 文件");
      }
      const selection = inferSourceSelection(acceptedFiles);
      setSelectedSourceFiles(acceptedFiles);
      setGenerateForm((current) => {
        const parsedCurrent = parseSourceDirectory(current.source_directory);
        const currentVendor = current.vendor || parsedCurrent.vendor;
        const currentModel = current.model || parsedCurrent.model;
        const outputWasAutomatic =
          !current.output_name.trim()
          || current.output_name === defaultOutputName(currentVendor, currentModel);
        return {
          ...current,
          source_directory: selection.model,
          vendor: "",
          model: selection.model,
          output_name: outputWasAutomatic
            ? defaultOutputName("", selection.model)
            : current.output_name
        };
      });
    } catch (err) {
      setGenerateError(err instanceof Error ? err.message : "选择源文档目录失败");
    }
  }

  // 单个 PDF 上传：浏览器非 webkitdirectory 模式下没有 webkitRelativePath，
  // 后端 infer_vendor_model_from_relative_paths 解不出厂商/型号，
  // 这里把"厂商/型号/文件名"挂到 File 对象上,api.generateDatasetFromFiles 会优先用它。
  function handleSelectSinglePdf(files: File[]) {
    setGenerateError("");
    setGenerateResult(null);
    setPendingDataset(null);
    try {
      const acceptedFiles = files.filter((file) => /\.pdf$/i.test(file.name));
      if (acceptedFiles.length === 0) {
        throw new Error("请选择 PDF 文件");
      }
      const vendor = generateForm.vendor.trim() || "unknown";
      const model = generateForm.model.trim() || "manual";
      // 同步写回表单,避免用户没填导致后端校验失败
      setGenerateForm((current) => ({
        ...current,
        vendor,
        model,
      }));
      // 把厂商/型号/文件名作为相对路径挂到 File 上,api 层会优先取这个
      acceptedFiles.forEach((file) => {
        (file as DesktopFile & { _syntheticRelativePath?: string })._syntheticRelativePath =
          `${vendor}/${model}/${file.name}`;
      });
      setSelectedSourceFiles(acceptedFiles);
    } catch (err) {
      setGenerateError(err instanceof Error ? err.message : "选择 PDF 失败");
    }
  }

  function handleGenerateFormChange(nextForm: GenerateDatasetPayload) {
    setGenerateForm(nextForm);
    setGenerateResult(null);
    setGenerateError("");
    setPendingDataset(null);
  }

  function handleRequestDelete(dataset: DatasetInfo) {
    setDeleteError("");
    setLastDeleted(null);
    setPendingDelete(dataset);
  }

  function handleCancelDelete() {
    if (deleteBusy) return;
    setPendingDelete(null);
    setDeleteError("");
  }

  async function handleConfirmDelete() {
    const target = pendingDelete;
    if (!target) return;
    setDeleteBusy(true);
    setDeleteError("");
    try {
      await deleteDataset(target.path);
      setLastDeleted({ name: target.name });
      setPendingDelete(null);
      // 删除成功后刷新列表；若删的就是当前概览项，selectedDatasetId 也会回退到第一项
      await loadDatasets();
    } catch (err) {
      const e = err as Error & { code?: string; status?: number };
      setDeleteError(errorPrefix("delete") + describeError(e.code, e.status, e.message).title);
    } finally {
      setDeleteBusy(false);
    }
  }

  return (
    <div className="dashboard-grid">
      <div className="workspace-stack full-span">
        <DatasetSummary
          dataset={featuredDataset}
          datasets={datasets}
          loading={loading}
          error={error}
          onSelect={setSelectedDatasetId}
          onDelete={handleRequestDelete}
          onRetry={() => void loadDatasets()}
        />

        <DatasetGeneratorPanel
          form={generateForm}
          result={generateResult}
          error={generateError}
          generating={generating}
          selectedFiles={selectedSourceFiles}
          vendorOptions={vendorOptions}
          onChange={handleGenerateFormChange}
          onSelectDirectory={handleSelectDirectory}
          onSelectSinglePdf={handleSelectSinglePdf}
          onSubmit={handleGenerateDataset}
        />

        {pendingDataset && (
          <section className="panel compact-panel pending-dataset-panel">
            <div className="pending-dataset-strip">
              <div>
                <strong>已生成草稿：{pendingDataset.name}</strong>
                <div className="pending-dataset-meta">
                  {pendingDataset.path}
                </div>
                <div className="pending-dataset-warning">
                  状态：草稿待审核。请进入编辑器逐行复核，审核通过前不能发起评测。
                </div>
              </div>
              <div className="action-row">
                <Link
                  className="primary-button"
                  to={`/datasets/${encodeURIComponent(pendingDataset.path)}/editor`}
                >
                  <Edit3 size={14} />
                  去审核草稿
                </Link>
              </div>
            </div>
          </section>
        )}

        {deleteError && (
          <section className="panel compact-panel">
            <div className="error-line">{deleteError}</div>
          </section>
        )}
      </div>

      {pendingDelete && (
        <DeleteDatasetDialog
          datasetName={pendingDelete.name}
          datasetPath={pendingDelete.path}
          sampleCount={pendingDelete.sample_count}
          busy={deleteBusy}
          error={deleteError}
          onCancel={handleCancelDelete}
          onConfirm={handleConfirmDelete}
        />
      )}
      {lastDeleted && (
        <DeleteSuccessToast
          datasetName={lastDeleted.name}
          onClose={() => setLastDeleted(null)}
        />
      )}
    </div>
  );
}

function reviewBadgeFor(status: string): { cls: string; text: string } {
  switch (status) {
    case "draft":
      return { cls: "draft", text: "草稿待审" };
    case "reviewed":
      return { cls: "reviewed", text: "已审核" };
    default:
      return { cls: "unreviewed", text: "未审核" };
  }
}

function DatasetSummary({
  dataset,
  datasets,
  loading,
  error,
  onSelect,
  onDelete,
  onRetry
}: {
  dataset: DatasetInfo | null;
  datasets: DatasetInfo[];
  loading: boolean;
  error: string;
  onSelect: (id: string) => void;
  onDelete: (dataset: DatasetInfo) => void;
  onRetry: () => void;
}) {
  if (error) {
    return (
      <section className="panel dataset-panel">
        <PanelHeader icon={<Database size={18} />} title="评测集概览" subtitle="加载失败" />
        <div className="empty-state compact">
          <div className="error-line full-width">{error}</div>
          <div className="empty-state-actions">
            <button type="button" className="ghost-button" onClick={onRetry}>
              <RefreshCw size={14} aria-hidden="true" />
              重试
            </button>
          </div>
        </div>
      </section>
    );
  }
  if (loading) {
    return (
      <section className="panel dataset-panel">
        <PanelHeader icon={<Database size={18} />} title="评测集概览" subtitle="加载中..." />
        <div className="empty-state">
          <Loader2 size={24} className="spin" />
          正在加载评测集...
        </div>
      </section>
    );
  }
  if (!dataset) {
    return (
      <section className="panel dataset-panel">
        <PanelHeader icon={<Database size={18} />} title="评测集概览" subtitle="暂无可用评测集" />
      </section>
    );
  }

  const distribution = dataset.scenario_distribution || {};
  const scenarioPreview = buildScenarioDistributionPreview(distribution);
  const summaryTiles = buildDatasetSummaryTiles({
    sampleCount: dataset.sample_count,
    scenarioCount: Object.keys(distribution).length || 0,
    updatedAtLabel: formatDateTime(dataset.updated_at, "date")
  });

  const reviewStatus = (dataset.review_status || "unreviewed") as
    | "unreviewed"
    | "draft"
    | "reviewed"
    | string;
  const reviewBadge = reviewBadgeFor(reviewStatus);

  return (
    <section className="panel dataset-panel">
      <PanelHeader
        icon={<Database size={18} />}
        title="评测集概览"
        subtitle={`${dataset.vendor} / ${dataset.model}`}
      />
      <div className="dataset-summary-layout">
        {reviewStatus !== "reviewed" && (
          <div className="dataset-review-banner compact dataset-summary-alert" data-status={reviewStatus}>
            <span className={`ui-badge dataset-review-badge ${reviewBadge.cls}`}>{reviewBadge.text}</span>
            <span className="dataset-review-banner-text">
              {reviewStatus === "draft" ? (
                <>
                  存在待审核草稿（{dataset.draft_path}）。请打开编辑器审核后点击
                  <strong>「标记为已审核」</strong>。
                </>
              ) : (
                <>旧版样本未走审核流程。如需启用，请打开编辑器并提交审核。</>
              )}
            </span>
          </div>
        )}

        <div className="dataset-current">
          <div className="dataset-current-heading">
            <div>
              <strong>当前评测集</strong>
              <span>选择需要查看或维护的评测集</span>
            </div>
            {reviewStatus === "reviewed" && (
              <span className={`ui-badge dataset-review-badge ${reviewBadge.cls}`}>{reviewBadge.text}</span>
            )}
          </div>
          <StandardSelect
            className="dataset-current-select"
            value={dataset.id}
            aria-label="当前评测集"
            title={`当前评测集：${dataset.name}（${dataset.vendor} / ${dataset.model}）`}
            onChange={(event) => onSelect(event.target.value)}
          >
            {datasets.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}（{item.vendor} / {item.model}）
              </option>
            ))}
          </StandardSelect>
          <div className="dataset-current-actions">
            <Link
              className="primary-button inline"
              to={`/datasets/${encodeURIComponent(dataset.path)}/editor`}
            >
              <Edit3 size={16} />
              编辑评测集
            </Link>
            <button
              type="button"
              className="ghost-button danger"
              onClick={() => onDelete(dataset)}
              title="删除当前评测集（需在弹窗中再次确认）"
            >
              <Trash2 size={16} />
              删除评测集
            </button>
          </div>
          <div className="dataset-current-meta">
            <span>{dataset.path}</span>
            {reviewStatus === "reviewed" && (
              <span>
                审核于 {dataset.reviewed_at ? formatDateTime(dataset.reviewed_at, "full") : "时间未记录"}
                {dataset.reviewed_by ? ` · ${dataset.reviewed_by}` : ""}
              </span>
            )}
          </div>
        </div>

        <div className="dataset-overview">
          <div className="dataset-summary-tiles" aria-label="评测集统计">
            {summaryTiles.map((tile) => (
              <div className="dataset-summary-tile" key={tile.key}>
                <strong>{tile.value}</strong>
                <span>{tile.label}</span>
              </div>
            ))}
          </div>
          <div className="scenario-compact">
            <div className="scenario-compact-head">
              <strong>场景分布</strong>
              <span>共 {scenarioPreview.items.length} 类</span>
            </div>
            <div className="scenario-bars compact">
              {scenarioPreview.items.map((item) => (
                <div className="scenario-row" key={item.name}>
                  <div className="scenario-meta">
                    <span>{item.name}</span>
                    <b>{item.count}</b>
                  </div>
                  <div className="bar-track">
                    <span style={{ width: `${item.percent}%` }} />
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}

function DatasetGeneratorPanel({
  form,
  result,
  error,
  generating,
  selectedFiles,
  vendorOptions,
  onChange,
  onSelectDirectory,
  onSelectSinglePdf,
  onSubmit
}: {
  form: GenerateDatasetPayload;
  result: GenerateDatasetResponse | null;
  error: string;
  generating: boolean;
  selectedFiles: File[];
  vendorOptions: string[];
  onChange: (form: GenerateDatasetPayload) => void;
  onSelectDirectory: (files: File[]) => void;
  onSelectSinglePdf?: (files: File[]) => void;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
}) {
  const directoryInputRef = useRef<HTMLInputElement>(null);
  const singlePdfInputRef = useRef<HTMLInputElement>(null);
  const parsed = parseSourceDirectory(form.source_directory);
  const vendor = form.vendor;
  const model = form.model || parsed.model;
  const knowledgeBaseName = `${vendor} ${model}`.trim();
  const skippedConversions = result?.mineru_conversions.filter((item) => item.status === "skipped") ?? [];
  const generatorSteps = buildGeneratorStepItems(form, selectedFiles.length);
  return (
    <section className="panel dataset-generator-panel">
      <PanelHeader
        icon={<Wand2 size={18} />}
        title="生成评测集"
        subtitle="选择源文档、校准知识库信息，再生成待审核的评测集"
      />
      <div className="generator-steps" aria-label="生成步骤">
        {generatorSteps.map((step, index) => (
          <div className={`generator-step ${step.state}`} key={step.key}>
            <span>{index + 1}</span>
            <div>
              <strong>{step.label}</strong>
              <small>{step.description}</small>
            </div>
          </div>
        ))}
      </div>
      <form className="generator-form" onSubmit={onSubmit}>
        <div className="generator-workbench">
          <div className="generator-column">
            <div className="directory-picker-row">
              <div className="selected-directory">
                <span>源文档目录</span>
                <strong>{form.source_directory || "未选择"}</strong>
              </div>
              <button
                className="ghost-button"
                type="button"
                onClick={() => directoryInputRef.current?.click()}
                disabled={generating}
              >
                <FolderOpen size={16} />
                选择文件夹
              </button>
              <button
                className="ghost-button"
                type="button"
                onClick={() => singlePdfInputRef.current?.click()}
                disabled={generating}
                title="未启用文件夹选择时,可用此按钮上传单个 PDF,需先在下方填好厂商/型号"
              >
                <FileText size={16} />
                选择 PDF
              </button>
              <input
                ref={(node) => {
                  directoryInputRef.current = node;
                  node?.setAttribute("webkitdirectory", "");
                  node?.setAttribute("directory", "");
                }}
                className="visually-hidden"
                type="file"
                multiple
                accept=".pdf,.md,.markdown"
                onChange={(event) => {
                  onSelectDirectory(Array.from(event.target.files || []));
                  event.target.value = "";
                }}
              />
              <input
                ref={(node) => {
                  singlePdfInputRef.current = node;
                }}
                className="visually-hidden"
                type="file"
                multiple
                accept=".pdf"
                onChange={(event) => {
                  onSelectSinglePdf?.(Array.from(event.target.files || []));
                  event.target.value = "";
                }}
              />
            </div>
            {selectedFiles.length > 0 && (
              <div className="selected-source-files">
                <span>已选择 {selectedFiles.length} 个源文件</span>
                <strong>{selectedFiles.slice(0, 3).map((file) => file.name).join("、")}</strong>
                {selectedFiles.length > 3 && <small>另有 {selectedFiles.length - 3} 个文件</small>}
              </div>
            )}
            <div className="form-row">
              <Field label="厂商">
                <DatalistInput
                  required
                  datalistId="kb-vendor-options"
                  options={vendorOptions}
                  value={form.vendor}
                  title={form.vendor ? `当前厂商：${form.vendor}` : "选择或输入厂商"}
                  onChange={(event) => {
                    const nextVendor = event.target.value;
                    const currentVendor = form.vendor;
                    const outputWasAutomatic =
                      !form.output_name.trim()
                      || form.output_name === defaultOutputName(currentVendor, model)
                      || form.output_name === defaultOutputName("", model);
                    onChange({
                      ...form,
                      vendor: nextVendor,
                      source_directory: [nextVendor, model].filter(Boolean).join("/"),
                      output_name: outputWasAutomatic ? defaultOutputName(nextVendor, model) : form.output_name
                    });
                  }}
                  placeholder="选择或输入厂商，例如：思科"
                />
              </Field>
              <Field label="型号（从文件夹识别）">
                <input
                  required
                  value={form.model}
                  onChange={(event) => {
                    const nextModel = event.target.value;
                    const outputWasAutomatic =
                      !form.output_name.trim()
                      || form.output_name === defaultOutputName(vendor, model)
                      || form.output_name === defaultOutputName("", model);
                    onChange({
                      ...form,
                      model: nextModel,
                      source_directory: [vendor, nextModel].filter(Boolean).join("/"),
                      output_name: outputWasAutomatic
                        ? defaultOutputName(vendor, nextModel)
                        : form.output_name
                    });
                  }}
                  placeholder={parsed.model || "例如：S1720"}
                />
              </Field>
            </div>
            <div className="generator-kv">
              <span><Database size={14} /> 知识库：{knowledgeBaseName || "待填写厂商和型号"}</span>
              <span><FolderOpen size={14} /> Markdown：{form.source_directory ? `${form.source_directory}/md` : "--"}</span>
            </div>
          </div>
          <div className="generator-column">
            <Field label="输出文件名">
              <input
                value={form.output_name}
                onChange={(event) => onChange({ ...form, output_name: event.target.value })}
                placeholder="huawei_s1720_generated.jsonl"
              />
            </Field>
            <div className="form-row">
              <Field label="样本上限">
                <input
                  type="number"
                  min={1}
                  max={300}
                  value={form.max_samples}
                  onChange={(event) => onChange({ ...form, max_samples: Number(event.target.value) })}
                />
              </Field>
              <Field label="最小章节长度">
                <input
                  type="number"
                  min={20}
                  value={form.min_section_chars}
                  onChange={(event) => onChange({ ...form, min_section_chars: Number(event.target.value) })}
                />
              </Field>
            </div>
            <div className="switch-row compact">
              <label className="switch-item">
                <input
                  type="checkbox"
                  checked={form.reuse_existing_markdown}
                  onChange={(event) => onChange({ ...form, reuse_existing_markdown: event.target.checked })}
                />
                <span>复用 md</span>
              </label>
              <label className="switch-item">
                <input
                  type="checkbox"
                  checked={form.overwrite}
                  onChange={(event) => onChange({ ...form, overwrite: event.target.checked })}
                />
                <span>覆盖原文件</span>
              </label>
            </div>
            <button className="primary-button" type="submit" disabled={generating}>
              {generating ? <Loader2 size={18} className="spin" /> : <FileText size={18} />}
              {generating ? "正在生成" : "生成评测集"}
            </button>
          </div>
        </div>
      </form>
      {result && (
        <div className="generator-result">
          <strong>{result.sample_count} 条样本已生成</strong>
          <span>{result.output_file}</span>
          <span>{result.markdown_output_dir}</span>
          {skippedConversions.length > 0 && (
            <div className="generator-skipped-list">
              <strong>超时跳过的 PDF：{skippedConversions.length}</strong>
              {skippedConversions.slice(0, 5).map((item) => (
                <span key={item.source_file}>{item.source_file}: {item.message || item.stderr_tail || "PDF 转换已跳过"}</span>
              ))}
            </div>
          )}
          <span>知识库名称校验：{result.knowledge_base_name}</span>
        </div>
      )}
    </section>
  );
}
