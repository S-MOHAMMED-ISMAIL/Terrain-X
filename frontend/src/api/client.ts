import type {
  AnalysisArtifact,
  AnalysisJob,
  ApiErrorBody,
  CalibrationResidualsResult,
  CoordinateResult,
  Dataset,
  DatasetRole,
  DistanceResult,
  Measurement,
  MeasurementCreatePayload,
  PixelResult,
  PixelValue,
  PointElevationResult,
  PointSlopeResult,
  ProfileResult,
  Project,
  RasterMetadata,
  Report,
  ReportData,
  TerrainLocalCoordinate,
  TerrainMetadata,
  User,
  VisualizationContext,
} from "./types";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000/api/v1";

const TOKEN_STORAGE_KEY = "terrainx_access_token";

export class ApiError extends Error {
  code: string;
  status: number;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_STORAGE_KEY);
}

export function setToken(token: string): void {
  localStorage.setItem(TOKEN_STORAGE_KEY, token);
}

export function clearToken(): void {
  localStorage.removeItem(TOKEN_STORAGE_KEY);
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = getToken();
  const headers = new Headers(options.headers);
  headers.set("Content-Type", "application/json");
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }

  const response = await fetch(`${API_BASE_URL}${path}`, { ...options, headers });

  if (response.status === 204) {
    return undefined as T;
  }

  const body = await response.json().catch(() => null);

  if (!response.ok) {
    const errorBody = body as ApiErrorBody | null;
    throw new ApiError(
      response.status,
      errorBody?.error?.code ?? "unknown_error",
      errorBody?.error?.message ?? "An unexpected error occurred",
    );
  }

  return body as T;
}

function parseErrorBody(responseText: string, status: number): ApiError {
  let errorBody: ApiErrorBody | null = null;
  try {
    errorBody = JSON.parse(responseText);
  } catch {
    // response wasn't JSON (e.g. a proxy error page); fall through to default message
  }
  return new ApiError(
    status,
    errorBody?.error?.code ?? "unknown_error",
    errorBody?.error?.message ?? "An unexpected error occurred",
  );
}

/**
 * Uploads via XMLHttpRequest (not fetch) specifically to get real
 * upload.onprogress byte counts — never a fabricated percentage.
 */
function uploadWithProgress(
  path: string,
  file: File,
  onProgress?: (loaded: number, total: number) => void,
  extraFields?: Record<string, string>,
): Promise<Dataset> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${API_BASE_URL}${path}`);
    const token = getToken();
    if (token) {
      xhr.setRequestHeader("Authorization", `Bearer ${token}`);
    }

    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable && onProgress) {
        onProgress(event.loaded, event.total);
      }
    };

    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(JSON.parse(xhr.responseText) as Dataset);
      } else {
        reject(parseErrorBody(xhr.responseText, xhr.status));
      }
    };

    xhr.onerror = () => reject(new ApiError(0, "network_error", "Network error during upload"));

    const formData = new FormData();
    formData.append("file", file);
    for (const [key, value] of Object.entries(extraFields ?? {})) {
      formData.append(key, value);
    }
    xhr.send(formData);
  });
}

async function downloadFile(path: string, filename: string): Promise<void> {
  const blob = await fetchBlob(path);
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

/**
 * Fetches a binary (image/octet-stream) response with the same
 * Authorization header used by every other authenticated request — never a
 * plain <img src="..."> (which cannot attach a Bearer token) and never a
 * token in the query string. Callers turn the Blob into an object URL
 * (for an <img>) or read it as an ArrayBuffer (for the terrain grid).
 */
async function fetchBlob(path: string): Promise<Blob> {
  const token = getToken();
  const headers = new Headers();
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }

  const response = await fetch(`${API_BASE_URL}${path}`, { headers });
  if (!response.ok) {
    const text = await response.text();
    throw parseErrorBody(text, response.status);
  }
  return response.blob();
}

async function fetchArrayBuffer(path: string): Promise<ArrayBuffer> {
  const blob = await fetchBlob(path);
  return blob.arrayBuffer();
}

export const api = {
  register: (email: string, password: string) =>
    request<User>("/auth/register", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    }),

  login: (email: string, password: string) =>
    request<{ access_token: string; token_type: string }>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    }),

  me: () => request<User>("/auth/me"),

  listProjects: () => request<Project[]>("/projects"),

  getProject: (id: string) => request<Project>(`/projects/${id}`),

  createProject: (name: string, description?: string) =>
    request<Project>("/projects", {
      method: "POST",
      body: JSON.stringify({ name, description: description || undefined }),
    }),

  updateProject: (id: string, data: { name?: string; description?: string }) =>
    request<Project>(`/projects/${id}`, {
      method: "PATCH",
      body: JSON.stringify(data),
    }),

  deleteProject: (id: string) =>
    request<void>(`/projects/${id}`, { method: "DELETE" }),

  listDatasets: (projectId: string, role?: DatasetRole) =>
    request<Dataset[]>(
      `/projects/${projectId}/datasets${role ? `?role=${encodeURIComponent(role)}` : ""}`,
    ),

  getDataset: (projectId: string, datasetId: string) =>
    request<Dataset>(`/projects/${projectId}/datasets/${datasetId}`),

  uploadDataset: (
    projectId: string,
    file: File,
    onProgress?: (loaded: number, total: number) => void,
    options?: { role?: DatasetRole; gcpCrs?: string },
  ) =>
    uploadWithProgress(`/projects/${projectId}/datasets`, file, onProgress, {
      ...(options?.role ? { role: options.role } : {}),
      ...(options?.gcpCrs ? { gcp_crs: options.gcpCrs } : {}),
    }),

  deleteDataset: (projectId: string, datasetId: string) =>
    request<void>(`/projects/${projectId}/datasets/${datasetId}`, { method: "DELETE" }),

  downloadDataset: (projectId: string, datasetId: string, filename: string) =>
    downloadFile(`/projects/${projectId}/datasets/${datasetId}/download`, filename),

  createAnalysisJob: (
    projectId: string,
    datasetId: string,
    options?: {
      demReferenceDatasetId?: string;
      gcpReferenceDatasetId?: string;
      enableSemanticSegmentation?: boolean;
      // Phase 8: real terrain-derived hazard screening. Standalone —
      // never combined with calibration/segmentation in the same job
      // (enforced server-side by AnalysisParametersV1._validate_disaster_screening).
      disasterSourceArtifactId?: string;
      runFloodScreening?: boolean;
      waterLevel?: number;
      runLandslideScreening?: boolean;
      landslideThresholds?: {
        lowMaxDeg?: number;
        moderateMaxDeg?: number;
        highMaxDeg?: number;
      };
    },
  ) =>
    request<AnalysisJob>(`/projects/${projectId}/datasets/${datasetId}/analysis`, {
      method: "POST",
      body: JSON.stringify({
        parameters: {
          version: "v1",
          ...(options?.demReferenceDatasetId
            ? { dem_reference_dataset_id: options.demReferenceDatasetId }
            : {}),
          ...(options?.gcpReferenceDatasetId
            ? { gcp_reference_dataset_id: options.gcpReferenceDatasetId }
            : {}),
          ...(options?.enableSemanticSegmentation
            ? { enable_semantic_segmentation: true }
            : {}),
          ...(options?.disasterSourceArtifactId
            ? { disaster_source_artifact_id: options.disasterSourceArtifactId }
            : {}),
          ...(options?.runFloodScreening ? { run_flood_screening: true } : {}),
          ...(options?.waterLevel !== undefined ? { water_level: options.waterLevel } : {}),
          ...(options?.runLandslideScreening ? { run_landslide_screening: true } : {}),
          ...(options?.landslideThresholds
            ? {
                landslide_thresholds: {
                  ...(options.landslideThresholds.lowMaxDeg !== undefined
                    ? { low_max_deg: options.landslideThresholds.lowMaxDeg }
                    : {}),
                  ...(options.landslideThresholds.moderateMaxDeg !== undefined
                    ? { moderate_max_deg: options.landslideThresholds.moderateMaxDeg }
                    : {}),
                  ...(options.landslideThresholds.highMaxDeg !== undefined
                    ? { high_max_deg: options.landslideThresholds.highMaxDeg }
                    : {}),
                },
              }
            : {}),
        },
      }),
    }),

  listAnalysisJobs: (projectId: string) =>
    request<AnalysisJob[]>(`/projects/${projectId}/analysis`),

  getAnalysisJob: (projectId: string, jobId: string) =>
    request<AnalysisJob>(`/projects/${projectId}/analysis/${jobId}`),

  cancelAnalysisJob: (projectId: string, jobId: string) =>
    request<AnalysisJob>(`/projects/${projectId}/analysis/${jobId}/cancel`, { method: "POST" }),

  listArtifacts: (projectId: string, jobId: string) =>
    request<AnalysisArtifact[]>(`/projects/${projectId}/analysis/${jobId}/artifacts`),

  downloadArtifact: (projectId: string, jobId: string, artifactId: string, filename: string) =>
    downloadFile(
      `/projects/${projectId}/analysis/${jobId}/artifacts/${artifactId}/download`,
      filename,
    ),

  // P1-5: the stored calibration residuals (sample points only).
  getCalibrationResiduals: (projectId: string, jobId: string, artifactId: string) =>
    request<CalibrationResidualsResult>(
      `/projects/${projectId}/analysis/${jobId}/artifacts/${artifactId}/calibration-residuals`,
    ),

  // ------------------------------------------------------------------------
  // Phase 5: visualization
  // ------------------------------------------------------------------------

  getVisualizationContext: (projectId: string, datasetId: string) =>
    request<VisualizationContext>(
      `/projects/${projectId}/datasets/${datasetId}/visualization/context`,
    ),

  getDatasetPreviewBlob: (projectId: string, datasetId: string) =>
    fetchBlob(`/projects/${projectId}/datasets/${datasetId}/visualization/preview`),

  // P1-6: presentation-only overlays reprojected to EPSG:3857 for the 2D map.
  getDatasetMapPreviewBlob: (projectId: string, datasetId: string) =>
    fetchBlob(`/projects/${projectId}/datasets/${datasetId}/visualization/map-preview`),

  getArtifactMapPreviewBlob: (projectId: string, jobId: string, artifactId: string) =>
    fetchBlob(
      `/projects/${projectId}/analysis/${jobId}/artifacts/${artifactId}/visualization/map-preview`,
    ),

  getArtifactRasterMetadata: (projectId: string, jobId: string, artifactId: string) =>
    request<RasterMetadata>(
      `/projects/${projectId}/analysis/${jobId}/artifacts/${artifactId}/visualization/metadata`,
    ),

  getArtifactPreviewBlob: (projectId: string, jobId: string, artifactId: string) =>
    fetchBlob(
      `/projects/${projectId}/analysis/${jobId}/artifacts/${artifactId}/visualization/preview`,
    ),

  getArtifactWindowBlob: (
    projectId: string,
    jobId: string,
    artifactId: string,
    window: { colOff: number; rowOff: number; width: number; height: number },
  ) =>
    fetchBlob(
      `/projects/${projectId}/analysis/${jobId}/artifacts/${artifactId}/visualization/window` +
        `?col_off=${window.colOff}&row_off=${window.rowOff}&width=${window.width}&height=${window.height}`,
    ),

  getTerrainMetadata: (projectId: string, jobId: string, artifactId: string) =>
    request<TerrainMetadata>(
      `/projects/${projectId}/analysis/${jobId}/artifacts/${artifactId}/visualization/terrain/metadata`,
    ),

  // P1-8: WGS84 -> the terrain's own local frame (for 2D flythrough waypoints).
  getTerrainLocalCoordinate: (
    projectId: string,
    jobId: string,
    artifactId: string,
    lng: number,
    lat: number,
  ) =>
    request<TerrainLocalCoordinate>(
      `/projects/${projectId}/analysis/${jobId}/artifacts/${artifactId}/visualization/terrain/local-coordinate` +
        `?lng=${lng}&lat=${lat}`,
    ),

  // P1-9: the terrain grid as a physical GLB mesh (256 or 512 px grid).
  getTerrainMeshGlb: (
    projectId: string,
    jobId: string,
    artifactId: string,
    resolution: number,
    texture: boolean,
  ) =>
    fetchArrayBuffer(
      `/projects/${projectId}/analysis/${jobId}/artifacts/${artifactId}/visualization/terrain/mesh.glb` +
        `?resolution=${resolution}&texture=${texture}`,
    ),

  getTerrainGrid: (projectId: string, jobId: string, artifactId: string) =>
    fetchArrayBuffer(
      `/projects/${projectId}/analysis/${jobId}/artifacts/${artifactId}/visualization/terrain/grid`,
    ),

  getArtifactPixelValue: (
    projectId: string,
    jobId: string,
    artifactId: string,
    row: number,
    col: number,
  ) =>
    request<PixelValue>(
      `/projects/${projectId}/analysis/${jobId}/artifacts/${artifactId}/visualization/value` +
        `?row=${row}&col=${col}`,
    ),

  // ------------------------------------------------------------------------
  // Phase 7: measurements
  // ------------------------------------------------------------------------

  getPointElevation: (projectId: string, jobId: string, artifactId: string, row: number, col: number) =>
    request<PointElevationResult>(
      `/projects/${projectId}/analysis/${jobId}/artifacts/${artifactId}/measurements/point` +
        `?row=${row}&col=${col}`,
    ),

  getMeasuredDistance: (
    projectId: string,
    jobId: string,
    artifactId: string,
    row1: number,
    col1: number,
    row2: number,
    col2: number,
  ) =>
    request<DistanceResult>(
      `/projects/${projectId}/analysis/${jobId}/artifacts/${artifactId}/measurements/distance` +
        `?row1=${row1}&col1=${col1}&row2=${row2}&col2=${col2}`,
    ),

  getElevationProfile: (
    projectId: string,
    jobId: string,
    artifactId: string,
    row1: number,
    col1: number,
    row2: number,
    col2: number,
    samples: number,
  ) =>
    request<ProfileResult>(
      `/projects/${projectId}/analysis/${jobId}/artifacts/${artifactId}/measurements/profile` +
        `?row1=${row1}&col1=${col1}&row2=${row2}&col2=${col2}&samples=${samples}`,
    ),

  getCoordinateForPixel: (
    projectId: string,
    jobId: string,
    artifactId: string,
    row: number,
    col: number,
  ) =>
    request<CoordinateResult>(
      `/projects/${projectId}/analysis/${jobId}/artifacts/${artifactId}/measurements/coordinate` +
        `?row=${row}&col=${col}`,
    ),

  // The backend-authoritative inverse of getCoordinateForPixel — resolves a
  // real map coordinate (e.g. from a 3D-view raycast hit point) to the
  // exact full-resolution pixel it falls in. See TerrainView3D.tsx for why
  // this replaced a fragile client-side grid-index rescale.
  getPixelForCoordinate: (
    projectId: string,
    jobId: string,
    artifactId: string,
    x: number,
    y: number,
    crs: string | null,
  ) =>
    request<PixelResult>(
      `/projects/${projectId}/analysis/${jobId}/artifacts/${artifactId}/measurements/pixel` +
        `?x=${x}&y=${y}` +
        (crs ? `&crs=${encodeURIComponent(crs)}` : ""),
    ),

  // P1-4: slope-at-point — the backend resolves (x, y, crs) against the
  // slope raster's own CRS/transform and returns the stored value unchanged.
  getPointSlope: (
    projectId: string,
    jobId: string,
    artifactId: string,
    x: number,
    y: number,
    crs: string | null,
  ) =>
    request<PointSlopeResult>(
      `/projects/${projectId}/analysis/${jobId}/artifacts/${artifactId}/measurements/slope` +
        `?x=${x}&y=${y}` +
        (crs ? `&crs=${encodeURIComponent(crs)}` : ""),
    ),

  createMeasurement: (projectId: string, payload: MeasurementCreatePayload) =>
    request<Measurement>(`/projects/${projectId}/measurements`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  listMeasurements: (projectId: string, analysisJobId?: string) =>
    request<Measurement[]>(
      `/projects/${projectId}/measurements` +
        (analysisJobId ? `?analysis_job_id=${analysisJobId}` : ""),
    ),

  getMeasurement: (projectId: string, measurementId: string) =>
    request<Measurement>(`/projects/${projectId}/measurements/${measurementId}`),

  deleteMeasurement: (projectId: string, measurementId: string) =>
    request<void>(`/projects/${projectId}/measurements/${measurementId}`, { method: "DELETE" }),

  // ------------------------------------------------------------------------
  // Phase 9: reports & export
  // ------------------------------------------------------------------------

  createReport: (projectId: string, datasetId: string) =>
    request<Report>(`/projects/${projectId}/datasets/${datasetId}/reports`, {
      method: "POST",
      body: JSON.stringify({}),
    }),

  listReports: (projectId: string, datasetId?: string) =>
    request<Report[]>(
      `/projects/${projectId}/reports` +
        (datasetId ? `?dataset_id=${datasetId}` : ""),
    ),

  getReport: (projectId: string, reportId: string) =>
    request<Report>(`/projects/${projectId}/reports/${reportId}`),

  getReportJson: (projectId: string, reportId: string) =>
    request<ReportData>(`/projects/${projectId}/reports/${reportId}/json`),

  downloadReportPdf: (projectId: string, reportId: string) =>
    downloadFile(`/projects/${projectId}/reports/${reportId}/pdf`, "report.pdf"),

  // Reuses the exact same authenticated blob-download mechanism as the
  // PDF/CSV/bundle downloads below — the real JSON export already existed
  // at the API level (see getReportJson above, and the backend's own
  // report_render.py) but had no "save to disk" UI action until now.
  downloadReportJson: (projectId: string, reportId: string) =>
    downloadFile(`/projects/${projectId}/reports/${reportId}/json`, "report.json"),

  downloadReportCsv: (projectId: string, reportId: string) =>
    downloadFile(`/projects/${projectId}/reports/${reportId}/csv`, "report.csv"),

  downloadReportBundle: (projectId: string, reportId: string) =>
    downloadFile(
      `/projects/${projectId}/reports/${reportId}/bundle`,
      "terrainx_report_bundle.zip",
    ),
};
