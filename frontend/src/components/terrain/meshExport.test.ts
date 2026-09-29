import { describe, expect, it } from "vitest";
import { MESH_EXPORT_RESOLUTIONS, meshExportFileName, readGlbSummary } from "./meshExport";

/** A minimal GLB (header + JSON chunk) built by hand for the reader. */
function glb(json: unknown, magic = 0x46546c67, version = 2): ArrayBuffer {
  let text = JSON.stringify(json);
  while (text.length % 4) text += " ";
  const body = new TextEncoder().encode(text);
  const buffer = new ArrayBuffer(20 + body.length);
  const view = new DataView(buffer);
  view.setUint32(0, magic, true);
  view.setUint32(4, version, true);
  view.setUint32(8, buffer.byteLength, true);
  view.setUint32(12, body.length, true);
  view.setUint32(16, 0x4e4f534a, true);
  new Uint8Array(buffer, 20).set(body);
  return buffer;
}

const extras = {
  format: "terrainx-terrain-mesh",
  vertex_count: 1234,
  triangle_count: 2400,
  height_kind: "relative_depth",
  vertical_units: "unitless",
  texture: "omitted",
  texture_omitted_reason: "Texture not requested.",
};

describe("mesh export helpers", () => {
  it("offers only 256 and 512", () => {
    expect([...MESH_EXPORT_RESOLUTIONS]).toEqual([256, 512]);
  });

  it("names the file after the artifact and resolution", () => {
    expect(meshExportFileName("abc", 512)).toBe("terrainx-terrain-abc-512.glb");
  });

  it("reads the export summary from asset.extras.terrainx", () => {
    expect(readGlbSummary(glb({ asset: { version: "2.0", extras: { terrainx: extras } } }))).toEqual({
      vertexCount: 1234,
      triangleCount: 2400,
      heightKind: "relative_depth",
      verticalUnits: "unitless",
      texture: "omitted",
      textureOmittedReason: "Texture not requested.",
    });
  });

  it("rejects non-GLB data, other glTF versions and foreign GLBs", () => {
    expect(() => readGlbSummary(new ArrayBuffer(8))).toThrow(/Not a GLB/);
    expect(() => readGlbSummary(glb({}, 0x12345678))).toThrow(/Not a GLB/);
    expect(() => readGlbSummary(glb({ asset: { extras: { terrainx: extras } } }, 0x46546c67, 1))).toThrow(
      /version/,
    );
    expect(() => readGlbSummary(glb({ asset: { version: "2.0" } }))).toThrow(/not a TERRAIN-X/);
  });
});
