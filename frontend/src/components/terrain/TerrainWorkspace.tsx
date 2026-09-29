import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiError, api } from "@/api/client";
import type {
  AnalysisJob,
  CalibrationResidualsResult,
  Dataset,
  LayerType,
  MeasurementCreatePayload,
  PixelValue,
  RasterMetadata,
  ResidualKind,
  SemanticRegion,
  TerrainMetadata,
  VisualizationContext,
} from "@/api/types";
import { Button, Card, EmptyState, ErrorState, LoadingState, Select } from "@/components/ui";
import { useWebGLSupport } from "@/hooks/useWebGLSupport";
import { CalibrationResidualsCard } from "./CalibrationResidualsCard";
import { CalibrationDiagnosticsPanel } from "./CalibrationDiagnosticsPanel";
import { residualMarkersInteractive } from "./calibrationResiduals";
import { DisasterPanel } from "./DisasterPanel";
import { DataQualityDetails } from "./DataQualityDetails";
import { type FlightTerrain, flightTerrainFromMesh } from "./flythrough";
import {
  checkWaypoint,
  IDLE_PLAYBACK,
  type PathWaypoint,
  type PlaybackCommand,
  type PlaybackStatus,
} from "./flythroughPath";
import { FlythroughPathCard } from "./FlythroughPathCard";
import { MeshExportCard } from "./MeshExportCard";
import { browserRecordingEnvironment, downloadBlob, recordingSupport } from "./flythroughRecorder";
import { findSemanticRegion, formatSample, pickInspectableLayer } from "./inspection";
import { LayerPanel } from "./LayerPanel";
import { LayerDetailsPanel } from "./LayerDetailsPanel";
import { type MapClick, type PixelResolver, resolveToSourcePixel, type SourcePixel } from "./mapClick";
import { MapView2D } from "./MapView2D";
import {
  addMeasurementPoint,
  isMeasurementReady,
  type MeasurementMode,
  type MeasurementPoint,
} from "./measurementMode";
import { MeasurementHistory } from "./MeasurementHistory";
import type { MeasurementResult } from "./MeasurementResultPanel";
import { MeasurementResultPanel } from "./MeasurementResultPanel";
import { MeasurementTools } from "./MeasurementTools";
import { buildTerrainGeometry } from "./terrainMesh";
import { sourcePixelCentreToLocal } from "./terrainCoords";
import {
  isRelativeTerrain,
  RELATIVE_TERRAIN_NOTICE,
  textureAvailable,
  textureLabel,
} from "./terrainAvailability";
import { relativeExaggerationSliderRange } from "./terrainExaggeration";
import { TerrainWorkspaceShell, type WorkspaceMode } from "./TerrainWorkspaceShell";
import { jobForLayer, workspaceQualityPresentation } from "./workspacePresentation";

const TerrainView3D = lazy(() =>
  import("./TerrainView3D").then((module) => ({ default: module.TerrainView3D })),
);

// Calibrated (DSM/metric elevation) terrain's existing, unchanged default
// and slider range — both axes are real meters there, so a small fixed
// multiplier is already meaningful. Never used for relative-depth terrain.
const DEFAULT_CALIBRATED_EXAGGERATION = 1.5;
// Shown only until the real auto-computed relative-depth baseline arrives
// (a brief loading window) — never actually used to build geometry, since
// TerrainView3D computes and reports its own real baseline before the
// first relative-depth mesh is ever built.
const FALLBACK_RELATIVE_EXAGGERATION = 5;

interface Props {
  projectId: string;
  projectName: string;
  datasets: Dataset[];
  requestedMode: WorkspaceMode;
  requestedDatasetId?: string;
}

// P1-6: a backend resolver for one layer's own raster — the single way a
// georeferenced 2D click becomes a source pixel.
function pixelResolverFor(projectId: string, jobId: string, artifactId: string): PixelResolver {
  return (lng, lat) =>
    api.getPixelForCoordinate(projectId, jobId, artifactId, lng, lat, "EPSG:4326");
}

type ViewMode = "2d" | "3d";

const DEFAULT_VISIBILITY: Record<LayerType, boolean> = {
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
const DEFAULT_OPACITY: Record<LayerType, number> = {
  rgb: 1,
  relative_depth: 0.85,
  metric_elevation: 0.85,
  dsm: 0.85,
  dtm: 0.85,
  ndsm: 0.85,
  semantic_segmentation: 0.75,
  slope: 0.8,
  aspect: 0.8,
  hillshade: 0.8,
  flood_screening: 0.75,
  landslide_screening: 0.75,
};

/** Phase 5 terrain/GIS workspace: real 2D (Leaflet) and 3D (Three.js) views
 * of actual Phase 3/4 artifacts for one dataset — see docs/ARCHITECTURE.md
 * §3.5. Renders nothing until a real VisualizationContext has loaded, and
 * every control here only affects presentation, never the stored data. */
export function TerrainWorkspace({ projectId, projectName, datasets, requestedMode, requestedDatasetId }: Props) {
  const webglSupported = useWebGLSupport();
  const validDatasets = datasets.filter((d) => d.status === "valid" && d.role === "source_image");

  const [selectedDatasetId, setSelectedDatasetId] = useState("");
  const [context, setContext] = useState<VisualizationContext | null>(null);
  const [analysisJob, setAnalysisJob] = useState<AnalysisJob | null>(null);
  const [analysisJobs, setAnalysisJobs] = useState<AnalysisJob[]>([]);
  const [contextError, setContextError] = useState<string | null>(null);
  const [loadingContext, setLoadingContext] = useState(false);

  useEffect(() => {
    if (requestedDatasetId && requestedDatasetId !== selectedDatasetId && datasets.some((dataset) => dataset.id === requestedDatasetId && dataset.status === "valid" && dataset.role === "source_image")) {
      setSelectedDatasetId(requestedDatasetId);
    }
  }, [datasets, requestedDatasetId, selectedDatasetId]);
  const [layerMetadata, setLayerMetadata] = useState<Record<string, RasterMetadata>>({});
  const [layerMetadataErrors, setLayerMetadataErrors] = useState<Record<string, string>>({});
  const metadataRequestedRef = useRef(new Set<string>());

  const [mode, setMode] = useState<ViewMode>("2d");
  const [workspaceMode, setWorkspaceMode] = useState<WorkspaceMode>(requestedMode);
  const [activeLayer, setActiveLayer] = useState<LayerType>("rgb");
  const [visibility, setVisibility] = useState(DEFAULT_VISIBILITY);
  const [opacity, setOpacity] = useState(DEFAULT_OPACITY);

  // Kept as two independent values — never shared — so adjusting relative-
  // depth's (much wider) exaggeration range can never leak into calibrated
  // DSM/metric-elevation terrain's existing scaling, and vice versa.
  const [calibratedExaggeration, setCalibratedExaggeration] = useState(
    DEFAULT_CALIBRATED_EXAGGERATION,
  );
  const [relativeExaggeration, setRelativeExaggeration] = useState<number | null>(null);
  // Which real artifact_id the auto-computed relative-depth baseline above
  // was last calculated for — lets a same-artifact remount (e.g. toggling
  // 2D <-> 3D, which fully unmounts/remounts TerrainView3D) skip
  // recalculating and clobbering the user's own slider adjustment, while a
  // genuinely different relative-depth artifact still gets its own fresh
  // baseline (see TerrainView3D.tsx's onAutoRelativeExaggeration).
  const autoExaggerationArtifactRef = useRef<string | null>(null);
  const [showGrid, setShowGrid] = useState(false);
  const [showTexture, setShowTexture] = useState(true);
  const [firstPerson, setFirstPerson] = useState(false);
  // Camera-only — same mesh/geometry/texture/gamma/sky mask either way (see
  // TerrainView3D.tsx's viewMode prop). Entering flythrough always forces
  // this back to "perspective" (see the Flythrough button below), since
  // first-person's own PointerLockControls camera is unrelated to the
  // top-down orthographic camera.
  const [viewMode, setViewMode] = useState<"perspective" | "top">("perspective");

  // P1-8: waypoint flythrough. Waypoints are validated against the flight
  // surface of the same terrain grid the 3D view renders (validity does not
  // depend on exaggeration); the flown path itself is built by TerrainView3D
  // from its own rendered surface.
  const [waypointMode, setWaypointMode] = useState(false);
  const [waypoints, setWaypoints] = useState<PathWaypoint[]>([]);
  const waypointsRef = useRef(waypoints);
  waypointsRef.current = waypoints;
  const [waypointMessage, setWaypointMessage] = useState<string | null>(null);
  const [pathTerrain, setPathTerrain] = useState<{
    artifactId: string;
    meta: TerrainMetadata;
    flight: FlightTerrain;
  } | null>(null);
  const [playbackCommand, setPlaybackCommand] = useState<PlaybackCommand | null>(null);
  const commandCounter = useRef(0);
  const [playbackSpeed, setPlaybackSpeed] = useState(1);
  const [playbackStatus, setPlaybackStatus] = useState<PlaybackStatus>(IDLE_PLAYBACK);
  const recordingInfo = useMemo(() => recordingSupport(browserRecordingEnvironment()), []);

  // P1-5: calibration residual points (held-out by default).
  const [residualsVisible, setResidualsVisible] = useState(false);
  const [residualKind, setResidualKind] = useState<ResidualKind>("heldout");
  const [residualData, setResidualData] = useState<CalibrationResidualsResult | null>(null);
  const [residualLoading, setResidualLoading] = useState(false);
  const [residualError, setResidualError] = useState<string | null>(null);
  const [resetToken, setResetToken] = useState(0);

  const [sample, setSample] = useState<MapClick | null>(
    null,
  );
  const [sampleValue, setSampleValue] = useState<PixelValue | null>(null);
  // P1-6: a georeferenced click that falls outside the inspected layer's
  // own raster (resolved by the backend).
  const [sampleOutside, setSampleOutside] = useState(false);
  // Real, already-persisted per-region metadata (pixel area, model mask
  // score) for the current semantic_segmentation job — fetched once per job,
  // not per click, and only when that layer is actually the one being
  // inspected. Never derived/estimated; null until the real job is fetched.
  const [semanticRegions, setSemanticRegions] = useState<SemanticRegion[] | null>(null);

  // Phase 7: measurement mode — "off" preserves exactly the Phase 5/6
  // inspection behavior above (unchanged); any other mode routes clicks
  // into the real point/distance/profile/coordinate measurement endpoints
  // instead. Never active by accident — a real mode-selector click is
  // required, and the current mode is always visibly labeled (see render
  // below) so a plain map click is never mistaken for a measurement.
  const [measurementMode, setMeasurementMode] = useState<MeasurementMode>("off");
  const [measurementPoints, setMeasurementPoints] = useState<MeasurementPoint[]>([]);
  // P1-6: the clicked points as source pixels of the measured layer — what
  // the result was computed from and what a save persists.
  const [resolvedPoints, setResolvedPoints] = useState<SourcePixel[]>([]);
  const [measurementResult, setMeasurementResult] = useState<MeasurementResult | null>(null);
  const [measurementLoading, setMeasurementLoading] = useState(false);
  const [measurementError, setMeasurementError] = useState<string | null>(null);
  const [measurementSaving, setMeasurementSaving] = useState(false);
  const [historyRefreshToken, setHistoryRefreshToken] = useState(0);

  useEffect(() => {
    setWorkspaceMode(requestedMode);
  }, [requestedMode]);

  // See autoExaggerationArtifactRef's comment — only actually applies the
  // computed value the first time it's reported for a given real artifact.
  function handleAutoRelativeExaggeration(value: number) {
    const artifactId = context?.terrain.artifact_id ?? null;
    if (!artifactId || autoExaggerationArtifactRef.current === artifactId) return;
    autoExaggerationArtifactRef.current = artifactId;
    setRelativeExaggeration(value);
  }

  function handleModeChange(newMode: MeasurementMode) {
    if (newMode !== "off") setWaypointMode(false);
    setMeasurementMode(newMode);
    setMeasurementPoints([]);
    setMeasurementResult(null);
    setMeasurementError(null);
  }

  // The single real click entry point for both the 2D map and 3D terrain
  // view — routes to plain inspection (mode "off", identical to Phase
  // 5/6's existing behavior) or into the real measurement point buffer.
  function handleMapClick(point: MapClick | null) {
    if (waypointMode) {
      if (point) void addWaypointFromMap(point);
      return;
    }
    if (measurementMode === "off") {
      setSample(point);
      return;
    }
    if (!point) return; // an out-of-bounds click has nothing real to measure
    setMeasurementError(null);
    setMeasurementResult(null);
    setMeasurementPoints((prev) =>
      addMeasurementPoint(measurementMode, prev, {
        row: point.row,
        col: point.col,
        lat: point.lat,
        lng: point.lng,
      }),
    );
  }

  // P1-8: add a waypoint after validating it (never adjusted to fit).
  function addWaypoint(candidate: PathWaypoint) {
    if (!pathTerrain) {
      setWaypointMessage("The terrain is still loading; try again in a moment.");
      return;
    }
    const check = checkWaypoint(pathTerrain.flight, waypointsRef.current, candidate);
    if (!check.ok) {
      setWaypointMessage(check.reason);
      return;
    }
    setWaypointMessage(null);
    setWaypoints([...waypointsRef.current, candidate]);
  }

  // 2D map click -> waypoint. Georeferenced: lng/lat is converted into the
  // terrain's own local frame by the backend (same CRS logic as the terrain
  // grid). Not georeferenced: the source pixel scaled onto the grid.
  async function addWaypointFromMap(point: MapClick) {
    const terrain = context?.terrain;
    if (!pathTerrain || !terrain?.artifact_id || !terrain.analysis_job_id) {
      setWaypointMessage("The terrain is still loading; try again in a moment.");
      return;
    }
    const meta = pathTerrain.meta;
    if (point.lat !== undefined && point.lng !== undefined) {
      try {
        const local = await api.getTerrainLocalCoordinate(
          projectId,
          terrain.analysis_job_id,
          terrain.artifact_id,
          point.lng,
          point.lat,
        );
        if (local.local_crs !== meta.local_crs || meta.origin_x === null || meta.origin_y === null) {
          setWaypointMessage("The terrain's coordinate frame did not match; waypoint not added.");
          return;
        }
        if (!local.in_footprint) {
          setWaypointMessage("Waypoint is outside the terrain footprint.");
          return;
        }
        addWaypoint({
          localX: local.map_x - meta.origin_x,
          localZ: local.map_y - meta.origin_y,
          mapX: local.map_x,
          mapY: local.map_y,
          pixelCol: null,
          pixelRow: null,
        });
      } catch (err) {
        setWaypointMessage(err instanceof ApiError ? err.message : "Waypoint conversion failed.");
      }
    } else if (point.row !== undefined && point.col !== undefined) {
      // The clicked pixel's centre, in continuous source-pixel coordinates.
      addWaypoint({ ...sourcePixelCentreToLocal(meta, point.row, point.col), mapX: null, mapY: null });
    }
  }

  // 3D terrain click -> waypoint at the exact grid-local hit point.
  function addWaypointFrom3D(localX: number, localZ: number) {
    if (!pathTerrain) {
      setWaypointMessage("The terrain is still loading; try again in a moment.");
      return;
    }
    const meta = pathTerrain.meta;
    const georeferenced = meta.is_georeferenced && meta.origin_x !== null && meta.origin_y !== null;
    addWaypoint({
      localX,
      localZ,
      mapX: georeferenced ? meta.origin_x! + localX : null,
      mapY: georeferenced ? meta.origin_y! + localZ : null,
      pixelCol: georeferenced ? null : (localX / (meta.cell_size_x ?? 1)) * (meta.source_width / meta.width),
      pixelRow: georeferenced
        ? null
        : (localZ / (meta.cell_size_y ?? 1)) * (meta.source_height / meta.height),
    });
  }

  function sendPlaybackCommand(type: PlaybackCommand["type"]) {
    if (type === "stop") {
      setPlaybackStatus((previous) => ({
        ...IDLE_PLAYBACK,
        pathOk: previous.pathOk,
        pathReason: previous.pathReason,
        clearance: previous.clearance,
        clearanceUnits: previous.clearanceUnits,
        duration: previous.duration,
      }));
    }
    commandCounter.current += 1;
    setPlaybackCommand({ type, id: commandCounter.current });
  }

  function downloadPath() {
    const meta = pathTerrain?.meta;
    if (!meta || waypoints.length < 2) return;
    const georeferenced = meta.is_georeferenced;
    const body = {
      format: "terrainx-flythrough-path",
      version: 1,
      terrain_artifact_id: meta.artifact_id,
      height_kind: meta.height_kind,
      georeferenced,
      local_crs: meta.local_crs,
      origin_x: meta.origin_x,
      origin_y: meta.origin_y,
      target_clearance: playbackStatus.clearance,
      clearance_units: playbackStatus.clearanceUnits,
      speed_multiplier: playbackSpeed,
      waypoints: waypoints.map((w) =>
        georeferenced ? { map_x: w.mapX, map_y: w.mapY } : { pixel_col: w.pixelCol, pixel_row: w.pixelRow },
      ),
    };
    downloadBlob(
      new Blob([JSON.stringify(body, null, 2)], { type: "application/json" }),
      `terrainx-flythrough-path-${meta.artifact_id}.json`,
    );
  }

  const loadContext = useCallback(async (datasetId: string) => {
    setLoadingContext(true);
    setContextError(null);
    setContext(null);
    setAnalysisJob(null);
    setAnalysisJobs([]);
    setLayerMetadata({});
    setLayerMetadataErrors({});
    metadataRequestedRef.current.clear();
    try {
      const [ctx, jobs] = await Promise.all([
        api.getVisualizationContext(projectId, datasetId),
        api.listAnalysisJobs(projectId),
      ]);
      const sourceJobId = ctx.terrain.analysis_job_id
        ?? ctx.layers.find((layer) => layer.analysis_job_id)?.analysis_job_id
        ?? ctx.calibration_residuals.analysis_job_id;
      const sourceJob = jobs.find((job) => job.id === sourceJobId)
        ?? jobs.find((job) => job.dataset_id === datasetId)
        ?? null;
      const datasetJobs = jobs.filter((job) => job.dataset_id === datasetId);
      setContext(ctx);
      setAnalysisJob(sourceJob);
      setAnalysisJobs(datasetJobs);
      // Reset per-dataset UI state to real defaults for the new dataset.
      setVisibility(DEFAULT_VISIBILITY);
      setActiveLayer("rgb");
      setSample(null);
      setSampleValue(null);
    } catch (err) {
      setContextError(
        err instanceof ApiError ? err.message : "Failed to load visualization context",
      );
    } finally {
      setLoadingContext(false);
    }

  }, [projectId]);

  // Re-fetches the real VisualizationContext for the current dataset without
  // resetting the user's existing visibility/opacity/active-layer choices —
  // used after a Phase 8 disaster-screening job completes so its newly
  // written slope/aspect/flood_screening/landslide_screening layers become
  // selectable, without discarding whatever the user was already looking at.
  const refreshContext = useCallback(async (datasetId: string) => {
    try {
      const [ctx, jobs] = await Promise.all([
        api.getVisualizationContext(projectId, datasetId),
        api.listAnalysisJobs(projectId),
      ]);
      setContext(ctx);
      setAnalysisJobs(jobs.filter((job) => job.dataset_id === datasetId));
    } catch {
      // A refresh failure after a completed job is not worth surfacing as a
      // fresh error — the existing context (and job result panel) stays as-is.
    }
  }, [projectId]);

  useEffect(() => {
    if (selectedDatasetId) loadContext(selectedDatasetId);
  }, [selectedDatasetId, loadContext]);

  // P1-8: a path belongs to one terrain artifact.
  const pathArtifactId = context?.terrain.available ? context.terrain.artifact_id : null;
  const pathJobId = context?.terrain.available ? context.terrain.analysis_job_id : null;
  useEffect(() => {
    setWaypoints([]);
    setWaypointMode(false);
    setWaypointMessage(null);
    setPlaybackStatus(IDLE_PLAYBACK);
  }, [pathArtifactId]);
  // Leaving the 3D view ends any playback there (it unmounts); the path
  // summary is kept.
  useEffect(() => {
    if (mode !== "2d") return;
    setPlaybackStatus((prev) => ({
      ...IDLE_PLAYBACK,
      pathOk: prev.pathOk,
      pathReason: prev.pathReason,
      clearance: prev.clearance,
      clearanceUnits: prev.clearanceUnits,
      duration: prev.duration,
    }));
  }, [mode]);
  useEffect(() => {
    if (!waypointMode || !pathArtifactId || !pathJobId) return;
    if (pathTerrain?.artifactId === pathArtifactId) return;
    let cancelled = false;
    Promise.all([
      api.getTerrainMetadata(projectId, pathJobId, pathArtifactId),
      api.getTerrainGrid(projectId, pathJobId, pathArtifactId),
    ])
      .then(([meta, buffer]) => {
        if (cancelled) return;
        const elevations = new Float32Array(buffer);
        const built = buildTerrainGeometry(meta, elevations, 1);
        const flight = flightTerrainFromMesh(
          built.geometry.getAttribute("position").array,
          elevations,
          {
            width: meta.width,
            height: meta.height,
            cellX: meta.cell_size_x ?? 1,
            cellY: meta.cell_size_y ?? 1,
          },
          built.halfWidth,
          built.halfHeight,
        );
        built.geometry.dispose();
        setPathTerrain({ artifactId: pathArtifactId, meta, flight });
      })
      .catch((err) => {
        if (!cancelled) {
          setWaypointMessage(
            err instanceof ApiError ? err.message : "Failed to load the terrain for waypoints.",
          );
        }
      });
    return () => {
      cancelled = true;
    };
  }, [waypointMode, pathArtifactId, pathJobId, projectId, pathTerrain?.artifactId]);

  // P1-5: fetch the stored residuals whenever the context names a (new)
  // residual artifact; cleared otherwise.
  const residualContext = context?.calibration_residuals ?? null;
  const residualArtifactId = residualContext?.available ? residualContext.artifact_id : null;
  const residualJobId = residualContext?.available ? residualContext.analysis_job_id : null;
  useEffect(() => {
    setResidualData(null);
    setResidualError(null);
    setResidualsVisible(false);
    setResidualKind("heldout");
    if (!residualArtifactId || !residualJobId) return;
    let cancelled = false;
    setResidualLoading(true);
    api
      .getCalibrationResiduals(projectId, residualJobId, residualArtifactId)
      .then((data) => {
        if (!cancelled) setResidualData(data);
      })
      .catch((err) => {
        if (!cancelled) {
          setResidualError(
            err instanceof ApiError ? err.message : "Failed to load calibration residuals",
          );
        }
      })
      .finally(() => {
        if (!cancelled) setResidualLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [projectId, residualArtifactId, residualJobId]);

  // Which layer a click actually samples — see pickInspectableLayer's
  // docstring for why this isn't simply `activeLayer`.
  const inspectableLayer = useMemo(
    () => (context ? pickInspectableLayer(context, activeLayer, visibility) : null),
    [context, activeLayer, visibility],
  );

  const activeContextLayer = context?.layers.find((layer) => layer.layer_type === activeLayer) ?? null;
  useEffect(() => {
    const artifactId = activeContextLayer?.artifact_id;
    const jobId = activeContextLayer?.analysis_job_id;
    if (!artifactId || !jobId || layerMetadata[artifactId] || metadataRequestedRef.current.has(artifactId)) return;
    metadataRequestedRef.current.add(artifactId);
    api
      .getArtifactRasterMetadata(projectId, jobId, artifactId)
      .then((metadata) => setLayerMetadata((current) => ({ ...current, [artifactId]: metadata })))
      .catch((err) => {
        setLayerMetadataErrors((current) => ({
          ...current,
          [artifactId]: err instanceof ApiError ? err.message : "The selected layer metadata could not be loaded.",
        }));
      });
  }, [activeContextLayer?.artifact_id, activeContextLayer?.analysis_job_id, layerMetadata, projectId]);

  // P1-4: slope-at-point always reads the authoritative slope raster
  // itself, never whichever layer happens to be inspectable/visible.
  const slopeLayer = useMemo(
    () => context?.layers.find((l) => l.layer_type === "slope") ?? null,
    [context],
  );
  // The real layer a measurement is computed against: the slope raster for
  // slope-at-point; otherwise the same `inspectableLayer` plain inspection
  // already uses (see pickInspectableLayer) — a real, currently-visible
  // scientific layer, never an arbitrary/hidden one.
  const measurementLayer = measurementMode === "slope" ? slopeLayer : inspectableLayer;

  useEffect(() => {
    if (measurementMode === "off") return;
    if (!isMeasurementReady(measurementMode, measurementPoints)) return;
    if (measurementMode === "slope") {
      if (!slopeLayer?.available) {
        setMeasurementError(
          `Slope at point needs a slope raster — ${
            slopeLayer?.unavailable_reason ?? "no slope layer exists for this dataset."
          }`,
        );
        return;
      }
      const [p] = measurementPoints;
      if (p.lat === undefined || p.lng === undefined) {
        setMeasurementError(
          "Slope at point uses the 2D map — switch to 2D Map and click a location there.",
        );
        return;
      }
    }
    if (!measurementLayer?.analysis_job_id || !measurementLayer.artifact_id) {
      setMeasurementError(
        "Enable a scientific layer's visibility first — a measurement needs a real layer to sample.",
      );
      return;
    }
    const jobId = measurementLayer.analysis_job_id;
    const artifactId = measurementLayer.artifact_id;
    let cancelled = false;
    setMeasurementLoading(true);
    setMeasurementError(null);

    async function run() {
      try {
        let points: SourcePixel[] = [];
        if (measurementMode !== "slope") {
          const resolver = pixelResolverFor(projectId, jobId, artifactId);
          const resolved = await Promise.all(
            measurementPoints.map((point) => resolveToSourcePixel(point, resolver)),
          );
          if (cancelled) return;
          if (resolved.some((pixel) => pixel === null)) {
            setMeasurementError(
              `A clicked point is outside the ${measurementLayer!.display_name} raster.`,
            );
            return;
          }
          points = resolved as SourcePixel[];
          setResolvedPoints(points);
        }
        if (measurementMode === "point") {
          const [p] = points;
          const data = await api.getPointElevation(projectId, jobId, artifactId, p.row, p.col);
          if (!cancelled) setMeasurementResult({ mode: "point", data });
        } else if (measurementMode === "coordinate") {
          const [p] = points;
          const data = await api.getCoordinateForPixel(projectId, jobId, artifactId, p.row, p.col);
          if (!cancelled) setMeasurementResult({ mode: "coordinate", data });
        } else if (measurementMode === "distance") {
          const [a, b] = points;
          const data = await api.getMeasuredDistance(
            projectId,
            jobId,
            artifactId,
            a.row,
            a.col,
            b.row,
            b.col,
          );
          if (!cancelled) setMeasurementResult({ mode: "distance", data });
        } else if (measurementMode === "profile") {
          const [a, b] = points;
          const data = await api.getElevationProfile(
            projectId,
            jobId,
            artifactId,
            a.row,
            a.col,
            b.row,
            b.col,
            64,
          );
          if (!cancelled) setMeasurementResult({ mode: "profile", data });
        } else if (measurementMode === "slope") {
          const [p] = measurementPoints;
          const data = await api.getPointSlope(
            projectId,
            jobId,
            artifactId,
            p.lng!,
            p.lat!,
            "EPSG:4326",
          );
          if (!cancelled) setMeasurementResult({ mode: "slope", data });
        }
      } catch (err) {
        if (!cancelled) {
          setMeasurementError(err instanceof ApiError ? err.message : "Measurement failed");
        }
      } finally {
        if (!cancelled) setMeasurementLoading(false);
      }
    }
    run();
    return () => {
      cancelled = true;
    };
  }, [measurementMode, measurementPoints, measurementLayer, slopeLayer, projectId]);

  async function handleSaveMeasurement() {
    if (!measurementResult || !measurementLayer?.analysis_job_id || !measurementLayer.artifact_id) {
      return;
    }
    const jobId = measurementLayer.analysis_job_id;
    const artifactId = measurementLayer.artifact_id;
    let payload: MeasurementCreatePayload;
    if (measurementResult.mode === "point") {
      const [p] = resolvedPoints;
      payload = {
        measurement_type: "point_elevation",
        analysis_job_id: jobId,
        artifact_id: artifactId,
        row: p.row,
        col: p.col,
      };
    } else if (measurementResult.mode === "coordinate") {
      const [p] = resolvedPoints;
      payload = {
        measurement_type: "coordinate",
        analysis_job_id: jobId,
        artifact_id: artifactId,
        row: p.row,
        col: p.col,
      };
    } else if (measurementResult.mode === "distance") {
      const [a, b] = resolvedPoints;
      payload = {
        measurement_type: "distance",
        analysis_job_id: jobId,
        artifact_id: artifactId,
        row1: a.row,
        col1: a.col,
        row2: b.row,
        col2: b.col,
      };
    } else if (measurementResult.mode === "slope") {
      const [p] = measurementPoints;
      payload = {
        measurement_type: "point_slope",
        analysis_job_id: jobId,
        artifact_id: artifactId,
        x: p.lng!,
        y: p.lat!,
        crs: "EPSG:4326",
      };
    } else {
      const [a, b] = resolvedPoints;
      payload = {
        measurement_type: "profile",
        analysis_job_id: jobId,
        artifact_id: artifactId,
        row1: a.row,
        col1: a.col,
        row2: b.row,
        col2: b.col,
        samples: 64,
      };
    }
    setMeasurementSaving(true);
    setMeasurementError(null);
    try {
      await api.createMeasurement(projectId, payload);
      setHistoryRefreshToken((t) => t + 1);
    } catch (err) {
      setMeasurementError(err instanceof ApiError ? err.message : "Failed to save measurement");
    } finally {
      setMeasurementSaving(false);
    }
  }

  // Real cursor-inspection: sample the actual stored value at the clicked
  // pixel through the real /value endpoint — never a fabricated number, and
  // never estimated from the preview PNG's colors.
  useEffect(() => {
    // Reset to "no answer yet" immediately (not just on early-return below)
    // so a stale value from a previous click never lingers while the new
    // request is in flight — the UI shows "Sampling…" in that gap instead.
    setSampleValue(null);
    setSampleOutside(false);

    if (!sample || !inspectableLayer || !inspectableLayer.analysis_job_id || !inspectableLayer.artifact_id) {
      return;
    }
    const jobId = inspectableLayer.analysis_job_id;
    const artifactId = inspectableLayer.artifact_id;
    let cancelled = false;
    resolveToSourcePixel(sample, pixelResolverFor(projectId, jobId, artifactId))
      .then((pixel) => {
        if (cancelled) return null;
        if (!pixel) {
          setSampleOutside(true);
          return null;
        }
        return api.getArtifactPixelValue(projectId, jobId, artifactId, pixel.row, pixel.col);
      })
      .then((value) => {
        if (!cancelled && value) setSampleValue(value);
      })
      .catch(() => {
        if (!cancelled) setSampleValue(null);
      });
    return () => {
      cancelled = true;
    };
  }, [sample, inspectableLayer, projectId]);

  // Fetch the real region list for the semantic layer's job exactly once per
  // job (keyed by analysis_job_id), not on every click — kept separate from
  // the per-pixel /value fetch above since it's job-level metadata, not a
  // per-sample query.
  useEffect(() => {
    if (inspectableLayer?.layer_type !== "semantic_segmentation" || !inspectableLayer.analysis_job_id) {
      setSemanticRegions(null);
      return;
    }
    let cancelled = false;
    api
      .getAnalysisJob(projectId, inspectableLayer.analysis_job_id)
      .then((job) => {
        if (!cancelled) setSemanticRegions(job.semantic_metadata?.regions ?? null);
      })
      .catch(() => {
        if (!cancelled) setSemanticRegions(null);
      });
    return () => {
      cancelled = true;
    };
  }, [inspectableLayer?.layer_type, inspectableLayer?.analysis_job_id, projectId]);

  // P1-5: memoized so the map only rebuilds markers when something real changes.
  const residualMarkersClickable = residualMarkersInteractive(measurementMode);
  const residualOverlay = useMemo(
    () =>
      residualsVisible && residualData
        ? {
            features: residualData.feature_collection.features,
            kind: residualKind,
            referenceType: residualData.summary.reference_type,
            interactive: residualMarkersClickable,
          }
        : null,
    [residualsVisible, residualData, residualKind, residualMarkersClickable],
  );

  if (validDatasets.length === 0) {
    return (
      <EmptyState
        title="No source dataset"
        description="Upload and validate a source image in the Datasets tab before opening the terrain workspace."
      />
    );
  }

  // D2: the backend decides from raster provenance (never from matching
  // dimensions alone, which a reprojected geographic grid still has).
  const textureCompatible = mode === "3d" && !!context && textureAvailable(context.terrain);

  // Whichever of the two independent exaggeration values is actually
  // relevant right now, purely from the real backend-reported height_kind —
  // never a guess. relativeExaggeration is null only until TerrainView3D
  // reports its real auto-computed baseline (see
  // handleAutoRelativeExaggeration); FALLBACK_RELATIVE_EXAGGERATION is shown
  // only for that brief loading window and never used to build geometry.
  const terrainIsRelative = !!context?.terrain.available && isRelativeTerrain(context.terrain);
  const activeExaggeration = terrainIsRelative
    ? (relativeExaggeration ?? FALLBACK_RELATIVE_EXAGGERATION)
    : calibratedExaggeration;
  const relativeSliderRange = relativeExaggerationSliderRange(
    relativeExaggeration ?? FALLBACK_RELATIVE_EXAGGERATION,
  );
  const selectedDatasetName =
    validDatasets.find((dataset) => dataset.id === selectedDatasetId)?.original_filename ?? "";
  const quality = context
    ? workspaceQualityPresentation(context, activeLayer, analysisJob)
    : null;
  const selectedLayerMetadata = quality?.activeLayer?.artifact_id
    ? layerMetadata[quality.activeLayer.artifact_id]
    : null;
  const detailedLayer = quality?.activeLayer && selectedLayerMetadata
    ? {
        ...quality.activeLayer,
        width: selectedLayerMetadata.width,
        height: selectedLayerMetadata.height,
        dtype: selectedLayerMetadata.dtype,
        nodata: selectedLayerMetadata.nodata,
        is_georeferenced: selectedLayerMetadata.is_georeferenced,
        crs: selectedLayerMetadata.crs,
        min_value: selectedLayerMetadata.min_value,
        max_value: selectedLayerMetadata.max_value,
        notes: selectedLayerMetadata.notes ?? quality.activeLayer.notes,
      }
    : quality?.activeLayer ?? null;
  const activeLayerJob = detailedLayer
    ? jobForLayer(detailedLayer, analysisJobs, analysisJob)
    : analysisJob;
  const qualityItems = quality?.items ?? [
    { label: "Surface", value: loadingContext ? "Loading" : contextError ? "Context error" : "No dataset" },
    { label: "Mode", value: "Unavailable" },
    { label: "Vertical", value: "Unknown", tone: "warning" as const },
    { label: "CRS", value: "Unknown", technical: true },
    { label: "Calibration", value: "Not available" },
  ];
  const showMeasurementTools = workspaceMode === "explore" || workspaceMode === "measure";
  const showFlythroughTools = workspaceMode === "explore" || workspaceMode === "flythrough";

  return (
    <TerrainWorkspaceShell
      projectName={projectName}
      datasetName={selectedDatasetName}
      mode={workspaceMode}
      onModeChange={setWorkspaceMode}
      qualityItems={qualityItems}
      qualityDetails={quality ? <DataQualityDetails job={analysisJob} quality={quality} /> : undefined}
      datasetControl={
        <label className="block min-w-0 text-metadata text-content-secondary">
          <span className="sr-only">Dataset</span>
          <Select
            aria-label="Dataset"
            value={selectedDatasetId}
            onChange={(e) => setSelectedDatasetId(e.target.value)}
            className="border-ui-border-strong bg-surface-panel text-content-primary"
          >
            <option value="">Select a dataset...</option>
            {validDatasets.map((dataset) => (
              <option key={dataset.id} value={dataset.id}>
                {dataset.original_filename}
              </option>
            ))}
          </Select>
        </label>
      }
      viewControl={
        <div className="flex rounded-control border border-ui-border-strong bg-surface-panel p-0.5" role="group" aria-label="Terrain view">
          <button
            type="button"
            aria-pressed={mode === "2d"}
            onClick={() => setMode("2d")}
            className={`min-h-control rounded-control px-3 text-control font-medium ${
              mode === "2d" ? "bg-accent text-slate-950" : "text-content-secondary hover:text-content-primary"
            }`}
          >
            2D Map
          </button>
          <button
            type="button"
            aria-pressed={mode === "3d"}
            onClick={() => setMode("3d")}
            disabled={!webglSupported}
            className={`min-h-control rounded-control px-3 text-control font-medium disabled:cursor-not-allowed disabled:opacity-45 ${
              mode === "3d" ? "bg-accent text-slate-950" : "text-content-secondary hover:text-content-primary"
            }`}
          >
            3D Terrain
          </button>
        </div>
      }
      layers={
        loadingContext ? (
          <LoadingState title="Loading layers" description="Reading the reported visualization context." />
        ) : contextError ? (
          <ErrorState title="Layers unavailable" description={contextError} />
        ) : context ? (
          <div className="flex flex-col gap-3">
            <LayerPanel
              layers={context.layers}
              activeLayer={activeLayer}
              onActiveLayerChange={setActiveLayer}
              visibility={visibility}
              onToggleVisibility={(layer) =>
                setVisibility((previous) => ({ ...previous, [layer]: !previous[layer] }))
              }
              opacity={opacity}
              onOpacityChange={(layer, value) =>
                setOpacity((previous) => ({ ...previous, [layer]: value }))
              }
              job={analysisJob}
              jobs={analysisJobs}
              quality={quality!}
            />
            <CalibrationResidualsCard
              context={context.calibration_residuals}
              data={residualData}
              loading={residualLoading}
              error={residualError}
              visible={residualsVisible}
              onToggleVisible={() => setResidualsVisible((visible) => !visible)}
              kind={residualKind}
              onKindChange={setResidualKind}
              onDownload={() => {
                if (residualJobId && residualArtifactId) {
                  api
                    .downloadArtifact(
                      projectId,
                      residualJobId,
                      residualArtifactId,
                      "calibration_residuals.geojson",
                    )
                    .catch((err) =>
                      setResidualError(
                        err instanceof ApiError ? err.message : "Failed to download residuals",
                      ),
                    );
                }
              }}
              is2d={mode === "2d"}
            />
          </div>
        ) : (
          <p className="p-2 text-supporting text-content-muted">Select a dataset to load its layers.</p>
        )
      }
      inspector={
        loadingContext ? (
          <LoadingState title="Loading details" />
        ) : contextError ? (
          <ErrorState title="Details unavailable" description={contextError} />
        ) : context ? (
          <>
            <LayerDetailsPanel
              layer={detailedLayer}
              job={activeLayerJob}
              quality={quality!}
              metadataError={quality!.activeLayer?.artifact_id
                ? layerMetadataErrors[quality!.activeLayer.artifact_id]
                : null}
            />
            {mode === "3d" && (
              <Card padding="sm">
                <h3 className="mb-2 text-panel-title text-slate-950">Terrain controls</h3>
                {!context.terrain.available ? (
                  <p className="text-supporting text-slate-500">
                    3D terrain is unavailable because no relative depth or DSM artifact exists.
                    {context.terrain.unavailable_reason ? ` ${context.terrain.unavailable_reason}` : ""}
                  </p>
                ) : (
                  <div className="flex flex-col gap-3 text-supporting">
                    {isRelativeTerrain(context.terrain) && (
                      <p className="rounded-control border border-amber-200 bg-amber-50 px-2 py-1 text-metadata font-medium text-amber-900">
                        {RELATIVE_TERRAIN_NOTICE}
                      </p>
                    )}
                    {isRelativeTerrain(context.terrain) ? (
                      <label className="flex flex-col gap-1">
                        Relative terrain exaggeration: {activeExaggeration.toFixed(1)}×
                        <input
                          type="range"
                          min={relativeSliderRange.min}
                          max={relativeSliderRange.max}
                          step={relativeSliderRange.step}
                          value={activeExaggeration}
                          onChange={(e) => setRelativeExaggeration(Number(e.target.value))}
                          className="accent-accent-active"
                        />
                        <span className="text-metadata text-slate-500">
                          Visual exaggeration only; relative depth is not metric elevation.
                        </span>
                      </label>
                    ) : (
                      <label className="flex flex-col gap-1">
                        Vertical exaggeration: {activeExaggeration.toFixed(1)}×
                        <input
                          type="range"
                          min={0.5}
                          max={5}
                          step={0.1}
                          value={activeExaggeration}
                          onChange={(e) => setCalibratedExaggeration(Number(e.target.value))}
                          className="accent-accent-active"
                        />
                      </label>
                    )}
                    <label className="flex min-h-control items-center gap-2">
                      <input
                        type="checkbox"
                        checked={showGrid}
                        onChange={(e) => setShowGrid(e.target.checked)}
                        className="accent-accent-active"
                      />
                      Show grid
                    </label>
                    <label className="flex min-h-control items-center gap-2">
                      <input
                        type="checkbox"
                        checked={showTexture}
                        onChange={(e) => setShowTexture(e.target.checked)}
                        disabled={!textureCompatible}
                        className="accent-accent-active"
                        data-testid="texture-toggle"
                        data-texture-compatible={textureCompatible ? "true" : "false"}
                        data-texture-code={context.terrain.texture_unavailable_code ?? ""}
                      />
                      {textureLabel(context.terrain)}
                      {!textureCompatible && <span className="text-slate-500">(not spatially compatible)</span>}
                    </label>
                    <div>
                      <p className="mb-1">View angle</p>
                      <div className="ui-control-row" role="group" aria-label="View angle">
                        <Button
                          size="sm"
                          variant={viewMode === "perspective" ? "primary" : "secondary"}
                          onClick={() => setViewMode("perspective")}
                        >
                          Perspective
                        </Button>
                        <Button
                          size="sm"
                          variant={viewMode === "top" ? "primary" : "secondary"}
                          onClick={() => setViewMode("top")}
                          disabled={firstPerson}
                        >
                          Top-down
                        </Button>
                      </div>
                    </div>
                    <div className="ui-control-row">
                      <Button size="sm" variant="secondary" onClick={() => setResetToken((token) => token + 1)}>
                        Reset / fit camera
                      </Button>
                      <Button
                        size="sm"
                        variant={firstPerson ? "primary" : "secondary"}
                        onClick={() =>
                          setFirstPerson((value) => {
                            const next = !value;
                            if (next) setViewMode("perspective");
                            return next;
                          })
                        }
                      >
                        {firstPerson ? "Exit flythrough" : "Flythrough mode"}
                      </Button>
                    </div>
                  </div>
                )}
              </Card>
            )}

            {showMeasurementTools && (
              <MeasurementTools
                mode={mode}
                measurementMode={measurementMode}
                points={measurementPoints}
                loading={measurementLoading}
                onModeChange={handleModeChange}
                onClear={() => {
                  setMeasurementPoints([]);
                  setMeasurementResult(null);
                  setMeasurementError(null);
                }}
              />
            )}

            {showFlythroughTools && context.terrain.available && (
              <FlythroughPathCard
                mode={mode}
                waypointMode={waypointMode}
                onToggleWaypointMode={() => {
                  if (!waypointMode) {
                    handleModeChange("off");
                    setSample(null);
                  }
                  setWaypointMessage(null);
                  setWaypointMode((value) => !value);
                }}
                waypoints={waypoints}
                message={waypointMessage}
                loadingTerrain={waypointMode && pathTerrain?.artifactId !== pathArtifactId}
                onUndo={() => {
                  setWaypointMessage(null);
                  setWaypoints((previous) => previous.slice(0, -1));
                }}
                onClear={() => {
                  setWaypointMessage(null);
                  setWaypoints([]);
                }}
                playback={playbackStatus}
                speed={playbackSpeed}
                onSpeedChange={setPlaybackSpeed}
                onCommand={sendPlaybackCommand}
                recording={recordingInfo}
                firstPerson={firstPerson}
                onDownloadPath={downloadPath}
              />
            )}

            {workspaceMode === "screen" && (
              <DisasterPanel
                projectId={projectId}
                datasetId={context.dataset.id}
                context={context}
                onJobSettled={() => refreshContext(context.dataset.id)}
              />
            )}

            {context.terrain.available && context.terrain.artifact_id && context.terrain.analysis_job_id && (
              <MeshExportCard
                projectId={projectId}
                jobId={context.terrain.analysis_job_id}
                artifactId={context.terrain.artifact_id}
              />
            )}
          </>
        ) : (
          <p className="text-supporting text-slate-500">Select a dataset to activate workspace tools.</p>
        )
      }
      drawer={
        context ? (
          <div className="grid min-w-0 grid-cols-1 gap-4 xl:grid-cols-2">
            <details className="min-w-0 border-b border-slate-200 pb-3 xl:col-span-2">
              <summary className="min-h-control cursor-pointer content-center text-panel-title font-semibold text-slate-950">
                Show calibration diagnostics
              </summary>
              <div className="mt-3 min-w-0">
                <CalibrationDiagnosticsPanel job={analysisJob} quality={quality!} />
              </div>
            </details>
            <div className="min-w-0">
              {measurementMode === "off" ? (
                <Card padding="sm" className="text-supporting text-slate-700">
                  <h3 className="mb-1 text-panel-title text-slate-950">Inspection</h3>
                  {sample ? (
                    <>
                      <p>
                        {context.dataset.is_georeferenced && sample.lat !== undefined
                          ? `Map: ${sample.lat.toFixed(6)}, ${sample.lng!.toFixed(6)}`
                          : `Pixel: (${sample.col}, ${sample.row})`}
                      </p>
                      {inspectableLayer ? (
                        sampleOutside ? (
                          <p className="text-slate-500">Outside the {inspectableLayer.display_name} raster.</p>
                        ) : sampleValue ? (
                          <>
                            <p>{formatSample(sampleValue, inspectableLayer.layer_type, inspectableLayer.legend)}</p>
                            {inspectableLayer.layer_type === "semantic_segmentation" &&
                              sampleValue.value !== null &&
                              (() => {
                                const region = findSemanticRegion(semanticRegions, sampleValue.value!);
                                return region ? (
                                  <p className="text-slate-500">
                                    Area: {region.pixel_area}px
                                    {region.mask_score !== null && `; mask stability: ${region.mask_score.toFixed(3)}`}
                                  </p>
                                ) : null;
                              })()}
                          </>
                        ) : (
                          <p className="text-slate-500">Sampling...</p>
                        )
                      ) : (
                        <p className="text-slate-500">
                          Enable a scientific result layer to sample a value at this point.
                        </p>
                      )}
                    </>
                  ) : (
                    <p className="text-slate-500">Click the {mode === "2d" ? "map" : "terrain"} to inspect a point.</p>
                  )}
                </Card>
              ) : (
                <MeasurementResultPanel
                  result={measurementResult}
                  loading={measurementLoading}
                  error={measurementError}
                  onSave={handleSaveMeasurement}
                  saving={measurementSaving}
                />
              )}
            </div>
            <div className="min-w-0">
              <MeasurementHistory projectId={projectId} refreshToken={historyRefreshToken} />
            </div>
          </div>
        ) : (
          <p className="text-supporting text-slate-500">Results will appear after a dataset is selected.</p>
        )
      }
      viewport={
        loadingContext ? (
          <div className="flex h-full items-center justify-center text-control text-content-secondary" role="status">
            Loading visualization context...
          </div>
        ) : contextError ? (
          <div className="flex h-full items-center justify-center p-4 text-center text-control text-red-300" role="alert">
            {contextError}
          </div>
        ) : !context ? (
          <div className="flex h-full items-center justify-center p-4 text-center text-control text-content-secondary">
            Select a valid source dataset to open the terrain viewport.
          </div>
        ) : (
          <div
            className="terrain-engine-host h-full w-full"
            data-testid="terrain-engine-host"
            data-active-tool={measurementMode !== "off" ? measurementMode : waypointMode ? "waypoint" : "inspect"}
          >
            {mode === "2d" ? (
              <MapView2D
                projectId={projectId}
                context={context}
                visibility={visibility}
                opacity={opacity}
                onSample={handleMapClick}
                residuals={residualOverlay}
              />
            ) : !webglSupported ? (
              <div className="flex h-full items-center justify-center p-4 text-center text-control text-content-secondary">
                WebGL is not available in this browser or device. Use the 2D Map view instead.
              </div>
            ) : !context.terrain.available ? (
              <div className="flex h-full items-center justify-center p-4 text-center text-control text-content-secondary">
                3D terrain is unavailable because no relative depth or DSM artifact exists.
                {context.terrain.unavailable_reason ? ` ${context.terrain.unavailable_reason}` : ""}
              </div>
            ) : (
              <div className="relative h-full w-full">
                {isRelativeTerrain(context.terrain) && (
                  <div className="absolute left-2 top-2 z-10 max-w-[85%] rounded-control bg-amber-100/95 px-2 py-1 text-supporting font-medium text-amber-900 shadow">
                    {RELATIVE_TERRAIN_NOTICE}
                  </div>
                )}
                <Suspense fallback={<div role="status" className="flex h-full items-center justify-center text-control text-content-secondary">Loading 3D terrain...</div>}>
                  <TerrainView3D
                    projectId={projectId}
                    jobId={context.terrain.analysis_job_id!}
                    artifactId={context.terrain.artifact_id!}
                    compatibleTextureBlob={
                      textureCompatible
                        ? () => api.getDatasetPreviewBlob(projectId, context.dataset.id)
                        : null
                    }
                    exaggeration={activeExaggeration}
                    onAutoRelativeExaggeration={handleAutoRelativeExaggeration}
                    showGrid={showGrid}
                    showTexture={showTexture}
                    firstPerson={firstPerson}
                    onExitFirstPerson={() => setFirstPerson(false)}
                    viewMode={viewMode}
                    resetToken={resetToken}
                    onSample={(row, col) => handleMapClick({ row, col })}
                    waypointMode={waypointMode}
                    onWaypoint={addWaypointFrom3D}
                    waypoints={waypoints}
                    playbackCommand={playbackCommand}
                    playbackSpeed={playbackSpeed}
                    onPlaybackStatus={setPlaybackStatus}
                  />
                </Suspense>
              </div>
            )}
          </div>
        )
      }
    />
  );
}
