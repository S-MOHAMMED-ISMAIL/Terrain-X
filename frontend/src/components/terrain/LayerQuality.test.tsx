// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { useState } from "react";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { AnalysisJob, LayerContext, LayerType, VisualizationContext } from "@/api/types";
import { CalibrationDiagnosticsPanel } from "./CalibrationDiagnosticsPanel";
import { CalibrationResidualsCard } from "./CalibrationResidualsCard";
import { LayerDetailsPanel } from "./LayerDetailsPanel";
import { LayerPanel } from "./LayerPanel";
import {
  layerDataState,
  layerUnitLabel,
  jobForLayer,
  workspaceQualityPresentation,
} from "./workspacePresentation";

afterEach(cleanup);

const visibility: Record<LayerType, boolean> = {
  rgb: true,
  relative_depth: false,
  metric_elevation: false,
  dsm: false,
  dtm: false,
  ndsm: false,
  semantic_segmentation: false,
  slope: false,
  aspect: false,
  hillshade: false,
  flood_screening: false,
  landslide_screening: false,
};

const opacity: Record<LayerType, number> = Object.fromEntries(
  Object.keys(visibility).map((key) => [key, 0.85]),
) as Record<LayerType, number>;

function layer(overrides: Partial<LayerContext> = {}): LayerContext {
  return {
    layer_type: "relative_depth",
    display_name: "Relative Depth (Uncalibrated)",
    available: true,
    unavailable_reason: null,
    artifact_id: "artifact-1",
    analysis_job_id: "job-1",
    width: 64,
    height: 32,
    dtype: "float32",
    nodata: -9999,
    is_georeferenced: false,
    crs: null,
    bounds: null,
    min_value: 0.1,
    max_value: 0.9,
    map_overlay_bounds: null,
    notes: "Unitless model-relative depth.",
    is_categorical: false,
    region_count: null,
    legend: null,
    ...overrides,
  };
}

function job(overrides: Partial<AnalysisJob> = {}): AnalysisJob {
  return {
    id: "job-1",
    project_id: "project-1",
    dataset_id: "dataset-1",
    user_id: "user-1",
    status: "completed",
    current_stage: "completed",
    progress: 1,
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
    created_at: "2026-09-28T00:00:00Z",
    started_at: "2026-09-28T00:00:00Z",
    completed_at: "2026-09-28T00:01:00Z",
    updated_at: "2026-09-28T00:01:00Z",
    ...overrides,
  };
}

function context(heightKind: "elevation" | "relative_depth", layers: LayerContext[]): VisualizationContext {
  return {
    dataset: {
      id: "dataset-1",
      original_filename: "terrain.tif",
      file_type: "tiff",
      width: 64,
      height: 32,
      is_georeferenced: heightKind === "elevation",
      crs: heightKind === "elevation" ? "EPSG:32613" : null,
      bounds: null,
      bounds_wgs84: null,
    },
    layers,
    terrain: {
      available: true,
      unavailable_reason: null,
      artifact_id: "artifact-1",
      analysis_job_id: "job-1",
      height_kind: heightKind,
      source_artifact_type: heightKind === "elevation" ? "dsm" : "relative_depth",
      width: 64,
      height: 32,
      texture_compatible: true,
      texture_unavailable_code: null,
      texture_unavailable_reason: null,
      is_georeferenced: heightKind === "elevation",
      crs: heightKind === "elevation" ? "EPSG:32613" : null,
      bounds: null,
      min_elevation: heightKind === "elevation" ? 100 : null,
      max_elevation: heightKind === "elevation" ? 180 : null,
      min_height_value: 0,
      max_height_value: 180,
    },
    calibration_residuals: {
      available: false,
      unavailable_reason: "No calibration residual artifact.",
      artifact_id: null,
      analysis_job_id: "job-1",
      reference_type: null,
      sample_count: null,
    },
  };
}

describe("Phase 4 layer state", () => {
  it("keeps active selection and visibility independent with keyboard activation", async () => {
    const user = userEvent.setup();
    const layers = [layer({ layer_type: "rgb", display_name: "RGB" }), layer()];
    function Harness() {
      const [active, setActive] = useState<LayerType>("rgb");
      const [shown, setShown] = useState(visibility);
      const quality = workspaceQualityPresentation(context("relative_depth", layers), active, job());
      return <LayerPanel layers={layers} activeLayer={active} onActiveLayerChange={setActive}
        visibility={shown} onToggleVisibility={(type) => setShown((old) => ({ ...old, [type]: !old[type] }))}
        opacity={opacity} onOpacityChange={() => undefined} job={job()} quality={quality} />;
    }
    render(<Harness />);
    const select = screen.getByRole("button", { name: /Relative Depth/ });
    select.focus();
    await user.keyboard("{Enter}");
    expect(select).toHaveAttribute("aria-pressed", "true");
    const row = select.closest('[role="listitem"]') as HTMLElement;
    const toggle = within(row).getByRole("checkbox", { name: "Visible" });
    expect(toggle).not.toBeChecked();
    await user.click(toggle);
    expect(toggle).toBeChecked();
    expect(select).toHaveAttribute("aria-pressed", "true");
  });

  it("disables unavailable layers and exposes the API reason", () => {
    const unavailable = layer({ available: false, artifact_id: null, unavailable_reason: "No completed analysis job yet." });
    const quality = workspaceQualityPresentation(context("relative_depth", [unavailable]), "relative_depth", job());
    render(<LayerPanel layers={[unavailable]} activeLayer="rgb" onActiveLayerChange={() => undefined}
      visibility={visibility} onToggleVisibility={() => undefined} opacity={opacity}
      onOpacityChange={() => undefined} job={job()} quality={quality} />);
    expect(screen.getByRole("button", { name: /Relative Depth/ })).toBeDisabled();
    expect(screen.getByText("No completed analysis job yet.")).toBeVisible();
    expect(screen.getByText("Unavailable")).toBeVisible();
  });

  it("distinguishes processing and failed artifact states from job fields", () => {
    const metric = layer({ layer_type: "dsm", display_name: "DSM", available: false });
    expect(layerDataState(metric, job({ calibration_status: "calibrating" })).state).toBe("processing");
    expect(layerDataState(metric, job({ calibration_status: "failed" })).state).toBe("failed");
  });

  it("uses a standalone disaster job for derivative and screening rows", () => {
    const source = job();
    const screening = job({ id: "screen-job", disaster_status: "processing", status: "running" });
    const slope = layer({ layer_type: "slope", available: false, artifact_id: null, analysis_job_id: null });
    const owningJob = jobForLayer(slope, [screening, source], source);
    expect(owningJob?.id).toBe("screen-job");
    expect(layerDataState(slope, owningJob).state).toBe("processing");
  });
});

describe("Phase 4 quality truth", () => {
  it("presents a successful metric calibration and declared vertical unit", () => {
    const dsm = layer({ layer_type: "dsm", display_name: "DSM", crs: "EPSG:32613" });
    const calibrated = job({ calibration_status: "calibrated", calibration_metadata: {
      vertical_unit: { status: "known", unit: "ft", unit_name: "foot", source: "vertical_crs" },
    } });
    const quality = workspaceQualityPresentation(context("elevation", [dsm]), "dsm", calibrated);
    expect(quality.mode).toBe("metric");
    expect(quality.calibration.label).toBe("Passed");
    expect(quality.verticalUnit.label).toBe("foot");
  });

  it("keeps relative-only output explicit when calibration is unavailable", () => {
    const relative = layer();
    const quality = workspaceQualityPresentation(context("relative_depth", [relative]), "relative_depth", job());
    expect(quality.mode).toBe("relative");
    expect(quality.verticalUnit.label).toBe("Unitless");
    expect(quality.calibration.label).toBe("Not available");
  });

  it("shows rejected calibration while preserving relative fallback", () => {
    const rejected = job({ calibration_status: "failed", calibration_metadata: {
      error: "Held-out RMSE exceeded the policy threshold.",
      quality_gate: { passed: false, failed_criteria: [], not_evaluated: [], policy: {} },
    } });
    const quality = workspaceQualityPresentation(context("relative_depth", [layer()]), "relative_depth", rejected);
    expect(quality.mode).toBe("relative");
    expect(quality.calibration.label).toBe("Rejected");
    expect(quality.calibration.detail).toContain("Held-out RMSE");
    expect(layerUnitLabel(layer({ layer_type: "metric_elevation", available: false }), quality))
      .toBe("Metric output unavailable");
  });

  it("does not assume a vertical unit for metric output", () => {
    const dsm = layer({ layer_type: "dsm", display_name: "DSM" });
    const quality = workspaceQualityPresentation(
      context("elevation", [dsm]),
      "dsm",
      job({ calibration_status: "calibrated", calibration_metadata: {} }),
    );
    expect(quality.verticalUnit).toEqual(expect.objectContaining({ label: "Unknown", known: false }));
  });
});

describe("Phase 4 details and diagnostics", () => {
  it("states that NoData is excluded rather than zero", () => {
    const active = layer();
    const quality = workspaceQualityPresentation(context("relative_depth", [active]), "relative_depth", job());
    render(<LayerDetailsPanel layer={active} job={job()} quality={quality} />);
    expect(screen.getByText("NoData: -9999 (excluded, not zero)")).toBeVisible();
    expect(screen.getByText(/not independent measurements/)).toBeVisible();
  });

  it("separates in-sample and held-out diagnostics", () => {
    const calibrated = job({ calibration_status: "calibrated", calibration_metadata: {
      fit_diagnostics: {
        scale: 1, offset: 0, valid_sample_count: 10, effective_sample_count: 9,
        all_sample_mae: 1, all_sample_rmse: 2, all_sample_bias: 0.1,
        in_sample_r2: 0.9, pearson_r: 0.95, spearman_rho: 0.94,
      },
      cross_validation: {
        method: "spatial_block", feasible: true, infeasibility_reason: null, fold_count: 4,
        heldout_sample_count: 10, heldout_mae: 3, heldout_rmse: 4, heldout_bias: 0.2,
        sse_cv: 1, sse_baseline_cv: 2, skill: 0.5, blocks_per_side: 2,
        non_empty_block_count: 4, folds: [],
      },
    } });
    const quality = workspaceQualityPresentation(context("elevation", [layer({ layer_type: "dsm" })]), "dsm", calibrated);
    render(<CalibrationDiagnosticsPanel job={calibrated} quality={quality} />);
    expect(screen.getByRole("heading", { name: "In-sample fit" })).toBeVisible();
    expect(screen.getByText("Fit diagnostics over the calibration samples; not validation.")).toBeVisible();
    expect(screen.getByRole("heading", { name: "Held-out validation" })).toBeVisible();
    expect(screen.getByText("spatial_block")).toBeVisible();
  });

  it("keeps residual visualization access and textual loading state", () => {
    render(<CalibrationResidualsCard context={{
      available: true, unavailable_reason: null, artifact_id: "residual-1", analysis_job_id: "job-1",
      reference_type: "dem", sample_count: 10,
    }} data={null} loading error={null} visible={false} onToggleVisible={() => undefined}
      kind="heldout" onKindChange={() => undefined} onDownload={() => undefined} is2d />);
    expect(screen.getByRole("checkbox", { name: "Show calibration residuals" })).toBeVisible();
    expect(screen.getByText("Loading residuals…")).toBeVisible();
    expect(screen.getByText("Held-out residual (validation view)")).toBeVisible();
  });
});
