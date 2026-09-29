import type {
  CalibrationResidualsContext,
  CalibrationResidualsResult,
  ResidualKind,
} from "@/api/types";
import { Button } from "@/components/ui";
import {
  formatResidual,
  RESIDUAL_KIND_LABELS,
  RESIDUAL_LEGEND_GRADIENT,
  RESIDUAL_NEGATIVE_MEANING,
  RESIDUAL_OUTLIER_TEXT,
  RESIDUAL_POSITIVE_MEANING,
  RESIDUAL_UNITS_TEXT,
  residualsUnavailableReason,
  symmetricScaleMax,
} from "./calibrationResiduals";

interface Props {
  context: CalibrationResidualsContext;
  data: CalibrationResidualsResult | null;
  loading: boolean;
  error: string | null;
  visible: boolean;
  onToggleVisible: () => void;
  kind: ResidualKind;
  onKindChange: (kind: ResidualKind) => void;
  onDownload: () => void;
  is2d: boolean;
}

/** P1-5: calibration residuals at calibration sample locations, styled like
 * a LayerPanel row. Points only — never a surface — and the held-out view
 * is the default. */
export function CalibrationResidualsCard({
  context,
  data,
  loading,
  error,
  visible,
  onToggleVisible,
  kind,
  onKindChange,
  onDownload,
  is2d,
}: Props) {
  const unavailable = residualsUnavailableReason(context);
  const features = data?.feature_collection.features ?? [];
  const scaleMax = symmetricScaleMax(features, kind);

  return (
    <div
      data-testid="calibration-residuals-card"
      className={`rounded-panel border border-slate-200 bg-white p-3 ${
        unavailable ? "opacity-60" : ""
      }`}
    >
      <div className="flex items-center justify-between gap-2">
        <span className={`text-sm font-medium ${unavailable ? "text-slate-500" : "text-slate-900"}`}>
          Calibration residuals
        </span>
        {!unavailable && (
          <label className="flex items-center gap-1 text-xs text-slate-500">
            <input
              type="checkbox"
              aria-label="Show calibration residuals"
              checked={visible}
              onChange={onToggleVisible}
            />
            Visible
          </label>
        )}
      </div>

      {unavailable ? (
        <p className="mt-1 text-xs text-slate-500">{unavailable}</p>
      ) : (
        <div className="mt-2 flex flex-col gap-2 text-xs text-slate-600">
          <div className="flex gap-1" role="radiogroup" aria-label="Residual kind">
            {(["heldout", "fit"] as ResidualKind[]).map((k) => (
              <button
                key={k}
                type="button"
                role="radio"
                aria-checked={kind === k}
                onClick={() => onKindChange(k)}
                className={`min-h-control flex-1 rounded-control px-2 py-1 text-metadata font-medium transition-colors duration-selection ${
                  kind === k ? "bg-slate-900 text-white" : "bg-slate-100 text-slate-600 hover:bg-slate-200"
                }`}
              >
                {k === "heldout" ? "Held-out" : "Fit (in-sample)"}
              </button>
            ))}
          </div>
          <p data-testid="residual-kind-label" className="font-medium text-slate-700">
            {RESIDUAL_KIND_LABELS[kind]}
          </p>

          {loading && <p role="status" className="text-slate-500">Loading residuals…</p>}
          {error && <p role="alert" className="text-red-700">{error}</p>}

          {data && (
            <>
              <div>
                <div className="h-2 w-full rounded-full" style={{ background: RESIDUAL_LEGEND_GRADIENT }} />
                <div
                  data-testid="residual-legend-values"
                  className="mt-1 flex justify-between text-metadata text-slate-500"
                >
                  <span>{formatResidual(-scaleMax)}</span>
                  <span>0</span>
                  <span>{formatResidual(scaleMax)}</span>
                </div>
                <p className="mt-1 text-metadata text-slate-500">
                  residual = predicted − reference, in {RESIDUAL_UNITS_TEXT}. Red: {RESIDUAL_POSITIVE_MEANING.toLowerCase()}. Blue: {RESIDUAL_NEGATIVE_MEANING.toLowerCase()}. Colour range is display scaling only, not a threshold.
                </p>
                <p className="text-metadata text-slate-500">{RESIDUAL_OUTLIER_TEXT}</p>
              </div>
              <p data-testid="residual-sample-count" className="text-metadata text-slate-500">
                {features.length} of {data.summary.total_candidate_samples} candidate samples valid ·{" "}
                {data.summary.reference_type === "dem"
                  ? "DEM, held out one spatial block at a time"
                  : "GCP, each point held out on its own"}
              </p>
              {!is2d && (
                <p className="text-metadata text-amber-700">Residual points are shown on the 2D map only.</p>
              )}
              <p className="text-metadata leading-snug text-amber-700">{data.summary.disclaimer}</p>
              <Button
                size="sm"
                variant="secondary"
                onClick={onDownload}
                className="self-start"
              >
                Download residuals (GeoJSON)
              </Button>
            </>
          )}
        </div>
      )}
    </div>
  );
}
