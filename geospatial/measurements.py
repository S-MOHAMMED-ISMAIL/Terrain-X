"""Phase 7: real point elevation, distance, terrain-profile, and coordinate
measurement primitives over an actual stored raster.

Independent of the backend/FastAPI layer by design, like the rest of
geospatial/ — the backend imports this module, never the reverse. Nothing
here fabricates a value: an out-of-bounds request is a real, explicit
MeasurementInputError (never silently clamped or answered with a guessed
number), and a real NoData/NaN/Inf pixel is always reported as `None`
(never coerced to 0), exactly like the rest of this project's raster-reading
code (see geospatial/raster_preview.py::sample_pixel_value).

Horizontal (planimetric) distance uses the raster's own real CRS — a
projected CRS's linear unit (e.g. "metre" for UTM) is read directly from
`rasterio`/PROJ, never assumed; a geographic (lat/lon) CRS is reprojected
into a real local UTM CRS first via `geospatial.terrain_grid.select_utm_crs`
(the exact same technique the Phase 5 3D terrain viewer already uses) —
degrees are never treated as if they were Cartesian metres anywhere in this
module. Vertical (elevation) values are returned completely unlabeled as to
unit here — this module has no opinion on what "elevation" means; that
scientific-honesty judgment (relative depth vs. calibrated elevation, and
the fact that a calibrated elevation's real-world unit is whatever the
reference data used, never assumed to be metres) belongs to
`app/services/measurement_service.py`, which is the only layer that knows
which artifact type produced the raster.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import rasterio
from rasterio.crs import CRS
from rasterio.errors import CRSError, RasterioIOError
from rasterio.warp import transform as warp_transform
from rasterio.windows import Window

from geospatial.exceptions import MeasurementInputError, RasterValidationError
from geospatial.terrain_grid import select_utm_crs

WGS84 = CRS.from_epsg(4326)


@dataclass(frozen=True)
class PixelResult:
    row: int
    col: int
    in_bounds: bool


@dataclass(frozen=True)
class PointElevationResult:
    row: int
    col: int
    # None for either an out-of-bounds request or a real, in-bounds
    # NoData/NaN/Inf pixel — `in_bounds` disambiguates which.
    value: float | None
    in_bounds: bool


@dataclass(frozen=True)
class CoordinateResult:
    row: float
    col: float
    is_georeferenced: bool
    crs: str | None
    # Real map coordinates in the raster's own (native) CRS — lon/lat for a
    # geographic CRS, projected x/y for a projected one. None only when the
    # raster is not georeferenced at all.
    native_x: float | None
    native_y: float | None
    # Real WGS84 lat/lon via an actual PROJ reprojection — None only if the
    # raster's CRS genuinely can't be reprojected (a real, if unlikely, data
    # problem), never a guessed value.
    wgs84_lon: float | None
    wgs84_lat: float | None


@dataclass(frozen=True)
class DistanceResult:
    row1: int
    col1: int
    row2: int
    col2: int
    # Real Euclidean distance in pixel space — always computed, regardless
    # of georeferencing, since it's a genuine property of the two requested
    # pixel coordinates.
    pixel_distance: float
    # Real-world planimetric distance — None only when the raster is not
    # georeferenced at all (never a pixel-distance-times-guessed-scale
    # substitute).
    distance: float | None
    units: str  # "pixels", or a real CRS linear unit string (e.g. "metre")
    is_georeferenced: bool
    crs: str | None
    # True if the source CRS was geographic and this distance was computed
    # by reprojecting both points into a real local UTM CRS first.
    reprojected: bool
    local_crs: str | None
    point1: CoordinateResult
    point2: CoordinateResult


@dataclass(frozen=True)
class ProfileSample:
    index: int
    row: float
    col: float
    # Real cumulative distance from the start point, computed by summing
    # actual consecutive-sample distances in real-world units (or pixel
    # units if not georeferenced) — never assumed proportional to the
    # sample's fractional position along the line, since reprojecting a
    # geographic CRS to a local UTM CRS is not perfectly linear.
    distance_along: float
    value: float | None  # None for a real NoData/NaN/Inf pixel
    coordinate: CoordinateResult


@dataclass(frozen=True)
class ProfileResult:
    row1: int
    col1: int
    row2: int
    col2: int
    sample_count: int
    total_distance: float
    units: str
    is_georeferenced: bool
    crs: str | None
    reprojected: bool
    local_crs: str | None
    samples: list[ProfileSample]


def _open_raster(file_path: str | Path):
    try:
        return rasterio.open(file_path)
    except (RasterioIOError, ValueError) as exc:
        raise RasterValidationError(f"Could not read file as a raster: {exc}") from exc


def _crs_str(crs: CRS) -> str:
    epsg = crs.to_epsg()
    return f"EPSG:{epsg}" if epsg is not None else crs.to_string()


def _validate_point(height: int, width: int, row: float, col: float, label: str) -> None:
    if not (0 <= row <= height - 1 and 0 <= col <= width - 1):
        raise MeasurementInputError(
            f"{label} (row={row}, col={col}) is outside the raster bounds "
            f"({height} rows x {width} cols)."
        )


def _coordinate_for(dataset: rasterio.DatasetReader, row: float, col: float) -> CoordinateResult:
    crs = dataset.crs
    if crs is None:
        return CoordinateResult(
            row=row,
            col=col,
            is_georeferenced=False,
            crs=None,
            native_x=None,
            native_y=None,
            wgs84_lon=None,
            wgs84_lat=None,
        )
    # Pixel-center convention (matches geospatial/calibration.py's own
    # `transform * (cols + 0.5, rows + 0.5)` — the established convention in
    # this codebase for turning a pixel index into a real map coordinate).
    native_x, native_y = dataset.transform * (col + 0.5, row + 0.5)
    try:
        lons, lats = warp_transform(crs, WGS84, [native_x], [native_y])
        wgs84_lon, wgs84_lat = float(lons[0]), float(lats[0])
    except CRSError:
        wgs84_lon = wgs84_lat = None
    return CoordinateResult(
        row=row,
        col=col,
        is_georeferenced=True,
        crs=_crs_str(crs),
        native_x=float(native_x),
        native_y=float(native_y),
        wgs84_lon=wgs84_lon,
        wgs84_lat=wgs84_lat,
    )


def sample_point_elevation(file_path: str | Path, *, row: int, col: int) -> PointElevationResult:
    """The exact real value at one pixel of band 1 — `None` for an
    out-of-bounds request (`in_bounds=False`) or a real, in-bounds
    NoData/NaN/Inf pixel (`in_bounds=True`, `value=None`). Never a
    fabricated number standing in for "no data here"."""
    with _open_raster(file_path) as dataset:
        in_bounds = 0 <= row < dataset.height and 0 <= col < dataset.width
        if not in_bounds:
            return PointElevationResult(row=row, col=col, value=None, in_bounds=False)
        raw = dataset.read(1, window=Window(col, row, 1, 1))[0, 0]
        nodata = dataset.nodata
        value = float(raw)
        if not np.isfinite(value) or (nodata is not None and value == nodata):
            value = None
        return PointElevationResult(row=row, col=col, value=value, in_bounds=True)


def pixel_to_coordinate(file_path: str | Path, *, row: float, col: float) -> CoordinateResult:
    """The real map coordinate of one pixel, computed from the raster's own
    actual affine transform (never a client-side approximation) — see
    `docs/ARCHITECTURE.md` §3.7 for why this replaced the earlier
    linear-interpolation-within-bounds approach for measurement purposes.
    Out-of-bounds requests are still answered (a coordinate is a real,
    well-defined position for any real number, in or out of the raster's
    own footprint) — bounds validation is a measurement-specific concern
    handled by distance/profile, not a property of "where is this point."
    """
    with _open_raster(file_path) as dataset:
        return _coordinate_for(dataset, row, col)


def compute_distance(
    file_path: str | Path, *, row1: float, col1: float, row2: float, col2: float
) -> DistanceResult:
    """Real planimetric distance between two pixel coordinates.

    - Not georeferenced: only a real pixel-space distance is returned
      (`distance=None`, `units="pixels"`) — never a fabricated real-world
      unit.
    - Projected CRS: both points' real map coordinates are read directly
      from the raster's own transform; distance is their real Euclidean
      distance in that CRS's own real linear unit (e.g. "metre" for UTM).
    - Geographic CRS: both points are reprojected into a real local UTM CRS
      (`geospatial.terrain_grid.select_utm_crs` — real GDAL/PROJ, the same
      technique already used for the Phase 5 3D terrain viewer) before
      computing distance — longitude/latitude degrees are never treated as
      Cartesian metres.
    """
    with _open_raster(file_path) as dataset:
        height, width = dataset.height, dataset.width
        _validate_point(height, width, row1, col1, "Start point")
        _validate_point(height, width, row2, col2, "End point")

        pixel_distance = math.hypot(row2 - row1, col2 - col1)
        crs = dataset.crs
        point1 = _coordinate_for(dataset, row1, col1)
        point2 = _coordinate_for(dataset, row2, col2)

        if crs is None:
            return DistanceResult(
                row1=row1,
                col1=col1,
                row2=row2,
                col2=col2,
                pixel_distance=pixel_distance,
                distance=None,
                units="pixels",
                is_georeferenced=False,
                crs=None,
                reprojected=False,
                local_crs=None,
                point1=point1,
                point2=point2,
            )

        if crs.is_geographic:
            bounds = dataset.bounds
            local_crs = select_utm_crs(
                (bounds.left + bounds.right) / 2, (bounds.bottom + bounds.top) / 2
            )
            xs, ys = warp_transform(
                crs,
                local_crs,
                [point1.native_x, point2.native_x],
                [point1.native_y, point2.native_y],
            )
            distance = math.hypot(xs[1] - xs[0], ys[1] - ys[0])
            return DistanceResult(
                row1=row1,
                col1=col1,
                row2=row2,
                col2=col2,
                pixel_distance=pixel_distance,
                distance=distance,
                units=local_crs.linear_units or "metre",
                is_georeferenced=True,
                crs=_crs_str(crs),
                reprojected=True,
                local_crs=_crs_str(local_crs),
                point1=point1,
                point2=point2,
            )

        distance = math.hypot(point2.native_x - point1.native_x, point2.native_y - point1.native_y)
        return DistanceResult(
            row1=row1,
            col1=col1,
            row2=row2,
            col2=col2,
            pixel_distance=pixel_distance,
            distance=distance,
            units=crs.linear_units or "unknown",
            is_georeferenced=True,
            crs=_crs_str(crs),
            reprojected=False,
            local_crs=None,
            point1=point1,
            point2=point2,
        )


def sample_profile(
    file_path: str | Path, *, row1: float, col1: float, row2: float, col2: float, samples: int
) -> ProfileResult:
    """Real elevation/value samples along the straight pixel-space line
    between two points, read from the FULL-RESOLUTION raster via one real
    windowed read covering the line's bounding box (never the display-only,
    downsampled 3D terrain grid — see `geospatial/terrain_grid.py`, which
    remains rendering-only). Value lookup uses nearest-pixel indexing (a
    real, documented simplification, consistent with this project's
    existing nearest-pixel GCP correspondence in `geospatial/calibration.py`)
    on real, sub-pixel-precise interpolated positions along the line.

    `distance_along` for each sample is a real cumulative sum of
    consecutive real-world segment distances (reprojected to a local UTM
    CRS first for a geographic source) — not assumed proportional to the
    sample's fractional position, since that reprojection is not perfectly
    linear.
    """
    if samples < 2:
        raise MeasurementInputError("A profile needs at least 2 samples.")

    with _open_raster(file_path) as dataset:
        height, width = dataset.height, dataset.width
        _validate_point(height, width, row1, col1, "Start point")
        _validate_point(height, width, row2, col2, "End point")

        fractions = [i / (samples - 1) for i in range(samples)]
        rows = [row1 + (row2 - row1) * t for t in fractions]
        cols = [col1 + (col2 - col1) * t for t in fractions]

        row_min = max(0, math.floor(min(rows)))
        row_max = min(height - 1, math.ceil(max(rows)))
        col_min = max(0, math.floor(min(cols)))
        col_max = min(width - 1, math.ceil(max(cols)))
        window = Window(col_min, row_min, col_max - col_min + 1, row_max - row_min + 1)
        band = dataset.read(1, window=window)
        nodata = dataset.nodata

        crs = dataset.crs
        local_crs = None
        reprojected = False
        if crs is not None and crs.is_geographic:
            bounds = dataset.bounds
            local_crs = select_utm_crs(
                (bounds.left + bounds.right) / 2, (bounds.bottom + bounds.top) / 2
            )
            reprojected = True

        coordinates = [
            _coordinate_for(dataset, row, col) for row, col in zip(rows, cols, strict=False)
        ]

        if crs is None:
            real_points = list(zip(cols, rows, strict=False))  # pixel-space "coordinates"
        elif reprojected:
            xs_src = [c.native_x for c in coordinates]
            ys_src = [c.native_y for c in coordinates]
            xs_dst, ys_dst = warp_transform(crs, local_crs, xs_src, ys_src)
            real_points = list(zip(xs_dst, ys_dst, strict=False))
        else:
            real_points = [(c.native_x, c.native_y) for c in coordinates]

        distances_along = [0.0]
        for i in range(1, len(real_points)):
            x0, y0 = real_points[i - 1]
            x1, y1 = real_points[i]
            distances_along.append(distances_along[-1] + math.hypot(x1 - x0, y1 - y0))

        profile_samples: list[ProfileSample] = []
        for i, (row, col) in enumerate(zip(rows, cols, strict=False)):
            r_idx = round(row) - row_min
            c_idx = round(col) - col_min
            raw = band[r_idx, c_idx]
            value = float(raw)
            if not np.isfinite(value) or (nodata is not None and value == nodata):
                value = None
            profile_samples.append(
                ProfileSample(
                    index=i,
                    row=row,
                    col=col,
                    distance_along=distances_along[i],
                    value=value,
                    coordinate=coordinates[i],
                )
            )

        if crs is None:
            units = "pixels"
        elif reprojected:
            units = local_crs.linear_units or "metre"
        else:
            units = crs.linear_units or "unknown"

        return ProfileResult(
            row1=row1,
            col1=col1,
            row2=row2,
            col2=col2,
            sample_count=samples,
            total_distance=distances_along[-1],
            units=units,
            is_georeferenced=crs is not None,
            crs=_crs_str(crs) if crs is not None else None,
            reprojected=reprojected,
            local_crs=_crs_str(local_crs) if local_crs is not None else None,
            samples=profile_samples,
        )


def coordinate_to_pixel(
    file_path: str | Path, *, x: float, y: float, crs: str | None = None
) -> PixelResult:
    """The real inverse of `pixel_to_coordinate`/`_coordinate_for`: converts
    a real map coordinate back to the integer (row, col) pixel it falls in,
    using the raster's own actual inverse affine transform — never a
    client-side approximation.

    `crs` names the CRS `(x, y)` is expressed in; if it differs from the
    raster's own CRS, `(x, y)` is reprojected via real GDAL/PROJ first
    (`crs=None` means "already in the raster's own native CRS").

    This is the fix for the Phase 5/6 3D-terrain coordinate-space bug: the
    3D viewer's downsampled display grid (`geospatial/terrain_grid.py`,
    capped at `MAX_TERRAIN_DIMENSION`) is rendered in a real local metric
    coordinate frame (`origin_x/origin_y/cell_size_x/cell_size_y/local_crs`)
    — a 3D click's raycast hit point is therefore a genuine real-world
    coordinate in that frame, valid regardless of whether the grid was
    reprojected from a geographic source. Round-tripping that real
    coordinate back through THIS function (rather than rescaling the
    downsampled grid's own row/col index, which is not a uniform
    transformation of the source's row/col index once reprojection has
    occurred) is what makes the 3D-click-to-full-resolution-pixel mapping
    exact instead of approximate. See docs/ARCHITECTURE.md §3.7.

    Uses `floor(inv_transform * (x, y))` — the exact same pixel-center
    convention already established in `geospatial/calibration.py`'s own
    map-coordinate-to-pixel sampling.
    """
    with _open_raster(file_path) as dataset:
        raster_crs = dataset.crs
        if raster_crs is None:
            raise MeasurementInputError(
                "This raster is not georeferenced; a real map coordinate cannot be "
                "converted to a pixel. Use pixel coordinates directly instead."
            )
        if crs is not None:
            try:
                source_crs = CRS.from_user_input(crs)
            except CRSError as exc:
                raise MeasurementInputError(f"Unrecognized CRS '{crs}': {exc}") from exc
            if source_crs != raster_crs:
                (x,), (y,) = warp_transform(source_crs, raster_crs, [x], [y])

        inv_transform = ~dataset.transform
        col_f, row_f = inv_transform * (x, y)
        row, col = math.floor(row_f), math.floor(col_f)
        in_bounds = 0 <= row < dataset.height and 0 <= col < dataset.width
        return PixelResult(row=row, col=col, in_bounds=in_bounds)
