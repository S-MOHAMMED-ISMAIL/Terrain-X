import { describe, expect, it } from "vitest";
import type { LayerContext, LayerType, SemanticRegion, VisualizationContext } from "@/api/types";
import { findSemanticRegion, formatSample, pickInspectableLayer } from "./inspection";

/**
 * Regression coverage for a real reported bug: clicking the 2D map updated
 * the displayed pixel coordinate (computed purely client-side) but never
 * issued a request to the backend's authoritative
 * `.../visualization/value` endpoint, because the sampling logic keyed
 * off `activeLayer` — which defaults to "rgb" and only changes if a user
 * clicks a layer's *name* in the panel, a separate action from the
 * visibility checkbox a user would naturally use to turn a layer on. These
 * tests exercise `pickInspectableLayer` (the fixed selection logic) and
 * `formatSample` (the value-formatting helper) directly and in isolation —
 * no DOM, no Leaflet, no network — since the project has no jsdom/React
 * Testing Library setup for full component-level interaction tests (see
 * docs/DEVELOPMENT.md's Testing section for why that's a documented,
 * deliberate limitation rather than an oversight).
 */

function makeLayer(overrides: Partial<LayerContext> & { layer_type: LayerType }): LayerContext {
  return {
    display_name: overrides.layer_type,
    available: true,
    unavailable_reason: null,
    artifact_id: "artifact-1",
    analysis_job_id: "job-1",
    width: 64,
    height: 64,
    dtype: "float32",
    nodata: null,
    is_georeferenced: true,
    crs: "EPSG:32633",
    bounds: null,
    min_value: 0,
    max_value: 1,
    map_overlay_bounds: null,
    notes: null,
    is_categorical: false,
    region_count: null,
    legend: null,
    ...overrides,
  };
}

function makeContext(layers: LayerContext[]): VisualizationContext {
  return {
    dataset: {
      id: "dataset-1",
      original_filename: "scene.tif",
      file_type: "tiff",
      width: 64,
      height: 64,
      is_georeferenced: true,
      crs: "EPSG:32633",
      bounds: null,
      bounds_wgs84: null,
    },
    layers,
    terrain: {
      available: false,
      unavailable_reason: null,
      artifact_id: null,
      analysis_job_id: null,
      height_kind: null,
      source_artifact_type: null,
      width: null,
      height: null,
      texture_compatible: null,
      texture_unavailable_code: null,
      texture_unavailable_reason: null,
      is_georeferenced: null,
      crs: null,
      bounds: null,
      min_elevation: null,
      max_elevation: null,
      min_height_value: null,
      max_height_value: null,
    },
    calibration_residuals: {
      available: false,
      unavailable_reason: "No calibration reference (DEM/GCP) was used for this analysis job.",
      artifact_id: null,
      analysis_job_id: null,
      reference_type: null,
      sample_count: null,
    },
  };
}

const HIDDEN_VISIBILITY: Record<LayerType, boolean> = {
  rgb: true,
  relative_depth: false,
  metric_elevation: false,
  dsm: false,
  dtm: false,
  ndsm: false,
  semantic_segmentation: false,
  slope: false,
  aspect: false,
  hillshade: false,
  flood_screening: false,
  landslide_screening: false,
};

describe("pickInspectableLayer", () => {
  it("falls back to a visible scientific layer when activeLayer is still the 'rgb' default (the reported bug)", () => {
    const context = makeContext([
      makeLayer({ layer_type: "rgb" }),
      makeLayer({ layer_type: "relative_depth" }),
    ]);
    const visibility = { ...HIDDEN_VISIBILITY, relative_depth: true };

    const picked = pickInspectableLayer(context, "rgb", visibility);

    expect(picked?.layer_type).toBe("relative_depth");
  });

  it("returns null when no scientific layer is visible (never silently samples rgb)", () => {
    const context = makeContext([
      makeLayer({ layer_type: "rgb" }),
      makeLayer({ layer_type: "relative_depth" }),
    ]);

    const picked = pickInspectableLayer(context, "rgb", HIDDEN_VISIBILITY);

    expect(picked).toBeNull();
  });

  it("respects an explicit activeLayer selection when that layer is visible and available", () => {
    const context = makeContext([
      makeLayer({ layer_type: "relative_depth" }),
      makeLayer({ layer_type: "dsm" }),
    ]);
    const visibility = { ...HIDDEN_VISIBILITY, relative_depth: true, dsm: true };

    const picked = pickInspectableLayer(context, "relative_depth", visibility);

    expect(picked?.layer_type).toBe("relative_depth");
  });

  it("ignores activeLayer if that layer isn't visible, falling back to the topmost visible layer instead", () => {
    const context = makeContext([
      makeLayer({ layer_type: "relative_depth" }),
      makeLayer({ layer_type: "dsm" }),
    ]);
    const visibility = { ...HIDDEN_VISIBILITY, relative_depth: true, dsm: false };

    const picked = pickInspectableLayer(context, "dsm", visibility);

    expect(picked?.layer_type).toBe("relative_depth");
  });

  it("prefers the topmost layer in real map stacking order (dsm over relative_depth) when both are visible", () => {
    const context = makeContext([
      makeLayer({ layer_type: "relative_depth" }),
      makeLayer({ layer_type: "metric_elevation" }),
      makeLayer({ layer_type: "dsm" }),
    ]);
    const visibility = {
      ...HIDDEN_VISIBILITY,
      relative_depth: true,
      metric_elevation: true,
      dsm: true,
    };

    const picked = pickInspectableLayer(context, "rgb", visibility);

    expect(picked?.layer_type).toBe("dsm");
  });

  it("never picks a layer the backend reports as unavailable, even if its visibility toggle is on", () => {
    const context = makeContext([
      makeLayer({ layer_type: "dsm", available: false, unavailable_reason: "Calibration failed." }),
      makeLayer({ layer_type: "relative_depth" }),
    ]);
    const visibility = { ...HIDDEN_VISIBILITY, dsm: true, relative_depth: true };

    const picked = pickInspectableLayer(context, "dsm", visibility);

    expect(picked?.layer_type).toBe("relative_depth");
  });

  it("never picks the rgb layer, even if it's somehow passed as visible and 'active'", () => {
    const context = makeContext([makeLayer({ layer_type: "rgb" })]);

    const picked = pickInspectableLayer(context, "rgb", { ...HIDDEN_VISIBILITY, rgb: true });

    expect(picked).toBeNull();
  });

  it("prefers semantic_segmentation over dsm when both are visible (topmost in real stacking order)", () => {
    const context = makeContext([
      makeLayer({ layer_type: "dsm" }),
      makeLayer({ layer_type: "semantic_segmentation", is_categorical: true, region_count: 3 }),
    ]);
    const visibility = { ...HIDDEN_VISIBILITY, dsm: true, semantic_segmentation: true };

    const picked = pickInspectableLayer(context, "rgb", visibility);

    expect(picked?.layer_type).toBe("semantic_segmentation");
  });

  it("prefers a Phase 8 hazard layer (landslide_screening) over semantic_segmentation and dsm when all are visible", () => {
    const context = makeContext([
      makeLayer({ layer_type: "dsm" }),
      makeLayer({ layer_type: "semantic_segmentation", is_categorical: true, region_count: 3 }),
      makeLayer({ layer_type: "slope" }),
      makeLayer({
        layer_type: "landslide_screening",
        is_categorical: true,
        legend: [{ value: 1, label: "Low" }],
      }),
    ]);
    const visibility = {
      ...HIDDEN_VISIBILITY,
      dsm: true,
      semantic_segmentation: true,
      slope: true,
      landslide_screening: true,
    };

    const picked = pickInspectableLayer(context, "rgb", visibility);

    expect(picked?.layer_type).toBe("landslide_screening");
  });

  it("falls back to slope over dsm/semantic_segmentation when no higher-priority hazard layer is visible", () => {
    const context = makeContext([
      makeLayer({ layer_type: "dsm" }),
      makeLayer({ layer_type: "slope" }),
    ]);
    const visibility = { ...HIDDEN_VISIBILITY, dsm: true, slope: true };

    const picked = pickInspectableLayer(context, "rgb", visibility);

    expect(picked?.layer_type).toBe("slope");
  });
});

describe("formatSample", () => {
  it("reports real NoData explicitly rather than a fabricated number", () => {
    expect(formatSample({ row: 1, col: 1, value: null }, "dsm")).toBe("No data");
  });

  it("labels relative_depth as relative inverse depth, never 'elevation'", () => {
    expect(formatSample({ row: 0, col: 0, value: 2.5 }, "relative_depth")).toBe(
      "Relative inverse depth: 2.500",
    );
  });

  it("labels metric_elevation and dsm as Elevation", () => {
    expect(formatSample({ row: 0, col: 0, value: 118.25 }, "metric_elevation")).toBe(
      "Elevation: 118.250",
    );
    expect(formatSample({ row: 0, col: 0, value: 118.25 }, "dsm")).toBe("Elevation: 118.250");
  });

  it("labels a semantic_segmentation value as a Region ID, never a class name", () => {
    expect(formatSample({ row: 0, col: 0, value: 3 }, "semantic_segmentation")).toBe(
      "Region ID: 3",
    );
  });

  it("reports background explicitly for region ID 0, distinct from real NoData", () => {
    expect(formatSample({ row: 0, col: 0, value: 0 }, "semantic_segmentation")).toBe(
      "Background / no region",
    );
  });

  it("formats slope as real degrees, never a fabricated unit", () => {
    expect(formatSample({ row: 0, col: 0, value: 12.345 }, "slope")).toBe("Slope: 12.3°");
  });

  it("formats aspect as a compass bearing in real degrees", () => {
    expect(formatSample({ row: 0, col: 0, value: 270.0 }, "aspect")).toBe(
      "Aspect: 270.0° (compass bearing)",
    );
  });

  it("reports the real flat-terrain sentinel (-1) as flat, never as a fabricated bearing", () => {
    expect(formatSample({ row: 0, col: 0, value: -1 }, "aspect")).toBe(
      "Aspect: Flat terrain (no defined direction)",
    );
  });

  it("looks up the real persisted class label for a flood_screening value via the legend", () => {
    const legend = [
      { value: 1, label: "Not potentially inundated" },
      { value: 2, label: "Potentially inundated" },
    ];
    expect(formatSample({ row: 0, col: 0, value: 2 }, "flood_screening", legend)).toBe(
      "Flood screening: Potentially inundated",
    );
  });

  it("looks up the real persisted class label for a landslide_screening value via the legend", () => {
    const legend = [
      { value: 1, label: "Low" },
      { value: 2, label: "Moderate" },
      { value: 3, label: "High" },
      { value: 4, label: "Very High" },
    ];
    expect(formatSample({ row: 0, col: 0, value: 3 }, "landslide_screening", legend)).toBe(
      "Landslide screening: High",
    );
  });

  it("falls back to a bare class number for a hazard layer when no legend is provided", () => {
    expect(formatSample({ row: 0, col: 0, value: 2 }, "flood_screening")).toBe(
      "Flood screening: Class 2",
    );
  });
});

describe("findSemanticRegion", () => {
  const regions: SemanticRegion[] = [
    {
      region_id: 1,
      pixel_area: 120,
      bbox_row_min: 0,
      bbox_col_min: 0,
      bbox_row_max: 10,
      bbox_col_max: 10,
      mask_score: 0.92,
      predicted_iou: 0.88,
    },
    {
      region_id: 2,
      pixel_area: 340,
      bbox_row_min: 5,
      bbox_col_min: 5,
      bbox_row_max: 20,
      bbox_col_max: 20,
      mask_score: null,
      predicted_iou: null,
    },
  ];

  it("finds the real persisted metadata for a matching region ID", () => {
    expect(findSemanticRegion(regions, 2)?.pixel_area).toBe(340);
  });

  it("returns null for the background ID (0) without searching", () => {
    expect(findSemanticRegion(regions, 0)).toBeNull();
  });

  it("returns null for an ID with no matching entry, rather than fabricating one", () => {
    expect(findSemanticRegion(regions, 99)).toBeNull();
  });

  it("returns null when no region list is available at all", () => {
    expect(findSemanticRegion(undefined, 1)).toBeNull();
  });
});

describe("P1-3 DTM/nDSM inspection", () => {
  it("labels DTM and nDSM values as estimates, never as measured elevation/height", () => {
    expect(formatSample({ row: 0, col: 0, value: 101.5 }, "dtm")).toBe(
      "Estimated bare-earth elevation: 101.500",
    );
    expect(formatSample({ row: 0, col: 0, value: 6.25 }, "ndsm")).toBe(
      "Estimated height above ground: 6.250",
    );
    expect(formatSample({ row: 0, col: 0, value: null }, "ndsm")).toBe("No data");
  });

  it("samples the visible nDSM/DTM layer above the DSM, as drawn on the map", () => {
    const context = makeContext([
      makeLayer({ layer_type: "dsm" }),
      makeLayer({ layer_type: "dtm" }),
      makeLayer({ layer_type: "ndsm" }),
    ]);
    expect(
      pickInspectableLayer(context, "rgb", { ...HIDDEN_VISIBILITY, dsm: true, ndsm: true })
        ?.layer_type,
    ).toBe("ndsm");
    expect(
      pickInspectableLayer(context, "rgb", { ...HIDDEN_VISIBILITY, dsm: true, dtm: true })
        ?.layer_type,
    ).toBe("dtm");
  });

  it("never samples an unavailable nDSM layer", () => {
    const context = makeContext([
      makeLayer({ layer_type: "dsm" }),
      makeLayer({ layer_type: "ndsm", available: false, unavailable_reason: "gate failed" }),
    ]);
    expect(
      pickInspectableLayer(context, "ndsm", { ...HIDDEN_VISIBILITY, dsm: true, ndsm: true })
        ?.layer_type,
    ).toBe("dsm");
  });
});
