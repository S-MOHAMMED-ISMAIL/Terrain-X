import * as THREE from "three";

// A camera looking straight down the world Y axis at a mesh built by
// buildTerrainGeometry (terrainMesh.ts: raster row -> world Z, raster col ->
// world X, elevation -> world Y — see that function's own docstring for the
// full derivation) needs an explicit `up` vector: THREE.Camera.lookAt is
// degenerate when the view direction is parallel to the default `up` of
// (0,1,0) (exactly the case here, since "straight down" IS the Y axis), so
// leaving `up` at its default produces an undefined/unstable on-screen
// rotation. World -Z is chosen so distant terrain (small Z, image row 0)
// reads toward the TOP of the screen and foreground terrain (large Z, image
// row height-1) reads toward the BOTTOM — the natural "far away at the top
// of a map" reading — verified empirically against a real click sweep in
// TerrainView3D's browser acceptance check, not assumed from this comment
// alone. World +X still maps to screen-right (see the deterministic test in
// terrainTopDownCamera.test.ts), matching col increasing left-to-right —
// this camera never mirrors or rotates the mesh itself, only how it's
// viewed.
export const TOP_DOWN_CAMERA_UP = new THREE.Vector3(0, 0, -1);

/**
 * Configures a real THREE.OrthographicCamera as a true top-down view of the
 * REAL terrain footprint — never a new/rotated/mirrored mesh, only a camera
 * change (see TerrainView3D.tsx, the only caller).
 *
 * Takes the mesh's REAL, measured world-space bounding box (`THREE.Box3`,
 * e.g. from `new THREE.Box3().setFromObject(mesh)`) rather than an assumed
 * "centered at the origin, ±halfWidth/±halfHeight" footprint — found by real
 * browser verification against an actual calibrated/georeferenced DSM
 * fixture with a NEGATIVE `cell_size_y` (a common raster convention: row 0
 * is the top/highest-Y scanline, so Y resolution is stored negative).
 * terrainMesh.ts uses the raw signed cell size for vertex positions
 * (`worldZ = (row + 0.5) * cellY`), so a negative cellY makes the mesh's real
 * world-Z span strictly negative after centering — NOT symmetric around 0
 * the way `halfWidth`/`halfHeight` alone would suggest. The existing
 * perspective camera's generous distance/FOV masks that offset; this
 * orthographic camera's tight, exact-fit frustum does not, and previously
 * ended up looking almost entirely at empty space for that real fixture.
 * Using the mesh's own real, measured bounding box instead of a formula
 * derived from grid dimensions/cell size fixes this for any sign
 * convention, without needing to special-case cell-size sign anywhere.
 *
 * Frames the FULL footprint with a small margin, "contain"-fit to the
 * viewport's own `aspect` so neither edge is ever cropped regardless of
 * panel shape. Deterministic given the same box + aspect, so this is
 * directly unit-testable without a WebGL context or React (see
 * terrainTopDownCamera.test.ts).
 */
export function configureTopDownCamera(
  camera: THREE.OrthographicCamera,
  worldBounds: THREE.Box3,
  aspect: number,
): void {
  const center = worldBounds.getCenter(new THREE.Vector3());
  const size = worldBounds.getSize(new THREE.Vector3());

  let viewHalfWidth = Math.max(size.x / 2, 1) * 1.05;
  let viewHalfHeight = Math.max(size.z / 2, 1) * 1.05;
  if (viewHalfWidth / viewHalfHeight > aspect) {
    viewHalfHeight = viewHalfWidth / aspect;
  } else {
    viewHalfWidth = viewHalfHeight * aspect;
  }
  // Orthographic projection makes the exact altitude visually irrelevant
  // (it doesn't affect scale) — this only needs to safely clear the
  // mesh's real, measured highest point and stay within near/far.
  const clearance = Math.max(viewHalfWidth, viewHalfHeight, 10) * 2 + 100;
  const altitude = worldBounds.max.y + clearance;

  camera.left = -viewHalfWidth;
  camera.right = viewHalfWidth;
  camera.top = viewHalfHeight;
  camera.bottom = -viewHalfHeight;
  camera.near = 0.1;
  // Comfortably brackets the real vertical span (altitude down to the
  // mesh's real lowest point) plus the same clearance margin used above.
  camera.far = altitude - worldBounds.min.y + clearance;
  camera.position.set(center.x, altitude, center.z);
  camera.up.copy(TOP_DOWN_CAMERA_UP);
  camera.lookAt(center.x, center.y, center.z);
  camera.updateProjectionMatrix();
  // Makes matrixWorldInverse immediately correct even before the next
  // render frame — relevant because a raycast can, in principle, happen
  // against this camera before WebGLRenderer.render() has run with it even
  // once (see TerrainView3D.tsx's onClick, which reads whichever camera is
  // currently active).
  camera.updateMatrixWorld(true);
}
