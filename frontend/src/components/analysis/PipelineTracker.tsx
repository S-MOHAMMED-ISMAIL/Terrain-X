import type { PipelineStepView } from "./analysisPipeline";

const STATE_DOT: Record<PipelineStepView["state"], string> = {
  done: "bg-brand-500",
  current: "bg-amber-500",
  pending: "bg-slate-200",
  failed: "bg-red-500",
};

const STATE_TEXT: Record<PipelineStepView["state"], string> = {
  done: "text-slate-600",
  current: "text-slate-900 font-medium",
  pending: "text-slate-500",
  failed: "text-red-600 font-medium",
};

interface Props {
  steps: PipelineStepView[];
  /** The real, raw backend stage id (e.g. "semantic_inference") shown as a
   * tooltip on the active step — full technical detail without cluttering
   * the compact visual. */
  currentStageRaw?: string;
}

/** A compact, real-state pipeline tracker — every dot's color comes only
 * from that step's already-computed real state (see analysisPipeline.ts),
 * never a fabricated or animated-independently-of-data progress signal.
 * The "current" dot pulses (a purely decorative animation, not a second
 * source of progress information) to draw the eye to where the real job
 * actually is right now. */
export function PipelineTracker({ steps, currentStageRaw }: Props) {
  return (
    <div className="flex items-center gap-1" title={currentStageRaw}>
      {steps.map((step, index) => (
        <div key={step.id} className="flex items-center gap-1">
          <div className="flex flex-col items-center gap-1">
            <span className="relative flex h-2.5 w-2.5 items-center justify-center">
              {step.state === "current" && (
                <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-amber-400 opacity-60" />
              )}
              <span className={`relative h-2.5 w-2.5 rounded-full ${STATE_DOT[step.state]}`} />
            </span>
            <span className={`whitespace-nowrap text-metadata leading-none ${STATE_TEXT[step.state]}`}>
              {step.label}
            </span>
          </div>
          {index < steps.length - 1 && (
            <span
              className={`mb-3.5 h-0.5 w-4 rounded-full ${
                step.state === "done" ? "bg-brand-500" : "bg-slate-200"
              }`}
            />
          )}
        </div>
      ))}
    </div>
  );
}
