import type { AnalysisJob, LayerContext, LayerType, VisualizationContext } from "@/api/types";
import type { DataStateValue } from "@/components/ui";
import type { QualityItem } from "./TerrainWorkspaceShell";

export interface CalibrationPresentation {
  state: "passed" | "failed" | "rejected" | "processing" | "unavailable";
  label: string;
  detail: string;
}

export interface VerticalUnitPresentation {
  label: string;
  detail: string | null;
  known: boolean;
}

export interface WorkspaceQualityPresentation {
  items: QualityItem[];
  calibration: CalibrationPresentation;
  verticalUnit: VerticalUnitPresentation;
  activeLayer: LayerContext | null;
  mode: "metric" | "relative" | "unavailable";
}

type MetadataRecord = Record<string, unknown>;

function record(value: unknown): MetadataRecord | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as MetadataRecord)
    : null;
}

export function calibrationPresentation(job: AnalysisJob | null): CalibrationPresentation {
  if (!job) {
    return {
      state: "unavailable",
      label: "Not available",
      detail: "No analysis job is associated with the displayed result.",
    };
  }
  if (job.calibration_status === "calibrating") {
    return {
      state: "processing",
      label: "Processing",
      detail: "Calibration is still processing; metric output is not yet available.",
    };
  }
  if (job.calibration_status === "calibrated") {
    return {
      state: "passed",
      label: "Passed",
      detail: "Calibration passed the configured quality gate. This is not an independent accuracy guarantee.",
    };
  }
  if (job.calibration_status === "failed") {
    const gate = record(job.calibration_metadata?.quality_gate);
    const rejected = gate?.passed === false;
    const error = typeof job.calibration_metadata?.error === "string"
      ? job.calibration_metadata.error
      : null;
    return {
      state: rejected ? "rejected" : "failed",
      label: rejected ? "Rejected" : "Failed",
      detail: error ?? (rejected
        ? "Calibration was rejected by the configured quality gate."
        : "Calibration did not complete successfully."),
    };
  }
  return {
    state: "unavailable",
    label: "Not available",
    detail: "No calibration reference was used; relative output remains available when produced.",
  };
}

export function verticalUnitPresentation(
  context: VisualizationContext,
  job: AnalysisJob | null,
): VerticalUnitPresentation {
  if (context.terrain.height_kind === "relative_depth") {
    return { label: "Unitless", detail: "Relative depth has no physical vertical unit.", known: true };
  }
  if (context.terrain.height_kind !== "elevation") {
    return { label: "Unknown", detail: "No physical terrain surface is available.", known: false };
  }

  const unit = record(job?.calibration_metadata?.vertical_unit);
  if (unit?.status === "known") {
    const name = typeof unit.unit_name === "string" ? unit.unit_name : null;
    const code = typeof unit.unit === "string" ? unit.unit : null;
    if (name || code) {
      return {
        label: name ?? code!,
        detail: typeof unit.source === "string" ? `Declared by ${unit.source}.` : null,
        known: true,
      };
    }
  }
  return {
    label: "Unknown",
    detail: typeof unit?.diagnostic === "string"
      ? unit.diagnostic
      : "The API does not report a known vertical unit for this calibrated surface.",
    known: false,
  };
}

export function workspaceQualityPresentation(
  context: VisualizationContext,
  activeLayer: LayerType,
  job: AnalysisJob | null = null,
): WorkspaceQualityPresentation {
  const selectedLayer = context.layers.find((layer) => layer.layer_type === activeLayer) ?? null;
  const mode = context.terrain.available && context.terrain.height_kind === "elevation"
    ? "metric"
    : context.terrain.available && context.terrain.height_kind === "relative_depth"
      ? "relative"
      : "unavailable";
  const calibration = calibrationPresentation(job);
  const verticalUnit = verticalUnitPresentation(context, job);
  const crs = selectedLayer?.crs ?? context.terrain.crs ?? context.dataset.crs;

  return {
    activeLayer: selectedLayer,
    calibration,
    verticalUnit,
    mode,
    items: [
      {
        label: "Surface",
        value: selectedLayer?.display_name ?? context.terrain.source_artifact_type ?? "Unavailable",
      },
      {
        label: "Mode",
        value: mode === "metric" ? "Metric" : mode === "relative" ? "Relative" : "Unavailable",
        tone: mode === "relative" ? "warning" : mode === "metric" ? "accent" : "neutral",
      },
      {
        label: "Vertical",
        value: verticalUnit.label,
        tone: mode === "relative" || !verticalUnit.known ? "warning" : "neutral",
      },
      {
        label: "CRS",
        value: crs ?? (context.dataset.is_georeferenced ? "Unknown" : "Local pixel coordinates"),
        technical: true,
      },
      {
        label: "Calibration",
        value: calibration.label,
        tone: calibration.state === "passed"
          ? "accent"
          : calibration.state === "failed" || calibration.state === "rejected"
            ? "warning"
            : "neutral",
      },
    ],
  };
}

export function workspaceQualityItems(
  context: VisualizationContext,
  activeLayer: LayerType,
  job: AnalysisJob | null = null,
): QualityItem[] {
  return workspaceQualityPresentation(context, activeLayer, job).items;
}

export function layerDataState(
  layer: LayerContext,
  job: AnalysisJob | null,
): { state: DataStateValue; detail: string | null } {
  if (layer.available) return { state: "available", detail: null };

  let state: DataStateValue = "unavailable";
  if (job) {
    if (["metric_elevation", "dsm"].includes(layer.layer_type)) {
      if (job.calibration_status === "calibrating") state = "processing";
      if (job.calibration_status === "failed") state = "failed";
    } else if (["dtm", "ndsm"].includes(layer.layer_type)) {
      if (job.ground_filter_status === "processing") state = "processing";
      if (job.ground_filter_status === "failed") state = "failed";
    } else if (layer.layer_type === "semantic_segmentation") {
      if (job.semantic_status === "processing") state = "processing";
      if (job.semantic_status === "failed") state = "failed";
    } else if (["slope", "aspect", "hillshade", "flood_screening", "landslide_screening"].includes(layer.layer_type)) {
      if (job.disaster_status === "processing") state = "processing";
      if (job.disaster_status === "failed") state = "failed";
    } else if (job.status === "queued" || job.status === "running") {
      state = "processing";
    } else if (job.status === "failed" || job.status === "cancelled") {
      state = "failed";
    }
  }
  return { state, detail: layer.unavailable_reason };
}

const DISASTER_LAYERS: LayerType[] = [
  "slope",
  "aspect",
  "hillshade",
  "flood_screening",
  "landslide_screening",
];

export function jobForLayer(
  layer: LayerContext,
  jobs: AnalysisJob[],
  sourceJob: AnalysisJob | null,
): AnalysisJob | null {
  if (layer.analysis_job_id) {
    const exact = jobs.find((job) => job.id === layer.analysis_job_id);
    if (exact) return exact;
  }
  if (DISASTER_LAYERS.includes(layer.layer_type)) {
    return jobs.find((job) => job.disaster_status !== "not_requested") ?? sourceJob;
  }
  return sourceJob;
}

export function layerUnitLabel(layer: LayerContext, quality: WorkspaceQualityPresentation): string {
  if (layer.layer_type === "relative_depth") return "Relative / unitless";
  if (layer.layer_type === "slope" || layer.layer_type === "aspect") return "degrees";
  if (["metric_elevation", "dsm", "dtm", "ndsm"].includes(layer.layer_type)) {
    if (quality.mode !== "metric") return "Metric output unavailable";
    return quality.verticalUnit.known ? quality.verticalUnit.label : "Vertical unit unknown";
  }
  return layer.is_categorical ? "Categorical" : "Display values";
}

export function noDataPresentation(layer: LayerContext): string {
  return layer.nodata === null
    ? "NoData value not reported; NoData is not zero"
    : `NoData: ${layer.nodata} (excluded, not zero)`;
}
