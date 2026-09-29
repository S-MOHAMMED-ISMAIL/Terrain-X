import type { ReactNode } from "react";
import { Badge, type BadgeTone } from "@/components/ui";

interface Props {
  title: string;
  status: string;
  tone?: BadgeTone;
  description?: ReactNode;
  children: ReactNode;
  testId?: string;
}

/** Shared operational-tool framing. State text is always visible; color is supplementary. */
export function WorkspaceToolSection({
  title,
  status,
  tone = "neutral",
  description,
  children,
  testId,
}: Props) {
  return (
    <section data-testid={testId} aria-label={title} className="border-b border-slate-200 pb-3 last:border-0 last:pb-0">
      <div className="flex min-w-0 items-start justify-between gap-2">
        <div className="min-w-0">
          <h3 className="text-panel-title text-slate-950">{title}</h3>
          {description && <div className="mt-0.5 text-metadata leading-snug text-slate-500">{description}</div>}
        </div>
        <Badge tone={tone}>{status}</Badge>
      </div>
      <div className="mt-3">{children}</div>
    </section>
  );
}
