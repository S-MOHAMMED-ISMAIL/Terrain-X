import type { HeightKind } from "@/api/types";

// P1-7: terrain-aware first-person flythrough — pure logic, no Three.js or
// React, so it is unit-testable (same pattern as inspection.ts).
//
// The flight surface IS the rendered surface. `buildTerrainGeometry`
// (terrainMesh.ts) places vertex (row, col) at mesh-local
// ((col + 0.5) * cellX, worldY, (row + 0.5) * cellY) — the centre of grid
// cell (row, col) (D1) — emits a quad's two triangles only when
// all four corners are finite, and splits every quad along the
// (row, col+1)-(row+1, col) diagonal. `flightTerrainFromMesh` takes world Y
// straight from that position buffer and interpolates on exactly those
// triangles — no second height formula, and exaggeration / relative-depth
// gamma are whatever the mesh already applied.
//
// Everything here is a DISPLAY-GRID value (the backend's <=256 px,
// block-averaged terrain grid). It drives flight and the HUD only; any
// authoritative value still comes from the full-resolution raster through
// the inspection/measurement tools.

/** Flight speed limits relative to the default speed. */
export const SPEED_STEP = 1.5;
export const SPEED_RANGE = 32;
/** Horizontal flight is limited to the footprint plus this fraction of its
 * extent on every side. */
export const FOOTPRINT_MARGIN = 0.1;
/** DSM: minimum clearance in the reference's own units (exaggeration is
 * applied to turn it into world units). */
export const DSM_MIN_CLEARANCE = 2;
/** Relative depth has no physical scale: minimum clearance is this fraction
 * of the mesh's larger horizontal extent, in visual (world) units. */
export const RELATIVE_MIN_CLEARANCE_FRACTION = 0.01;

export interface FlightTerrain {
  width: number;
  height: number;
  cellX: number;
  cellY: number;
  // The mesh is drawn at position (-halfWidth, 0, -halfHeight) (see
  // TerrainView3D.tsx), so world = mesh-local - half.
  halfWidth: number;
  halfHeight: number;
  /** World Y of every vertex, exactly as rendered. */
  worldY: Float32Array;
  /** The raw display-grid value of every vertex (NaN where invalid). */
  raw: Float32Array;
  /** 1 where the raw value is finite (the mesh's own validity rule). */
  valid: Uint8Array;
}

export interface Vec3 {
  x: number;
  y: number;
  z: number;
}

/** Builds the flight surface from the rendered mesh's own position buffer
 * (x, y, z per vertex) and the raw grid it was built from. */
export function flightTerrainFromMesh(
  positions: ArrayLike<number>,
  raw: Float32Array,
  grid: { width: number; height: number; cellX: number; cellY: number },
  halfWidth: number,
  halfHeight: number,
): FlightTerrain {
  const n = grid.width * grid.height;
  if (raw.length !== n || positions.length !== n * 3) {
    throw new Error("Flight terrain: grid, raw values and mesh positions do not match.");
  }
  const worldY = new Float32Array(n);
  const valid = new Uint8Array(n);
  for (let i = 0; i < n; i++) {
    worldY[i] = positions[i * 3 + 1];
    valid[i] = Number.isFinite(raw[i]) ? 1 : 0;
  }
  return { ...grid, halfWidth, halfHeight, worldY, raw, valid };
}

/** World (x, z) -> fractional grid (col, row); integer (col, row) is the
 * vertex at the centre of that grid cell. */
export function worldToGrid(t: FlightTerrain, x: number, z: number): { col: number; row: number } {
  return { col: (x + t.halfWidth) / t.cellX - 0.5, row: (z + t.halfHeight) / t.cellY - 0.5 };
}

export function gridToWorld(t: FlightTerrain, col: number, row: number): { x: number; z: number } {
  return { x: (col + 0.5) * t.cellX - t.halfWidth, z: (row + 0.5) * t.cellY - t.halfHeight };
}

/** Interpolates `values` at a fractional (col, row) on the mesh's own
 * triangles, or null where the mesh has no surface (a quad with any
 * invalid corner, or outside the grid). */
function interpolateOnMesh(
  t: FlightTerrain,
  values: Float32Array,
  col: number,
  row: number,
): number | null {
  const eps = 1e-9;
  if (!(col >= -eps && row >= -eps && col <= t.width - 1 + eps && row <= t.height - 1 + eps)) {
    return null;
  }
  if (t.width < 2 || t.height < 2) return null;
  const c0 = Math.min(Math.max(Math.floor(col), 0), t.width - 2);
  const r0 = Math.min(Math.max(Math.floor(row), 0), t.height - 2);
  const a = r0 * t.width + c0; // (r0, c0)
  const b = a + 1; // (r0, c0+1)
  const c = a + t.width; // (r0+1, c0)
  const d = c + 1; // (r0+1, c0+1)
  if (!t.valid[a] || !t.valid[b] || !t.valid[c] || !t.valid[d]) return null;
  const u = col - c0;
  const v = row - r0;
  // Triangles (a, c, b) and (b, c, d), split along b-c (u + v = 1).
  if (u + v <= 1) {
    return values[a] + u * (values[b] - values[a]) + v * (values[c] - values[a]);
  }
  return values[d] + (1 - u) * (values[c] - values[d]) + (1 - v) * (values[b] - values[d]);
}

/** World Y of the rendered surface at world (x, z), or null (no surface). */
export function groundHeightAt(t: FlightTerrain, x: number, z: number): number | null {
  const { col, row } = worldToGrid(t, x, z);
  return interpolateOnMesh(t, t.worldY, col, row);
}

/** The raw display-grid value under world (x, z), interpolated on the same
 * triangles, or null (no surface). */
export function groundValueAt(t: FlightTerrain, x: number, z: number): number | null {
  const { col, row } = worldToGrid(t, x, z);
  return interpolateOnMesh(t, t.raw, col, row);
}

export interface Bounds2D {
  minX: number;
  maxX: number;
  minZ: number;
  maxZ: number;
}

/** The mesh's vertex footprint in world (x, z). */
export function footprint(t: FlightTerrain): Bounds2D {
  const a = gridToWorld(t, 0, 0);
  const b = gridToWorld(t, t.width - 1, t.height - 1);
  return {
    minX: Math.min(a.x, b.x),
    maxX: Math.max(a.x, b.x),
    minZ: Math.min(a.z, b.z),
    maxZ: Math.max(a.z, b.z),
  };
}

/** Footprint plus FOOTPRINT_MARGIN of its extent on every side. */
export function flightBounds(t: FlightTerrain): Bounds2D {
  const f = footprint(t);
  const mx = (f.maxX - f.minX) * FOOTPRINT_MARGIN;
  const mz = (f.maxZ - f.minZ) * FOOTPRINT_MARGIN;
  return { minX: f.minX - mx, maxX: f.maxX + mx, minZ: f.minZ - mz, maxZ: f.maxZ + mz };
}

const clamp = (v: number, lo: number, hi: number) => Math.min(Math.max(v, lo), hi);

export interface FlightParams {
  /** Horizontal and vertical speed, world units per second. */
  speed: number;
  /** Minimum world-unit clearance above the rendered surface. */
  minClearance: number;
  /** Terrain-follow: hold exactly `followClearance` above the surface. */
  follow: boolean;
  followClearance: number;
}

export interface FlightInput {
  forward: number; // -1..1 (W/S)
  right: number; // -1..1 (D/A)
  up: number; // -1..1 (Space/Shift)
}

/** Unit horizontal forward direction (world x, z). */
export interface Heading {
  fx: number;
  fz: number;
}

/** Parameters t in (0, 1) where the segment crosses a mesh triangle edge:
 * a grid column, a grid row, or a quad diagonal (col + row = integer). */
export function triangleEdgeCrossings(
  t: FlightTerrain,
  x0: number,
  z0: number,
  x1: number,
  z1: number,
): number[] {
  const g0 = worldToGrid(t, x0, z0);
  const g1 = worldToGrid(t, x1, z1);
  const out: number[] = [];
  const lines: [number, number][] = [
    [g0.col, g1.col],
    [g0.row, g1.row],
    [g0.col + g0.row, g1.col + g1.row],
  ];
  for (const [f0, f1] of lines) {
    if (f0 === f1) continue;
    const lo = Math.min(f0, f1);
    const hi = Math.max(f0, f1);
    for (let k = Math.ceil(lo); k <= Math.floor(hi); k++) {
      const s = (k - f0) / (f1 - f0);
      if (s > 0 && s < 1) out.push(s);
    }
  }
  return out.sort((p, q) => p - q);
}

/** Highest rendered surface at a path point, looking a hair either side so
 * a point exactly on an edge next to a no-surface quad still sees the
 * surface that edge belongs to. */
function surfaceAtPathPoint(
  t: FlightTerrain,
  x0: number,
  z0: number,
  dx: number,
  dz: number,
  s: number,
): number | null {
  let best: number | null = null;
  for (const ds of [0, -1e-9, 1e-9]) {
    const q = clamp(s + ds, 0, 1);
    const g = groundHeightAt(t, x0 + dx * q, z0 + dz * q);
    if (g !== null && (best === null || g > best)) best = g;
  }
  return best;
}

/**
 * One flight step. The camera moves along the straight horizontal segment
 * from its current position to the (bounds-clamped) target, and its height
 * is constrained at EVERY point where that segment crosses a mesh triangle
 * edge, plus both ends. Between consecutive crossings the rendered surface
 * and the camera path are both linear, so constraining those points
 * guarantees the whole path — however long the step — never passes below
 * ground + clearance: no tunnelling through a steep ridge or peak, whatever
 * dt or speed.
 *
 * Free flight: height follows the requested vertical motion, lifted where
 * needed to ground + minClearance (and the lift is kept). Terrain-follow: height is exactly
 * ground + followClearance (Space/Shift change followClearance, never below
 * minClearance). Over no-surface points (NoData, sky, outside the grid)
 * nothing is imposed and no height is invented.
 */
export function stepFlight(
  t: FlightTerrain,
  pos: Vec3,
  heading: Heading,
  input: FlightInput,
  dt: number,
  params: FlightParams,
): { position: Vec3; followClearance: number } {
  const step = Math.max(0, dt) * params.speed;
  const rx = -heading.fz;
  const rz = heading.fx;
  let hx = heading.fx * input.forward + rx * input.right;
  let hz = heading.fz * input.forward + rz * input.right;
  const len = Math.hypot(hx, hz);
  if (len > 1) {
    hx /= len;
    hz /= len;
  }
  const bounds = flightBounds(t);
  const x0 = clamp(pos.x, bounds.minX, bounds.maxX);
  const z0 = clamp(pos.z, bounds.minZ, bounds.maxZ);
  const x1 = clamp(x0 + hx * step, bounds.minX, bounds.maxX);
  const z1 = clamp(z0 + hz * step, bounds.minZ, bounds.maxZ);
  const dx = x1 - x0;
  const dz = z1 - z0;

  const followClearance = params.follow
    ? Math.max(params.minClearance, params.followClearance + input.up * step)
    : params.followClearance;
  // Free flight: total requested vertical change over this step, spread
  // evenly along the path.
  const verticalRequest = params.follow ? 0 : input.up * step;

  // Walk the path from crossing to crossing. A lift over terrain carries
  // forward (the camera climbs over a ridge and stays on it, instead of
  // snapping back to the requested line on the far side); requested descent
  // then continues from there. Because the surface and the path are linear
  // between crossings, this gives exactly the result of integrating the same
  // motion in arbitrarily small steps — the outcome does not depend on dt.
  let y = pos.y;
  let previous = 0;
  for (const s of [0, ...triangleEdgeCrossings(t, x0, z0, x1, z1), 1]) {
    const ground = surfaceAtPathPoint(t, x0, z0, dx, dz, s);
    if (params.follow) {
      if (ground !== null) y = ground + followClearance;
    } else {
      y += verticalRequest * (s - previous);
      if (ground !== null) y = Math.max(y, ground + params.minClearance);
    }
    previous = s;
  }
  return { position: { x: x1, y, z: z1 }, followClearance };
}

/** Deterministic entry pose: the centre of the footprint's south edge
 * (image-bottom row), `clearance` above the rendered surface there — or
 * above the highest rendered vertex when that point has no surface — looking
 * level toward the footprint centre. */
export function entryPose(t: FlightTerrain, clearance: number): { position: Vec3; lookAt: Vec3 } {
  const centre = gridToWorld(t, (t.width - 1) / 2, (t.height - 1) / 2);
  const south = gridToWorld(t, (t.width - 1) / 2, t.height - 1);
  let base = groundHeightAt(t, south.x, south.z);
  if (base === null) {
    let highest = -Infinity;
    for (let i = 0; i < t.worldY.length; i++) {
      if (t.valid[i] && t.worldY[i] > highest) highest = t.worldY[i];
    }
    base = Number.isFinite(highest) ? highest : 0;
  }
  const y = base + clearance;
  return { position: { x: south.x, y, z: south.z }, lookAt: { x: centre.x, y, z: centre.z } };
}

/** Horizontal heading from a camera's world direction, or `fallback` when
 * the camera looks straight up/down. */
export function headingFromDirection(dx: number, dz: number, fallback: Heading): Heading {
  const len = Math.hypot(dx, dz);
  return len < 1e-6 ? fallback : { fx: dx / len, fz: dz / len };
}

/** Compass heading in degrees, clockwise from north. North is the image-top
 * direction (decreasing row); east is increasing column. */
export function headingDegrees(t: FlightTerrain, h: Heading): number {
  const north = { x: 0, z: -Math.sign(t.cellY || 1) };
  const east = { x: Math.sign(t.cellX || 1), z: 0 };
  const deg =
    (Math.atan2(h.fx * east.x + h.fz * east.z, h.fx * north.x + h.fz * north.z) * 180) / Math.PI;
  return (deg + 360) % 360;
}

export interface ClearanceDefaults {
  minClearance: number;
  /** Entry height above the surface, and the initial terrain-follow value. */
  entryClearance: number;
  /** Default speed, world units per second. */
  speed: number;
}

/** DSM: 2 reference units of clearance (times the visual exaggeration).
 * Relative depth: 1% of the larger horizontal extent (visual units). Entry
 * clearance: 10x that, or 5% of the extent if larger. Speed: the pre-P1-7
 * WASD speed. */
export function clearanceDefaults(
  t: FlightTerrain,
  kind: HeightKind,
  exaggeration: number,
): ClearanceDefaults {
  const extent = Math.max(Math.abs(t.cellX) * t.width, Math.abs(t.cellY) * t.height);
  const minClearance =
    kind === "elevation"
      ? DSM_MIN_CLEARANCE * exaggeration
      : RELATIVE_MIN_CLEARANCE_FRACTION * extent;
  return {
    minClearance,
    entryClearance: Math.max(10 * minClearance, 0.05 * extent),
    speed: Math.max(t.halfWidth, t.halfHeight, 10) * 0.6,
  };
}

export function changeSpeed(speed: number, defaultSpeed: number, direction: 1 | -1): number {
  const next = direction > 0 ? speed * SPEED_STEP : speed / SPEED_STEP;
  return clamp(next, defaultSpeed / SPEED_RANGE, defaultSpeed * SPEED_RANGE);
}

export interface FlightReadoutContext {
  kind: HeightKind;
  exaggeration: number;
  isGeoreferenced: boolean;
  localCrs: string | null;
  originX: number | null;
  originY: number | null;
  sourceWidth: number;
  sourceHeight: number;
}

export interface FlightReadout {
  /** Georeferenced: map coordinate in the grid's local CRS (the same frame
   * the 3D click path sends to /measurements/pixel). */
  mapX: number | null;
  mapY: number | null;
  crs: string | null;
  /** Not georeferenced: source pixel (col, row). */
  pixelCol: number | null;
  pixelRow: number | null;
  /** Raw display-grid value under the camera, or null (no surface). */
  groundValue: number | null;
  groundLabel: string;
  /** Height above the rendered surface. DSM: reference units (exaggeration
   * removed). Relative depth: visual units. Null over no surface. */
  heightAboveGround: number | null;
  minClearance: number;
  clearanceUnits: string;
  headingDeg: number;
  /** Horizontal speed in map units (georeferenced) or grid units per second. */
  speed: number;
  speedUnits: string;
  follow: boolean;
}

export function flightReadout(
  t: FlightTerrain,
  pos: Vec3,
  heading: Heading,
  params: FlightParams,
  ctx: FlightReadoutContext,
): FlightReadout {
  const isElevation = ctx.kind === "elevation";
  // World Y per reported unit: DSM world Y is (value - baseline) *
  // exaggeration, so dividing by exaggeration gives reference units.
  const verticalScale = isElevation ? ctx.exaggeration : 1;
  const localX = pos.x + t.halfWidth;
  const localZ = pos.z + t.halfHeight;
  const ground = groundHeightAt(t, pos.x, pos.z);
  return {
    mapX: ctx.isGeoreferenced ? (ctx.originX ?? 0) + localX : null,
    mapY: ctx.isGeoreferenced ? (ctx.originY ?? 0) + localZ : null,
    crs: ctx.isGeoreferenced ? ctx.localCrs : null,
    pixelCol: ctx.isGeoreferenced ? null : localX * (ctx.sourceWidth / t.width),
    pixelRow: ctx.isGeoreferenced ? null : localZ * (ctx.sourceHeight / t.height),
    groundValue: groundValueAt(t, pos.x, pos.z),
    groundLabel: isElevation
      ? "Ground elevation (display grid, exaggeration removed)"
      : "Relative depth below (unitless, display grid)",
    heightAboveGround: ground === null ? null : (pos.y - ground) / verticalScale,
    minClearance: params.minClearance / verticalScale,
    clearanceUnits: isElevation ? "reference units" : "visual units (relative, not a distance)",
    headingDeg: headingDegrees(t, heading),
    speed: params.speed,
    // Horizontal world units are the grid's local map units when
    // georeferenced (for a DSM or a georeferenced relative-depth grid),
    // otherwise display-grid cells.
    speedUnits: ctx.isGeoreferenced ? "map units/s" : "grid units/s",
    follow: params.follow,
  };
}
