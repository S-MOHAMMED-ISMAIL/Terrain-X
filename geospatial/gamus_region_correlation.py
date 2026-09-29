"""Development/validation-only diagnostic: measures whether TERRAIN-X's
existing Depth Anything V2 relative-depth output carries local, per-region
correlation with GAMUS AGL when conditioned on MobileSAM's class-agnostic
regions — the exact diagnostic proposed in the "Height-Pipeline Architecture
Options" report (§D/E, Option C) to decide whether per-region calibration is
worth pursuing, BEFORE writing any new calibration code.

## Scope and scientific-honesty framing

This module answers one narrow empirical question: "restricted to the
pixels inside one MobileSAM region, is relative depth linearly related to
GAMUS AGL?" It is not a new model, not a new calibration strategy, and
computes nothing that gets used by the production pipeline
(`app/services/calibration_pipeline.py`, `geospatial/calibration.py`) —
those remain untouched. GAMUS CLS is used here strictly as an
EVALUATION-side partition label (which class a region's pixels mostly
belong to), never as an inference-time input, exactly like
`geospatial/gamus_semantic_validation.py`'s own scientific-honesty rule.

## Why this reuses geospatial.gamus_validation and geospatial.calibration
rather than reimplementing fitting/correlation

Per-region fitting is mathematically identical to the whole-image fitting
`geospatial/gamus_validation.py::fit_and_validate_gamus` already does
(itself a thin wrapper over `geospatial/calibration.py::fit_robust_affine`
+ `compute_validation_metrics`) — just called once per region instead of
once globally. `compute_region_purity` from
`geospatial/gamus_semantic_validation.py` already computes exactly the
per-region "GAMUS majority class + purity" facts this diagnostic needs to
group by. Nothing here duplicates that math; it only adds the orchestration
loop and the minimum-region-size / class-grouping logic that don't exist
anywhere else yet.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass

import numpy as np

from geospatial.calibration import DegenerateCalibrationError
from geospatial.gamus_semantic_validation import RegionPurity, compute_region_purity
from geospatial.gamus_validation import build_gamus_valid_mask, fit_and_validate_gamus

# A region needs enough VALID (finite depth + finite AGL) pixels for its
# Pearson r/R² to mean anything rather than reflecting small-sample noise.
# 100 is a documented, deliberately conservative floor: MobileSAM found 61
# regions on DC_03_26 averaging ~1,593 px/region (97,196 foreground px total
# / 61 regions), so 100 is well below the typical region size (keeps most
# regions eligible) while still ruling out the smallest, statistically
# unstable ones. Callers may override it, but never silently.
DEFAULT_MIN_VALID_PIXELS = 100

# The same robust-fit defaults app/core/config.Settings uses in production
# (CALIBRATION_OUTLIER_SIGMA / CALIBRATION_MAX_ITERATIONS) — restated here
# for the same reason scripts/validate_gamus.py restates them: this module
# must not import backend settings.
DEFAULT_OUTLIER_SIGMA = 2.5
DEFAULT_MAX_ITERATIONS = 5

# The predefined diagnostic threshold from the architecture report (§E) for
# deciding whether a region's correlation is even worth further attention —
# NOT an acceptance claim about any final model (see this module's and the
# experiment script's docstrings).
MEANINGFUL_CORRELATION_THRESHOLD = 0.30


@dataclass(frozen=True)
class RegionCorrelation:
    """Real, per-region statistics for one MobileSAM region that had enough
    valid pixels to fit. `pearson_r`/`r2` may be `None` with an explicit
    reason (see `geospatial.gamus_validation.ExtendedValidationMetrics`) —
    never a fabricated 0.0 standing in for "undefined"."""

    region_id: int
    pixel_count: int  # total region pixels (label_map == region_id), regardless of validity
    valid_pixel_count: int  # pixels with finite depth AND finite AGL
    majority_class: int
    purity: float
    pearson_r: float | None
    pearson_r_explicit_reason: str | None
    r2: float | None
    r2_explicit_reason: str | None
    mae: float
    rmse: float
    scale: float
    offset: float
    inlier_count: int
    outlier_count: int


@dataclass(frozen=True)
class SkippedRegion:
    """A region that was excluded from analysis, and exactly why — every
    region MobileSAM produced is accounted for, never silently dropped."""

    region_id: int
    pixel_count: int
    valid_pixel_count: int
    reason: str


@dataclass(frozen=True)
class RegionCorrelationRun:
    """The full result of one region-correlation diagnostic run."""

    min_valid_pixels: int
    outlier_sigma: float
    max_iterations: int
    total_regions: int  # every nonzero region MobileSAM produced, evaluated + skipped
    evaluated: list[RegionCorrelation]
    skipped: list[SkippedRegion]


def compute_region_correlations(
    label_map: np.ndarray,
    relative_depth: np.ndarray,
    agl: np.ndarray,
    cls: np.ndarray,
    *,
    min_valid_pixels: int = DEFAULT_MIN_VALID_PIXELS,
    outlier_sigma: float = DEFAULT_OUTLIER_SIGMA,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
) -> RegionCorrelationRun:
    """For every nonzero MobileSAM region in `label_map`, fits a robust
    affine relationship (relative_depth -> AGL) using ONLY that region's
    pixels and reports Pearson r / R² / MAE / RMSE, alongside the region's
    GAMUS majority class and purity (from `compute_region_purity`).

    A region is skipped (recorded in `.skipped`, never silently dropped) if:
      - it has fewer than `min_valid_pixels` valid (finite depth + finite
        AGL, via `build_gamus_valid_mask`) pixels, or
      - `fit_and_validate_gamus` raises `DegenerateCalibrationError` (e.g.
        near-zero depth variance within the region even after the size
        floor is met).

    Raises `ValueError` if `label_map`/`relative_depth`/`agl`/`cls` shapes
    don't all match (same contract as the sibling gamus_*_validation
    modules).
    """
    shapes = {
        "label_map": label_map.shape,
        "relative_depth": relative_depth.shape,
        "agl": agl.shape,
        "cls": cls.shape,
    }
    if len(set(shapes.values())) > 1:
        raise ValueError(
            f"All input arrays must share one shape for pixel-aligned analysis; got {shapes}"
        )

    purities: dict[int, RegionPurity] = {
        p.region_id: p for p in compute_region_purity(label_map, cls)
    }

    evaluated: list[RegionCorrelation] = []
    skipped: list[SkippedRegion] = []
    region_ids = sorted(int(r) for r in np.unique(label_map).tolist() if r != 0)

    for region_id in region_ids:
        region_mask = label_map == region_id
        pixel_count = int(region_mask.sum())
        depth_region = relative_depth[region_mask]
        agl_region = agl[region_mask]

        valid_mask = build_gamus_valid_mask(agl_region, depth_region)
        valid_pixel_count = int(valid_mask.sum())

        if valid_pixel_count < min_valid_pixels:
            skipped.append(
                SkippedRegion(
                    region_id=region_id,
                    pixel_count=pixel_count,
                    valid_pixel_count=valid_pixel_count,
                    reason=(
                        f"Only {valid_pixel_count} valid pixel(s), below the "
                        f"{min_valid_pixels}-pixel minimum region size for this diagnostic."
                    ),
                )
            )
            continue

        try:
            fit, metrics = fit_and_validate_gamus(
                depth_region,
                agl_region,
                valid_mask,
                outlier_sigma=outlier_sigma,
                max_iterations=max_iterations,
            )
        except DegenerateCalibrationError as exc:
            skipped.append(
                SkippedRegion(
                    region_id=region_id,
                    pixel_count=pixel_count,
                    valid_pixel_count=valid_pixel_count,
                    reason=f"Degenerate fit: {exc}",
                )
            )
            continue

        purity = purities[region_id]
        evaluated.append(
            RegionCorrelation(
                region_id=region_id,
                pixel_count=pixel_count,
                valid_pixel_count=valid_pixel_count,
                majority_class=purity.majority_class,
                purity=purity.purity,
                pearson_r=metrics.pearson_r,
                pearson_r_explicit_reason=metrics.pearson_r_explicit_reason,
                r2=metrics.r2,
                r2_explicit_reason=metrics.r2_explicit_reason,
                mae=metrics.base.mae,
                rmse=metrics.base.rmse,
                scale=fit.scale,
                offset=fit.offset,
                inlier_count=metrics.base.inlier_count,
                outlier_count=metrics.base.outlier_count,
            )
        )

    return RegionCorrelationRun(
        min_valid_pixels=min_valid_pixels,
        outlier_sigma=outlier_sigma,
        max_iterations=max_iterations,
        total_regions=len(region_ids),
        evaluated=evaluated,
        skipped=skipped,
    )


@dataclass(frozen=True)
class ClassCorrelationSummary:
    """Real, aggregated per-GAMUS-class statistics over every EVALUATED
    region whose majority class is `gamus_class`. `median_abs_r`/etc. are
    `None` (never a fabricated 0.0) when no region in this class had a
    defined Pearson r to aggregate."""

    gamus_class: int
    region_count: int
    regions_with_defined_r: int
    total_valid_pixels: int
    median_abs_r: float | None
    mean_abs_r: float | None
    max_abs_r: float | None
    median_mae: float | None
    median_rmse: float | None


def summarize_by_class(
    correlations: list[RegionCorrelation], *, gamus_classes: tuple[int, ...] = tuple(range(7))
) -> list[ClassCorrelationSummary]:
    """Groups evaluated regions by their GAMUS majority class and reports
    real aggregate statistics for each class (see `ClassCorrelationSummary`).
    Every class in `gamus_classes` is reported, including ones with zero
    regions (all-`None`/zero stats), for a complete, never-silently-omitted
    picture."""
    summaries: list[ClassCorrelationSummary] = []
    for gamus_class in gamus_classes:
        class_regions = [c for c in correlations if c.majority_class == gamus_class]
        abs_r_values = [abs(c.pearson_r) for c in class_regions if c.pearson_r is not None]
        maes = [c.mae for c in class_regions]
        rmses = [c.rmse for c in class_regions]

        summaries.append(
            ClassCorrelationSummary(
                gamus_class=gamus_class,
                region_count=len(class_regions),
                regions_with_defined_r=len(abs_r_values),
                total_valid_pixels=sum(c.valid_pixel_count for c in class_regions),
                median_abs_r=statistics.median(abs_r_values) if abs_r_values else None,
                mean_abs_r=statistics.mean(abs_r_values) if abs_r_values else None,
                max_abs_r=max(abs_r_values) if abs_r_values else None,
                median_mae=statistics.median(maes) if maes else None,
                median_rmse=statistics.median(rmses) if rmses else None,
            )
        )
    return summaries


def regions_meeting_threshold(
    correlations: list[RegionCorrelation], *, threshold: float = MEANINGFUL_CORRELATION_THRESHOLD
) -> list[RegionCorrelation]:
    """Every evaluated region whose |Pearson r| >= `threshold`. An empty
    result is a real, reportable finding, not an error."""
    return [c for c in correlations if c.pearson_r is not None and abs(c.pearson_r) >= threshold]
