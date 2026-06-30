import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  AlertTriangle,
  ArrowLeft,
  CheckCircle2,
  ChevronDown,
  Download,
  FileText,
  Filter,
  Loader2,
  Plus,
  Save,
  Search,
  Tag,
  Trash2,
  X
} from "lucide-react";
import { exportDataset, getDatasetRows, saveDatasetRows, commitDatasetReview, deleteDataset, type RequestError } from "../api";
import type {
  DatasetRow,
  DatasetRowValidationError,
  DatasetRowsResponse
} from "../types";
import { formatDateTime } from "../utils";
import { ConfirmDialog, type ConfirmDialogProps } from "../widgets/ConfirmDialog";
import { DeleteDatasetDialog } from "../widgets/DeleteDatasetDialog";
import { showErrorToast } from "../widgets/ErrorToast";
import { PanelHeader } from "../widgets/PanelHeader";
import { DatalistInput, StandardSelect } from "../widgets/StandardSelect";
import {
  buildDatasetDiscardConfirmation,
  buildDatasetSaveConfirmation,
  buildFilterOptions,
  highlightSegments,
  nextActiveIndexAfterDelete,
  pageForIndex,
  rowMatchesFilters,
  toStringList,
  type DatasetEditorFilters
} from "./datasetEditorHelpers";

type CellField =
  | "id"
  | "vendor"
  | "model"
  | "scenario_type"
  | "topic"
  | "difficulty"
  | "question"
  | "evaluation_focus";

const LIST_FIELDS = [
  "expected_documents",
  "expected_sections",
  "expected_keywords",
  "alternative_queries"
] as const;

type ListField = (typeof LIST_FIELDS)[number];

type ConfirmState = Pick<
  ConfirmDialogProps,
  "title" | "message" | "confirmText" | "cancelText" | "tone"
> & {
  onConfirm: () => void;
};

const STRING_FIELDS: CellField[] = [
  "id",
  "vendor",
  "model",
  "scenario_type",
  "topic",
  "difficulty",
  "question",
  "evaluation_focus"
];

const REQUIRED_FIELDS: CellField[] = [
  "id",
  "vendor",
  "model",
  "scenario_type",
  "topic",
  "question",
  "evaluation_focus"
];

const REQUIRED_LIST_FIELDS: ListField[] = [
  "expected_documents",
  "expected_sections",
  "expected_keywords"
];

function reviewBadgeCls(status: string | undefined | null): string {
  switch (status) {
    case "draft":
      return "draft";
    case "reviewed":
      return "reviewed";
    default:
      return "unreviewed";
  }
}

function reviewBadgeText(status: string | undefined | null): string {
  switch (status) {
    case "draft":
      return "草稿待审";
    case "reviewed":
      return "已审核";
    default:
      return "未审核";
  }
}

function newEmptyRow(): DatasetRow {
  return {
    id: "",
    vendor: "",
    model: "",
    scenario_type: "",
    topic: "",
    difficulty: "",
    question: "",
    alternative_queries: [],
    expected_documents: [],
    expected_sections: [],
    expected_keywords: [],
    evaluation_focus: ""
  };
}

function isListField(value: string): value is ListField {
  return (LIST_FIELDS as readonly string[]).includes(value);
}

function localValidate(rows: DatasetRow[]): DatasetRowValidationError[] {
  const errors: DatasetRowValidationError[] = [];
  const seen = new Map<string, number>();
  rows.forEach((row, index) => {
    const id = (row?.id as string) || `row-${index + 1}`;
    for (const field of REQUIRED_FIELDS) {
      const value = row?.[field];
      if (typeof value !== "string" || !value.trim()) {
        errors.push({ row_index: index, sample_id: id, field, message: `${field} 不能为空` });
      }
    }
    for (const field of REQUIRED_LIST_FIELDS) {
      const list = toStringList(row?.[field]);
      if (list.length === 0) {
        errors.push({ row_index: index, sample_id: id, field, message: `${field} 至少 1 个非空字符串` });
      }
    }
    const alt = row?.alternative_queries;
    if (alt !== undefined && alt !== null) {
      if (!Array.isArray(alt) || alt.some((item) => typeof item !== "string")) {
        errors.push({
          row_index: index,
          sample_id: id,
          field: "alternative_queries",
          message: "alternative_queries 必须是字符串数组"
        });
      }
    }
    if (seen.has(id)) {
      errors.push({
        row_index: index,
        sample_id: id,
        field: "id",
        message: `id 重复（与第 ${(seen.get(id) ?? 0) + 1} 行）`
      });
    } else {
      seen.set(id, index);
    }
  });
  return errors;
}

function serialiseRow(row: DatasetRow): DatasetRow {
  const clone: DatasetRow = { ...row };
  LIST_FIELDS.forEach((field) => {
    clone[field] = toStringList(row[field]);
  });
  return clone;
}

export function DatasetEditor() {
  const params = useParams<{ path: string }>();
  const navigate = useNavigate();
  const datasetPath = decodeURIComponent(params.path || "");

  const [data, setData] = useState<DatasetRowsResponse | null>(null);
  const [rows, setRows] = useState<DatasetRow[]>([]);
  // 与当前 rows 对应的"保存前快照"，用于计算每一行是否被改过。
  // baseline 未设置时，修改状态按脏标记兜底（增/删行）。
  const [baselineRows, setBaselineRows] = useState<DatasetRow[]>([]);
  const [dirty, setDirty] = useState(false);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [saveState, setSaveState] = useState<"idle" | "saving" | "saved">("idle");
  const [serverErrors, setServerErrors] = useState<DatasetRowValidationError[]>([]);
  const [saveError, setSaveError] = useState("");
  const [filter, setFilter] = useState("");
  const [scenarioFilter, setScenarioFilter] = useState<string>("");
  const [difficultyFilter, setDifficultyFilter] = useState("");
  const [vendorFilter, setVendorFilter] = useState("");
  const [modifiedOnly, setModifiedOnly] = useState(false);
  const [errorOnly, setErrorOnly] = useState(false);
  const [activeRowIndex, setActiveRowIndex] = useState<number | null>(null);
  const [jsonlPreview, setJsonlPreview] = useState<string>("");
  // 前端分页：rows 整体仍在内存，表格只渲染当前页的 slice
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState<10 | 20 | 50 | 100>(20);
  const [reviewState, setReviewState] = useState<"idle" | "committing" | "committed">("idle");
  const [reviewError, setReviewError] = useState("");
  const [reviewedBy, setReviewedBy] = useState("");
  const [confirmState, setConfirmState] = useState<ConfirmState | null>(null);
  const [isDeleting, setIsDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState("");
  const fileInputRef = useRef<HTMLInputElement>(null);

  async function load(path: string) {
    setLoading(true);
    setLoadError("");
    try {
      const result = await getDatasetRows(path);
      setData(result);
      const cloned = result.rows.map((row) => ({ ...row }));
      setRows(cloned);
      // 加载时把当前内容作为"已保存基线"，确保首次进入不算修改
      setBaselineRows(cloned.map((row) => ({ ...row })));
      setDirty(false);
      setServerErrors([]);
    } catch (err) {
      const message = err instanceof Error ? err.message : "加载评测集失败";
      setLoadError(message);
      showErrorToast({ title: message, code: "unknown" });
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (datasetPath) {
      void load(datasetPath);
    }
  }, [datasetPath]);

  const localErrors = useMemo(() => localValidate(rows), [rows]);
  const cellErrorMap = useMemo(() => {
    const map = new Map<string, DatasetRowValidationError>();
    for (const err of localErrors) {
      map.set(`${err.row_index}:${err.field ?? ""}`, err);
    }
    for (const err of serverErrors) {
      map.set(`${err.row_index}:${err.field ?? ""}`, err);
    }
    return map;
  }, [localErrors, serverErrors]);

  const rowErrorCounts = useMemo(() => {
    const counts = new Map<number, number>();
    [...localErrors, ...serverErrors].forEach((err) => {
      counts.set(err.row_index, (counts.get(err.row_index) || 0) + 1);
    });
    return counts;
  }, [localErrors, serverErrors]);

  // 哪些行相对基线有改动：用浅比 + 列表序列化的稳定字符串，
  // 这样新增/删除项、顺序变化、字段值变化都能被检测到。
  const modifiedRowSet = useMemo(() => {
    const set = new Set<number>();
    const max = Math.max(rows.length, baselineRows.length);
    for (let i = 0; i < max; i++) {
      const a = rows[i];
      const b = baselineRows[i];
      if (!a || !b) { set.add(i); continue; }
      if (JSON.stringify(serialiseRow(a)) !== JSON.stringify(serialiseRow(b))) {
        set.add(i);
      }
    }
    return set;
  }, [rows, baselineRows]);

  const filterOptions = useMemo(() => buildFilterOptions(rows), [rows]);

  const scenarioOptions = useMemo(() => {
    const set = new Set<string>(data?.scenario_types || []);
    filterOptions.scenarios.forEach((value) => set.add(value));
    return Array.from(set).sort((a, b) => a.localeCompare(b, "zh-Hans-CN"));
  }, [data, filterOptions.scenarios]);

  const filters = useMemo<DatasetEditorFilters>(() => ({
    keyword: filter,
    scenario: scenarioFilter,
    difficulty: difficultyFilter,
    vendor: vendorFilter,
    modifiedOnly,
    errorOnly
  }), [filter, scenarioFilter, difficultyFilter, vendorFilter, modifiedOnly, errorOnly]);

  const hasActiveFilters = Boolean(
    filter.trim() ||
    scenarioFilter ||
    difficultyFilter ||
    vendorFilter ||
    modifiedOnly ||
    errorOnly
  );

  const visibleRows = useMemo(() => {
    return rows
      .map((row, index) => ({ row, index }))
      .filter(({ row, index }) => rowMatchesFilters(row, index, filters, modifiedRowSet, rowErrorCounts));
  }, [rows, filters, modifiedRowSet, rowErrorCounts]);

  const totalPages = Math.max(1, Math.ceil(visibleRows.length / pageSize));

  // 过滤条件或每页条数变化时，回到第 1 页
  useEffect(() => {
    setPage(1);
  }, [filter, scenarioFilter, difficultyFilter, vendorFilter, modifiedOnly, errorOnly, pageSize]);

  // 当前页切片
  const pagedRows = useMemo(() => {
    const start = (page - 1) * pageSize;
    return visibleRows.slice(start, start + pageSize);
  }, [visibleRows, page, pageSize]);

  function clearFilters() {
    setFilter("");
    setScenarioFilter("");
    setDifficultyFilter("");
    setVendorFilter("");
    setModifiedOnly(false);
    setErrorOnly(false);
  }

  function updateRow(index: number, patch: Partial<DatasetRow>) {
    setRows((current) => current.map((row, idx) => (idx === index ? { ...row, ...patch } : row)));
    setDirty(true);
    setServerErrors([]);
  }

  function setListValue(index: number, field: ListField, value: string[]) {
    updateRow(index, { [field]: value } as Partial<DatasetRow>);
  }

  function addListItem(index: number, field: ListField, value: string) {
    const trimmed = value.trim();
    if (!trimmed) return;
    const current = toStringList(rows[index][field]);
    if (current.includes(trimmed)) return;
    setListValue(index, field, [...current, trimmed]);
  }

  function removeListItem(index: number, field: ListField, valueIndex: number) {
    const current = toStringList(rows[index][field]);
    setListValue(
      index,
      field,
      current.filter((_, idx) => idx !== valueIndex)
    );
  }

  function editListItem(index: number, field: ListField, valueIndex: number, next: string) {
    const trimmed = next.trim();
    if (!trimmed) {
      removeListItem(index, field, valueIndex);
      return;
    }
    const current = toStringList(rows[index][field]);
    if (current[valueIndex] === trimmed) return;
    const updated = current.map((item, idx) => (idx === valueIndex ? trimmed : item));
    setListValue(index, field, updated);
  }

  function addRow() {
    const nextIndex = rows.length;
    if (hasActiveFilters) {
      clearFilters();
      window.setTimeout(() => setPage(pageForIndex(nextIndex, pageSize)), 0);
    } else {
      setPage(pageForIndex(nextIndex, pageSize));
    }
    setRows((current) => [...current, newEmptyRow()]);
    setDirty(true);
    setServerErrors([]);
    setActiveRowIndex(nextIndex);
  }

  function removeRow(index: number) {
    const nextActiveIndex = nextActiveIndexAfterDelete(index, rows.length);
    const nextRowCount = Math.max(0, rows.length - 1);
    const nextTotalPages = Math.max(1, Math.ceil(nextRowCount / pageSize));
    setRows((current) => current.filter((_, idx) => idx !== index));
    setBaselineRows((current) => current.filter((_, idx) => idx !== index));
    setDirty(true);
    setServerErrors([]);
    setActiveRowIndex((current) => {
      if (current === index) return nextActiveIndex;
      if (current !== null && current > index) return current - 1;
      return current;
    });
    setPage((current) => Math.min(current, nextTotalPages));
  }

  function handleCellError(rowIndex: number, field: string) {
    return cellErrorMap.get(`${rowIndex}:${field}`);
  }

  function closeConfirm() {
    setConfirmState(null);
  }

  function confirmAndRun() {
    const action = confirmState?.onConfirm;
    setConfirmState(null);
    action?.();
  }

  function handleSave() {
    if (!data) return;
    // 草稿状态：保存动作落到 draft；其他情况落到 main（主评测集文件）。
    const target: "draft" | "main" = data.review_status === "draft" ? "draft" : "main";
    const changeCount = modifiedRowSet.size;
    const localErrCount = localErrors.length;
    setConfirmState({
      title: target === "draft" ? "保存到草稿" : "覆盖主评测集",
      message: buildDatasetSaveConfirmation({
        target,
        changeCount,
        rowCount: rows.length,
        localErrorCount: localErrCount
      }),
      confirmText: target === "draft" ? "保存到草稿" : "覆盖保存",
      tone: localErrCount > 0 || target === "main" ? "warning" : "primary",
      onConfirm: () => void performSave(target)
    });
  }

  async function performSave(target: "main" | "draft") {
    if (!data) return;
    setSaveState("saving");
    setSaveError("");
    setServerErrors([]);
    try {
      const serialised = rows.map(serialiseRow);
      const response = await saveDatasetRows(data.path, serialised, { target });
      setSaveState("saved");
      setDirty(false);
      // refresh metadata
      void load(data.path);
      window.setTimeout(() => setSaveState("idle"), 1500);
      void response;
    } catch (err) {
      setSaveState("idle");
      const e = err as RequestError;
      if (Array.isArray(e.validation_errors) && e.validation_errors.length > 0) {
        setServerErrors(e.validation_errors as DatasetRowValidationError[]);
        setSaveError("存在校验错误，请按行内红框修正后再保存。");
      } else {
        setSaveError(e.message || "保存失败");
      }
    }
  }

  function handleCommitReview() {
    if (!data) return;
    const localErrCount = localErrors.length;
    const message = localErrCount > 0
      ? `当前仍有 ${localErrCount} 条本地校验告警，确认仍要提交审核吗？`
      : data.review_status === "draft"
        ? "确认把当前草稿内容提交为已审核状态？\n提交后将覆盖原评测集文件，并删除草稿。"
        : "确认将当前主评测集标记为已审核状态？\n提交后会写入审核元信息。";
    setConfirmState({
      title: "标记为已审核",
      message,
      confirmText: "确认审核通过",
      tone: localErrCount > 0 ? "warning" : "primary",
      onConfirm: () => void performCommitReview()
    });
  }

  async function performCommitReview() {
    if (!data) return;
    setReviewState("committing");
    setReviewError("");
    setServerErrors([]);
    try {
      const serialised = rows.map(serialiseRow);
      await commitDatasetReview(data.path, serialised, reviewedBy);
      setReviewState("committed");
      setDirty(false);
      void load(data.path);
      window.setTimeout(() => setReviewState("idle"), 1800);
    } catch (err) {
      setReviewState("idle");
      const e = err as RequestError;
      if (Array.isArray(e.validation_errors) && e.validation_errors.length > 0) {
        setServerErrors(e.validation_errors as DatasetRowValidationError[]);
        setReviewError("存在校验错误，请按行内红框修正后再提交审核。");
      } else {
        setReviewError(e.message || "提交审核失败");
      }
    }
  }

  async function handleExport() {
    if (!data) return;
    try {
      const result = await exportDataset(data.path);
      const blob = new Blob([result.content], { type: "application/jsonl;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `${data.name}.jsonl`;
      link.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : "导出失败");
    }
  }

  function handleShowJsonl() {
    const content = rows.map((row) => JSON.stringify(serialiseRow(row))).join("\n");
    setJsonlPreview(content + (content ? "\n" : ""));
  }

  function handleRequestDelete() {
    if (!data) return;
    setDeleteError("");
    setIsDeleting(true);
  }

  function handleCancelDelete() {
    if (!isDeleting) return;
    setDeleteError("");
    setIsDeleting(false);
  }

  async function handleConfirmDelete() {
    if (!data) return;
    setDeleteError("");
    try {
      await deleteDataset(data.path);
      // 删除后回到列表页，由后端真实状态决定后续。
      navigate("/datasets");
    } catch (err) {
      const e = err as RequestError;
      setDeleteError(e.message || "删除评测集失败");
    } finally {
      setIsDeleting(false);
    }
  }

  function handleDownloadEditedJsonl() {
    if (!data) return;
    const content = rows.map((row) => JSON.stringify(serialiseRow(row))).join("\n");
    const blob = new Blob([content + (content ? "\n" : "")], { type: "application/jsonl;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `${data.name}.edited.jsonl`;
    link.click();
    URL.revokeObjectURL(url);
  }

  if (loading) {
    return (
      <div className="detail-loading">
        <Loader2 size={24} className="spin" />
        正在加载评测集...
      </div>
    );
  }
  if (loadError) {
    return (
      <div className="workspace-stack">
        <div className="error-line">{loadError}</div>
        <Link to="/datasets" className="ghost-link">
          <ArrowLeft size={14} /> 返回评测集列表
        </Link>
      </div>
    );
  }
  if (!data) return null;
  const reviewCanCommit = data.review_status !== "reviewed";

  return (
    <div className="workspace-stack">
      <section className="panel dataset-editor-panel">
        <PanelHeader
          icon={<FileText size={18} />}
          title={`编辑：${data.name}`}
          subtitle={data.path}
          action={
            <div className="panel-action-row">
              <button
                type="button"
                className="ghost-link danger"
                onClick={handleRequestDelete}
                title="删除整个评测集（需在弹窗中输入名称确认）"
              >
                <Trash2 size={14} /> 删除评测集
              </button>
              <button
                type="button"
                className="ghost-link"
                onClick={() => navigate("/datasets")}
              >
                <ArrowLeft size={14} /> 返回
              </button>
            </div>
          }
        />
        <div className="dataset-review-banner" data-status={data.review_status || "unreviewed"}>
          <span className={`ui-badge dataset-review-badge ${reviewBadgeCls(data.review_status)}`}>
            {reviewBadgeText(data.review_status)}
          </span>
          <span className="dataset-review-banner-text">
            {data.review_status === "draft" && (
              <>
                当前编辑的是草稿（{data.draft_path}）。修改会先回到草稿，点
                <strong>「标记为已审核」</strong>才落盘到主评测集。
              </>
            )}
            {data.review_status === "reviewed" && (
              <>
                已通过人工审核
                {data.reviewed_at ? `（${formatDateTime(data.reviewed_at, "full")}` : ""}
                {data.reviewed_by ? ` · ${data.reviewed_by}` : ""}
                {data.reviewed_at ? "）" : ""}
                。再次修改会直接落主文件并重置审核元信息。
              </>
            )}
            {(!data.review_status || data.review_status === "unreviewed") && (
              <>
                旧版样本未走审核流程。提交审核会写入审核元信息，并保留当前主文件。
              </>
            )}
          </span>
        </div>
        <div className="dataset-editor-meta">
          <div>
            <strong>{rows.length}</strong>
            <span>行数（编辑中）</span>
          </div>
          <div>
            <strong>{data.sample_count}</strong>
            <span>原始样本数</span>
          </div>
          <div>
            <strong>{localErrors.length}</strong>
            <span>本地校验告警</span>
          </div>
          <div>
            <strong>{serverErrors.length}</strong>
            <span>服务端返回错误</span>
          </div>
        </div>
        <div className="dataset-editor-toolbar">
          <div className="dataset-editor-search">
            <Search size={14} />
            <input
              value={filter}
              onChange={(event) => setFilter(event.target.value)}
              placeholder="搜索 ID、问题、章节、关键词..."
            />
            {filter && (
              <button type="button" className="icon-button tiny" onClick={() => setFilter("")}>
                <X size={12} />
              </button>
            )}
          </div>
          <label className="field inline">
            <span>场景过滤</span>
            <StandardSelect
              value={scenarioFilter}
              title={scenarioFilter ? `当前场景：${scenarioFilter}` : "按场景过滤"}
              onChange={(event) => setScenarioFilter(event.target.value)}
            >
              <option value="">全部</option>
              {scenarioOptions.map((option) => (
                <option key={option} value={option}>
                  {option}
                </option>
              ))}
            </StandardSelect>
          </label>
          <label className="field inline">
            <span>厂商</span>
            <StandardSelect
              value={vendorFilter}
              title={vendorFilter ? `当前厂商：${vendorFilter}` : "按厂商过滤"}
              onChange={(event) => setVendorFilter(event.target.value)}
            >
              <option value="">全部</option>
              {filterOptions.vendors.map((option) => (
                <option key={option} value={option}>
                  {option}
                </option>
              ))}
            </StandardSelect>
          </label>
          <label className="field inline">
            <span>难度</span>
            <StandardSelect
              value={difficultyFilter}
              title={difficultyFilter ? `当前难度：${difficultyFilter}` : "按难度过滤"}
              onChange={(event) => setDifficultyFilter(event.target.value)}
            >
              <option value="">全部</option>
              {filterOptions.difficulties.map((option) => (
                <option key={option} value={option}>
                  {option}
                </option>
              ))}
            </StandardSelect>
          </label>
          <label className="filter-toggle">
            <input
              type="checkbox"
              checked={modifiedOnly}
              onChange={(event) => setModifiedOnly(event.target.checked)}
            />
            <span>已修改</span>
          </label>
          <label className="filter-toggle">
            <input
              type="checkbox"
              checked={errorOnly}
              onChange={(event) => setErrorOnly(event.target.checked)}
            />
            <span>有错误</span>
          </label>
          {hasActiveFilters && (
            <button type="button" className="ghost-button" onClick={clearFilters}>
              <X size={14} />
              清空筛选
            </button>
          )}
          <button type="button" className="ghost-button" onClick={addRow}>
            <Plus size={14} />
            新增行
          </button>
          <button type="button" className="ghost-button" onClick={handleShowJsonl}>
            <Tag size={14} />
            查看原始数据
          </button>
          <button type="button" className="ghost-button" onClick={handleExport}>
            <Download size={14} />
            下载原文件
          </button>
          {reviewCanCommit && (
            <label className="field inline reviewer-input">
              <span>审核人</span>
              <input
                value={reviewedBy}
                onChange={(event) => setReviewedBy(event.target.value)}
                placeholder="例如：张三"
              />
            </label>
          )}
          <button
            type="button"
            className="primary-button inline"
            onClick={handleSave}
            disabled={!dirty || saveState === "saving"}
          >
            {saveState === "saving" ? <Loader2 size={16} className="spin" /> : <Save size={16} />}
            {saveState === "saved"
              ? "已保存"
              : data.review_status === "draft"
                ? "保存到草稿"
                : "保存评测集"}
          </button>
          {reviewCanCommit && (
            <button
              type="button"
              className="primary-button inline commit-review"
              onClick={handleCommitReview}
              disabled={reviewState === "committing" || localErrors.length > 0}
              title={
                localErrors.length > 0
                  ? "先消除本地校验告警再提交审核"
                  : data.review_status === "draft"
                    ? "把当前草稿内容提交为已审核"
                    : "把当前评测集标记为已审核"
              }
            >
              {reviewState === "committing" ? (
                <Loader2 size={16} className="spin" />
              ) : (
                <CheckCircle2 size={16} />
              )}
              {reviewState === "committed" ? "已提交审核" : "标记为已审核"}
            </button>
          )}
        </div>
        {saveError && <div className="error-line">{saveError}</div>}
        {reviewError && <div className="error-line">{reviewError}</div>}
        {deleteError && <div className="error-line">{deleteError}</div>}
        {localErrors.length > 0 && serverErrors.length === 0 && (
          <div className="warning-line">
            共 {localErrors.length} 条本地校验告警，未阻断保存。点击保存后服务端正则会再次校验。
          </div>
        )}
        {saveState === "saved" && data.review_status !== "draft" && (
          <div className="success-line">
            <CheckCircle2 size={14} /> 已保存到评测集，下次评测将使用最新内容。
          </div>
        )}
        {saveState === "saved" && data.review_status === "draft" && (
          <div className="success-line">
            <CheckCircle2 size={14} /> 已保存到草稿。下一步：点击「标记为已审核」把内容落盘到主评测集。
          </div>
        )}
        {reviewState === "committed" && (
          <div className="success-line">
            <CheckCircle2 size={14} /> 已提交审核，主评测集已更新。
          </div>
        )}
        {dirty && saveState !== "saved" && (
          <div className="warning-line">
            <AlertTriangle size={14} /> 已修改 {modifiedRowSet.size} 行（点击右下角"保存评测集"才会落盘）
          </div>
        )}
      </section>

      <section className="panel dataset-editor-panel">
        <PanelHeader
          icon={<Filter size={18} />}
          title={`样本表格（${visibleRows.length} / ${rows.length}）`}
          subtitle="点击单元格直接编辑；标签字段回车新增，× 删除"
          action={<span className="table-scroll-hint">左右滚动查看更多字段</span>}
        />
        <div className="dataset-editor-table-wrap">
          <table className="dataset-editor-table">
            <thead>
              <tr>
                <th style={{ width: 80 }}>操作</th>
                <th style={{ width: 40 }}>#</th>
                {STRING_FIELDS.map((field) => (
                  <th key={field} style={{ minWidth: columnMinWidth(field) }}>
                    {labelOf(field)}
                  </th>
                ))}
                {LIST_FIELDS.map((field) => (
                  <th key={field} style={{ minWidth: listColumnMinWidth(field) }}>
                    {labelOf(field)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {pagedRows.map(({ row, index }) => (
                <EditorRow
                  key={`${row.id || "row"}-${index}`}
                  row={row}
                  index={index}
                  active={activeRowIndex === index}
                  modified={modifiedRowSet.has(index)}
                  rowErrorCount={rowErrorCounts.get(index) || 0}
                  keyword={filter}
                  scenarioOptions={scenarioOptions}
                  onActivate={() => setActiveRowIndex(index)}
                  onUpdate={updateRow}
                  onAddList={addListItem}
                  onRemoveList={removeListItem}
                  onEditList={editListItem}
                  onRemoveRow={removeRow}
                  errorFor={handleCellError}
                />
              ))}
              {pagedRows.length === 0 && (
                <tr>
                  <td colSpan={STRING_FIELDS.length + LIST_FIELDS.length + 2} className="empty-cell">
                    {visibleRows.length === 0
                      ? "没有匹配的样本。试试清空搜索或调整场景过滤。"
                      : "当前页为空。"}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        {visibleRows.length > 0 && (
          <div className="dataset-pagination">
            <div className="dataset-pagination-info">
              第 {page} / {totalPages} 页 · 共 {visibleRows.length} 行匹配
            </div>
            <div className="dataset-pagination-actions">
              <label className="field inline pagination-size">
                <span>每页</span>
                <StandardSelect
                  value={pageSize}
                  title={`每页显示 ${pageSize} 行`}
                  onChange={(event) => setPageSize(Number(event.target.value) as 10 | 20 | 50 | 100)}
                >
                  <option value={10}>10</option>
                  <option value={20}>20</option>
                  <option value={50}>50</option>
                  <option value={100}>100</option>
                </StandardSelect>
              </label>
              <button
                type="button"
                className="ghost-button"
                onClick={() => setPage((current) => Math.max(1, current - 1))}
                disabled={page <= 1}
              >
                <ChevronDown size={14} className="chevron-prev" />
                上一页
              </button>
              <button
                type="button"
                className="ghost-button"
                onClick={() => setPage((current) => Math.min(totalPages, current + 1))}
                disabled={page >= totalPages}
              >
                下一页
                <ChevronDown size={14} className="chevron-next" />
              </button>
            </div>
          </div>
        )}
      </section>

      {activeRowIndex !== null && rows[activeRowIndex] && (
        <RowDetailDrawer
          row={rows[activeRowIndex]}
          index={activeRowIndex}
          modified={modifiedRowSet.has(activeRowIndex)}
          errors={[...localErrors, ...serverErrors].filter((err) => err.row_index === activeRowIndex)}
          onChange={(patch) => updateRow(activeRowIndex, patch)}
          onAddList={(field, value) => addListItem(activeRowIndex, field, value)}
          onRemoveList={(field, valueIndex) => removeListItem(activeRowIndex, field, valueIndex)}
          onEditList={(field, valueIndex, next) => editListItem(activeRowIndex, field, valueIndex, next)}
          onClose={() => setActiveRowIndex(null)}
        />
      )}

      {jsonlPreview && (
        <JsonlPreviewModal
          content={jsonlPreview}
          onClose={() => setJsonlPreview("")}
          onDownload={handleDownloadEditedJsonl}
        />
      )}

      {confirmState && (
        <ConfirmDialog
          title={confirmState.title}
          message={confirmState.message}
          confirmText={confirmState.confirmText}
          cancelText={confirmState.cancelText}
          tone={confirmState.tone}
          onCancel={closeConfirm}
          onConfirm={confirmAndRun}
        />
      )}

      {isDeleting && data && (
        <DeleteDatasetDialog
          datasetName={data.name}
          datasetPath={data.path}
          sampleCount={data.sample_count}
          error={deleteError}
          onCancel={handleCancelDelete}
          onConfirm={handleConfirmDelete}
        />
      )}

      <input
        ref={fileInputRef}
        type="file"
        accept=".jsonl"
        style={{ display: "none" }}
        onChange={(event) => {
          // not used today; placeholder for future file-input flows
          event.target.value = "";
        }}
      />

      {dirty && (
        <div className="dataset-save-bar" role="region" aria-label="保存评测集">
          <div className="dataset-save-bar-text">
            <AlertTriangle size={16} />
            <span>
              已修改 {modifiedRowSet.size} / {rows.length} 行（未保存到评测集）
            </span>
          </div>
          <div className="dataset-save-bar-actions">
            <button
              type="button"
              className="ghost-button"
              onClick={() => {
                if (!data) return;
                const changeCount = modifiedRowSet.size;
                setConfirmState({
                  title: "放弃本地改动",
                  message: buildDatasetDiscardConfirmation(changeCount),
                  confirmText: "放弃并重新加载",
                  tone: "danger",
                  onConfirm: () => void load(data.path)
                });
              }}
              title="放弃本地改动，重新加载"
            >
              放弃改动
            </button>
            <button
              type="button"
              className="primary-button"
              onClick={handleSave}
              disabled={saveState === "saving"}
            >
              {saveState === "saving" ? <Loader2 size={16} className="spin" /> : <Save size={16} />}
              {saveState === "saved" ? "已保存" : "保存评测集"}
            </button>
          </div>
        </div>
      )}

      {!dirty && saveState === "saved" && (
        <div className="dataset-save-bar saved" role="status">
          <div className="dataset-save-bar-text">
            <CheckCircle2 size={16} />
            <span>已保存到评测集</span>
          </div>
        </div>
      )}
    </div>
  );
}

function columnMinWidth(field: string) {
  switch (field) {
    case "id":
      return 140;
    case "question":
      return 240;
    case "evaluation_focus":
      return 260;
    case "topic":
      return 140;
    default:
      return 110;
  }
}

function listColumnMinWidth(field: string) {
  switch (field) {
    case "expected_documents":
      return 200;
    case "expected_sections":
      return 200;
    case "expected_keywords":
      return 200;
    case "alternative_queries":
      return 200;
    default:
      return 160;
  }
}

function labelOf(field: string) {
  switch (field) {
    case "id":
      return "ID";
    case "vendor":
      return "厂商";
    case "model":
      return "型号";
    case "scenario_type":
      return "场景";
    case "topic":
      return "主题";
    case "difficulty":
      return "难度";
    case "question":
      return "主问题";
    case "evaluation_focus":
      return "评估关注点";
    case "expected_documents":
      return "期望文档";
    case "expected_sections":
      return "期望章节";
    case "expected_keywords":
      return "期望关键词";
    case "alternative_queries":
      return "同义问法";
    default:
      return field;
  }
}

interface EditorRowProps {
  row: DatasetRow;
  index: number;
  active: boolean;
  modified: boolean;
  rowErrorCount: number;
  keyword: string;
  scenarioOptions: string[];
  onActivate: () => void;
  onUpdate: (index: number, patch: Partial<DatasetRow>) => void;
  onAddList: (index: number, field: ListField, value: string) => void;
  onRemoveList: (index: number, field: ListField, valueIndex: number) => void;
  onEditList: (index: number, field: ListField, valueIndex: number, next: string) => void;
  onRemoveRow: (index: number) => void;
  errorFor: (rowIndex: number, field: string) => DatasetRowValidationError | undefined;
}

function EditorRow({
  row,
  index,
  active,
  modified,
  rowErrorCount,
  keyword,
  scenarioOptions,
  onActivate,
  onUpdate,
  onAddList,
  onRemoveList,
  onEditList,
  onRemoveRow,
  errorFor
}: EditorRowProps) {
  const [scenarioDraft, setScenarioDraft] = useState("");
  // 行内二次删除确认：第一次点击进入"待确认"态，3 秒未再点则回退
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  useEffect(() => {
    if (!confirmingDelete) return;
    const timer = window.setTimeout(() => setConfirmingDelete(false), 3000);
    return () => window.clearTimeout(timer);
  }, [confirmingDelete]);
  return (
    <tr className={active ? "active" : ""} onClick={onActivate}>
      <td onClick={(event) => event.stopPropagation()}>
        {confirmingDelete ? (
          <div className="row-confirm-delete" onClick={(event) => event.stopPropagation()}>
            <button
              type="button"
              className="row-confirm-yes"
              onClick={(event) => {
                event.stopPropagation();
                setConfirmingDelete(false);
                onRemoveRow(index);
              }}
              title="再次点击以确认删除"
            >
              确认删除
            </button>
            <button
              type="button"
              className="row-confirm-no"
              onClick={(event) => {
                event.stopPropagation();
                setConfirmingDelete(false);
              }}
              title="取消删除"
              aria-label="取消删除"
            >
              <X size={12} />
            </button>
          </div>
        ) : (
          <button
            type="button"
            className="icon-button danger"
            onClick={(event) => {
              event.stopPropagation();
              setConfirmingDelete(true);
            }}
            title="删除该行（需再次点击确认）"
          >
            <Trash2 size={14} />
          </button>
        )}
      </td>
      <td className="row-index" onClick={onActivate}>
        <span
          className={`row-modified-dot ${modified ? "is-modified" : "is-saved"}`}
          title={modified ? "本行有未保存的修改" : "本行已保存"}
        />
        {index + 1}
        {rowErrorCount > 0 && (
          <span className="row-error-count" title={`本行有 ${rowErrorCount} 条校验问题`}>
            {rowErrorCount}
          </span>
        )}
      </td>
      {STRING_FIELDS.map((field) => {
        const value = (row[field] as string) || "";
        const err = errorFor(index, field);
        return (
          <td key={field} className={err ? "cell-error" : undefined}>
            {field === "scenario_type" ? (
              <DatalistInput
                datalistId={`scenario-options-${index}`}
                options={scenarioOptions}
                value={value}
                placeholder="例如：故障恢复"
                onChange={(event) => {
                  const next = event.target.value;
                  setScenarioDraft(next);
                  onUpdate(index, { scenario_type: next });
                }}
                title={value ? `当前场景：${value}` : "选择或输入场景"}
              />
            ) : field === "difficulty" ? (
              <StandardSelect
                value={value}
                title={value ? `当前难度：${value}` : "选择难度"}
                onChange={(event) => onUpdate(index, { difficulty: event.target.value })}
              >
                <option value="">未指定</option>
                <option value="基础">基础</option>
                <option value="中等">中等</option>
                <option value="高级">高级</option>
              </StandardSelect>
            ) : field === "question" || field === "evaluation_focus" ? (
              <LongTextCell
                value={value}
                placeholder={`点击编辑${labelOf(field)}`}
                keyword={keyword}
                onChange={(next) => onUpdate(index, { [field]: next } as Partial<DatasetRow>)}
              />
            ) : (
              <input
                className="cell-truncate"
                value={value}
                title={value}
                onChange={(event) => onUpdate(index, { [field]: event.target.value } as Partial<DatasetRow>)}
              />
            )}
            {err && <small className="cell-error-tip">{err.message}</small>}
            {field === "scenario_type" && scenarioDraft === "" && null}
          </td>
        );
      })}
      {LIST_FIELDS.map((field) => {
        const list = toStringList(row[field]);
        return (
          <td key={field} className={errorFor(index, field) ? "cell-error" : undefined}>
            <CompactTagList
              items={list}
              field={field}
              onAdd={(value) => onAddList(index, field, value)}
              onRemove={(valueIndex) => onRemoveList(index, field, valueIndex)}
              onEdit={(valueIndex, next) => onEditList(index, field, valueIndex, next)}
              keyword={keyword}
              maxInlineTags={3}
            />
            {errorFor(index, field) && (
              <small className="cell-error-tip">{errorFor(index, field)?.message}</small>
            )}
          </td>
        );
      })}
    </tr>
  );
}

function HighlightedText({ text, keyword }: { text: string; keyword: string }) {
  return (
    <>
      {highlightSegments(text, keyword).map((segment, index) => (
        segment.match ? (
          <mark key={`${segment.text}-${index}`} className="search-hit">
            {segment.text}
          </mark>
        ) : (
          <span key={`${segment.text}-${index}`}>{segment.text}</span>
        )
      ))}
    </>
  );
}

interface TagListProps {
  items: string[];
  field: ListField;
  onAdd: (value: string) => void;
  onRemove: (valueIndex: number) => void;
  onEdit: (valueIndex: number, next: string) => void;
  keyword?: string;
  maxInlineTags?: number;
}

function CompactTagList({ items, field, onAdd, onRemove, onEdit, keyword = "", maxInlineTags = 8 }: TagListProps) {
  void field;
  const [draft, setDraft] = useState("");
  const [collapsed, setCollapsed] = useState(true);
  const visible = collapsed ? items.slice(0, maxInlineTags) : items;
  const hiddenCount = items.length - visible.length;
  return (
    <div className="tag-list" onClick={(event) => event.stopPropagation()}>
      {visible.map((item, idx) => (
        <EditableChip
          key={`${item}-${idx}`}
          value={item}
          keyword={keyword}
          onCommit={(next) => onEdit(idx, next)}
          onRemove={() => onRemove(idx)}
        />
      ))}
      {hiddenCount > 0 && collapsed && (
        <button
          type="button"
          className="tag-overflow"
          onClick={() => setCollapsed(false)}
          title="展开查看全部"
        >
          +{hiddenCount} ▾
        </button>
      )}
      {!collapsed && items.length > maxInlineTags && (
        <button
          type="button"
          className="tag-overflow collapse"
          onClick={() => setCollapsed(true)}
        >
          收起 ▴
        </button>
      )}
      <input
        className="tag-input"
        value={draft}
        placeholder="回车新增"
        onChange={(event) => setDraft(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter" || event.key === ",") {
            event.preventDefault();
            if (draft.trim()) {
              onAdd(draft);
              setDraft("");
            }
          }
        }}
      />
    </div>
  );
}

interface EditableChipProps {
  value: string;
  keyword?: string;
  onCommit: (next: string) => void;
  onRemove: () => void;
}

function EditableChip({ value, keyword = "", onCommit, onRemove }: EditableChipProps) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(value);
  if (editing) {
    return (
      <span className="ui-chip tag-chip editing">
        <input
          className="tag-chip-input"
          autoFocus
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onClick={(event) => event.stopPropagation()}
          onBlur={() => {
            if (draft.trim() && draft !== value) onCommit(draft.trim());
            setEditing(false);
          }}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              event.preventDefault();
              if (draft.trim() && draft !== value) onCommit(draft.trim());
              setEditing(false);
            } else if (event.key === "Escape") {
              event.preventDefault();
              setDraft(value);
              setEditing(false);
            }
          }}
        />
        <button
          type="button"
          className="tag-remove"
          onClick={(event) => {
            event.stopPropagation();
            onRemove();
          }}
          aria-label={`移除 ${value}`}
          title="删除该标签"
        >
          ×
        </button>
      </span>
    );
  }
  return (
    <span
      className="ui-chip tag-chip"
      title={`${value}（双击修改，× 删除）`}
      onDoubleClick={(event) => {
        event.stopPropagation();
        setDraft(value);
        setEditing(true);
      }}
    >
      <span className="tag-chip-text">
        <HighlightedText text={value} keyword={keyword} />
      </span>
      <button
        type="button"
        className="tag-remove"
        onClick={(event) => {
          event.stopPropagation();
          onRemove();
        }}
        aria-label={`移除 ${value}`}
        title="删除该标签"
      >
        ×
      </button>
    </span>
  );
}

interface LongTextCellProps {
  value: string;
  placeholder: string;
  keyword?: string;
  onChange: (next: string) => void;
}

function LongTextCell({ value, placeholder, keyword = "", onChange }: LongTextCellProps) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(value);

  useEffect(() => {
    if (!editing) setDraft(value);
  }, [value, editing]);

  if (editing) {
    return (
      <textarea
        className="cell-textarea"
        autoFocus
        rows={4}
        value={draft}
        onChange={(event) => setDraft(event.target.value)}
        onBlur={() => {
          onChange(draft);
          setEditing(false);
        }}
        onKeyDown={(event) => {
          if (event.key === "Escape") {
            event.preventDefault();
            setDraft(value);
            setEditing(false);
          } else if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
            event.preventDefault();
            onChange(draft);
            setEditing(false);
          }
        }}
        onClick={(event) => event.stopPropagation()}
      />
    );
  }

  return (
    <div
      className={`cell-long-text ${value ? "" : "placeholder"}`}
      title={value || placeholder}
      role="button"
      tabIndex={0}
      onClick={(event) => {
        event.stopPropagation();
        setEditing(true);
      }}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          setEditing(true);
        }
      }}
    >
      {value ? <HighlightedText text={value} keyword={keyword} /> : placeholder}
    </div>
  );
}

interface RowDetailDrawerProps {
  row: DatasetRow;
  index: number;
  modified: boolean;
  errors: DatasetRowValidationError[];
  onChange: (patch: Partial<DatasetRow>) => void;
  onAddList: (field: ListField, value: string) => void;
  onRemoveList: (field: ListField, valueIndex: number) => void;
  onEditList: (field: ListField, valueIndex: number, next: string) => void;
  onClose: () => void;
}

function RowDetailDrawer({
  row,
  index,
  modified,
  errors,
  onChange,
  onAddList,
  onRemoveList,
  onEditList,
  onClose
}: RowDetailDrawerProps) {
  const metadataValue = useMemo(() => {
    const { id: _id, vendor: _vendor, model: _model, scenario_type: _s, topic: _t, difficulty: _d,
      question: _q, alternative_queries: _a, expected_documents: _ed, expected_sections: _es,
      expected_keywords: _ek, evaluation_focus: _ef, ...rest } = row as DatasetRow & Record<string, unknown>;
    return Object.keys(rest).length > 0 ? JSON.stringify(rest, null, 2) : "";
  }, [row]);
  const [metaDraft, setMetaDraft] = useState(metadataValue);
  const [metaError, setMetaError] = useState("");

  useEffect(() => {
    setMetaDraft(metadataValue);
    setMetaError("");
  }, [metadataValue, index]);

  function applyMetadata() {
    if (!metaDraft.trim()) {
      onChange({} as Partial<DatasetRow>);
      setMetaError("");
      return;
    }
    try {
      const parsed = JSON.parse(metaDraft) as Record<string, unknown>;
      const { id, vendor, model, scenario_type, topic, difficulty, question,
        alternative_queries, expected_documents, expected_sections, expected_keywords, evaluation_focus, ...rest } = row as DatasetRow & Record<string, unknown>;
      const merged: DatasetRow = {
        ...row,
        ...rest,
        ...parsed
      } as DatasetRow;
      onChange(merged);
      setMetaError("");
    } catch (err) {
      setMetaError(err instanceof Error ? err.message : "JSON 解析失败");
    }
  }

  return (
    <section className="panel dataset-editor-panel">
      <PanelHeader
        icon={<ChevronDown size={18} />}
        title={`第 ${index + 1} 行详情`}
        subtitle={(row.id as string) || "未填写 ID"}
        action={
          <button type="button" className="ghost-link" onClick={onClose}>
            <X size={14} /> 收起
          </button>
        }
      />
      <div className="drawer-status-row">
        <span className={`ui-badge drawer-status-chip ${modified ? "changed" : "saved"}`}>
          {modified ? "本行有未保存修改" : "本行无未保存修改"}
        </span>
        {errors.length > 0 && (
          <span className="ui-badge drawer-status-chip error">校验问题 {errors.length}</span>
        )}
      </div>
      {errors.length > 0 && (
        <div className="drawer-error-list">
          {errors.map((err, errIndex) => (
            <span key={`${err.field || "row"}-${errIndex}`}>
              {err.field ? `${labelOf(err.field)}：` : ""}{err.message}
            </span>
          ))}
        </div>
      )}
      <div className="drawer-grid detail-editor-grid">
        {(["id", "vendor", "model", "scenario_type", "topic"] as CellField[]).map((field) => (
          <label key={field} className="drawer-section detail-field">
            <strong>{labelOf(field)}</strong>
            <input
              value={(row[field] as string) || ""}
              onChange={(event) => onChange({ [field]: event.target.value } as Partial<DatasetRow>)}
              placeholder={`填写${labelOf(field)}`}
            />
          </label>
        ))}
        <label className="drawer-section detail-field">
          <strong>难度</strong>
          <StandardSelect
            value={(row.difficulty as string) || ""}
            title={row.difficulty ? `当前难度：${row.difficulty}` : "选择难度"}
            onChange={(event) => onChange({ difficulty: event.target.value })}
          >
            <option value="">未指定</option>
            <option value="基础">基础</option>
            <option value="中等">中等</option>
            <option value="高级">高级</option>
          </StandardSelect>
        </label>
        <label className="drawer-section detail-field long">
          <strong>主问题</strong>
          <textarea
            rows={4}
            value={(row.question as string) || ""}
            onChange={(event) => onChange({ question: event.target.value })}
            placeholder="填写主问题"
          />
        </label>
        <label className="drawer-section detail-field long">
          <strong>评估关注点</strong>
          <textarea
            rows={5}
            value={(row.evaluation_focus as string) || ""}
            onChange={(event) => onChange({ evaluation_focus: event.target.value })}
            placeholder="填写评估关注点"
          />
        </label>
        {LIST_FIELDS.map((field) => (
          <div key={field} className="drawer-section detail-list-section">
            <strong>{labelOf(field)}</strong>
            <CompactTagList
              items={toStringList(row[field])}
              field={field}
              onAdd={(value) => onAddList(field, value)}
              onRemove={(valueIndex) => onRemoveList(field, valueIndex)}
              onEdit={(valueIndex, next) => onEditList(field, valueIndex, next)}
              maxInlineTags={100}
            />
          </div>
        ))}
        <div className="drawer-section metadata-section">
          <strong>其他元数据（JSON 透传）</strong>
          <textarea
            rows={6}
            value={metaDraft}
            onChange={(event) => setMetaDraft(event.target.value)}
            placeholder='例如 {"generated_by": "manual", "heading_path": ["1.1"]}'
          />
          {metaError && <div className="error-line">{metaError}</div>}
          <div>
            <button type="button" className="ghost-button" onClick={applyMetadata}>
              应用元数据
            </button>
          </div>
        </div>
      </div>
    </section>
  );
}

interface JsonlPreviewModalProps {
  content: string;
  onClose: () => void;
  onDownload: () => void;
}

function JsonlPreviewModal({ content, onClose, onDownload }: JsonlPreviewModalProps) {
  return (
    <div className="modal-mask" onClick={onClose}>
      <div className="modal-card" onClick={(event) => event.stopPropagation()}>
        <PanelHeader
          icon={<FileText size={18} />}
          title="原始数据预览"
          subtitle="保存前的最终落盘内容"
          action={
            <button type="button" className="ghost-link" onClick={onClose}>
              <X size={14} /> 关闭
            </button>
          }
        />
        <pre className="jsonl-preview">{content || "（暂无行）"}</pre>
        <div className="modal-actions">
          <button type="button" className="primary-button inline" onClick={onDownload}>
            <Download size={16} /> 下载当前编辑结果
          </button>
        </div>
      </div>
    </div>
  );
}
