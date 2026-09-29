import uuid

import jwt
from fastapi import Depends
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError, UnauthorizedError
from app.core.security import decode_access_token
from app.db.session import get_db
from app.models.analysis_artifact import AnalysisArtifact
from app.models.analysis_job import AnalysisJob
from app.models.dataset import Dataset
from app.models.measurement import Measurement
from app.models.project import Project
from app.models.report import Report
from app.models.user import User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)


async def get_current_user(
    token: str | None = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    if token is None:
        raise UnauthorizedError("Missing authentication token")

    try:
        payload = decode_access_token(token)
        user_id = uuid.UUID(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        raise UnauthorizedError("Invalid or expired token") from None

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise UnauthorizedError("User no longer exists")

    return user


async def get_owned_project(project_id: uuid.UUID, current_user: User, db: AsyncSession) -> Project:
    """Fetch a project only if it belongs to the current user.

    Returns 404 (not 403) for a project owned by someone else, so existence
    of other users' projects is never leaked (IDOR prevention).
    """
    result = await db.execute(
        select(Project).where(Project.id == project_id, Project.owner_id == current_user.id)
    )
    project = result.scalar_one_or_none()
    if project is None:
        raise NotFoundError("Project not found")
    return project


async def get_owned_dataset(
    project_id: uuid.UUID, dataset_id: uuid.UUID, current_user: User, db: AsyncSession
) -> Dataset:
    """Fetch a dataset only if the current user owns its project and it
    actually belongs to that project (not just any dataset the DB has).

    Returns 404 (not 403) in every rejection case — same IDOR-prevention
    rationale as `get_owned_project`.
    """
    await get_owned_project(project_id, current_user, db)
    result = await db.execute(
        select(Dataset).where(Dataset.id == dataset_id, Dataset.project_id == project_id)
    )
    dataset = result.scalar_one_or_none()
    if dataset is None:
        raise NotFoundError("Dataset not found")
    return dataset


async def get_owned_analysis_job(
    project_id: uuid.UUID, job_id: uuid.UUID, current_user: User, db: AsyncSession
) -> AnalysisJob:
    """Fetch an analysis job only if the current user owns its project and
    it actually belongs to that project — same IDOR-prevention shape as
    `get_owned_dataset` (404, never 403, on any ownership mismatch). Used by
    Phase 5's visualization endpoints (`app/api/v1/endpoints/visualization.py`);
    `app/api/v1/endpoints/analysis.py` has its own equivalent private helper
    predating this one, left as-is to avoid touching working Phase 2 code.
    """
    await get_owned_project(project_id, current_user, db)
    result = await db.execute(
        select(AnalysisJob).where(AnalysisJob.id == job_id, AnalysisJob.project_id == project_id)
    )
    job = result.scalar_one_or_none()
    if job is None:
        raise NotFoundError("Analysis job not found")
    return job


async def get_owned_artifact(
    project_id: uuid.UUID,
    job_id: uuid.UUID,
    artifact_id: uuid.UUID,
    current_user: User,
    db: AsyncSession,
) -> AnalysisArtifact:
    """Fetch an artifact only through the full project -> job -> artifact
    ownership chain (see `get_owned_analysis_job`)."""
    await get_owned_analysis_job(project_id, job_id, current_user, db)
    result = await db.execute(
        select(AnalysisArtifact).where(
            AnalysisArtifact.id == artifact_id, AnalysisArtifact.analysis_job_id == job_id
        )
    )
    artifact = result.scalar_one_or_none()
    if artifact is None:
        raise NotFoundError("Artifact not found")
    return artifact


async def get_owned_measurement(
    project_id: uuid.UUID, measurement_id: uuid.UUID, current_user: User, db: AsyncSession
) -> Measurement:
    """Fetch a saved measurement only if the current user owns its project
    and it actually belongs to that project — same IDOR-prevention shape
    (404, never 403) as `get_owned_dataset`/`get_owned_artifact`. Used by
    Phase 7's measurement CRUD endpoints
    (`app/api/v1/endpoints/measurements.py`)."""
    await get_owned_project(project_id, current_user, db)
    result = await db.execute(
        select(Measurement).where(
            Measurement.id == measurement_id, Measurement.project_id == project_id
        )
    )
    measurement = result.scalar_one_or_none()
    if measurement is None:
        raise NotFoundError("Measurement not found")
    return measurement


async def get_owned_report(
    project_id: uuid.UUID, report_id: uuid.UUID, current_user: User, db: AsyncSession
) -> Report:
    """Fetch a report only if the current user owns its project and it
    actually belongs to that project — same IDOR-prevention shape (404,
    never 403) as `get_owned_measurement`. Used by Phase 9's report/export
    endpoints (`app/api/v1/endpoints/reports.py`)."""
    await get_owned_project(project_id, current_user, db)
    result = await db.execute(
        select(Report).where(Report.id == report_id, Report.project_id == project_id)
    )
    report = result.scalar_one_or_none()
    if report is None:
        raise NotFoundError("Report not found")
    return report
