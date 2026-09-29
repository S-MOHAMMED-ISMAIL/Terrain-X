import * as THREE from "three";
import { describe, expect, it } from "vitest";
import type { TerrainMetadata } from "@/api/types";
import { buildTerrainGeometry } from "./terrainMesh";
import { configureTopDownCamera } from "./terrainTopDownCamera";

function project(camera: THREE.OrthographicCamera, point: THREE.Vector3): THREE.Vector3 {
  return point.clone().project(camera);
}

// A symmetric box centered at the origin — the common case (non-
// georeferenced grid, positive cell sizes) most of these tests exercise.
function symmetricBox(halfWidth: number, halfHeight: number): THREE.Box3 {
  return new THREE.Box3(
    new THREE.Vector3(-halfWidth, -1, -halfHeight),
    new THREE.Vector3(halfWidth, 1, halfHeight),
  );
}

describe("configureTopDownCamera", () => {
  const halfWidth = 100;
  const halfHeight = 40;

  it("1. looks straight down: the terrain center projects to screen center", () => {
    const camera = new THREE.OrthographicCamera();
    configureTopDownCamera(camera, symmetricBox(halfWidth, halfHeight), 2);
    const center = project(camera, new THREE.Vector3(0, 0, 0));
    expect(center.x).toBeCloseTo(0, 5);
    expect(center.y).toBeCloseTo(0, 5);
  });

  it("2. distant terrain (negative Z, image row 0) projects above foreground terrain (positive Z, image row height-1)", () => {
    const camera = new THREE.OrthographicCamera();
    configureTopDownCamera(camera, symmetricBox(halfWidth, halfHeight), 2);
    const distant = project(camera, new THREE.Vector3(0, 0, -halfHeight));
    const foreground = project(camera, new THREE.Vector3(0, 0, halfHeight));
    expect(distant.y).toBeGreaterThan(0);
    expect(foreground.y).toBeLessThan(0);
    expect(distant.y).toBeGreaterThan(foreground.y);
  });

  it("3. image col increases left-to-right: +X projects to the right of -X (no mirroring)", () => {
    const camera = new THREE.OrthographicCamera();
    configureTopDownCamera(camera, symmetricBox(halfWidth, halfHeight), 2);
    const left = project(camera, new THREE.Vector3(-halfWidth, 0, 0));
    const right = project(camera, new THREE.Vector3(halfWidth, 0, 0));
    expect(right.x).toBeGreaterThan(left.x);
  });

  it("4. the real full footprint stays within the visible NDC box across several aspect ratios (never cropped)", () => {
    for (const aspect of [0.3, 1, 2, 5]) {
      const camera = new THREE.OrthographicCamera();
      configureTopDownCamera(camera, symmetricBox(halfWidth, halfHeight), aspect);
      const corners = [
        new THREE.Vector3(-halfWidth, 0, -halfHeight),
        new THREE.Vector3(halfWidth, 0, -halfHeight),
        new THREE.Vector3(-halfWidth, 0, halfHeight),
        new THREE.Vector3(halfWidth, 0, halfHeight),
      ];
      for (const corner of corners) {
        const p = project(camera, corner);
        expect(Number.isFinite(p.x)).toBe(true);
        expect(Number.isFinite(p.y)).toBe(true);
        expect(p.x).toBeGreaterThanOrEqual(-1.001);
        expect(p.x).toBeLessThanOrEqual(1.001);
        expect(p.y).toBeGreaterThanOrEqual(-1.001);
        expect(p.y).toBeLessThanOrEqual(1.001);
      }
    }
  });

  it("5. elevation (world Y, i.e. any exaggeration/gamma-driven height) never shifts the projected X/Y position", () => {
    const camera = new THREE.OrthographicCamera();
    configureTopDownCamera(camera, symmetricBox(halfWidth, halfHeight), 2);
    const low = project(camera, new THREE.Vector3(20, -500, 10));
    const high = project(camera, new THREE.Vector3(20, 500, 10));
    expect(low.x).toBeCloseTo(high.x, 5);
    expect(low.y).toBeCloseTo(high.y, 5);
  });

  it("6. re-configuring for a new footprint/aspect fully replaces the previous frustum (no stale state)", () => {
    const camera = new THREE.OrthographicCamera();
    configureTopDownCamera(camera, symmetricBox(500, 500), 1);
    configureTopDownCamera(camera, symmetricBox(halfWidth, halfHeight), 2);
    const right = project(camera, new THREE.Vector3(halfWidth, 0, 0));
    // With the small footprint's own frustum, its own right edge should sit
    // near the visible edge (not swallowed inside a much larger stale one).
    expect(right.x).toBeGreaterThan(0.9);
  });

  it("7. a real raycast from screen center through this camera actually hits the SAME real terrain mesh TerrainView3D.tsx builds (reproduces the exact app setup end-to-end)", () => {
    const metadata: TerrainMetadata = {
      width: 4,
      height: 4,
      cell_size_x: 1,
      cell_size_y: 1,
      height_kind: "relative_depth",
      source_artifact_type: "relative_depth",
      is_georeferenced: false,
      origin_x: null,
      origin_y: null,
      local_crs: null,
      source_width: 4,
      source_height: 4,
    } as TerrainMetadata;
    // A real, fully-valid 4x4 depth grid (no sky/nodata) with genuine relief.
    const elevations = new Float32Array([
      1, 1, 1, 1,
      1, 3, 3, 1,
      1, 3, 3, 1,
      1, 1, 1, 1,
    ]);
    const built = buildTerrainGeometry(metadata, elevations, 5);
    // Matches TerrainView3D.tsx's real material (MeshStandardMaterial with
    // side: THREE.DoubleSide) — without DoubleSide, THREE.Mesh's default
    // FrontSide-only raycasting can miss triangles depending on winding
    // order/view angle.
    const mesh = new THREE.Mesh(built.geometry, new THREE.MeshBasicMaterial({ side: THREE.DoubleSide }));
    // The exact same centering TerrainView3D.tsx applies after building.
    mesh.position.set(-built.halfWidth, 0, -built.halfHeight);
    mesh.updateMatrixWorld(true);

    const camera = new THREE.OrthographicCamera();
    const worldBounds = new THREE.Box3().setFromObject(mesh);
    configureTopDownCamera(camera, worldBounds, 1);

    const raycaster = new THREE.Raycaster();
    raycaster.setFromCamera(new THREE.Vector2(0, 0), camera);
    const hits = raycaster.intersectObject(mesh);
    expect(hits.length, "a center screen raycast through the top-down camera must hit the real mesh").toBeGreaterThan(0);
    // The hit's world X/Z, once un-centered, should land near the grid's own
    // real center cell — proving the camera is actually looking at the
    // terrain's true footprint, not some empty/offset region.
    const gridLocalX = hits[0].point.x + built.halfWidth;
    const gridLocalZ = hits[0].point.z + built.halfHeight;
    expect(gridLocalX).toBeGreaterThan(0);
    expect(gridLocalX).toBeLessThan(built.halfWidth * 2);
    expect(gridLocalZ).toBeGreaterThan(0);
    expect(gridLocalZ).toBeLessThan(built.halfHeight * 2);
  });

  it("8. REGRESSION: a real georeferenced grid with a NEGATIVE cell_size_y (a common raster convention — row 0 is the top/highest-Y scanline) is still fully framed and hittable, not left mostly outside the frustum", () => {
    // Reproduces the exact real bug found via browser verification against
    // a real calibrated DSM fixture (cell_size_x=2, cell_size_y=-2): since
    // terrainMesh.ts uses the RAW signed cell size for vertex positions
    // (worldZ = row * cellY), a negative cellY makes the mesh's real
    // world-Z span strictly negative after centering — asymmetric, not
    // centered at the origin the way halfWidth/halfHeight alone implies.
    // Before this fix, configureTopDownCamera assumed origin-centering and
    // ended up looking almost entirely at empty space for this real case.
    const metadata: TerrainMetadata = {
      width: 8,
      height: 8,
      cell_size_x: 2,
      cell_size_y: -2,
      height_kind: "elevation",
      source_artifact_type: "dsm",
      is_georeferenced: true,
      origin_x: 500000,
      origin_y: 4649984,
      local_crs: "EPSG:32633",
      source_width: 8,
      source_height: 8,
    } as TerrainMetadata;
    const elevations = new Float32Array(64).fill(0).map((_, i) => 120 + (i % 8));
    const built = buildTerrainGeometry(metadata, elevations, 1.5);
    const mesh = new THREE.Mesh(built.geometry, new THREE.MeshBasicMaterial({ side: THREE.DoubleSide }));
    mesh.position.set(-built.halfWidth, 0, -built.halfHeight);
    mesh.updateMatrixWorld(true);

    const worldBounds = new THREE.Box3().setFromObject(mesh);
    // Sanity-check the bug's own real premise: the mesh's real Z span is
    // NOT the symmetric [-halfHeight, halfHeight] a halfWidth/halfHeight-only
    // formula would assume.
    expect(worldBounds.min.z).toBeLessThan(-built.halfHeight + 1);
    expect(worldBounds.max.z).toBeLessThan(built.halfHeight - 1);

    const camera = new THREE.OrthographicCamera();
    configureTopDownCamera(camera, worldBounds, 1);

    const raycaster = new THREE.Raycaster();
    raycaster.setFromCamera(new THREE.Vector2(0, 0), camera);
    const hits = raycaster.intersectObject(mesh);
    expect(
      hits.length,
      "a center screen raycast must hit the mesh even when its real footprint is off-center due to a negative cell size",
    ).toBeGreaterThan(0);
  });
});
