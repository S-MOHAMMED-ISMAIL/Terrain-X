import { describe, expect, it } from "vitest";
import type { TerrainMetadata } from "@/api/types";
import { buildTerrainGeometry } from "./terrainMesh";

// Defaults to the CALIBRATED path ("elevation") — the plain linear,
// unchanged formula — since most tests below are about X/Z/UV coordinate
// mapping and invalid-cell handling, which apply identically regardless of
// height_kind. Tests specifically about the relative-depth gamma curve
// (see the dedicated describe block below) explicitly override height_kind
// to "relative_depth".
function makeMetadata(overrides: Partial<TerrainMetadata> = {}): TerrainMetadata {
  return {
    artifact_id: "test-artifact",
    height_kind: "elevation",
    source_artifact_type: "dsm",
    width: 3,
    height: 3,
    source_width: 3,
    source_height: 3,
    nodata_present: false,
    is_georeferenced: false,
    crs: null,
    local_crs: null,
    origin_x: 0,
    origin_y: 0,
    cell_size_x: 1,
    cell_size_y: 1,
    bounds: null,
    min_elevation: null,
    max_elevation: null,
    min_height_value: null,
    max_height_value: null,
    encoding: "float32",
    ...overrides,
  };
}

// A real, hand-checked 3x3 raster:
//   top    row (row 0) = [1, 2, 3]
//   middle row (row 1) = [4, 5, 6]
//   bottom row (row 2) = [7, 8, 9]
// Row-major, matching exactly how the backend serves /terrain/grid.
const KNOWN_3X3 = new Float32Array([1, 2, 3, 4, 5, 6, 7, 8, 9]);

function positionOf(geometry: ReturnType<typeof buildTerrainGeometry>["geometry"], idx: number) {
  const arr = geometry.getAttribute("position").array as Float32Array;
  return { x: arr[idx * 3], y: arr[idx * 3 + 1], z: arr[idx * 3 + 2] };
}

function uvOf(geometry: ReturnType<typeof buildTerrainGeometry>["geometry"], idx: number) {
  const arr = geometry.getAttribute("uv").array as Float32Array;
  return { u: arr[idx * 2], v: arr[idx * 2 + 1] };
}

describe("buildTerrainGeometry coordinate mapping (real positions, not just presence)", () => {
  it("maps image row 0 (top) to the SMALLEST world Z and row H-1 (bottom) to the LARGEST world Z", () => {
    const built = buildTerrainGeometry(makeMetadata(), KNOWN_3X3, 1);
    const topLeft = positionOf(built.geometry, 0); // row 0, col 0 -> value 1
    const bottomLeft = positionOf(built.geometry, 6); // row 2, col 0 -> value 7
    expect(topLeft.z).toBeLessThan(bottomLeft.z);
    expect(topLeft.z).toBe(0.5); // centre of row 0: 0.5 * cell_size_y (D1)
    expect(bottomLeft.z).toBe(2.5); // centre of row H-1: (height-0.5) * cell_size_y
  });

  it("maps image col 0 (left) to the SMALLEST world X and col W-1 (right) to the LARGEST world X", () => {
    const built = buildTerrainGeometry(makeMetadata(), KNOWN_3X3, 1);
    const topLeft = positionOf(built.geometry, 0); // row 0, col 0
    const topRight = positionOf(built.geometry, 2); // row 0, col 2
    expect(topLeft.x).toBeLessThan(topRight.x);
    expect(topLeft.x).toBe(0.5); // centre of col 0: 0.5 * cell_size_x (D1)
    expect(topRight.x).toBe(2.5); // centre of col W-1: (width-0.5) * cell_size_x
  });

  it("preserves every real elevation value as world Y, unmodified aside from baseline-centering and exaggeration", () => {
    const built = buildTerrainGeometry(makeMetadata(), KNOWN_3X3, 1);
    // baseline = (min + max) / 2 = (1 + 9) / 2 = 5
    const topLeft = positionOf(built.geometry, 0); // value 1
    const center = positionOf(built.geometry, 4); // value 5 (the middle cell)
    const bottomRight = positionOf(built.geometry, 8); // value 9
    expect(topLeft.y).toBeCloseTo(1 - 5);
    expect(center.y).toBeCloseTo(5 - 5);
    expect(bottomRight.y).toBeCloseTo(9 - 5);
    // The real top-to-bottom, near-to-far value ordering must survive into Y.
    expect(topLeft.y).toBeLessThan(bottomRight.y);
  });

  it("image pixel (0,0) [top-left], (W-1,0) [top-right], (0,H-1) [bottom-left] land at the documented world positions", () => {
    const built = buildTerrainGeometry(makeMetadata(), KNOWN_3X3, 1);
    const topLeft = positionOf(built.geometry, 0);
    const topRight = positionOf(built.geometry, 2);
    const bottomLeft = positionOf(built.geometry, 6);
    // Pixel centres (D1): ((col + 0.5) * cellX, y, (row + 0.5) * cellY).
    expect(topLeft).toEqual({ x: 0.5, y: -4, z: 0.5 });
    expect(topRight).toEqual({ x: 2.5, y: -2, z: 0.5 });
    expect(bottomLeft).toEqual({ x: 0.5, y: 2, z: 2.5 });
  });

  it("UV v-coordinate is flipped relative to raster row (the one, singular flip point for texture-space convention)", () => {
    const built = buildTerrainGeometry(makeMetadata(), KNOWN_3X3, 1);
    const topLeftUv = uvOf(built.geometry, 0); // row 0 (image top)
    const bottomLeftUv = uvOf(built.geometry, 6); // row 2 (image bottom)
    // Texel centres of the same pixels (D1): u = (col+0.5)/W, v = 1 - (row+0.5)/H.
    expect(topLeftUv.u).toBeCloseTo(1 / 6, 7);
    expect(topLeftUv.v).toBeCloseTo(5 / 6, 7); // image top -> near texture v=1 (flipY convention)
    expect(bottomLeftUv.u).toBeCloseTo(1 / 6, 7);
    expect(bottomLeftUv.v).toBeCloseTo(1 / 6, 7); // image bottom -> near texture v=0
  });

  it("exaggeration scales only Y, never X or Z", () => {
    const built1x = buildTerrainGeometry(makeMetadata(), KNOWN_3X3, 1);
    const built5x = buildTerrainGeometry(makeMetadata(), KNOWN_3X3, 5);
    const p1 = positionOf(built1x.geometry, 0);
    const p5 = positionOf(built5x.geometry, 0);
    expect(p5.x).toBe(p1.x);
    expect(p5.z).toBe(p1.z);
    expect(p5.y).toBeCloseTo(p1.y * 5);
  });
});

describe("buildTerrainGeometry invalid-cell (sky/nodata) triangle exclusion", () => {
  it("never emits a triangle referencing an invalid (NaN) vertex", () => {
    // A 3x3 grid where the entire top row is invalid (e.g. backend-side
    // sky exclusion — geospatial/sky_mask.py), same shape as KNOWN_3X3.
    const withSky = new Float32Array([NaN, NaN, NaN, 4, 5, 6, 7, 8, 9]);
    const built = buildTerrainGeometry(makeMetadata(), withSky, 1);
    const indices = built.geometry.getIndex()!.array;
    // Vertex indices 0, 1, 2 (the invalid top row) must never appear in
    // the index buffer at all.
    for (const i of indices) {
      expect(i).not.toBe(0);
      expect(i).not.toBe(1);
      expect(i).not.toBe(2);
    }
  });

  it("still renders the real, valid remainder of the grid", () => {
    const withSky = new Float32Array([NaN, NaN, NaN, 4, 5, 6, 7, 8, 9]);
    const built = buildTerrainGeometry(makeMetadata(), withSky, 1);
    const indices = built.geometry.getIndex()!.array;
    // The bottom quad (rows 1-2, all real) must still produce a real triangle pair.
    expect(indices.length).toBeGreaterThan(0);
    expect(built.validCount).toBe(6);
    expect(built.nodataCount).toBe(3);
  });

  it("computes min/max elevation only over valid cells, never letting an invalid cell skew the real range", () => {
    const withSky = new Float32Array([NaN, NaN, NaN, 4, 5, 6, 7, 8, 9]);
    const built = buildTerrainGeometry(makeMetadata(), withSky, 1);
    expect(built.minElevation).toBe(4);
    expect(built.maxElevation).toBe(9);
  });

  it("an all-invalid grid produces zero triangles, not a fabricated flat plane", () => {
    const allInvalid = new Float32Array(9).fill(NaN);
    const built = buildTerrainGeometry(makeMetadata(), allInvalid, 1);
    expect(built.geometry.getIndex()!.array.length).toBe(0);
    expect(built.validCount).toBe(0);
  });
});

describe("buildTerrainGeometry relative-depth gamma visualization (height_kind='relative_depth' ONLY)", () => {
  // 4x5 grid, values 1..20 row-major — large enough for robustDepthRange's
  // P5/P95 picks to differ meaningfully from the raw min/max, so the
  // gamma-vs-linear distinction is actually exercised (P5=2, P95=19 for
  // this exact array — see the "extreme outliers clamp" test below, which
  // depends on that).
  const GRID_4X5 = new Float32Array(Array.from({ length: 20 }, (_, i) => i + 1));
  const relMeta = (overrides: Partial<TerrainMetadata> = {}) =>
    makeMetadata({
      height_kind: "relative_depth",
      source_artifact_type: "relative_depth",
      width: 4,
      height: 5,
      ...overrides,
    });

  function worldYFor(elevationValue: number, geometry: ReturnType<typeof buildTerrainGeometry>["geometry"], grid: Float32Array) {
    const idx = grid.indexOf(elevationValue);
    const arr = geometry.getAttribute("position").array as Float32Array;
    return arr[idx * 3 + 1];
  }

  it("gamma=1 is a pure linear stretch of the real P5-P95 window (not the old raw-min/max centering)", () => {
    const built = buildTerrainGeometry(relMeta(), GRID_4X5, 1, 1);
    // P5=2, P95=19 (see robustDepthRange on this exact sorted 1..20 array).
    const p5 = 2;
    const p95 = 19;
    const robustRange = p95 - p5;
    for (const value of [2, 10, 19]) {
      const normalized = (value - p5) / robustRange;
      const expectedY = (normalized - 0.5) * 1 * robustRange;
      expect(worldYFor(value, built.geometry, GRID_4X5)).toBeCloseTo(expectedY, 5);
    }
  });

  it("gamma=0.5 (default) expands the low end relative to a linear (gamma=1) mapping", () => {
    // A real, below-the-window-midpoint value: normalized < 0.5, so
    // gamma=0.5 (sqrt) must push its visual position HIGHER than the plain
    // linear gamma=1 mapping would — proving the curve actually redistributes
    // contrast toward the low end, not just relabels it.
    const linear = buildTerrainGeometry(relMeta(), GRID_4X5, 1, 1);
    const gamma = buildTerrainGeometry(relMeta(), GRID_4X5, 1, 0.5);
    const value = 6; // normalized = (6-2)/17 ≈ 0.235, well below 0.5
    const yLinear = worldYFor(value, linear.geometry, GRID_4X5);
    const yGamma = worldYFor(value, gamma.geometry, GRID_4X5);
    expect(yGamma).toBeGreaterThan(yLinear);
  });

  it("preserves real elevation ordering end-to-end (monotonicity), for both gamma=1 and gamma=0.5", () => {
    for (const gamma of [1, 0.5, 2]) {
      const built = buildTerrainGeometry(relMeta(), GRID_4X5, 1, gamma);
      const ys = [1, 5, 10, 15, 20].map((v) => worldYFor(v, built.geometry, GRID_4X5));
      for (let i = 1; i < ys.length; i++) {
        expect(ys[i]).toBeGreaterThanOrEqual(ys[i - 1]);
      }
    }
  });

  it("extreme outliers below P5 clamp to the same visual floor as P5 itself, never below it", () => {
    const built = buildTerrainGeometry(relMeta(), GRID_4X5, 1, 0.5);
    const yAt1 = worldYFor(1, built.geometry, GRID_4X5); // below P5=2
    const yAtP5 = worldYFor(2, built.geometry, GRID_4X5); // exactly P5
    expect(yAt1).toBeCloseTo(yAtP5, 5);
  });

  it("the calibrated (elevation) path NEVER applies gamma — changing gamma has zero effect", () => {
    const calibratedMeta = makeMetadata({ height_kind: "elevation", width: 4, height: 5 });
    const withDefaultGamma = buildTerrainGeometry(calibratedMeta, GRID_4X5, 1);
    const withExtremeGamma = buildTerrainGeometry(calibratedMeta, GRID_4X5, 1, 0.01);
    const posA = withDefaultGamma.geometry.getAttribute("position").array as Float32Array;
    const posB = withExtremeGamma.geometry.getAttribute("position").array as Float32Array;
    expect(Array.from(posA)).toEqual(Array.from(posB));
    // And matches the plain linear formula exactly (baseline = raw min/max
    // center — (1+20)/2 = 10.5 — never the P5/P95 center relative-depth uses).
    const yAt10 = worldYFor(10, withDefaultGamma.geometry, GRID_4X5);
    expect(yAt10).toBeCloseTo((10 - 10.5) * 1, 5);
  });
});
