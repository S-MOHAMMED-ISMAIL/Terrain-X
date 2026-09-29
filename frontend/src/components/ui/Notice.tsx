import type { HTMLAttributes, ReactNode } from "react";

export type NoticeTone = "info" | "success" | "warning" | "error";

const TONES: Record<NoticeTone, string> = {
  info: "border-sky-200 bg-sky-50 text-sky-900",
  success: "border-green-200 bg-green-50 text-green-900",
  warning: "border-amber-200 bg-amber-50 text-amber-950",
  error: "border-red-200 bg-red-50 text-red-900",
};

interface NoticeProps extends HTMLAttributes<HTMLDivElement> {
  tone?: NoticeTone;
  title?: string;
  action?: ReactNode;
  announce?: boolean;
}

export function Notice({
  tone = "info",
  title,
  action,
  announce = false,
  className = "",
  children,
  ...rest
}: NoticeProps) {
  return (
    <div
      role={announce ? (tone === "error" ? "alert" : "status") : undefined}
      aria-live={announce ? (tone === "error" ? "assertive" : "polite") : undefined}
      className={`rounded-panel border px-3 py-2 text-control ${TONES[tone]} ${className}`}
      {...rest}
    >
      {title && <p className="font-semibold">{title}</p>}
      <div className={title ? "mt-0.5" : ""}>{children}</div>
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}

interface LiveStatusProps extends HTMLAttributes<HTMLDivElement> {
  children: ReactNode;
  priority?: "polite" | "assertive";
}

export function LiveStatus({ children, priority = "polite", ...rest }: LiveStatusProps) {
  return (
    <div role={priority === "assertive" ? "alert" : "status"} aria-live={priority} aria-atomic="true" {...rest}>
      {children}
    </div>
  );
}
