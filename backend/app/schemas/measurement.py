"""Phase 7 measurement schemas.

Every numeric field here either comes straight from a real backend
calculation over an actual stored raster (`geospatial/measurements.py`) or
is `None` when genuinely unavailable (out of bounds, not georeferenced,
real NoData) — never a fabricated placeholder. See
`app/services/measurement_service.py` for how `value_kind`/`units`/
`calibration_state`/`disclaimer` are derived per artifact type.
"""

import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.measurement import MeasurementType


class CoordinateOut(BaseModel):
    row: float
    col: float
    is_georeferenced: bool
    crs: str | None
    native_x: float | None
    native_y: float | None
    wgs84_lon: float | None
    wgs84_lat: float | None


class PixelOut(BaseModel):
    """The real, backend-authoritative pixel a map coordinate falls in —
    see `geospatial.measurements.coordinate_to_pixel` and the Phase 7 fix
    for the Phase 5/6 3D-terrain coordinate-space bug (docs/ARCHITECTURE.md
    §3.7)."""

    row: int
    col: int
    in_bounds: bool


class PointElevationOut(BaseModel):
    row: int
    col: int
    value: float | None
    in_bounds: bool
    # "elevation" for metric_elevation/dsm, "relative_depth" for
    # relative_depth — never presented as interchangeable (see NON-NEGOTIABLE
    # rule #1 in docs/ARCHITECTURE.md §3.7).
    value_kind: Literal["elevation", "relative_depth"]
    units: str
    calibration_state: str
    artifact_type: str
    disclaimer: str
    coordinate: CoordinateOut


class PointSlopeOut(BaseModel):
    """P1-4 slope-at-point: the value stored in an existing `slope` artifact
    (Horn 1981, computed by a disaster-screening job) at the pixel that a map
    coordinate falls in, resolved against the SLOPE raster's own CRS/
    transform. Never recomputed, never interpolated, never elevation.
    `calibration_state` is deliberately absent — the slope artifact belongs
    to a disaster-screening job whose own calibration status is always
    "uncalibrated"; the source-elevation provenance fields replace it."""

    row: int
    col: int
    in_bounds: bool
    value: float | None
    value_kind: Literal["slope"]
    units: str
    query_x: float
    query_y: float
    query_crs: str | None
    coordinate: CoordinateOut | None
    artifact_type: str
    source_artifact_id: str | None
    source_artifact_type: str | None
    reprojected_for_analysis: bool | None
    method: str | None
    disclaimer: str


class DistanceOut(BaseModel):
    point1: CoordinateOut
    point2: CoordinateOut
    pixel_distance: float
    distance: float | None
    units: str
    is_georeferenced: bool
    crs: str | None
    reprojected: bool
    local_crs: str | None
    calibration_state: str
    artifact_type: str
    disclaimer: str


class ProfileSampleOut(BaseModel):
    index: int
    row: float
    col: float
    distance_along: float
    value: float | None
    coordinate: CoordinateOut


class ProfileOut(BaseModel):
    sample_count: int
    total_distance: float
    # Horizontal (X-axis, distance-along-line) units — "pixels" or a real
    # CRS linear unit. Deliberately a SEPARATE field from `value_units`
    # (Y-axis, elevation/relative-depth) — the two axes have genuinely
    # different, independent unit semantics and must never be conflated
    # into one ambiguous "units" field.
    distance_units: str
    is_georeferenced: bool
    crs: str | None
    reprojected: bool
    local_crs: str | None
    value_kind: Literal["elevation", "relative_depth"]
    value_units: str
    calibration_state: str
    artifact_type: str
    disclaimer: str
    samples: list[ProfileSampleOut]


# --------------------------------------------------------------------------
# Persistence: create/list/read/delete a real saved measurement
# --------------------------------------------------------------------------


class MeasurementCreatePointElevation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    measurement_type: Literal[MeasurementType.POINT_ELEVATION]
    analysis_job_id: uuid.UUID
    artifact_id: uuid.UUID
    row: int
    col: int


class MeasurementCreateDistance(BaseModel):
    model_config = ConfigDict(extra="forbid")
    measurement_type: Literal[MeasurementType.DISTANCE]
    analysis_job_id: uuid.UUID
    artifact_id: uuid.UUID
    row1: float
    col1: float
    row2: float
    col2: float


class MeasurementCreateProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    measurement_type: Literal[MeasurementType.PROFILE]
    analysis_job_id: uuid.UUID
    artifact_id: uuid.UUID
    row1: float
    col1: float
    row2: float
    col2: float
    samples: int = 64


class MeasurementCreateCoordinate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    measurement_type: Literal[MeasurementType.COORDINATE]
    analysis_job_id: uuid.UUID
    artifact_id: uuid.UUID
    row: float
    col: float


class MeasurementCreatePointSlope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    measurement_type: Literal[MeasurementType.POINT_SLOPE]
    analysis_job_id: uuid.UUID
    artifact_id: uuid.UUID
    x: float
    y: float
    crs: str | None = None


MeasurementCreate = Annotated[
    MeasurementCreatePointElevation
    | MeasurementCreateDistance
    | MeasurementCreateProfile
    | MeasurementCreateCoordinate
    | MeasurementCreatePointSlope,
    Field(discriminator="measurement_type"),
]


class MeasurementRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    analysis_job_id: uuid.UUID
    artifact_id: uuid.UUID
    user_id: uuid.UUID
    measurement_type: MeasurementType
    input_data: dict
    result_data: dict
    created_at: datetime
