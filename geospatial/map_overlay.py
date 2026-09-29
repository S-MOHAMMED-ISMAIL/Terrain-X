"""P1-6: presentation-only 2D map overlays in the web map's own projection.

The Leaflet 2D map is EPSG:3857 (Web Mercator), and an image overlay is
stretched LINEARLY in Web Mercator between its two corner coordinates. A
raster in any other CRS placed that way (the pre-P1-6 behaviour: the source
preview stretched over its WGS84 envelope) is drawn in the wrong place
whenever its grid is rotated relative to Web Mercator — e.g. a UTM scene
away from its zone's central meridian.

This module draws a raster onto an axis-aligned EPSG:3857 grid (GDAL's
suggested resolution from `rasterio.warp.calculate_default_transform`,
covering the raster's full footprint envelope) and reports that grid's exact
corners, so the overlay's linear stretch is exact. Each overlay pixel takes
the value of the source pixel its centre falls in (nearest neighbour — every
drawn pixel is a real source value, never an interpolated one). The centre is
carried into the raster's own CRS with an EXACT PROJ transform
(`rasterio.warp.transform`, the same one `coordinate_to_pixel` uses) and
located with the raster's own inverse affine transform. GDAL's
`rasterio.warp.reproject` is deliberately not used for this: in the pinned
rasterio (1.4.x) it always goes through GDAL's approximate transformer
(0.125 px error), which picks a neighbouring source pixel at pixel
boundaries — so the drawn overlay could disagree with the pixel a click
resolves to.

A map overlay is a DISPLAY derivative only. It is never read back for any
measurement, elevation, slope or other value: analytical pixel resolution
always uses the authoritative source raster through
`geospatial.measurements.coordinate_to_pixel`.
"""

from __future__ import annotations

import io
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import rasterio
from affine import Affine
from PIL import Image
from rasterio.crs import CRS
from rasterio.enums import Resampling
from rasterio.errors import RasterioIOError
from rasterio.transform import array_bounds, from_origin
from rasterio.warp import calculate_default_transform, transform_bounds
from rasterio.warp import transform as warp_transform

from geospatial.exceptions import RasterValidationError
from geospatial.raster_preview import (
    _colorize_single_band,
    _finite_mask,
    _render_categorical_array,
)

WEB_MERCATOR = CRS.from_epsg(3857)
WGS84 = CRS.from_epsg(4326)


@dataclass(frozen=True)
class WebMercatorGrid:
    """The EPSG:3857 grid a raster's map overlay is drawn on."""

    transform: Affine  # EPSG:3857, north-up, axis-aligned
    width: int
    height: int
    # Exact WGS84 corners of that grid: (west, south, east, north).
    bounds_wgs84: tuple[float, float, float, float]
    # The (possibly decimated) source read the overlay is drawn from.
    source_width: int
    source_height: int
    source_transform: Affine


def _decimated_shape(width: int, height: int, max_dimension: int) -> tuple[int, int]:
    """Same rounding rule as `raster_preview.generate_preview_png`."""
    scale = min(1.0, max_dimension / max(width, height, 1))
    return max(1, round(height * scale)), max(1, round(width * scale))


def _grid_for(dataset, *, max_dimension: int) -> WebMercatorGrid:
    if dataset.crs is None:
        raise RasterValidationError(
            "This raster is not georeferenced, so it has no geographic map overlay; "
            "it is shown in local pixel coordinates instead."
        )
    out_height, out_width = _decimated_shape(dataset.width, dataset.height, max_dimension)
    source_transform = dataset.transform * Affine.scale(
        dataset.width / out_width, dataset.height / out_height
    )
    left, bottom, right, top = array_bounds(out_height, out_width, source_transform)
    # GDAL's suggested resolution, but the grid is sized to the full
    # (densified) footprint envelope with ceil — calculate_default_transform's
    # own width/height can stop short of a corner and clip the overlay.
    suggested, _w, _h = calculate_default_transform(
        dataset.crs,
        WEB_MERCATOR,
        out_width,
        out_height,
        left=left,
        bottom=bottom,
        right=right,
        top=top,
    )
    res_x, res_y = suggested.a, -suggested.e
    env_left, env_bottom, env_right, env_top = transform_bounds(
        dataset.crs, WEB_MERCATOR, left, bottom, right, top, densify_pts=101
    )
    dst_width = max(1, math.ceil((env_right - env_left) / res_x))
    dst_height = max(1, math.ceil((env_top - env_bottom) / res_y))
    dst_transform = from_origin(env_left, env_top, res_x, res_y)
    west_m, north_m = dst_transform * (0, 0)
    east_m, south_m = dst_transform * (dst_width, dst_height)
    lons, lats = warp_transform(WEB_MERCATOR, WGS84, [west_m, east_m], [south_m, north_m])
    return WebMercatorGrid(
        transform=dst_transform,
        width=int(dst_width),
        height=int(dst_height),
        bounds_wgs84=(lons[0], lats[0], lons[1], lats[1]),
        source_width=out_width,
        source_height=out_height,
        source_transform=source_transform,
    )


def web_mercator_grid(file_path: str | Path, *, max_dimension: int) -> WebMercatorGrid:
    """The overlay grid for a raster, from its header only (no pixel read).
    Raises RasterValidationError for a non-georeferenced raster."""
    try:
        with rasterio.open(file_path) as dataset:
            return _grid_for(dataset, max_dimension=max_dimension)
    except (RasterioIOError, ValueError) as exc:
        raise RasterValidationError(f"Could not read file as a raster: {exc}") from exc


def _source_pixel_of_each_overlay_centre(
    grid: WebMercatorGrid, src_crs
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(row, col, inside) in the decimated source read, one entry per overlay
    pixel (row-major), for the source pixel each overlay pixel centre lies in."""
    rows, cols = np.meshgrid(np.arange(grid.height), np.arange(grid.width), indexing="ij")
    mx, my = grid.transform * (cols.ravel() + 0.5, rows.ravel() + 0.5)
    xs, ys = warp_transform(WEB_MERCATOR, src_crs, mx, my)
    fcol, frow = ~grid.source_transform * (np.asarray(xs), np.asarray(ys))
    finite = np.isfinite(fcol) & np.isfinite(frow)
    src_col = np.floor(np.where(finite, fcol, -1)).astype(np.int64)
    src_row = np.floor(np.where(finite, frow, -1)).astype(np.int64)
    inside = (
        finite
        & (src_col >= 0)
        & (src_col < grid.source_width)
        & (src_row >= 0)
        & (src_row < grid.source_height)
    )
    return src_row, src_col, inside


def _draw(band: np.ndarray, sample, fill, dtype) -> np.ndarray:
    src_row, src_col, inside, shape = sample
    out = np.full(inside.shape, fill, dtype=dtype)
    out[inside] = band[src_row[inside], src_col[inside]]
    return out.reshape(shape)


def generate_web_mercator_overlay_png(
    file_path: str | Path, *, max_dimension: int, categorical: bool
) -> tuple[bytes, WebMercatorGrid]:
    """Renders the raster onto its EPSG:3857 overlay grid. Outside the
    raster's real footprint and at NoData the PNG is fully transparent.

    - categorical rasters: IDs are carried over unchanged and coloured
      exactly as `generate_categorical_preview_png`;
    - 3+-band uint8 imagery: RGB passthrough, alpha from the real footprint;
    - anything else: band 1 coloured with the continuous ramp, normalized by
      the source read's own finite min/max (as the source-grid preview is).
    """
    try:
        with rasterio.open(file_path) as dataset:
            grid = _grid_for(dataset, max_dimension=max_dimension)
            out_shape = (grid.source_height, grid.source_width)
            nodata = dataset.nodata
            src_row, src_col, inside = _source_pixel_of_each_overlay_centre(grid, dataset.crs)
            sample = (src_row, src_col, inside, (grid.height, grid.width))

            if categorical:
                band = dataset.read(1, out_shape=out_shape, resampling=Resampling.nearest)
                background = 0 if nodata is None else nodata
                image = _render_categorical_array(
                    _draw(band, sample, background, band.dtype), background
                )
            elif dataset.count >= 3 and dataset.dtypes[0] == "uint8":
                bands = dataset.read(
                    [1, 2, 3], out_shape=(3, *out_shape), resampling=Resampling.nearest
                )
                rgba = np.zeros((grid.height, grid.width, 4), dtype=np.uint8)
                for i in range(3):
                    rgba[..., i] = _draw(bands[i], sample, 0, np.uint8)
                rgba[..., 3] = np.where(inside, 255, 0).astype(np.uint8).reshape(grid.height, -1)
                image = Image.fromarray(rgba, mode="RGBA")
            else:
                band = dataset.read(1, out_shape=out_shape, resampling=Resampling.nearest).astype(
                    "float64"
                )
                valid_mask = _finite_mask(band, nodata)
                valid = band[valid_mask]
                value_range = (float(valid.min()), float(valid.max())) if valid.size else None
                band = np.where(valid_mask, band, np.nan)  # NoData -> transparent
                drawn = _draw(band, sample, np.nan, "float64")
                image = Image.fromarray(
                    _colorize_single_band(drawn, None, value_range), mode="RGBA"
                )
    except (RasterioIOError, ValueError) as exc:
        raise RasterValidationError(f"Could not read file as a raster: {exc}") from exc

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue(), grid
