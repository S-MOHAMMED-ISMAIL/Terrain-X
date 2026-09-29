import type { AnalysisJob } from "@/api/types";
import { DataState, TechnicalKeyValue } from "@/components/ui";
import type { WorkspaceQualityPresentation } from "./workspacePresentation";

interface Props {
  job: AnalysisJob | null;
  quality: WorkspaceQualityPresentation;
}

export function DataQualityDetails({ job, quality }: Props) {
  const state = quality.calibration.state === "passed"
    ? "available"
    : quality.calibration.state === "processing"
      ? "processing"
      : quality.calibration.state === "failed" || quality.calibration.state === "rejected"
        ? "failed"
        : "unavailable";

  return (
    <div className="grid min-w-0 grid-cols-1 gap-3 pb-1 md:grid-cols-2">
      <div className="min-w-0">
        <DataState state={state} detail={`Calibration ${quality.calibration.label.toLowerCase()}`} />
        <p className="mt-1 break-words text-metadata leading-4">{quality.calibration.detail}</p>
      </div>
      <dl className="min-w-0 space-y-1">
        <TechnicalKeyValue label="Active layer" value={quality.activeLayer?.display_name ?? "Unavailable"} />
        <TechnicalKeyValue label="Vertical unit" value={quality.verticalUnit.label} />
        <TechnicalKeyValue label="Analysis job" value={job?.id ?? "Not available"} />
      </dl>
    </div>
  );
}
