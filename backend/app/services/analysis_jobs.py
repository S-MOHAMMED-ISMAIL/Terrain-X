"""Analysis job creation: validates the target dataset, persists a job row,
and enqueues it for the worker — handling enqueue failure so a job is never
left silently `queued` with no worker ever able to see it.
"""

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import ConflictError, ValidationAppError
from app.jobs.queue import get_queue
from app.jobs.tasks import run_analysis_job
from app.models.analysis_artifact import AnalysisArtifact
from app.models.analysis_job import AnalysisJob, AnalysisJobStatus
from app.models.dataset import Dataset, DatasetRole, DatasetStatus
from app.models.user import User
from app.schemas.analysis import AnalysisJobCreate
from app.services.disaster_pipeline import ELEVATION_ARTIFACT_TYPES, NOT_ELEVATION_ERROR

logger = logging.getLogger("terrainx.backend.analysis")


async def _validate_reference_dataset(
    project_id: uuid.UUID,
    reference_dataset_id: uuid.UUID,
    expected_role: DatasetRole,
    db: AsyncSession,
) -> None:
    """Validates a DEM/GCP reference at job-creation time (immediate 422 on
    a bad reference) — the same dataset is re-verified again by the worker
    at execution time (app/services/calibration_pipeline.py), since time
    passes between creation and execution and the reference could be
    deleted or changed in between; this check exists purely for fast,
    friendly feedback on an obviously-wrong reference.
    """
    result = await db.execute(
        select(Dataset).where(Dataset.id == reference_dataset_id, Dataset.project_id == project_id)
    )
    reference = result.scalar_one_or_none()
    if reference is None:
        raise ValidationAppError(
            f"Reference dataset {reference_dataset_id} was not found in this project."
        )
    if reference.role != expected_role:
        raise ValidationAppError(
            f"Dataset {reference_dataset_id} has role '{reference.role.value}', "
            f"but a '{expected_role.value}' dataset was expected."
        )
    if reference.status != DatasetStatus.VALID:
        raise ValidationAppError(
            f"Reference dataset {reference_dataset_id} has status "
            f"'{reference.status.value}'; only 'valid' references can be used."
        )


async def _validate_disaster_source_artifact(
    project_id: uuid.UUID, artifact_id: uuid.UUID, db: AsyncSession
) -> None:
    """Validates a disaster-screening job's cited elevation artifact at
    job-creation time (immediate 422 on a bad reference) — the same
    artifact is re-verified again by the worker at execution time
    (app/services/disaster_pipeline.py), since time passes between creation
    and execution and the artifact could be deleted in between; this check
    exists purely for fast, friendly feedback on an obviously-wrong
    reference. Scoped by PROJECT, not by this job's own dataset — a
    disaster job may screen an elevation artifact produced from any dataset
    within the same project (see docs/ARCHITECTURE.md §3.8).
    """
    result = await db.execute(
        select(AnalysisArtifact, AnalysisJob.project_id)
        .join(AnalysisJob, AnalysisArtifact.analysis_job_id == AnalysisJob.id)
        .where(AnalysisArtifact.id == artifact_id, AnalysisJob.project_id == project_id)
    )
    row = result.first()
    if row is None:
        raise ValidationAppError(f"Elevation artifact {artifact_id} was not found in this project.")
    artifact, _ = row
    if artifact.artifact_type not in ELEVATION_ARTIFACT_TYPES:
        raise ValidationAppError(NOT_ELEVATION_ERROR)


async def create_analysis_job(
    project_id: uuid.UUID,
    dataset: Dataset,
    payload: AnalysisJobCreate,
    current_user: User,
    db: AsyncSession,
) -> AnalysisJob:
    if dataset.status != DatasetStatus.VALID:
        raise ValidationAppError(
            "Only datasets with status 'valid' can be analyzed. "
            f"This dataset's status is '{dataset.status.value}'."
        )

    parameters = payload.parameters
    if parameters.dem_reference_dataset_id is not None:
        await _validate_reference_dataset(
            project_id, parameters.dem_reference_dataset_id, DatasetRole.DEM_REFERENCE, db
        )
    if parameters.gcp_reference_dataset_id is not None:
        await _validate_reference_dataset(
            project_id, parameters.gcp_reference_dataset_id, DatasetRole.GCP_REFERENCE, db
        )
    if parameters.disaster_source_artifact_id is not None:
        await _validate_disaster_source_artifact(
            project_id, parameters.disaster_source_artifact_id, db
        )

    job = AnalysisJob(
        project_id=project_id,
        dataset_id=dataset.id,
        user_id=current_user.id,
        status=AnalysisJobStatus.QUEUED,
        # mode="json" so UUID reference-dataset IDs are serialized to plain
        # strings before hitting the JSONB column — SQLAlchemy's JSON
        # serializer has no UUID encoder of its own.
        parameters=payload.parameters.model_dump(mode="json"),
    )
    db.add(job)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise ConflictError(
            "This dataset already has an active analysis job. "
            "Wait for it to finish, or cancel it, before starting another."
        ) from None
    await db.refresh(job)

    try:
        # Phase 11: an explicit, generous job_timeout — see
        # Settings.ANALYSIS_JOB_TIMEOUT_SECONDS for the empirical rationale.
        # Without this, RQ silently applies its own 180s default, which a
        # real measured worst-case run came within ~10s of.
        get_queue().enqueue(
            run_analysis_job,
            str(job.id),
            job_id=str(job.id),
            job_timeout=get_settings().ANALYSIS_JOB_TIMEOUT_SECONDS,
        )
    except Exception:
        # The job row exists and is real, but nothing will ever process it —
        # reflect that truthfully rather than leaving it "queued" forever.
        logger.exception("Failed to enqueue analysis job %s", job.id)
        job.status = AnalysisJobStatus.FAILED
        job.error_message = "Failed to enqueue this job for processing. Please try again."
        job.completed_at = datetime.now(UTC)
        await db.commit()
        await db.refresh(job)

    return job
