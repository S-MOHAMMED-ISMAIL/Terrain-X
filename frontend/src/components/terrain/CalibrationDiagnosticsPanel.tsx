import type { AnalysisJob } from "@/api/types";
import { DataState, Notice, TechnicalKeyValue } from "@/components/ui";
import type { WorkspaceQualityPresentation } from "./workspacePresentation";

interface Props {
  job: AnalysisJob | null;
  quality: WorkspaceQualityPresentation;
}

function metric(value: unknown): string {
  return typeof value === "number" && Number.isFinite(value) ? value.toFixed(4) : "Not reported";
}

export function CalibrationDiagnosticsPanel({ job, quality }: Props) {
  const metadata = job?.calibration_metadata;
  const fit = metadata?.fit_diagnostics;
  const cv = metadata?.cross_validation;
  const calibrationState = quality.calibration.state === "passed"
    ? "available"
    : quality.calibration.state === "processing"
      ? "processing"
      : quality.calibration.state === "failed" || quality.calibration.state === "rejected"
        ? "failed"
        : "unavailable";

  return (
    <section aria-labelledby="calibration-diagnostics-title" className="min-w-0">
      <div className="flex min-w-0 flex-wrap items-center justify-between gap-2">
        <h3 id="calibration-diagnostics-title" className="text-panel-title text-slate-950">
          Calibration diagnostics
        </h3>
        <DataState state={calibrationState} detail={quality.calibration.label} />
      </div>

      <p className="mt-2 break-words text-supporting text-slate-600">{quality.calibration.detail}</p>

      {quality.mode === "relative" && (
        <Notice tone="warning" title="Relative output remains available" className="mt-3">
          Relative depth is usable as a unitless visualization. It is not metric elevation.
        </Notice>
      )}

      {metadata?.error && quality.calibration.state !== "passed" && (
        <div className="calibration-rejection mt-3">
          <div className="calibration-rejection-title">
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
              <circle cx="8" cy="8" r="7" stroke="currentColor" strokeWidth="1.5"/>
              <path d="M8 5v3M8 10.5v.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
            </svg>
            Calibration rejected
          </div>
          <p className="calibration-rejection-reason">
            Metric calibration was rejected by the scientific quality gate. Relative terrain remains available.
          </p>
          <details className="mt-2">
            <summary className="cursor-pointer text-metadata font-medium text-slate-600 hover:text-slate-800">
              Technical details
            </summary>
            <p className="mt-1 break-words text-metadata leading-4 text-slate-500">{metadata.error}</p>
          </details>
        </div>
      )}

      <dl className="mt-3 grid min-w-0 grid-cols-1 gap-x-5 gap-y-1.5 xl:grid-cols-2">
        <TechnicalKeyValue label="Reference" value={metadata?.reference_type?.toUpperCase() ?? "Not reported"} />
        <TechnicalKeyValue label="Vertical unit" value={quality.verticalUnit.label} />
        <TechnicalKeyValue label="Source CRS" value={metadata?.source_crs ?? "Not reported"} />
        <TechnicalKeyValue label="Reference CRS" value={metadata?.reference_crs ?? "Not reported"} />
      </dl>

      {(fit || cv) && (
        <div className="mt-4 grid min-w-0 grid-cols-1 gap-4 xl:grid-cols-2">
          <section aria-labelledby="in-sample-title" className="min-w-0 border-l-2 border-slate-300 pl-3">
            <h4 id="in-sample-title" className="text-control font-semibold text-slate-900">In-sample fit</h4>
            <p className="text-metadata text-slate-500">Fit diagnostics over the calibration samples; not validation.</p>
            <dl className="mt-2 space-y-1">
              <TechnicalKeyValue label="Samples" value={fit?.valid_sample_count ?? "Not reported"} />
              <TechnicalKeyValue label="MAE" value={metric(fit?.all_sample_mae)} />
              <TechnicalKeyValue label="RMSE" value={metric(fit?.all_sample_rmse)} />
              <TechnicalKeyValue label="Bias" value={metric(fit?.all_sample_bias)} />
              <TechnicalKeyValue label="R2" value={metric(fit?.in_sample_r2)} />
            </dl>
          </section>

          <section aria-labelledby="held-out-title" className="min-w-0 border-l-2 border-sky-300 pl-3">
            <h4 id="held-out-title" className="text-control font-semibold text-slate-900">Held-out validation</h4>
            <p className="text-metadata text-slate-500">
              {cv?.method ?? "Cross-validation method not reported"}
            </p>
            <dl className="mt-2 space-y-1">
              <TechnicalKeyValue label="Samples" value={cv?.heldout_sample_count ?? "Not reported"} />
              <TechnicalKeyValue label="MAE" value={metric(cv?.heldout_mae)} />
              <TechnicalKeyValue label="RMSE" value={metric(cv?.heldout_rmse)} />
              <TechnicalKeyValue label="Bias" value={metric(cv?.heldout_bias)} />
              <TechnicalKeyValue label="Skill" value={metric(cv?.skill)} />
            </dl>
          </section>
        </div>
      )}

      {metadata?.limitations && (
        <p className="mt-3 break-words text-metadata leading-4 text-slate-600">{metadata.limitations}</p>
      )}
    </section>
  );
}
