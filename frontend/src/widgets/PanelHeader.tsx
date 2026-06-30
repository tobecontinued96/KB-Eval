import type { ReactNode } from "react";

export function PanelHeader({
  icon,
  title,
  subtitle,
  action
}: {
  icon: ReactNode;
  title: string;
  subtitle: string;
  action?: ReactNode;
}) {
  return (
    <div className="panel-header">
      <div className="panel-icon">{icon}</div>
      <div className="panel-header-text">
        <h2>{title}</h2>
        <p>{subtitle}</p>
      </div>
      {action && <div className="panel-header-action">{action}</div>}
    </div>
  );
}
