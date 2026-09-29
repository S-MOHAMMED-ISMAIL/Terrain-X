"""Real raster pixel I/O via rasterio/GDAL — reading arrays for model input
and writing single-band float32 output rasters.

Independent of the backend/FastAPI layer by design (see docs/ARCHITECTURE.md)
— the backend imports this module, never the reverse. Companion to
`geospatial.raster_metadata`, which reads structural metadata only; this
module additionally reads/writes real pixel data.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import rasterio
from rasterio.crs import CRS
from rasterio.errors import RasterioIOError
from rasterio.transform import Affine

from geospatial.exceptions import RasterValidationError


@dataclass(frozen=True)
class RasterArray:
    data: np.ndarray  # shape (bands, height, width), native dtype
    dtype: str
    crs: CRS | None
    transform: Affine | None
    is_georeferenced: bool


def read_raster_array(file_path: str | Path) -> RasterArray:
    """Opens `file_path` with GDAL and reads the real pixel data (all
    bands), plus its real georeferencing state (never fabricated — `crs`/
    `transform` are None unless GDAL actually reports a CRS).

    Raises RasterValidationError if the file cannot be opened/read.
    """
    try:
        with rasterio.open(file_path) as dataset:
            data = dataset.read()
            crs = dataset.crs
            is_georeferenced = crs is not None
            transform = dataset.transform if is_georeferenced else None
            return RasterArray(
                data=data,
                dtype=str(data.dtype),
                crs=crs,
                transform=transform,
                is_georeferenced=is_georeferenced,
            )
    except (RasterioIOError, ValueError) as exc:
        raise RasterValidationError(f"Could not read file as a raster: {exc}") from exc


def write_single_band_float32(
    file_path: str | Path,
    data: np.ndarray,
    *,
    crs: CRS | None,
    transform: Affine | None,
    nodata: float | None = None,
    band_unit: str | None = None,
) -> None:
    """Writes a single-band float32 GeoTIFF.

    `band_unit` (D3) is written as the GDAL band unit type only when given —
    for a physical elevation whose vertical unit is actually known; never
    invented.

    Only carries CRS/transform when both are provided — a non-georeferenced
    source (crs=None) always produces a non-georeferenced output. Never
    infers or fabricates georeferencing that wasn't genuinely present on the
    source. `nodata` (added for Phase 8's slope/aspect derivatives, which
    have real NoData edge/propagated pixels — Phase 3/4's depth/elevation
    arrays never needed one) is written into the GeoTIFF's own NoData tag
    only when explicitly given; omitted entirely otherwise, exactly
    preserving every pre-Phase-8 caller's behavior.
    """
    if data.ndim != 2:
        raise ValueError(f"Expected a 2D (height, width) array, got shape {data.shape}")

    height, width = data.shape
    profile: dict = {
        "driver": "GTiff",
        "height": height,
        "width": width,
        "count": 1,
        "dtype": "float32",
    }
    if crs is not None and transform is not None:
        profile["crs"] = crs
        profile["transform"] = transform
    if nodata is not None:
        profile["nodata"] = nodata

    path = Path(file_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(data.astype("float32"), 1)
        if band_unit is not None:
            dst.set_band_unit(1, band_unit)


def write_single_band_categorical(
    file_path: str | Path,
    data: np.ndarray,
    *,
    crs: CRS | None,
    transform: Affine | None,
    nodata: int = 0,
) -> None:
    """Writes a single-band uint32 GeoTIFF of CATEGORICAL integer IDs (e.g.
    Phase 6's region/segment labels) — never float32, since region IDs are
    not interpolatable quantities and must never be blended/resampled by a
    tool that doesn't know they're categorical.

    `nodata` (default 0) marks "no region assigned" — written into the
    GeoTIFF's own NoData tag so any GIS tool opening this file already knows
    0 isn't a real region. Only carries CRS/transform when both are
    provided — a non-georeferenced source (crs=None) always produces a
    non-georeferenced output, exactly like `write_single_band_float32`.
    """
    if data.ndim != 2:
        raise ValueError(f"Expected a 2D (height, width) array, got shape {data.shape}")

    height, width = data.shape
    profile: dict = {
        "driver": "GTiff",
        "height": height,
        "width": width,
        "count": 1,
        "dtype": "uint32",
        "nodata": nodata,
    }
    if crs is not None and transform is not None:
        profile["crs"] = crs
        profile["transform"] = transform

    path = Path(file_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(data.astype("uint32"), 1)
