import type { ReactNode } from "react";

interface Props {
  icon?: ReactNode;
  title: string;
  description?: string;
  action?: ReactNode;
}

/** The one "there is genuinely nothing here yet" presentation used
 * throughout the app — real absence of backend data, never a fabricated
 * placeholder value standing in for one. */
export function EmptyState({ icon, title, description, action }: Props) {
  return (
    <div className="flex min-w-0 flex-col items-center gap-2 rounded-panel border border-dashed border-slate-300 bg-slate-50 px-4 py-8 text-center animate-fade-in sm:px-6 sm:py-10">
      {icon && <div className="text-slate-300">{icon}</div>}
      <p className="text-control font-medium text-slate-700">{title}</p>
      {description && <p className="max-w-sm text-supporting text-slate-500">{description}</p>}
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}
