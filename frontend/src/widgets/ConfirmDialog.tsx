import { useId } from "react";
import { AlertTriangle, X } from "lucide-react";

export interface ConfirmDialogProps {
  title: string;
  message: string;
  confirmText: string;
  cancelText?: string;
  tone?: "primary" | "warning" | "danger";
  onConfirm: () => void;
  onCancel: () => void;
}

export function ConfirmDialog({
  title,
  message,
  confirmText,
  cancelText = "取消",
  tone = "primary",
  onConfirm,
  onCancel
}: ConfirmDialogProps) {
  const titleId = useId();
  const lines = message.split("\n");

  return (
    <div className="modal-mask" role="presentation" onClick={onCancel}>
      <section
        className={`ui-dialog-card confirm-card ${tone}`}
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
            <h2 id={titleId}>{title}</h2>
            <p>
              {lines.map((line, index) => (
                <span key={`${line}-${index}`}>
                  {line}
                  {index < lines.length - 1 && <br />}
                </span>
              ))}
            </p>
          </div>
          <button type="button" className="icon-button" aria-label="关闭" onClick={onCancel}>
            <X size={16} />
          </button>
        </div>
        <div className="confirm-actions">
          <button type="button" className="ghost-button" onClick={onCancel}>
            {cancelText}
          </button>
          <button type="button" className={`primary-button inline ${tone}`} onClick={onConfirm}>
            {confirmText}
          </button>
        </div>
      </section>
    </div>
  );
}
