import * as THREE from "three";
import { describe, expect, it } from "vitest";
import type { TerrainMetadata } from "@/api/types";
import {
  changeSpeed,
  clearanceDefaults,
  DSM_MIN_CLEARANCE,
  entryPose,
  type FlightParams,
  type FlightTerrain,
  flightBounds,
  flightReadout,
  flightTerrainFromMesh,
  footprint,
  groundHeightAt,
  groundValueAt,
  type Heading,
  headingDegrees,
  headingFromDirection,
  SPEED_RANGE,
  SPEED_STEP,
  stepFlight,
  type Vec3,
} from "./flythrough";
import { buildTerrainGeometry } from "./terrainMesh";

// Every terrain below is a REAL mesh from buildTerrainGeometry (the exact
// function the 3D view renders), so these tests check flight against the
// rendered surface, not a re-derivation of it.

function metadata(
  width: number,
  height: number,
  overrides: Partial<TerrainMetadata> = {},
): TerrainMetadata {
  return {
    artifact_id: "a",
    height_kind: "elevation",
    source_artifact_type: "dsm",
    width,
    height,
    source_width: width,
    source_height: height,
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

function grid(width: number, height: number, f: (col: number, row: number) => number) {
  const values = new Float32Array(width * height);
  for (let row = 0; row < height; row++) {
    for (let col = 0; col < width; col++) values[row * width + col] = f(col, row);
  }
  return values;
}

function terrain(meta: TerrainMetadata, raw: Float32Array, exaggeration = 1.5) {
  const built = buildTerrainGeometry(meta, raw, exaggeration);
  const positions = built.geometry.getAttribute("position").array;
  const t = flightTerrainFromMesh(
    positions,
    raw,
    {
      width: meta.width,
      height: meta.height,
      cellX: meta.cell_size_x ?? 1,
      cellY: meta.cell_size_y ?? 1,
    },
    built.halfWidth,
    built.halfHeight,
  );
  return { t, built, exaggeration };
}

/** Rendered-surface height by casting a ray straight down onto the actual
 * Three.js mesh, placed exactly as TerrainView3D.tsx places it. */
function raycastGround(built: ReturnType<typeof buildTerrainGeometry>, x: number, z: number) {
  const mesh = new THREE.Mesh(
    built.geometry,
    new THREE.MeshBasicMaterial({ side: THREE.DoubleSide }),
  );
  mesh.position.set(-built.halfWidth, 0, -built.halfHeight);
  mesh.updateMatrixWorld(true);
  const ray = new THREE.Raycaster(new THREE.Vector3(x, 1e6, z), new THREE.Vector3(0, -1, 0));
  const hits = ray.intersectObject(mesh);
  return hits.length ? hits[0].point.y : null;
}

/** World position of fractional grid (col, row), where integer (col, row)
 * is the CENTRE of that grid cell (D1): local = (index + 0.5) * cell. */
function worldOf(t: FlightTerrain, col: number, row: number) {
  return { x: (col + 0.5) * t.cellX - t.halfWidth, z: (row + 0.5) * t.cellY - t.halfHeight };
}

const NORTH_UP_PLANE = (col: number, row: number) => 100 + 0.5 * col - 0.25 * row;

function params(t: FlightTerrain, overrides: Partial<FlightParams> = {}): FlightParams {
  const d = clearanceDefaults(t, "elevation", 1.5);
  return {
    speed: d.speed,
    minClearance: d.minClearance,
    follow: false,
    followClearance: d.entryClearance,
    ...overrides,
  };
}

const HEAD_EAST: Heading = { fx: 1, fz: 0 };
const idle = { forward: 0, right: 0, up: 0 };

describe("ground height is the rendered surface", () => {
  it("is exact on synthetic planar terrain (DSM: worldY and raw value)", () => {
    const meta = metadata(6, 5);
    const raw = grid(6, 5, NORTH_UP_PLANE);
    const { t } = terrain(meta, raw, 1.5);
    const baseline = (Math.min(...raw) + Math.max(...raw)) / 2;
    for (const [col, row] of [
      [0, 0],
      [2.3, 1.7],
      [4.9, 3.2],
      [5, 4],
      [0.5, 3.9],
    ]) {
      const { x, z } = worldOf(t, col, row);
      const expectedRaw = NORTH_UP_PLANE(col, row);
      expect(groundValueAt(t, x, z)).toBeCloseTo(expectedRaw, 4);
      expect(groundHeightAt(t, x, z)).toBeCloseTo((expectedRaw - baseline) * 1.5, 4);
    }
  });

  it("matches a raycast on the real mesh for rough terrain, on both triangles of a quad", () => {
    const meta = metadata(7, 6, { cell_size_x: 3, cell_size_y: -3 });
    const raw = grid(7, 6, (c, r) => 50 + Math.sin(c * 1.7) * 9 + ((r * 13) % 5) * 4);
    const { t, built } = terrain(meta, raw, 2);
    for (let i = 0; i < 60; i++) {
      const col = ((i * 37) % 600) / 100;
      const row = ((i * 53) % 500) / 100;
      const { x, z } = worldOf(t, col, row);
      const expected = raycastGround(built, x, z);
      expect(expected).not.toBeNull();
      expect(groundHeightAt(t, x, z)).toBeCloseTo(expected!, 3);
    }
  });

  it("returns null over a NoData vertex's quads and outside the footprint", () => {
    const meta = metadata(5, 5, { nodata_present: true });
    const raw = grid(5, 5, NORTH_UP_PLANE);
    raw[2 * 5 + 2] = Number.NaN;
    const { t, built } = terrain(meta, raw);
    for (const [col, row] of [
      [1.5, 1.5],
      [2.5, 2.5],
      [1.2, 2.8],
      [2.9, 1.1],
    ]) {
      const { x, z } = worldOf(t, col, row);
      expect(groundHeightAt(t, x, z)).toBeNull();
      expect(groundValueAt(t, x, z)).toBeNull();
      expect(raycastGround(built, x, z)).toBeNull(); // the mesh has no surface there either
    }
    const inside = worldOf(t, 0.5, 0.5);
    expect(groundHeightAt(t, inside.x, inside.z)).not.toBeNull();
    const outside = worldOf(t, -0.5, 2);
    expect(groundHeightAt(t, outside.x, outside.z)).toBeNull();
    const beyond = worldOf(t, 2, 4.5);
    expect(groundHeightAt(t, beyond.x, beyond.z)).toBeNull();
  });
});

describe("stepFlight", () => {
  const meta = metadata(11, 11);
  const flat = terrain(meta, grid(11, 11, () => 100));
  const centre = worldOf(flat.t, 5, 5);

  it("moves relative to the camera heading, scaled by speed and dt", () => {
    const p = params(flat.t, { speed: 4 });
    const start: Vec3 = { x: centre.x, y: 30, z: centre.z };
    const heading = headingFromDirection(1, 1, HEAD_EAST); // south-east in world x/z
    const fwd = stepFlight(flat.t, start, heading, { ...idle, forward: 1 }, 0.5, p).position;
    expect(Math.hypot(fwd.x - start.x, fwd.z - start.z)).toBeCloseTo(2, 6);
    expect(fwd.x - start.x).toBeCloseTo(Math.SQRT1_2 * 2, 6);
    expect(fwd.z - start.z).toBeCloseTo(Math.SQRT1_2 * 2, 6);
    // Right is heading x up: for heading (fx, fz) it is (-fz, fx).
    const right = stepFlight(flat.t, start, HEAD_EAST, { ...idle, right: 1 }, 0.25, p).position;
    expect(right.x).toBeCloseTo(start.x, 6);
    expect(right.z - start.z).toBeCloseTo(1, 6);
    // Diagonal input is normalized, never faster.
    const diag = stepFlight(flat.t, start, HEAD_EAST, { ...idle, forward: 1, right: 1 }, 1, p);
    expect(Math.hypot(diag.position.x - start.x, diag.position.z - start.z)).toBeCloseTo(4, 6);
    // Twice the dt, twice the distance.
    const a = stepFlight(flat.t, start, HEAD_EAST, { ...idle, forward: 1 }, 0.1, p).position;
    const b = stepFlight(flat.t, start, HEAD_EAST, { ...idle, forward: 1 }, 0.2, p).position;
    expect(b.x - start.x).toBeCloseTo(2 * (a.x - start.x), 6);
  });

  it("Space/Shift move vertically; Shift stops at ground + minimum clearance", () => {
    const p = params(flat.t, { speed: 10 });
    const ground = groundHeightAt(flat.t, centre.x, centre.z)!;
    const start: Vec3 = { x: centre.x, y: ground + 20, z: centre.z };
    const up = stepFlight(flat.t, start, HEAD_EAST, { ...idle, up: 1 }, 0.5, p).position;
    expect(up.y).toBeCloseTo(start.y + 5, 6);
    const down = stepFlight(flat.t, start, HEAD_EAST, { ...idle, up: -1 }, 0.5, p).position;
    expect(down.y).toBeCloseTo(start.y - 5, 6);
    const dive = stepFlight(flat.t, start, HEAD_EAST, { ...idle, up: -1 }, 100, p).position;
    expect(dive.y).toBeCloseTo(ground + p.minClearance, 6);
  });

  it("never ends a step below ground + minimum clearance on a slope", () => {
    const slope = terrain(metadata(21, 21), grid(21, 21, (c) => 100 + 6 * c));
    const p = params(slope.t, { speed: 7 });
    const start = worldOf(slope.t, 1, 10);
    let pos: Vec3 = {
      ...start,
      y: groundHeightAt(slope.t, start.x, start.z)! + p.minClearance,
    };
    for (let i = 0; i < 40; i++) {
      pos = stepFlight(slope.t, pos, HEAD_EAST, { forward: 1, right: 0, up: -1 }, 0.1, p).position;
      const g = groundHeightAt(slope.t, pos.x, pos.z);
      if (g !== null) expect(pos.y).toBeGreaterThanOrEqual(g + p.minClearance - 1e-6);
    }
  });

  it("cannot tunnel through a steep ridge, however large the step", () => {
    // A one-cell-wide, very tall ridge across the middle of a flat plain.
    const ridge = terrain(
      metadata(31, 11),
      grid(31, 11, (c) => (c === 15 ? 400 : 100)),
      1,
    );
    const p = params(ridge.t, { speed: 20, minClearance: 2 });
    const start: Vec3 = { ...worldOf(ridge.t, 3, 5), y: 0 };
    start.y = groundHeightAt(ridge.t, start.x, start.z)! + 5;
    const ridgeTop = groundHeightAt(ridge.t, worldOf(ridge.t, 15, 5).x, worldOf(ridge.t, 15, 5).z)!;

    // One huge step (20 cells: col 3 -> col 23) that jumps from before the
    // ridge (col 15) to well beyond it.
    const big = stepFlight(ridge.t, start, HEAD_EAST, { ...idle, forward: 1 }, 2, p).position;
    expect(big.x).toBeGreaterThan(worldOf(ridge.t, 20, 5).x);
    // It climbed over the ridge and kept that height: it does NOT reappear
    // on the far side at its original, lower altitude.
    expect(big.y).toBeGreaterThanOrEqual(ridgeTop + p.minClearance - 1e-6);

    // Identical to integrating the same motion in many tiny steps.
    let fine = start;
    for (let i = 0; i < 400; i++) {
      fine = stepFlight(ridge.t, fine, HEAD_EAST, { ...idle, forward: 1 }, 2 / 400, p).position;
    }
    expect(big.x).toBeCloseTo(fine.x, 6);
    expect(big.y).toBeCloseTo(fine.y, 6);

    // Same with descent requested throughout: still never below the ridge
    // on the way over, then descending at the requested rate.
    const bigDown = stepFlight(ridge.t, start, HEAD_EAST, { forward: 1, right: 0, up: -0.2 }, 2, p);
    let fineDown = start;
    for (let i = 0; i < 400; i++) {
      fineDown = stepFlight(
        ridge.t,
        fineDown,
        HEAD_EAST,
        { forward: 1, right: 0, up: -0.2 },
        2 / 400,
        p,
      ).position;
    }
    expect(bigDown.position.y).toBeCloseTo(fineDown.y, 6);
    const endGround = groundHeightAt(ridge.t, bigDown.position.x, bigDown.position.z)!;
    expect(bigDown.position.y).toBeGreaterThanOrEqual(endGround + p.minClearance - 1e-6);
  });

  it("imposes nothing and invents no height over NoData", () => {
    const hole = terrain(
      metadata(11, 11, { nodata_present: true }),
      grid(11, 11, (c, r) => (c >= 4 && c <= 6 && r >= 4 && r <= 6 ? Number.NaN : 100)),
    );
    const p = params(hole.t, { speed: 10 });
    const inHole = worldOf(hole.t, 5, 5);
    expect(groundHeightAt(hole.t, inHole.x, inHole.z)).toBeNull();
    const start: Vec3 = { x: inHole.x, y: -500, z: inHole.z };
    // Free flight over no surface: no snap, only the requested motion.
    const still = stepFlight(hole.t, start, HEAD_EAST, idle, 1, p).position;
    expect(still.y).toBe(-500);
    const down = stepFlight(hole.t, start, HEAD_EAST, { ...idle, up: -1 }, 0.1, p).position;
    expect(down.y).toBeCloseTo(-501, 6);
    // Terrain-follow keeps its altitude over the hole.
    const follow = stepFlight(hole.t, start, HEAD_EAST, idle, 1, { ...p, follow: true }).position;
    expect(follow.y).toBe(-500);
  });

  it("limits horizontal flight to the footprint plus a 10% margin", () => {
    const f = footprint(flat.t);
    const b = flightBounds(flat.t);
    expect(b.maxX - f.maxX).toBeCloseTo(0.1 * (f.maxX - f.minX), 9);
    expect(f.minZ - b.minZ).toBeCloseTo(0.1 * (f.maxZ - f.minZ), 9);
    const p = params(flat.t, { speed: 1000 });
    const start: Vec3 = { ...centre, y: 50 };
    const east = stepFlight(flat.t, start, HEAD_EAST, { ...idle, forward: 1 }, 10, p).position;
    expect(east.x).toBeCloseTo(b.maxX, 9);
    const north = stepFlight(flat.t, start, { fx: 0, fz: -1 }, { ...idle, forward: 1 }, 10, p).position;
    expect(north.z).toBeCloseTo(b.minZ, 9);
    // Outside the footprint (inside the margin) there is no surface: no clamp.
    const outside = stepFlight(flat.t, { x: b.maxX, y: -100, z: centre.z }, HEAD_EAST, idle, 1, p);
    expect(outside.position.y).toBe(-100);
  });

  it("terrain-follow holds the configured clearance and Space/Shift adjust it", () => {
    const hills = terrain(metadata(21, 21), grid(21, 21, (c, r) => 100 + 10 * Math.sin(c / 3) + r));
    const p = params(hills.t, { speed: 5, follow: true, followClearance: 12 });
    let pos: Vec3 = { ...worldOf(hills.t, 2, 10), y: 999 };
    let clearance = p.followClearance;
    for (let i = 0; i < 30; i++) {
      const next = stepFlight(hills.t, pos, HEAD_EAST, { ...idle, forward: 1 }, 0.1, {
        ...p,
        followClearance: clearance,
      });
      pos = next.position;
      clearance = next.followClearance;
      expect(pos.y - groundHeightAt(hills.t, pos.x, pos.z)!).toBeCloseTo(12, 6);
    }
    const raised = stepFlight(hills.t, pos, HEAD_EAST, { ...idle, up: 1 }, 1, {
      ...p,
      followClearance: 12,
    });
    expect(raised.followClearance).toBeCloseTo(17, 6);
    expect(raised.position.y - groundHeightAt(hills.t, pos.x, pos.z)!).toBeCloseTo(17, 6);
    const lowered = stepFlight(hills.t, pos, HEAD_EAST, { ...idle, up: -1 }, 100, p);
    expect(lowered.followClearance).toBe(p.minClearance);
  });
});

describe("speed control", () => {
  it("+/- multiply by 1.5 within limits", () => {
    expect(changeSpeed(10, 10, 1)).toBeCloseTo(10 * SPEED_STEP);
    expect(changeSpeed(10, 10, -1)).toBeCloseTo(10 / SPEED_STEP);
    expect(changeSpeed(10 * SPEED_RANGE, 10, 1)).toBe(10 * SPEED_RANGE);
    expect(changeSpeed(10 / SPEED_RANGE, 10, -1)).toBe(10 / SPEED_RANGE);
  });
});

describe("entry pose", () => {
  it("is deterministic: south-edge centre, clearance above the surface, looking at the centre", () => {
    const { t } = terrain(metadata(9, 7), grid(9, 7, NORTH_UP_PLANE));
    const pose = entryPose(t, 25);
    const south = worldOf(t, 4, 6);
    const centre = worldOf(t, 4, 3);
    expect(pose.position.x).toBeCloseTo(south.x, 9);
    expect(pose.position.z).toBeCloseTo(south.z, 9);
    expect(pose.position.y).toBeCloseTo(groundHeightAt(t, south.x, south.z)! + 25, 6);
    expect(pose.lookAt).toEqual({ x: centre.x, y: pose.position.y, z: centre.z });
    expect(entryPose(t, 25)).toEqual(pose);
    // Looking north.
    const h = headingFromDirection(
      pose.lookAt.x - pose.position.x,
      pose.lookAt.z - pose.position.z,
      HEAD_EAST,
    );
    expect(headingDegrees(t, h)).toBeCloseTo(0, 9);
  });

  it("uses the highest rendered vertex when the entry point has no surface", () => {
    const raw = grid(5, 5, NORTH_UP_PLANE);
    for (let c = 0; c < 5; c++) raw[4 * 5 + c] = Number.NaN; // bottom row NoData
    const { t } = terrain(metadata(5, 5, { nodata_present: true }), raw);
    let highest = -Infinity;
    for (let i = 0; i < t.worldY.length; i++) if (t.valid[i]) highest = Math.max(highest, t.worldY[i]);
    expect(entryPose(t, 10).position.y).toBeCloseTo(highest + 10, 6);
  });
});

describe("heading", () => {
  it("is clockwise from north for a north-up (negative cellY) grid", () => {
    const { t } = terrain(metadata(3, 3), grid(3, 3, () => 1));
    expect(headingDegrees(t, { fx: 0, fz: 1 })).toBeCloseTo(0); // decreasing row = +z here
    expect(headingDegrees(t, { fx: 1, fz: 0 })).toBeCloseTo(90);
    expect(headingDegrees(t, { fx: 0, fz: -1 })).toBeCloseTo(180);
    expect(headingDegrees(t, { fx: -1, fz: 0 })).toBeCloseTo(270);
  });

  it("uses the image-top as north for a local-pixel (positive cellY) grid", () => {
    const { t } = terrain(
      metadata(3, 3, { is_georeferenced: false, cell_size_x: 1, cell_size_y: 1 }),
      grid(3, 3, () => 1),
    );
    expect(headingDegrees(t, { fx: 0, fz: -1 })).toBeCloseTo(0);
    expect(headingFromDirection(0, 0, HEAD_EAST)).toBe(HEAD_EAST);
  });
});

describe("readout semantics", () => {
  it("DSM: map coordinate in the local CRS, ground value and clearance with exaggeration removed", () => {
    const meta = metadata(6, 5);
    const raw = grid(6, 5, NORTH_UP_PLANE);
    const exaggeration = 2.5;
    const { t } = terrain(meta, raw, exaggeration);
    const at = worldOf(t, 2.25, 1.5);
    const ground = groundHeightAt(t, at.x, at.z)!;
    const d = clearanceDefaults(t, "elevation", exaggeration);
    expect(d.minClearance).toBeCloseTo(DSM_MIN_CLEARANCE * exaggeration);
    const p = { speed: 3, minClearance: d.minClearance, follow: true, followClearance: 7 };
    const r = flightReadout(t, { x: at.x, y: ground + 7.5 * exaggeration, z: at.z }, HEAD_EAST, p, {
      kind: "elevation",
      exaggeration,
      isGeoreferenced: true,
      localCrs: "EPSG:32633",
      originX: 500000,
      originY: 4649984,
      sourceWidth: 6,
      sourceHeight: 5,
    });
    // Grid position (2.25, 1.5) measured from the centre of cell (0, 0).
    expect(r.mapX).toBeCloseTo(500000 + (2.25 + 0.5) * 2, 6);
    expect(r.mapY).toBeCloseTo(4649984 - (1.5 + 0.5) * 2, 6);
    expect(r.crs).toBe("EPSG:32633");
    expect(r.pixelCol).toBeNull();
    expect(r.groundValue).toBeCloseTo(NORTH_UP_PLANE(2.25, 1.5), 4);
    expect(r.heightAboveGround).toBeCloseTo(7.5, 6);
    expect(r.minClearance).toBeCloseTo(DSM_MIN_CLEARANCE, 9);
    expect(r.groundLabel).toMatch(/Ground elevation/);
    expect(r.clearanceUnits).toBe("reference units");
    expect(r.speedUnits).toBe("map units/s");
    expect(r.headingDeg).toBeCloseTo(90);
    expect(r.follow).toBe(true);
  });

  it("relative depth: labelled relative/unitless, never elevation; local pixel coordinates", () => {
    const meta = metadata(8, 6, {
      height_kind: "relative_depth",
      source_artifact_type: "relative_depth",
      is_georeferenced: false,
      local_crs: null,
      origin_x: 0,
      origin_y: 0,
      cell_size_x: 1,
      cell_size_y: 1,
      source_width: 16,
      source_height: 12,
    });
    const raw = grid(8, 6, (c, r) => 0.2 + 0.1 * c + 0.05 * r);
    const { t } = terrain(meta, raw, 4);
    const d = clearanceDefaults(t, "relative_depth", 4);
    expect(d.minClearance).toBeCloseTo(0.01 * 8);
    const at = worldOf(t, 3, 2);
    const ground = groundHeightAt(t, at.x, at.z)!;
    const r = flightReadout(
      t,
      { x: at.x, y: ground + 1.25, z: at.z },
      HEAD_EAST,
      { speed: 2, minClearance: d.minClearance, follow: false, followClearance: 1 },
      {
        kind: "relative_depth",
        exaggeration: 4,
        isGeoreferenced: false,
        localCrs: null,
        originX: 0,
        originY: 0,
        sourceWidth: 16,
        sourceHeight: 12,
      },
    );
    expect(r.mapX).toBeNull();
    // Centre of grid cell (3, 2) in continuous source pixels: (3.5 * 16/8, 2.5 * 12/6).
    expect(r.pixelCol).toBeCloseTo(7, 9);
    expect(r.pixelRow).toBeCloseTo(5, 9);
    expect(r.groundValue).toBeCloseTo(0.2 + 0.1 * 3 + 0.05 * 2, 5);
    expect(r.groundLabel).toBe("Relative depth below (unitless, display grid)");
    expect(r.groundLabel).not.toMatch(/elevation/i);
    expect(r.heightAboveGround).toBeCloseTo(1.25, 6); // visual units, no exaggeration removal
    expect(r.clearanceUnits).toMatch(/visual units/);
    expect(r.speedUnits).toBe("grid units/s");
  });

  it("reports no ground and no clearance over no surface", () => {
    const { t } = terrain(metadata(4, 4), grid(4, 4, () => 5));
    const b = flightBounds(t);
    const r = flightReadout(
      t,
      { x: b.maxX, y: 10, z: 0 },
      HEAD_EAST,
      { speed: 1, minClearance: 3, follow: false, followClearance: 3 },
      {
        kind: "elevation",
        exaggeration: 1.5,
        isGeoreferenced: true,
        localCrs: "EPSG:32633",
        originX: 0,
        originY: 0,
        sourceWidth: 4,
        sourceHeight: 4,
      },
    );
    expect(r.groundValue).toBeNull();
    expect(r.heightAboveGround).toBeNull();
  });
});
