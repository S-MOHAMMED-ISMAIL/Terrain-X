import type { ReactNode } from "react";
import type { AnalysisJob, Dataset } from "@/api/types";
import { Badge, type BadgeTone, Button, Notice, ProgressBar, TechnicalKeyValue } from "@/components/ui";
import { PipelineTracker } from "./PipelineTracker";
import { buildPipelineSteps, isDepthPipelineJob } from "./analysisPipeline";
import { calibrationPresentation, groundProductPresentation, verticalUnit } from "./analysisPresentation";

type ArtifactType = "relative_depth" | "metric_elevation" | "dsm" | "dtm" | "ndsm" | "semantic_segmentation";

const JOB_TONE: Record<AnalysisJob["status"], BadgeTone> = {
  queued: "neutral", running: "warning", completed: "success", failed: "danger", cancelled: "neutral",
};

const STATE_TONE: Record<string, BadgeTone> = {
  available: "info", processing: "warning", completed: "success", failed: "danger", rejected: "warning", unavailable: "neutral",
};

interface Props {
  job: AnalysisJob;
  dataset?: Dataset;
  cancelling: boolean;
  onCancel: () => void;
  onDownload: (type: ArtifactType) => void;
  onOpenWorkspace: () => void;
  onOpenReports: () => void;
}

function date(value: string | null): string {
  return value ? new Date(value).toLocaleString() : "Not available";
}

function metric(value: unknown): string {
  return typeof value === "number" && Number.isFinite(value) ? value.toFixed(4) : "Not reported";
}

export function AnalysisJobCard({ job, dataset, cancelling, onCancel, onDownload, onOpenWorkspace, onOpenReports }: Props) {
  const calibration = calibrationPresentation(job);
  const ground = groundProductPresentation(job.ground_filter_status, job);
  const metadata = job.calibration_metadata;
  const fit = metadata?.fit_diagnostics;
  const heldout = metadata?.cross_validation;
  const unit = verticalUnit(job);
  const active = job.status === "queued" || job.status === "running";

  return (
    <article className="rounded-panel border border-slate-200 bg-white shadow-panel" aria-label={`Analysis job ${dataset?.original_filename ?? job.dataset_id}`}>
      <header className="flex min-w-0 flex-wrap items-start justify-between gap-3 border-b border-slate-200 px-4 py-3">
        <div className="min-w-0">
          <h3 className="truncate text-panel-title text-slate-950">Terrain analysis</h3>
          <p className="truncate text-supporting text-slate-500">Input: {dataset?.original_filename ?? job.dataset_id}</p>
        </div>
        <span role="status" aria-live="polite" aria-atomic="true">
          <Badge tone={JOB_TONE[job.status]} dot={job.status === "running"}>{job.status}</Badge>
        </span>
      </header>

      <div className="px-4 py-3">
        {active && <ProgressBar value={job.progress} className="mb-3" />}
        {isDepthPipelineJob(job) && <div className="mb-3 overflow-x-auto pb-1"><PipelineTracker steps={buildPipelineSteps(job)} currentStageRaw={`${job.current_stage} (${job.progress}%)`} /></div>}
        {job.status === "failed" && <Notice tone="error" title="Analysis failed">{job.error_message ?? "The analysis job did not complete."} Review the selected inputs and run the analysis again.</Notice>}

        <section aria-label="Generated products" className="mt-3 divide-y divide-slate-100 border-y border-slate-100">
          <ProductRow name="Relative Depth" description="Unitless monocular depth; not elevation" state={job.status === "completed" ? "completed" : active ? "processing" : job.status === "failed" ? "failed" : "unavailable"} label={job.status === "completed" ? "Complete" : active ? "Processing" : job.status === "failed" ? "Failed" : "Unavailable"} unit="Unitless" action={job.status === "completed" ? <Button size="sm" variant="ghost" onClick={() => onDownload("relative_depth")}>Download depth</Button> : null} />
          <ProductRow name="Metric Elevation" description="Calibrated elevation raster" state={calibration.state} label={calibration.state === "completed" ? "Complete" : calibration.label} unit={calibration.state === "completed" ? unit : "Unavailable"} reason={calibration.reason} action={calibration.state === "completed" ? <Button size="sm" variant="ghost" onClick={() => onDownload("metric_elevation")}>Download elevation</Button> : null} />
          <ProductRow name="DSM" description="Calibrated surface elevation" state={calibration.state} label={calibration.state === "completed" ? "Complete" : calibration.label} unit={calibration.state === "completed" ? unit : "Unavailable"} reason={calibration.reason} action={calibration.state === "completed" ? <Button size="sm" variant="ghost" onClick={() => onDownload("dsm")}>Download DSM</Button> : null} />
          <ProductRow name="DTM" description="Estimated ground surface; raster-filter estimate" state={ground.state} label={ground.label} unit={ground.state === "completed" ? unit : "Unavailable"} reason={ground.reason} action={ground.state === "completed" ? <Button size="sm" variant="ghost" onClick={() => onDownload("dtm")}>Download DTM (estimate)</Button> : null} />
          <ProductRow name="nDSM" description="DSM minus estimated DTM; estimated height above ground" state={ground.state} label={ground.label} unit={ground.state === "completed" ? unit : "Unavailable"} reason={ground.reason} action={ground.state === "completed" ? <Button size="sm" variant="ghost" onClick={() => onDownload("ndsm")}>Download nDSM (estimate)</Button> : null} />
          {job.parameters?.enable_semantic_segmentation === true && <ProductRow name="Surface Regions" description="Class-agnostic detected regions; IDs are not semantic classes" state={job.semantic_status === "completed" ? "completed" : job.semantic_status === "processing" ? "processing" : job.semantic_status === "failed" ? "failed" : "unavailable"} label={job.semantic_status === "completed" ? "Complete" : job.semantic_status === "processing" ? "Processing" : job.semantic_status === "failed" ? "Failed" : "Unavailable"} unit="Region ID" reason={job.semantic_status === "failed" ? (job.semantic_metadata?.error ?? "Region segmentation did not complete.") : undefined} action={job.semantic_status === "completed" ? <Button size="sm" variant="ghost" onClick={() => onDownload("semantic_segmentation")}>Download regions</Button> : null} />}
        </section>

        <details className="mt-3 rounded-control border border-slate-200 px-3 py-2">
          <summary className="min-h-control cursor-pointer content-center text-control font-semibold text-slate-900">Calibration and diagnostics</summary>
          <div className="mt-2">
            <div className="flex items-center gap-2"><Badge tone={STATE_TONE[calibration.state]}>{calibration.label}</Badge><span className="text-supporting text-slate-500">Metric output {calibration.state === "completed" ? "available" : "unavailable"}</span></div>
            {calibration.reason && <p className="mt-2 text-supporting text-slate-600">{calibration.reason}</p>}
            <dl className="mt-3 grid grid-cols-1 gap-1 sm:grid-cols-2 lg:grid-cols-4">
              <TechnicalKeyValue label="Scale" value={metric(metadata?.scale_a)} />
              <TechnicalKeyValue label="Offset" value={metric(metadata?.offset_b)} />
              <TechnicalKeyValue label="Samples" value={metadata?.valid_samples ?? "Not reported"} />
              <TechnicalKeyValue label="Outliers" value={metadata?.outlier_samples ?? "Not reported"} />
            </dl>
            {(fit || heldout) && <div className="mt-3 grid grid-cols-1 gap-3 md:grid-cols-2">
              <section className="border-l-2 border-slate-300 pl-3"><h4 className="font-semibold text-slate-900">In-sample fit</h4><p className="text-metadata text-slate-500">Fit samples; not validation.</p><p className="mt-1 text-supporting text-slate-700">MAE {metric(fit?.all_sample_mae)} · RMSE {metric(fit?.all_sample_rmse)} · bias {metric(fit?.all_sample_bias)} · R2 {metric(fit?.in_sample_r2)}</p></section>
              <section className="border-l-2 border-sky-300 pl-3"><h4 className="font-semibold text-slate-900">Held-out validation</h4><p className="text-metadata text-slate-500">{heldout?.method ?? "Method not reported"}</p><p className="mt-1 text-supporting text-slate-700">MAE {metric(heldout?.heldout_mae)} · RMSE {metric(heldout?.heldout_rmse)} · bias {metric(heldout?.heldout_bias)} · skill {metric(heldout?.skill)}</p></section>
            </div>}
            <p className="mt-3 text-metadata text-slate-500">Residual = Predicted - Reference. Positive is higher than reference; negative is lower. Held-out residuals are the default workspace view.</p>
          </div>
        </details>

        <footer className="mt-3 flex flex-wrap items-center gap-2 border-t border-slate-100 pt-3">
          {active && <Button size="sm" variant="danger" loading={cancelling} loadingLabel="Cancelling..." onClick={onCancel}>Cancel</Button>}
          {job.status === "completed" && <><Button size="sm" onClick={onOpenWorkspace}>Open in workspace</Button><Button size="sm" variant="secondary" onClick={onOpenReports}>Generate report</Button></>}
          <span className="ml-auto text-metadata text-slate-500">Created {date(job.created_at)}{job.completed_at ? ` · Completed ${date(job.completed_at)}` : ""}</span>
        </footer>
      </div>
    </article>
  );
}

function ProductRow({ name, description, state, label, unit, reason, action }: { name: string; description: string; state: string; label: string; unit: string; reason?: string | null; action: ReactNode }) {
  return <div className="grid min-w-0 grid-cols-1 gap-2 py-2.5 sm:grid-cols-[minmax(10rem,1fr)_auto] sm:items-center"><div className="min-w-0"><div className="flex flex-wrap items-center gap-2"><h4 className="font-semibold text-slate-900">{name}</h4><Badge tone={STATE_TONE[state]}>{label}</Badge></div><p className="text-supporting text-slate-500">{description} · Unit: {unit}</p>{reason && <p className="text-metadata text-slate-500">{reason}</p>}</div>{action && <div>{action}</div>}</div>;
}
