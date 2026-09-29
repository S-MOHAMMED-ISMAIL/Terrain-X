import { describe, expect, it } from "vitest";
import type {
  CoordinateResult,
  DistanceResult,
  Measurement,
  PointElevationResult,
  PointSlopeResult,
  ProfileResult,
} from "@/api/types";
import {
  formatCoordinate,
  formatMeasurementSummary,
  formatSlopeResult,
  measurementTypeLabel,
} from "./measurementFormat";

function makeCoordinate(overrides: Partial<CoordinateResult> = {}): CoordinateResult {
  return {
    row: 10,
    col: 20,
    is_georeferenced: true,
    crs: "EPSG:32633",
    native_x: 500020.0,
    native_y: 4649964.0,
    wgs84_lon: 15.0001,
    wgs84_lat: 42.0012,
    ...overrides,
  };
}

function makeMeasurement(measurement_type: Measurement["measurement_type"], result_data: unknown): Measurement {
  return {
    id: "m-1",
    project_id: "p-1",
    analysis_job_id: "j-1",
    artifact_id: "a-1",
    user_id: "u-1",
    measurement_type,
    input_data: {},
    result_data: result_data as Record<string, unknown>,
    created_at: "2026-01-01T00:00:00Z",
  };
}

describe("formatCoordinate", () => {
  it("reports real native + WGS84 coordinates for a georeferenced point", () => {
    const text = formatCoordinate(makeCoordinate());
    expect(text).toContain("500020.00");
    expect(text).toContain("EPSG:32633");
    expect(text).toContain("42.001200");
    expect(text).toContain("15.000100");
  });

  it("reports plain pixel coordinates, never a fabricated CRS, when not georeferenced", () => {
    const text = formatCoordinate(
      makeCoordinate({
        is_georeferenced: false,
        crs: null,
        native_x: null,
        native_y: null,
        wgs84_lon: null,
        wgs84_lat: null,
      }),
    );
    expect(text).toBe("Pixel (10, 20) — not georeferenced");
  });

  it("omits the WGS84 clause when reprojection genuinely failed, rather than fabricating one", () => {
    const text = formatCoordinate(makeCoordinate({ wgs84_lat: null, wgs84_lon: null }));
    expect(text).not.toContain("WGS84");
  });
});

describe("measurementTypeLabel", () => {
  it("has a real, distinct label for every measurement type", () => {
    const types: Measurement["measurement_type"][] = ["point_elevation", "distance", "profile", "coordinate"];
    const labels = types.map(measurementTypeLabel);
    expect(new Set(labels).size).toBe(types.length);
  });
});

describe("formatMeasurementSummary", () => {
  it("labels an elevation-kind point measurement as elevation, never relative depth", () => {
    const result: PointElevationResult = {
      row: 1,
      col: 1,
      value: 120.5,
      in_bounds: true,
      value_kind: "elevation",
      units: "unspecified",
      calibration_state: "calibrated",
      artifact_type: "dsm",
      disclaimer: "",
      coordinate: makeCoordinate(),
    };
    const summary = formatMeasurementSummary(makeMeasurement("point_elevation", result));
    expect(summary).toContain("120.500");
    expect(summary).toContain("elevation");
    expect(summary).not.toContain("relative depth");
  });

  it("labels a relative_depth-kind point measurement honestly", () => {
    const result: PointElevationResult = {
      row: 1,
      col: 1,
      value: 0.42,
      in_bounds: true,
      value_kind: "relative_depth",
      units: "relative units",
      calibration_state: "uncalibrated",
      artifact_type: "relative_depth",
      disclaimer: "",
      coordinate: makeCoordinate(),
    };
    const summary = formatMeasurementSummary(makeMeasurement("point_elevation", result));
    expect(summary).toContain("relative depth");
  });

  it("reports real NoData explicitly for a point measurement, never a fabricated number", () => {
    const result: PointElevationResult = {
      row: 1,
      col: 1,
      value: null,
      in_bounds: true,
      value_kind: "elevation",
      units: "unspecified",
      calibration_state: "calibrated",
      artifact_type: "dsm",
      disclaimer: "",
      coordinate: makeCoordinate(),
    };
    expect(formatMeasurementSummary(makeMeasurement("point_elevation", result))).toBe("No data");
  });

  it("formats a georeferenced distance in its real units, never assuming pixels", () => {
    const result: DistanceResult = {
      point1: makeCoordinate(),
      point2: makeCoordinate({ row: 20 }),
      pixel_distance: 10,
      distance: 20.0,
      units: "metre",
      is_georeferenced: true,
      crs: "EPSG:32633",
      reprojected: false,
      local_crs: null,
      calibration_state: "calibrated",
      artifact_type: "dsm",
      disclaimer: "",
    };
    expect(formatMeasurementSummary(makeMeasurement("distance", result))).toBe("20.00 metre");
  });

  it("formats a non-georeferenced distance in pixels, never a fabricated real-world unit", () => {
    const result: DistanceResult = {
      point1: makeCoordinate({ is_georeferenced: false }),
      point2: makeCoordinate({ is_georeferenced: false }),
      pixel_distance: 15.5,
      distance: null,
      units: "pixels",
      is_georeferenced: false,
      crs: null,
      reprojected: false,
      local_crs: null,
      calibration_state: "uncalibrated",
      artifact_type: "dsm",
      disclaimer: "",
    };
    expect(formatMeasurementSummary(makeMeasurement("distance", result))).toBe("15.5 px");
  });

  it("summarizes a profile with its real sample count and total distance", () => {
    const result: ProfileResult = {
      sample_count: 8,
      total_distance: 100.0,
      distance_units: "metre",
      is_georeferenced: true,
      crs: "EPSG:32633",
      reprojected: false,
      local_crs: null,
      value_kind: "elevation",
      value_units: "unspecified",
      calibration_state: "calibrated",
      artifact_type: "dsm",
      disclaimer: "",
      samples: [],
    };
    expect(formatMeasurementSummary(makeMeasurement("profile", result))).toBe(
      "8 samples, 100.00 metre",
    );
  });

  it("summarizes a coordinate measurement with real WGS84 lat/lon when available", () => {
    const result: CoordinateResult = makeCoordinate();
    expect(formatMeasurementSummary(makeMeasurement("coordinate", result))).toBe(
      "42.00120, 15.00010",
    );
  });

  it("summarizes a coordinate measurement with pixel coordinates when not georeferenced", () => {
    const result: CoordinateResult = makeCoordinate({
      is_georeferenced: false,
      wgs84_lat: null,
      wgs84_lon: null,
    });
    expect(formatMeasurementSummary(makeMeasurement("coordinate", result))).toBe("pixel (10, 20)");
  });
});

function makeSlope(overrides: Partial<PointSlopeResult> = {}): PointSlopeResult {
  return {
    row: 7,
    col: 9,
    in_bounds: true,
    value: 12.345,
    value_kind: "slope",
    units: "degrees",
    query_x: 15.0001,
    query_y: 42.0012,
    query_crs: "EPSG:4326",
    coordinate: makeCoordinate({ row: 7, col: 9 }),
    artifact_type: "slope",
    source_artifact_id: "dsm-1",
    source_artifact_type: "dsm",
    reprojected_for_analysis: false,
    method: "Horn (1981) 3x3 weighted finite-difference",
    disclaimer: "Real terrain slope in degrees.",
    ...overrides,
  };
}

describe("P1-4 slope-at-point formatting", () => {
  it("shows the stored slope value in degrees, never as elevation", () => {
    expect(formatSlopeResult(makeSlope())).toBe("Slope: 12.35°");
    expect(formatSlopeResult(makeSlope())).not.toContain("Elevation");
  });

  it("distinguishes NoData from a point outside the slope raster", () => {
    expect(formatSlopeResult(makeSlope({ value: null }))).toBe("No data at this point.");
    expect(formatSlopeResult(makeSlope({ value: null, in_bounds: false, coordinate: null }))).toBe(
      "Outside the slope raster.",
    );
  });

  it("uses the artifact's own unit text if it is ever not degrees", () => {
    expect(formatSlopeResult(makeSlope({ units: "percent" }))).toBe("Slope: 12.35 percent");
  });

  it("labels and summarises saved point_slope measurements", () => {
    expect(measurementTypeLabel("point_slope")).toBe("Slope at point");
    expect(formatMeasurementSummary(makeMeasurement("point_slope", makeSlope()))).toBe(
      "12.35° slope",
    );
    expect(
      formatMeasurementSummary(makeMeasurement("point_slope", makeSlope({ value: null }))),
    ).toBe("No data");
    expect(
      formatMeasurementSummary(
        makeMeasurement("point_slope", makeSlope({ value: null, in_bounds: false })),
      ),
    ).toBe("Outside slope raster");
  });
});
