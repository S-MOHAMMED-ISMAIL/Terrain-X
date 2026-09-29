import * as THREE from "three";
import { describe, expect, it } from "vitest";
import type { TerrainMetadata } from "@/api/types";
import {
  flightBounds,
  flightTerrainFromMesh,
  groundHeightAt,
  headingDegrees,
  triangleEdgeCrossings,
  type FlightTerrain,
} from "./flythrough";
import {
  buildFlightPath,
  catmullRomPoint,
  checkWaypoint,
  type FlightPath,
  lookAheadDistance,
  MAX_PITCH_DEG,
  PATH_SPACING_CELLS,
  pathPointAt,
  pathStateAt,
  type PathWaypoint,
  samplePath,
  waypointWorld,
} from "./flythroughPath";
import { buildTerrainGeometry } from "./terrainMesh";

function metadata(w: number, h: number, overrides: Partial<TerrainMetadata> = {}): TerrainMetadata {
  return {
    artifact_id: "a",
    height_kind: "elevation",
    source_artifact_type: "dsm",
    width: w,
    height: h,
    source_width: w,
    source_height: h,
    nodata_present: false,
    is_georeferenced: true,
    crs: "EPSG:32633",
    local_crs: "EPSG:32633",
    origin_x: 500000,
    origin_y: 4649984,
    cell_size_x: 2,
    cell_size_y: -2,
    bounds: null,
    min_elevation: null,
    max_elevation: null,
    min_height_value: null,
    max_height_value: null,
    encoding: "float32",
    ...overrides,
  };
}

function grid(w: number, h: number, f: (c: number, r: number) => number) {
  const v = new Float32Array(w * h);
  for (let r = 0; r < h; r++) for (let c = 0; c < w; c++) v[r * w + c] = f(c, r);
  return v;
}

function terrain(meta: TerrainMetadata, raw: Float32Array, exaggeration = 1.5) {
  const built = buildTerrainGeometry(meta, raw, exaggeration);
  const t = flightTerrainFromMesh(
    built.geometry.getAttribute("position").array,
    raw,
    { width: meta.width, height: meta.height, cellX: meta.cell_size_x!, cellY: meta.cell_size_y! },
    built.halfWidth,
    built.halfHeight,
  );
  const mesh = new THREE.Mesh(built.geometry, new THREE.MeshBasicMaterial({ side: THREE.DoubleSide }));
  mesh.position.set(-built.halfWidth, 0, -built.halfHeight);
  mesh.updateMatrixWorld(true);
  const ray = new THREE.Raycaster();
  const raycast = (x: number, z: number) => {
    ray.set(new THREE.Vector3(x, 1e6, z), new THREE.Vector3(0, -1, 0));
    const hits = ray.intersectObject(mesh);
    return hits.length ? hits[0].point.y : null;
  };
  return { t, raycast, exaggeration };
}

/** A waypoint at grid (col, row), as a 3D click would produce it; integer
 * (col, row) is the centre of that grid cell, i.e. its mesh vertex (D1). */
function wp(t: FlightTerrain, col: number, row: number): PathWaypoint {
  return {
    localX: (col + 0.5) * t.cellX,
    localZ: (row + 0.5) * t.cellY,
    mapX: 500000 + (col + 0.5) * t.cellX,
    mapY: 4649984 + (row + 0.5) * t.cellY,
    pixelCol: null,
    pixelRow: null,
  };
}

function mustBuild(t: FlightTerrain, wps: PathWaypoint[], clearance = 3): FlightPath {
  const result = buildFlightPath(t, wps, clearance, 1.5);
  if (!result.ok) throw new Error(result.reason);
  return result.path;
}

/** Clearance check on the flown path against the RENDERED mesh (raycast):
 * every path vertex, plus `randomSamples` random arc-length points. */
function assertClearance(
  path: FlightPath,
  raycast: (x: number, z: number) => number | null,
  randomSamples: number,
) {
  let worst = Infinity;
  const check = (p: { x: number; y: number; z: number }) => {
    const g = raycast(p.x, p.z);
    if (g !== null) worst = Math.min(worst, p.y - (g + path.clearance));
  };
  for (let k = 0; k < path.x.length; k++) check({ x: path.x[k], y: path.y[k], z: path.z[k] });
  let seed = 12345;
  for (let i = 0; i < randomSamples; i++) {
    seed = (seed * 1103515245 + 12345) % 2147483648;
    check(pathPointAt(path, (seed / 2147483648) * path.total));
  }
  expect(worst).toBeGreaterThanOrEqual(-1e-6);
}

describe("waypoint validation", () => {
  const { t } = terrain(
    metadata(12, 12, { nodata_present: true }),
    grid(12, 12, (c, r) => (c >= 8 && r >= 8 ? Number.NaN : 100 + c)),
  );

  it("accepts a waypoint on surface and rejects outside / NoData / too close — never adjusting it", () => {
    expect(checkWaypoint(t, [], wp(t, 3, 3))).toEqual({ ok: true });
    const outside = checkWaypoint(t, [], wp(t, -1, 3));
    expect(outside.ok).toBe(false);
    expect(!outside.ok && outside.reason).toMatch(/outside the terrain footprint/);
    const hole = checkWaypoint(t, [], wp(t, 9.5, 9.5));
    expect(!hole.ok && hole.reason).toMatch(/no terrain surface/);
    const close = checkWaypoint(t, [wp(t, 3, 3)], wp(t, 3.5, 3.2));
    expect(!close.ok && close.reason).toMatch(/closer than one grid cell/);
    expect(checkWaypoint(t, [wp(t, 3, 3)], wp(t, 4, 3))).toEqual({ ok: true });
  });

  it("refuses too few waypoints, a too-short path and invalid waypoints", () => {
    expect(buildFlightPath(t, [wp(t, 2, 2)], 3, 1.5)).toEqual({
      ok: false,
      reason: "Add at least two waypoints.",
    });
    const short = buildFlightPath(t, [wp(t, 2, 2), wp(t, 3, 2)], 3, 1.5);
    expect(!short.ok && short.reason).toMatch(/shorter than two grid cells/);
    const bad = buildFlightPath(t, [wp(t, 2, 2), wp(t, 9.5, 9.5)], 3, 1.5);
    expect(!bad.ok && bad.reason).toMatch(/^Waypoint 2: .*no terrain surface/);
  });

  it("a 2D (map coordinate) waypoint and a 3D (local) waypoint at the same place coincide", () => {
    const fromMap: PathWaypoint = {
      // Centre of grid cell (5, 5): origin + (5 + 0.5) * cell.
      localX: 500011 - 500000, // map - origin
      localZ: 4649973 - 4649984,
      mapX: 500011,
      mapY: 4649973,
      pixelCol: null,
      pixelRow: null,
    };
    expect(waypointWorld(t, fromMap)).toEqual(waypointWorld(t, wp(t, 5, 5)));
  });
});

describe("sampled polyline = authoritative path", () => {
  it("passes exactly through every waypoint with chords <= 0.25 cell", () => {
    const pts = [
      { x: 0, z: 0 },
      { x: 10, z: 3 },
      { x: 4, z: 12 },
      { x: 15, z: 14 },
    ];
    const { vertices, waypointIndex } = samplePath(pts, 0.5);
    expect(waypointIndex.map((i) => vertices[i])).toEqual(pts);
    for (let k = 1; k < vertices.length; k++) {
      expect(Math.hypot(vertices[k].x - vertices[k - 1].x, vertices[k].z - vertices[k - 1].z))
        .toBeLessThanOrEqual(0.5 + 1e-9);
    }
  });

  it("is a straight line for collinear evenly spaced waypoints", () => {
    const { vertices } = samplePath(
      [
        { x: 0, z: 0 },
        { x: 5, z: 5 },
        { x: 10, z: 10 },
      ],
      0.25,
    );
    for (const v of vertices) expect(v.x).toBeCloseTo(v.z, 9);
  });

  it("matches the centripetal Catmull-Rom formula at its endpoints", () => {
    const p0 = { x: -1, z: 0 };
    const p1 = { x: 0, z: 0 };
    const p2 = { x: 4, z: 1 };
    const p3 = { x: 5, z: 5 };
    const a = catmullRomPoint(p0, p1, p2, p3, 0);
    const b = catmullRomPoint(p0, p1, p2, p3, 1);
    expect(a.x).toBeCloseTo(0, 9);
    expect(a.z).toBeCloseTo(0, 9);
    expect(b.x).toBeCloseTo(4, 9);
    expect(b.z).toBeCloseTo(1, 9);
  });

  it("flies only along the sampled polyline; waypoints sit at their arc lengths", () => {
    const { t } = terrain(metadata(24, 24), grid(24, 24, (c, r) => 100 + 3 * Math.sin(c / 2) + r));
    const wps = [wp(t, 3, 4), wp(t, 18, 6), wp(t, 12, 19)];
    const path = mustBuild(t, wps);
    // Every flown vertex lies ON a sampled polyline segment.
    const segs = path.sampled;
    let seg = 1;
    for (let k = 0; k < path.x.length; k++) {
      const p = { x: path.x[k], z: path.z[k] };
      let found = false;
      for (let j = Math.max(1, seg - 1); j < segs.length && !found; j++) {
        const a = segs[j - 1];
        const b = segs[j];
        const cross = (b.x - a.x) * (p.z - a.z) - (b.z - a.z) * (p.x - a.x);
        const dot = (p.x - a.x) * (b.x - a.x) + (p.z - a.z) * (b.z - a.z);
        const len2 = (b.x - a.x) ** 2 + (b.z - a.z) ** 2;
        if (Math.abs(cross) <= 1e-7 * Math.max(1, len2) && dot >= -1e-9 && dot <= len2 + 1e-9) {
          found = true;
          seg = j;
        }
      }
      expect(found).toBe(true);
    }
    // Spacing bound holds on the polyline itself.
    for (let k = 1; k < segs.length; k++) {
      expect(Math.hypot(segs[k].x - segs[k - 1].x, segs[k].z - segs[k - 1].z)).toBeLessThanOrEqual(
        PATH_SPACING_CELLS * path.cell + 1e-9,
      );
    }
    // Waypoints on the flown path.
    wps.forEach((w, i) => {
      const at = pathPointAt(path, path.waypointS[i]);
      const expected = waypointWorld(t, w);
      expect(at.x).toBeCloseTo(expected.x, 9);
      expect(at.z).toBeCloseTo(expected.z, 9);
    });
    // Every triangle-edge crossing of every sampled segment is a flown vertex.
    const flown = new Set<string>();
    for (let k = 0; k < path.x.length; k++) flown.add(`${path.x[k].toFixed(9)},${path.z[k].toFixed(9)}`);
    for (let j = 1; j < segs.length; j++) {
      const a = segs[j - 1];
      const b = segs[j];
      for (const u of triangleEdgeCrossings(t, a.x, a.z, b.x, b.z)) {
        const key = `${(a.x + (b.x - a.x) * u).toFixed(9)},${(a.z + (b.z - a.z) * u).toFixed(9)}`;
        expect(flown.has(key)).toBe(true);
      }
    }
  });
});

describe("path clearance against the rendered mesh", () => {
  it("holds everywhere on rolling terrain (vertices + 10,000 random points)", () => {
    const { t, raycast } = terrain(
      metadata(30, 30),
      grid(30, 30, (c, r) => 100 + 8 * Math.sin(c / 3) * Math.cos(r / 4) + 0.5 * r),
    );
    const path = mustBuild(t, [wp(t, 2, 2), wp(t, 25, 8), wp(t, 6, 20), wp(t, 27, 27)], 4);
    assertClearance(path, raycast, 10_000);
  });

  it("clears a one-cell steep ridge between two waypoints", () => {
    const { t, raycast } = terrain(metadata(31, 11), grid(31, 11, (c) => (c === 15 ? 500 : 100)));
    const path = mustBuild(t, [wp(t, 3, 5), wp(t, 27, 5)], 2);
    assertClearance(path, raycast, 10_000);
    const top = groundHeightAt(t, 15.5 * t.cellX - t.halfWidth, 5.5 * t.cellY - t.halfHeight)!;
    const mid = pathPointAt(path, path.total / 2);
    expect(mid.y).toBeGreaterThanOrEqual(top + path.clearance - 1e-6);
  });

  it("clears a single-vertex spike the path passes beside and over", () => {
    const { t, raycast } = terrain(
      metadata(21, 21),
      grid(21, 21, (c, r) => (c === 10 && r === 10 ? 900 : 100)),
    );
    for (const wps of [
      [wp(t, 2, 10), wp(t, 18, 10)], // straight over it
      [wp(t, 2, 9.4), wp(t, 18, 10.7)], // diagonally past it
      [wp(t, 3, 3), wp(t, 10.3, 10.2), wp(t, 17, 3)], // turning on top of it
    ]) {
      assertClearance(mustBuild(t, wps, 3), raycast, 10_000);
    }
  });

  it("over a NoData stretch imposes nothing and invents no ground", () => {
    const { t, raycast } = terrain(
      metadata(31, 11, { nodata_present: true }),
      grid(31, 11, (c) => (c >= 12 && c <= 18 ? Number.NaN : 100 + c)),
    );
    const path = mustBuild(t, [wp(t, 2, 5), wp(t, 28, 5)], 2);
    assertClearance(path, raycast, 5_000);
    // Inside the hole (beyond the envelope window) there is no surface; the
    // height there is interpolated between the constrained points around it.
    const hole = pathPointAt(path, path.total * (15 - 2) / 26);
    expect(groundHeightAt(t, hole.x, hole.z)).toBeNull();
    const before = pathPointAt(path, path.total * (9 - 2) / 26).y;
    const after = pathPointAt(path, path.total * (21 - 2) / 26).y;
    expect(hole.y).toBeGreaterThanOrEqual(Math.min(before, after) - 1e-6);
    expect(hole.y).toBeLessThanOrEqual(Math.max(before, after) + 1e-6);
  });

  it("holds on a relative-depth (gamma-mapped) mesh", () => {
    const meta = metadata(20, 16, {
      height_kind: "relative_depth",
      source_artifact_type: "relative_depth",
      is_georeferenced: false,
      local_crs: null,
      cell_size_x: 1,
      cell_size_y: 1,
    });
    const { t, raycast } = terrain(meta, grid(20, 16, (c, r) => 0.2 + ((c * 7 + r * 3) % 11) / 10), 6);
    assertClearance(mustBuild(t, [wp(t, 2, 2), wp(t, 17, 7), wp(t, 4, 13)], 0.3), raycast, 5_000);
  });
});

describe("sharp turns, overshoot, orientation and playback state", () => {
  const { t } = terrain(metadata(40, 40), grid(40, 40, (c, r) => 100 + 0.2 * c + 0.1 * r));

  it("a ~170 degree hairpin turns smoothly (bounded heading change per step)", () => {
    const path = mustBuild(t, [wp(t, 5, 20), wp(t, 30, 21), wp(t, 5, 23)]);
    const look = lookAheadDistance(path, 5);
    let previous = headingDegrees(t, pathStateAt(path, 0, look).heading);
    const step = path.cell * 0.05;
    for (let s = step; s <= path.total; s += step) {
      const h = headingDegrees(t, pathStateAt(path, s, look).heading);
      const delta = Math.abs(((h - previous + 540) % 360) - 180);
      expect(delta).toBeLessThan(20);
      previous = h;
    }
  });

  it("keeps curve overshoot inside the flight bounds and counts it", () => {
    const path = mustBuild(t, [wp(t, 0, 0), wp(t, 39, 1), wp(t, 0, 3)]);
    const b = flightBounds(t);
    for (const v of path.sampled) {
      expect(v.x).toBeGreaterThanOrEqual(b.minX - 1e-9);
      expect(v.x).toBeLessThanOrEqual(b.maxX + 1e-9);
      expect(v.z).toBeGreaterThanOrEqual(b.minZ - 1e-9);
      expect(v.z).toBeLessThanOrEqual(b.maxZ + 1e-9);
    }
    expect(path.clampedVertices).toBeGreaterThanOrEqual(0);
  });

  it("pathStateAt is deterministic, progress is monotonic, pitch is limited, ends finished", () => {
    const path = mustBuild(t, [wp(t, 3, 3), wp(t, 30, 10), wp(t, 12, 34)]);
    const look = lookAheadDistance(path, 8);
    expect(pathStateAt(path, 17.3, look)).toEqual(pathStateAt(path, 17.3, look));
    let last = -1;
    for (let s = 0; s <= path.total; s += path.total / 500) {
      const st = pathStateAt(path, s, look);
      expect(st.progress).toBeGreaterThanOrEqual(last);
      last = st.progress;
      expect(Math.abs(st.pitch)).toBeLessThanOrEqual((MAX_PITCH_DEG * Math.PI) / 180 + 1e-12);
      expect(Number.isFinite(st.heading.fx) && Number.isFinite(st.heading.fz)).toBe(true);
    }
    const start = pathStateAt(path, -5, look);
    expect(start.progress).toBe(0);
    expect(start.finished).toBe(false);
    const end = pathStateAt(path, path.total + 5, look);
    expect(end.progress).toBe(1);
    expect(end.finished).toBe(true);
    expect(end.position.x).toBeCloseTo(waypointWorld(t, wp(t, 12, 34)).x, 9);
    // At the very end the heading keeps the final direction of travel.
    const nearEnd = pathStateAt(path, path.total - 1e-6, look);
    expect(end.heading.fx).toBeCloseTo(nearEnd.heading.fx, 3);
    expect(end.heading.fz).toBeCloseTo(nearEnd.heading.fz, 3);
  });
});
