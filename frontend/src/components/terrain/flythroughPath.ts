import {
  type FlightTerrain,
  flightBounds,
  footprint,
  groundHeightAt,
  type Heading,
  triangleEdgeCrossings,
  type Vec3,
} from "./flythrough";

// P1-8: waypoint flythrough paths — pure logic, no Three.js or React.
//
// The waypoints are joined by a centripetal Catmull-Rom curve, which is
// SAMPLED ONCE into a polyline with vertices at most PATH_SPACING_CELLS
// apart. That sampled polyline is the authoritative flown path: clearance,
// arc length, playback position and heading/look-ahead all operate on it,
// and the spline is never re-evaluated afterwards. Points where the polyline
// crosses a rendered-mesh triangle edge are inserted ON the polyline (they
// lie on its segments, so its shape is unchanged) — between consecutive such
// points both the surface and the path are linear, so meeting the clearance
// at those points proves it for the whole path (the same argument as P1-7's
// stepFlight). The surface is the rendered mesh, via flythrough.ts.

/** Maximum distance between consecutive sampled path vertices, in cells. */
export const PATH_SPACING_CELLS = 0.25;
/** Consecutive waypoints must be at least this far apart, in cells. */
export const MIN_WAYPOINT_SPACING_CELLS = 1;
/** A path must be at least this long horizontally, in cells. */
export const MIN_PATH_LENGTH_CELLS = 2;
/** Half-width of the clearance envelope window (smooths the ride), in cells. */
export const ENVELOPE_HALF_WINDOW_CELLS = 2;
/** Look-ahead for heading/pitch: the larger of this many cells ... */
export const LOOK_AHEAD_CELLS = 3;
/** ... and this many seconds of travel. */
export const LOOK_AHEAD_SECONDS = 1;
export const MAX_PITCH_DEG = 30;
/** Centripetal parameterisation (no cusps/self-intersection in a segment). */
const ALPHA = 0.5;

/** A waypoint in the terrain's grid-local frame (world = local - half). */
export interface PathWaypoint {
  localX: number;
  localZ: number;
  /** Georeferenced: map coordinate in the terrain's local CRS. */
  mapX: number | null;
  mapY: number | null;
  /** Not georeferenced: source pixel. */
  pixelCol: number | null;
  pixelRow: number | null;
}

export interface Point2 {
  x: number;
  z: number;
}

export function cellSize(t: FlightTerrain): number {
  return Math.min(Math.abs(t.cellX), Math.abs(t.cellY));
}

export function waypointWorld(t: FlightTerrain, w: PathWaypoint): Point2 {
  return { x: w.localX - t.halfWidth, z: w.localZ - t.halfHeight };
}

export type Check = { ok: true } | { ok: false; reason: string };

/** A waypoint must be inside the mesh footprint, on rendered surface, and
 * at least MIN_WAYPOINT_SPACING_CELLS from the previous waypoint. It is
 * never moved to make it valid. */
export function checkWaypoint(
  t: FlightTerrain,
  existing: PathWaypoint[],
  candidate: PathWaypoint,
): Check {
  const p = waypointWorld(t, candidate);
  const f = footprint(t);
  const eps = 1e-9 * Math.max(1, cellSize(t));
  if (p.x < f.minX - eps || p.x > f.maxX + eps || p.z < f.minZ - eps || p.z > f.maxZ + eps) {
    return { ok: false, reason: "Waypoint is outside the terrain footprint." };
  }
  if (groundHeightAt(t, p.x, p.z) === null) {
    return {
      ok: false,
      reason: "Waypoint has no terrain surface under it (NoData or excluded sky).",
    };
  }
  if (existing.length > 0) {
    const prev = waypointWorld(t, existing[existing.length - 1]);
    if (Math.hypot(p.x - prev.x, p.z - prev.z) < MIN_WAYPOINT_SPACING_CELLS * cellSize(t)) {
      return {
        ok: false,
        reason: "Waypoint is closer than one grid cell to the previous waypoint.",
      };
    }
  }
  return { ok: true };
}

// --------------------------------------------------------------------------
// Centripetal Catmull-Rom (Barry-Goldman), sampled into the flown polyline
// --------------------------------------------------------------------------

const dist = (a: Point2, b: Point2) => Math.hypot(b.x - a.x, b.z - a.z);
const lerp2 = (a: Point2, b: Point2, wa: number, wb: number): Point2 => ({
  x: a.x * wa + b.x * wb,
  z: a.z * wa + b.z * wb,
});

/** Point on the centripetal Catmull-Rom segment p1->p2 at parameter u in
 * [0, 1] (mapped onto the segment's own knot interval). */
export function catmullRomPoint(
  p0: Point2,
  p1: Point2,
  p2: Point2,
  p3: Point2,
  u: number,
): Point2 {
  const t0 = 0;
  const t1 = t0 + dist(p0, p1) ** ALPHA;
  const t2 = t1 + dist(p1, p2) ** ALPHA;
  const t3 = t2 + dist(p2, p3) ** ALPHA;
  const t = t1 + (t2 - t1) * u;
  const a1 = lerp2(p0, p1, (t1 - t) / (t1 - t0), (t - t0) / (t1 - t0));
  const a2 = lerp2(p1, p2, (t2 - t) / (t2 - t1), (t - t1) / (t2 - t1));
  const a3 = lerp2(p2, p3, (t3 - t) / (t3 - t2), (t - t2) / (t3 - t2));
  const b1 = lerp2(a1, a2, (t2 - t) / (t2 - t0), (t - t0) / (t2 - t0));
  const b2 = lerp2(a2, a3, (t3 - t) / (t3 - t1), (t - t1) / (t3 - t1));
  return lerp2(b1, b2, (t2 - t) / (t2 - t1), (t - t1) / (t2 - t1));
}

/** Samples the curve through `points` (at least 2, consecutive points
 * distinct) into a polyline whose consecutive vertices are at most
 * `spacing` apart. The curve passes exactly through every point; the ends
 * use mirrored phantom points. Returns the polyline and the index of each
 * waypoint in it. */
export function samplePath(
  points: Point2[],
  spacing: number,
): { vertices: Point2[]; waypointIndex: number[] } {
  const n = points.length;
  const at = (i: number): Point2 => {
    if (i < 0) return lerp2(points[0], points[1], 2, -1); // 2*P0 - P1
    if (i >= n) return lerp2(points[n - 1], points[n - 2], 2, -1); // 2*Pn-1 - Pn-2
    return points[i];
  };
  const vertices: Point2[] = [];
  const waypointIndex: number[] = [];
  for (let i = 0; i < n - 1; i++) {
    const p0 = at(i - 1);
    const p1 = points[i];
    const p2 = points[i + 1];
    const p3 = at(i + 2);
    let count = Math.max(4, Math.ceil(dist(p1, p2) / spacing));
    let segment: Point2[] = [];
    for (let attempt = 0; attempt < 16; attempt++) {
      segment = [p1];
      for (let k = 1; k < count; k++) segment.push(catmullRomPoint(p0, p1, p2, p3, k / count));
      segment.push(p2);
      let longest = 0;
      for (let k = 1; k < segment.length; k++) {
        longest = Math.max(longest, dist(segment[k - 1], segment[k]));
      }
      if (longest <= spacing) break;
      count *= 2;
    }
    waypointIndex.push(vertices.length);
    vertices.push(...segment.slice(0, -1));
  }
  waypointIndex.push(vertices.length);
  vertices.push(points[n - 1]);

  // Guarantee the spacing bound on the polyline itself: split any chord
  // that is still longer (linear points ON the polyline, shape unchanged).
  const out: Point2[] = [vertices[0]];
  const remap = new Map<number, number>([[0, 0]]);
  for (let k = 1; k < vertices.length; k++) {
    const a = vertices[k - 1];
    const b = vertices[k];
    const pieces = Math.ceil(dist(a, b) / spacing);
    for (let j = 1; j < pieces; j++) out.push(lerp2(a, b, 1 - j / pieces, j / pieces));
    out.push(b);
    remap.set(k, out.length - 1);
  }
  return { vertices: out, waypointIndex: waypointIndex.map((i) => remap.get(i)!) };
}

// --------------------------------------------------------------------------
// The flown path: polyline + clearance envelope + 3D arc length
// --------------------------------------------------------------------------

export interface FlightPath {
  /** Flown path vertices (world), including every triangle-edge crossing. */
  x: Float64Array;
  y: Float64Array;
  z: Float64Array;
  /** Cumulative 3D arc length at each vertex. */
  s: Float64Array;
  total: number;
  horizontalLength: number;
  /** Arc length at each waypoint. */
  waypointS: number[];
  /** Target clearance above the rendered surface (world units). */
  clearance: number;
  /** The sampled polyline (before crossing insertion) — for inspection/tests. */
  sampled: Point2[];
  /** Sampled vertices moved inside the flight bounds (curve overshoot). */
  clampedVertices: number;
  /** The exaggeration of the surface this path was built on. */
  exaggeration: number;
  cell: number;
}

export type PathResult = { ok: true; path: FlightPath } | { ok: false; reason: string };

/** Highest rendered surface at or immediately around (x, z): a point exactly
 * on a triangle edge or vertex next to a no-surface quad still sees the
 * surface that edge/vertex belongs to. */
function surfaceAround(t: FlightTerrain, x: number, z: number): number | null {
  const e = 1e-7 * cellSize(t);
  let best: number | null = null;
  for (const [dx, dz] of [
    [0, 0],
    [e, 0],
    [-e, 0],
    [0, e],
    [0, -e],
    [e, e],
    [e, -e],
    [-e, e],
    [-e, -e],
  ]) {
    const g = groundHeightAt(t, x + dx, z + dz);
    if (g !== null && (best === null || g > best)) best = g;
  }
  return best;
}

export function buildFlightPath(
  t: FlightTerrain,
  waypoints: PathWaypoint[],
  clearance: number,
  exaggeration: number,
): PathResult {
  if (waypoints.length < 2) {
    return { ok: false, reason: "Add at least two waypoints." };
  }
  for (let i = 0; i < waypoints.length; i++) {
    const check = checkWaypoint(t, waypoints.slice(0, i), waypoints[i]);
    if (!check.ok) return { ok: false, reason: `Waypoint ${i + 1}: ${check.reason}` };
  }
  const cell = cellSize(t);
  const { vertices, waypointIndex } = samplePath(
    waypoints.map((w) => waypointWorld(t, w)),
    PATH_SPACING_CELLS * cell,
  );

  // Curve overshoot is kept inside the P1-7 flight bounds (and counted).
  const b = flightBounds(t);
  let clampedVertices = 0;
  const sampled = vertices.map((p) => {
    const x = Math.min(Math.max(p.x, b.minX), b.maxX);
    const z = Math.min(Math.max(p.z, b.minZ), b.maxZ);
    if (x !== p.x || z !== p.z) clampedVertices++;
    return { x, z };
  });

  let horizontalLength = 0;
  for (let k = 1; k < sampled.length; k++) horizontalLength += dist(sampled[k - 1], sampled[k]);
  if (horizontalLength < MIN_PATH_LENGTH_CELLS * cell) {
    return { ok: false, reason: "The path is shorter than two grid cells." };
  }

  // Dense points on the polyline: every vertex + every triangle-edge crossing.
  const px: number[] = [sampled[0].x];
  const pz: number[] = [sampled[0].z];
  const pd: number[] = [0];
  const denseIndexOfVertex: number[] = [0];
  let d = 0;
  for (let k = 1; k < sampled.length; k++) {
    const a = sampled[k - 1];
    const c = sampled[k];
    const len = dist(a, c);
    for (const u of triangleEdgeCrossings(t, a.x, a.z, c.x, c.z)) {
      px.push(a.x + (c.x - a.x) * u);
      pz.push(a.z + (c.z - a.z) * u);
      pd.push(d + len * u);
    }
    d += len;
    px.push(c.x);
    pz.push(c.z);
    pd.push(d);
    denseIndexOfVertex.push(px.length - 1);
  }
  const count = px.length;
  const required: (number | null)[] = new Array(count);
  for (let k = 0; k < count; k++) {
    const g = surfaceAround(t, px[k], pz[k]);
    required[k] = g === null ? null : g + clearance;
  }

  // Clearance envelope: the highest requirement within +/- W along the path
  // (monotone deque sliding-window maximum). Every point with a requirement
  // is inside its own window, so y >= requirement everywhere it applies.
  const W = ENVELOPE_HALF_WINDOW_CELLS * cell;
  const y: (number | null)[] = new Array(count).fill(null);
  const deque: number[] = [];
  let head = 0;
  let next = 0;
  for (let k = 0; k < count; k++) {
    while (next < count && pd[next] <= pd[k] + W) {
      if (required[next] !== null) {
        while (deque.length > head && required[deque[deque.length - 1]]! <= required[next]!) {
          deque.pop();
        }
        deque.push(next);
      }
      next++;
    }
    while (deque.length > head && pd[deque[head]] < pd[k] - W) head++;
    if (deque.length > head) y[k] = required[deque[head]];
  }
  // No surface anywhere within the window: no constraint and no invented
  // ground — interpolate linearly between the nearest constrained points.
  for (let k = 0; k < count; k++) {
    if (y[k] !== null) continue;
    let before = k - 1;
    while (before >= 0 && y[before] === null) before--;
    let after = k + 1;
    while (after < count && y[after] === null) after++;
    if (before >= 0 && after < count) {
      const f = (pd[k] - pd[before]) / (pd[after] - pd[before]);
      y[k] = y[before]! + f * (y[after]! - y[before]!);
    } else if (before >= 0) {
      y[k] = y[before];
    } else if (after < count) {
      y[k] = y[after];
    }
  }

  const xs = Float64Array.from(px);
  const zs = Float64Array.from(pz);
  const ys = Float64Array.from(y as number[]);
  const s = new Float64Array(count);
  for (let k = 1; k < count; k++) {
    s[k] = s[k - 1] + Math.hypot(xs[k] - xs[k - 1], ys[k] - ys[k - 1], zs[k] - zs[k - 1]);
  }
  return {
    ok: true,
    path: {
      x: xs,
      y: ys,
      z: zs,
      s,
      total: s[count - 1],
      horizontalLength,
      waypointS: waypointIndex.map((i) => s[denseIndexOfVertex[i]]),
      clearance,
      sampled,
      clampedVertices,
      exaggeration,
      cell,
    },
  };
}

/** Position on the flown path at 3D arc length `at` (linear between path
 * vertices — never re-evaluating the spline). */
export function pathPointAt(path: FlightPath, at: number): Vec3 {
  const n = path.s.length;
  const target = Math.min(Math.max(at, 0), path.total);
  let lo = 0;
  let hi = n - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (path.s[mid] <= target) lo = mid;
    else hi = mid;
  }
  const span = path.s[hi] - path.s[lo];
  const f = span > 0 ? (target - path.s[lo]) / span : 0;
  return {
    x: path.x[lo] + f * (path.x[hi] - path.x[lo]),
    y: path.y[lo] + f * (path.y[hi] - path.y[lo]),
    z: path.z[lo] + f * (path.z[hi] - path.z[lo]),
  };
}

export function lookAheadDistance(path: FlightPath, speed: number): number {
  return Math.max(LOOK_AHEAD_CELLS * path.cell, LOOK_AHEAD_SECONDS * speed);
}

export interface PathState {
  position: Vec3;
  heading: Heading;
  pitch: number; // radians, within +/- MAX_PITCH_DEG
  lookAt: Vec3;
  progress: number; // 0..1
  finished: boolean;
}

/** Deterministic camera state at arc length `at`: position on the flown
 * polyline, heading/pitch toward the point `lookAhead` further along it
 * (clamped to the end; at the very end the last non-degenerate segment's
 * direction is kept). */
export function pathStateAt(path: FlightPath, at: number, lookAhead: number): PathState {
  const s = Math.min(Math.max(at, 0), path.total);
  const position = pathPointAt(path, s);
  let ahead = pathPointAt(path, Math.min(s + lookAhead, path.total));
  let hx = ahead.x - position.x;
  let hz = ahead.z - position.z;
  let h = Math.hypot(hx, hz);
  if (h < 1e-9) {
    const n = path.s.length;
    for (let k = n - 1; k > 0; k--) {
      hx = path.x[k] - path.x[k - 1];
      hz = path.z[k] - path.z[k - 1];
      h = Math.hypot(hx, hz);
      if (h >= 1e-9) {
        ahead = { x: position.x + hx, y: position.y, z: position.z + hz };
        break;
      }
    }
  }
  const heading = h >= 1e-9 ? { fx: hx / h, fz: hz / h } : { fx: 0, fz: -1 };
  const maxPitch = (MAX_PITCH_DEG * Math.PI) / 180;
  const pitch =
    h >= 1e-9 ? Math.min(Math.max(Math.atan2(ahead.y - position.y, h), -maxPitch), maxPitch) : 0;
  return {
    position,
    heading,
    pitch,
    lookAt: {
      x: position.x + heading.fx * Math.cos(pitch),
      y: position.y + Math.sin(pitch),
      z: position.z + heading.fz * Math.cos(pitch),
    },
    progress: path.total > 0 ? s / path.total : 1,
    finished: s >= path.total,
  };
}

// --------------------------------------------------------------------------
// Playback control/status shared by the workspace controls and the 3D view
// --------------------------------------------------------------------------

export type PlaybackState = "idle" | "playing" | "paused" | "finished";

export interface PlaybackCommand {
  type: "play" | "pause" | "resume" | "restart" | "stop" | "record";
  /** Increments per command so repeated commands are distinct. */
  id: number;
}

export interface PlaybackStatus {
  state: PlaybackState;
  progress: number; // 0..1
  /** Path time at 1x speed, seconds. */
  elapsed: number;
  duration: number;
  recording: boolean;
  pathOk: boolean;
  pathReason: string | null;
  /** Target clearance above the rendered surface, in `clearanceUnits`. */
  clearance: number | null;
  clearanceUnits: string;
  message: string | null;
}

export const IDLE_PLAYBACK: PlaybackStatus = {
  state: "idle",
  progress: 0,
  elapsed: 0,
  duration: 0,
  recording: false,
  pathOk: false,
  pathReason: null,
  clearance: null,
  clearanceUnits: "",
  message: null,
};
