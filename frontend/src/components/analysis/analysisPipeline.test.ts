import { describe, expect, it } from "vitest";
import type { AnalysisJob } from "@/api/types";
import { buildPipelineSteps, isDepthPipelineJob } from "./analysisPipeline";

function makeJob(overrides: Partial<AnalysisJob>): AnalysisJob {
  return {
    id: "j1",
    project_id: "p1",
    dataset_id: "d1",
    user_id: "u1",
    status: "queued",
    current_stage: "queued",
    progress: 0,
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
    created_at: "2026-01-01T00:00:00Z",
    started_at: null,
    completed_at: null,
    updated_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

describe("isDepthPipelineJob", () => {
  it("is true for a plain depth job with no disaster source artifact", () => {
    expect(isDepthPipelineJob(makeJob({ parameters: {} }))).toBe(true);
  });

  it("is false for a standalone disaster-screening job", () => {
    expect(
      isDepthPipelineJob(makeJob({ parameters: { disaster_source_artifact_id: "a1" } })),
    ).toBe(false);
  });
});

describe("buildPipelineSteps", () => {
  it("a plain job (no calibration, no semantic) only shows Input/Depth/Final", () => {
    const steps = buildPipelineSteps(makeJob({ parameters: {} }));
    expect(steps.map((s) => s.id)).toEqual(["input", "depth", "final"]);
  });

  it("a job with a DEM reference includes the Calibration step", () => {
    const steps = buildPipelineSteps(
      makeJob({ parameters: { dem_reference_dataset_id: "dem1" } }),
    );
    expect(steps.map((s) => s.id)).toContain("calibration");
  });

  it("a job with enable_semantic_segmentation includes the Semantic step", () => {
    const steps = buildPipelineSteps(
      makeJob({ parameters: { enable_semantic_segmentation: true } }),
    );
    expect(steps.map((s) => s.id)).toContain("semantic");
  });

  it("marks earlier real stages done and the real current_stage as current, never fabricating progress", () => {
    const steps = buildPipelineSteps(
      makeJob({ status: "running", current_stage: "inference", parameters: {} }),
    );
    const byId = Object.fromEntries(steps.map((s) => [s.id, s.state]));
    expect(byId.input).toBe("done");
    expect(byId.depth).toBe("current");
    expect(byId.final).toBe("pending");
  });

  it("a completed job shows every relevant step as done", () => {
    const steps = buildPipelineSteps(
      makeJob({ status: "completed", current_stage: "completed", parameters: {} }),
    );
    expect(steps.every((s) => s.state === "done")).toBe(true);
  });

  it("a failed job marks the stage it failed at as failed, earlier stages done, later stages pending", () => {
    const steps = buildPipelineSteps(
      makeJob({
        status: "failed",
        current_stage: "calibrating",
        parameters: { dem_reference_dataset_id: "dem1" },
      }),
    );
    const byId = Object.fromEntries(steps.map((s) => [s.id, s.state]));
    expect(byId.input).toBe("done");
    expect(byId.depth).toBe("done");
    expect(byId.calibration).toBe("failed");
    expect(byId.final).toBe("pending");
  });

  it("a cancelled job marks the checkpoint stage as failed, not silently done or hidden", () => {
    const steps = buildPipelineSteps(
      makeJob({ status: "cancelled", current_stage: "inference", parameters: {} }),
    );
    const byId = Object.fromEntries(steps.map((s) => [s.id, s.state]));
    expect(byId.depth).toBe("failed");
  });
});

describe("P1-3 ground-filter stages", () => {
  it("shows ground filtering as the current Calibration step, after the DSM is done", () => {
    for (const stage of ["filtering_ground", "writing_dtm", "writing_ndsm"] as const) {
      const steps = buildPipelineSteps(
        makeJob({
          status: "running",
          current_stage: stage,
          parameters: { dem_reference_dataset_id: "dem1" },
        }),
      );
      const byId = Object.fromEntries(steps.map((s) => [s.id, s.state]));
      expect(byId.depth).toBe("done");
      expect(byId.calibration).toBe("current");
      expect(byId.final).toBe("pending");
    }
  });
});
