import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, api } from "@/api/client";
import { AnalysisJobCard } from "@/components/analysis/AnalysisJobCard";
import { useBackgroundActivity } from "@/components/PageHeaderContext";
import { Badge, Button, Card, EmptyState, Spinner } from "@/components/ui";
import type { AnalysisJob, Dataset } from "@/api/types";
import { ACTIVE_JOB_STATUSES } from "@/api/types";

// The backend/database is the sole source of truth for job state; this
// value only controls how often the UI asks it for an update.
const POLL_INTERVAL_MS = Number(import.meta.env.VITE_ANALYSIS_POLL_INTERVAL_MS) || 3000;

function hasActiveJob(jobs: AnalysisJob[] | null): boolean {
  return (jobs ?? []).some((job) => ACTIVE_JOB_STATUSES.includes(job.status));
}

interface Props {
  projectId: string;
  datasets: Dataset[];
  onOpenWorkspace: (datasetId?: string) => void;
  onOpenReports: () => void;
}

type ReferenceChoice = "none" | "dem" | "gcp";

export function AnalysisPanel({ projectId, datasets, onOpenWorkspace, onOpenReports }: Props) {
  const [jobs, setJobs] = useState<AnalysisJob[] | null>(null);
  // Real, already-known state (is a real analysis job currently queued/
  // running for this project) — lets the ambient background's scan sweep
  // become slightly livelier while genuine work is in progress, and settle
  // otherwise. Never a fabricated/simulated activity signal.
  useBackgroundActivity(hasActiveJob(jobs) ? "active" : "idle");
  const [selectedDatasetId, setSelectedDatasetId] = useState("");
  const [referenceChoice, setReferenceChoice] = useState<ReferenceChoice>("none");
  const [demReferenceId, setDemReferenceId] = useState("");
  const [gcpReferenceId, setGcpReferenceId] = useState("");
  const [enableSemanticSegmentation, setEnableSemanticSegmentation] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [cancellingId, setCancellingId] = useState<string | null>(null);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const validDatasets = datasets.filter(
    (d) => d.status === "valid" && d.role === "source_image",
  );
  const selectedDataset = validDatasets.find((d) => d.id === selectedDatasetId);
  const demReferences = datasets.filter((d) => d.role === "dem_reference" && d.status === "valid");
  const gcpReferences = datasets.filter((d) => d.role === "gcp_reference" && d.status === "valid");

  const refresh = useCallback(async (): Promise<AnalysisJob[] | null> => {
    try {
      const data = await api.listAnalysisJobs(projectId);
      setJobs(data);
      return data;
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load analysis jobs");
      return null;
    }
  }, [projectId]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // Poll only while a job is queued/running, and stop as soon as none are —
  // never simulate progress independently of what the backend reports.
  useEffect(() => {
    const stopPolling = () => {
      if (intervalRef.current) {
        clearInterval(intervalRef.current);
        intervalRef.current = null;
      }
    };

    if (!hasActiveJob(jobs)) {
      stopPolling();
      return;
    }

    if (!intervalRef.current) {
      intervalRef.current = setInterval(async () => {
        const data = await refresh();
        if (!hasActiveJob(data)) {
          stopPolling();
        }
      }, POLL_INTERVAL_MS);
    }

    return stopPolling;
  }, [jobs, refresh]);

  async function handleSubmit() {
    if (!selectedDatasetId) return;
    setError(null);
    setIsSubmitting(true);
    try {
      await api.createAnalysisJob(projectId, selectedDatasetId, {
        demReferenceDatasetId: referenceChoice === "dem" ? demReferenceId : undefined,
        gcpReferenceDatasetId: referenceChoice === "gcp" ? gcpReferenceId : undefined,
        enableSemanticSegmentation,
      });
      await refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to start analysis");
    } finally {
      setIsSubmitting(false);
    }
  }

  async function handleCancel(jobId: string) {
    setError(null);
    setCancellingId(jobId);
    try {
      await api.cancelAnalysisJob(projectId, jobId);
      await refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to cancel job");
    } finally {
      setCancellingId(null);
    }
  }

  async function handleDownloadArtifact(
    jobId: string,
    artifactType:
      | "relative_depth"
      | "metric_elevation"
      | "dsm"
      | "dtm"
      | "ndsm"
      | "semantic_segmentation",
  ) {
    setError(null);
    try {
      const artifacts = await api.listArtifacts(projectId, jobId);
      const artifact = artifacts.find((a) => a.artifact_type === artifactType);
      if (!artifact) {
        setError(`No ${artifactType} artifact is available for this job.`);
        return;
      }
      await api.downloadArtifact(projectId, jobId, artifact.id, `${artifactType}.tif`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : `Failed to download ${artifactType} artifact`);
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <section aria-labelledby="available-analysis-title">
        <div className="mb-3">
          <h2 id="available-analysis-title" className="text-lg font-semibold text-white">Available analysis</h2>
          <p className="text-supporting text-slate-400">One terrain analysis produces the products supported by its selected inputs.</p>
        </div>
        <div className="divide-y divide-slate-200 rounded-panel border border-slate-200 bg-white shadow-panel">
          <AnalysisOption name="Relative depth" description="Unitless monocular depth; produced by every completed terrain analysis." status={validDatasets.length ? "Available" : "Requires input"} />
          <AnalysisOption name="Calibration and DSM" description="Attempts metric calibration when a valid DEM or GCP reference is selected." status={demReferences.length || gcpReferences.length ? "Available" : "Requires reference"} />
          <AnalysisOption name="DTM and nDSM" description="Estimated ground and height-above-ground products generated after calibration passes its quality gate." status="Pipeline product" />
          <AnalysisOption name="Disaster screening" description="Existing terrain-derived screening workflow in the shared workspace." status="Open workspace" action={onOpenWorkspace} />
        </div>
      </section>

      <Card>
        <h2 className="mb-3 text-panel-title text-slate-950">Configure analysis</h2>
        <label htmlFor="analysis-dataset" className="mb-1 block text-sm font-medium text-slate-700">Dataset</label>
        {validDatasets.length === 0 ? (
          <EmptyState
            title="No valid datasets yet"
            description="Upload one in the Datasets tab first."
          />
        ) : (
          <>
            <select
              id="analysis-dataset"
              value={selectedDatasetId}
              onChange={(e) => setSelectedDatasetId(e.target.value)}
              className="mb-3 w-full rounded-md border border-slate-300 px-3 py-2 text-sm transition-colors focus:border-brand-500"
            >
              <option value="">Select a dataset…</option>
              {validDatasets.map((d) => (
                <option key={d.id} value={d.id}>
                  {d.original_filename}
                </option>
              ))}
            </select>

            {selectedDataset && (
              <div className="mb-3 rounded-md bg-slate-50 px-3 py-2 text-xs text-slate-600 animate-fade-in">
                <p>
                  {selectedDataset.width} × {selectedDataset.height}, {selectedDataset.bands}{" "}
                  band(s)
                </p>
                <p>
                  {selectedDataset.is_georeferenced
                    ? `Georeferenced (${selectedDataset.crs ?? "unknown CRS"})`
                    : "Non-georeferenced imagery"}
                </p>
              </div>
            )}

            <div className="mb-3">
              <span className="mb-1 block text-sm font-medium text-slate-700">
                Metric calibration (optional)
              </span>
              <div className="mb-2 flex gap-2">
                {(
                  [
                    { value: "none", label: "None (relative depth only)" },
                    { value: "dem", label: "DEM reference" },
                    { value: "gcp", label: "GCP reference" },
                  ] as { value: ReferenceChoice; label: string }[]
                ).map((option) => (
                  <button
                    key={option.value}
                    type="button"
                    onClick={() => setReferenceChoice(option.value)}
                    className={`rounded-md px-3 py-1.5 text-xs font-medium transition-colors duration-150 ${
                      referenceChoice === option.value
                        ? "bg-slate-900 text-white"
                        : "bg-slate-100 text-slate-600 hover:bg-slate-200"
                    }`}
                  >
                    {option.label}
                  </button>
                ))}
              </div>

              {referenceChoice === "dem" &&
                (demReferences.length === 0 ? (
                  <p className="text-xs text-slate-500">
                    No valid DEM references yet — upload one (role: DEM reference) in the Datasets
                    tab.
                  </p>
                ) : (
                  <select
                    aria-label="DEM reference dataset"
                    value={demReferenceId}
                    onChange={(e) => setDemReferenceId(e.target.value)}
                    className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
                  >
                    <option value="">Select a DEM reference…</option>
                    {demReferences.map((d) => (
                      <option key={d.id} value={d.id}>
                        {d.original_filename} ({d.crs ?? "unknown CRS"})
                      </option>
                    ))}
                  </select>
                ))}

              {referenceChoice === "gcp" &&
                (gcpReferences.length === 0 ? (
                  <p className="text-xs text-slate-500">
                    No valid GCP references yet — upload one (role: GCP reference) in the Datasets
                    tab.
                  </p>
                ) : (
                  <select
                    aria-label="GCP reference dataset"
                    value={gcpReferenceId}
                    onChange={(e) => setGcpReferenceId(e.target.value)}
                    className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
                  >
                    <option value="">Select a GCP reference…</option>
                    {gcpReferences.map((d) => (
                      <option key={d.id} value={d.id}>
                        {d.original_filename} ({d.gcp_point_count ?? 0} points, {d.gcp_crs})
                      </option>
                    ))}
                  </select>
                ))}
            </div>

            <div className="mb-3">
              <label className="flex items-center gap-2 text-sm font-medium text-slate-700">
                <input
                  type="checkbox"
                  checked={enableSemanticSegmentation}
                  onChange={(e) => setEnableSemanticSegmentation(e.target.checked)}
                />
                Detect distinct surface regions (experimental)
              </label>
              <p className="mt-1 text-xs text-slate-500">
                Runs real class-agnostic region segmentation (MobileSAM) over the source image.
                Detected regions are arbitrary IDs, not semantic labels like "building" or "road" —
                see the Terrain workspace for details. Adds real CPU inference time (typically
                tens of seconds) to this job.
              </p>
            </div>

            <Button
              onClick={handleSubmit}
              disabled={
                !selectedDatasetId ||
                isSubmitting ||
                (referenceChoice === "dem" && !demReferenceId) ||
                (referenceChoice === "gcp" && !gcpReferenceId)
              }
              className="flex items-center gap-2"
            >
              {isSubmitting && <Spinner />}
              {isSubmitting ? "Starting…" : "Start Analysis"}
            </Button>
            <p className="mt-3 text-xs text-slate-500">
              Always produces{" "}
              <span className="font-medium text-slate-500">Relative Depth — Uncalibrated</span> from
              a monocular depth model — not elevation, not a DSM on its own. Attaching a DEM/GCP
              reference above additionally attempts real metric calibration; if it succeeds you also
              get <span className="font-medium text-slate-500">Metric Elevation — Calibrated</span>{" "}
              and <span className="font-medium text-slate-500">DSM — Calibrated</span> with honest
              scale/offset/residual statistics. If calibration fails (e.g. no valid reference
              samples), the job still completes with its relative depth result.
            </p>
          </>
        )}
      </Card>

      {error && (
        <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700 animate-fade-in">
          {error}
        </div>
      )}

      <section aria-labelledby="recent-analysis-title">
        <h2 id="recent-analysis-title" className="mb-3 text-lg font-semibold text-white">Active and recent jobs</h2>
        {jobs === null ? (
          <div role="status" aria-label="Loading analysis jobs" className="flex flex-col gap-2"><div className="h-28 w-full skeleton" /><div className="h-28 w-full skeleton" /></div>
        ) : jobs.length === 0 ? (
          <EmptyState title="No analysis jobs yet" description="Select a valid source dataset and start the first terrain analysis above." />
        ) : (
          <div className="flex flex-col gap-3">
            {jobs.map((job) => <AnalysisJobCard key={job.id} job={job} dataset={datasets.find((dataset) => dataset.id === job.dataset_id)} cancelling={cancellingId === job.id} onCancel={() => handleCancel(job.id)} onDownload={(type) => handleDownloadArtifact(job.id, type)} onOpenWorkspace={() => onOpenWorkspace(job.dataset_id)} onOpenReports={onOpenReports} />)}
          </div>
        )}
      </section>
    </div>
  );
}

function AnalysisOption({ name, description, status, action }: { name: string; description: string; status: string; action?: () => void }) {
  return (
    <div className="flex min-w-0 flex-col gap-2 px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
      <div className="min-w-0"><h3 className="text-panel-title text-slate-950">{name}</h3><p className="text-supporting text-slate-500">{description}</p></div>
      <div className="flex shrink-0 items-center gap-2"><Badge tone={status === "Available" ? "success" : status === "Requires input" || status === "Requires reference" ? "warning" : "neutral"}>{status}</Badge>{action && <Button size="sm" variant="secondary" onClick={action}>Open</Button>}</div>
    </div>
  );
}
