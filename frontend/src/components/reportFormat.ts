import type { ReportStatus } from "@/api/types";

// Pure, network-free helpers for the Reports panel — kept in their own
// module (no React/API imports) specifically so they can be unit-tested
// directly, mirroring components/terrain/measurementFormat.ts's precedent.

export const REPORT_STATUS_LABELS: Record<ReportStatus, string> = {
  pending: "Queued",
  generating: "Generating…",
  completed: "Completed",
  failed: "Failed",
};

// A report actively being produced by the worker — the UI polls while any
// report is in one of these states, exactly like AnalysisPanel's own
// active-job polling (never simulate progress independently of what the
// backend reports).
export function isReportActive(status: ReportStatus): boolean {
  return status === "pending" || status === "generating";
}

export function reportStatusLabel(status: ReportStatus): string {
  return REPORT_STATUS_LABELS[status];
}
