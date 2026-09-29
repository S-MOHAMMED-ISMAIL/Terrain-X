import type { ReactNode } from "react";
import { Badge, type BadgeTone } from "./Badge";

interface MetricValueProps {
  value: ReactNode;
  unit?: ReactNode;
  label?: ReactNode;
}

export function MetricValue({ value, unit, label }: MetricValueProps) {
  return (
    <span className="inline-flex min-w-0 items-baseline gap-1 tabular-nums">
      {label && <span className="text-content-muted">{label}</span>}
      <span className="font-semibold text-inherit">{value}</span>
      {unit && <span className="text-supporting text-current">{unit}</span>}
    </span>
  );
}

interface CoordinateValueProps {
  coordinate: ReactNode;
  crs?: ReactNode;
}

export function CoordinateValue({ coordinate, crs }: CoordinateValueProps) {
  return (
    <span className="inline-flex min-w-0 flex-wrap items-baseline gap-x-2 font-mono text-metadata tabular-nums">
      <span>{coordinate}</span>
      {crs && <span className="text-content-muted">{crs}</span>}
    </span>
  );
}

interface QualityStateProps {
  state: string;
  label: string;
  explanation?: ReactNode;
  tone?: BadgeTone;
}

export function QualityState({ state, label, explanation, tone = "neutral" }: QualityStateProps) {
  return (
    <div className="min-w-0">
      <div className="flex min-w-0 flex-wrap items-center gap-2">
        <Badge tone={tone}>{state}</Badge>
        <span className="text-control font-medium">{label}</span>
      </div>
      {explanation && <div className="mt-1 text-supporting text-slate-500">{explanation}</div>}
    </div>
  );
}

export type DataStateValue = "available" | "unavailable" | "processing" | "failed";

const DATA_STATE: Record<DataStateValue, { label: string; tone: BadgeTone }> = {
  available: { label: "Available", tone: "success" },
  unavailable: { label: "Unavailable", tone: "neutral" },
  processing: { label: "Processing", tone: "info" },
  failed: { label: "Failed", tone: "danger" },
};

export function DataState({ state, detail }: { state: DataStateValue; detail?: ReactNode }) {
  const presentation = DATA_STATE[state];
  return (
    <span className="inline-flex min-w-0 flex-wrap items-center gap-2">
      <Badge tone={presentation.tone} dot={state === "processing"}>
        {presentation.label}
      </Badge>
      {detail && <span className="text-supporting text-slate-500">{detail}</span>}
    </span>
  );
}

export function TechnicalKeyValue({ label, value }: { label: ReactNode; value: ReactNode }) {
  return (
    <div className="grid min-w-0 grid-cols-[minmax(6rem,auto)_minmax(0,1fr)] gap-x-3 text-supporting">
      <dt className="text-slate-500">{label}</dt>
      <dd className="min-w-0 break-words font-mono text-slate-800 tabular-nums">{value}</dd>
    </div>
  );
}
