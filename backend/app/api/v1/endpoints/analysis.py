import json
import logging
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse
from redis.exceptions import RedisError
from rq.exceptions import InvalidJobOperation, NoSuchJobError
from rq.job import Job
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_owned_dataset, get_owned_project
from app.core.exceptions import ConflictError, NotFoundError, ValidationAppError
from app.core.storage import get_storage
from app.db.session import get_db
from app.jobs.queue import get_redis_connection
from app.models.analysis_artifact import AnalysisArtifact
from app.models.analysis_job import AnalysisJob, AnalysisJobStatus
from app.models.user import User
from app.schemas.analysis import AnalysisJobCreate, AnalysisJobRead
from app.schemas.analysis_artifact import AnalysisArtifactRead
from app.schemas.calibration_residuals import CalibrationResidualsOut
from app.services.analysis_jobs import create_analysis_job
from app.services.visualization import CALIBRATION_RESIDUALS_ARTIFACT_TYPE, artifact_filename
from storage import StorageBackend

logger = logging.getLogger("terrainx.backend.analysis")

# Mounted at /projects/{project_id}/datasets/{dataset_id}/analysis
creation_router = APIRouter()
# Mounted at /projects/{project_id}/analysis
management_router = APIRouter()


async def _get_owned_job(
    project_id: uuid.UUID, job_id: uuid.UUID, current_user: User, db: AsyncSession
) -> AnalysisJob:
    await get_owned_project(project_id, current_user, db)
    result = await db.execute(
        select(AnalysisJob).where(AnalysisJob.id == job_id, AnalysisJob.project_id == project_id)
    )
    job = result.scalar_one_or_none()
    if job is None:
        raise NotFoundError("Analysis job not found")
    return job


@creation_router.post("", response_model=AnalysisJobRead, status_code=201)
async def create_job(
    project_id: uuid.UUID,
    dataset_id: uuid.UUID,
    payload: AnalysisJobCreate = AnalysisJobCreate(),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AnalysisJob:
    dataset = await get_owned_dataset(project_id, dataset_id, current_user, db)
    return await create_analysis_job(project_id, dataset, payload, current_user, db)


@management_router.get("", response_model=list[AnalysisJobRead])
async def list_jobs(
    project_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[AnalysisJob]:
    await get_owned_project(project_id, current_user, db)
    result = await db.execute(
        select(AnalysisJob)
        .where(AnalysisJob.project_id == project_id)
        .order_by(AnalysisJob.created_at.desc())
    )
    return list(result.scalars().all())


@management_router.get("/{job_id}", response_model=AnalysisJobRead)
async def get_job(
    project_id: uuid.UUID,
    job_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AnalysisJob:
    return await _get_owned_job(project_id, job_id, current_user, db)


@management_router.post("/{job_id}/cancel", response_model=AnalysisJobRead)
async def cancel_job(
    project_id: uuid.UUID,
    job_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AnalysisJob:
    """Request cancellation of a job — while it is still `queued` OR already
    `running` (Phase 11; previously queued-only).

    A `queued` job is cancelled immediately and unconditionally (it never
    started, so there is nothing to interrupt). A `running` job is
    cancelled *cooperatively*: this endpoint only flips the job's status to
    `cancelled` — it does NOT and cannot interrupt an in-flight CPU model
    inference call. The worker's pipeline observes the request at its next
    safe checkpoint (`_raise_if_cancelled` in analysis_execution.py, called
    between pipeline stages — never mid-inference) and stops there, leaving
    the job `cancelled` rather than `completed`. This is why the response
    for a running job is honest that cancellation was merely *requested*.

    The status flip is a single atomic, conditional UPDATE (not a
    read-then-write) so a worker racing to claim/complete/fail the job at
    the same moment can't have its result silently overwritten, and can't
    silently overwrite this cancellation either — see docs/ARCHITECTURE.md.
    A job that has already reached a terminal state (`completed`/`failed`/
    `cancelled`) can never be cancelled: the WHERE clause below simply
    matches no rows, and the conditional writes in analysis_execution.py
    that finalize a job (mark it completed/failed) are equally conditional
    on the job still being `running`, so a completion/failure and a
    cancellation request racing each other can never both "win" — exactly
    one final state is ever persisted.
    """
    job = await _get_owned_job(project_id, job_id, current_user, db)

    result = await db.execute(
        update(AnalysisJob)
        .where(
            AnalysisJob.id == job_id,
            AnalysisJob.status.in_([AnalysisJobStatus.QUEUED, AnalysisJobStatus.RUNNING]),
        )
        .values(status=AnalysisJobStatus.CANCELLED, completed_at=datetime.now(UTC))
    )
    await db.commit()
    if result.rowcount == 0:
        raise ConflictError(
            "This job cannot be cancelled; it has already reached a final state "
            "(completed, failed, or cancelled)."
        )

    try:
        Job.fetch(str(job_id), connection=get_redis_connection()).cancel()
    except (NoSuchJobError, InvalidJobOperation):
        # Best-effort RQ-side bookkeeping only — our Postgres row above is
        # the real source of truth. NoSuchJobError: already gone from RQ's
        # own registries. InvalidJobOperation: RQ already considers it
        # finished/cancelled (e.g. it raced with real completion). Neither
        # changes the outcome of the DB flip that already committed above.
        pass
    except RedisError:
        logger.warning("Could not reach Redis to cancel queued RQ job %s", job_id)

    # expire_on_commit=False means `job` won't reflect the raw UPDATE above
    # until explicitly refreshed.
    await db.refresh(job)
    return job


async def _get_owned_artifact(
    project_id: uuid.UUID,
    job_id: uuid.UUID,
    artifact_id: uuid.UUID,
    current_user: User,
    db: AsyncSession,
) -> AnalysisArtifact:
    await _get_owned_job(project_id, job_id, current_user, db)
    result = await db.execute(
        select(AnalysisArtifact).where(
            AnalysisArtifact.id == artifact_id, AnalysisArtifact.analysis_job_id == job_id
        )
    )
    artifact = result.scalar_one_or_none()
    if artifact is None:
        raise NotFoundError("Artifact not found")
    return artifact


@management_router.get("/{job_id}/artifacts", response_model=list[AnalysisArtifactRead])
async def list_artifacts(
    project_id: uuid.UUID,
    job_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[AnalysisArtifact]:
    await _get_owned_job(project_id, job_id, current_user, db)
    result = await db.execute(
        select(AnalysisArtifact)
        .where(AnalysisArtifact.analysis_job_id == job_id)
        .order_by(AnalysisArtifact.created_at.asc())
    )
    return list(result.scalars().all())


@management_router.get("/{job_id}/artifacts/{artifact_id}/download")
async def download_artifact(
    project_id: uuid.UUID,
    job_id: uuid.UUID,
    artifact_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    storage: StorageBackend = Depends(get_storage),
) -> FileResponse:
    artifact = await _get_owned_artifact(project_id, job_id, artifact_id, current_user, db)
    absolute_path = storage.absolute_path(artifact.storage_key)
    if not absolute_path.is_file():
        raise NotFoundError("Stored artifact file is missing")
    return FileResponse(
        path=absolute_path,
        media_type=artifact.mime_type,
        filename=artifact_filename(artifact),
    )


@management_router.get(
    "/{job_id}/artifacts/{artifact_id}/calibration-residuals",
    response_model=CalibrationResidualsOut,
)
async def get_calibration_residuals(
    project_id: uuid.UUID,
    job_id: uuid.UUID,
    artifact_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    storage: StorageBackend = Depends(get_storage),
) -> CalibrationResidualsOut:
    """P1-5: the stored calibration residuals (sample points only) plus the
    artifact's persisted summary/provenance — returned exactly as stored."""
    artifact = await _get_owned_artifact(project_id, job_id, artifact_id, current_user, db)
    if artifact.artifact_type != CALIBRATION_RESIDUALS_ARTIFACT_TYPE:
        raise ValidationAppError(
            "Calibration residuals require a calibration_residuals artifact; "
            f"this artifact is '{artifact.artifact_type}'."
        )
    absolute_path = storage.absolute_path(artifact.storage_key)
    if not absolute_path.is_file():
        raise NotFoundError("Stored artifact file is missing")
    feature_collection = json.loads(absolute_path.read_text(encoding="utf-8"))
    return CalibrationResidualsOut(
        artifact_id=artifact.id,
        analysis_job_id=artifact.analysis_job_id,
        summary=artifact.artifact_metadata or {},
        feature_collection=feature_collection,
    )
