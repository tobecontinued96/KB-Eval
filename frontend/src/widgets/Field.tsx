import type { ReactNode } from "react";

export function Field({
  label,
  children,
  className = "",
  hint,
  error
}: {
  label: string;
  children: ReactNode;
  className?: string;
  hint?: ReactNode;
  error?: ReactNode;
}) {
  return (
    <label className={`field ${className}`.trim()}>
      <span>{label}</span>
      {children}
      {hint && !error && <small className="field-hint">{hint}</small>}
      {error && (
        <small className="field-hint field-hint-error" role="alert">
          {error}
        </small>
      )}
    </label>
  );
}
