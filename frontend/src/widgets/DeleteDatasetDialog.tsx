import { useEffect, useId, useState } from "react";
import { AlertTriangle, Trash2, X } from "lucide-react";

export interface DeleteDatasetDialogProps {
  datasetName: string;
  datasetPath: string;
  sampleCount: number;
  busy?: boolean;
  error?: string;
  onCancel: () => void;
  onConfirm: () => void;
}

/**
 * 强化的"删除评测集"二次确认弹窗。
 *
 * 为了避免误操作点掉整份评测集，用户必须：
 * 1. 在文本框里准确输入 ``datasetName`` 才能激活"确认删除"按钮。
 * 2. 看到这次删除会产生的副作用（备份策略 + 草稿/审核元信息一并清理）。
 */
export function DeleteDatasetDialog({
  datasetName,
  datasetPath,
  sampleCount,
  busy = false,
  error = "",
  onCancel,
  onConfirm
}: DeleteDatasetDialogProps) {
  const [typed, setTyped] = useState("");
  const titleId = useId();

  useEffect(() => {
    // 切换数据集时清空已输入的内容，避免上一轮的输入残留
    setTyped("");
  }, [datasetName]);

  const matched = typed.trim() === datasetName.trim() && datasetName.trim().length > 0;

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
            <h2 id={titleId}>删除评测集</h2>
            <p>
              此操作会永久删除评测集文件，并清理对应的草稿与审核元信息。
              <br />
              删除前会自动生成一份带时间戳的备份，以便误删后恢复。
            </p>
          </div>
          <button
            type="button"
            className="icon-button"
            aria-label="关闭"
            disabled={busy}
            onClick={onCancel}
          >
            <X size={16} />
          </button>
        </div>
        <div className="delete-dataset-body">
          <dl className="delete-dataset-meta">
            <div>
              <dt>名称</dt>
              <dd>{datasetName}</dd>
            </div>
            <div>
              <dt>路径</dt>
              <dd>{datasetPath}</dd>
            </div>
            <div>
              <dt>样本数</dt>
              <dd>{sampleCount}</dd>
            </div>
          </dl>
          <label className="delete-dataset-confirm">
            <span>
              请输入 <strong>{datasetName}</strong> 以确认删除
            </span>
            <input
              autoFocus
              value={typed}
              disabled={busy}
              onChange={(event) => setTyped(event.target.value)}
              placeholder={datasetName}
              spellCheck={false}
            />
          </label>
          {error && (
            <div className="error-line delete-dataset-error" role="alert">
              {error}
            </div>
          )}
        </div>
        <div className="confirm-actions">
          <button type="button" className="ghost-button" disabled={busy} onClick={onCancel}>
            取消
          </button>
          <button
            type="button"
            className="primary-button inline danger"
            disabled={!matched || busy}
            onClick={onConfirm}
          >
            <Trash2 size={16} />
            {busy ? "正在删除..." : "确认删除"}
          </button>
        </div>
      </section>
    </div>
  );
}
