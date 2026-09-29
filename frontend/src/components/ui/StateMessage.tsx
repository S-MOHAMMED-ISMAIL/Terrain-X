import type { ReactNode } from "react";
import { Spinner } from "./Spinner";
import { Notice, type NoticeTone } from "./Notice";

interface StateMessageProps {
  title: string;
  description?: ReactNode;
  action?: ReactNode;
  className?: string;
}

export function LoadingState({
  title = "Loading",
  description,
  className = "",
}: Partial<StateMessageProps>) {
  return (
    <div
      role="status"
      aria-live="polite"
      aria-busy="true"
      className={`flex min-w-0 items-start gap-2 rounded-panel border border-slate-200 bg-slate-50 px-3 py-2 text-control text-slate-700 ${className}`}
    >
      <Spinner label={null} className="mt-0.5" />
      <div>
        <p className="font-medium">{title}</p>
        {description && <div className="mt-0.5 text-supporting text-slate-500">{description}</div>}
      </div>
    </div>
  );
}

function NoticeState({
  tone,
  title,
  description,
  action,
  announce,
  className = "",
}: StateMessageProps & { tone: NoticeTone; announce?: boolean }) {
  return (
    <Notice tone={tone} title={title} action={action} announce={announce} className={className}>
      {description}
    </Notice>
  );
}

export function ErrorState(props: StateMessageProps) {
  return <NoticeState {...props} tone="error" announce />;
}

export function SuccessState(props: StateMessageProps) {
  return <NoticeState {...props} tone="success" announce />;
}

export function ProcessingState(props: StateMessageProps) {
  return <NoticeState {...props} tone="info" announce />;
}
