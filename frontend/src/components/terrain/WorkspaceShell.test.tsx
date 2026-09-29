// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { useEffect, useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { AnalysisJob, LayerContext, VisualizationContext } from "@/api/types";
import { LayerPanel } from "./LayerPanel";
import { TerrainWorkspaceShell, type WorkspaceMode } from "./TerrainWorkspaceShell";
import { workspaceQualityItems, workspaceQualityPresentation } from "./workspacePresentation";

afterEach(cleanup);

const metricLayer: LayerContext = {
  layer_type: "dsm",
  display_name: "DSM",
  available: true,
  unavailable_reason: null,
  artifact_id: "artifact-1",
  analysis_job_id: "job-1",
  width: 64,
  height: 64,
  dtype: "float32",
  nodata: -9999,
  is_georeferenced: true,
  crs: "EPSG:32613",
  bounds: null,
  min_value: 100,
  max_value: 180,
  map_overlay_bounds: null,
  notes: "Estimated surface model.",
  is_categorical: false,
  region_count: null,
  legend: null,
};

const calibratedJob: AnalysisJob = {
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
  calibration_status: "calibrated",
  calibration_metadata: {
    vertical_unit: {
      status: "known",
      unit: "m",
      unit_name: "metre",
      source: "geotiff_band_unit",
    },
  },
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
};

function context(heightKind: "elevation" | "relative_depth"): VisualizationContext {
  return {
    dataset: {
      id: "dataset-1",
      original_filename: "terrain.tif",
      file_type: "tiff",
      width: 64,
      height: 64,
      is_georeferenced: heightKind === "elevation",
      crs: heightKind === "elevation" ? "EPSG:32613" : null,
      bounds: null,
      bounds_wgs84: null,
    },
    layers: [metricLayer],
    terrain: {
      available: true,
      unavailable_reason: null,
      artifact_id: "artifact-1",
      analysis_job_id: "job-1",
      height_kind: heightKind,
      source_artifact_type: heightKind === "elevation" ? "dsm" : "relative_depth",
      width: 64,
      height: 64,
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
      unavailable_reason: "No residual artifact.",
      artifact_id: null,
      analysis_job_id: null,
      reference_type: null,
      sample_count: null,
    },
  };
}

function PersistentViewport({ view, onMount }: { view: "2d" | "3d"; onMount: () => void }) {
  useEffect(onMount, [onMount]);
  return <div data-testid="persistent-viewport">{view}</div>;
}

function ShellHarness({ onViewportMount = () => undefined }: { onViewportMount?: () => void }) {
  const [mode, setMode] = useState<WorkspaceMode>("explore");
  const [view, setView] = useState<"2d" | "3d">("2d");

  return (
    <TerrainWorkspaceShell
      projectName="North Ridge"
      datasetName="terrain.tif"
      datasetControl={<select aria-label="Dataset"><option>terrain.tif</option></select>}
      viewControl={
        <>
          <button type="button" onClick={() => setView("2d")}>2D Map</button>
          <button type="button" onClick={() => setView("3d")}>3D Terrain</button>
        </>
      }
      mode={mode}
      onModeChange={setMode}
      qualityItems={workspaceQualityItems(context("elevation"), "dsm", calibratedJob)}
      layers={<p>Layer controls</p>}
      viewport={<PersistentViewport view={view} onMount={onViewportMount} />}
      inspector={<p>Inspector controls</p>}
      drawer={<p>Measurement results</p>}
    />
  );
}

describe("TerrainWorkspaceShell", () => {
  it("renders the workstation landmarks, header, and quality bar", () => {
    render(<ShellHarness />);
    expect(screen.getByRole("region", { name: "Terrain workspace" })).toBeVisible();
    expect(screen.getByRole("main", { name: "Terrain viewport" })).toBeVisible();
    expect(screen.getByRole("complementary", { name: "Layers" })).toBeVisible();
    expect(screen.getByRole("complementary", { name: "Inspector and tools" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "North Ridge" })).toBeVisible();
    expect(screen.getByText("Passed")).toBeVisible();
  });

  it("opens and closes both rails with expanded state and inert content", async () => {
    const user = userEvent.setup();
    render(<ShellHarness />);

    const layersButton = screen.getByRole("button", { name: "Hide layers" });
    await user.click(layersButton);
    expect(layersButton).toHaveAttribute("aria-expanded", "false");
    expect(document.getElementById(layersButton.getAttribute("aria-controls")!)).toHaveAttribute("inert");

    const toolsButton = screen.getByRole("button", { name: "Hide tools" });
    await user.click(toolsButton);
    expect(toolsButton).toHaveAttribute("aria-expanded", "false");
    expect(document.getElementById(toolsButton.getAttribute("aria-controls")!)).toHaveAttribute("inert");
  });

  it("collapses and restores the results drawer", async () => {
    const user = userEvent.setup();
    render(<ShellHarness />);
    const button = screen.getByRole("button", { name: "Hide results" });
    await user.click(button);
    expect(button).toHaveAttribute("aria-expanded", "false");
    await user.click(screen.getByRole("button", { name: "Show results" }));
    expect(screen.getByRole("button", { name: "Hide results" })).toHaveAttribute("aria-expanded", "true");
  });

  it("switches contextual modes with explicit pressed state", async () => {
    const user = userEvent.setup();
    render(<ShellHarness />);
    await user.click(screen.getByRole("button", { name: "Measure" }));
    expect(screen.getByRole("button", { name: "Measure" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("heading", { name: "Measure tools" })).toBeVisible();
  });

  it("keeps the viewport mounted while panels, drawer, mode, and view state change", async () => {
    const user = userEvent.setup();
    const onMount = vi.fn();
    render(<ShellHarness onViewportMount={onMount} />);
    const viewport = screen.getByTestId("persistent-viewport");

    await user.click(screen.getByRole("button", { name: "Hide layers" }));
    await user.click(screen.getByRole("button", { name: "Hide tools" }));
    await user.click(screen.getByRole("button", { name: "Hide results" }));
    await user.click(screen.getByRole("button", { name: "Flythrough" }));
    await user.click(screen.getByRole("button", { name: "3D Terrain" }));

    expect(screen.getByTestId("persistent-viewport")).toBe(viewport);
    expect(onMount).toHaveBeenCalledOnce();
    expect(viewport).toHaveTextContent("3d");
  });

  it("supports keyboard operation for mode and panel controls", async () => {
    const user = userEvent.setup();
    render(<ShellHarness />);
    screen.getByRole("button", { name: "Screen" }).focus();
    await user.keyboard(" ");
    expect(screen.getByRole("button", { name: "Screen" })).toHaveAttribute("aria-pressed", "true");
    const layers = screen.getByRole("button", { name: "Hide layers" });
    layers.focus();
    await user.keyboard("{Enter}");
    expect(screen.getByRole("button", { name: "Show layers" })).toHaveFocus();
  });
});

describe("workspace quality and layers", () => {
  it("labels backend elevation as metric and uses its declared vertical unit", () => {
    const items = workspaceQualityItems(context("elevation"), "dsm", calibratedJob);
    expect(items).toContainEqual(expect.objectContaining({ label: "Mode", value: "Metric" }));
    expect(items).toContainEqual({ label: "Vertical", value: "metre", tone: "neutral" });
    expect(items).toContainEqual(expect.objectContaining({ label: "CRS", value: "EPSG:32613" }));
  });

  it("keeps relative terrain explicitly relative and unitless", () => {
    const items = workspaceQualityItems(context("relative_depth"), "dsm");
    expect(items).toContainEqual(expect.objectContaining({ label: "Mode", value: "Relative", tone: "warning" }));
    expect(items).toContainEqual(expect.objectContaining({ label: "Vertical", value: "Unitless", tone: "warning" }));
    expect(items).toContainEqual(expect.objectContaining({ label: "Calibration", value: "Not available" }));
  });

  it("supports active-layer selection and visibility independently", async () => {
    const user = userEvent.setup();
    function Harness() {
      const [active, setActive] = useState<"rgb" | "dsm">("rgb");
      const [visible, setVisible] = useState({
        rgb: true,
        dsm: false,
        relative_depth: false,
        metric_elevation: false,
        dtm: false,
        ndsm: false,
        semantic_segmentation: false,
        slope: false,
        aspect: false,
        hillshade: false,
        flood_screening: false,
        landslide_screening: false,
      });
      const layers = [
        { ...metricLayer, layer_type: "rgb" as const, display_name: "RGB" },
        metricLayer,
      ];
      return (
        <LayerPanel
          layers={layers}
          activeLayer={active}
          onActiveLayerChange={(layer) => setActive(layer as "rgb" | "dsm")}
          visibility={visible}
          onToggleVisibility={(layer) => setVisible((state) => ({ ...state, [layer]: !state[layer] }))}
          opacity={{
            rgb: 1,
            dsm: 0.85,
            relative_depth: 0.85,
            metric_elevation: 0.85,
            dtm: 0.85,
            ndsm: 0.85,
            semantic_segmentation: 0.75,
            slope: 0.8,
            aspect: 0.8,
            hillshade: 0.8,
            flood_screening: 0.75,
            landslide_screening: 0.75,
          }}
          onOpacityChange={() => undefined}
          job={calibratedJob}
          quality={workspaceQualityPresentation(context("elevation"), active, calibratedJob)}
        />
      );
    }

    render(<Harness />);
    await user.click(screen.getByRole("button", { name: "DSM" }));
    expect(screen.getByRole("button", { name: "DSM" })).toHaveAttribute("aria-pressed", "true");
    const row = screen.getByRole("button", { name: "DSM" }).parentElement;
    const visible = within(row!).getByRole("checkbox", { name: "Visible" });
    expect(row).toContainElement(visible);
    await user.click(visible);
    expect(visible).toBeChecked();
  });
});
