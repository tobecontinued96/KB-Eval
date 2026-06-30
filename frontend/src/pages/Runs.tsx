import { useCallback, useEffect, useId, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import {
  AlertTriangle,
  BarChart3,
  Clock3,
  FileJson,
  Loader2,
  RefreshCw,
  Trash2,
  X
} from "lucide-react";
import { deleteRun, listRuns } from "../api";
import { readCurrentDifyUrl } from "../difySource";
import type { EvalRunListItem } from "../types";
import { formatDateTime, formatDuration, formatPercent, metricTone } from "../utils";
import {
  areAllManageableRunsSelected,
  buildBulkDeleteMessage,
  canManageRun,
  getManageableRunIds,
  getRunHistoryMetricK,
  getRunHistoryRecallMetric,
  getSelectedRuns,
  pruneManagedSelection,
  toggleManagedSelection
} from "./runHistoryHelpers";
import { DeleteSuccessToast } from "../widgets/DeleteSuccessToast";
import { showErrorToast } from "../widgets/ErrorToast";
import { PanelHeader } from "../widgets/PanelHeader";
import { StatusBadge } from "../widgets/StatusBadge";

const LOAD_ERROR_MESSAGE = "历史记录加载失败";

export function Runs() {
  const navigate = useNavigate();
  const location = useLocation();
  const [runs, setRuns] = useState<EvalRunListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [selectedRunIds, setSelectedRunIds] = useState<string[]>([]);
  const [bulkDeleteOpen, setBulkDeleteOpen] = useState(false);
  const [bulkDeleteBusy, setBulkDeleteBusy] = useState(false);
  const [bulkDeleteError, setBulkDeleteError] = useState("");
  const [lastDeleted, setLastDeleted] = useState<{ id: string; name: string } | null>(null);
  // 与 RunCompare 一致：只读取成功连接后记录的当前 Dify URL，按它过滤历史列表。
  // 用 lazy initializer 只读一次 —— 历史页内不修改它。
  const [currentDifyUrl] = useState<string | null>(() => readCurrentDifyUrl());

  const load = useCallback(async (options?: { showLoading?: boolean }) => {
    const showLoading = options?.showLoading ?? false;
    try {
      if (showLoading) {
        setLoading(true);
      }
      const params = currentDifyUrl ? { difyBaseUrl: currentDifyUrl } : {};
      const result = await listRuns(params);
      setRuns(result.items);
    } catch (err) {
      showErrorToast({ title: err instanceof Error ? err.message : LOAD_ERROR_MESSAGE, code: "unknown" });
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [currentDifyUrl]);

  useEffect(() => {
    void load({ showLoading: true });
  }, [load]);

  useEffect(() => {
    const timer = window.setInterval(() => {
      void load();
    }, 10_000);
    return () => window.clearInterval(timer);
  }, [load]);

  useEffect(() => {
    if (!lastDeleted) return;
    const timer = window.setTimeout(() => setLastDeleted(null), 1500);
    return () => window.clearTimeout(timer);
  }, [lastDeleted]);

  useEffect(() => {
    setSelectedRunIds((current) => pruneManagedSelection(current, runs));
  }, [runs]);

  const manageableRunIds = getManageableRunIds(runs);
  const selectedRuns = getSelectedRuns(runs, selectedRunIds);
  const allManageableSelected = areAllManageableRunsSelected(runs, selectedRunIds);

  const handleRefresh = useCallback(() => {
    setRefreshing(true);
    void load();
  }, [load]);

  const handleToggleSelectAll = useCallback(() => {
    setBulkDeleteError("");
    setSelectedRunIds((current) => {
      if (current.length === manageableRunIds.length) {
        return [];
      }
      return manageableRunIds;
    });
  }, [manageableRunIds]);

  const handleToggleSelection = useCallback((run: EvalRunListItem) => {
    if (!canManageRun(run)) return;
    setBulkDeleteError("");
    setSelectedRunIds((current) => toggleManagedSelection(current, run.id));
  }, []);

  const handleOpenBulkDelete = useCallback(() => {
    if (selectedRuns.length === 0) return;
    setBulkDeleteError("");
    setBulkDeleteOpen(true);
  }, [selectedRuns.length]);

  const handleCloseBulkDelete = useCallback(() => {
    if (bulkDeleteBusy) return;
    setBulkDeleteOpen(false);
    setBulkDeleteError("");
  }, [bulkDeleteBusy]);

  const handleConfirmBulkDelete = useCallback(async () => {
    if (selectedRuns.length === 0) return;
    setBulkDeleteBusy(true);
    setBulkDeleteError("");

    const failedRuns: EvalRunListItem[] = [];
    let successCount = 0;

    for (const run of selectedRuns) {
      try {
        await deleteRun(run.id);
        successCount += 1;
      } catch {
        failedRuns.push(run);
      }
    }

    if (successCount > 0) {
      setLastDeleted({
        id: selectedRuns.map((run) => run.id).join(","),
        name: successCount === 1 ? `${selectedRuns[0].name || selectedRuns[0].id}（${selectedRuns[0].id}）` : `${successCount} 条历史记录`
      });
    }

    await load();

    if (failedRuns.length > 0) {
      const preview = failedRuns
        .slice(0, 3)
        .map((run) => run.name || run.id)
        .join("、");
      setBulkDeleteError(
        `已成功删除 ${successCount} 条，但还有 ${failedRuns.length} 条删除失败：${preview}${failedRuns.length > 3 ? " 等" : ""}`
      );
      setSelectedRunIds(failedRuns.map((run) => run.id));
    } else {
      setBulkDeleteOpen(false);
      setSelectedRunIds([]);
    }

    setBulkDeleteBusy(false);
  }, [load, selectedRuns]);

  return (
    <div className="dashboard-grid">
      <div className="workspace-stack full-span">
        <section className="panel history-panel">
          <div className="history-heading">
            <PanelHeader
              icon={<Clock3 size={18} />}
              title="历史评测"
              subtitle="每 10 秒轻量刷新一次，可进入详情查看报告与产物"
            />
            <div className="history-actions">
              <Link className="ghost-link" to="/compare">
                <BarChart3 size={16} />
                分析对比
              </Link>
              <button className="ghost-button" type="button" onClick={handleRefresh} disabled={refreshing}>
                <RefreshCw size={16} className={refreshing ? "spin" : ""} />
                刷新
              </button>
            </div>
          </div>

          {!loading && runs.length > 0 && (
            <div className="history-bulk-toolbar">
              <div className="history-bulk-summary">
                <span>已选 {selectedRunIds.length} 条，可删除 {manageableRunIds.length} 条</span>
              </div>
              <div className="history-bulk-actions">
                <button
                  className="primary-button inline danger"
                  type="button"
                  onClick={handleOpenBulkDelete}
                  disabled={selectedRunIds.length === 0 || bulkDeleteBusy}
                >
                  <Trash2 size={16} />
                  批量删除
                </button>
              </div>
            </div>
          )}

          {loading ? (
            <div className="empty-state">
              <Loader2 size={24} className="spin" />
              正在加载评测运行...
            </div>
          ) : (
            <RunHistoryTable
              runs={runs}
              selectedRunIds={selectedRunIds}
              allManageableSelected={allManageableSelected}
              onOpen={(runId) =>
                navigate(`/runs/${runId}`, {
                  state: { from: { pathname: "/runs", search: location.search } }
                })
              }
              onToggleSelection={handleToggleSelection}
              onToggleSelectAll={handleToggleSelectAll}
              actionsDisabled={bulkDeleteBusy}
            />
          )}
        </section>
      </div>

      {bulkDeleteOpen && (
        <BulkDeleteRunsDialog
          runs={selectedRuns}
          busy={bulkDeleteBusy}
          error={bulkDeleteError}
          onCancel={handleCloseBulkDelete}
          onConfirm={() => void handleConfirmBulkDelete()}
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

function RunHistoryTable({
  runs,
  selectedRunIds,
  allManageableSelected,
  onOpen,
  onToggleSelection,
  onToggleSelectAll,
  actionsDisabled
}: {
  runs: EvalRunListItem[];
  selectedRunIds: string[];
  allManageableSelected: boolean;
  onOpen: (runId: string) => void;
  onToggleSelection: (run: EvalRunListItem) => void;
  onToggleSelectAll: () => void;
  actionsDisabled: boolean;
}) {
  if (runs.length === 0) {
    return (
      <div className="empty-state">
        <FileJson size={24} />
        暂无评测运行
      </div>
    );
  }

  const selectedSet = new Set(selectedRunIds);

  return (
    <div className="table-wrap">
      <div className="run-table header">
        <span className="run-select-cell">
          <input
            type="checkbox"
            checked={allManageableSelected}
            aria-label={allManageableSelected ? "取消全选可删除历史" : "全选可删除历史"}
            onChange={onToggleSelectAll}
          />
        </span>
        <span>运行</span>
        <span>模型</span>
        <span>状态</span>
        <span>核心指标</span>
        <span>样本</span>
        <span>耗时</span>
        <span>创建时间</span>
      </div>
      {runs.map((run) => {
        const selectable = canManageRun(run);
        const selected = selectedSet.has(run.id);
        const recallMetric = getRunHistoryRecallMetric(run);
        const metricK = recallMetric?.k ?? getRunHistoryMetricK(run);
        const recall = recallMetric?.value;
        const recallLabel = recallMetric?.axis === "document" ? "Document Recall" : "Content Recall";
        return (
          <div
            className={`run-table row${selected ? " selected" : ""}`}
            key={run.id}
            role="button"
            tabIndex={0}
            onClick={() => onOpen(run.id)}
            onKeyDown={(event) => {
              if (event.key !== "Enter" && event.key !== " ") return;
              event.preventDefault();
              onOpen(run.id);
            }}
          >
            <span className="run-select-cell">
              <input
                type="checkbox"
                checked={selected}
                disabled={!selectable || actionsDisabled}
                aria-label={`选择 ${run.name || "未命名运行"}`}
                onClick={(event) => event.stopPropagation()}
                onChange={() => onToggleSelection(run)}
              />
            </span>
            <span className="run-title">
              <span className="run-title-main" title={run.name || "未命名运行"}>
                <b>{run.name || "未命名运行"}</b>
              </span>
            </span>
            <span className="run-title-models">
              <span
                className={`ui-chip run-model-chip${run.embedding_model ? "" : " is-empty"}`}
                title={`Embedding 模型：${run.embedding_model || "（空）"}`}
              >
                <span className="run-model-chip-key">Emb</span>
                <span className="run-model-chip-val">{run.embedding_model || "（空）"}</span>
              </span>
              <span
                className={`ui-chip run-model-chip${run.rerank_model ? "" : " is-empty"}`}
                title={`Rerank 模型：${run.rerank_model || "（空）"}`}
              >
                <span className="run-model-chip-key">Rerank</span>
                <span className="run-model-chip-val">{run.rerank_model || "（空）"}</span>
              </span>
            </span>
            <span>
              <StatusBadge status={run.status} />
            </span>
            <span className="metric-pair">
              <b className={metricTone(recall)} title={`${recallLabel}@${metricK}`}>
                {formatPercent(recall)}
              </b>
              <small>MRR（平均倒数排名）{(run.metrics.content_mrr ?? run.metrics.document_mrr)?.toFixed(3) || "--"}</small>
            </span>
            <span>{run.query_count || run.sample_count}</span>
            <span>{formatDuration(run.duration_ms)}</span>
            <span>{formatDateTime(run.created_at)}</span>
          </div>
        );
      })}
    </div>
  );
}

function BulkDeleteRunsDialog({
  runs,
  busy,
  error,
  onCancel,
  onConfirm
}: {
  runs: EvalRunListItem[];
  busy: boolean;
  error: string;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const titleId = useId();
  const messageLines = buildBulkDeleteMessage(runs).split("\n");

  return (
    <div className="modal-mask" role="presentation" onClick={busy ? undefined : onCancel}>
      <section
        className="ui-dialog-card confirm-card danger"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        onClick={(event) => event.stopPropagation()}
      >
        <div className="confirm-head">
          <div className="confirm-icon">
            <AlertTriangle size={18} />
          </div>
          <div>
            <h2 id={titleId}>批量删除历史记录</h2>
            <p>
              {messageLines.map((line, index) => (
                <span key={`${line}-${index}`}>
                  {line}
                  {index < messageLines.length - 1 && <br />}
                </span>
              ))}
            </p>
          </div>
          <button type="button" className="icon-button" aria-label="关闭" disabled={busy} onClick={onCancel}>
            <X size={16} />
          </button>
        </div>
        <div className="history-delete-preview" role="list">
          {runs.slice(0, 5).map((run) => (
            <div className="history-delete-preview-item" key={run.id} role="listitem">
              <strong>{run.name || run.id}</strong>
              <span>{run.id}</span>
            </div>
          ))}
          {runs.length > 5 && <div className="history-delete-preview-more">还有 {runs.length - 5} 条未展开</div>}
        </div>
        <div className="confirm-actions">
          <button type="button" className="ghost-button" disabled={busy} onClick={onCancel}>
            取消
          </button>
          <button type="button" className="primary-button inline danger" disabled={busy} onClick={onConfirm}>
            {busy ? <Loader2 size={16} className="spin" /> : <Trash2 size={16} />}
            {busy ? "正在删除..." : `确认删除 ${runs.length} 条`}
          </button>
        </div>
      </section>
    </div>
  );
}
