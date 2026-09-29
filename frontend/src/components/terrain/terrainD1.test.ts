import * as THREE from "three";
import { describe, expect, it } from "vitest";
import type { TerrainMetadata } from "@/api/types";
import { flightReadout, flightTerrainFromMesh, groundValueAt } from "./flythrough";
import { type PathWaypoint, waypointWorld } from "./flythroughPath";
import { pixelIndexAt, sourcePixelCentreToLocal } from "./terrainCoords";
import { buildTerrainGeometry } from "./terrainMesh";

// D1: the 3D terrain uses the pixel-centre convention. The fixture is the
// SAME one as backend/tests/test_d1_pixel_centre_georeferencing.py — an
// asymmetric 13 x 9 grid, non-square 3 m x 2 m cells, an origin aligned to
// nothing, and one distinctive value (777) at the off-centre pixel (6, 9).
// Expected coordinates are computed independently (origin + (i + 0.5) *
// cell), never with the code under test. Every test here fails on the old
// corner placement (vertex at origin + i * cell).

const W = 13;
const H = 9;
const X0 = 500123.37;
const Y0 = 4649987.61;
const CX = 3;
const CY = -2;
const SPIKE = { row: 6, col: 9, value: 777 };

function meta(overrides: Partial<TerrainMetadata> = {}): TerrainMetadata {
  return {
    artifact_id: "d1",
    height_kind: "elevation",
    source_artifact_type: "dsm",
    width: W,
    height: H,
    source_width: W,
    source_height: H,
    nodata_present: false,
    is_georeferenced: true,
    crs: "EPSG:32633",
    local_crs: "EPSG:32633",
    origin_x: X0,
    origin_y: Y0,
    cell_size_x: CX,
    cell_size_y: CY,
    bounds: null,
    min_elevation: null,
    max_elevation: null,
    min_height_value: null,
    max_height_value: null,
    encoding: "float32",
    ...overrides,
  };
}

function values(width = W, height = H): Float32Array {
  const v = new Float32Array(width * height);
  for (let r = 0; r < height; r++) {
    for (let c = 0; c < width; c++) v[r * width + c] = 100 + 0.25 * c - 0.5 * r;
  }
  if (width === W && height === H) v[SPIKE.row * W + SPIKE.col] = SPIKE.value;
  return v;
}

/** Independent pixel centre of (row, col) from the raster transform. */
const centre = (row: number, col: number) => ({ x: X0 + (col + 0.5) * CX, y: Y0 + (row + 0.5) * CY });

/** Independent 2D resolution (the backend's floor(~transform * (x, y))). */
const pixelAt = (x: number, y: number) => ({
  row: Math.floor((y - Y0) / CY),
  col: Math.floor((x - X0) / CX),
});

function scene(m: TerrainMetadata = meta(), raw: Float32Array = values()) {
  const built = buildTerrainGeometry(m, raw, 1.5);
  const mesh = new THREE.Mesh(built.geometry, new THREE.MeshBasicMaterial({ side: THREE.DoubleSide }));
  // Exactly as TerrainView3D.tsx places it.
  mesh.position.set(-built.halfWidth, 0, -built.halfHeight);
  mesh.updateMatrixWorld(true);
  const positions = built.geometry.getAttribute("position").array;
  const flight = flightTerrainFromMesh(
    positions,
    raw,
    { width: m.width, height: m.height, cellX: m.cell_size_x ?? 1, cellY: m.cell_size_y ?? 1 },
    built.halfWidth,
    built.halfHeight,
  );
  /** Mesh vertex of (row, col) in the local frame and its world position. */
  const vertex = (row: number, col: number) => {
    const i = (row * m.width + col) * 3;
    const local = { x: positions[i], z: positions[i + 2] };
    return { local, world: { x: local.x - built.halfWidth, y: positions[i + 1], z: local.z - built.halfHeight } };
  };
  return { built, mesh, flight, vertex };
}

/** TerrainView3D's click: raycast straight down, hit -> local -> map. */
function click3D(s: ReturnType<typeof scene>, worldX: number, worldZ: number) {
  const ray = new THREE.Raycaster(new THREE.Vector3(worldX, 1e6, worldZ), new THREE.Vector3(0, -1, 0));
  const hit = ray.intersectObject(s.mesh)[0];
  const localX = hit.point.x + s.built.halfWidth;
  const localZ = hit.point.z + s.built.halfHeight;
  return { localX, localZ, mapX: X0 + localX, mapY: Y0 + localZ };
}

describe("D1 pixel-centre georeferencing (projected, non-square, off-centre)", () => {
  it("5: every rendered mesh vertex is its pixel's centre", () => {
    const s = scene();
    for (let r = 0; r < H; r++) {
      for (let c = 0; c < W; c++) {
        const { local } = s.vertex(r, c);
        expect(X0 + local.x).toBeCloseTo(centre(r, c).x, 6);
        expect(Y0 + local.z).toBeCloseTo(centre(r, c).y, 6);
      }
    }
    // First pixel: its centre, not the raster corner.
    expect(s.vertex(0, 0).local).toEqual({ x: 1.5, z: -1 });
    // Non-square: each axis uses its own cell size.
    expect(s.vertex(1, 1).local).toEqual({ x: 4.5, z: -3 });
  });

  it("6: a 3D click on the spike vertex and the 2D resolution name the same pixel", () => {
    const s = scene();
    const v = s.vertex(SPIKE.row, SPIKE.col);
    // It IS the spike: calibrated world Y = (value - (min + max) / 2) * 1.5, min 96 at (8, 0).
    expect(v.world.y).toBeCloseTo((SPIKE.value - (96 + 777) / 2) * 1.5, 3);
    const hit = click3D(s, v.world.x, v.world.z);
    expect(hit.mapX).toBeCloseTo(centre(SPIKE.row, SPIKE.col).x, 6);
    expect(hit.mapY).toBeCloseTo(centre(SPIKE.row, SPIKE.col).y, 6);
    expect(pixelAt(hit.mapX, hit.mapY)).toEqual({ row: SPIKE.row, col: SPIKE.col });
    // Anywhere inside the spike pixel's rendered area resolves to it too.
    for (const [dx, dz] of [[0.4, 0.4], [-0.4, -0.4], [0.4, -0.4], [-0.4, 0.4]]) {
      const h = click3D(s, v.world.x + dx * CX, v.world.z + dz * CY);
      expect(pixelAt(h.mapX, h.mapY)).toEqual({ row: SPIKE.row, col: SPIKE.col });
    }
  });

  it("8: rendered mesh vertex == GLB vertex (up to the GLB's cell-(0,0)-centre origin)", () => {
    const s = scene();
    // geospatial/mesh_export.py: origin = corner + cell / 2, x = c * cx,
    // z = -r * cy, map_x = origin_x + x, map_y = origin_y - z.
    const glbOrigin = { x: X0 + 0.5 * CX, y: Y0 + 0.5 * CY };
    for (const [r, c] of [[0, 0], [SPIKE.row, SPIKE.col], [H - 1, W - 1], [3, 11]]) {
      const glb = { x: glbOrigin.x + c * CX, y: glbOrigin.y - -r * CY };
      const { local } = s.vertex(r, c);
      expect(X0 + local.x).toBeCloseTo(glb.x, 6);
      expect(Y0 + local.z).toBeCloseTo(glb.y, 6);
    }
  });

  it("9: the flythrough HUD over the spike vertex reports its centre and value", () => {
    const s = scene();
    const v = s.vertex(SPIKE.row, SPIKE.col);
    const r = flightReadout(
      s.flight,
      { x: v.world.x, y: v.world.y + 10, z: v.world.z },
      { fx: 1, fz: 0 },
      { speed: 1, minClearance: 1, follow: false, followClearance: 1 },
      {
        kind: "elevation",
        exaggeration: 1.5,
        isGeoreferenced: true,
        localCrs: "EPSG:32633",
        originX: X0,
        originY: Y0,
        sourceWidth: W,
        sourceHeight: H,
      },
    );
    expect(r.mapX).toBeCloseTo(centre(SPIKE.row, SPIKE.col).x, 6);
    expect(r.mapY).toBeCloseTo(centre(SPIKE.row, SPIKE.col).y, 6);
    expect(r.groundValue).toBeCloseTo(SPIKE.value, 3);
    expect(r.heightAboveGround).toBeCloseTo(10 / 1.5, 6);
  });

  it("10: 2D-map and 3D-click waypoints at the spike coincide on its vertex and value", () => {
    const s = scene();
    const v = s.vertex(SPIKE.row, SPIKE.col);
    const c = centre(SPIKE.row, SPIKE.col);
    // 2D: the local-coordinate endpoint returns map = the centre; the
    // workspace stores local = map - origin.
    const from2D: PathWaypoint = {
      localX: c.x - X0,
      localZ: c.y - Y0,
      mapX: c.x,
      mapY: c.y,
      pixelCol: null,
      pixelRow: null,
    };
    // 3D: the click's local hit; the workspace stores map = origin + local.
    const hit = click3D(s, v.world.x, v.world.z);
    const from3D: PathWaypoint = {
      localX: hit.localX,
      localZ: hit.localZ,
      mapX: X0 + hit.localX,
      mapY: Y0 + hit.localZ,
      pixelCol: null,
      pixelRow: null,
    };
    for (const w of [from2D, from3D]) {
      const p = waypointWorld(s.flight, w);
      expect(p.x).toBeCloseTo(v.world.x, 6);
      expect(p.z).toBeCloseTo(v.world.z, 6);
      expect(groundValueAt(s.flight, p.x, p.z)).toBeCloseTo(SPIKE.value, 3);
      expect(w.mapX!).toBeCloseTo(c.x, 6);
      expect(w.mapY!).toBeCloseTo(c.y, 6);
    }
  });

  it("the 3D texture samples the pixel's own texel centre (as the GLB does)", () => {
    const s = scene();
    const uv = s.built.geometry.getAttribute("uv").array;
    const i = (SPIKE.row * W + SPIKE.col) * 2;
    expect(uv[i]).toBeCloseTo((SPIKE.col + 0.5) / W, 7);
    expect(uv[i + 1]).toBeCloseTo(1 - (SPIKE.row + 0.5) / H, 7);
  });
});

describe("D1 geographic source (reprojected to UTM by the backend)", () => {
  it("11: vertices are the centres of the reprojected grid's own transform", () => {
    // The backend reports the reprojected UTM grid's transform; the mesh
    // must use its centres exactly like a projected grid (no degree maths).
    const m = meta({
      crs: "EPSG:4326",
      local_crs: "EPSG:32633",
      width: 11,
      height: 7,
      source_width: 13,
      source_height: 9,
      origin_x: 612873.5516,
      origin_y: 6658431.2381,
      cell_size_x: 16.37,
      cell_size_y: -19.02,
    });
    const s = scene(m, values(11, 7));
    const { local } = s.vertex(4, 7);
    // Mesh positions are Float32 (local frame, so ~1e-6 m of rounding here).
    expect(m.origin_x! + local.x).toBeCloseTo(612873.5516 + 7.5 * 16.37, 5);
    expect(m.origin_y! + local.z).toBeCloseTo(6658431.2381 - 4.5 * 19.02, 5);
  });
});

describe("D1 relative-depth, non-georeferenced fixture (decimated 16x12 -> 8x6)", () => {
  const m = meta({
    height_kind: "relative_depth",
    source_artifact_type: "relative_depth",
    is_georeferenced: false,
    crs: null,
    local_crs: null,
    origin_x: 0,
    origin_y: 0,
    cell_size_x: 1,
    cell_size_y: 1,
    width: 8,
    height: 6,
    source_width: 16,
    source_height: 12,
  });

  it("14: each vertex is its grid cell's centre in continuous source pixels (the GLB convention)", () => {
    const s = scene(m, values(8, 6));
    const readout = (col: number, row: number) => {
      const v = s.vertex(row, col);
      return flightReadout(
        s.flight,
        { x: v.world.x, y: v.world.y + 1, z: v.world.z },
        { fx: 1, fz: 0 },
        { speed: 1, minClearance: 0.1, follow: false, followClearance: 1 },
        {
          kind: "relative_depth",
          exaggeration: 1,
          isGeoreferenced: false,
          localCrs: null,
          originX: 0,
          originY: 0,
          sourceWidth: 16,
          sourceHeight: 12,
        },
      );
    };
    // GLB (non-georeferenced): pixel_col = 0.5 * step + c * step, step = 2.
    const r = readout(3, 2);
    expect(r.pixelCol).toBeCloseTo(0.5 * 2 + 3 * 2, 9);
    expect(r.pixelRow).toBeCloseTo(0.5 * 2 + 2 * 2, 9);
    expect(readout(0, 0).pixelCol).toBeCloseTo(1, 9);
  });

  it("14: source pixel -> 3D local -> click resolution round-trips exactly", () => {
    for (let row = 0; row < 12; row++) {
      for (let col = 0; col < 16; col++) {
        const p = sourcePixelCentreToLocal(m, row, col);
        expect(p.pixelCol).toBe(col + 0.5);
        // TerrainView3D's non-georeferenced click resolution.
        expect(pixelIndexAt(p.localX * (16 / 8), 16)).toBe(col);
        expect(pixelIndexAt(p.localZ * (12 / 6), 12)).toBe(row);
      }
    }
    expect(pixelIndexAt(-0.2, 16)).toBe(0);
    expect(pixelIndexAt(16.3, 16)).toBe(15);
  });
});
