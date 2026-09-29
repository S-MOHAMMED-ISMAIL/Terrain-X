import { describe, expect, it } from "vitest";
import {
  addMeasurementPoint,
  isMeasurementReady,
  MEASUREMENT_MODE_LABELS,
  type MeasurementMode,
  pointsRemaining,
  POINTS_NEEDED,
} from "./measurementMode";

describe("POINTS_NEEDED / MEASUREMENT_MODE_LABELS", () => {
  it("declares a real label for every mode with a points requirement", () => {
    const modes = Object.keys(POINTS_NEEDED) as MeasurementMode[];
    for (const mode of modes) {
      expect(MEASUREMENT_MODE_LABELS[mode]).toBeTruthy();
    }
  });

  it("off needs zero points; point/coordinate need one; distance/profile need two", () => {
    expect(POINTS_NEEDED.off).toBe(0);
    expect(POINTS_NEEDED.point).toBe(1);
    expect(POINTS_NEEDED.coordinate).toBe(1);
    expect(POINTS_NEEDED.distance).toBe(2);
    expect(POINTS_NEEDED.profile).toBe(2);
  });
});

describe("addMeasurementPoint", () => {
  it("accumulates points up to what the mode needs", () => {
    let points = addMeasurementPoint("distance", [], { row: 0, col: 0 });
    expect(points).toEqual([{ row: 0, col: 0 }]);
    points = addMeasurementPoint("distance", points, { row: 5, col: 5 });
    expect(points).toEqual([
      { row: 0, col: 0 },
      { row: 5, col: 5 },
    ]);
  });

  it("drops the oldest point once the mode's real limit is exceeded, so re-clicking redoes the measurement", () => {
    const twoPoints = [
      { row: 0, col: 0 },
      { row: 5, col: 5 },
    ];
    const afterThirdClick = addMeasurementPoint("distance", twoPoints, { row: 9, col: 9 });
    expect(afterThirdClick).toEqual([
      { row: 5, col: 5 },
      { row: 9, col: 9 },
    ]);
  });

  it("a single-point mode (point/coordinate) always keeps only the latest click", () => {
    const points = addMeasurementPoint("point", [{ row: 1, col: 1 }], { row: 2, col: 2 });
    expect(points).toEqual([{ row: 2, col: 2 }]);
  });

  it("mode 'off' never accumulates any points", () => {
    expect(addMeasurementPoint("off", [{ row: 1, col: 1 }], { row: 2, col: 2 })).toEqual([]);
  });
});

describe("isMeasurementReady / pointsRemaining", () => {
  it("point mode is ready after exactly one click", () => {
    expect(isMeasurementReady("point", [])).toBe(false);
    expect(isMeasurementReady("point", [{ row: 0, col: 0 }])).toBe(true);
  });

  it("distance/profile mode is not ready until two clicks are collected", () => {
    expect(isMeasurementReady("distance", [{ row: 0, col: 0 }])).toBe(false);
    expect(
      isMeasurementReady("distance", [
        { row: 0, col: 0 },
        { row: 1, col: 1 },
      ]),
    ).toBe(true);
  });

  it("pointsRemaining counts down to zero and never goes negative", () => {
    expect(pointsRemaining("distance", [])).toBe(2);
    expect(pointsRemaining("distance", [{ row: 0, col: 0 }])).toBe(1);
    expect(
      pointsRemaining("distance", [
        { row: 0, col: 0 },
        { row: 1, col: 1 },
      ]),
    ).toBe(0);
  });
});

describe("P1-4 slope-at-point mode", () => {
  it("is a one-point mode labelled 'Slope at point'", () => {
    expect(POINTS_NEEDED.slope).toBe(1);
    expect(MEASUREMENT_MODE_LABELS.slope).toBe("Slope at point");
    expect(isMeasurementReady("slope", [{ row: 1, col: 2, lat: 42.0, lng: 15.0 }])).toBe(true);
    expect(pointsRemaining("slope", [])).toBe(1);
  });

  it("keeps the clicked map coordinate with the point, replacing the previous one", () => {
    const first = addMeasurementPoint("slope", [], { row: 1, col: 2, lat: 42.0, lng: 15.0 });
    const second = addMeasurementPoint("slope", first, { row: 3, col: 4, lat: 42.5, lng: 15.5 });
    expect(second).toEqual([{ row: 3, col: 4, lat: 42.5, lng: 15.5 }]);
  });

  it("still works for existing modes without a map coordinate (3D / non-georeferenced)", () => {
    expect(addMeasurementPoint("point", [], { row: 5, col: 6 })).toEqual([{ row: 5, col: 6 }]);
  });
});
