import { describe, expect, it } from "vitest";
import type { ResidualFeature, ResidualFeatureProperties } from "@/api/types";
import {
  formatResidual,
  RESIDUAL_KIND_LABELS,
  RESIDUAL_NEGATIVE_MEANING,
  RESIDUAL_POSITIVE_MEANING,
  RESIDUAL_UNITS_TEXT,
  residualColor,
  residualMarkersInteractive,
  residualsUnavailableReason,
  residualTooltipLines,
  residualValue,
  symmetricScaleMax,
} from "./calibrationResiduals";

function props(overrides: Partial<ResidualFeatureProperties> = {}): ResidualFeatureProperties {
  return {
    sample_index: 7,
    row: 12,
    col: 30,
    source_x: 500061,
    source_y: 4649959,
    relative_depth: 0.5,
    reference_elevation: 101.25,
    predicted_heldout: 101.5,
    residual_heldout: 0.25,
    predicted_fit: 101.2,
    residual_fit: -0.05,
    inlier_in_production_fit: true,
    fold_id: 3,
    block_id: 3,
    reference_cell_id: 414,
    gcp_index: null,
    ...overrides,
  };
}

function feature(p: ResidualFeatureProperties): ResidualFeature {
  return { type: "Feature", geometry: { type: "Point", coordinates: [15, 42] }, properties: p };
}

describe("residual values and kinds", () => {
  it("selects the stored held-out or fit residual, never recomputing it", () => {
    const p = props();
    expect(residualValue(p, "heldout")).toBe(0.25);
    expect(residualValue(p, "fit")).toBe(-0.05);
  });

  it("labels held-out as validation and fit as not validation", () => {
    expect(RESIDUAL_KIND_LABELS.heldout).toMatch(/Held-out/);
    expect(RESIDUAL_KIND_LABELS.fit).toMatch(/not validation/);
  });
});

describe("display scaling and colours", () => {
  it("scales symmetrically at the largest |residual| of the kind shown", () => {
    const features = [
      feature(props({ residual_heldout: -3, residual_fit: 1 })),
      feature(props({ residual_heldout: 2, residual_fit: -0.5 })),
    ];
    expect(symmetricScaleMax(features, "heldout")).toBe(3);
    expect(symmetricScaleMax(features, "fit")).toBe(1);
    expect(symmetricScaleMax([], "heldout")).toBe(0);
  });

  it("uses red for positive (higher), blue for negative (lower), near-white at 0", () => {
    expect(residualColor(3, 3)).toBe("rgb(178, 24, 43)");
    expect(residualColor(-3, 3)).toBe("rgb(33, 102, 172)");
    expect(residualColor(0, 3)).toBe("rgb(247, 247, 247)");
    expect(residualColor(1.5, 3)).toBe(residualColor(1.5, 3));
    expect(residualColor(1.5, 3)).not.toBe(residualColor(-1.5, 3));
  });

  it("is neutral when there is no spread to scale", () => {
    expect(residualColor(0, 0)).toBe("rgb(247, 247, 247)");
  });

  it("states sign meaning and never claims metres", () => {
    expect(RESIDUAL_POSITIVE_MEANING).toMatch(/higher than the reference/);
    expect(RESIDUAL_NEGATIVE_MEANING).toMatch(/lower than the reference/);
    expect(RESIDUAL_UNITS_TEXT).toBe("units of the calibration reference");
    expect(RESIDUAL_UNITS_TEXT).not.toMatch(/\bm\b|metre|meter/);
  });
});

describe("tooltip", () => {
  it("shows signed stored values with 4 decimals", () => {
    expect(formatResidual(0.25)).toBe("+0.2500");
    expect(formatResidual(-0.05)).toBe("-0.0500");
    expect(formatResidual(0)).toBe("0.0000");
  });

  it("lists the held-out residual, prediction, reference and block", () => {
    expect(residualTooltipLines(props(), "heldout")).toEqual([
      "Held-out residual: +0.2500 (units of the calibration reference)",
      "Held-out prediction: 101.5000",
      "Reference elevation: 101.2500",
      "Source pixel (row, col): 12, 30",
      "Spatial block 3 (held out as a block)",
    ]);
  });

  it("labels the fit view as in-sample and flags outliers and GCP identity", () => {
    const lines = residualTooltipLines(
      props({ inlier_in_production_fit: false, gcp_index: 2, block_id: null }),
      "fit",
    );
    expect(lines[0]).toBe("Fit (in-sample) residual: -0.0500 (units of the calibration reference)");
    expect(lines).toContain("GCP #2 (held out on its own)");
    expect(lines).toContain("Excluded from the production fit (outlier)");
  });
});

describe("availability and interaction", () => {
  it("returns the backend's real unavailable reason", () => {
    expect(
      residualsUnavailableReason({
        available: false,
        unavailable_reason: "No calibration reference (DEM/GCP) was used for this analysis job.",
        artifact_id: null,
        analysis_job_id: null,
        reference_type: null,
        sample_count: null,
      }),
    ).toMatch(/No calibration reference/);
    expect(
      residualsUnavailableReason({
        available: true,
        unavailable_reason: null,
        artifact_id: "a",
        analysis_job_id: "j",
        reference_type: "dem",
        sample_count: 10,
      }),
    ).toBeNull();
  });

  it("markers take clicks only in plain inspection mode", () => {
    expect(residualMarkersInteractive("off")).toBe(true);
    expect(residualMarkersInteractive("point")).toBe(false);
    expect(residualMarkersInteractive("slope")).toBe(false);
  });
});
