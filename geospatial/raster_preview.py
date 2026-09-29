"""Phase 5 visualization: real raster statistics and presentation-only PNG
previews, generated on demand from an actual stored raster artifact.

Independent of the backend/FastAPI layer by design (see docs/ARCHITECTURE.md)
— the backend imports this module, never the reverse. A preview PNG is a
*display* derivative only: normalized for on-screen viewing, never fed back
into calibration or any other pipeline stage, and never a replacement for
the original GeoTIFF, which remains the sole scientific artifact.
"""

from __future__ import annotations

import colorsys
import io
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import rasterio
from PIL import Image
from rasterio.enums import Resampling
from rasterio.errors import RasterioIOError
from rasterio.windows import Window

from geospatial.exceptions import RasterValidationError

# A fixed, documented 8-stop perceptually-ordered color ramp (dark purple ->
# yellow, modeled on "viridis") for continuous single-band scientific
# rasters (relative depth, metric elevation, DSM). Not sourced from
# matplotlib (not a project dependency) — a small hand-picked control-point
# table linearly interpolated in RGB space. Documented as an approximation,
# not claimed to be colorimetrically identical to any particular library's
# implementation.
_RAMP_POSITIONS = np.array([0.0, 0.14, 0.29, 0.43, 0.57, 0.71, 0.86, 1.0])
_RAMP_COLORS = np.array(
    [
        [68, 1, 84],
        [72, 40, 120],
        [62, 74, 137],
        [49, 104, 142],
        [38, 130, 142],
        [31, 158, 137],
        [53, 183, 121],
        [253, 231, 37],
    ],
    dtype=np.float64,
)


@dataclass(frozen=True)
class RasterStatistics:
    driver: str
    width: int
    height: int
    count: int
    dtype: str
    nodata: float | None
    is_georeferenced: bool
    crs: str | None
    bounds: tuple[float, float, float, float] | None  # min_x, min_y, max_x, max_y
    resolution: tuple[float, float] | None  # pixel size (x, y), map units
    min_value: float | None  # real, computed finite-value min of band 1
    max_value: float | None
    has_finite_data: bool
    stats_sampled: bool  # True if min/max came from a decimated sample, not every source pixel


def _finite_mask(array: np.ndarray, nodata: float | None) -> np.ndarray:
    mask = np.isfinite(array)
    if nodata is not None:
        mask &= array != nodata
    return mask


def compute_raster_statistics(
    file_path: str | Path, *, sample_max_dimension: int = 2048
) -> RasterStatistics:
    """Reads real structural metadata plus a real finite-value min/max for
    band 1. Bounds memory use for a large raster via a decimated read
    (`out_shape`, GDAL-level decimation, never a full in-memory read first)
    when the raster's longest side exceeds `sample_max_dimension` — the
    resulting min/max is still a genuinely computed statistic, just over a
    representative decimated sample rather than every source pixel
    (`stats_sampled=True` says so honestly, exactly like a "do not claim
    unsampled statistics" requirement demands).
    """
    try:
        with rasterio.open(file_path) as dataset:
            width, height = dataset.width, dataset.height
            longest = max(width, height, 1)
            sampled = longest > sample_max_dimension
            if sampled:
                scale = sample_max_dimension / longest
                out_height = max(1, round(height * scale))
                out_width = max(1, round(width * scale))
                band1 = dataset.read(
                    1, out_shape=(out_height, out_width), resampling=Resampling.average
                )
            else:
                band1 = dataset.read(1)

            nodata = dataset.nodata
            mask = _finite_mask(band1, nodata)
            has_finite = bool(mask.any())
            min_value = float(band1[mask].min()) if has_finite else None
            max_value = float(band1[mask].max()) if has_finite else None

            crs = dataset.crs
            is_georeferenced = crs is not None
            crs_str = None
            bounds = None
            resolution = None
            if is_georeferenced:
                epsg = crs.to_epsg()
                crs_str = f"EPSG:{epsg}" if epsg is not None else crs.to_string()
                b = dataset.bounds
                bounds = (b.left, b.bottom, b.right, b.top)
                resolution = (abs(dataset.transform.a), abs(dataset.transform.e))

            return RasterStatistics(
                driver=dataset.driver,
                width=width,
                height=height,
                count=dataset.count,
                dtype=str(dataset.dtypes[0]),
                nodata=nodata,
                is_georeferenced=is_georeferenced,
                crs=crs_str,
                bounds=bounds,
                resolution=resolution,
                min_value=min_value,
                max_value=max_value,
                has_finite_data=has_finite,
                stats_sampled=sampled,
            )
    except (RasterioIOError, ValueError) as exc:
        raise RasterValidationError(f"Could not read file as a raster: {exc}") from exc


def _colorize_single_band(
    band: np.ndarray,
    nodata: float | None,
    value_range: tuple[float, float] | None = None,
) -> np.ndarray:
    """Normalizes a single band by its own real finite min/max (nodata/NaN/
    Inf excluded from both the range computation and the output — those
    pixels get alpha=0, never a fabricated color), then applies the ramp
    above. Returns an (H, W, 4) uint8 RGBA array. `value_range` (P1-6)
    supplies the min/max explicitly — used when `band` is a reprojected
    subset of the raster whose own range should set the colours.
    """
    mask = _finite_mask(band, nodata)
    height, width = band.shape
    rgba = np.zeros((height, width, 4), dtype=np.uint8)
    if not mask.any():
        return rgba  # entirely nodata/invalid — fully transparent, not fabricated color

    if value_range is None:
        valid = band[mask]
        vmin = float(valid.min())
        vmax = float(valid.max())
    else:
        vmin, vmax = value_range
    span = vmax - vmin
    normalized = np.zeros_like(band, dtype=np.float64)
    if span > 0:
        normalized[mask] = (band[mask] - vmin) / span
    # else: constant raster — every valid pixel maps to 0.0 (the ramp's first color), not an error

    r = np.interp(normalized, _RAMP_POSITIONS, _RAMP_COLORS[:, 0])
    g = np.interp(normalized, _RAMP_POSITIONS, _RAMP_COLORS[:, 1])
    b = np.interp(normalized, _RAMP_POSITIONS, _RAMP_COLORS[:, 2])
    rgba[..., 0] = r.astype(np.uint8)
    rgba[..., 1] = g.astype(np.uint8)
    rgba[..., 2] = b.astype(np.uint8)
    rgba[..., 3] = np.where(mask, 255, 0).astype(np.uint8)
    return rgba


def _render_display_array(data: np.ndarray, nodata: float | None) -> Image.Image:
    """`data` is (bands, H, W) as actually read from the raster. Renders:
    - 3 or 4 uint8 bands -> RGB(A) passthrough (alpha band dropped/ignored;
      real image content, never synthesized)
    - anything else -> band 1 treated as a continuous scientific field,
      colorized via `_colorize_single_band`
    """
    count = data.shape[0]
    if count >= 3 and data.dtype == np.uint8:
        rgb = np.transpose(data[:3], (1, 2, 0))
        return Image.fromarray(rgb, mode="RGB")
    rgba = _colorize_single_band(data[0].astype("float64"), nodata)
    return Image.fromarray(rgba, mode="RGBA")


def generate_preview_png(file_path: str | Path, *, max_dimension: int) -> bytes:
    """Reads a decimated (GDAL-level, never a full-resolution load) version
    of the raster, at most `max_dimension` px on its longest side, and
    renders it to a PNG — a presentation derivative only.
    """
    try:
        with rasterio.open(file_path) as dataset:
            width, height = dataset.width, dataset.height
            longest = max(width, height, 1)
            scale = min(1.0, max_dimension / longest)
            out_height = max(1, round(height * scale))
            out_width = max(1, round(width * scale))
            band_count = min(dataset.count, 4)
            data = dataset.read(
                indexes=list(range(1, band_count + 1)),
                out_shape=(band_count, out_height, out_width),
                resampling=Resampling.average,
            )
            image = _render_display_array(data, dataset.nodata)
    except (RasterioIOError, ValueError) as exc:
        raise RasterValidationError(f"Could not read file as a raster: {exc}") from exc

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def generate_window_png(
    file_path: str | Path,
    *,
    col_off: int,
    row_off: int,
    width: int,
    height: int,
    max_dimension: int,
) -> bytes:
    """Reads a real pixel sub-region (clamped to the raster's actual bounds)
    at up to native resolution, downsampled only if the requested window
    exceeds `max_dimension` on its longest side — for closer inspection of a
    specific area without transferring the whole raster.
    """
    try:
        with rasterio.open(file_path) as dataset:
            col_off = max(0, min(col_off, dataset.width - 1))
            row_off = max(0, min(row_off, dataset.height - 1))
            width = max(1, min(width, dataset.width - col_off))
            height = max(1, min(height, dataset.height - row_off))
            window = Window(col_off=col_off, row_off=row_off, width=width, height=height)

            longest = max(width, height, 1)
            scale = min(1.0, max_dimension / longest)
            out_height = max(1, round(height * scale))
            out_width = max(1, round(width * scale))
            band_count = min(dataset.count, 4)
            data = dataset.read(
                indexes=list(range(1, band_count + 1)),
                window=window,
                out_shape=(band_count, out_height, out_width),
                resampling=Resampling.average,
            )
            image = _render_display_array(data, dataset.nodata)
    except (RasterioIOError, ValueError) as exc:
        raise RasterValidationError(f"Could not read file as a raster: {exc}") from exc

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def sample_pixel_value(file_path: str | Path, *, row: int, col: int) -> float | None:
    """Reads the real value of band 1 at one pixel — a tiny (1x1) windowed
    read, not a full-raster load. Returns None for an out-of-bounds pixel or
    a real NoData/NaN/Inf value there (never a fabricated "0" — the caller
    is expected to render that as "No data", per docs/ARCHITECTURE.md §3.5).
    """
    try:
        with rasterio.open(file_path) as dataset:
            if not (0 <= row < dataset.height and 0 <= col < dataset.width):
                return None
            value = float(dataset.read(1, window=Window(col, row, 1, 1))[0, 0])
            nodata = dataset.nodata
            if not np.isfinite(value) or (nodata is not None and value == nodata):
                return None
            return value
    except (RasterioIOError, ValueError) as exc:
        raise RasterValidationError(f"Could not read file as a raster: {exc}") from exc


# --------------------------------------------------------------------------
# Phase 6: categorical (region/segment ID) rendering — deliberately separate
# from the continuous-ramp functions above. Region IDs are categorical
# integers (see geospatial/raster_io.py::write_single_band_categorical) and
# must NEVER be normalized/interpolated by min/max like a continuous
# scientific field — doing so would blend meaningless "averages" between
# unrelated region IDs. Every categorical read below uses
# `Resampling.nearest`, never `Resampling.average`.
# --------------------------------------------------------------------------


def _region_color(region_id: int) -> tuple[int, int, int]:
    """A deterministic, real (not random-per-call) color for a given region
    ID — the same ID always renders the same color, and different IDs are
    visually well-separated, via the well-known "golden-angle hue rotation"
    technique (each successive hue is offset by the golden ratio conjugate,
    which spreads any number of colors around the hue wheel with minimal
    adjacent-hue collisions). This is a display convenience only: the color
    itself carries no meaning (e.g. it never signals "building" or "road")
    — see docs/ARCHITECTURE.md §3.6.
    """
    hue = (region_id * 0.618033988749895) % 1.0
    r, g, b = colorsys.hsv_to_rgb(hue, 0.65, 0.95)
    return int(r * 255), int(g * 255), int(b * 255)


def generate_categorical_preview_png(file_path: str | Path, *, max_dimension: int) -> bytes:
    """Real decimated (nearest-neighbor, never averaged) read of a
    categorical raster, rendered with one deterministic color per distinct
    region ID actually present. Background/NoData (see the raster's own
    `nodata` tag, e.g. 0 for Phase 6 region rasters) is fully transparent
    (alpha=0) — never given a fabricated color.
    """
    try:
        with rasterio.open(file_path) as dataset:
            width, height = dataset.width, dataset.height
            longest = max(width, height, 1)
            scale = min(1.0, max_dimension / longest)
            out_height = max(1, round(height * scale))
            out_width = max(1, round(width * scale))
            band = dataset.read(1, out_shape=(out_height, out_width), resampling=Resampling.nearest)
            nodata = dataset.nodata
            image = _render_categorical_array(band, nodata)
    except (RasterioIOError, ValueError) as exc:
        raise RasterValidationError(f"Could not read file as a raster: {exc}") from exc

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def generate_categorical_window_png(
    file_path: str | Path,
    *,
    col_off: int,
    row_off: int,
    width: int,
    height: int,
    max_dimension: int,
) -> bytes:
    """Same real pixel-sub-region semantics as `generate_window_png`, but
    for a categorical raster — nearest-neighbor only."""
    try:
        with rasterio.open(file_path) as dataset:
            col_off = max(0, min(col_off, dataset.width - 1))
            row_off = max(0, min(row_off, dataset.height - 1))
            width = max(1, min(width, dataset.width - col_off))
            height = max(1, min(height, dataset.height - row_off))
            window = Window(col_off=col_off, row_off=row_off, width=width, height=height)

            longest = max(width, height, 1)
            scale = min(1.0, max_dimension / longest)
            out_height = max(1, round(height * scale))
            out_width = max(1, round(width * scale))
            band = dataset.read(
                1,
                window=window,
                out_shape=(out_height, out_width),
                resampling=Resampling.nearest,
            )
            nodata = dataset.nodata
            image = _render_categorical_array(band, nodata)
    except (RasterioIOError, ValueError) as exc:
        raise RasterValidationError(f"Could not read file as a raster: {exc}") from exc

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _render_categorical_array(band: np.ndarray, nodata: float | None) -> Image.Image:
    height, width = band.shape
    rgba = np.zeros((height, width, 4), dtype=np.uint8)
    background_value = 0 if nodata is None else nodata
    foreground_mask = band != background_value

    for region_id in np.unique(band[foreground_mask]):
        color = _region_color(int(region_id))
        region_mask = band == region_id
        rgba[region_mask, 0] = color[0]
        rgba[region_mask, 1] = color[1]
        rgba[region_mask, 2] = color[2]
        rgba[region_mask, 3] = 255

    return Image.fromarray(rgba, mode="RGBA")
