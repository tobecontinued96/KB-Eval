/**
 * Global error toast — renders a transient error message in the top-right
 * corner, matching the visual style of ``DeleteSuccessToast``.
 *
 * Usage:
 *   - Mount ``<ErrorToastContainer />`` once near the app root (App.tsx).
 *   - Call ``showErrorToast(view)`` from any catch site. The view comes from
 *     ``describeError(code, status, fallbackMessage)``.
 *
 * Multiple toasts stack vertically. Each auto-dismisses after
 * ``ERROR_TOAST_DURATION_MS`` (5s) and can be closed by clicking the X.
 */

import { useEffect, useState } from "react";
import { AlertCircle, X } from "lucide-react";
import type { ErrorView } from "../errorCodes";

export const ERROR_TOAST_DURATION_MS = 5000;

interface ToastEntry {
  id: number;
  view: ErrorView;
}

let nextId = 1;
const listeners = new Set<(toasts: ToastEntry[]) => void>();
let currentToasts: ToastEntry[] = [];

function emit() {
  for (const listener of listeners) listener(currentToasts);
}

function push(view: ErrorView): number {
  const id = nextId++;
  currentToasts = [...currentToasts, { id, view }];
  emit();
  return id;
}

function dismiss(id: number) {
  currentToasts = currentToasts.filter((t) => t.id !== id);
  emit();
}

/**
 * Show an error toast. Safe to call from any module — it forwards the
 * precomputed ``ErrorView`` from ``describeError(...)`` directly to the
 * container. Returns the toast id so callers can dismiss it early if needed.
 */
export function showErrorToast(view: ErrorView): number {
  return push(view);
}

/** Dismiss a specific toast by id (e.g. when the user navigates away). */
export function dismissErrorToast(id: number): void {
  dismiss(id);
}

export function ErrorToastContainer() {
  const [toasts, setToasts] = useState<ToastEntry[]>(currentToasts);

  useEffect(() => {
    listeners.add(setToasts);
    return () => {
      listeners.delete(setToasts);
    };
  }, []);

  if (toasts.length === 0) return null;
  return (
    <div className="error-toast-stack" role="region" aria-label="错误提示">
      {toasts.map((toast) => (
        <ErrorToastItem key={toast.id} entry={toast} onClose={() => dismiss(toast.id)} />
      ))}
    </div>
  );
}

function ErrorToastItem({
  entry,
  onClose,
}: {
  entry: ToastEntry;
  onClose: () => void;
}) {
  useEffect(() => {
    const timer = window.setTimeout(onClose, ERROR_TOAST_DURATION_MS);
    return () => window.clearTimeout(timer);
  }, [onClose]);

  return (
    <div className="app-toast app-toast--error error-toast" role="alert" aria-live="assertive">
      <AlertCircle size={20} />
      <div>
        <strong>{entry.view.title}</strong>
      </div>
      <button
        type="button"
        className="icon-button"
        aria-label="关闭错误提示"
        onClick={onClose}
      >
        <X size={15} />
      </button>
    </div>
  );
}
