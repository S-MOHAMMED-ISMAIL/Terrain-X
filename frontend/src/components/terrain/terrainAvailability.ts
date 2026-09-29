import type { TerrainContext } from "@/api/types";

// Phase 10: real, pure classification of the backend's own TerrainContext —
// never re-derives height_kind from anything else (e.g. layer visibility,
// calibration status guesses); just reads what the backend already
// computed. Kept in its own module (no React/Three.js imports) so it's
// directly unit-testable, mirroring inspection.ts's/reportFormat.ts's
// precedent for exactly this reason.

// Shown persistently whenever the active 3D terrain is backed by
// relative_depth rather than a calibrated DSM — the exact required wording
// plus a short reason, never omitted, never shown for the calibrated case.
export const RELATIVE_TERRAIN_NOTICE =
  "Relative terrain preview — NOT elevation, NOT a DSM. Heights are derived " +
  "from unitless relative depth (uncalibrated), not a calibrated or " +
  "georeferenced elevation model.";

export function isRelativeTerrain(terrain: TerrainContext): boolean {
  return terrain.available && terrain.height_kind === "relative_depth";
}

export function isElevationTerrain(terrain: TerrainContext): boolean {
  return terrain.available && terrain.height_kind === "elevation";
}

/** D2: whether the RGB texture may be draped on this terrain — exactly the
 * backend's provenance-based decision (the same rule as the GLB export),
 * never re-derived here from dimensions or bounds. */
export function textureAvailable(terrain: TerrainContext): boolean {
  return terrain.available && terrain.texture_compatible === true;
}

/** Real, honest label for the "Show RGB texture" control — never calls a
 * relative-depth-backed texture an orthomosaic/orthophoto/georeferenced
 * texture, since none of those claims are supported for it. */
export function textureLabel(terrain: TerrainContext): string {
  return isRelativeTerrain(terrain) ? "RGB texture on relative terrain" : "Show RGB texture";
}
