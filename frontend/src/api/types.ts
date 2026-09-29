export interface User {
  id: string;
  email: string;
  role: string;
  created_at: string;
  updated_at: string;
}

export interface Project {
  id: string;
  owner_id: string;
  name: string;
  description: string | null;
  created_at: string;
  updated_at: string;
}

export interface ApiErrorBody {
  error: {
    code: string;
    message: string;
    details?: unknown;
  };
}

export type DatasetStatus = "uploaded" | "validating" | "valid" | "invalid" | "failed";

// What a dataset is used for — a validated server-side enum (app.models.dataset.DatasetRole),
// never an arbitrary client-chosen label. SOURCE_IMAGE is the Phase 1 default.
export type DatasetRole = "source_image" | "dem_reference" | "gcp_reference";

export interface BoundingBox {
  min_x: number;
  min_y: number;
  max_x: number;
  max_y: number;
}

export interface Dataset {
  id: string;
  project_id: string;
  original_filename: string;
  file_type: string;
  mime_type: string;
  file_size_bytes: number;
  role: DatasetRole;
  status: DatasetStatus;
  validation_error: string | null;
  width: number | null;
  height: number | null;
  bands: number | null;
  is_georeferenced: boolean;
  crs: string | null;
  bbox: BoundingBox | null;
  // Populated only for role="gcp_reference".
  gcp_crs: string | null;
  gcp_point_count: number | null;
  created_at: string;
  updated_at: string;
}

export type AnalysisJobStatus = "queued" | "running" | "completed" | "failed" | "cancelled";

export type AnalysisStage =
  | "queued"
  | "preparing"
  | "validating_input"
  | "loading_model"
  | "preprocessing"
  | "inference"
  | "writing_depth"
  | "calibrating"
  | "writing_metric_elevation"
  | "writing_dsm"
  | "validating_results"
  // P1-3: DTM/nDSM estimates, only after a gate-passed calibration.
  | "filtering_ground"
  | "writing_dtm"
  | "writing_ndsm"
  | "loading_semantic_model"
  | "semantic_preprocessing"
  | "semantic_inference"
  | "writing_semantic"
  | "executing"
  | "finalizing"
  | "completed";

// Whether this job's relative depth was converted to metric elevation.
// UNCALIBRATED is the default/normal outcome for a job with no DEM/GCP
// reference — not an error. FAILED means calibration was requested but could
// not be completed; the job itself can still be "completed" either way.
export type CalibrationStatus = "uncalibrated" | "calibrating" | "calibrated" | "failed";

// Whether this job's real class-agnostic region segmentation (MobileSAM)
// completed. NOT_REQUESTED is the default/normal outcome for a job that
// didn't set enable_semantic_segmentation — not an error. FAILED means it
// was requested but could not be completed; the job itself can still be
// "completed" either way (soft-failure — mirrors CalibrationStatus).
export type SemanticStatus = "not_requested" | "processing" | "completed" | "failed";

// P1-3: whether the raster bare-earth approximation (DTM) and nDSM were
// produced. Only ever run after a calibration that passed the quality gate;
// NOT_REQUESTED otherwise. FAILED is a soft failure — the job and its DSM
// are unaffected.
export type GroundFilterStatus = "not_requested" | "processing" | "completed" | "failed";

// Whether this job's real terrain-derived hazard screening (Phase 8) was
// requested and how it went. Unlike CalibrationStatus/SemanticStatus (soft-
// failure add-ons to a depth-estimation job), a disaster-screening job is
// ALWAYS standalone — a real FAILED attempt also fails the job as a whole.
export type DisasterStatus = "not_requested" | "processing" | "completed" | "failed";

// One real detected region, exactly as persisted by the backend
// (app/services/semantic_pipeline.py). mask_score/predicted_iou are the
// model's own reported values — "Mask Stability", never "confidence" or
// "accuracy" — and are null when the model did not report one.
export interface SemanticRegion {
  region_id: number;
  pixel_area: number;
  bbox_row_min: number;
  bbox_col_min: number;
  bbox_row_max: number;
  bbox_col_max: number;
  mask_score: number | null;
  predicted_iou: number | null;
}

// Real fields on a successful semantic_metadata (a failed attempt instead
// carries just `error`) — kept loose (Record) at the type level since this
// is a JSONB blob, but these are the keys the backend actually populates.
export interface SemanticMetadata {
  error?: string;
  model_name?: string;
  model_task?: string;
  model_repository?: string;
  model_revision?: string;
  model_checkpoint?: string;
  model_license?: string;
  device?: string;
  points_per_side?: number;
  min_region_area_px?: number;
  inference_seconds?: number;
  total_seconds?: number;
  output_width?: number;
  output_height?: number;
  region_count?: number;
  regions?: SemanticRegion[];
  mask_stability_mean?: number | null;
  mask_stability_min?: number | null;
  mask_stability_max?: number | null;
  predicted_iou_mean?: number | null;
  value_semantics?: string;
  [key: string]: unknown;
}

// Real, backend-computed image-quality indicators (geospatial/image_quality.py),
// exactly as placed into AnalysisJob.execution_summary.image_quality —
// quality indicators only, never a model-uncertainty or confidence value.
export interface ImageQualitySummary {
  sharpness_laplacian_variance: number;
  underexposed_fraction: number;
  overexposed_fraction: number;
  luminance_min: number;
  luminance_max: number;
  luminance_mean: number;
  valid_pixel_fraction: number;
  notes: string;
}

export interface CalibrationValidationMetrics {
  mae: number;
  rmse: number;
  bias: number;
  min_residual: number;
  max_residual: number;
}

// P1-2 calibration quality gate (geospatial/calibration.py). Present on a
// calibration that reached the fitting stage — on success, and also on a
// quality-gate failure, which keeps every diagnostic alongside `error`.
export interface CalibrationFitDiagnostics {
  scale: number;
  offset: number;
  valid_sample_count: number;
  effective_sample_count: number | null;
  all_sample_mae: number;
  all_sample_rmse: number;
  all_sample_bias: number;
  in_sample_r2: number | null;
  pearson_r: number | null;
  spearman_rho: number | null;
}

export interface CalibrationCrossValidation {
  method: string;
  feasible: boolean;
  infeasibility_reason: string | null;
  fold_count: number;
  heldout_sample_count: number;
  heldout_mae: number | null;
  heldout_rmse: number | null;
  heldout_bias: number | null;
  sse_cv: number | null;
  sse_baseline_cv: number | null;
  skill: number | null;
  blocks_per_side: number | null;
  non_empty_block_count: number | null;
  folds: Record<string, number>[];
}

export interface CalibrationQualityGate {
  passed: boolean;
  failed_criteria: Record<string, unknown>[];
  not_evaluated: string[];
  policy: Record<string, unknown>;
}

// Real fields on calibration_metadata — kept loose (Record) at the type
// level since this is a JSONB blob, but these are the keys the backend
// actually populates (app/services/calibration_pipeline.py). A failure
// before fitting carries just `error`/`reference_type`; a quality-gate
// failure carries `error` plus the full diagnostics below.
export interface CalibrationMetadata {
  error?: string;
  method?: string;
  reference_type?: "dem" | "gcp";
  reference_dataset_id?: string;
  gcp_point_count?: number;
  scale_a?: number;
  offset_b?: number;
  outlier_sigma?: number;
  fit_iterations?: number;
  total_candidate_samples?: number;
  valid_samples?: number;
  inlier_samples?: number;
  outlier_samples?: number;
  // In-sample statistics over the fit's own inliers (see
  // validation_metrics_scope === "in_sample_inliers").
  validation_metrics?: CalibrationValidationMetrics;
  validation_metrics_scope?: string;
  fit_diagnostics?: CalibrationFitDiagnostics;
  cross_validation?: CalibrationCrossValidation;
  quality_gate?: CalibrationQualityGate;
  source_crs?: string;
  reference_crs?: string;
  reprojected?: boolean;
  calibration_duration_seconds?: number;
  limitations?: string;
  metric_elevation_artifact_id?: string;
  dsm_artifact_id?: string;
  [key: string]: unknown;
}

export interface AnalysisJob {
  id: string;
  project_id: string;
  dataset_id: string;
  user_id: string;
  status: AnalysisJobStatus;
  current_stage: AnalysisStage;
  progress: number;
  error_message: string | null;
  parameters: Record<string, unknown>;
  execution_summary: Record<string, unknown> | null;
  calibration_status: CalibrationStatus;
  calibration_metadata: CalibrationMetadata | null;
  semantic_status: SemanticStatus;
  semantic_metadata: SemanticMetadata | null;
  ground_filter_status: GroundFilterStatus;
  ground_filter_metadata: Record<string, unknown> | null;
  disaster_status: DisasterStatus;
  disaster_metadata: DisasterMetadata | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  updated_at: string;
}

export const ACTIVE_JOB_STATUSES: AnalysisJobStatus[] = ["queued", "running"];

export interface DepthExecutionSummary {
  model_name: string;
  model_revision: string;
  device: string;
  inference_seconds: number;
  output_width: number;
  output_height: number;
  artifact_id: string;
}

export interface AnalysisArtifact {
  id: string;
  analysis_job_id: string;
  artifact_type: string;
  mime_type: string;
  file_size_bytes: number;
  artifact_metadata: Record<string, unknown> | null;
  created_at: string;
}

// --------------------------------------------------------------------------
// Phase 5: visualization
// --------------------------------------------------------------------------

export type LayerType =
  | "rgb"
  | "relative_depth"
  | "metric_elevation"
  | "dsm"
  // P1-3 raster-derived ESTIMATES (never measured bare earth / heights).
  | "dtm"
  | "ndsm"
  | "semantic_segmentation"
  | "slope"
  | "aspect"
  | "hillshade"
  | "flood_screening"
  | "landslide_screening";

// A real value -> label pair for a fixed-class categorical layer (Phase 8's
// flood_screening/landslide_screening) — never populated for
// semantic_segmentation, whose region IDs are arbitrary per-image labels,
// not fixed named classes.
export interface LegendEntry {
  value: number;
  label: string;
}

export interface DatasetVisualizationContext {
  id: string;
  original_filename: string;
  file_type: string;
  width: number | null;
  height: number | null;
  is_georeferenced: boolean;
  crs: string | null;
  bounds: BoundingBox | null;
  bounds_wgs84: BoundingBox | null;
}

export interface PixelValue {
  row: number;
  col: number;
  value: number | null;
}

export interface LayerContext {
  layer_type: LayerType;
  display_name: string;
  available: boolean;
  unavailable_reason: string | null;
  artifact_id: string | null;
  analysis_job_id: string | null;
  width: number | null;
  height: number | null;
  dtype: string | null;
  nodata: number | null;
  is_georeferenced: boolean | null;
  crs: string | null;
  bounds: BoundingBox | null;
  min_value: number | null;
  max_value: number | null;
  // P1-6: exact WGS84 corners of this layer's own EPSG:3857 map-overlay grid
  // (null when not georeferenced) — where its `/map-preview` image goes.
  map_overlay_bounds: BoundingBox | null;
  notes: string | null;
  // True for a categorical (region-ID) raster — render with a deterministic
  // per-ID color legend, never the continuous min/max ramp used for
  // relative_depth/metric_elevation/dsm.
  is_categorical: boolean;
  // Only meaningful when is_categorical=true: the real number of distinct
  // detected regions (excludes background/NoData).
  region_count: number | null;
  // Only meaningful when is_categorical=true AND this layer has a real,
  // FIXED class set (Phase 8's flood_screening/landslide_screening) — null
  // for semantic_segmentation's arbitrary region IDs.
  legend: LegendEntry[] | null;
}

// Phase 10: a real 3D terrain source is either a calibrated `dsm` artifact
// ("elevation") or — when no calibration has succeeded — the job's own
// `relative_depth` artifact ("relative_depth"). `null` only when
// `available` is false. NEVER treat a "relative_depth" terrain's height
// values as elevation — see HeightKind's usage throughout
// components/terrain/terrainAvailability.ts.
//
// "remote_sensing_height" is a reserved, currently-UNUSED third value —
// nothing in the backend ever produces it today (see
// ai/rs_height_estimator.py and app/services/visualization.py's
// height_kind_for_artifact_type; TERRAIN_SOURCE_ARTIFACT_TYPES there does
// not yet accept it as a terrain source at all). It's declared here only so
// this type already matches the backend's real (equally unused) contract —
// no rendering code branches on it yet, and none should until a real
// remote-sensing height model exists. See docs/ARCHITECTURE_NOTE_RS_HEIGHT.md.
export type HeightKind = "elevation" | "relative_depth" | "remote_sensing_height";

export interface TerrainContext {
  available: boolean;
  unavailable_reason: string | null;
  artifact_id: string | null;
  analysis_job_id: string | null;
  height_kind: HeightKind | null;
  source_artifact_type: string | null; // "dsm" or "relative_depth"
  width: number | null;
  height: number | null;
  // D2: the backend's single texture-compatibility decision (also used by
  // the GLB export). Null only when the terrain is unavailable.
  texture_compatible: boolean | null;
  texture_unavailable_code: string | null;
  texture_unavailable_reason: string | null;
  is_georeferenced: boolean | null;
  crs: string | null;
  bounds: BoundingBox | null;
  // Populated ONLY when height_kind === "elevation" — never for a
  // relative-depth-backed terrain (see backend TerrainContextOut docstring).
  min_elevation: number | null;
  max_elevation: number | null;
  // Real min/max of the actual height values regardless of height_kind —
  // scientifically neutral naming, safe to display in either mode.
  min_height_value: number | null;
  max_height_value: number | null;
}

// P1-5: availability of the job's calibration_residuals POINT artifact —
// deliberately not a LayerContext (it is not a raster; see backend
// CalibrationResidualsContextOut).
export interface CalibrationResidualsContext {
  available: boolean;
  unavailable_reason: string | null;
  artifact_id: string | null;
  analysis_job_id: string | null;
  reference_type: "dem" | "gcp" | null;
  sample_count: number | null;
}

export interface VisualizationContext {
  dataset: DatasetVisualizationContext;
  layers: LayerContext[];
  terrain: TerrainContext;
  calibration_residuals: CalibrationResidualsContext;
}

// --------------------------------------------------------------------------
// P1-5: calibration residuals at calibration sample locations.
// residual = predicted calibrated elevation - reference elevation, in the
// calibration reference's own units. Held-out is the validation view; fit is
// in-sample.
// --------------------------------------------------------------------------

export type ResidualKind = "heldout" | "fit";

export interface ResidualFeatureProperties {
  sample_index: number;
  row: number;
  col: number;
  source_x: number;
  source_y: number;
  relative_depth: number;
  reference_elevation: number;
  predicted_heldout: number;
  residual_heldout: number;
  predicted_fit: number;
  residual_fit: number;
  inlier_in_production_fit: boolean;
  fold_id: number;
  block_id: number | null;
  reference_cell_id: number | null;
  gcp_index: number | null;
}

export interface ResidualFeature {
  type: "Feature";
  geometry: { type: "Point"; coordinates: [number, number] }; // [lon, lat], WGS84
  properties: ResidualFeatureProperties;
}

export interface ResidualStatistics {
  count: number;
  mae: number;
  rmse: number;
  bias: number;
  min: number;
  max: number;
}

export interface CalibrationResidualsSummary {
  residual_definition: string;
  units: string;
  disclaimer: string;
  reference_type: "dem" | "gcp";
  default_kind: ResidualKind;
  kinds: Record<ResidualKind, { label: string; statistics: ResidualStatistics }>;
  cv_method: string;
  sample_count: number;
  total_candidate_samples: number;
  inlier_samples: number;
  outlier_samples: number;
}

export interface CalibrationResidualsResult {
  artifact_id: string;
  analysis_job_id: string;
  summary: CalibrationResidualsSummary;
  feature_collection: { type: "FeatureCollection"; features: ResidualFeature[] };
}

export interface RasterMetadata {
  artifact_id: string;
  artifact_type: string;
  display_name: string;
  driver: string;
  width: number;
  height: number;
  count: number;
  dtype: string;
  nodata: number | null;
  is_georeferenced: boolean;
  crs: string | null;
  bounds: BoundingBox | null;
  resolution_x: number | null;
  resolution_y: number | null;
  min_value: number | null;
  max_value: number | null;
  has_finite_data: boolean;
  stats_sampled: boolean;
  notes: string | null;
  is_categorical: boolean;
  region_count: number | null;
  legend: LegendEntry[] | null;
}

// --------------------------------------------------------------------------
// Phase 8: disaster screening (terrain derivatives + hazard screening)
// --------------------------------------------------------------------------

export interface TerrainStatisticsSummary {
  min_elevation: number;
  max_elevation: number;
  mean_elevation: number;
  median_elevation: number;
  elevation_range: number;
  min_slope_deg: number | null;
  max_slope_deg: number | null;
  mean_slope_deg: number | null;
  valid_pixel_count: number;
  total_pixel_count: number;
}

// Real fields on a successful flood-screening sub-object of
// AnalysisJob.disaster_metadata (app/services/disaster_pipeline.py) — a
// terrain-based elevation-threshold screening, never a hydraulic simulation.
export interface FloodScreeningSummary {
  method: string;
  water_level: number;
  min_elevation: number;
  max_elevation: number;
  potentially_inundated_pixel_count: number;
  valid_pixel_count: number;
  pixel_area: number;
  area_unit: string;
  potentially_inundated_area: number;
  valid_area: number;
  potentially_inundated_percentage: number;
  class_labels: Record<string, string>;
  disclaimer: string;
}

// Real fields on a successful landslide-screening sub-object — a terrain-
// derived susceptibility screening INDEX, never a calibrated probability.
export interface LandslideScreeningSummary {
  method: string;
  thresholds: { low_max_deg: number; moderate_max_deg: number; high_max_deg: number };
  class_pixel_counts: Record<string, number>;
  class_areas: Record<string, number>;
  class_percentages: Record<string, number>;
  max_slope_deg: number;
  mean_slope_deg: number;
  pixel_area: number;
  area_unit: string;
  valid_pixel_count: number;
  class_labels: Record<string, string>;
  disclaimer: string;
}

// Real fields on AnalysisJob.disaster_metadata for a successful disaster-
// screening job (a failed attempt instead carries just `error`).
export interface DisasterMetadata {
  error?: string;
  source_artifact_id?: string;
  source_artifact_type?: string;
  source_crs?: string;
  analysis_crs?: string;
  reprojected_for_analysis?: boolean;
  pixel_width?: number;
  pixel_height?: number;
  width?: number;
  height?: number;
  terrain_statistics?: TerrainStatisticsSummary;
  timings_seconds?: Record<string, number>;
  disclaimer?: string;
  flood?: FloodScreeningSummary;
  landslide?: LandslideScreeningSummary;
  artifact_ids?: Record<string, string>;
  [key: string]: unknown;
}

// --------------------------------------------------------------------------
// Phase 7: measurements
// --------------------------------------------------------------------------

export type MeasurementType =
  | "point_elevation"
  | "distance"
  | "profile"
  | "coordinate"
  // P1-4: the stored slope-raster value at a map coordinate.
  | "point_slope";

// A real map coordinate for one pixel, computed server-side from the
// raster's own actual affine transform (geospatial/measurements.py) — never
// the earlier client-side linear-WGS84-interpolation approximation.
export interface CoordinateResult {
  row: number;
  col: number;
  is_georeferenced: boolean;
  crs: string | null;
  native_x: number | null;
  native_y: number | null;
  wgs84_lon: number | null;
  wgs84_lat: number | null;
}

// The real, backend-authoritative pixel a map coordinate falls in — the
// fix for the Phase 5/6 3D-terrain coordinate-space bug (see
// components/terrain/TerrainView3D.tsx).
export interface PixelResult {
  row: number;
  col: number;
  in_bounds: boolean;
}

export interface PointElevationResult {
  row: number;
  col: number;
  value: number | null;
  in_bounds: boolean;
  // "elevation" for metric_elevation/dsm, "relative_depth" for
  // relative_depth — never presented as interchangeable.
  value_kind: "elevation" | "relative_depth";
  units: string;
  calibration_state: string;
  artifact_type: string;
  disclaimer: string;
  coordinate: CoordinateResult;
}

// P1-4 slope-at-point (backend PointSlopeOut): the value stored in an
// existing slope raster at the pixel the map coordinate falls in, resolved
// against the slope raster's own CRS/transform. Never elevation, never
// recomputed or interpolated client-side. `value` is null on NoData or
// outside the raster (`in_bounds` says which); `coordinate` is null outside.
export interface PointSlopeResult {
  row: number;
  col: number;
  in_bounds: boolean;
  value: number | null;
  value_kind: "slope";
  units: string;
  query_x: number;
  query_y: number;
  query_crs: string | null;
  coordinate: CoordinateResult | null;
  artifact_type: string;
  source_artifact_id: string | null;
  source_artifact_type: string | null;
  reprojected_for_analysis: boolean | null;
  method: string | null;
  disclaimer: string;
}

export interface DistanceResult {
  point1: CoordinateResult;
  point2: CoordinateResult;
  pixel_distance: number;
  // Real-world planimetric distance — null only when the artifact is not
  // georeferenced at all (never a pixel-distance-times-guessed-scale value).
  distance: number | null;
  units: string;
  is_georeferenced: boolean;
  crs: string | null;
  reprojected: boolean;
  local_crs: string | null;
  calibration_state: string;
  artifact_type: string;
  disclaimer: string;
}

export interface ProfileSample {
  index: number;
  row: number;
  col: number;
  distance_along: number;
  value: number | null; // null = real NoData/NaN/Inf at this sample
  coordinate: CoordinateResult;
}

export interface ProfileResult {
  sample_count: number;
  total_distance: number;
  distance_units: string;
  is_georeferenced: boolean;
  crs: string | null;
  reprojected: boolean;
  local_crs: string | null;
  value_kind: "elevation" | "relative_depth";
  value_units: string;
  calibration_state: string;
  artifact_type: string;
  disclaimer: string;
  samples: ProfileSample[];
}

// A real, persisted measurement row (backend/app/models/measurement.py).
// `input_data`/`result_data` are loose (Record) at the type level since
// they're JSONB blobs whose shape depends on `measurement_type` — see the
// *Result interfaces above for what `result_data` actually contains per type.
export interface Measurement {
  id: string;
  project_id: string;
  analysis_job_id: string;
  artifact_id: string;
  user_id: string;
  measurement_type: MeasurementType;
  input_data: Record<string, unknown>;
  result_data: Record<string, unknown>;
  created_at: string;
}

export type MeasurementCreatePayload =
  | { measurement_type: "point_elevation"; analysis_job_id: string; artifact_id: string; row: number; col: number }
  | {
      measurement_type: "distance";
      analysis_job_id: string;
      artifact_id: string;
      row1: number;
      col1: number;
      row2: number;
      col2: number;
    }
  | {
      measurement_type: "profile";
      analysis_job_id: string;
      artifact_id: string;
      row1: number;
      col1: number;
      row2: number;
      col2: number;
      samples?: number;
    }
  | {
      measurement_type: "coordinate";
      analysis_job_id: string;
      artifact_id: string;
      row: number;
      col: number;
    }
  | {
      measurement_type: "point_slope";
      analysis_job_id: string;
      artifact_id: string;
      x: number;
      y: number;
      crs: string | null;
    };

// P1-8: a WGS84 point in a terrain artifact's own local 3D frame.
export interface TerrainLocalCoordinate {
  local_crs: string;
  map_x: number;
  map_y: number;
  in_footprint: boolean;
}

export interface TerrainMetadata {
  artifact_id: string;
  height_kind: HeightKind;
  source_artifact_type: string; // "dsm" or "relative_depth"
  width: number;
  height: number;
  source_width: number;
  source_height: number;
  nodata_present: boolean;
  is_georeferenced: boolean;
  crs: string | null;
  local_crs: string | null;
  origin_x: number | null;
  origin_y: number | null;
  cell_size_x: number | null;
  cell_size_y: number | null;
  bounds: BoundingBox | null;
  // Populated ONLY when height_kind === "elevation".
  min_elevation: number | null;
  max_elevation: number | null;
  // Real min/max of the actual returned grid values regardless of
  // height_kind.
  min_height_value: number | null;
  max_height_value: number | null;
  encoding: string;
}

// --------------------------------------------------------------------------
// Phase 9: reports & export
// --------------------------------------------------------------------------

export type ReportStatus = "pending" | "generating" | "completed" | "failed";

// The real, persisted status/availability record for one report — never
// includes the (potentially large) full report data snapshot itself; that
// is fetched separately via `getReportJson` only once a report is
// completed. See backend app/schemas/report.py::ReportRead.
export interface Report {
  id: string;
  project_id: string;
  dataset_id: string;
  user_id: string;
  status: ReportStatus;
  error_message: string | null;
  pdf_available: boolean;
  csv_available: boolean;
  bundle_available: boolean;
  created_at: string;
  completed_at: string | null;
  updated_at: string;
}

// The real, frozen data snapshot a completed report's PDF/CSV/bundle were
// all rendered from (see backend app/services/report_builder.py) — fetched
// via `GET .../reports/{id}/json`. Loosely typed (deliberately not a 1:1
// mirror of every backend field) since this is inherently a large, nested,
// evolving structure; the frontend only ever displays it, never recomputes
// or fabricates any of its values.
export interface ReportData {
  report_schema_version: string;
  generated_at: string;
  project: { id: string; name: string; description: string | null; created_at: string };
  dataset: Record<string, unknown>;
  depth_analysis: Record<string, unknown> | null;
  disaster_screening: Record<string, unknown> | null;
  artifacts: Record<string, unknown>[];
  measurements: Record<string, unknown>[];
  provenance: Record<string, unknown>;
  limitations: string[];
  [key: string]: unknown;
}
