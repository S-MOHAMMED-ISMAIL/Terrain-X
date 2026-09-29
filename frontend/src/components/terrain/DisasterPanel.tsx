import { useEffect, useRef, useState } from "react";
import { ApiError, api } from "@/api/client";
import type { AnalysisJob, AnalysisJobStatus, VisualizationContext } from "@/api/types";
import { ACTIVE_JOB_STATUSES } from "@/api/types";
import { useBackgroundActivity } from "@/components/PageHeaderContext";
import { Badge, type BadgeTone, Button, Card, LiveStatus, Notice, ProgressBar } from "@/components/ui";
import { WorkspaceToolSection } from "./WorkspaceToolSection";

const POLL_INTERVAL_MS = Number(import.meta.env.VITE_ANALYSIS_POLL_INTERVAL_MS) || 3000;

const STATUS_TONE: Record<AnalysisJobStatus, BadgeTone> = {
  queued: "neutral",
  running: "warning",
  completed: "success",
  failed: "danger",
  cancelled: "neutral",
};

function formatArea(value: number, unit: string): string {
  return `${value.toLocaleString(undefined, { maximumFractionDigits: 2 })} ${unit}`;
}

interface Props {
  projectId: string;
  datasetId: string;
  context: VisualizationContext;
  // Called once a disaster-screening job reaches a terminal status — the
  // caller reloads the real VisualizationContext so any newly-completed
  // slope/aspect/flood_screening/landslide_screening layers appear.
  onJobSettled: () => void;
}

/**
 * Phase 8: real terrain-derived hazard screening controls. Backend remains
 * fully authoritative — this only lets the user pick an existing elevation
 * artifact and submit a real AnalysisJob (disaster_source_artifact_id set);
 * every number shown afterward comes from that job's own persisted
 * disaster_metadata (see app/services/disaster_pipeline.py), never computed
 * or estimated client-side.
 */
export function DisasterPanel({ projectId, datasetId, context, onJobSettled }: Props) {
  // A disaster job cites an already-produced metric_elevation/dsm artifact
  // directly (see AnalysisParametersV1.disaster_source_artifact_id) — so the
  // only real choices here are the elevation-family layers this dataset's
  // visualization context already reports as available.
  const elevationOptions = context.layers.filter(
    (l) => (l.layer_type === "metric_elevation" || l.layer_type === "dsm") && l.available,
  );

  const [elevationArtifactKey, setElevationArtifactKey] = useState("");
  const [waterLevel, setWaterLevel] = useState("");
  const [runFlood, setRunFlood] = useState(true);
  const [runLandslide, setRunLandslide] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [job, setJob] = useState<AnalysisJob | null>(null);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    if (!elevationArtifactKey && elevationOptions.length > 0) {
      setElevationArtifactKey(elevationOptions[0].layer_type);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [datasetId]);

  useEffect(() => {
    const stopPolling = () => {
      if (intervalRef.current) {
        clearInterval(intervalRef.current);
        intervalRef.current = null;
      }
    };
    if (!job || !ACTIVE_JOB_STATUSES.includes(job.status)) {
      stopPolling();
      return stopPolling;
    }
    if (!intervalRef.current) {
      intervalRef.current = setInterval(async () => {
        try {
          const updated = await api.getAnalysisJob(projectId, job.id);
          setJob(updated);
          if (!ACTIVE_JOB_STATUSES.includes(updated.status)) {
            stopPolling();
            onJobSettled();
          }
        } catch {
          // A transient poll failure is not a real job failure — leave the
          // last-known job state displayed and simply try again next tick.
        }
      }, POLL_INTERVAL_MS);
    }
    return stopPolling;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [job, projectId]);

  const selectedElevationLayer = elevationOptions.find((l) => l.layer_type === elevationArtifactKey);

  async function handleRun() {
    if (!selectedElevationLayer?.artifact_id) return;
    if (!runFlood && !runLandslide) {
      setError("Select at least one of flood screening or landslide screening to run.");
      return;
    }
    if (runFlood && waterLevel.trim() === "") {
      setError("A water level is required to run flood screening.");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const created = await api.createAnalysisJob(projectId, datasetId, {
        disasterSourceArtifactId: selectedElevationLayer.artifact_id,
        runFloodScreening: runFlood,
        waterLevel: runFlood ? Number(waterLevel) : undefined,
        runLandslideScreening: runLandslide,
      });
      setJob(created);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to start disaster screening");
    } finally {
      setSubmitting(false);
    }
  }

  const isActive = !!job && ACTIVE_JOB_STATUSES.includes(job.status);
  // Real, already-known state — a genuine disaster-screening job currently
  // running — lets the ambient background respond honestly; never fabricated.
  useBackgroundActivity(isActive ? "active" : "idle");
  const meta = job?.disaster_metadata;
  const terrainStats = meta?.terrain_statistics;
  const flood = meta?.flood;
  const landslide = meta?.landslide;

  const operationStatus = elevationOptions.length === 0
    ? "Unavailable"
    : job
      ? job.status === "running" || job.status === "queued"
        ? "Processing"
        : job.status === "completed"
          ? "Completed"
          : job.status === "failed"
            ? "Failed"
            : "Idle"
      : "Idle";

  return (
    <Card padding="sm" className="text-xs text-slate-600">
      <WorkspaceToolSection
        title="Terrain screening"
        status={operationStatus}
        tone={operationStatus === "Completed" ? "success" : operationStatus === "Failed" ? "danger" : operationStatus === "Processing" ? "warning" : "neutral"}
        description="Configure terrain-derived flood and landslide screening."
        testId="disaster-screening-tools"
      >
        <Notice tone="warning" title="Screening, not prediction">
          This is terrain-derived screening only: never rainfall/hydraulic simulation, weather data,
          or a machine-learned flood/landslide prediction. Use it for situational awareness, not as
          a substitute for a validated hydrological, hydraulic, or geotechnical study.
        </Notice>
      </WorkspaceToolSection>

      <WorkspaceToolSection
        title="Inputs"
        status={selectedElevationLayer ? "Ready" : "Required"}
        tone={selectedElevationLayer ? "info" : "warning"}
        description="Select the existing calibrated terrain product and screening operations."
      >
      {elevationOptions.length === 0 ? (
        <Notice tone="warning" title="Screening unavailable">
          No calibrated Metric Elevation or DSM artifact is available. Run analysis with a DEM or GCP reference first.
        </Notice>
      ) : (
        <div className="flex flex-col gap-2">
          <label className="flex flex-col gap-1">
            Elevation source
            <select
              value={elevationArtifactKey}
              onChange={(e) => setElevationArtifactKey(e.target.value)}
              disabled={isActive}
              className="rounded-md border border-slate-300 px-2 py-1"
            >
              {elevationOptions.map((l) => (
                <option key={l.layer_type} value={l.layer_type}>
                  {l.display_name}
                </option>
              ))}
            </select>
          </label>

          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={runFlood}
              disabled={isActive}
              onChange={(e) => setRunFlood(e.target.checked)}
            />
            Run flood screening
          </label>
          {runFlood && (
            <label className="flex flex-col gap-1 pl-5">
              Water level (same units as the elevation source)
              <input
                type="number"
                value={waterLevel}
                disabled={isActive}
                onChange={(e) => setWaterLevel(e.target.value)}
                className="rounded-md border border-slate-300 px-2 py-1"
                placeholder="e.g. 121.0"
              />
            </label>
          )}

          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={runLandslide}
              disabled={isActive}
              onChange={(e) => setRunLandslide(e.target.checked)}
            />
            Run landslide susceptibility screening (default slope thresholds)
          </label>

          <Button onClick={handleRun} loading={submitting} loadingLabel="Starting screening..." disabled={isActive || !selectedElevationLayer}>
            {isActive ? "Screening in progress" : job ? "Run screening again" : "Run screening"}
          </Button>
        </div>
      )}
      </WorkspaceToolSection>

      {error && (
        <LiveStatus priority="assertive" className="mt-3 rounded-md border border-red-200 bg-red-50 px-2 py-1 text-metadata text-red-700 animate-fade-in">
          {error}
        </LiveStatus>
      )}

      {job && (
        <div className="mt-3 animate-fade-in">
          <WorkspaceToolSection
            title="Screening result"
            status={job.status}
            tone={STATUS_TONE[job.status]}
            description={`Updated ${new Date(job.updated_at).toLocaleString()}`}
          >
          <div className="mb-2 flex items-center justify-between gap-2 text-metadata text-slate-500">
            <span>{job.current_stage.replaceAll("_", " ")}</span>
            <span>{job.progress}%</span>
          </div>
          <ProgressBar value={job.progress} tone={job.status === "failed" ? "danger" : job.status === "completed" ? "success" : "brand"} />

          {job.status === "failed" && job.error_message && (
            <Notice tone="error" title="Screening failed" announce className="mt-2">
              {job.error_message} Review the inputs and run the screening again.
            </Notice>
          )}

          {job.disaster_status === "completed" && meta && (
            <div className="mt-3 flex flex-col gap-3 text-slate-600 animate-fade-in-up">
              <div className="flex items-center justify-between border-b border-slate-200 pb-1">
                <p className="font-semibold text-slate-900">Summary</p>
                <Badge tone="success">Completed</Badge>
              </div>
              {terrainStats && (
                <div>
                  <p className="font-medium text-slate-800">Terrain statistics</p>
                  <p>
                    Elevation: {terrainStats.min_elevation.toFixed(2)}–
                    {terrainStats.max_elevation.toFixed(2)} (mean{" "}
                    {terrainStats.mean_elevation.toFixed(2)})
                  </p>
                  {terrainStats.mean_slope_deg !== null && (
                    <p>
                      Slope: {terrainStats.min_slope_deg?.toFixed(1)}°–
                      {terrainStats.max_slope_deg?.toFixed(1)}° (mean{" "}
                      {terrainStats.mean_slope_deg.toFixed(1)}°)
                    </p>
                  )}
                  <p>Valid pixels: {terrainStats.valid_pixel_count.toLocaleString()}</p>
                </div>
              )}

              {flood && (
                <div>
                  <p className="font-medium text-slate-800">Flood screening</p>
                  <p>
                    Water level {flood.water_level} · potentially inundated:{" "}
                    {flood.potentially_inundated_pixel_count.toLocaleString()} px (
                    {flood.potentially_inundated_percentage.toFixed(1)}%) ·{" "}
                    {formatArea(flood.potentially_inundated_area, flood.area_unit)}
                  </p>
                </div>
              )}

              {landslide && (
                <div>
                  <p className="font-medium text-slate-800">Landslide susceptibility screening</p>
                  <p>
                    Max slope {landslide.max_slope_deg.toFixed(1)}° · mean{" "}
                    {landslide.mean_slope_deg.toFixed(1)}°
                  </p>
                  <p>
                    {Object.entries(landslide.class_labels)
                      .map(
                        ([value, label]) =>
                          `${label}: ${(landslide.class_percentages[value] ?? 0).toFixed(1)}%`,
                      )
                      .join(" · ")}
                  </p>
                </div>
              )}

              {meta.disclaimer && (
                <p className="text-metadata leading-snug text-amber-700">{meta.disclaimer}</p>
              )}
              <div className="border-t border-slate-200 pt-2">
                <p className="font-semibold text-slate-900">Details</p>
                <p>Terrain product: {selectedElevationLayer?.display_name ?? "Source no longer available"}</p>
                <p>Flood screening: {runFlood ? `water level ${waterLevel}` : "not selected"}</p>
                <p>Landslide screening: {runLandslide ? "selected" : "not selected"}</p>
              </div>
              <div className="border-t border-slate-200 pt-2">
                <p className="font-semibold text-slate-900">Actions</p>
                <p className="text-slate-500">Inspect generated screening layers in the Layers rail, or adjust inputs and run again.</p>
              </div>
            </div>
          )}

          {job.disaster_status === "failed" && meta?.error && (
            <Notice tone="error" title="Screening output failed" className="mt-2">{meta.error}</Notice>
          )}
          </WorkspaceToolSection>
        </div>
      )}
    </Card>
  );
}
