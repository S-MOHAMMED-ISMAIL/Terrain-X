import { describe, expect, it } from "vitest";
import type { TerrainContext } from "@/api/types";
import {
  isElevationTerrain,
  isRelativeTerrain,
  RELATIVE_TERRAIN_NOTICE,
  textureAvailable,
  textureLabel,
} from "./terrainAvailability";

function makeTerrain(overrides: Partial<TerrainContext>): TerrainContext {
  return {
    available: true,
    unavailable_reason: null,
    artifact_id: "artifact-1",
    analysis_job_id: "job-1",
    height_kind: "elevation",
    source_artifact_type: "dsm",
    width: 64,
    height: 64,
    texture_compatible: true,
    texture_unavailable_code: null,
    texture_unavailable_reason: null,
    is_georeferenced: true,
    crs: "EPSG:32633",
    bounds: null,
    min_elevation: 100,
    max_elevation: 130,
    min_height_value: 100,
    max_height_value: 130,
    ...overrides,
  };
}

describe("isRelativeTerrain / isElevationTerrain", () => {
  it("accepts relative_depth as a valid 3D terrain source", () => {
    const terrain = makeTerrain({
      height_kind: "relative_depth",
      source_artifact_type: "relative_depth",
      min_elevation: null,
      max_elevation: null,
    });
    expect(isRelativeTerrain(terrain)).toBe(true);
    expect(isElevationTerrain(terrain)).toBe(false);
  });

  it("keeps dsm as the calibrated terrain source", () => {
    const terrain = makeTerrain({ height_kind: "elevation", source_artifact_type: "dsm" });
    expect(isElevationTerrain(terrain)).toBe(true);
    expect(isRelativeTerrain(terrain)).toBe(false);
  });

  it("reports neither kind when terrain is unavailable, regardless of a stray height_kind value", () => {
    const terrain = makeTerrain({ available: false, height_kind: null });
    expect(isRelativeTerrain(terrain)).toBe(false);
    expect(isElevationTerrain(terrain)).toBe(false);
  });
});

describe("RELATIVE_TERRAIN_NOTICE", () => {
  it("explicitly states the required scientific-honesty wording", () => {
    expect(RELATIVE_TERRAIN_NOTICE).toContain("NOT elevation");
    expect(RELATIVE_TERRAIN_NOTICE).toContain("NOT a DSM");
  });
});

describe("textureLabel", () => {
  it("labels relative terrain texture honestly, never as an orthomosaic/orthophoto", () => {
    const terrain = makeTerrain({ height_kind: "relative_depth" });
    const label = textureLabel(terrain);
    expect(label).toBe("RGB texture on relative terrain");
    expect(label.toLowerCase()).not.toContain("ortho");
  });

  it("uses the plain calibrated label for elevation-backed terrain", () => {
    const terrain = makeTerrain({ height_kind: "elevation" });
    expect(textureLabel(terrain)).toBe("Show RGB texture");
  });
});

describe("textureAvailable (D2)", () => {
  it("follows the backend's provenance decision, never matching dimensions", () => {
    // Same dimensions as the source, but the backend says the grid was
    // reprojected from a geographic source: no texture.
    const reprojected = makeTerrain({
      width: 64,
      height: 64,
      texture_compatible: false,
      texture_unavailable_code: "reprojected_terrain_grid",
      texture_unavailable_reason: "The terrain grid was reprojected from a geographic source, …",
    });
    expect(textureAvailable(reprojected)).toBe(false);
    expect(textureAvailable(makeTerrain({ texture_compatible: true }))).toBe(true);
    for (const code of ["dimension_mismatch", "crs_mismatch", "transform_mismatch", "not_rgb_uint8"]) {
      expect(
        textureAvailable(makeTerrain({ texture_compatible: false, texture_unavailable_code: code })),
      ).toBe(false);
    }
  });

  it("is false when the terrain is unavailable or compatibility is unknown", () => {
    expect(textureAvailable(makeTerrain({ available: false, texture_compatible: true }))).toBe(false);
    expect(textureAvailable(makeTerrain({ texture_compatible: null }))).toBe(false);
  });
});
