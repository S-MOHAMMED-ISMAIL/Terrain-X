"""P1-3: raster bare-earth APPROXIMATION (DTM) and normalized DSM (nDSM) from
a calibrated surface-elevation raster, via a raster progressive
morphological filter (PMF, after Zhang et al. 2003). Pure numpy — no scipy.

## What this is, and is not

TERRAIN-X's only elevation input is ONE calibrated monocular surface raster
(`dsm`, numerically identical to `metric_elevation`). There is no point
cloud, no multiple returns, no stereo and no classified ground. So no true
DTM can be measured here. This module estimates bare earth purely from the
surface's own geometry:

- `dtm`  = estimated bare-earth elevation (a raster-filter approximation).
- `ndsm` = `dsm - dtm` = estimated height of the surface above that
  estimated ground. Not a measured object height.

## Algorithm (Zhang et al. 2003, raster form)

1. A cell is valid if finite (and not the source NoData value). Invalid cells
   are EXCLUDED from every morphological window (+inf for erosion, -inf for
   dilation) — never fed in as a sentinel value.
2. Horizontal cell size in metres is measured on the input's own grid (no
   reprojection, so outputs stay cell-aligned with the DSM).
3. Windows grow per level (half-widths 1, 2, 4, 8, ... cells of the coarser
   axis, converted to cells per axis), up to `max_window_m`. Each level's
   elevation-difference threshold is `dh_1 = dh0`,
   `dh_k = min(s * (size_k - size_{k-1}) + dh0, dh_max)`.
4. `S_0 = DSM`; at each level `O_k = opening(S_{k-1}, w_k)` (square, flat,
   separable window) and a cell becomes non-ground once
   `S_{k-1} - O_k > dh_k`; then `S_k = O_k`.
5. `DTM = DSM` on ground cells and `S_K` (the final opened surface) on
   non-ground cells; NoData wherever the DSM is invalid.

Grey-scale opening never exceeds its input, and the openings are chained, so
`DTM <= DSM` at every valid cell BY CONSTRUCTION; hence `nDSM >= 0`, and
`nDSM == 0` exactly on cells classified as ground. The DTM's values are all
values the DSM itself contains (erosion/dilation only select values), so the
subtraction is exact in float32. A negative nDSM would therefore be a bug and
fails the filter rather than being clipped.

## Assumptions and failure modes (persisted with every result)

- Objects are narrower than `max_window_m`; wider objects are kept as ground.
- Terrain slope is at most `s`; steeper narrow ridges/hilltops are partly
  treated as objects and cut down.
- Objects rising less than `dh0` above their surroundings are kept as ground.
- Under an object on sloping ground the fill is the flat opened surface, so
  nDSM there is overestimated on the downhill side.
- D3: window sizes, the slope parameter and the thresholds are physical
  METRES. The DSM's vertical unit must be declared (`vertical_unit`, from
  geospatial/vertical_units.py — never assumed); a threshold dh (m) is applied
  to the native values as dh / to_metre. Grey opening commutes with a
  positive scale, so this equals filtering the DSM converted to metres, while
  the DTM/nDSM stay in the DSM's own unit, exact (DTM values are DSM values).
- A monocular calibrated surface is smooth and represents objects only
  approximately; nothing here validates object heights.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

import numpy as np
from affine import Affine
from rasterio.crs import CRS
from rasterio.warp import transform as warp_transform

from geospatial.terrain_grid import select_utm_crs
from geospatial.vertical_units import CALCULATION_UNIT, VerticalUnit

GROUND_FILTER_NODATA = -9999.0
# nDSM is exactly >= 0 by construction (see module docstring); any valid cell
# below this is a real invariant violation, reported as a failure.
NDSM_NEGATIVE_TOLERANCE = 1e-6

PMF_METHOD = (
    "raster_progressive_morphological_filter (Zhang et al. 2003), square separable "
    "van Herk/Gil-Werman openings, opened-surface fill under non-ground cells"
)


class GroundFilterError(Exception):
    """A real, expected reason ground filtering cannot produce a result."""


@dataclass(frozen=True)
class GroundFilterParameters:
    max_window_m: float
    slope: float  # terrain slope assumption s (dz/dx, dimensionless)
    initial_threshold: float  # dh0, metres (D3)
    max_threshold: float  # dh_max, metres (D3)

    def validate(self) -> None:
        if not (math.isfinite(self.max_window_m) and self.max_window_m > 0):
            raise GroundFilterError("max_window_m must be a positive, finite number.")
        if not (math.isfinite(self.slope) and self.slope >= 0):
            raise GroundFilterError("slope must be a non-negative, finite number.")
        if not (math.isfinite(self.initial_threshold) and self.initial_threshold >= 0):
            raise GroundFilterError("initial_threshold must be a non-negative, finite number.")
        if not (math.isfinite(self.max_threshold) and self.max_threshold >= self.initial_threshold):
            raise GroundFilterError("max_threshold must be finite and >= initial_threshold.")


@dataclass(frozen=True)
class CellSize:
    x_m: float
    y_m: float
    method: str  # "projected_linear_units" | "geographic_local_utm"
    crs_linear_unit: str | None


@dataclass(frozen=True)
class WindowLevel:
    level: int
    half_rows: int
    half_cols: int
    window_rows: int
    window_cols: int
    size_m: float  # nominal window size along the coarser axis
    threshold: float  # dh_k, metres (D3)
    clamped_to_raster: bool


@dataclass(frozen=True)
class GroundFilterStatistics:
    valid_count: int
    ground_count: int
    ground_fraction: float
    dtm_min: float
    dtm_max: float
    dtm_mean: float
    ndsm_min: float
    ndsm_max: float
    ndsm_mean: float
    ndsm_p95: float


@dataclass(frozen=True)
class GroundFilterResult:
    dtm: np.ndarray  # float32, GROUND_FILTER_NODATA where invalid
    ndsm: np.ndarray  # float32, GROUND_FILTER_NODATA where invalid
    ground_mask: np.ndarray  # bool, True on valid cells classified as ground
    valid_mask: np.ndarray  # bool
    cell_size: CellSize
    levels: tuple[WindowLevel, ...]
    statistics: GroundFilterStatistics
    parameters: GroundFilterParameters
    vertical_unit: VerticalUnit

    def metadata(self) -> dict:
        return {
            "method": PMF_METHOD,
            "parameters": asdict(self.parameters),
            "cell_size": asdict(self.cell_size),
            "window_levels": [asdict(level) for level in self.levels],
            "statistics": asdict(self.statistics),
            "nodata": GROUND_FILTER_NODATA,
            # D3: thresholds/windows in metres; DTM/nDSM and the statistics
            # above in the DSM's own (declared) vertical unit.
            "threshold_unit": CALCULATION_UNIT.code,
            "window_unit": CALCULATION_UNIT.code,
            "source_vertical_unit": self.vertical_unit.code,
            "vertical_conversion_factor": self.vertical_unit.to_metre,
            "calculation_vertical_unit": CALCULATION_UNIT.code,
            "output_vertical_unit": self.vertical_unit.code,
        }


def metric_cell_size(
    crs: CRS | None, transform: Affine | None, width: int, height: int
) -> CellSize:
    """Horizontal cell size in metres, measured on the raster's own grid.

    Projected CRS: pixel size times the CRS's linear-unit factor (so a
    US-foot CRS is converted). Geographic CRS: the centre pixel's
    one-column and one-row offsets measured in the local UTM zone. A
    non-georeferenced or rotated grid is rejected — there is no honest
    metric window for it."""
    if crs is None or transform is None:
        raise GroundFilterError("Ground filtering needs a georeferenced elevation raster.")
    if transform.b != 0 or transform.d != 0:
        raise GroundFilterError("Rotated/sheared raster grids are not supported.")

    if crs.is_geographic:
        col, row = width / 2.0, height / 2.0
        xs, ys = transform * (
            np.array([col, col + 1.0, col], dtype="float64"),
            np.array([row, row, row + 1.0], dtype="float64"),
        )
        utm = select_utm_crs(float(xs[0]), float(ys[0]))
        ux, uy = warp_transform(crs, utm, list(map(float, xs)), list(map(float, ys)))
        x_m = math.hypot(ux[1] - ux[0], uy[1] - uy[0])
        y_m = math.hypot(ux[2] - ux[0], uy[2] - uy[0])
        return CellSize(x_m=x_m, y_m=y_m, method="geographic_local_utm", crs_linear_unit=None)

    if crs.is_projected:
        unit_name, factor = crs.linear_units_factor
        return CellSize(
            x_m=abs(transform.a) * factor,
            y_m=abs(transform.e) * factor,
            method="projected_linear_units",
            crs_linear_unit=unit_name,
        )

    raise GroundFilterError(f"Unsupported CRS for ground filtering: {crs.to_string()}")


def _running_extreme_last_axis(values: np.ndarray, window: int, ufunc, fill: float) -> np.ndarray:
    """Centred running min/max of odd `window` along the last axis in O(n),
    van Herk/Gil-Werman: per window-sized block, a prefix and a suffix
    accumulation; any window spans at most two blocks, so its extreme is
    ufunc(suffix[start], prefix[end]). Out-of-raster positions are `fill`."""
    rows, n = values.shape
    radius = window // 2
    length = n + 2 * radius
    total = -(-length // window) * window
    padded = np.full((rows, total), fill, dtype=values.dtype)
    padded[:, radius : radius + n] = values
    blocks = padded.reshape(rows, -1, window)
    prefix = ufunc.accumulate(blocks, axis=2).reshape(rows, total)
    suffix = ufunc.accumulate(blocks[:, :, ::-1], axis=2)[:, :, ::-1].reshape(rows, total)
    return ufunc(suffix[:, :n], prefix[:, window - 1 : window - 1 + n])


def _running_extreme(
    array: np.ndarray, window_rows: int, window_cols: int, ufunc, fill: float
) -> np.ndarray:
    if window_rows < 1 or window_cols < 1 or window_rows % 2 == 0 or window_cols % 2 == 0:
        raise ValueError("Window sizes must be positive odd integers.")
    work = np.where(np.isfinite(array), array, fill).astype("float32")
    work = _running_extreme_last_axis(work, window_cols, ufunc, fill)
    work = _running_extreme_last_axis(work.T.copy(), window_rows, ufunc, fill).T
    return np.where(np.isinf(work), np.nan, work).astype("float32")


def running_min(array: np.ndarray, window_rows: int, window_cols: int) -> np.ndarray:
    """NaN-aware centred rectangular running minimum: NaN cells are ignored;
    a window containing no finite cell yields NaN."""
    return _running_extreme(array, window_rows, window_cols, np.minimum, np.inf)


def running_max(array: np.ndarray, window_rows: int, window_cols: int) -> np.ndarray:
    """NaN-aware centred rectangular running maximum (see running_min)."""
    return _running_extreme(array, window_rows, window_cols, np.maximum, -np.inf)


def grey_opening(surface: np.ndarray, window_rows: int, window_cols: int) -> np.ndarray:
    """Flat grey-scale opening (erosion then dilation, same window) over the
    finite cells of `surface`. NaN wherever `surface` is NaN. For every
    finite cell, opening <= surface (a symmetric window around any cell is
    contained in the dilation window that reaches back to it)."""
    valid = np.isfinite(surface)
    opened = running_max(running_min(surface, window_rows, window_cols), window_rows, window_cols)
    return np.where(valid, opened, np.nan).astype("float32")


def window_schedule(
    cell_size: CellSize, parameters: GroundFilterParameters, height: int, width: int
) -> tuple[WindowLevel, ...]:
    """Deterministic PMF window/threshold schedule (see module docstring)."""
    coarse = max(cell_size.x_m, cell_size.y_m)
    if not (math.isfinite(coarse) and coarse > 0):
        raise GroundFilterError("Could not determine a positive metric cell size.")
    max_half = int((parameters.max_window_m / coarse - 1.0) // 2)
    if max_half < 1:
        raise GroundFilterError(
            f"max_window_m={parameters.max_window_m:g} m is smaller than a 3-cell window "
            f"at this raster's ~{coarse:.4g} m cell size."
        )

    halves: list[int] = []
    half = 1
    while half < max_half:
        halves.append(half)
        half *= 2
    halves.append(max_half)

    levels: list[WindowLevel] = []
    previous_size = None
    seen: set[tuple[int, int]] = set()
    for half in halves:
        half_m = half * coarse
        half_rows = max(1, round(half_m / cell_size.y_m))
        half_cols = max(1, round(half_m / cell_size.x_m))
        clamped = False
        if half_rows > (height - 1) // 2 or half_cols > (width - 1) // 2:
            half_rows = max(1, min(half_rows, (height - 1) // 2))
            half_cols = max(1, min(half_cols, (width - 1) // 2))
            clamped = True
        if (half_rows, half_cols) in seen:
            continue
        seen.add((half_rows, half_cols))
        size_m = (2 * half + 1) * coarse
        if previous_size is None:
            threshold = parameters.initial_threshold
        else:
            threshold = min(
                parameters.slope * (size_m - previous_size) + parameters.initial_threshold,
                parameters.max_threshold,
            )
        levels.append(
            WindowLevel(
                level=len(levels) + 1,
                half_rows=half_rows,
                half_cols=half_cols,
                window_rows=2 * half_rows + 1,
                window_cols=2 * half_cols + 1,
                size_m=size_m,
                threshold=threshold,
                clamped_to_raster=clamped,
            )
        )
        previous_size = size_m
    return tuple(levels)


def _statistics(
    dtm: np.ndarray, ndsm: np.ndarray, valid: np.ndarray, ground: np.ndarray
) -> GroundFilterStatistics:
    valid_count = int(valid.sum())
    d, n = dtm[valid].astype("float64"), ndsm[valid].astype("float64")
    return GroundFilterStatistics(
        valid_count=valid_count,
        ground_count=int(ground.sum()),
        ground_fraction=float(ground.sum()) / valid_count,
        dtm_min=float(d.min()),
        dtm_max=float(d.max()),
        dtm_mean=float(d.mean()),
        ndsm_min=float(n.min()),
        ndsm_max=float(n.max()),
        ndsm_mean=float(n.mean()),
        ndsm_p95=float(np.percentile(n, 95)),
    )


def progressive_morphological_filter(
    dsm: np.ndarray,
    *,
    crs: CRS | None,
    transform: Affine | None,
    parameters: GroundFilterParameters,
    vertical_unit: VerticalUnit | None,
    nodata: float | None = None,
) -> GroundFilterResult:
    """Runs the raster PMF over `dsm` and returns DTM/nDSM on the SAME grid,
    in the DSM's own vertical unit. `vertical_unit` is REQUIRED (D3): None
    (unknown) refuses to filter rather than assuming metres. Raises
    GroundFilterError for every anticipated failure."""
    if vertical_unit is None:
        raise GroundFilterError(
            "The DSM's vertical unit could not be established from the calibration "
            "reference's metadata, so metric ground filtering (thresholds in metres) "
            "cannot run. It is not assumed to be metres."
        )
    if dsm.ndim != 2:
        raise GroundFilterError(f"Expected a 2D elevation raster, got shape {dsm.shape}.")
    parameters.validate()
    height, width = dsm.shape

    surface = dsm.astype("float32")
    valid = np.isfinite(surface)
    if nodata is not None:
        valid &= surface != np.float32(nodata)
    if not valid.any():
        raise GroundFilterError("The elevation raster has no valid cells to filter.")
    surface = np.where(valid, surface, np.nan).astype("float32")

    cell_size = metric_cell_size(crs, transform, width, height)
    levels = window_schedule(cell_size, parameters, height, width)

    non_ground = np.zeros(surface.shape, dtype=bool)
    current = surface
    for level in levels:
        opened = grey_opening(current, level.window_rows, level.window_cols)
        with np.errstate(invalid="ignore"):
            non_ground |= valid & (
                (current - opened) > np.float32(level.threshold / vertical_unit.to_metre)
            )
        current = opened

    ground = valid & ~non_ground
    dtm = np.where(ground, surface, current)
    ndsm = surface - dtm  # float32 minus values drawn from the same array: exact

    ndsm_valid = ndsm[valid]
    if not np.isfinite(dtm[valid]).all() or not np.isfinite(ndsm_valid).all():
        raise GroundFilterError("Ground filtering produced a non-finite value on a valid cell.")
    if float(ndsm_valid.min()) < -NDSM_NEGATIVE_TOLERANCE:
        raise GroundFilterError(
            f"Invariant violated: DTM exceeds DSM (minimum nDSM {float(ndsm_valid.min()):.6g}); "
            "refusing to clip it."
        )

    dtm_out = np.where(valid, dtm, GROUND_FILTER_NODATA).astype("float32")
    ndsm_out = np.where(valid, ndsm, GROUND_FILTER_NODATA).astype("float32")
    return GroundFilterResult(
        dtm=dtm_out,
        ndsm=ndsm_out,
        ground_mask=ground,
        valid_mask=valid,
        cell_size=cell_size,
        levels=levels,
        statistics=_statistics(dtm, ndsm, valid, ground),
        parameters=parameters,
        vertical_unit=vertical_unit,
    )
