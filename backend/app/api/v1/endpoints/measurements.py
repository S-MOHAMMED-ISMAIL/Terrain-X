"""Phase 7: real point elevation / distance / terrain-profile / coordinate
measurement endpoints.

Two distinct concerns, two routers:
  - `artifact_router` — live, UNSAVED computation under the exact same
    project -> job -> artifact ownership chain as the Phase 5 visualization
    endpoints (`app/api/v1/endpoints/visualization.py`). Nothing here is
    persisted; it's the real-time "click a point, see the real value"
    interaction.
  - `project_router` — persisted CRUD (`app/models/measurement.py`) under
    the project ownership chain. `POST` always RECOMPUTES the result
    server-side from the request's real input coordinates (never trusts a
    client-supplied result) before saving it.
"""

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    get_current_user,
    get_owned_analysis_job,
    get_owned_artifact,
    get_owned_measurement,
    get_owned_project,
)
from app.core.config import Settings, get_settings
from app.core.exceptions import NotFoundError, ValidationAppError
from app.core.storage import get_storage
from app.db.session import get_db
from app.models.analysis_artifact import AnalysisArtifact
from app.models.analysis_job import AnalysisJob
from app.models.measurement import Measurement
from app.models.user import User
from app.schemas.measurement import (
    CoordinateOut,
    DistanceOut,
    MeasurementCreate,
    MeasurementRead,
    PixelOut,
    PointElevationOut,
    PointSlopeOut,
    ProfileOut,
)
from app.services import measurement_service
from geospatial.exceptions import MeasurementInputError, RasterValidationError
from storage import StorageBackend

# Mounted at /projects/{project_id}/analysis/{job_id}/artifacts/{artifact_id}/measurements
artifact_router = APIRouter()
# Mounted at /projects/{project_id}/measurements
project_router = APIRouter()


def _require_file(storage: StorageBackend, storage_key: str) -> None:
    if not storage.exists(storage_key):
        raise NotFoundError("Stored artifact file is missing")


async def _get_owned_job_and_artifact(
    project_id: uuid.UUID,
    job_id: uuid.UUID,
    artifact_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> tuple[AnalysisJob, AnalysisArtifact]:
    job = await get_owned_analysis_job(project_id, job_id, current_user, db)
    artifact = await get_owned_artifact(project_id, job_id, artifact_id, current_user, db)
    return job, artifact


async def _get_owned_artifact_dep(
    project_id: uuid.UUID,
    job_id: uuid.UUID,
    artifact_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AnalysisArtifact:
    return await get_owned_artifact(project_id, job_id, artifact_id, current_user, db)


# --------------------------------------------------------------------------
# Live (unsaved) computation
# --------------------------------------------------------------------------


@artifact_router.get("/point", response_model=PointElevationOut)
async def get_point_elevation(
    row: int = Query(..., ge=0),
    col: int = Query(..., ge=0),
    job_and_artifact: tuple[AnalysisJob, AnalysisArtifact] = Depends(_get_owned_job_and_artifact),
    storage: StorageBackend = Depends(get_storage),
) -> PointElevationOut:
    job, artifact = job_and_artifact
    _require_file(storage, artifact.storage_key)
    try:
        return measurement_service.compute_point_elevation(artifact, job, row=row, col=col)
    except RasterValidationError as exc:
        raise ValidationAppError(str(exc)) from exc


@artifact_router.get("/slope", response_model=PointSlopeOut)
async def get_point_slope(
    x: float = Query(...),
    y: float = Query(...),
    crs: str | None = Query(default=None),
    artifact: AnalysisArtifact = Depends(_get_owned_artifact_dep),
    storage: StorageBackend = Depends(get_storage),
) -> PointSlopeOut:
    """P1-4 slope-at-point: the stored slope value at the pixel a real map
    coordinate falls in, resolved against the slope artifact's own CRS/
    transform (see measurement_service.compute_point_slope). An outside
    point is a 200 with `in_bounds=false`, like /point."""
    _require_file(storage, artifact.storage_key)
    try:
        return measurement_service.compute_point_slope(artifact, x=x, y=y, crs=crs)
    except (MeasurementInputError, RasterValidationError) as exc:
        raise ValidationAppError(str(exc)) from exc


@artifact_router.get("/distance", response_model=DistanceOut)
async def get_distance(
    row1: float = Query(...),
    col1: float = Query(...),
    row2: float = Query(...),
    col2: float = Query(...),
    job_and_artifact: tuple[AnalysisJob, AnalysisArtifact] = Depends(_get_owned_job_and_artifact),
    storage: StorageBackend = Depends(get_storage),
) -> DistanceOut:
    job, artifact = job_and_artifact
    _require_file(storage, artifact.storage_key)
    try:
        return measurement_service.compute_distance(
            artifact, job, row1=row1, col1=col1, row2=row2, col2=col2
        )
    except (MeasurementInputError, RasterValidationError) as exc:
        raise ValidationAppError(str(exc)) from exc


@artifact_router.get("/profile", response_model=ProfileOut)
async def get_profile(
    row1: float = Query(...),
    col1: float = Query(...),
    row2: float = Query(...),
    col2: float = Query(...),
    samples: int = Query(64, ge=2),
    job_and_artifact: tuple[AnalysisJob, AnalysisArtifact] = Depends(_get_owned_job_and_artifact),
    storage: StorageBackend = Depends(get_storage),
    settings: Settings = Depends(get_settings),
) -> ProfileOut:
    job, artifact = job_and_artifact
    _require_file(storage, artifact.storage_key)
    try:
        return measurement_service.compute_profile(
            artifact,
            job,
            row1=row1,
            col1=col1,
            row2=row2,
            col2=col2,
            samples=samples,
            settings=settings,
        )
    except (MeasurementInputError, RasterValidationError) as exc:
        raise ValidationAppError(str(exc)) from exc


@artifact_router.get("/coordinate", response_model=CoordinateOut)
async def get_coordinate(
    row: float = Query(...),
    col: float = Query(...),
    artifact: AnalysisArtifact = Depends(_get_owned_artifact_dep),
    storage: StorageBackend = Depends(get_storage),
) -> CoordinateOut:
    _require_file(storage, artifact.storage_key)
    try:
        return measurement_service.compute_coordinate(artifact, row=row, col=col)
    except RasterValidationError as exc:
        raise ValidationAppError(str(exc)) from exc


@artifact_router.get("/pixel", response_model=PixelOut)
async def get_pixel_for_coordinate(
    x: float = Query(...),
    y: float = Query(...),
    crs: str | None = Query(default=None),
    artifact: AnalysisArtifact = Depends(_get_owned_artifact_dep),
    storage: StorageBackend = Depends(get_storage),
) -> PixelOut:
    """The real, backend-authoritative inverse of `/coordinate` — converts a
    real map coordinate back to the exact full-resolution pixel it falls
    in. This is the fix for the Phase 5/6 3D-terrain coordinate-space bug:
    the 3D viewer's downsampled display grid is rendered in a real local
    metric coordinate frame, so a 3D click's raycast hit point is a genuine
    real-world coordinate that must be resolved against the ARTIFACT'S OWN
    full-resolution transform here, never interpreted directly as a
    full-resolution row/col (see docs/ARCHITECTURE.md §3.7).
    """
    _require_file(storage, artifact.storage_key)
    try:
        return measurement_service.resolve_pixel(artifact, x=x, y=y, crs=crs)
    except (MeasurementInputError, RasterValidationError) as exc:
        raise ValidationAppError(str(exc)) from exc


# --------------------------------------------------------------------------
# Persisted CRUD
# --------------------------------------------------------------------------


@project_router.post("", response_model=MeasurementRead, status_code=201)
async def create_measurement(
    project_id: uuid.UUID,
    payload: MeasurementCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    storage: StorageBackend = Depends(get_storage),
    settings: Settings = Depends(get_settings),
) -> Measurement:
    job = await get_owned_analysis_job(project_id, payload.analysis_job_id, current_user, db)
    artifact = await get_owned_artifact(
        project_id, payload.analysis_job_id, payload.artifact_id, current_user, db
    )
    _require_file(storage, artifact.storage_key)
    try:
        return await measurement_service.create_measurement(
            db,
            project_id=project_id,
            current_user=current_user,
            job=job,
            artifact=artifact,
            payload=payload,
            settings=settings,
        )
    except (MeasurementInputError, RasterValidationError) as exc:
        raise ValidationAppError(str(exc)) from exc


@project_router.get("", response_model=list[MeasurementRead])
async def list_measurements(
    project_id: uuid.UUID,
    analysis_job_id: uuid.UUID | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[Measurement]:
    await get_owned_project(project_id, current_user, db)
    stmt = select(Measurement).where(Measurement.project_id == project_id)
    if analysis_job_id is not None:
        stmt = stmt.where(Measurement.analysis_job_id == analysis_job_id)
    stmt = stmt.order_by(Measurement.created_at.desc()).limit(limit).offset(offset)
    result = await db.execute(stmt)
    return list(result.scalars().all())


@project_router.get("/{measurement_id}", response_model=MeasurementRead)
async def get_measurement(
    project_id: uuid.UUID,
    measurement_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Measurement:
    return await get_owned_measurement(project_id, measurement_id, current_user, db)


@project_router.delete("/{measurement_id}", status_code=204)
async def delete_measurement(
    project_id: uuid.UUID,
    measurement_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    measurement = await get_owned_measurement(project_id, measurement_id, current_user, db)
    await db.delete(measurement)
    await db.commit()
