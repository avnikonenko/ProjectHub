import type { ReactNode } from 'react';

export function SettingsHelp({ title, id, children }: {
  title: string;
  id?: string;
  children: ReactNode;
}) {
  return <details className="settings-help">
    <summary id={id}>{title}</summary>
    <div>{children}</div>
  </details>;
}
