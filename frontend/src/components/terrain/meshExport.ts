// P1-9: 3D terrain mesh export — pure helpers (no React/Three imports).
//
// The GLB is built by the backend from the authoritative terrain grid: cell-
// centre vertices, raw grid values, NoData/sky as holes, no display
// exaggeration/gamma/centring (see geospatial/mesh_export.py). This is a
// DIFFERENT representation from the browser 3D mesh (terrainMesh.ts), which
// is a display/flythrough representation.

/** The only resolutions the backend accepts. */
export const MESH_EXPORT_RESOLUTIONS = [256, 512] as const;
export type MeshExportResolution = (typeof MESH_EXPORT_RESOLUTIONS)[number];

export function meshExportFileName(artifactId: string, resolution: MeshExportResolution): string {
  return `terrainx-terrain-${artifactId}-${resolution}.glb`;
}

export interface GlbSummary {
  vertexCount: number;
  triangleCount: number;
  heightKind: string;
  verticalUnits: string;
  texture: "embedded" | "omitted";
  textureOmittedReason: string | null;
}

/** Reads the export summary from a GLB's JSON chunk (asset.extras.terrainx).
 * Throws on anything that is not a TERRAIN-X terrain GLB. */
export function readGlbSummary(buffer: ArrayBuffer): GlbSummary {
  const view = new DataView(buffer);
  if (buffer.byteLength < 20 || view.getUint32(0, true) !== 0x46546c67) {
    throw new Error("Not a GLB file.");
  }
  if (view.getUint32(4, true) !== 2) throw new Error("Unsupported glTF version.");
  const jsonLength = view.getUint32(12, true);
  if (view.getUint32(16, true) !== 0x4e4f534a) throw new Error("GLB has no JSON chunk.");
  const json = JSON.parse(new TextDecoder().decode(new Uint8Array(buffer, 20, jsonLength)));
  const extras = json?.asset?.extras?.terrainx;
  if (!extras || extras.format !== "terrainx-terrain-mesh") {
    throw new Error("GLB is not a TERRAIN-X terrain export.");
  }
  return {
    vertexCount: extras.vertex_count,
    triangleCount: extras.triangle_count,
    heightKind: extras.height_kind,
    verticalUnits: extras.vertical_units,
    texture: extras.texture,
    textureOmittedReason: extras.texture_omitted_reason ?? null,
  };
}
