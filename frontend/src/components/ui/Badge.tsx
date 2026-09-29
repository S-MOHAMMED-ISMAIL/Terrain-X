import type { ReactNode } from "react";
import { Tooltip } from "./Tooltip";

export type BadgeTone = "neutral" | "info" | "success" | "warning" | "danger" | "brand";

const TONE_CLASSES: Record<BadgeTone, string> = {
  neutral: "bg-slate-100 text-slate-600",
  info: "bg-sky-100 text-sky-700",
  success: "bg-green-100 text-green-700",
  warning: "bg-amber-100 text-amber-700",
  danger: "bg-red-100 text-red-700",
  brand: "bg-brand-100 text-brand-800",
};

interface Props {
  tone?: BadgeTone;
  children: ReactNode;
  title?: string;
  dot?: boolean;
  className?: string;
}

/** A single, consistent status-pill primitive. Callers decide the tone and
 * text from real backend status values — this component never invents a
 * status, it only renders whichever real one it's given consistently. */
export function Badge({ tone = "neutral", children, title, dot = false, className = "" }: Props) {
  const badge = (
    <span
      tabIndex={title ? 0 : undefined}
      className={`inline-flex min-h-5 items-center gap-1.5 rounded-full px-2 py-0.5 text-metadata font-medium ${TONE_CLASSES[tone]} ${className}`}
    >
      {dot && <span className="h-1.5 w-1.5 rounded-full bg-current" aria-hidden="true" />}
      {children}
    </span>
  );
  return title ? <Tooltip content={title}>{badge}</Tooltip> : badge;
}

export const StatusBadge = Badge;
