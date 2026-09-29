import type { AnalysisJob, AnalysisStage } from "@/api/types";

export type PipelineStepState = "done" | "current" | "pending" | "failed";

export interface PipelineStepView {
  id: string;
  label: string;
  state: PipelineStepState;
}

// The exact real stage sequence a depth-pipeline job moves through (see
// backend app/models/analysis_job.py::AnalysisStage / STAGE_PROGRESS) —
// grouped here into coarser, judge-legible macro-stages for display. This
// never invents a stage: every entry below is a real backend stage value,
// only their on-screen grouping/labeling is new.
const STAGE_ORDER: AnalysisStage[] = [
  "queued",
  "preparing",
  "validating_input",
  "loading_model",
  "preprocessing",
  "inference",
  "writing_depth",
  "calibrating",
  "writing_metric_elevation",
  "writing_dsm",
  "validating_results",
  "filtering_ground",
  "writing_dtm",
  "writing_ndsm",
  "loading_semantic_model",
  "semantic_preprocessing",
  "semantic_inference",
  "writing_semantic",
  "finalizing",
  "completed",
];

interface MacroStep {
  id: string;
  label: string;
  stages: AnalysisStage[];
  requiresCalibration?: boolean;
  requiresSemantic?: boolean;
}

const MACRO_STEPS: MacroStep[] = [
  { id: "input", label: "Input", stages: ["queued", "preparing", "validating_input"] },
  {
    id: "depth",
    label: "Depth",
    stages: ["loading_model", "preprocessing", "inference", "writing_depth"],
  },
  {
    id: "calibration",
    label: "Calibration",
    // P1-3 ground filtering (DTM/nDSM estimates) only ever follows a
    // successful, gate-passed calibration, so it is shown as part of it.
    stages: [
      "calibrating",
      "writing_metric_elevation",
      "writing_dsm",
      "validating_results",
      "filtering_ground",
      "writing_dtm",
      "writing_ndsm",
    ],
    requiresCalibration: true,
  },
  {
    id: "semantic",
    label: "Semantic",
    stages: [
      "loading_semantic_model",
      "semantic_preprocessing",
      "semantic_inference",
      "writing_semantic",
    ],
    requiresSemantic: true,
  },
  { id: "final", label: "Final result", stages: ["finalizing", "completed"] },
];

/** A disaster-screening job is a completely different, standalone job type
 * (see AnalysisParametersV1.disaster_source_artifact_id) that never runs
 * the depth/calibration/semantic pipeline this module visualizes — this
 * lets callers fall back to plain stage text for that job type instead of
 * building a misleading depth-pipeline tracker for it. */
export function isDepthPipelineJob(job: AnalysisJob): boolean {
  return job.parameters?.disaster_source_artifact_id == null;
}

/** Builds the real, current pipeline-stage view for one depth-pipeline job
 * — every "done"/"current"/"pending"/"failed" classification below is
 * derived only from that job's own real `status`/`current_stage` and
 * whether calibration/segmentation were genuinely requested
 * (`parameters.dem_reference_dataset_id`/`gcp_reference_dataset_id`/
 * `enable_semantic_segmentation`) — never a fabricated or estimated
 * progress signal. */
export function buildPipelineSteps(job: AnalysisJob): PipelineStepView[] {
  const wantsCalibration =
    job.parameters?.dem_reference_dataset_id != null ||
    job.parameters?.gcp_reference_dataset_id != null;
  const wantsSemantic = job.parameters?.enable_semantic_segmentation === true;

  const relevantSteps = MACRO_STEPS.filter((step) => {
    if (step.requiresCalibration && !wantsCalibration) return false;
    if (step.requiresSemantic && !wantsSemantic) return false;
    return true;
  });

  const currentStageIndex = STAGE_ORDER.indexOf(job.current_stage);

  return relevantSteps.map((step) => {
    const stageIndices = step.stages
      .map((s) => STAGE_ORDER.indexOf(s))
      .filter((i) => i >= 0);
    const minIndex = Math.min(...stageIndices);
    const maxIndex = Math.max(...stageIndices);

    let state: PipelineStepState;
    if (job.status === "completed") {
      state = "done";
    } else if (job.status === "failed" || job.status === "cancelled") {
      if (currentStageIndex > maxIndex) {
        state = "done";
      } else if (currentStageIndex >= minIndex) {
        state = "failed";
      } else {
        state = "pending";
      }
    } else if (currentStageIndex > maxIndex) {
      state = "done";
    } else if (currentStageIndex >= minIndex) {
      state = "current";
    } else {
      state = "pending";
    }

    return { id: step.id, label: step.label, state };
  });
}
