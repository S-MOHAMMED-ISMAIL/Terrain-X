import { describe, expect, it } from "vitest";
import type { AnalysisJob } from "@/api/types";
import {
  calibrationPresentation,
  groundProductPresentation,
  verticalUnit,
} from "./analysisPresentation";

function job(overrides: Partial<AnalysisJob> = {}): AnalysisJob {
  return {
    id: "job-1",
    project_id: "project-1",
    dataset_id: "dataset-1",
    user_id: "user-1",
    status: "completed",
    current_stage: "completed",
    progress: 100,
    error_message: null,
    parameters: {},
    execution_summary: null,
    calibration_status: "uncalibrated",
    calibration_metadata: null,
    semantic_status: "not_requested",
    semantic_metadata: null,
    ground_filter_status: "not_requested",
    ground_filter_metadata: null,
    disaster_status: "not_requested",
    disaster_metadata: null,
    created_at: "2026-09-29T10:00:00Z",
    started_at: "2026-09-29T10:00:01Z",
    completed_at: "2026-09-29T10:01:00Z",
    updated_at: "2026-09-29T10:01:00Z",
    ...overrides,
  };
}

describe("analysis result presentation", () => {
  it("distinguishes unavailable, processing, and validated calibration", () => {
    expect(calibrationPresentation(job())).toMatchObject({ state: "unavailable" });
    expect(calibrationPresentation(job({ calibration_status: "calibrating" }))).toMatchObject({ state: "processing" });
    expect(calibrationPresentation(job({ calibration_status: "calibrated" }))).toMatchObject({ state: "completed", label: "Validated" });
  });

  it("distinguishes quality rejection from execution failure", () => {
    const rejected = job({ calibration_status: "failed", calibration_metadata: { error: "Held-out skill below threshold", quality_gate: { passed: false, failed_criteria: [], not_evaluated: [], policy: {} } } });
    const failed = job({ calibration_status: "failed", calibration_metadata: { error: "Reference could not be sampled" } });
    expect(calibrationPresentation(rejected)).toEqual({ state: "rejected", label: "Rejected", reason: "Held-out skill below threshold" });
    expect(calibrationPresentation(failed)).toEqual({ state: "failed", label: "Failed", reason: "Reference could not be sampled" });
  });

  it("explains ground-product prerequisites and real failure reasons", () => {
    expect(groundProductPresentation("not_requested", job()).reason).toContain("calibrated DSM");
    expect(groundProductPresentation("processing", job())).toMatchObject({ state: "processing" });
    expect(groundProductPresentation("completed", job())).toMatchObject({ state: "completed" });
    expect(groundProductPresentation("failed", job({ ground_filter_metadata: { error: "No stable ground surface" } }))).toEqual({ state: "failed", label: "Failed", reason: "No stable ground surface" });
  });

  it("only displays a vertical unit when metadata marks it known", () => {
    expect(verticalUnit(job())).toBe("Unknown");
    expect(verticalUnit(job({ calibration_metadata: { vertical_unit: { status: "unknown", unit: "m" } } }))).toBe("Unknown");
    expect(verticalUnit(job({ calibration_metadata: { vertical_unit: { status: "known", unit: "m" } } }))).toBe("m");
  });
});
