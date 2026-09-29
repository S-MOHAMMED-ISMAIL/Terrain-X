"""Phase 7: the scientific-honesty + orchestration layer between the pure
geospatial measurement primitives (geospatial/measurements.py) and the API.

This module is the ONLY place that decides what a raster's values actually
mean (real calibrated elevation vs. relative depth) and what units claim, if
any, is honest to make — geospatial/measurements.py has no opinion on any of
that; it only does real raster math. See docs/ARCHITECTURE.md §3.7 for the
full rationale, and app/services/analysis_execution.py / semantic_pipeline.py
for the identical separation-of-concerns pattern already used in Phases 3-6.
"""

from __future__ import annotations

import math
import uuid

from rasterio.crs import CRS
from rasterio.errors import CRSError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.exceptions import ValidationAppError
from app.models.analysis_artifact import AnalysisArtifact
from app.models.analysis_job import AnalysisJob
from app.models.measurement import Measurement, MeasurementType
from app.models.user import User
from app.schemas.measurement import (
    CoordinateOut,
    DistanceOut,
    MeasurementCreate,
    PixelOut,
    PointElevationOut,
    PointSlopeOut,
    ProfileOut,
    ProfileSampleOut,
)
from app.services.depth_pipeline import RELATIVE_DEPTH_VALUE_SEMANTICS
from app.services.visualization import artifact_raster_path
from geospatial.exceptions import MeasurementInputError
from geospatial.measurements import CoordinateResult
from geospatial.measurements import compute_distance as _compute_distance
from geospatial.measurements import coordinate_to_pixel as _coordinate_to_pixel
from geospatial.measurements import pixel_to_coordinate as _pixel_to_coordinate
from geospatial.measurements import sample_point_elevation as _sample_point_elevation
from geospatial.measurements import sample_profile as _sample_profile

# Artifact types that carry a real, continuous scalar field a point/profile
# measurement can sample. `semantic_segmentation` is deliberately excluded —
# it's a categorical region-ID raster (Phase 6), not a continuous field, and
# has no "elevation" or "depth" reading to report.
# P1-3: `dtm` (estimated bare-earth elevation) is a real calibrated-unit
# elevation field too. `ndsm` is deliberately NOT here — see
# NDSM_MEASUREMENT_REJECTION below.
ELEVATION_ARTIFACT_TYPES = {"metric_elevation", "dsm", "dtm"}

# nDSM values are estimated heights above an estimated ground surface, not
# elevations — reporting them through the elevation measurement contract
# (value_kind="elevation") would mislabel them. Height-above-ground
# measurements are future object-height work; pixel inspection already
# reports the real value with its own label.
NDSM_MEASUREMENT_REJECTION = (
    "Measurements are not supported on the nDSM layer: its values are estimated "
    "heights above an estimated ground surface, not elevations. Use pixel "
    "inspection to read an nDSM value, or measure on the DSM/DTM layer instead."
)

_ELEVATION_UNITS_DISCLAIMER = (
    "unspecified — same unit as the calibration reference used for this job "
    "(see the job's calibration_metadata); not independently verified as metres"
)

_ELEVATION_DEFAULT_DISCLAIMER = (
    "Calibrated elevation — see this job's calibration_metadata for the real "
    "validation metrics (MAE/RMSE/bias) against the reference actually used. "
    "Not independently validated ground truth, and not a bare-earth surface "
    "(no terrain/object-top separation has been performed)."
)

_DISTANCE_DISCLAIMER = (
    "Planimetric (horizontal) distance only — computed from the raster's own "
    "real CRS/pixel scale. Does not account for real terrain relief (slope) "
    "between the two points, and is never survey-grade or ground-truth accurate."
)


def _coordinate_out(c: CoordinateResult) -> CoordinateOut:
    return CoordinateOut(
        row=c.row,
        col=c.col,
        is_georeferenced=c.is_georeferenced,
        crs=c.crs,
        native_x=c.native_x,
        native_y=c.native_y,
        wgs84_lon=c.wgs84_lon,
        wgs84_lat=c.wgs84_lat,
    )


def elevation_units_label(metadata: dict) -> str:
    """D3: the unit claim for a calibrated elevation value — the vertical unit
    the calibration reference DECLARED (recorded on the artifact), else
    "unspecified". Never "m" merely because the value is an elevation."""
    unit = metadata.get("vertical_unit") or {}
    if unit.get("status") == "known" and unit.get("unit_name"):
        return (
            f"{unit['unit_name']} — declared by the calibration reference's metadata "
            f"({unit.get('source')}); not independently verified"
        )
    return _ELEVATION_UNITS_DISCLAIMER


def _value_kind_units_disclaimer(artifact: AnalysisArtifact) -> tuple[str, str, str]:
    """Decides whether this artifact's pixel values are real calibrated
    elevation or relative depth, and what unit claim (if any) is honest —
    see NON-NEGOTIABLE rules #1/#2/#8 (docs/ARCHITECTURE.md §3.7). Raises
    ValidationAppError for any artifact type with no scalar value at all.
    """
    metadata = artifact.artifact_metadata or {}
    if artifact.artifact_type == "relative_depth":
        return (
            "relative_depth",
            "relative units (unitless inverse depth — NOT elevation)",
            metadata.get("value_semantics", RELATIVE_DEPTH_VALUE_SEMANTICS),
        )
    if artifact.artifact_type in ELEVATION_ARTIFACT_TYPES:
        # D3 records the reference's declared vertical unit when one exists;
        # otherwise this remains explicitly unspecified, never guessed as
        # metres.
        disclaimer = (
            metadata.get("value_semantics")
            or metadata.get("limitations")
            or _ELEVATION_DEFAULT_DISCLAIMER
        )
        return "elevation", elevation_units_label(metadata), disclaimer
    if artifact.artifact_type == "slope":
        raise ValidationAppError(SLOPE_POINT_ELEVATION_REJECTION)
    raise ValidationAppError(
        f"Artifact type '{artifact.artifact_type}' has no scalar elevation/depth value to "
        f"measure — it is a categorical raster, not a continuous scientific field."
    )


# P1-4: slope values are degrees of terrain inclination, not elevation — a
# point-elevation reading of them would mislabel the value. The dedicated
# slope-at-point measurement reads them with the right value kind/units.
SLOPE_POINT_ELEVATION_REJECTION = (
    "Slope values are terrain inclination in degrees, not elevation. Use the "
    "'Slope at point' measurement to read the slope raster at a location."
)

# D3: slopes computed with a declared vertical unit (unit_provenance recorded).
_SLOPE_DISCLAIMER_SUFFIX_D3 = (
    " Read directly from the stored slope raster (nearest pixel, no interpolation, "
    "never recomputed). Computed with vertical and horizontal distances both in metres "
    "(see the slope artifact's unit_provenance); it inherits every limitation of the "
    "calibrated elevation it was derived from."
)
# Slopes computed before D3 recorded no unit provenance.
_SLOPE_DISCLAIMER_SUFFIX = (
    " Read directly from the stored slope raster (nearest pixel, no interpolation, "
    "never recomputed). It inherits every limitation of the calibrated elevation it "
    "was derived from, and assumes the elevation unit equals the horizontal unit."
)


def _reject_ndsm(artifact: AnalysisArtifact) -> None:
    if artifact.artifact_type == "ndsm":
        raise ValidationAppError(NDSM_MEASUREMENT_REJECTION)


def compute_point_elevation(
    artifact: AnalysisArtifact, job: AnalysisJob, *, row: int, col: int
) -> PointElevationOut:
    _reject_ndsm(artifact)
    value_kind, units, disclaimer = _value_kind_units_disclaimer(artifact)
    path = artifact_raster_path(artifact)
    result = _sample_point_elevation(path, row=row, col=col)
    coordinate = _pixel_to_coordinate(path, row=float(row), col=float(col))
    return PointElevationOut(
        row=result.row,
        col=result.col,
        value=result.value,
        in_bounds=result.in_bounds,
        value_kind=value_kind,
        units=units,
        calibration_state=job.calibration_status.value,
        artifact_type=artifact.artifact_type,
        disclaimer=disclaimer,
        coordinate=_coordinate_out(coordinate),
    )


def compute_distance(
    artifact: AnalysisArtifact,
    job: AnalysisJob,
    *,
    row1: float,
    col1: float,
    row2: float,
    col2: float,
) -> DistanceOut:
    # Distance is purely geometric (position only, never a pixel VALUE) — it
    # doesn't need a scalar-value artifact type. Every artifact belonging to
    # a job shares the source dataset's exact CRS/transform by construction
    # (Phase 3/4/6 all carry it through unchanged), so distance is valid for
    # any of relative_depth/metric_elevation/dsm/semantic_segmentation.
    _reject_ndsm(artifact)
    result = _compute_distance(
        artifact_raster_path(artifact), row1=row1, col1=col1, row2=row2, col2=col2
    )
    return DistanceOut(
        point1=_coordinate_out(result.point1),
        point2=_coordinate_out(result.point2),
        pixel_distance=result.pixel_distance,
        distance=result.distance,
        units=result.units,
        is_georeferenced=result.is_georeferenced,
        crs=result.crs,
        reprojected=result.reprojected,
        local_crs=result.local_crs,
        calibration_state=job.calibration_status.value,
        artifact_type=artifact.artifact_type,
        disclaimer=_DISTANCE_DISCLAIMER,
    )


def compute_profile(
    artifact: AnalysisArtifact,
    job: AnalysisJob,
    *,
    row1: float,
    col1: float,
    row2: float,
    col2: float,
    samples: int,
    settings: Settings,
) -> ProfileOut:
    _reject_ndsm(artifact)
    if samples > settings.MAX_PROFILE_SAMPLES:
        raise ValidationAppError(f"samples must be at most {settings.MAX_PROFILE_SAMPLES}.")
    value_kind, value_units, disclaimer = _value_kind_units_disclaimer(artifact)
    result = _sample_profile(
        artifact_raster_path(artifact), row1=row1, col1=col1, row2=row2, col2=col2, samples=samples
    )
    return ProfileOut(
        sample_count=result.sample_count,
        total_distance=result.total_distance,
        distance_units=result.units,
        is_georeferenced=result.is_georeferenced,
        crs=result.crs,
        reprojected=result.reprojected,
        local_crs=result.local_crs,
        value_kind=value_kind,
        value_units=value_units,
        calibration_state=job.calibration_status.value,
        artifact_type=artifact.artifact_type,
        disclaimer=disclaimer,
        samples=[
            ProfileSampleOut(
                index=s.index,
                row=s.row,
                col=s.col,
                distance_along=s.distance_along,
                value=s.value,
                coordinate=_coordinate_out(s.coordinate),
            )
            for s in result.samples
        ],
    )


def compute_point_slope(
    artifact: AnalysisArtifact, *, x: float, y: float, crs: str | None
) -> PointSlopeOut:
    """P1-4 slope-at-point. Resolves the map coordinate (x, y, crs) against
    the SLOPE artifact's own CRS/transform (`coordinate_to_pixel`), reads the
    stored value there unchanged (`sample_point_elevation` — its NoData and
    out-of-bounds semantics are kept exactly), and locates that pixel's
    centre (`pixel_to_coordinate`). A slope raster from a geographic source
    lives on a reprojected UTM grid, which is why the pixel is resolved here
    rather than taken from a source-grid row/col."""
    if artifact.artifact_type != "slope":
        raise ValidationAppError(
            "Slope at point requires a slope artifact (produced by disaster screening); "
            f"this artifact is '{artifact.artifact_type}'."
        )
    if not (math.isfinite(x) and math.isfinite(y)):
        raise MeasurementInputError("Map coordinates x and y must be finite numbers.")
    if crs is not None:
        # rasterio raises ValueError (not only CRSError) for some malformed
        # strings, e.g. "EPSG:abc"; either way it is a real input error.
        try:
            CRS.from_user_input(crs)
        except (CRSError, ValueError) as exc:
            raise MeasurementInputError(f"Unrecognized CRS '{crs}': {exc}") from exc

    path = artifact_raster_path(artifact)
    pixel = _coordinate_to_pixel(path, x=x, y=y, crs=crs)
    sample = _sample_point_elevation(path, row=pixel.row, col=pixel.col)
    coordinate = (
        _coordinate_out(_pixel_to_coordinate(path, row=float(pixel.row), col=float(pixel.col)))
        if pixel.in_bounds
        else None
    )
    metadata = artifact.artifact_metadata or {}
    return PointSlopeOut(
        row=pixel.row,
        col=pixel.col,
        in_bounds=pixel.in_bounds,
        value=sample.value,
        value_kind="slope",
        units=metadata.get("units") or "unknown (not recorded on this slope artifact)",
        query_x=x,
        query_y=y,
        query_crs=crs,
        coordinate=coordinate,
        artifact_type=artifact.artifact_type,
        source_artifact_id=metadata.get("source_artifact_id"),
        source_artifact_type=metadata.get("source_artifact_type"),
        reprojected_for_analysis=metadata.get("reprojected_for_analysis"),
        method=metadata.get("method"),
        disclaimer=(metadata.get("value_semantics") or "Terrain slope.")
        + (
            _SLOPE_DISCLAIMER_SUFFIX_D3
            if metadata.get("unit_provenance")
            else _SLOPE_DISCLAIMER_SUFFIX
        ),
    )


def compute_coordinate(artifact: AnalysisArtifact, *, row: float, col: float) -> CoordinateOut:
    return _coordinate_out(_pixel_to_coordinate(artifact_raster_path(artifact), row=row, col=col))


def resolve_pixel(artifact: AnalysisArtifact, *, x: float, y: float, crs: str | None) -> PixelOut:
    """The real, backend-authoritative inverse of `compute_coordinate` — see
    `geospatial.measurements.coordinate_to_pixel` for why this exists (the
    Phase 5/6 3D-terrain coordinate-space bug fix, docs/ARCHITECTURE.md §3.7).
    """
    result = _coordinate_to_pixel(artifact_raster_path(artifact), x=x, y=y, crs=crs)
    return PixelOut(row=result.row, col=result.col, in_bounds=result.in_bounds)


def _build_input_and_result(
    payload: MeasurementCreate, artifact: AnalysisArtifact, job: AnalysisJob, settings: Settings
) -> tuple[dict, dict]:
    """The single place a saved measurement is computed — always a REAL
    server-side recalculation from `payload`'s real input coordinates, never
    a client-supplied result trusted as-is (a client could otherwise persist
    a fabricated number)."""
    if payload.measurement_type == MeasurementType.POINT_ELEVATION:
        result = compute_point_elevation(artifact, job, row=payload.row, col=payload.col)
        input_data = {"row": payload.row, "col": payload.col}
    elif payload.measurement_type == MeasurementType.DISTANCE:
        result = compute_distance(
            artifact,
            job,
            row1=payload.row1,
            col1=payload.col1,
            row2=payload.row2,
            col2=payload.col2,
        )
        input_data = {
            "row1": payload.row1,
            "col1": payload.col1,
            "row2": payload.row2,
            "col2": payload.col2,
        }
    elif payload.measurement_type == MeasurementType.PROFILE:
        result = compute_profile(
            artifact,
            job,
            row1=payload.row1,
            col1=payload.col1,
            row2=payload.row2,
            col2=payload.col2,
            samples=payload.samples,
            settings=settings,
        )
        input_data = {
            "row1": payload.row1,
            "col1": payload.col1,
            "row2": payload.row2,
            "col2": payload.col2,
            "samples": payload.samples,
        }
    elif payload.measurement_type == MeasurementType.COORDINATE:
        result = compute_coordinate(artifact, row=payload.row, col=payload.col)
        input_data = {"row": payload.row, "col": payload.col}
    elif payload.measurement_type == MeasurementType.POINT_SLOPE:
        result = compute_point_slope(artifact, x=payload.x, y=payload.y, crs=payload.crs)
        input_data = {"x": payload.x, "y": payload.y, "crs": payload.crs}
    else:  # pragma: no cover - the discriminated union admits no other type
        raise ValidationAppError(f"Unsupported measurement type: {payload.measurement_type}")

    return input_data, result.model_dump(mode="json")


async def create_measurement(
    db: AsyncSession,
    *,
    project_id: uuid.UUID,
    current_user: User,
    job: AnalysisJob,
    artifact: AnalysisArtifact,
    payload: MeasurementCreate,
    settings: Settings,
) -> Measurement:
    input_data, result_data = _build_input_and_result(payload, artifact, job, settings)
    measurement = Measurement(
        project_id=project_id,
        analysis_job_id=job.id,
        artifact_id=artifact.id,
        user_id=current_user.id,
        measurement_type=payload.measurement_type,
        input_data=input_data,
        result_data=result_data,
    )
    db.add(measurement)
    await db.commit()
    await db.refresh(measurement)
    return measurement
