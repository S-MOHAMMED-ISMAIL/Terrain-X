"""Phase 8: real terrain derivatives (slope, aspect, terrain statistics) and
terrain-derived hazard screening (flood, landslide susceptibility) computed
directly from an actual, already-calibrated elevation raster
(`metric_elevation`/`dsm` — see geospatial/calibration.py, Phase 4).

Independent of the backend/FastAPI layer by design, like the rest of
geospatial/ — the backend imports this module, never the reverse.

SCOPE AND SCIENTIFIC HONESTY (read before using any output of this module):
  - This module operates ONLY on a real, already-calibrated elevation
    raster. It has no opinion on whether that calibration is itself
    accurate — see docs/ARCHITECTURE.md §3.4 for that discussion. A slope/
    flood/landslide result is only ever as trustworthy as the elevation it
    was computed from, and inherits every one of Phase 4's own limitations
    (scale ambiguity, no terrain/object-top separation, reference-dependent
    accuracy).
  - Slope/aspect require real, consistent horizontal (x/y) and vertical (z)
    units. D3: the vertical unit is resolved from the raster's own metadata
    (geospatial/vertical_units.py) and REQUIRED — an unknown unit fails with
    a diagnostic, never assumed to be metres; gradients are computed with both
    axes in metres. A geographic (lat/lon) CRS is reprojected into a real local UTM
    CRS first (`geospatial.terrain_grid.select_utm_crs` — the same
    technique already used by the Phase 5 3D terrain viewer and Phase 7
    measurements) so slope is never computed by mixing degrees with
    whatever the elevation's own z-unit is. A non-georeferenced elevation
    raster is rejected outright — real-world slope/area cannot be computed
    without a real horizontal scale. In practice this project's own Phase 4
    invariant already guarantees every `metric_elevation`/`dsm` artifact is
    georeferenced (calibration itself refuses a non-georeferenced source,
    `geospatial/calibration.py`), so this is a defensive check, not a
    reachable path today.
  - Flood screening is a real ELEVATION-THRESHOLD SCREENING SCENARIO, not a
    hydrological/hydraulic simulation: it does not model rainfall,
    drainage, rivers, flow routing, infiltration, tides, storm surge,
    hydraulic connectivity, or temporal flood dynamics. A "potentially
    inundated" pixel is never verified to be hydraulically connected to any
    real water source — this is elevation-threshold screening, not a flood
    prediction.
  - Landslide screening is a real, transparent, terrain-derived
    SUSCEPTIBILITY SCREENING INDEX based on documented slope thresholds —
    it is NOT a calibrated or validated probability of landslide
    occurrence, and must never be presented as a percentage chance.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import rasterio
from rasterio.crs import CRS
from rasterio.enums import Resampling
from rasterio.errors import RasterioIOError
from rasterio.transform import Affine
from rasterio.warp import calculate_default_transform, reproject

from geospatial.exceptions import DisasterAnalysisError, RasterValidationError
from geospatial.terrain_grid import select_utm_crs
from geospatial.vertical_units import (
    VerticalUnitError,
    VerticalUnitResolution,
    horizontal_to_metre,
    read_raster_vertical_unit,
    require_known,
)

# Real, fixed, documented sentinels — never fabricated per-call.
ELEVATION_ANALYSIS_NODATA = -9999.0  # slope/aspect working-array NoData
# A real, valid elevation pixel whose slope is exactly 0 (perfectly flat)
# has no mathematically defined downslope direction — distinct from NoData,
# matching the same real convention ArcGIS's own Aspect tool documents.
ASPECT_FLAT_SENTINEL = -1.0

# Hillshade sun position defaults — the same conventional defaults GDAL's
# `gdaldem hillshade` and ArcGIS's Hillshade tool both use (NW light, 45°
# above the horizon): a real, standard convention, not an arbitrary choice.
# Both are real, explicit keyword parameters on `compute_hillshade` (never
# hardcoded inside it) so a future config layer can override them.
DEFAULT_HILLSHADE_AZIMUTH_DEG = 315.0
DEFAULT_HILLSHADE_ALTITUDE_DEG = 45.0

FLOOD_NODATA = 0
FLOOD_NOT_INUNDATED = 1
FLOOD_POTENTIALLY_INUNDATED = 2
FLOOD_CLASS_LABELS = {
    FLOOD_NOT_INUNDATED: "Not potentially inundated",
    FLOOD_POTENTIALLY_INUNDATED: "Potentially inundated",
}

LANDSLIDE_NODATA = 0
LANDSLIDE_LOW = 1
LANDSLIDE_MODERATE = 2
LANDSLIDE_HIGH = 3
LANDSLIDE_VERY_HIGH = 4
LANDSLIDE_CLASS_LABELS = {
    LANDSLIDE_LOW: "Low",
    LANDSLIDE_MODERATE: "Moderate",
    LANDSLIDE_HIGH: "High",
    LANDSLIDE_VERY_HIGH: "Very High",
}


@dataclass(frozen=True)
class LandslideThresholds:
    """Real, documented, configurable slope-degree thresholds for the
    landslide susceptibility screening index — a real, if intentionally
    simple, methodology choice: steeper terrain is more susceptible to mass
    movement, all else equal. Not derived from any site-specific
    geotechnical data; see the module docstring's scope statement."""

    low_max_deg: float = 10.0
    moderate_max_deg: float = 20.0
    high_max_deg: float = 30.0
    # slope >= high_max_deg -> Very High

    def as_dict(self) -> dict:
        return {
            "low_max_deg": self.low_max_deg,
            "moderate_max_deg": self.moderate_max_deg,
            "high_max_deg": self.high_max_deg,
        }


DEFAULT_LANDSLIDE_THRESHOLDS = LandslideThresholds()


def crs_to_string(crs: CRS) -> str:
    epsg = crs.to_epsg()
    return f"EPSG:{epsg}" if epsg is not None else crs.to_string()


def _area_unit(crs: CRS) -> str:
    unit = crs.linear_units
    if not unit or unit == "unknown":
        return "square units (CRS linear unit not verified)"
    return f"square {unit}"


@dataclass(frozen=True)
class PreparedElevation:
    """A real elevation array ready for derivative computation — reprojected
    to a real local UTM CRS first if the source was geographic, otherwise
    used directly. Every slope/aspect/flood/landslide computation for one
    job operates on THIS array/transform, so results stay internally
    consistent (one real CRS/pixel size throughout)."""

    elevation: np.ndarray  # float32 (height, width); ELEVATION_ANALYSIS_NODATA where invalid
    transform: Affine
    crs: CRS
    pixel_width: float  # real horizontal pixel size, in `crs`'s own real linear unit
    pixel_height: float
    reprojected: bool
    source_crs: str  # the real, original artifact CRS string (provenance)
    width: int
    height: int
    # D3: `elevation` stays in the source's own vertical unit (statistics and
    # flood thresholds are read in it); the Horn gradients convert both axes
    # to metres with these exact factors. 1.0 for metres (bit-identical).
    vertical_unit: VerticalUnitResolution | None = None
    vertical_to_metre: float = 1.0
    horizontal_to_metre: float = 1.0


@dataclass(frozen=True)
class TerrainStatistics:
    min_elevation: float
    max_elevation: float
    mean_elevation: float
    median_elevation: float
    elevation_range: float
    min_slope_deg: float | None
    max_slope_deg: float | None
    mean_slope_deg: float | None
    valid_pixel_count: int
    total_pixel_count: int


@dataclass(frozen=True)
class FloodScreeningResult:
    classification: (
        np.ndarray
    )  # uint32: FLOOD_NODATA / FLOOD_NOT_INUNDATED / FLOOD_POTENTIALLY_INUNDATED
    water_level: float
    min_elevation: float
    max_elevation: float
    potentially_inundated_pixel_count: int
    valid_pixel_count: int
    pixel_area: float
    area_unit: str
    potentially_inundated_area: float
    valid_area: float
    potentially_inundated_percentage: float


@dataclass(frozen=True)
class LandslideScreeningResult:
    classification: np.ndarray  # uint32: LANDSLIDE_NODATA / LOW / MODERATE / HIGH / VERY_HIGH
    thresholds: LandslideThresholds
    class_pixel_counts: dict = field(default_factory=dict)
    class_areas: dict = field(default_factory=dict)
    class_percentages: dict = field(default_factory=dict)
    max_slope_deg: float = 0.0
    mean_slope_deg: float = 0.0
    pixel_area: float = 0.0
    area_unit: str = ""
    valid_pixel_count: int = 0


def _open_raster(file_path: str | Path):
    try:
        return rasterio.open(file_path)
    except (RasterioIOError, ValueError) as exc:
        raise RasterValidationError(f"Could not read file as a raster: {exc}") from exc


def prepare_elevation(file_path: str | Path) -> PreparedElevation:
    """Reads a real elevation raster and prepares it for derivative
    computation. Raises DisasterAnalysisError for a non-georeferenced input,
    one with no valid elevation pixels at all (see module docstring), or one
    whose vertical unit cannot be established from its own metadata (D3 —
    never assumed to be metres; see geospatial/vertical_units.py)."""
    vertical = read_raster_vertical_unit(file_path)
    try:
        vertical_unit = require_known(vertical, "Slope, aspect, hillshade and landslide screening")
    except VerticalUnitError as exc:
        raise DisasterAnalysisError(str(exc)) from exc
    with _open_raster(file_path) as dataset:
        crs = dataset.crs
        transform = dataset.transform
        if crs is None:
            raise DisasterAnalysisError(
                "This elevation raster is not georeferenced — real slope, aspect, "
                "and area-based hazard screening require a real horizontal scale, "
                "which cannot be fabricated."
            )

        band = dataset.read(1).astype("float32")
        nodata = dataset.nodata
        valid = np.isfinite(band)
        if nodata is not None:
            valid &= band != nodata
        working = np.where(valid, band, ELEVATION_ANALYSIS_NODATA).astype("float32")
        if not bool(valid.any()):
            raise DisasterAnalysisError("No valid elevation pixels were available for analysis.")

        if crs.is_geographic:
            bounds = dataset.bounds
            local_crs = select_utm_crs(
                (bounds.left + bounds.right) / 2, (bounds.bottom + bounds.top) / 2
            )
            dst_transform, dst_width, dst_height = calculate_default_transform(
                crs, local_crs, dataset.width, dataset.height, *bounds
            )
            reprojected = np.full(
                (dst_height, dst_width), ELEVATION_ANALYSIS_NODATA, dtype="float32"
            )
            reproject(
                source=working,
                destination=reprojected,
                src_transform=transform,
                src_crs=crs,
                src_nodata=ELEVATION_ANALYSIS_NODATA,
                dst_transform=dst_transform,
                dst_crs=local_crs,
                dst_nodata=ELEVATION_ANALYSIS_NODATA,
                resampling=Resampling.bilinear,
            )
            if not bool((reprojected != ELEVATION_ANALYSIS_NODATA).any()):
                raise DisasterAnalysisError(
                    "No valid elevation pixels remained after reprojecting to a real "
                    "local metric CRS."
                )
            return PreparedElevation(
                elevation=reprojected,
                transform=dst_transform,
                crs=local_crs,
                pixel_width=abs(dst_transform.a),
                pixel_height=abs(dst_transform.e),
                reprojected=True,
                source_crs=crs_to_string(crs),
                width=dst_width,
                height=dst_height,
                vertical_unit=vertical,
                vertical_to_metre=vertical_unit.to_metre,
                horizontal_to_metre=horizontal_to_metre(local_crs),
            )

        return PreparedElevation(
            elevation=working,
            transform=transform,
            crs=crs,
            pixel_width=abs(transform.a),
            pixel_height=abs(transform.e),
            reprojected=False,
            source_crs=crs_to_string(crs),
            width=dataset.width,
            height=dataset.height,
            vertical_unit=vertical,
            vertical_to_metre=vertical_unit.to_metre,
            horizontal_to_metre=_horizontal_to_metre(crs),
        )


def _horizontal_to_metre(crs: CRS) -> float:
    try:
        return horizontal_to_metre(crs)
    except VerticalUnitError as exc:
        raise DisasterAnalysisError(str(exc)) from exc


def _horn_gradients(prepared: PreparedElevation) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Real Horn (1981) 3x3 weighted finite-difference surface gradients
    (dz/dx, dz/dy) plus the per-pixel neighborhood validity mask — the
    shared core both `compute_slope_aspect` and `compute_hillshade` build
    on, so the two derivatives can never silently disagree about the same
    elevation neighborhood. Reflect-padded at the raster edge (real, if
    edge-mirrored, neighbors, never zeroed).
    """
    elevation = prepared.elevation
    valid = elevation != ELEVATION_ANALYSIS_NODATA
    padded = np.pad(elevation, 1, mode="reflect")
    padded_valid = np.pad(valid, 1, mode="reflect")

    # 3x3 neighborhood, row-major: a b c / d e f (center) / g h i
    a, b, c = padded[:-2, :-2], padded[:-2, 1:-1], padded[:-2, 2:]
    d, f = padded[1:-1, :-2], padded[1:-1, 2:]
    g, h, i = padded[2:, :-2], padded[2:, 1:-1], padded[2:, 2:]

    # D3: both axes in metres — vertical differences times the exact vertical
    # factor, cell sizes times the CRS linear-unit factor (both 1.0 for
    # metres, which leaves every result bit-identical).
    vz = prepared.vertical_to_metre
    cellsize_x = prepared.pixel_width * prepared.horizontal_to_metre
    cellsize_y = prepared.pixel_height * prepared.horizontal_to_metre
    dz_dx = (((c + 2 * f + i) - (a + 2 * d + g)) * vz) / (8.0 * cellsize_x)
    dz_dy = (((g + 2 * h + i) - (a + 2 * b + c)) * vz) / (8.0 * cellsize_y)

    va, vb, vc = padded_valid[:-2, :-2], padded_valid[:-2, 1:-1], padded_valid[:-2, 2:]
    vd, vf = padded_valid[1:-1, :-2], padded_valid[1:-1, 2:]
    vg, vh, vi = padded_valid[2:, :-2], padded_valid[2:, 1:-1], padded_valid[2:, 2:]
    neighborhood_valid = va & vb & vc & vd & vf & vg & vh & vi & valid

    return dz_dx, dz_dy, neighborhood_valid


def compute_slope_aspect(prepared: PreparedElevation) -> tuple[np.ndarray, np.ndarray]:
    """Real Horn (1981) 3x3 weighted finite-difference slope/aspect — the
    same method ArcGIS/QGIS use by default for their own Slope/Aspect
    tools. Uses the prepared elevation's real `pixel_width`/`pixel_height`
    throughout — never assumes 1 unit = 1 metre.

    Returns (slope_degrees, aspect_degrees), float32, same shape as the
    input. NoData: any output pixel whose real 3x3 neighborhood (reflect-
    padded at the raster edge — real, if edge-mirrored, neighbors, never
    zeroed) includes an invalid (ELEVATION_ANALYSIS_NODATA) elevation pixel
    is itself NoData in both outputs. Aspect additionally uses
    ASPECT_FLAT_SENTINEL for a real, valid, perfectly flat pixel (slope==0,
    downslope direction mathematically undefined) — distinct from NoData.
    """
    dz_dx, dz_dy, neighborhood_valid = _horn_gradients(prepared)

    slope_deg = np.degrees(np.arctan(np.hypot(dz_dx, dz_dy))).astype("float32")

    # Real ESRI/Horn aspect convention: math-convention atan2 (0=East,
    # counter-clockwise) converted to compass bearing (0=North, clockwise).
    aspect_math_deg = np.degrees(np.arctan2(dz_dy, -dz_dx))
    aspect_deg = 90.0 - aspect_math_deg
    aspect_deg = np.where(aspect_deg < 0.0, aspect_deg + 360.0, aspect_deg)
    aspect_deg = np.where(aspect_deg >= 360.0, aspect_deg - 360.0, aspect_deg)

    flat = (dz_dx == 0.0) & (dz_dy == 0.0)
    aspect_deg = np.where(flat, ASPECT_FLAT_SENTINEL, aspect_deg).astype("float32")

    slope_deg = np.where(neighborhood_valid, slope_deg, ELEVATION_ANALYSIS_NODATA).astype("float32")
    aspect_deg = np.where(neighborhood_valid, aspect_deg, ELEVATION_ANALYSIS_NODATA).astype(
        "float32"
    )
    return slope_deg, aspect_deg


def compute_hillshade(
    prepared: PreparedElevation,
    *,
    azimuth_deg: float = DEFAULT_HILLSHADE_AZIMUTH_DEG,
    altitude_deg: float = DEFAULT_HILLSHADE_ALTITUDE_DEG,
) -> np.ndarray:
    """Real hillshade (terrain illumination) — the standard Lambertian
    cosine-illumination model, computed over the SAME Horn (1981) 3x3
    surface gradients `compute_slope_aspect` uses (via the shared
    `_horn_gradients` helper above), so hillshade can never silently
    disagree with this module's own slope/aspect about the terrain's real
    shape.

    `azimuth_deg` is the real sun bearing (0=North, clockwise — the SAME
    compass convention `compute_slope_aspect`'s aspect output already uses,
    so no conversion between the two is needed). `altitude_deg` is the real
    sun elevation above the horizon (0=on the horizon, 90=directly
    overhead). Defaults (315°/NW, 45°) are the same conventional defaults
    GDAL's `gdaldem hillshade` and ArcGIS's Hillshade tool both use — see
    DEFAULT_HILLSHADE_AZIMUTH_DEG/DEFAULT_HILLSHADE_ALTITUDE_DEG above; both
    are real, explicit keyword parameters so a future caller/config layer
    can override them.

    Returns float32, same shape as the input, real values in [0.0, 1.0]:
    the Lambertian illumination fraction (1.0 = surface directly facing the
    sun, 0.0 = perpendicular to or facing away from it). A negative dot
    product (self-shadowed slope) is clamped to 0.0 rather than left
    negative — the standard simplification for this per-pixel
    surface-orientation model, which has no ray-traced cast-shadow
    detection from neighboring terrain. This is illumination, NOT
    elevation — never a height value, never a valid 3D terrain-mesh height
    source (see app/services/visualization.py::TERRAIN_SOURCE_ARTIFACT_TYPES,
    which deliberately never includes "hillshade").

    NoData: identical rule to `compute_slope_aspect` — any pixel whose real
    3x3 neighborhood includes an invalid elevation pixel is itself
    ELEVATION_ANALYSIS_NODATA in the output; an invalid pixel is never
    given a fabricated illumination value.

    Raises DisasterAnalysisError if `altitude_deg` is not a real, finite
    value in [0, 90] or `azimuth_deg` is not a real, finite number (no
    physical illumination exists to compute otherwise).
    """
    if not math.isfinite(altitude_deg) or not (0.0 <= altitude_deg <= 90.0):
        raise DisasterAnalysisError(
            f"Hillshade sun altitude must be a real, finite value between 0 and 90 degrees "
            f"above the horizon; got {altitude_deg}."
        )
    if not math.isfinite(azimuth_deg):
        raise DisasterAnalysisError(
            f"Hillshade sun azimuth must be a real, finite number of degrees; got {azimuth_deg}."
        )

    dz_dx, dz_dy, neighborhood_valid = _horn_gradients(prepared)

    zenith_rad = math.radians(90.0 - altitude_deg)
    azimuth_rad = math.radians(azimuth_deg % 360.0)

    slope_rad = np.arctan(np.hypot(dz_dx, dz_dy))
    # Real aspect in radians, computed directly from the gradients using
    # the SAME atan2/compass-bearing convention compute_slope_aspect uses.
    # Deliberately does NOT substitute ASPECT_FLAT_SENTINEL for a flat
    # pixel (dz_dx==dz_dy==0): slope_rad==0 there makes sin(slope_rad)==0,
    # which mathematically zeroes out this term's entire contribution to
    # illumination regardless of the (otherwise undefined) aspect angle —
    # so no special-casing is needed for correctness, only for what
    # compute_slope_aspect chooses to REPORT as aspect's own dedicated
    # output.
    aspect_math_rad = np.arctan2(dz_dy, -dz_dx)
    aspect_rad = (math.pi / 2.0) - aspect_math_rad

    illumination = np.cos(zenith_rad) * np.cos(slope_rad) + np.sin(zenith_rad) * np.sin(
        slope_rad
    ) * np.cos(azimuth_rad - aspect_rad)
    illumination = np.clip(illumination, 0.0, 1.0).astype("float32")

    return np.where(neighborhood_valid, illumination, ELEVATION_ANALYSIS_NODATA).astype("float32")


def compute_terrain_statistics(
    prepared: PreparedElevation, slope_deg: np.ndarray
) -> TerrainStatistics:
    elevation = prepared.elevation
    valid = elevation != ELEVATION_ANALYSIS_NODATA
    if not bool(valid.any()):
        raise DisasterAnalysisError("No valid elevation pixels were available for analysis.")
    elev_values = elevation[valid]

    slope_valid = slope_deg != ELEVATION_ANALYSIS_NODATA
    slope_values = slope_deg[slope_valid] if bool(slope_valid.any()) else None

    return TerrainStatistics(
        min_elevation=float(elev_values.min()),
        max_elevation=float(elev_values.max()),
        mean_elevation=float(elev_values.mean()),
        median_elevation=float(np.median(elev_values)),
        elevation_range=float(elev_values.max() - elev_values.min()),
        min_slope_deg=float(slope_values.min()) if slope_values is not None else None,
        max_slope_deg=float(slope_values.max()) if slope_values is not None else None,
        mean_slope_deg=float(slope_values.mean()) if slope_values is not None else None,
        valid_pixel_count=int(valid.sum()),
        total_pixel_count=int(elevation.size),
    )


def run_flood_screening(prepared: PreparedElevation, *, water_level: float) -> FloodScreeningResult:
    if not math.isfinite(water_level):
        raise DisasterAnalysisError("Water level must be a real, finite number.")

    elevation = prepared.elevation
    valid = elevation != ELEVATION_ANALYSIS_NODATA
    if not bool(valid.any()):
        raise DisasterAnalysisError("No valid elevation pixels were available for flood screening.")

    classification = np.full(elevation.shape, FLOOD_NODATA, dtype="uint32")
    inundated = valid & (elevation <= water_level)
    not_inundated = valid & ~inundated
    classification[not_inundated] = FLOOD_NOT_INUNDATED
    classification[inundated] = FLOOD_POTENTIALLY_INUNDATED

    pixel_area = prepared.pixel_width * prepared.pixel_height
    valid_count = int(valid.sum())
    inundated_count = int(inundated.sum())

    return FloodScreeningResult(
        classification=classification,
        water_level=water_level,
        min_elevation=float(elevation[valid].min()),
        max_elevation=float(elevation[valid].max()),
        potentially_inundated_pixel_count=inundated_count,
        valid_pixel_count=valid_count,
        pixel_area=pixel_area,
        area_unit=_area_unit(prepared.crs),
        potentially_inundated_area=inundated_count * pixel_area,
        valid_area=valid_count * pixel_area,
        potentially_inundated_percentage=(
            (inundated_count / valid_count * 100.0) if valid_count else 0.0
        ),
    )


def run_landslide_screening(
    prepared: PreparedElevation,
    slope_deg: np.ndarray,
    *,
    thresholds: LandslideThresholds | None = None,
) -> LandslideScreeningResult:
    thresholds = thresholds or DEFAULT_LANDSLIDE_THRESHOLDS
    valid = slope_deg != ELEVATION_ANALYSIS_NODATA
    if not bool(valid.any()):
        raise DisasterAnalysisError("No valid slope pixels were available for landslide screening.")

    classification = np.full(slope_deg.shape, LANDSLIDE_NODATA, dtype="uint32")
    low = valid & (slope_deg < thresholds.low_max_deg)
    moderate = (
        valid & (slope_deg >= thresholds.low_max_deg) & (slope_deg < thresholds.moderate_max_deg)
    )
    high = (
        valid & (slope_deg >= thresholds.moderate_max_deg) & (slope_deg < thresholds.high_max_deg)
    )
    very_high = valid & (slope_deg >= thresholds.high_max_deg)
    classification[low] = LANDSLIDE_LOW
    classification[moderate] = LANDSLIDE_MODERATE
    classification[high] = LANDSLIDE_HIGH
    classification[very_high] = LANDSLIDE_VERY_HIGH

    pixel_area = prepared.pixel_width * prepared.pixel_height
    valid_count = int(valid.sum())
    counts = {
        "low": int(low.sum()),
        "moderate": int(moderate.sum()),
        "high": int(high.sum()),
        "very_high": int(very_high.sum()),
    }
    areas = {k: v * pixel_area for k, v in counts.items()}
    percentages = {k: (v / valid_count * 100.0 if valid_count else 0.0) for k, v in counts.items()}

    return LandslideScreeningResult(
        classification=classification,
        thresholds=thresholds,
        class_pixel_counts=counts,
        class_areas=areas,
        class_percentages=percentages,
        max_slope_deg=float(slope_deg[valid].max()),
        mean_slope_deg=float(slope_deg[valid].mean()),
        pixel_area=pixel_area,
        area_unit=_area_unit(prepared.crs),
        valid_pixel_count=valid_count,
    )
