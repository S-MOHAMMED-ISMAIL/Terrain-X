import uuid

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_owned_dataset, get_owned_project
from app.core.config import get_settings
from app.core.exceptions import NotFoundError, PayloadTooLargeError, ValidationAppError
from app.core.storage import get_storage
from app.db.session import get_db
from app.models.dataset import Dataset, DatasetRole
from app.models.user import User
from app.schemas.dataset import DatasetRead
from app.services.dataset_ingestion import ingest_uploaded_file
from app.services.gcp_ingestion import ingest_gcp_reference
from storage import StorageBackend

router = APIRouter()


@router.post("", response_model=DatasetRead, status_code=201)
async def upload_dataset(
    project_id: uuid.UUID,
    request: Request,
    file: UploadFile = File(...),
    role: str = Form(default=DatasetRole.SOURCE_IMAGE.value),
    gcp_crs: str | None = Form(default=None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Dataset:
    """Uploads a dataset. `role` is a validated enum, never an arbitrary
    client-supplied string (see app.models.dataset.DatasetRole) — invalid
    values are rejected with 422, not silently coerced. `gcp_crs` is
    required (and only meaningful) when role=gcp_reference: a CSV of GCPs
    carries no CRS of its own, so the caller must declare one explicitly.
    """
    await get_owned_project(project_id, current_user, db)

    try:
        dataset_role = DatasetRole(role)
    except ValueError:
        raise ValidationAppError(
            f"Invalid role '{role}'. Must be one of: "
            f"{', '.join(member.value for member in DatasetRole)}."
        ) from None

    settings = get_settings()
    content_length = request.headers.get("content-length")
    if content_length is not None and int(content_length) > settings.max_upload_size_bytes:
        raise PayloadTooLargeError(
            f"File exceeds the maximum allowed size of {settings.MAX_UPLOAD_SIZE_MB} MB"
        )

    if dataset_role == DatasetRole.GCP_REFERENCE:
        if not gcp_crs:
            raise ValidationAppError(
                "gcp_crs is required when uploading a GCP reference (e.g. 'EPSG:4326')."
            )
        return await ingest_gcp_reference(file, project_id, gcp_crs, db)

    return await ingest_uploaded_file(file, project_id, db, role=dataset_role)


@router.get("", response_model=list[DatasetRead])
async def list_datasets(
    project_id: uuid.UUID,
    role: str | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[Dataset]:
    await get_owned_project(project_id, current_user, db)

    query = select(Dataset).where(Dataset.project_id == project_id)
    if role is not None:
        try:
            query = query.where(Dataset.role == DatasetRole(role))
        except ValueError:
            raise ValidationAppError(
                f"Invalid role '{role}'. Must be one of: "
                f"{', '.join(member.value for member in DatasetRole)}."
            ) from None

    result = await db.execute(query.order_by(Dataset.created_at.desc()))
    return list(result.scalars().all())


@router.get("/{dataset_id}", response_model=DatasetRead)
async def get_dataset(
    project_id: uuid.UUID,
    dataset_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Dataset:
    return await get_owned_dataset(project_id, dataset_id, current_user, db)


@router.delete("/{dataset_id}", status_code=204)
async def delete_dataset(
    project_id: uuid.UUID,
    dataset_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    storage: StorageBackend = Depends(get_storage),
) -> None:
    dataset = await get_owned_dataset(project_id, dataset_id, current_user, db)
    storage_key = dataset.storage_key
    await db.delete(dataset)
    await db.commit()
    storage.delete(storage_key)


@router.get("/{dataset_id}/download")
async def download_dataset(
    project_id: uuid.UUID,
    dataset_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    storage: StorageBackend = Depends(get_storage),
) -> FileResponse:
    dataset = await get_owned_dataset(project_id, dataset_id, current_user, db)
    absolute_path = storage.absolute_path(dataset.storage_key)
    if not absolute_path.is_file():
        raise NotFoundError("Stored file is missing")
    return FileResponse(
        path=absolute_path,
        media_type=dataset.mime_type,
        filename=dataset.original_filename,
    )
