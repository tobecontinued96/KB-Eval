import { CheckCircle2, CircleDashed, Loader2, OctagonAlert, XCircle } from "lucide-react";
import type { EvalRunStatus } from "../types";
import { statusLabel } from "../utils";

export function StatusBadge({ status }: { status: EvalRunStatus }) {
  const icon = {
    queued: <CircleDashed size={13} />,
    running: <Loader2 size={13} className="spin" />,
    completed: <CheckCircle2 size={13} />,
    failed: <OctagonAlert size={13} />,
    canceled: <XCircle size={13} />
  }[status];

  return (
    <span className={`ui-badge status-badge ${status}`}>
      {icon}
      {statusLabel(status)}
    </span>
  );
}
