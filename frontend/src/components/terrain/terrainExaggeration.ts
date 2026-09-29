// Fixes the "flat mountain" problem for UNCALIBRATED relative-depth terrain
// (see docs/ARCHITECTURE.md §3.5 and the Phase-14 flat-terrain diagnosis):
// Depth Anything's relative depth is a small, unitless number (e.g. 0-6)
// while the terrain grid's own horizontal spacing is an arbitrary "1 unit
// per grid cell" (geospatial/terrain_grid.py, non-georeferenced case) that
// can span hundreds of units across the grid. These two arbitrary scales
// have no natural relationship, so a single fixed exaggeration constant
// that looks right for calibrated (metric) DSM terrain — where horizontal
// and vertical are both real meters — leaves relative-depth terrain looking
// nearly flat. This module is intentionally free of React/Three.js imports
// so both functions are directly unit-testable, mirroring
// terrainAvailability.ts's precedent.
//
// This is a PRESENTATION-ONLY multiplier layered on top of the real,
// unmodified depth values already rendered by terrainMesh.ts — it never
// converts relative depth into metric elevation, and never touches the
// stored raster or any calibration/measurement computation.

// Middle of the requested restrained 15%-25% visual-relief-ratio range.
const TARGET_RELIEF_RATIO = 0.2;

// A visual-only safety range for the AUTOMATIC baseline and the slider it
// seeds. Never lets a near-zero depth range explode the mesh into a
// spike, and never lets an unusually large depth range collapse the
// multiplier to (near) zero — both real, finite, stable outcomes.
export const MIN_AUTO_RELATIVE_EXAGGERATION = 0.1;
export const MAX_AUTO_RELATIVE_EXAGGERATION = 500;

/**
 * Computes a data-driven initial visual exaggeration for an UNCALIBRATED
 * relative-depth terrain mesh — never applied to calibrated DSM/metric
 * elevation terrain (callers gate this on `height_kind === "relative_depth"`;
 * see TerrainView3D.tsx). Targets `TARGET_RELIEF_RATIO` of the terrain's own
 * larger horizontal dimension — the same dimension `fitCamera()` in
 * TerrainView3D.tsx already frames the camera distance from — so the
 * resulting relief reads as real 3D structure without a per-image manual
 * guess, and the auto-fitting camera keeps working unchanged.
 */
export function calculateRelativeTerrainExaggeration(
  depthMin: number,
  depthMax: number,
  terrainWidth: number,
  terrainHeight: number,
): number {
  const rawRange = Math.max(depthMax - depthMin, 1e-6);
  const horizontalDimension = Math.max(terrainWidth, terrainHeight, 1e-6);
  const targetRelief = horizontalDimension * TARGET_RELIEF_RATIO;
  const exaggeration = targetRelief / rawRange;
  return Math.min(
    MAX_AUTO_RELATIVE_EXAGGERATION,
    Math.max(MIN_AUTO_RELATIVE_EXAGGERATION, exaggeration),
  );
}

/**
 * A robust (outlier-resistant) [min, max] range from a real elevation/depth
 * grid, for VISUAL SCALING ONLY. A handful of near-zero sky pixels or a
 * single bright foreground spike (real diagnosis: raw min/max included a
 * small outlier tail beyond the P1/P5 values) should not by themselves
 * dominate the auto-computed exaggeration. Two consumers, both
 * presentation-only: (1) sizing the initial exaggeration multiplier
 * (unchanged); (2) for an uncalibrated relative-depth grid specifically,
 * terrainMesh.ts also uses this same window as the normalization range fed
 * into applyRelativeTerrainGamma — a handful of extreme cells beyond
 * [P5, P95] render clamped to the window's own boundary height rather than
 * stretching the visual scale to accommodate them, the same robustness
 * rationale already applied to the exaggeration multiplier. Calibrated
 * DSM/metric elevation never uses this — terrainMesh.ts still builds that
 * geometry from every real, unclipped raw min/max value.
 */
export function robustDepthRange(
  elevations: Float32Array,
  lowerPercentile = 5,
  upperPercentile = 95,
): { min: number; max: number } {
  const finite: number[] = [];
  for (let i = 0; i < elevations.length; i++) {
    const v = elevations[i];
    if (Number.isFinite(v)) finite.push(v);
  }
  if (finite.length === 0) return { min: 0, max: 0 };
  finite.sort((a, b) => a - b);
  const pick = (p: number) => {
    const idx = Math.min(
      finite.length - 1,
      Math.max(0, Math.round((p / 100) * (finite.length - 1))),
    );
    return finite[idx];
  };
  return { min: pick(lowerPercentile), max: pick(upperPercentile) };
}

// Starting point for the relative-depth visualization relief curve (see
// applyRelativeTerrainGamma below) — proven against a real hazy aerial
// photo (docs/ARCHITECTURE.md's gamma-transform section): a close
// foreground peak occupies most of the P5-P95 depth range, visually
// flattening the much smaller share of that range real distant terrain
// occupies under a linear mapping. gamma=0.5 (a square-root curve)
// noticeably restored legible distant relief without over-flattening the
// foreground. Not yet user-configurable — see terrainMesh.ts's docstring.
export const DEFAULT_RELATIVE_TERRAIN_GAMMA = 0.5;

/**
 * Monotonic (order-preserving), power-curve remap of an already-normalized
 * [0,1] relative-depth value, for VISUALIZATION ONLY — never applied to
 * calibrated DSM/metric elevation (callers gate this on
 * `height_kind === "relative_depth"`; see terrainMesh.ts). A close
 * foreground feature can occupy most of a relative-depth grid's real P5-P95
 * range, which — under a plain LINEAR height mapping — visually flattens
 * real (if smaller-magnitude) distant terrain relief into near-invisibility.
 * `gamma < 1` expands the low end of the normalized range and compresses
 * the high end, without moving any value's real rank relative to any other
 * (for any gamma > 0, `x -> x^gamma` is strictly increasing on [0,1], so
 * `a < b` on input always still gives `f(a) <= f(b)` on output) — no image
 * row/column mapping changes, no real depth value changes, and every
 * near/far ordering relationship the raw data expresses is preserved
 * exactly, just visually redistributed. `gamma === 1` is the identity (the
 * plain linear mapping). Out-of-range input is clamped to [0,1] BEFORE the
 * power curve (matching a "valid normalized depth" precondition); NaN/
 * Infinity pass through unchanged rather than producing a fabricated
 * number, since an invalid depth value must never silently become a real-
 * looking finite height.
 */
export function applyRelativeTerrainGamma(normalizedDepth: number, gamma: number): number {
  if (!(gamma > 0)) {
    throw new Error(`applyRelativeTerrainGamma: gamma must be a positive number, got ${gamma}`);
  }
  if (!Number.isFinite(normalizedDepth)) return normalizedDepth;
  const clamped = Math.min(1, Math.max(0, normalizedDepth));
  return Math.pow(clamped, gamma);
}

/**
 * The slider range shown to the user for relative-depth exaggeration —
 * "sensible... based on the actual auto-calculated baseline" rather than
 * calibrated terrain's fixed 0.5-5x (which would leave relative-depth
 * terrain unable to reach a visually meaningful relief). Centered on the
 * real auto-computed baseline so the default sits mid-slider, while still
 * letting the user reach a much stronger or much flatter look when needed.
 */
export function relativeExaggerationSliderRange(
  baseline: number,
): { min: number; max: number; step: number } {
  const min = Math.max(MIN_AUTO_RELATIVE_EXAGGERATION, baseline / 10);
  const max = Math.min(MAX_AUTO_RELATIVE_EXAGGERATION, baseline * 10);
  const step = Math.max((max - min) / 100, 0.01);
  return { min, max, step };
}
