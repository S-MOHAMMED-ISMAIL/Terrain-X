import type { AnalysisJob, GroundFilterStatus } from "@/api/types";

export type OperationalState = "available" | "processing" | "completed" | "failed" | "rejected" | "unavailable";

export function calibrationPresentation(job: AnalysisJob): { state: OperationalState; label: string; reason: string | null } {
  if (job.calibration_status === "calibrating") return { state: "processing", label: "Processing", reason: null };
  if (job.calibration_status === "calibrated") return { state: "completed", label: "Validated", reason: null };
  if (job.calibration_status === "failed") {
    const rejected = job.calibration_metadata?.quality_gate?.passed === false;
    return {
      state: rejected ? "rejected" : "failed",
      label: rejected ? "Rejected" : "Failed",
      reason: job.calibration_metadata?.error ?? "Metric calibration did not complete.",
    };
  }
  return { state: "unavailable", label: "Not available", reason: "No DEM or GCP reference was requested." };
}

export function groundProductPresentation(status: GroundFilterStatus, job: AnalysisJob) {
  if (status === "processing") return { state: "processing" as const, label: "Processing", reason: null };
  if (status === "completed") return { state: "completed" as const, label: "Complete", reason: null };
  if (status === "failed") {
    const metadata = job.ground_filter_metadata as { error?: string } | null;
    return { state: "failed" as const, label: "Failed", reason: metadata?.error ?? "Ground filtering did not complete." };
  }
  return {
    state: "unavailable" as const,
    label: "Unavailable",
    reason: "Requires a calibrated DSM that passed the calibration quality gate.",
  };
}

export function verticalUnit(job: AnalysisJob): string {
  const unit = job.calibration_metadata?.vertical_unit as { status?: string; unit?: string } | undefined;
  return unit?.status === "known" && unit.unit ? unit.unit : "Unknown";
}
