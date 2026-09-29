"""Phase 5: extracts a real, downsampled height grid from an actual DSM (or
metric elevation) artifact for interactive 3D rendering, plus a real local
metric coordinate frame for the raster's footprint.

Phase 10: `extract_terrain_grid` is artifact-type-agnostic by construction —
it reads band 1 of whatever single-band raster it's given, with no
dependency on that raster being a calibrated DSM. This is reused as-is to
also build a 3D mesh from a job's real `relative_depth` artifact when no
calibration has succeeded — see `app/services/visualization.py`'s
`height_kind_for_artifact_type`/`get_terrain`, which decide (never this
module) what a raster's values scientifically mean.

Independent of the backend/FastAPI layer by design, like the rest of
geospatial/ — the backend imports this module, never the reverse.

Never generates or approximates elevation values — every returned cell is
either a real (decimated-average) sample of the stored raster or explicitly
NaN (nodata/invalid, never coerced to 0). See docs/ARCHITECTURE.md §3.5 for
the full CRS/local-frame rationale.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import rasterio
from rasterio.crs import CRS
from rasterio.enums import Resampling
from rasterio.errors import RasterioIOError
from rasterio.warp import calculate_default_transform, reproject

from geospatial.exceptions import RasterValidationError


@dataclass(frozen=True)
class TerrainGrid:
    width: int  # actual grid width returned (after downsampling/reprojection)
    height: int
    source_width: int  # the DSM's real, original pixel dimensions
    source_height: int
    elevations: np.ndarray  # float32 (height, width); NaN where nodata/invalid — never fabricated
    min_elevation: float | None  # real finite min of the returned grid
    max_elevation: float | None
    nodata_present: bool
    crs: str | None  # the DSM's own CRS, for display context (None if not georeferenced)
    is_georeferenced: bool
    local_crs: str | None  # the projected CRS the local x/y frame below is actually expressed in
    origin_x: float | None  # map-space coordinate (in local_crs units) of grid cell (0, 0)'s corner
    origin_y: float | None
    cell_size_x: float | None  # local units per grid cell, along +x (columns)
    cell_size_y: float | None  # local units per grid cell, along +y (rows; negative for north-up)
    bounds: tuple[float, float, float, float] | None  # original CRS bounds, for map-context display


def grid_is_reprojected(crs: CRS | None) -> bool:
    """Whether `extract_terrain_grid` reprojects a raster in `crs` (into a
    local UTM CRS) instead of keeping its own pixel grid — the single
    definition, used by the extraction itself and by texture compatibility."""
    return crs is not None and crs.is_geographic


@dataclass(frozen=True)
class TextureCompatibility:
    """D2: whether an RGB source raster can be draped on the terrain grid
    pixel-for-pixel. `code` is machine-readable, `reason` human-readable;
    both None iff `compatible`."""

    compatible: bool
    code: str | None = None
    reason: str | None = None


def _incompatible(code: str, reason: str) -> TextureCompatibility:
    return TextureCompatibility(compatible=False, code=code, reason=reason)


def rgb_texture_compatibility(
    source_path: str | Path | None, terrain_path: str | Path
) -> TextureCompatibility:
    """D2: the ONE definition of "texture compatible", used by the browser
    3D view (via the visualization context) and the GLB export. Decided from
    the rasters' own provenance (headers), never from dimensions alone:

    - the source is a readable raster with >= 3 bands, the first 3 uint8;
    - the terrain raster is not reprojected by `extract_terrain_grid`
      (`grid_is_reprojected`) — a reprojected grid's cells are no longer the
      source's pixels, even when every array dimension still matches;
    - source and terrain raster have the same dimensions, the same
      georeferenced-ness, CRS and affine transform, so terrain pixel (r, c)
      IS source pixel (r, c) (the grid's decimation is then a uniform
      rescale of both, which the texture's normalised UVs follow exactly).

    `source_path=None` means there is no source raster image."""
    if source_path is None:
        return _incompatible("no_source_image", "No source image is available for this terrain.")
    try:
        with rasterio.open(terrain_path) as terrain:
            t_size, t_crs, t_transform = (
                (terrain.width, terrain.height),
                terrain.crs,
                terrain.transform,
            )
    except RasterioIOError as exc:
        return _incompatible("terrain_unreadable", f"The terrain raster could not be read: {exc}")
    try:
        with rasterio.open(source_path) as src:
            s_size, s_crs, s_transform = (src.width, src.height), src.crs, src.transform
            s_count, s_dtypes = src.count, src.dtypes
    except RasterioIOError as exc:
        return _incompatible("source_unreadable", f"The source image could not be read: {exc}")
    if grid_is_reprojected(t_crs):
        return _incompatible(
            "reprojected_terrain_grid",
            "The terrain grid was reprojected from a geographic source, so the source image "
            "no longer aligns with it pixel-for-pixel.",
        )
    if s_size != t_size:
        return _incompatible(
            "dimension_mismatch", "The source image size differs from the terrain raster's size."
        )
    if s_count < 3 or any(dtype != "uint8" for dtype in s_dtypes[:3]):
        return _incompatible("not_rgb_uint8", "The source image is not 8-bit RGB.")
    if (s_crs is None) != (t_crs is None):
        return _incompatible(
            "georeferencing_mismatch",
            "Only one of the source image and the terrain raster is georeferenced.",
        )
    if s_crs is not None and s_crs != t_crs:
        return _incompatible(
            "crs_mismatch", "The source image and the terrain raster use different CRSs."
        )
    if not s_transform.almost_equals(t_transform):
        return _incompatible(
            "transform_mismatch",
            "The source image and the terrain raster have different pixel grids (transforms).",
        )
    return TextureCompatibility(compatible=True)


def select_utm_crs(center_lon: float, center_lat: float) -> CRS:
    """Standard UTM zone selection (a well-defined rule, not a coordinate
    approximation) — the actual coordinate transform is performed by
    rasterio/GDAL/PROJ in `reproject()` below, never hand-rolled here.

    Public (not module-private) because `geospatial/measurements.py` reuses
    this exact selection rule for Phase 7's geographic-CRS distance/profile
    reprojection — the same real technique, not a second, divergent one.
    """
    zone = int(math.floor((center_lon + 180.0) / 6.0) % 60) + 1
    epsg = (32600 if center_lat >= 0 else 32700) + zone
    return CRS.from_epsg(epsg)


def extract_terrain_grid(file_path: str | Path, *, max_dimension: int) -> TerrainGrid:
    """Reads band 1 of the raster at `file_path` (the DSM/metric elevation
    artifact), downsampled to at most `max_dimension` px on its longest
    side via a decimated, block-averaged read (never a full-resolution load
    for a large raster). For a georeferenced-but-geographic (lat/lon) CRS,
    reprojects that already-small grid into a real local UTM CRS so 3D
    vertex coordinates never carry raw degree values; for an already
    projected CRS, uses its real transform directly, re-centered to a local
    per-grid origin. For a non-georeferenced source, returns plain pixel
    coordinates (cell_size=1, origin=0) — never a fabricated CRS.
    """
    try:
        with rasterio.open(file_path) as dataset:
            source_width, source_height = dataset.width, dataset.height
            longest = max(source_width, source_height, 1)
            scale = min(1.0, max_dimension / longest)
            out_height = max(1, round(source_height * scale))
            out_width = max(1, round(source_width * scale))

            band = dataset.read(1, out_shape=(out_height, out_width), resampling=Resampling.average)
            nodata = dataset.nodata
            mask = np.isfinite(band)
            if nodata is not None:
                mask &= band != nodata
            elevations = np.where(mask, band, np.nan).astype("float32")
            has_finite = bool(mask.any())
            min_elevation = float(elevations[mask].min()) if has_finite else None
            max_elevation = float(elevations[mask].max()) if has_finite else None
            nodata_present = bool((~mask).any())

            crs = dataset.crs
            if crs is None:
                return TerrainGrid(
                    width=out_width,
                    height=out_height,
                    source_width=source_width,
                    source_height=source_height,
                    elevations=elevations,
                    min_elevation=min_elevation,
                    max_elevation=max_elevation,
                    nodata_present=nodata_present,
                    crs=None,
                    is_georeferenced=False,
                    local_crs=None,
                    origin_x=0.0,
                    origin_y=0.0,
                    cell_size_x=1.0,
                    cell_size_y=1.0,
                    bounds=None,
                )

            crs_str = f"EPSG:{crs.to_epsg()}" if crs.to_epsg() is not None else crs.to_string()
            src_bounds = dataset.bounds
            bounds_tuple = (src_bounds.left, src_bounds.bottom, src_bounds.right, src_bounds.top)
            # The transform of the *decimated* grid actually read above —
            # rasterio's own documented idiom for pairing an out_shape read
            # with its correct map-space transform.
            decimated_transform = dataset.transform * dataset.transform.scale(
                source_width / out_width, source_height / out_height
            )

            if grid_is_reprojected(crs):
                center_lon = (src_bounds.left + src_bounds.right) / 2
                center_lat = (src_bounds.bottom + src_bounds.top) / 2
                local_crs = select_utm_crs(center_lon, center_lat)
                dst_transform, dst_width, dst_height = calculate_default_transform(
                    crs, local_crs, out_width, out_height, *src_bounds
                )
                reprojected = np.full((dst_height, dst_width), np.nan, dtype="float32")
                reproject(
                    source=elevations,
                    destination=reprojected,
                    src_transform=decimated_transform,
                    src_crs=crs,
                    src_nodata=np.nan,
                    dst_transform=dst_transform,
                    dst_crs=local_crs,
                    dst_nodata=np.nan,
                    resampling=Resampling.bilinear,
                )
                elevations = reprojected
                out_width, out_height = dst_width, dst_height
                mask = np.isfinite(elevations)
                has_finite = bool(mask.any())
                min_elevation = float(elevations[mask].min()) if has_finite else None
                max_elevation = float(elevations[mask].max()) if has_finite else None
                nodata_present = bool((~mask).any())
                origin_x, origin_y = dst_transform.c, dst_transform.f
                cell_size_x, cell_size_y = dst_transform.a, dst_transform.e
                local_crs_str = local_crs.to_string()
            else:
                origin_x, origin_y = decimated_transform.c, decimated_transform.f
                cell_size_x, cell_size_y = decimated_transform.a, decimated_transform.e
                local_crs_str = crs_str

            return TerrainGrid(
                width=out_width,
                height=out_height,
                source_width=source_width,
                source_height=source_height,
                elevations=elevations,
                min_elevation=min_elevation,
                max_elevation=max_elevation,
                nodata_present=nodata_present,
                crs=crs_str,
                is_georeferenced=True,
                local_crs=local_crs_str,
                origin_x=float(origin_x),
                origin_y=float(origin_y),
                cell_size_x=float(cell_size_x),
                cell_size_y=float(cell_size_y),
                bounds=bounds_tuple,
            )
    except (RasterioIOError, ValueError) as exc:
        raise RasterValidationError(f"Could not read DSM raster: {exc}") from exc
