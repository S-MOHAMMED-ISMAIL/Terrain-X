/** @vitest-environment jsdom */
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MeasurementTools } from "./MeasurementTools";
import { FlythroughPathCard } from "./FlythroughPathCard";
import { MeshExportCard } from "./MeshExportCard";
import { DisasterPanel } from "./DisasterPanel";
import type { PlaybackStatus } from "./flythroughPath";
import type { AnalysisJob, VisualizationContext } from "@/api/types";
import { PageHeaderProvider } from "@/components/PageHeaderContext";

const apiMocks = vi.hoisted(() => ({ getTerrainMeshGlb: vi.fn(), createAnalysisJob: vi.fn(), getAnalysisJob: vi.fn() }));
vi.mock("@/api/client", () => ({
  ApiError: class ApiError extends Error {},
  api: apiMocks,
}));
vi.mock("./meshExport", async (loadOriginal) => {
  const actual = await loadOriginal<typeof import("./meshExport")>();
  return {
    ...actual,
    readGlbSummary: vi.fn(() => ({
      triangleCount: 24,
      vertexCount: 16,
      heightKind: "elevation",
      verticalUnits: "m",
      texture: "embedded",
      textureOmittedReason: null,
    })),
  };
});
vi.mock("./flythroughRecorder", async (loadOriginal) => {
  const actual = await loadOriginal<typeof import("./flythroughRecorder")>();
  return { ...actual, downloadBlob: vi.fn() };
});

afterEach(cleanup);

const playback = (overrides: Partial<PlaybackStatus> = {}): PlaybackStatus => ({
  state: "idle",
  progress: 0,
  elapsed: 0,
  duration: 12,
  recording: false,
  pathOk: true,
  pathReason: null,
  clearance: 4,
  clearanceUnits: "reference units",
  message: null,
  ...overrides,
});

describe("measurement tool state", () => {
  it("activates inspect and slope with keyboard-operable aria state, feedback, and clear", async () => {
    const user = userEvent.setup();
    const onModeChange = vi.fn();
    const onClear = vi.fn();
    const { rerender } = render(
      <MeasurementTools mode="2d" measurementMode="off" points={[]} loading={false} onModeChange={onModeChange} onClear={onClear} />,
    );
    expect(screen.getByRole("button", { name: "Inspect (off)" })).toHaveAttribute("aria-pressed", "true");
    await user.tab();
    await user.keyboard("{Enter}");
    expect(onModeChange).toHaveBeenCalledWith("off");
    await user.click(screen.getByRole("button", { name: "Slope at point" }));
    expect(onModeChange).toHaveBeenCalledWith("slope");

    rerender(<MeasurementTools mode="2d" measurementMode="slope" points={[{ row: 2, col: 3 }]} loading onModeChange={onModeChange} onClear={onClear} />);
    expect(screen.getByRole("button", { name: "Slope at point" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("status")).toHaveTextContent("Reading terrain value");
    await user.click(screen.getByRole("button", { name: "Clear measurement" }));
    expect(onClear).toHaveBeenCalledOnce();
  });

  it("presents an explicit unavailable state", () => {
    render(<MeasurementTools mode="2d" measurementMode="off" points={[]} loading={false} unavailableReason="No measurable terrain product is available." onModeChange={vi.fn()} onClear={vi.fn()} />);
    expect(screen.getByText("Unavailable")).toBeVisible();
    expect(screen.getByText("No measurable terrain product is available.")).toBeVisible();
  });
});

function renderFlythrough(status: PlaybackStatus, options: { message?: string; supported?: boolean } = {}) {
  const onCommand = vi.fn();
  const onClear = vi.fn();
  const onSpeedChange = vi.fn();
  render(
    <FlythroughPathCard
      mode="3d"
      waypointMode={false}
      onToggleWaypointMode={vi.fn()}
      waypoints={[
        { localX: 1, localZ: 1, mapX: null, mapY: null, pixelCol: 1, pixelRow: 1 },
        { localX: 8, localZ: 8, mapX: null, mapY: null, pixelCol: 8, pixelRow: 8 },
      ]}
      message={options.message ?? null}
      loadingTerrain={false}
      onUndo={vi.fn()}
      onClear={onClear}
      playback={status}
      speed={1}
      onSpeedChange={onSpeedChange}
      onCommand={onCommand}
      recording={options.supported === false ? { supported: false, reason: "Recording is unavailable in this browser." } : { supported: true, mimeType: "video/webm" }}
      firstPerson={false}
      onDownloadPath={vi.fn()}
    />,
  );
  return { onCommand, onClear, onSpeedChange };
}

describe("flythrough, waypoint, playback, and recording state", () => {
  it("shows valid path readiness, clear, play, pause, and speed controls", async () => {
    const user = userEvent.setup();
    const controls = renderFlythrough(playback());
    expect(screen.getByText(/Path ready:/)).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Clear waypoints" }));
    await user.click(screen.getByRole("button", { name: "Play path" }));
    await user.selectOptions(screen.getByRole("combobox", { name: "Playback speed" }), "2");
    expect(controls.onClear).toHaveBeenCalledOnce();
    expect(controls.onCommand).toHaveBeenCalledWith("play");
    expect(controls.onSpeedChange).toHaveBeenCalledWith(2);
  });

  it("keeps the authoritative invalid reason and disables playback", () => {
    renderFlythrough(playback({ pathOk: false, pathReason: "Waypoint 2: Waypoint is outside the terrain footprint." }));
    expect(screen.getByText("Waypoint 2: Waypoint is outside the terrain footprint.")).toBeVisible();
    expect(screen.getByRole("button", { name: "Play path" })).toBeDisabled();
  });

  it("renders paused, recording, complete, failed, and exact terminal progress states", () => {
    const { rerender } = render(
      <FlythroughPathCard mode="3d" waypointMode={false} onToggleWaypointMode={vi.fn()} waypoints={[]} message={null} loadingTerrain={false} onUndo={vi.fn()} onClear={vi.fn()} playback={playback({ state: "paused", progress: 0.5 })} speed={1} onSpeedChange={vi.fn()} onCommand={vi.fn()} recording={{ supported: true, mimeType: "video/webm" }} firstPerson={false} onDownloadPath={vi.fn()} />,
    );
    expect(screen.getByRole("button", { name: "Resume" })).toBeVisible();
    rerender(<FlythroughPathCard mode="3d" waypointMode={false} onToggleWaypointMode={vi.fn()} waypoints={[]} message={null} loadingTerrain={false} onUndo={vi.fn()} onClear={vi.fn()} playback={playback({ state: "playing", recording: true, progress: 0.3 })} speed={1} onSpeedChange={vi.fn()} onCommand={vi.fn()} recording={{ supported: true, mimeType: "video/webm" }} firstPerson={false} onDownloadPath={vi.fn()} />);
    expect(screen.getByRole("button", { name: "Stop recording" })).toBeVisible();
    rerender(<FlythroughPathCard mode="3d" waypointMode={false} onToggleWaypointMode={vi.fn()} waypoints={[]} message={null} loadingTerrain={false} onUndo={vi.fn()} onClear={vi.fn()} playback={playback({ state: "finished", progress: 1, message: "Recording saved." })} speed={1} onSpeedChange={vi.fn()} onCommand={vi.fn()} recording={{ supported: true, mimeType: "video/webm" }} firstPerson={false} onDownloadPath={vi.fn()} />);
    expect(screen.getByText("Complete")).toBeVisible();
    expect(screen.getByTestId("playback-progress-text")).toHaveTextContent("100%");
    rerender(<FlythroughPathCard mode="3d" waypointMode={false} onToggleWaypointMode={vi.fn()} waypoints={[]} message={null} loadingTerrain={false} onUndo={vi.fn()} onClear={vi.fn()} playback={playback({ message: "Recording failed; no file was produced." })} speed={1} onSpeedChange={vi.fn()} onCommand={vi.fn()} recording={{ supported: false, reason: "Recording unavailable" }} firstPerson={false} onDownloadPath={vi.fn()} />);
    expect(screen.getByText("Failed")).toBeVisible();
    expect(screen.getByTestId("recording-unsupported")).toBeVisible();
  });
});

describe("GLB export state", () => {
  it("requests backend-validated texture and shows resolution, generation, completion, and failure states", async () => {
    const user = userEvent.setup();
    let resolveExport!: (value: ArrayBuffer) => void;
    apiMocks.getTerrainMeshGlb.mockReturnValueOnce(new Promise<ArrayBuffer>((resolve) => { resolveExport = resolve; }));
    const { rerender } = render(
      <MeshExportCard projectId="project" jobId="job" artifactId="terrain" />,
    );
    await user.selectOptions(screen.getByRole("combobox", { name: "Mesh resolution" }), "512");
    await user.click(screen.getByRole("button", { name: "Export 3D mesh (GLB)" }));
    expect(screen.getByRole("button", { name: "Generating GLB..." })).toBeDisabled();
    expect(apiMocks.getTerrainMeshGlb).toHaveBeenCalledWith("project", "job", "terrain", 512, true);
    resolveExport(new ArrayBuffer(16));
    expect(await screen.findByTestId("mesh-export-summary")).toHaveTextContent("Exported 24 triangles");

    rerender(<MeshExportCard projectId="project" jobId="job" artifactId="terrain" />);
    expect(screen.getByRole("checkbox", { name: "Embed source texture" })).toBeEnabled();
    expect(screen.getByText(/exporter confirms compatibility/)).toBeVisible();

    apiMocks.getTerrainMeshGlb.mockRejectedValueOnce(new Error("Export service unavailable"));
    await user.click(screen.getByRole("button", { name: "Export 3D mesh (GLB)" }));
    expect(await screen.findByTestId("mesh-export-error")).toHaveTextContent("Export service unavailable");
    expect(screen.getByRole("button", { name: "Retry GLB export" })).toBeVisible();
  });
});

const screeningContext = (available = true) => ({
  dataset: { id: "dataset" },
  layers: available
    ? [{ layer_type: "dsm", available: true, artifact_id: "dsm-artifact", display_name: "Calibrated DSM" }]
    : [],
  terrain: {},
  calibration_residuals: {},
}) as unknown as VisualizationContext;

const screeningJob = (status: AnalysisJob["status"]): AnalysisJob => ({
  id: `job-${status}`,
  project_id: "project",
  dataset_id: "dataset",
  user_id: "user",
  status,
  current_stage: status === "completed" ? "completed" : "executing",
  progress: status === "completed" ? 100 : status === "failed" ? 65 : 25,
  error_message: status === "failed" ? "The source raster could not be screened." : null,
  parameters: {},
  execution_summary: null,
  calibration_status: "uncalibrated",
  calibration_metadata: null,
  semantic_status: "not_requested",
  semantic_metadata: null,
  ground_filter_status: "not_requested",
  ground_filter_metadata: null,
  disaster_status: status === "completed" ? "completed" : status === "failed" ? "failed" : "processing",
  disaster_metadata: status === "completed" ? {
    terrain_statistics: { min_elevation: 100, max_elevation: 130, mean_elevation: 115, median_elevation: 114, elevation_range: 30, min_slope_deg: 0, max_slope_deg: 35, mean_slope_deg: 8, valid_pixel_count: 1200, total_pixel_count: 1250 },
    disclaimer: "Screening only.",
  } : status === "failed" ? { error: "Screening output was not produced." } : null,
  created_at: "2026-09-28T10:00:00Z",
  started_at: null,
  completed_at: status === "completed" ? "2026-09-28T10:01:00Z" : null,
  updated_at: "2026-09-28T10:01:00Z",
});

async function runScreeningReturning(status: AnalysisJob["status"]) {
  const user = userEvent.setup();
  apiMocks.createAnalysisJob.mockResolvedValueOnce(screeningJob(status));
  render(
    <PageHeaderProvider>
      <DisasterPanel projectId="project" datasetId="dataset" context={screeningContext()} onJobSettled={vi.fn()} />
    </PageHeaderProvider>,
  );
  await user.type(screen.getByRole("spinbutton", { name: /Water level/ }), "121");
  await user.click(screen.getByRole("button", { name: "Run screening" }));
}

describe("disaster screening state", () => {
  it("shows idle inputs and a specific unavailable state", () => {
    const { rerender } = render(<PageHeaderProvider><DisasterPanel projectId="project" datasetId="dataset" context={screeningContext()} onJobSettled={vi.fn()} /></PageHeaderProvider>);
    expect(screen.getByText("Idle")).toBeVisible();
    expect(screen.getByText("Calibrated DSM")).toBeVisible();
    rerender(<PageHeaderProvider><DisasterPanel projectId="project" datasetId="dataset" context={screeningContext(false)} onJobSettled={vi.fn()} /></PageHeaderProvider>);
    expect(screen.getByText("Screening unavailable")).toBeVisible();
    expect(screen.getByText(/No calibrated Metric Elevation or DSM/)).toBeVisible();
  });

  it("shows processing from the real created job", async () => {
    await runScreeningReturning("queued");
    expect(await screen.findByText("Processing")).toBeVisible();
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "25");
  });

  it("organizes completed output into summary, details, and actions", async () => {
    await runScreeningReturning("completed");
    expect(await screen.findByText("Screening result")).toBeVisible();
    expect(screen.getByText("Summary")).toBeVisible();
    expect(screen.getByText("Details")).toBeVisible();
    expect(screen.getByText("Actions")).toBeVisible();
  });

  it("announces a failed screening with recovery guidance", async () => {
    await runScreeningReturning("failed");
    expect(await screen.findByText("Screening failed")).toBeVisible();
    expect(screen.getByRole("alert")).toHaveTextContent("Review the inputs and run the screening again");
  });
});
