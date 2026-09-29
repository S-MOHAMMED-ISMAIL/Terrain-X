"""Real raster metadata extraction via rasterio/GDAL.

Independent of the backend/FastAPI layer by design (see docs/ARCHITECTURE.md) —
the backend imports this module, never the reverse.

A file's extension is never trusted as evidence of georeferencing: whether a
raster is actually georeferenced is decided solely by whether GDAL reports a
CRS for it. A `.tif` with no embedded CRS is reported as non-georeferenced,
exactly like a plain JPEG would be.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import rasterio
from rasterio.errors import RasterioIOError

from geospatial.exceptions import RasterValidationError


@dataclass(frozen=True)
class BoundingBox:
    min_x: float
    min_y: float
    max_x: float
    max_y: float


@dataclass(frozen=True)
class RasterMetadata:
    driver: str
    width: int
    height: int
    bands: int
    is_georeferenced: bool
    crs: str | None
    bbox: BoundingBox | None


def extract_raster_metadata(file_path: str | Path) -> RasterMetadata:
    """Open `file_path` with GDAL and extract real structural metadata.

    Raises RasterValidationError if the file cannot be opened/understood as
    a raster by GDAL — the caller should treat this as an invalid dataset,
    not an internal error.
    """
    try:
        with rasterio.open(file_path) as dataset:
            crs = dataset.crs
            is_georeferenced = crs is not None

            bbox = None
            crs_str = None
            if is_georeferenced:
                epsg = crs.to_epsg()
                crs_str = f"EPSG:{epsg}" if epsg is not None else crs.to_string()
                bounds = dataset.bounds
                bbox = BoundingBox(
                    min_x=bounds.left,
                    min_y=bounds.bottom,
                    max_x=bounds.right,
                    max_y=bounds.top,
                )

            return RasterMetadata(
                driver=dataset.driver,
                width=dataset.width,
                height=dataset.height,
                bands=dataset.count,
                is_georeferenced=is_georeferenced,
                crs=crs_str,
                bbox=bbox,
            )
    except (RasterioIOError, ValueError) as exc:
        raise RasterValidationError(f"Could not read file as a raster: {exc}") from exc
