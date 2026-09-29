import { describe, expect, it } from "vitest";
import {
  applyRelativeTerrainGamma,
  calculateRelativeTerrainExaggeration,
  MAX_AUTO_RELATIVE_EXAGGERATION,
  MIN_AUTO_RELATIVE_EXAGGERATION,
  relativeExaggerationSliderRange,
  robustDepthRange,
} from "./terrainExaggeration";

describe("calculateRelativeTerrainExaggeration", () => {
  it("1. normal mountain-like depth range produces a moderate, non-trivial multiplier", () => {
    // Real diagnosed values (job 936a92a7): depth ~-0.13..5.58, grid ~256x144.
    const result = calculateRelativeTerrainExaggeration(-0.13, 5.58, 256, 144);
    expect(result).toBeGreaterThan(1);
    expect(result).toBeLessThan(50);
  });

  it("2. a very small depth range does not explode — clamps to the safety maximum", () => {
    const result = calculateRelativeTerrainExaggeration(1.0, 1.0001, 256, 144);
    expect(result).toBe(MAX_AUTO_RELATIVE_EXAGGERATION);
    expect(Number.isFinite(result)).toBe(true);
  });

  it("3. a very large depth range does not collapse to zero — clamps to the safety minimum", () => {
    const result = calculateRelativeTerrainExaggeration(0, 10_000, 256, 144);
    expect(result).toBe(MIN_AUTO_RELATIVE_EXAGGERATION);
    expect(result).toBeGreaterThan(0);
  });

  it("4. width-dominant terrain scales off the larger (width) dimension", () => {
    const wide = calculateRelativeTerrainExaggeration(0, 5, 1000, 50);
    const square = calculateRelativeTerrainExaggeration(0, 5, 50, 50);
    expect(wide).toBeGreaterThan(square);
  });

  it("5. height-dominant terrain scales off the larger (height) dimension symmetrically with width-dominant", () => {
    const wide = calculateRelativeTerrainExaggeration(0, 5, 1000, 50);
    const tall = calculateRelativeTerrainExaggeration(0, 5, 50, 1000);
    expect(tall).toBeCloseTo(wide, 10);
  });

  it("6. degenerate/zero range (min === max) never divides by zero or returns NaN/Infinity", () => {
    const result = calculateRelativeTerrainExaggeration(2, 2, 256, 144);
    expect(Number.isFinite(result)).toBe(true);
    expect(result).toBe(MAX_AUTO_RELATIVE_EXAGGERATION);
  });

  it("7. output is always a stable finite number across a range of real-world-like inputs", () => {
    const cases: [number, number, number, number][] = [
      [-0.13, 5.58, 256, 144],
      [0, 1, 1, 1],
      [-1000, 1000, 4096, 4096],
      [0, 0, 0, 0],
      [5, -5, 256, 144], // inverted min/max — still must not produce NaN/Infinity
    ];
    for (const [dMin, dMax, w, h] of cases) {
      const result = calculateRelativeTerrainExaggeration(dMin, dMax, w, h);
      expect(Number.isFinite(result)).toBe(true);
      expect(Number.isNaN(result)).toBe(false);
    }
  });

  it("8. output always stays within the documented safety range", () => {
    const cases: [number, number, number, number][] = [
      [-0.13, 5.58, 256, 144],
      [1.0, 1.0001, 256, 144],
      [0, 10_000, 256, 144],
      [0, 5, 1000, 50],
      [0, 5, 50, 1000],
      [2, 2, 256, 144],
    ];
    for (const [dMin, dMax, w, h] of cases) {
      const result = calculateRelativeTerrainExaggeration(dMin, dMax, w, h);
      expect(result).toBeGreaterThanOrEqual(MIN_AUTO_RELATIVE_EXAGGERATION);
      expect(result).toBeLessThanOrEqual(MAX_AUTO_RELATIVE_EXAGGERATION);
    }
  });
});

describe("robustDepthRange", () => {
  it("excludes a low-end outlier tail using percentiles instead of raw min", () => {
    // 100 values: one extreme low outlier, then a uniform run from 0..98.
    const values = new Float32Array([-50, ...Array.from({ length: 99 }, (_, i) => i)]);
    const { min, max } = robustDepthRange(values, 5, 95);
    expect(min).toBeGreaterThan(-50);
    expect(max).toBeLessThanOrEqual(98);
  });

  it("ignores non-finite values entirely", () => {
    const values = new Float32Array([NaN, Infinity, -Infinity, 1, 2, 3, 4, 5]);
    const { min, max } = robustDepthRange(values, 0, 100);
    expect(Number.isFinite(min)).toBe(true);
    expect(Number.isFinite(max)).toBe(true);
    expect(min).toBeGreaterThanOrEqual(1);
    expect(max).toBeLessThanOrEqual(5);
  });

  it("returns {0, 0} for an all-invalid input rather than NaN", () => {
    const values = new Float32Array([NaN, Infinity, -Infinity]);
    const { min, max } = robustDepthRange(values);
    expect(min).toBe(0);
    expect(max).toBe(0);
  });
});

describe("applyRelativeTerrainGamma", () => {
  it("1. gamma=1 is the identity: f(x, 1) === x for several x in [0,1]", () => {
    for (const x of [0, 0.1, 0.25, 0.5, 0.75, 0.9, 1]) {
      expect(applyRelativeTerrainGamma(x, 1)).toBeCloseTo(x, 10);
    }
  });

  it("2. gamma=0.5 matches the exact expected square-root values", () => {
    expect(applyRelativeTerrainGamma(0, 0.5)).toBeCloseTo(0, 10);
    expect(applyRelativeTerrainGamma(1, 0.5)).toBeCloseTo(1, 10);
    expect(applyRelativeTerrainGamma(0.25, 0.5)).toBeCloseTo(0.5, 10);
    expect(applyRelativeTerrainGamma(0.5, 0.5)).toBeCloseTo(0.7071, 4);
    expect(applyRelativeTerrainGamma(0.75, 0.5)).toBeCloseTo(0.866, 3);
  });

  it("3. monotonicity: x1 < x2 < x3 implies f(x1) <= f(x2) <= f(x3), for several gamma values", () => {
    const xs = [0, 0.1, 0.2, 0.35, 0.5, 0.6, 0.75, 0.9, 1];
    for (const gamma of [0.1, 0.5, 1, 2, 5]) {
      const ys = xs.map((x) => applyRelativeTerrainGamma(x, gamma));
      for (let i = 1; i < ys.length; i++) {
        expect(ys[i]).toBeGreaterThanOrEqual(ys[i - 1]);
      }
    }
  });

  it("4. NaN input remains NaN, never a fabricated finite number", () => {
    expect(Number.isNaN(applyRelativeTerrainGamma(NaN, 0.5))).toBe(true);
  });

  it("5. out-of-range [0,1] input is clamped before the gamma curve is applied", () => {
    expect(applyRelativeTerrainGamma(-0.5, 1)).toBeCloseTo(0, 10);
    expect(applyRelativeTerrainGamma(1.5, 1)).toBeCloseTo(1, 10);
    expect(applyRelativeTerrainGamma(-3, 0.5)).toBeCloseTo(0, 10);
    expect(applyRelativeTerrainGamma(10, 0.5)).toBeCloseTo(1, 10);
  });

  it("6. gamma <= 0 is rejected outright rather than silently misbehaving", () => {
    expect(() => applyRelativeTerrainGamma(0.5, 0)).toThrow();
    expect(() => applyRelativeTerrainGamma(0.5, -1)).toThrow();
  });

  it("7. Infinity/-Infinity input pass through unchanged (never treated as a valid normalized depth)", () => {
    expect(applyRelativeTerrainGamma(Infinity, 0.5)).toBe(Infinity);
    expect(applyRelativeTerrainGamma(-Infinity, 0.5)).toBe(-Infinity);
  });

  it("8. gamma !== 1 genuinely redistributes contrast (not a no-op) for a below-midpoint value", () => {
    const linear = applyRelativeTerrainGamma(0.25, 1);
    const gamma = applyRelativeTerrainGamma(0.25, 0.5);
    expect(gamma).toBeGreaterThan(linear);
  });
});

describe("relativeExaggerationSliderRange", () => {
  it("centers a wide, sensible range around the real baseline", () => {
    const { min, max, step } = relativeExaggerationSliderRange(9);
    expect(min).toBeLessThan(9);
    expect(max).toBeGreaterThan(9);
    expect(step).toBeGreaterThan(0);
  });

  it("stays within the global safety bounds even for an extreme baseline", () => {
    const { min, max } = relativeExaggerationSliderRange(MAX_AUTO_RELATIVE_EXAGGERATION);
    expect(min).toBeGreaterThanOrEqual(MIN_AUTO_RELATIVE_EXAGGERATION);
    expect(max).toBeLessThanOrEqual(MAX_AUTO_RELATIVE_EXAGGERATION);
  });
});
