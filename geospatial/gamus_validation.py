"""Development/validation-only support for scoring TERRAIN-X's monocular
relative-depth output against the GAMUS benchmark (RGB + AGL "above ground
level" height, distributed as plain HDF5 arrays — https://github.com/EPFL-
VILAB/GAMUS-style nDSM tiles). This is NOT part of the production analysis
pipeline (see app/services/depth_pipeline.py / calibration_pipeline.py /
analysis_execution.py) and is never imported by it.

## Why this module exists, and why it's separate from geospatial/calibration.py

`geospatial/calibration.py` fits `Z_metric = a * D_relative + b` against a
REAL, georeferenced DEM/GCP reference (§3.4 of docs/ARCHITECTURE.md) — its
`sample_dem_pairs`/`sample_gcp_pairs` exist specifically to turn map
coordinates into pixel correspondences across two files that may have
different CRSs, resolutions, or footprints.

GAMUS gives us something simpler and stricter: an RGB tile and its AGL tile
are already the exact same (H, W) pixel grid — pixel (i, j) in one is pixel
(i, j) in the other, with NO CRS, NO affine transform, and NO map-coordinate
correspondence to establish (the GAMUS HDF5 files carry no georeferencing at
all). Treating GAMUS AGL as if it were a real DEM/GeoTIFF — inventing a CRS
or pixel-to-map transform for it — would be scientifically dishonest, so this
module never does that: it works entirely in raw pixel-index space and reuses
`geospatial.calibration.fit_robust_affine` /
`geospatial.calibration.compute_validation_metrics` (both already pure numpy,
requiring no CRS/transform) for the actual fitting and residual statistics
rather than duplicating that logic.

## Scope

This module only fits/scores the SAME affine relationship the production
calibration pipeline uses; it does not calibrate, persist, or expose a metric
elevation/DSM artifact from GAMUS data, and it must never be described as
doing so. GAMUS AGL is a validation *reference*, not a production input.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import h5py
import numpy as np

from geospatial.calibration import (
    DegenerateCalibrationError,
    RobustFitResult,
    ValidationMetrics,
    compute_validation_metrics,
    fit_robust_affine,
)

GAMUS_H5_DATASET_KEY = "image"


class GamusH5FormatError(Exception):
    """Raised when a GAMUS `.h5` file does not have the expected
    `"image"` dataset key. Distinct from a missing/unreadable file (a plain
    `OSError` from h5py itself, left to propagate as-is) — this is a file
    h5py opens just fine that simply isn't shaped like a GAMUS sample."""


@dataclass(frozen=True)
class ExtendedValidationMetrics:
    """`geospatial.calibration.ValidationMetrics` plus the two goodness-of-fit
    statistics GAMUS validation additionally wants (R² and Pearson r).
    Deliberately a separate, wrapping dataclass — not an addition to
    `ValidationMetrics` itself — so the DEM/GCP calibration pipeline's own
    metadata shape (`app/services/calibration_pipeline.py`) is completely
    unaffected by this development-only capability."""

    base: ValidationMetrics
    r2: float
    r2_explicit_reason: str | None  # set instead of a fabricated value when R² is undefined
    pearson_r: float
    pearson_r_explicit_reason: str | None  # ditto, for Pearson r


def load_gamus_h5(path: str) -> np.ndarray:
    """Reads a GAMUS `.h5` file's `"image"` dataset into memory as a real
    numpy array (RGB: `(H, W, 3)` uint8; AGL/CLS: `(H, W)` float32 — see the
    GAMUS sample inspection this module's design is based on).

    Deliberately h5py only — GAMUS `.h5` files carry no CRS/geotransform, so
    reading them via rasterio/GDAL (as `geospatial.raster_io.read_raster_array`
    does for real georeferenced rasters) would invite exactly the kind of
    fabricated-georeferencing mistake this module exists to avoid.

    Raises `GamusH5FormatError` if the file has no `"image"` key.
    """
    with h5py.File(path, "r") as f:
        if GAMUS_H5_DATASET_KEY not in f:
            raise GamusH5FormatError(
                f"'{path}' has no '{GAMUS_H5_DATASET_KEY}' dataset "
                f"(found keys: {list(f.keys())}); this does not look like a "
                f"GAMUS sample file."
            )
        return f[GAMUS_H5_DATASET_KEY][:]


def build_gamus_valid_mask(
    agl: np.ndarray,
    depth: np.ndarray,
    *,
    agl_nodata: float | None = None,
) -> np.ndarray:
    """Builds the boolean valid-pixel mask for pairing GAMUS AGL against
    predicted relative depth.

    ## Mask policy (read before changing)

    A pixel is valid if and only if BOTH arrays are finite at that pixel
    (`np.isfinite` — rejects NaN and +/-Inf), optionally also excluding an
    explicit `agl_nodata` sentinel value when the caller knows one applies.

    Negative AGL values are NEVER excluded on their own. Real GAMUS AGL
    samples were inspected directly (`heights/test/DC_03_26_AGL.h5`): all
    1,048,576 pixels are finite (no NaN/Inf and no large sentinel like
    -9999), and 0.52% are negative, of which ~91% sit exactly at -5.0 — a
    hard floor concentrated in specific CLS classes, not scattered noise.
    This is consistent with GAMUS's AGL being generated as a
    DSM-minus-DTM height that gets floor-clamped near flat/ground-level
    terrain to bound interpolation noise, NOT a missing-data flag. Treating
    `AGL < 0` (or `AGL == -5`) as invalid would silently discard real,
    intentionally-bounded ground-truth height data.

    `agl_nodata` exists so a *different* GAMUS tile that turns out to carry
    an actual sentinel nodata value (e.g. a large negative fill like -9999,
    which would be an obvious, deliberate outlier rather than a small
    physically-plausible clamp) can be excluded explicitly and by name, by a
    caller who has verified that for their own file — never invented or
    assumed here.

    Raises `ValueError` if `agl.shape != depth.shape`.
    """
    if agl.shape != depth.shape:
        raise ValueError(
            f"AGL and depth arrays must have matching shapes for pixel-aligned "
            f"comparison; got AGL {agl.shape} vs depth {depth.shape}."
        )

    mask = np.isfinite(agl) & np.isfinite(depth)
    if agl_nodata is not None:
        mask &= agl != agl_nodata
    return mask


def _compute_r2(reference: np.ndarray, predicted: np.ndarray) -> tuple[float, str | None]:
    """R² = 1 - SS_res / SS_tot, computed against the inlier set actually
    used by the fit (consistent with `compute_validation_metrics`'s own
    residual statistics). Zero-variance reference data (SS_tot == 0) makes R²
    mathematically undefined -- a fraction of explained variance is
    meaningless when there is no variance to explain -- so that case is
    reported explicitly via the returned reason string, never as a silent
    NaN or a fabricated 0.0/1.0."""
    ss_tot = float(np.sum((reference - np.mean(reference)) ** 2))
    if ss_tot < 1e-12:
        return math.nan, (
            "Reference (AGL) values in the inlier set have (near-)zero variance; "
            "R² (fraction of explained variance) is undefined when there is no "
            "variance to explain."
        )
    ss_res = float(np.sum((reference - predicted) ** 2))
    return 1.0 - ss_res / ss_tot, None


def _compute_pearson_r(depth: np.ndarray, reference: np.ndarray) -> tuple[float, str | None]:
    """Pearson correlation between relative depth and reference AGL over the
    inlier set. Requires at least 2 samples and nonzero variance in both
    arrays (a constant array has no defined correlation direction) — reported
    explicitly via the returned reason string rather than a silent NaN."""
    if depth.shape[0] < 2:
        return math.nan, "Fewer than 2 inlier samples; Pearson correlation is undefined."
    if np.std(depth) < 1e-12 or np.std(reference) < 1e-12:
        return math.nan, (
            "Relative depth or reference AGL has (near-)zero variance in the inlier "
            "set; Pearson correlation is undefined without variance in both variables."
        )
    r = float(np.corrcoef(depth, reference)[0, 1])
    return r, None


def compute_extended_validation_metrics(
    relative_depth: np.ndarray,
    reference_agl: np.ndarray,
    fit: RobustFitResult,
) -> ExtendedValidationMetrics:
    """`compute_validation_metrics` (MAE/RMSE/bias/residuals/counts) plus R²
    and Pearson r, both computed over the same inlier set the fit actually
    used — for the same "honest fit-quality numbers against the samples
    really used" reasoning `geospatial.calibration` already documents.
    Reuses `compute_validation_metrics` rather than recomputing its fields."""
    base = compute_validation_metrics(relative_depth, reference_agl, fit)

    d_in = relative_depth[fit.inlier_mask]
    z_in = reference_agl[fit.inlier_mask]
    predicted = fit.scale * d_in + fit.offset

    r2, r2_reason = _compute_r2(z_in, predicted)
    pearson_r, pearson_reason = _compute_pearson_r(d_in, z_in)

    return ExtendedValidationMetrics(
        base=base,
        r2=r2,
        r2_explicit_reason=r2_reason,
        pearson_r=pearson_r,
        pearson_r_explicit_reason=pearson_reason,
    )


def fit_and_validate_gamus(
    relative_depth: np.ndarray,
    reference_agl: np.ndarray,
    valid_mask: np.ndarray,
    *,
    outlier_sigma: float,
    max_iterations: int,
) -> tuple[RobustFitResult, ExtendedValidationMetrics]:
    """Convenience wrapper: flattens `relative_depth`/`reference_agl` under
    `valid_mask`, fits the robust affine relationship via
    `geospatial.calibration.fit_robust_affine`, and scores it via
    `compute_extended_validation_metrics`. Raises `DegenerateCalibrationError`
    (re-exported from `geospatial.calibration`) under the same conditions
    `fit_robust_affine` already documents (near-zero-variance or too few
    valid pixels)."""
    d_valid = relative_depth[valid_mask].astype("float64")
    z_valid = reference_agl[valid_mask].astype("float64")

    if d_valid.shape[0] < 2:
        raise DegenerateCalibrationError(
            f"Only {d_valid.shape[0]} valid pixel(s) after masking; at least 2 are "
            f"required to fit an affine relationship."
        )

    fit = fit_robust_affine(
        d_valid, z_valid, outlier_sigma=outlier_sigma, max_iterations=max_iterations
    )
    metrics = compute_extended_validation_metrics(d_valid, z_valid, fit)
    return fit, metrics
