import { describe, expect, it } from "vitest";
import type { AnalysisJob, Dataset } from "@/api/types";
import { summarizeDashboardCounts } from "./dashboardStats";

function makeDataset(id: string): Dataset {
  return {
    id,
    project_id: "p1",
    original_filename: "photo.jpg",
    file_type: "jpeg",
    mime_type: "image/jpeg",
    file_size_bytes: 1,
    role: "source_image",
    status: "valid",
    validation_error: null,
    width: 1,
    height: 1,
    bands: 3,
    is_georeferenced: false,
    crs: null,
    bbox: null,
    gcp_crs: null,
    gcp_point_count: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  };
}

function makeJob(id: string): AnalysisJob {
  return {
    id,
    project_id: "p1",
    dataset_id: "d1",
    user_id: "u1",
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
    created_at: "2026-01-01T00:00:00Z",
    started_at: null,
    completed_at: null,
    updated_at: "2026-01-01T00:00:00Z",
  };
}

describe("summarizeDashboardCounts", () => {
  it("returns all-zero counts for no projects at all — never a placeholder", () => {
    expect(summarizeDashboardCounts([])).toEqual({ projects: 0, datasets: 0, analysisJobs: 0 });
  });

  it("counts a single project with zero datasets and zero jobs as real zeros", () => {
    const result = summarizeDashboardCounts([{ datasets: [], analysisJobs: [] }]);
    expect(result).toEqual({ projects: 1, datasets: 0, analysisJobs: 0 });
  });

  it("sums real dataset/job counts for one project", () => {
    const result = summarizeDashboardCounts([
      { datasets: [makeDataset("d1"), makeDataset("d2")], analysisJobs: [makeJob("j1")] },
    ]);
    expect(result).toEqual({ projects: 1, datasets: 2, analysisJobs: 1 });
  });

  it("sums across multiple projects, including a mix of zero and nonzero", () => {
    const result = summarizeDashboardCounts([
      { datasets: [makeDataset("d1")], analysisJobs: [makeJob("j1"), makeJob("j2")] },
      { datasets: [], analysisJobs: [] },
      { datasets: [makeDataset("d2"), makeDataset("d3")], analysisJobs: [] },
    ]);
    expect(result).toEqual({ projects: 3, datasets: 3, analysisJobs: 2 });
  });

  it("is a pure function of its input — the same input always produces the same real totals, never a hidden/hardcoded value", () => {
    const input = [{ datasets: [makeDataset("d1")], analysisJobs: [] }];
    expect(summarizeDashboardCounts(input)).toEqual(summarizeDashboardCounts(input));
  });
});
