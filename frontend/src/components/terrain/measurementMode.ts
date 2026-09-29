/**
 * Pure measurement-mode/point-buffer logic for the Phase 7 measurement
 * tools — kept in its own module with no React/Leaflet/Three imports (the
 * same reason `inspection.ts` exists standalone) so it can be unit-tested
 * directly under Vitest's plain default (Node) environment.
 */

export type MeasurementMode = "off" | "point" | "distance" | "profile" | "coordinate" | "slope";

// How many real clicked points each mode needs before it computes a result.
export const POINTS_NEEDED: Record<MeasurementMode, number> = {
  off: 0,
  point: 1,
  distance: 2,
  profile: 2,
  coordinate: 1,
  slope: 1,
};

export const MEASUREMENT_MODE_LABELS: Record<MeasurementMode, string> = {
  off: "Inspect (off)",
  point: "Point elevation",
  distance: "Distance",
  profile: "Profile",
  coordinate: "Coordinate",
  slope: "Slope at point",
};

export interface MeasurementPoint {
  // Source-grid pixel — set for a 3D or non-georeferenced 2D click. P1-6: a
  // georeferenced 2D click carries only its map coordinate (lat/lng); the
  // backend resolves it against the measured layer's own raster.
  row?: number;
  col?: number;
  // The real clicked map coordinate (WGS84) when the click came from the
  // georeferenced 2D map. Slope-at-point sends this to the backend, which
  // resolves it against the slope raster's own grid.
  lat?: number;
  lng?: number;
}

/**
 * Appends a real clicked point to the buffer, keeping only the most recent
 * `POINTS_NEEDED[mode]` points — so clicking a 3rd point in "distance" mode
 * (which needs 2) replaces the oldest, letting a user simply re-click to
 * redo a measurement without an explicit "reset" action.
 */
export function addMeasurementPoint(
  mode: MeasurementMode,
  points: MeasurementPoint[],
  point: MeasurementPoint,
): MeasurementPoint[] {
  const needed = POINTS_NEEDED[mode];
  if (needed === 0) return [];
  return [...points, point].slice(-needed);
}

export function isMeasurementReady(mode: MeasurementMode, points: MeasurementPoint[]): boolean {
  return points.length >= POINTS_NEEDED[mode];
}

/** How many more points a user still needs to click before this mode has
 * enough to compute a result — 0 once ready, never negative. */
export function pointsRemaining(mode: MeasurementMode, points: MeasurementPoint[]): number {
  return Math.max(0, POINTS_NEEDED[mode] - points.length);
}
