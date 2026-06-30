import { CheckCircle2, X } from "lucide-react";

export const DELETE_SUCCESS_TOAST_DURATION_MS = 1500;

export function DeleteSuccessToast({
  datasetName,
  onClose
}: {
  datasetName: string;
  onClose: () => void;
}) {
  return (
    <div className="app-toast app-toast--success success-toast" role="status" aria-live="polite">
      <CheckCircle2 size={20} />
      <div>
        <strong>删除成功</strong>
        <span>{datasetName} 已删除，备份已保留</span>
      </div>
      <button type="button" className="icon-button" aria-label="关闭删除成功提示" onClick={onClose}>
        <X size={15} />
      </button>
    </div>
  );
}
