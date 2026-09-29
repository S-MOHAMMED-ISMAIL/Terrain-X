import * as THREE from "three";
import type { TerrainMetadata } from "@/api/types";
import {
  applyRelativeTerrainGamma,
  DEFAULT_RELATIVE_TERRAIN_GAMMA,
  robustDepthRange,
} from "./terrainExaggeration";

// The same fixed ramp used server-side (geospatial/raster_preview.py) and in
// Legend.tsx — kept in one place per layer type would be nicer, but this is
// the only other place it's needed, so it's duplicated with the same
// documented caveat: an approximation, not a colorimetric match to any
// particular library.
const RAMP_STOPS: [number, [number, number, number]][] = [
  [0.0, [68, 1, 84]],
  [0.14, [72, 40, 120]],
  [0.29, [62, 74, 137]],
  [0.43, [49, 104, 142]],
  [0.57, [38, 130, 142]],
  [0.71, [31, 158, 137]],
  [0.86, [53, 183, 121]],
  [1.0, [253, 231, 37]],
];

function rampColor(t: number): [number, number, number] {
  const clamped = Math.min(1, Math.max(0, t));
  for (let i = 0; i < RAMP_STOPS.length - 1; i++) {
    const [p0, c0] = RAMP_STOPS[i];
    const [p1, c1] = RAMP_STOPS[i + 1];
    if (clamped >= p0 && clamped <= p1) {
      const f = p1 === p0 ? 0 : (clamped - p0) / (p1 - p0);
      return [
        (c0[0] + (c1[0] - c0[0]) * f) / 255,
        (c0[1] + (c1[1] - c0[1]) * f) / 255,
        (c0[2] + (c1[2] - c0[2]) * f) / 255,
      ];
    }
  }
  const last = RAMP_STOPS[RAMP_STOPS.length - 1][1];
  return [last[0] / 255, last[1] / 255, last[2] / 255];
}

export interface BuiltTerrain {
  geometry: THREE.BufferGeometry;
  /** Real finite elevation range actually present in the grid, computed
   * ONLY over valid (non-nodata, non-sky-masked) cells — used to pick a
   * sensible default camera position/near-far planes, never a hardcoded
   * constant. */
  minElevation: number;
  maxElevation: number;
  /** World-space half-extents of the mesh, for framing the camera. */
  halfWidth: number;
  halfHeight: number;
  /** Cells with no real data — either genuine raster nodata, or (for an
   * uncalibrated relative-depth grid) a real cell the backend's sky
   * heuristic excluded (geospatial/sky_mask.py) — never a fabricated
   * peak/valley, never a sine wave, never random terrain. */
  nodataCount: number;
  /** Real cells that DID contribute to the rendered surface — exposed for
   * the dev-only diagnostic overlay (see TerrainView3D.tsx). */
  validCount: number;
}

/**
 * Coordinate convention (see docs/ARCHITECTURE.md's terrain section for the
 * full derivation and TerrainView3D.test.ts / terrainMesh.test.ts for a
 * concrete, position-checking unit test):
 *
 *   vertex (row, col)    = CENTRE of grid cell (row, col) (D1):
 *                          local X = (col + 0.5) * cellX
 *                          local Z = (row + 0.5) * cellY   (before centering)
 *   local (0, 0)         = the grid transform's origin corner, so
 *                          map_x = origin_x + localX, map_y = origin_y + localZ
 *   raster row 0 = image TOP, col 0 = image LEFT; signed cell sizes
 *   elevation            = world Y (Three.js "up" axis)
 *
 * TerrainView3D.tsx re-centers the mesh (`mesh.position.set(-halfWidth, 0,
 * -halfHeight)`) so this origin-relative frame sits centered under the
 * scene origin; centering shifts every axis by a constant and does not
 * change any of the relationships above. Row therefore maps directly and
 * monotonically to Z, and col to X — there is no independent flip applied
 * to the geometry anywhere in this function.
 *
 * UV note: the texture's v-coordinate is deliberately `1 - (row+0.5)/height`,
 * NOT (row+0.5)/height — this is the one, singular, necessary conversion
 * between "row 0 is the image's own top scanline" (this function's/every
 * raster's convention) and "v=1 is the top of a texture" (Three.js/WebGL's
 * default texture-space convention, given TextureLoader's default
 * `flipY = true` — see TerrainView3D.tsx, which never overrides it). This
 * is why the geometry is never flipped to "fix" texture orientation, and
 * the texture is never flipped independently of the geometry: the UV
 * formula IS the single point where that conversion happens, consistently.
 *
 * Builds a real Three.js terrain mesh directly from the actual DSM/
 * relative-depth height grid returned by the backend (see
 * api.getTerrainGrid) — every vertex's height is either a real sampled
 * value or, for an invalid cell (raster nodata, or backend-side sky
 * exclusion for uncalibrated relative depth — see
 * geospatial/sky_mask.py), excluded from the rendered surface entirely
 * (never filled with a fabricated value, never stretched from a
 * neighboring real cell). `exaggeration` only scales the Y axis for
 * on-screen readability — it never modifies the underlying data this
 * function was called with.
 *
 * Height/color mapping — TWO DISTINCT, EXPLICITLY GATED formulas (see
 * `metadata.height_kind`, never inferred any other way):
 *
 *   CALIBRATED (height_kind === "elevation", DSM/metric elevation) —
 *   UNCHANGED, plain linear, centered on the real raw min/max:
 *     baseline = (minElevation + maxElevation) / 2
 *     worldY   = (elevation - baseline) * exaggeration
 *     colorT   = (elevation - minElevation) / (maxElevation - minElevation)
 *
 *   UNCALIBRATED RELATIVE DEPTH (height_kind === "relative_depth") — a
 *   real foreground feature can occupy most of the grid's real P5-P95
 *   depth range, which under the plain linear formula above visually
 *   flattens real (if smaller-magnitude) distant terrain into
 *   near-invisibility (proven against a real hazy aerial photo — see
 *   applyRelativeTerrainGamma's docstring in terrainExaggeration.ts).
 *   Applies a monotonic (order-preserving) gamma curve to a robust
 *   [P5, P95] normalization of the SAME real depth values before scaling:
 *     normalized = clamp((elevation - P5) / (P95 - P5), 0, 1)
 *     visual     = normalized ^ gamma            // gamma < 1 expands the
 *                                                 // low end, compresses
 *                                                 // the high end
 *     worldY     = (visual - 0.5) * exaggeration * (P95 - P5)
 *     colorT     = visual
 *   At gamma = 1 this is a pure linear stretch of the SAME real values
 *   (just P5/P95-centered rather than raw-min/max-centered, for the same
 *   outlier-robustness reason `calculateRelativeTerrainExaggeration`
 *   already uses P5/P95) — gamma only changes how visual contrast is
 *   REDISTRIBUTED across the real range, never which real value maps to a
 *   larger or smaller height than another (see the monotonicity tests in
 *   terrainMesh.test.ts). This never modifies the depth artifact, the
 *   backend-reported min/max, or any measurement/inspection value — every
 *   sampled value shown to a user always comes from a fresh, real backend
 *   API call keyed on the clicked pixel's row/col, never from reading this
 *   mesh's own Y position back (see TerrainView3D.tsx's onClick handler,
 *   which only ever reads `point.x`/`point.z` off a raycast hit).
 */
export function buildTerrainGeometry(
  metadata: TerrainMetadata,
  elevations: Float32Array,
  exaggeration: number,
  relativeDepthGamma: number = DEFAULT_RELATIVE_TERRAIN_GAMMA,
): BuiltTerrain {
  const { width, height, cell_size_x, cell_size_y, height_kind } = metadata;
  const cellX = cell_size_x ?? 1;
  const cellY = cell_size_y ?? 1;
  const isRelativeDepth = height_kind === "relative_depth";

  let minElevation = Infinity;
  let maxElevation = -Infinity;
  let nodataCount = 0;
  const valid = new Uint8Array(width * height);
  for (let i = 0; i < elevations.length; i++) {
    const v = elevations[i];
    if (Number.isFinite(v)) {
      valid[i] = 1;
      if (v < minElevation) minElevation = v;
      if (v > maxElevation) maxElevation = v;
    } else {
      nodataCount++;
    }
  }
  if (!Number.isFinite(minElevation)) {
    minElevation = 0;
    maxElevation = 0;
  }

  // Calibrated: plain linear, raw-min/max-centered (byte-for-byte the
  // original formula). Relative depth: robust [P5, P95] window feeding the
  // gamma curve above — computed from the SAME real elevations array, never
  // altering it.
  const baseline = (minElevation + maxElevation) / 2;
  const range = Math.max(maxElevation - minElevation, 1e-6);
  const robust = isRelativeDepth ? robustDepthRange(elevations) : null;
  const robustRange = robust ? Math.max(robust.max - robust.min, 1e-6) : 1;

  const positions = new Float32Array(width * height * 3);
  const colors = new Float32Array(width * height * 3);

  for (let row = 0; row < height; row++) {
    for (let col = 0; col < width; col++) {
      const idx = row * width + col;
      const rawElevation = elevations[idx];
      // An invalid cell still needs SOME finite position (it may still be
      // referenced as a neighbor's vertex index even though no triangle
      // using it as a corner will be emitted below — see the index-build
      // loop's validity check) — minElevation is a safe, inert placeholder
      // since it is never rendered as part of any triangle.
      const elevation = Number.isFinite(rawElevation) ? rawElevation : minElevation;

      // D1: vertex (row, col) sits at the CENTRE of grid cell (row, col) —
      // the location its value belongs to — in the local frame whose (0, 0)
      // is the grid transform's origin corner (origin_x/origin_y, see
      // geospatial/terrain_grid.py): map = origin + local. Signed cell sizes;
      // Three.js Y is "up", so rows become Z and elevation becomes Y.
      const worldX = (col + 0.5) * cellX;
      const worldZ = (row + 0.5) * cellY;

      let worldY: number;
      let t: number;
      if (isRelativeDepth && robust) {
        const normalized = (elevation - robust.min) / robustRange;
        const visual = applyRelativeTerrainGamma(normalized, relativeDepthGamma);
        worldY = (visual - 0.5) * exaggeration * robustRange;
        t = visual;
      } else {
        worldY = (elevation - baseline) * exaggeration;
        t = (elevation - minElevation) / range;
      }

      positions[idx * 3] = worldX;
      positions[idx * 3 + 1] = worldY;
      positions[idx * 3 + 2] = worldZ;

      const [r, g, b] = rampColor(t);
      colors[idx * 3] = r;
      colors[idx * 3 + 1] = g;
      colors[idx * 3 + 2] = b;
    }
  }

  // Skip any quad (both its triangles) touching an invalid cell — this is
  // what actually keeps sky/nodata OUT of the rendered surface: an invalid
  // vertex still exists in the position buffer (needed for indexing) but
  // is never a corner of any emitted triangle, so no triangle can stretch
  // between real terrain and sky, and no artificial wall/spike/slab is
  // ever created at the boundary.
  const indices: number[] = [];
  for (let row = 0; row < height - 1; row++) {
    for (let col = 0; col < width - 1; col++) {
      const a = row * width + col;
      const b = row * width + col + 1;
      const c = (row + 1) * width + col;
      const d = (row + 1) * width + col + 1;
      if (!valid[a] || !valid[b] || !valid[c] || !valid[d]) continue;
      indices.push(a, c, b, b, c, d);
    }
  }

  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
  geometry.setAttribute("color", new THREE.BufferAttribute(colors, 3));
  geometry.setIndex(indices);

  // UVs so a real, spatially-compatible RGB texture can be mapped onto the
  // same grid (col/row match 1:1 with the texture's own pixel grid) — see
  // this function's own docstring above for why the v-coordinate is
  // flipped here and nowhere else.
  const uvs = new Float32Array(width * height * 2);
  for (let row = 0; row < height; row++) {
    for (let col = 0; col < width; col++) {
      const idx = row * width + col;
      // Texel centre of the same pixel (as in the GLB export).
      uvs[idx * 2] = (col + 0.5) / width;
      uvs[idx * 2 + 1] = 1 - (row + 0.5) / height;
    }
  }
  geometry.setAttribute("uv", new THREE.BufferAttribute(uvs, 2));

  geometry.computeVertexNormals();

  return {
    geometry,
    minElevation,
    maxElevation,
    halfWidth: (width * Math.abs(cellX)) / 2,
    halfHeight: (height * Math.abs(cellY)) / 2,
    nodataCount,
    validCount: width * height - nodataCount,
  };
}
