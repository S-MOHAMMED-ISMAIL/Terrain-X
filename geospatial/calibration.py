"""Real metric calibration: fits a documented affine relationship between
Phase 3's relative (unitless, inverse) monocular depth and real elevation
references (a DEM raster or surveyed Ground Control Points), then reports
honest validation metrics for that fit.

Independent of the backend/FastAPI layer by design (see docs/ARCHITECTURE.md)
— the backend imports this module, never the reverse.

## Why an affine model

The calibration model is

    Z_metric = a * D_relative + b

This is the simplest model consistent with what Depth Anything V2 actually
outputs: an *unknown monotonic (in practice, close to affine) rescaling* of
true depth, with no known absolute scale or offset (see ai/depth_anything.py
for the model's documented output semantics). An affine fit is the
mathematically justified minimum: it has exactly the two degrees of freedom
needed to recover an unknown scale and an unknown offset from paired
(relative_depth, real_elevation) observations, and no more — fitting a
higher-order (e.g. polynomial) relationship would not be justified by
anything in the model's actual output semantics, would be far more sensitive
to reference-sample noise and outliers, and would invite the reader to
mistake it for a more sophisticated calibration than the single-view input
actually supports. If the real relationship differs from affine in some
region (e.g. because monocular depth degrades on distant/textureless areas),
that shows up honestly as larger residuals (§ validation metrics), not as a
license to add more free parameters until the fit "looks" better.

## Limitations (see docs/ARCHITECTURE.md §3.4 for the full discussion)

- Single-view relative depth is scale-ambiguous *and* the model's relative
  depth is not a linear function of world depth in general — the affine fit
  is a genuine approximation, not a computation with a fixed, precisely known
  form. This is why we always report residual statistics rather than a
  single "confidence" number.
- Neither DEM nor GCP calibration can distinguish terrain from object tops
  (buildings, trees, ...) that happen to be visible in the source image —
  the resulting "metric elevation" surface is a calibrated version of
  whatever the depth model saw, not a bare-earth terrain model.
- The fit is only as good as the reference data and the correspondence
  between reference points and image pixels.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
import rasterio
from affine import Affine
from rasterio.crs import CRS
from rasterio.transform import rowcol
from rasterio.warp import transform as warp_transform

from geospatial.exceptions import RasterValidationError


class DegenerateCalibrationError(Exception):
    """Raised when the valid samples exist but are mathematically unusable
    for an affine fit (e.g. zero variance in the relative-depth samples, so
    no scale is determinable)."""


@dataclass(frozen=True)
class CalibrationSamples:
    """Paired (relative_depth, reference_elevation) observations, plus the
    real counts at each filtering step — never silently dropped without
    being counted."""

    relative_depth: np.ndarray  # float64, shape (n_valid,)
    reference_elevation: np.ndarray  # float64, shape (n_valid,)
    total_candidates: int  # samples considered before any validity filtering
    valid_count: int  # samples that passed all validity checks (== len of the arrays above)
    source_crs: str
    reference_crs: str
    reprojected: bool  # whether reference_crs != source_crs required a transform
    # P1-2 per-sample provenance, aligned 1:1 with the arrays above. Needed
    # for spatially blocked cross-validation and an honest effective sample
    # count; never changes WHICH samples are selected. Optional only so a
    # caller constructing samples by hand (tests) need not supply them.
    sample_rows: np.ndarray | None = None  # int64, source-raster row of each sample
    sample_cols: np.ndarray | None = None  # int64, source-raster column of each sample
    # DEM only: flat index (dem_row * dem_width + dem_col) of the DEM cell each
    # sample actually read — the same row/col mapping `dem.sample()` uses.
    reference_cell_ids: np.ndarray | None = None
    # DEM: number of DISTINCT DEM cells behind the valid samples — a DEM
    # coarser than the sampling stride makes many samples read the same cell,
    # and those are not independent observations. GCP: the valid point count.
    effective_sample_count: int | None = None
    # P1-5 per-sample location provenance, aligned 1:1 with the arrays above.
    # Map coordinates in the SOURCE CRS: the sampled pixel centre (DEM) or
    # the GCP's own surveyed position reprojected into the source CRS (GCP).
    sample_map_xs: np.ndarray | None = None
    sample_map_ys: np.ndarray | None = None
    # GCP only: index of each valid sample's point in the reference's own
    # `gcp_points` list.
    source_point_indices: np.ndarray | None = None


@dataclass(frozen=True)
class RobustFitResult:
    scale: float  # a
    offset: float  # b
    inlier_mask: np.ndarray  # bool, shape (n_valid,) — True for samples kept in the final fit
    iterations_used: int
    outlier_sigma: float


@dataclass(frozen=True)
class ValidationMetrics:
    mae: float
    rmse: float
    bias: float  # mean(residual) = mean(predicted - reference); signed, not "error magnitude"
    min_residual: float
    max_residual: float
    sample_count: int  # == valid_count
    inlier_count: int
    outlier_count: int


def _pixel_centers_to_map_coords(
    transform: Affine, rows: np.ndarray, cols: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Real affine pixel->map transform, evaluated at pixel *centers*
    (row+0.5, col+0.5) — the standard convention for what a raster cell's
    coordinate represents."""
    xs, ys = transform * (cols + 0.5, rows + 0.5)
    return np.asarray(xs), np.asarray(ys)


def sample_dem_pairs(
    depth: np.ndarray,
    source_crs: CRS,
    source_transform: Affine,
    dem_path: str | Path,
    *,
    max_samples: int,
) -> CalibrationSamples:
    """Builds calibration pairs by sampling a real DEM raster at the map
    coordinates of a uniform grid of depth-raster pixels.

    Uses `rasterio.DatasetReader.sample()` (point sampling) rather than
    reading the whole DEM into memory — the DEM can be arbitrarily large and
    cover far more area than the source image; only the specific points
    needed are ever read.

    Sampling strategy: a uniform grid over the depth raster (stride chosen
    so the candidate count is close to `max_samples`), not random sampling —
    this gives deterministic, reproducible results and even spatial coverage
    of the source image, rather than being biased toward whatever a random
    seed happens to pick.
    """
    height, width = depth.shape
    total_pixels = height * width
    stride = max(1, math.isqrt(max(1, total_pixels // max(1, max_samples))))

    rows = np.arange(0, height, stride)
    cols = np.arange(0, width, stride)
    grid_rows, grid_cols = np.meshgrid(rows, cols, indexing="ij")
    grid_rows = grid_rows.ravel()
    grid_cols = grid_cols.ravel()
    total_candidates = grid_rows.size

    map_xs, map_ys = _pixel_centers_to_map_coords(source_transform, grid_rows, grid_cols)

    try:
        with rasterio.open(dem_path) as dem:
            dem_crs = dem.crs
            if dem_crs is None:
                raise RasterValidationError(
                    "The DEM reference has no CRS; a DEM must be georeferenced to be used "
                    "as a calibration reference."
                )

            reprojected = dem_crs.to_string() != source_crs.to_string()
            if reprojected:
                sample_xs, sample_ys = warp_transform(
                    source_crs, dem_crs, map_xs.tolist(), map_ys.tolist()
                )
                sample_xs = np.asarray(sample_xs)
                sample_ys = np.asarray(sample_ys)
            else:
                sample_xs, sample_ys = map_xs, map_ys

            # Reject points outside the DEM's own bounds before sampling —
            # rasterio.sample() would otherwise return its nodata/fill value
            # for them, which must not be mistaken for a real elevation.
            left, bottom, right, top = dem.bounds
            in_bounds = (
                (sample_xs >= left)
                & (sample_xs <= right)
                & (sample_ys >= bottom)
                & (sample_ys <= top)
            )

            dem_nodata = dem.nodata
            coords = list(
                zip(sample_xs[in_bounds].tolist(), sample_ys[in_bounds].tolist(), strict=True)
            )
            kept_rows = grid_rows[in_bounds]
            kept_cols = grid_cols[in_bounds]
            kept_map_xs = map_xs[in_bounds]
            kept_map_ys = map_ys[in_bounds]
            depth_vals = depth[kept_rows, kept_cols]

            if coords:
                sampled = np.array([val[0] for val in dem.sample(coords)], dtype="float64")
                # The exact coordinate->cell mapping `dem.sample()` itself
                # uses (rasterio.sample._transform_xy -> rowcol), so each ID
                # names the cell whose value was actually read.
                dem_rows, dem_cols = rowcol(
                    dem.transform, sample_xs[in_bounds].tolist(), sample_ys[in_bounds].tolist()
                )
                cell_ids = np.asarray(dem_rows, dtype="int64") * dem.width + np.asarray(
                    dem_cols, dtype="int64"
                )
            else:
                sampled = np.array([], dtype="float64")
                cell_ids = np.array([], dtype="int64")
    except (RasterValidationError, ValueError):
        raise
    except Exception as exc:  # rasterio's own IO errors, etc.
        raise RasterValidationError(f"Could not read DEM reference: {exc}") from exc

    valid_mask = np.isfinite(sampled) & np.isfinite(depth_vals)
    if dem_nodata is not None:
        valid_mask &= sampled != dem_nodata

    valid_cell_ids = cell_ids[valid_mask]
    return CalibrationSamples(
        relative_depth=depth_vals[valid_mask].astype("float64"),
        reference_elevation=sampled[valid_mask],
        total_candidates=total_candidates,
        valid_count=int(valid_mask.sum()),
        source_crs=source_crs.to_string(),
        reference_crs=dem_crs.to_string(),
        reprojected=reprojected,
        sample_rows=kept_rows[valid_mask].astype("int64"),
        sample_cols=kept_cols[valid_mask].astype("int64"),
        reference_cell_ids=valid_cell_ids,
        effective_sample_count=int(np.unique(valid_cell_ids).size),
        sample_map_xs=kept_map_xs[valid_mask].astype("float64"),
        sample_map_ys=kept_map_ys[valid_mask].astype("float64"),
    )


def sample_gcp_pairs(
    depth: np.ndarray,
    source_crs: CRS,
    source_transform: Affine,
    gcp_points: list[dict],
    gcp_crs: str,
) -> CalibrationSamples:
    """Builds calibration pairs by transforming each GCP's (x, y) into the
    source raster's CRS/pixel space and sampling the real relative-depth
    array at that pixel (nearest-pixel indexing — sub-pixel interpolation is
    not implemented, a documented simplification).

    Points that fall outside the source image's pixel bounds are rejected —
    a GCP correspondence is never invented for a point the image doesn't
    actually cover.
    """
    total_candidates = len(gcp_points)
    xs = np.array([p["x"] for p in gcp_points], dtype="float64")
    ys = np.array([p["y"] for p in gcp_points], dtype="float64")
    zs = np.array([p["z"] for p in gcp_points], dtype="float64")

    gcp_crs_obj = CRS.from_user_input(gcp_crs)
    reprojected = gcp_crs_obj.to_string() != source_crs.to_string()
    if reprojected:
        proj_xs, proj_ys = warp_transform(gcp_crs_obj, source_crs, xs.tolist(), ys.tolist())
        proj_xs = np.asarray(proj_xs)
        proj_ys = np.asarray(proj_ys)
    else:
        proj_xs, proj_ys = xs, ys

    inv_transform = ~source_transform
    cols, rows = inv_transform * (proj_xs, proj_ys)
    cols = np.floor(cols).astype("int64")
    rows = np.floor(rows).astype("int64")

    height, width = depth.shape
    in_bounds = (cols >= 0) & (cols < width) & (rows >= 0) & (rows < height)

    depth_vals = np.full(total_candidates, np.nan, dtype="float64")
    valid_rows = rows[in_bounds]
    valid_cols = cols[in_bounds]
    depth_vals[in_bounds] = depth[valid_rows, valid_cols]

    valid_mask = in_bounds & np.isfinite(depth_vals) & np.isfinite(zs)

    return CalibrationSamples(
        relative_depth=depth_vals[valid_mask],
        reference_elevation=zs[valid_mask],
        total_candidates=total_candidates,
        valid_count=int(valid_mask.sum()),
        source_crs=source_crs.to_string(),
        reference_crs=gcp_crs_obj.to_string(),
        reprojected=reprojected,
        sample_rows=rows[valid_mask],
        sample_cols=cols[valid_mask],
        # Each GCP is its own independently surveyed point.
        effective_sample_count=int(valid_mask.sum()),
        sample_map_xs=np.asarray(proj_xs, dtype="float64")[valid_mask],
        sample_map_ys=np.asarray(proj_ys, dtype="float64")[valid_mask],
        source_point_indices=np.arange(total_candidates, dtype="int64")[valid_mask],
    )


def fit_robust_affine(
    relative_depth: np.ndarray,
    reference_elevation: np.ndarray,
    *,
    outlier_sigma: float,
    max_iterations: int,
) -> RobustFitResult:
    """Ordinary-least-squares affine fit (Z = a*D + b), refined by iterative
    sigma-clipping: fit, compute residuals, drop points whose residual
    exceeds `outlier_sigma` standard deviations, refit on the remainder,
    repeat until no further points are dropped or `max_iterations` is
    reached. This is a standard, simple, dependency-free robust-regression
    technique (an instance of iteratively reweighted least squares with a
    hard 0/1 weight) — chosen over RANSAC for this phase because it needs no
    random sampling/consensus-search parameters beyond one threshold, is
    fully deterministic, and is explicitly permitted as an alternative to
    RANSAC for this phase.

    Raises DegenerateCalibrationError if the input has no usable variance in
    `relative_depth` (a scale cannot be determined from constant input) or
    if too few points remain to keep fitting.
    """
    if np.std(relative_depth) < 1e-9:
        raise DegenerateCalibrationError(
            "Relative depth samples have (near-)zero variance; a scale cannot be determined."
        )

    mask = np.ones(relative_depth.shape, dtype=bool)
    a, b = 0.0, 0.0
    iterations_used = 0

    for iteration in range(1, max_iterations + 1):
        iterations_used = iteration
        if mask.sum() < 2:
            raise DegenerateCalibrationError(
                "Too few inlier samples remained during robust fitting to keep fitting."
            )
        d_fit = relative_depth[mask]
        z_fit = reference_elevation[mask]
        a, b = np.polyfit(d_fit, z_fit, 1)

        residuals_all = (a * relative_depth + b) - reference_elevation
        residual_std = float(np.std(residuals_all[mask]))
        if residual_std < 1e-9:
            break  # perfect (or near-perfect) fit; nothing left to clip

        new_mask = np.abs(residuals_all) <= (outlier_sigma * residual_std)
        if new_mask.sum() == mask.sum():
            break  # converged: no new outliers found
        mask = new_mask

    return RobustFitResult(
        scale=float(a),
        offset=float(b),
        inlier_mask=mask,
        iterations_used=iterations_used,
        outlier_sigma=outlier_sigma,
    )


def compute_validation_metrics(
    relative_depth: np.ndarray,
    reference_elevation: np.ndarray,
    fit: RobustFitResult,
) -> ValidationMetrics:
    """Real residual statistics computed from the *inlier* set the final fit
    actually used. These are honest fit-quality numbers, not an "accuracy
    percentage" — see docs/ARCHITECTURE.md §3.4 for why that framing is
    deliberately avoided."""
    d_in = relative_depth[fit.inlier_mask]
    z_in = reference_elevation[fit.inlier_mask]
    predicted = fit.scale * d_in + fit.offset
    residuals = predicted - z_in

    valid_count = relative_depth.shape[0]
    inlier_count = int(fit.inlier_mask.sum())

    return ValidationMetrics(
        mae=float(np.mean(np.abs(residuals))),
        rmse=float(np.sqrt(np.mean(residuals**2))),
        bias=float(np.mean(residuals)),
        min_residual=float(np.min(residuals)),
        max_residual=float(np.max(residuals)),
        sample_count=valid_count,
        inlier_count=inlier_count,
        outlier_count=valid_count - inlier_count,
    )


# ==========================================================================
# P1-2: calibration quality gate
# ==========================================================================
#
# `fit_robust_affine` always returns *some* line for any input with non-zero
# depth variance — including input where depth carries no information about
# the reference at all, or the opposite relationship to the one this
# pipeline's depth convention implies. The functions below decide whether a
# fit may be persisted as calibrated metric elevation. They never change the
# production fit: the final `a`/`b` are still `fit_robust_affine` over ALL
# valid samples, exactly as before. Cross-validation estimates how well that
# same fitting PROCEDURE generalises to reference samples it did not see.
#
# Three criteria gate the result:
#   G0 validation feasibility — the held-out procedure can actually be run
#      (a precondition, not a threshold).
#   G1 expected scale sign — the caller supplies the sign its depth
#      convention and geometry imply (see
#      app/services/calibration_pipeline.py::EXPECTED_SCALE_SIGN); this module
#      encodes no universal rule about depth sign.
#   G2 held-out skill — 1 - SSE_cv / SSE_baseline_cv must exceed the policy
#      minimum, where the baseline predicts each training fold's mean
#      reference elevation ("calibration that ignores depth").
# In-sample R², Pearson and Spearman are REPORTED, never gated on: in-sample
# R² rewards any chance fit on the same data it scores (it is >= 0 for plain
# OLS; the sigma-clipped fit can make it marginally negative over all
# samples), and any correlation cut-off would be an arbitrary number.
# Passing the gate is NOT an accuracy guarantee.

DEM_CV_METHOD = "leave_one_spatial_block_out"
GCP_CV_METHOD = "leave_one_out"

G0_VALIDATION_FEASIBILITY = "G0_validation_feasibility"
G1_EXPECTED_SCALE_SIGN = "G1_expected_scale_sign"
G2_HELDOUT_SKILL = "G2_heldout_skill"

# Same (near-)zero-variance tolerance fit_robust_affine already applies to
# relative depth.
_ZERO_VARIANCE_TOLERANCE = 1e-9

QUALITY_POLICY_STATEMENT = (
    "Engineering acceptance policy, not an empirically validated accuracy "
    "standard. Passing means only that the fitted scale has the sign this "
    "pipeline's depth convention implies and that depth predicted held-out "
    "reference elevations better than their training-fold mean; it does not "
    "certify the accuracy of the resulting metric elevation."
)


@dataclass(frozen=True)
class QualityGatePolicy:
    version: str
    min_cv_skill: float
    expected_scale_sign: int  # +1 or -1, supplied by the caller
    cv_blocks_per_side: int
    dem_cv_method: str = DEM_CV_METHOD
    gcp_cv_method: str = GCP_CV_METHOD
    statement: str = QUALITY_POLICY_STATEMENT

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class FitDiagnostics:
    """Reporting-only statistics for the production fit over ALL valid
    samples (outliers included) — unlike `ValidationMetrics`, which covers
    the fit's own inliers only. `None` where a statistic is mathematically
    undefined (e.g. a correlation with a constant input), never 0."""

    scale: float
    offset: float
    valid_sample_count: int
    effective_sample_count: int | None
    all_sample_mae: float
    all_sample_rmse: float
    all_sample_bias: float
    in_sample_r2: float | None
    pearson_r: float | None
    spearman_rho: float | None

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class CrossValidationFold:
    fold_id: int  # spatial block ID (DEM) or sample index (GCP)
    train_count: int
    heldout_count: int
    train_inlier_count: int
    scale: float
    offset: float
    heldout_sse: float
    heldout_baseline_sse: float


@dataclass(frozen=True)
class CrossValidationResult:
    method: str
    feasible: bool
    infeasibility_reason: str | None
    fold_count: int
    heldout_sample_count: int
    heldout_mae: float | None
    heldout_rmse: float | None
    heldout_bias: float | None  # mean(predicted - reference), same convention as `bias`
    sse_cv: float | None
    sse_baseline_cv: float | None
    skill: float | None
    blocks_per_side: int | None = None  # DEM only
    non_empty_block_count: int | None = None  # DEM only
    folds: tuple[CrossValidationFold, ...] = ()
    # P1-5: the exact per-sample held-out prediction each sample received
    # from the fold model fitted WITHOUT it, and that sample's fold ID —
    # aligned 1:1 with the samples. Only set when `feasible`. Never
    # serialized by `as_dict` (they would bloat calibration_metadata); the
    # calibration_residuals artifact carries them instead.
    heldout_predictions: np.ndarray | None = field(default=None, repr=False, compare=False)
    sample_fold_ids: np.ndarray | None = field(default=None, repr=False, compare=False)

    def as_dict(self) -> dict:
        data = {
            key: value
            for key, value in asdict(self).items()
            if key not in ("heldout_predictions", "sample_fold_ids")
        }
        data["folds"] = [asdict(fold) for fold in self.folds]
        return data


@dataclass(frozen=True)
class QualityGateResult:
    passed: bool
    # One dict per failed criterion: {"criterion", "reason", plus the real
    # values that failed}.
    failed_criteria: tuple[dict, ...]
    # Criteria that could not be evaluated (G2 when G0 fails) — never
    # silently counted as passed.
    not_evaluated: tuple[str, ...]
    policy: QualityGatePolicy

    def as_dict(self) -> dict:
        return {
            "passed": self.passed,
            "failed_criteria": [dict(criterion) for criterion in self.failed_criteria],
            "not_evaluated": list(self.not_evaluated),
            "policy": self.policy.as_dict(),
        }


def average_ranks(values: np.ndarray) -> np.ndarray:
    """1-based ranks with tied values sharing the mean of the ranks they
    span (the standard convention Spearman's rho requires)."""
    values = np.asarray(values, dtype="float64")
    _, inverse, counts = np.unique(values, return_inverse=True, return_counts=True)
    last_rank = np.cumsum(counts)
    mean_rank = last_rank - (counts - 1) / 2.0
    return mean_rank[inverse]


def pearson_correlation(x: np.ndarray, y: np.ndarray) -> float | None:
    x = np.asarray(x, dtype="float64")
    y = np.asarray(y, dtype="float64")
    if x.size < 2 or np.ptp(x) == 0 or np.ptp(y) == 0:
        return None
    dx = x - x.mean()
    dy = y - y.mean()
    return float(np.sum(dx * dy) / math.sqrt(float(np.sum(dx * dx)) * float(np.sum(dy * dy))))


def spearman_rank_correlation(x: np.ndarray, y: np.ndarray) -> float | None:
    """Pearson correlation of the average ranks — numpy-only, no scipy."""
    x = np.asarray(x, dtype="float64")
    y = np.asarray(y, dtype="float64")
    if x.size < 2:
        return None
    return pearson_correlation(average_ranks(x), average_ranks(y))


def compute_fit_diagnostics(
    relative_depth: np.ndarray,
    reference_elevation: np.ndarray,
    fit: RobustFitResult,
    *,
    effective_sample_count: int | None,
) -> FitDiagnostics:
    predicted = fit.scale * relative_depth + fit.offset
    residuals = predicted - reference_elevation
    ss_tot = float(np.sum((reference_elevation - reference_elevation.mean()) ** 2))
    in_sample_r2 = (
        None if np.ptp(reference_elevation) == 0 else 1.0 - float(np.sum(residuals**2)) / ss_tot
    )
    return FitDiagnostics(
        scale=fit.scale,
        offset=fit.offset,
        valid_sample_count=int(relative_depth.shape[0]),
        effective_sample_count=effective_sample_count,
        all_sample_mae=float(np.mean(np.abs(residuals))),
        all_sample_rmse=float(np.sqrt(np.mean(residuals**2))),
        all_sample_bias=float(np.mean(residuals)),
        in_sample_r2=in_sample_r2,
        pearson_r=pearson_correlation(relative_depth, reference_elevation),
        spearman_rho=spearman_rank_correlation(relative_depth, reference_elevation),
    )


def assign_spatial_blocks(
    rows: np.ndarray, cols: np.ndarray, *, height: int, width: int, blocks_per_side: int
) -> np.ndarray:
    """Deterministic contiguous spatial blocks over the SOURCE raster: block
    ID = block_row * blocks_per_side + block_col, where
    block_row = row * blocks_per_side // height (likewise for columns). Every
    in-raster sample gets exactly one block."""
    if blocks_per_side < 1:
        raise ValueError("blocks_per_side must be at least 1.")
    rows = np.asarray(rows, dtype="int64")
    cols = np.asarray(cols, dtype="int64")
    if rows.size and (
        rows.min() < 0 or rows.max() >= height or cols.min() < 0 or cols.max() >= width
    ):
        raise ValueError("Sample row/column lies outside the source raster.")
    block_rows = rows * blocks_per_side // height
    block_cols = cols * blocks_per_side // width
    return block_rows * blocks_per_side + block_cols


def _infeasible(
    method: str,
    reason: str,
    *,
    blocks_per_side: int | None,
    non_empty_block_count: int | None,
    fold_count: int = 0,
) -> CrossValidationResult:
    return CrossValidationResult(
        method=method,
        feasible=False,
        infeasibility_reason=reason,
        fold_count=fold_count,
        heldout_sample_count=0,
        heldout_mae=None,
        heldout_rmse=None,
        heldout_bias=None,
        sse_cv=None,
        sse_baseline_cv=None,
        skill=None,
        blocks_per_side=blocks_per_side,
        non_empty_block_count=non_empty_block_count,
    )


def cross_validate_affine(
    relative_depth: np.ndarray,
    reference_elevation: np.ndarray,
    groups: np.ndarray,
    *,
    method: str,
    outlier_sigma: float,
    max_iterations: int,
    blocks_per_side: int | None = None,
) -> CrossValidationResult:
    """Leave-one-group-out cross-validation of the EXACT production fitting
    procedure (`fit_robust_affine`, sigma-clipping included) — one fold per
    distinct group. The held-out side is never clipped: every held-out
    sample, including any the training fit would have rejected as an
    outlier, contributes to the held-out metrics. Each sample is held out
    exactly once.

    Returns `feasible=False` with a real reason (never raises) when the
    procedure cannot be meaningfully run: fewer than 2 groups, reference
    elevations with no variance (the mean baseline and skill are
    undefined), or a training fold the fit cannot use.
    """
    groups = np.asarray(groups)
    unique_groups = np.unique(groups)
    is_blocked = blocks_per_side is not None
    non_empty = int(unique_groups.size) if is_blocked else None
    group_label = "spatial block" if is_blocked else "held-out sample"

    if unique_groups.size < 2:
        return _infeasible(
            method,
            f"Cross-validation needs at least 2 non-empty {group_label}s to hold one "
            f"out; found {unique_groups.size}.",
            blocks_per_side=blocks_per_side,
            non_empty_block_count=non_empty,
        )
    if np.std(reference_elevation) < _ZERO_VARIANCE_TOLERANCE:
        return _infeasible(
            method,
            "Reference elevations have (near-)zero variance, so the training-fold "
            "mean baseline has no error to improve on and held-out skill is undefined.",
            blocks_per_side=blocks_per_side,
            non_empty_block_count=non_empty,
        )

    predicted = np.empty_like(reference_elevation, dtype="float64")
    baseline = np.empty_like(reference_elevation, dtype="float64")
    folds: list[CrossValidationFold] = []

    for group in unique_groups:
        heldout = groups == group
        train = ~heldout
        if np.std(relative_depth[train]) < _ZERO_VARIANCE_TOLERANCE:
            return _infeasible(
                method,
                f"Training fold holding out {group_label} {int(group)} has (near-)zero "
                "relative-depth variance, so no scale can be fitted for it.",
                blocks_per_side=blocks_per_side,
                non_empty_block_count=non_empty,
                fold_count=int(unique_groups.size),
            )
        try:
            fold_fit = fit_robust_affine(
                relative_depth[train],
                reference_elevation[train],
                outlier_sigma=outlier_sigma,
                max_iterations=max_iterations,
            )
        except DegenerateCalibrationError as exc:
            return _infeasible(
                method,
                f"Training fold holding out {group_label} {int(group)} could not be "
                f"fitted: {exc}",
                blocks_per_side=blocks_per_side,
                non_empty_block_count=non_empty,
                fold_count=int(unique_groups.size),
            )
        fold_pred = fold_fit.scale * relative_depth[heldout] + fold_fit.offset
        fold_base = np.full(int(heldout.sum()), float(np.mean(reference_elevation[train])))
        predicted[heldout] = fold_pred
        baseline[heldout] = fold_base
        folds.append(
            CrossValidationFold(
                fold_id=int(group),
                train_count=int(train.sum()),
                heldout_count=int(heldout.sum()),
                train_inlier_count=int(fold_fit.inlier_mask.sum()),
                scale=fold_fit.scale,
                offset=fold_fit.offset,
                heldout_sse=float(np.sum((fold_pred - reference_elevation[heldout]) ** 2)),
                heldout_baseline_sse=float(np.sum((fold_base - reference_elevation[heldout]) ** 2)),
            )
        )

    residuals = predicted - reference_elevation
    sse_cv = float(np.sum(residuals**2))
    sse_baseline = float(np.sum((baseline - reference_elevation) ** 2))
    if not sse_baseline > 0.0:
        return _infeasible(
            method,
            "The training-fold mean baseline predicted every held-out reference "
            "elevation exactly, so held-out skill is undefined.",
            blocks_per_side=blocks_per_side,
            non_empty_block_count=non_empty,
            fold_count=int(unique_groups.size),
        )

    return CrossValidationResult(
        method=method,
        feasible=True,
        infeasibility_reason=None,
        fold_count=len(folds),
        heldout_sample_count=int(residuals.size),
        heldout_mae=float(np.mean(np.abs(residuals))),
        heldout_rmse=float(np.sqrt(np.mean(residuals**2))),
        heldout_bias=float(np.mean(residuals)),
        sse_cv=sse_cv,
        sse_baseline_cv=sse_baseline,
        skill=1.0 - sse_cv / sse_baseline,
        blocks_per_side=blocks_per_side,
        non_empty_block_count=non_empty,
        folds=tuple(folds),
        heldout_predictions=predicted,
        sample_fold_ids=groups.astype("int64"),
    )


def cross_validate_samples(
    samples: CalibrationSamples,
    *,
    reference_type: str,
    source_height: int,
    source_width: int,
    policy: QualityGatePolicy,
    outlier_sigma: float,
    max_iterations: int,
) -> CrossValidationResult:
    """Chooses the validation grouping for a calibration reference type:
    DEM samples come from a spatially autocorrelated grid, so they are held
    out one contiguous source-image block at a time; GCPs are sparse,
    individually surveyed points, so each is held out on its own."""
    if reference_type == "dem":
        if samples.sample_rows is None or samples.sample_cols is None:
            raise ValueError("DEM cross-validation needs each sample's source row/column.")
        groups = assign_spatial_blocks(
            samples.sample_rows,
            samples.sample_cols,
            height=source_height,
            width=source_width,
            blocks_per_side=policy.cv_blocks_per_side,
        )
        return cross_validate_affine(
            samples.relative_depth,
            samples.reference_elevation,
            groups,
            method=policy.dem_cv_method,
            outlier_sigma=outlier_sigma,
            max_iterations=max_iterations,
            blocks_per_side=policy.cv_blocks_per_side,
        )
    if reference_type == "gcp":
        return cross_validate_affine(
            samples.relative_depth,
            samples.reference_elevation,
            np.arange(samples.valid_count),
            method=policy.gcp_cv_method,
            outlier_sigma=outlier_sigma,
            max_iterations=max_iterations,
        )
    raise ValueError(f"Unknown calibration reference type: {reference_type!r}")


def evaluate_quality_gate(
    fit: RobustFitResult, cross_validation: CrossValidationResult, policy: QualityGatePolicy
) -> QualityGateResult:
    """Applies G0/G1/G2 (see this section's header). Comparisons are written
    as `not (value > limit)` so a NaN fails rather than slipping through."""
    failed: list[dict] = []
    not_evaluated: list[str] = []

    if not cross_validation.feasible:
        failed.append(
            {
                "criterion": G0_VALIDATION_FEASIBILITY,
                "reason": cross_validation.infeasibility_reason,
            }
        )

    if not fit.scale * policy.expected_scale_sign > 0.0:
        failed.append(
            {
                "criterion": G1_EXPECTED_SCALE_SIGN,
                "scale_a": fit.scale,
                "expected_scale_sign": policy.expected_scale_sign,
                "reason": (
                    f"Fitted scale a={fit.scale:.6g} does not have the expected sign "
                    f"({policy.expected_scale_sign:+d}); the calibrated surface would "
                    "contradict this pipeline's depth convention."
                ),
            }
        )

    if cross_validation.feasible:
        skill = cross_validation.skill
        if skill is None or not skill > policy.min_cv_skill:
            failed.append(
                {
                    "criterion": G2_HELDOUT_SKILL,
                    "cv_skill": skill,
                    "min_cv_skill": policy.min_cv_skill,
                    "reason": (
                        f"Held-out skill {skill:.6g} is not above the policy minimum "
                        f"{policy.min_cv_skill:g}: depth did not predict held-out "
                        "reference elevations better than their training-fold mean."
                    ),
                }
            )
    else:
        not_evaluated.append(G2_HELDOUT_SKILL)

    return QualityGateResult(
        passed=not failed,
        failed_criteria=tuple(failed),
        not_evaluated=tuple(not_evaluated),
        policy=policy,
    )


def describe_quality_gate_failure(result: QualityGateResult) -> str:
    """Human-readable summary naming every failed criterion and its real
    values — the `error` string a gate-failed calibration persists."""
    reasons = "; ".join(f"{c['criterion']}: {c['reason']}" for c in result.failed_criteria)
    return (
        f"Calibration rejected by the quality gate (policy {result.policy.version}) - "
        f"{reasons} No metric elevation or DSM was written."
    )


# ==========================================================================
# P1-5: calibration residuals at sample locations
# ==========================================================================
#
# Residuals exist ONLY where a real calibration sample exists — never
# interpolated across the image, never a per-pixel raster. Two kinds, never
# conflated:
#   - held-out: the sample's own held-out prediction from P1-2's
#     cross-validation (the fold model fitted WITHOUT that sample's spatial
#     block / point), taken verbatim from `CrossValidationResult` — never
#     recomputed from the production fit;
#   - fit: the production a/b applied to the sample (in-sample, not
#     validation).
# Both use the existing convention: predicted - reference.

RESIDUAL_DEFINITION = (
    "residual = predicted calibrated elevation - reference elevation, in the "
    "calibration reference's own units. Positive: the calibrated surface is higher "
    "than the reference at that sample; negative: lower."
)
RESIDUAL_UNITS = "same units as the calibration reference"
RESIDUALS_DISCLAIMER = (
    "Residuals describe agreement between the calibrated surface and the calibration "
    "reference at calibration sample locations only. They do not independently "
    "establish absolute elevation accuracy; the reference's own error, resolution and "
    "object-vs-ground content are not accounted for. Held-out residuals come from fold "
    "models fitted without the sample's spatial block (DEM) or point (GCP); fit "
    "residuals are in-sample and are not validation."
)
RESIDUAL_KINDS = ("heldout", "fit")


@dataclass(frozen=True)
class CalibrationResiduals:
    """Per-sample residuals, aligned 1:1 with the `CalibrationSamples`."""

    predicted_fit: np.ndarray
    residual_fit: np.ndarray
    predicted_heldout: np.ndarray
    residual_heldout: np.ndarray
    fold_ids: np.ndarray
    inlier_in_production_fit: np.ndarray


def compute_calibration_residuals(
    samples: CalibrationSamples,
    fit: RobustFitResult,
    cross_validation: CrossValidationResult,
) -> CalibrationResiduals:
    """Residuals for every valid sample. Raises ValueError when the
    cross-validation was not feasible (no held-out prediction exists)."""
    if (
        not cross_validation.feasible
        or cross_validation.heldout_predictions is None
        or cross_validation.sample_fold_ids is None
    ):
        raise ValueError("Held-out residuals need a feasible cross-validation result.")
    if cross_validation.heldout_predictions.shape != samples.reference_elevation.shape:
        raise ValueError("Held-out predictions are not aligned with the calibration samples.")
    # Same expressions P1-2 uses: compute_fit_diagnostics (fit) and
    # cross_validate_affine (held-out).
    predicted_fit = fit.scale * samples.relative_depth + fit.offset
    predicted_heldout = cross_validation.heldout_predictions
    return CalibrationResiduals(
        predicted_fit=predicted_fit,
        residual_fit=predicted_fit - samples.reference_elevation,
        predicted_heldout=predicted_heldout,
        residual_heldout=predicted_heldout - samples.reference_elevation,
        fold_ids=cross_validation.sample_fold_ids,
        inlier_in_production_fit=np.asarray(fit.inlier_mask, dtype=bool),
    )


def summarize_residuals(residuals: np.ndarray) -> dict:
    residuals = np.asarray(residuals, dtype="float64")
    return {
        "count": int(residuals.size),
        "mae": float(np.mean(np.abs(residuals))),
        "rmse": float(np.sqrt(np.mean(residuals**2))),
        "bias": float(np.mean(residuals)),
        "min": float(np.min(residuals)),
        "max": float(np.max(residuals)),
    }


def heldout_block_summaries(
    residuals: CalibrationResiduals, cross_validation: CrossValidationResult
) -> list[dict]:
    """DEM: one entry per spatial block (fold), with its position on the
    blocks_per_side x blocks_per_side grid and held-out statistics over the
    samples in it."""
    blocks_per_side = cross_validation.blocks_per_side
    if blocks_per_side is None:
        return []
    out = []
    for fold in cross_validation.folds:
        in_block = residuals.fold_ids == fold.fold_id
        block_residuals = residuals.residual_heldout[in_block]
        out.append(
            {
                "block_id": fold.fold_id,
                "block_row": fold.fold_id // blocks_per_side,
                "block_col": fold.fold_id % blocks_per_side,
                "heldout_count": fold.heldout_count,
                "heldout_rmse": math.sqrt(fold.heldout_sse / fold.heldout_count),
                "heldout_bias": float(np.mean(block_residuals)),
                "heldout_mae": float(np.mean(np.abs(block_residuals))),
            }
        )
    return out


def build_residual_feature_collection(
    samples: CalibrationSamples,
    residuals: CalibrationResiduals,
    *,
    reference_type: str,
) -> dict:
    """RFC 7946 GeoJSON FeatureCollection (WGS84 lon/lat), one Point feature
    per valid calibration sample. Each feature also carries the sample's
    source-grid row/col and source-CRS map coordinates. No interpolation:
    features exist only where a real sample exists."""
    if samples.sample_map_xs is None or samples.sample_map_ys is None:
        raise ValueError("Residual features need each sample's source map coordinates.")
    if samples.sample_rows is None or samples.sample_cols is None:
        raise ValueError("Residual features need each sample's source row/column.")
    if reference_type == "gcp" and samples.source_point_indices is None:
        raise ValueError("GCP residual features need each sample's source point index.")

    source_crs = CRS.from_user_input(samples.source_crs)
    wgs84 = CRS.from_epsg(4326)
    if source_crs == wgs84:
        lons, lats = samples.sample_map_xs.tolist(), samples.sample_map_ys.tolist()
    else:
        lons, lats = warp_transform(
            source_crs, wgs84, samples.sample_map_xs.tolist(), samples.sample_map_ys.tolist()
        )

    features = []
    for i in range(samples.valid_count):
        properties = {
            "sample_index": i,
            "row": int(samples.sample_rows[i]),
            "col": int(samples.sample_cols[i]),
            "source_x": float(samples.sample_map_xs[i]),
            "source_y": float(samples.sample_map_ys[i]),
            "relative_depth": float(samples.relative_depth[i]),
            "reference_elevation": float(samples.reference_elevation[i]),
            "predicted_heldout": float(residuals.predicted_heldout[i]),
            "residual_heldout": float(residuals.residual_heldout[i]),
            "predicted_fit": float(residuals.predicted_fit[i]),
            "residual_fit": float(residuals.residual_fit[i]),
            "inlier_in_production_fit": bool(residuals.inlier_in_production_fit[i]),
            "fold_id": int(residuals.fold_ids[i]),
        }
        if reference_type == "dem":
            properties["block_id"] = int(residuals.fold_ids[i])
            if samples.reference_cell_ids is not None:
                properties["reference_cell_id"] = int(samples.reference_cell_ids[i])
        else:
            properties["gcp_index"] = int(samples.source_point_indices[i])
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [float(lons[i]), float(lats[i])]},
                "properties": properties,
            }
        )
    return {"type": "FeatureCollection", "features": features}
