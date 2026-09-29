import type {
  CalibrationResidualsContext,
  ResidualFeature,
  ResidualFeatureProperties,
  ResidualKind,
} from "@/api/types";
import type { MeasurementMode } from "./measurementMode";

// P1-5: pure presentation logic for calibration residuals at calibration
// sample locations — kept free of Leaflet/React imports so it is unit-
// testable (same pattern as inspection.ts / measurementFormat.ts). Nothing
// here recomputes a residual: every value shown is the stored one.

export const RESIDUAL_KIND_LABELS: Record<ResidualKind, string> = {
  heldout: "Held-out residual (validation view)",
  fit: "In-sample fit residual — not validation",
};

export const RESIDUAL_POSITIVE_MEANING = "Calibrated surface higher than the reference";
export const RESIDUAL_NEGATIVE_MEANING = "Calibrated surface lower than the reference";
export const RESIDUAL_UNITS_TEXT = "units of the calibration reference";
export const RESIDUAL_OUTLIER_TEXT = "Black ring: excluded from the production fit (σ-clipping)";

export function residualValue(p: ResidualFeatureProperties, kind: ResidualKind): number {
  return kind === "heldout" ? p.residual_heldout : p.residual_fit;
}

export function predictedValue(p: ResidualFeatureProperties, kind: ResidualKind): number {
  return kind === "heldout" ? p.predicted_heldout : p.predicted_fit;
}

/** Display-only colour scaling: symmetric about zero at the largest
 * |residual| of the kind shown. Not a threshold and not a quality judgement. */
export function symmetricScaleMax(features: ResidualFeature[], kind: ResidualKind): number {
  let max = 0;
  for (const f of features) {
    const v = Math.abs(residualValue(f.properties, kind));
    if (Number.isFinite(v) && v > max) max = v;
  }
  return max;
}

const NEGATIVE_RGB: [number, number, number] = [33, 102, 172]; // blue: lower
const ZERO_RGB: [number, number, number] = [247, 247, 247]; // near-white: ~0
const POSITIVE_RGB: [number, number, number] = [178, 24, 43]; // red: higher

function mix(a: [number, number, number], b: [number, number, number], t: number): string {
  const c = a.map((v, i) => Math.round(v + (b[i] - v) * t));
  return `rgb(${c[0]}, ${c[1]}, ${c[2]})`;
}

/** Diverging colour centred on 0: red where the calibrated surface is
 * higher than the reference, blue where lower. */
export function residualColor(value: number, scaleMax: number): string {
  if (!Number.isFinite(value) || !(scaleMax > 0)) return mix(ZERO_RGB, ZERO_RGB, 0);
  const t = Math.max(-1, Math.min(1, value / scaleMax));
  return t >= 0 ? mix(ZERO_RGB, POSITIVE_RGB, t) : mix(ZERO_RGB, NEGATIVE_RGB, -t);
}

export const RESIDUAL_LEGEND_GRADIENT =
  `linear-gradient(to right, rgb(${NEGATIVE_RGB.join(",")}), ` +
  `rgb(${ZERO_RGB.join(",")}), rgb(${POSITIVE_RGB.join(",")}))`;

/** Signed, fixed 4-decimal display of a stored value (never rounded before
 * storage — this is display only). */
export function formatResidual(value: number): string {
  const text = value.toFixed(4);
  return value > 0 ? `+${text}` : text;
}

export function formatElevation(value: number): string {
  return value.toFixed(4);
}

/** The exact lines shown when a residual marker is clicked. */
export function residualTooltipLines(p: ResidualFeatureProperties, kind: ResidualKind): string[] {
  const kindLabel = kind === "heldout" ? "Held-out" : "Fit (in-sample)";
  const lines = [
    `${kindLabel} residual: ${formatResidual(residualValue(p, kind))} (${RESIDUAL_UNITS_TEXT})`,
    `${kindLabel} prediction: ${formatElevation(predictedValue(p, kind))}`,
    `Reference elevation: ${formatElevation(p.reference_elevation)}`,
    `Source pixel (row, col): ${p.row}, ${p.col}`,
    p.gcp_index !== null && p.gcp_index !== undefined
      ? `GCP #${p.gcp_index} (held out on its own)`
      : `Spatial block ${p.block_id ?? p.fold_id} (held out as a block)`,
  ];
  if (!p.inlier_in_production_fit) lines.push("Excluded from the production fit (outlier)");
  return lines;
}

/** Markers only take clicks in plain inspection mode, so they never swallow
 * a measurement click on the map. */
export function residualMarkersInteractive(mode: MeasurementMode): boolean {
  return mode === "off";
}

export function residualsUnavailableReason(ctx: CalibrationResidualsContext): string | null {
  if (ctx.available) return null;
  return ctx.unavailable_reason ?? "Calibration residuals are not available for this dataset.";
}
