import uuid

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_owned_dataset, get_owned_project, get_owned_report
from app.core.exceptions import NotFoundError, ValidationAppError
from app.core.storage import get_storage
from app.db.session import get_db
from app.models.report import Report, ReportStatus
from app.models.user import User
from app.schemas.report import ReportCreate, ReportRead
from app.services.report_reconciliation import reconcile_stale_reports
from app.services.report_service import create_report
from storage import StorageBackend

# Mounted at /projects/{project_id}/datasets/{dataset_id}/reports
creation_router = APIRouter()
# Mounted at /projects/{project_id}/reports
management_router = APIRouter()


@creation_router.post("", response_model=ReportRead, status_code=201)
async def create_dataset_report(
    project_id: uuid.UUID,
    dataset_id: uuid.UUID,
    payload: ReportCreate = ReportCreate(),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Report:
    dataset = await get_owned_dataset(project_id, dataset_id, current_user, db)
    return await create_report(project_id, dataset, current_user, db)


@management_router.get("", response_model=list[ReportRead])
async def list_reports(
    project_id: uuid.UUID,
    dataset_id: uuid.UUID | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[Report]:
    await get_owned_project(project_id, current_user, db)
    await reconcile_stale_reports(db, project_id=project_id)
    query = select(Report).where(Report.project_id == project_id)
    if dataset_id is not None:
        query = query.where(Report.dataset_id == dataset_id)
    result = await db.execute(query.order_by(Report.created_at.desc()))
    return list(result.scalars().all())


@management_router.get("/{report_id}", response_model=ReportRead)
async def get_report(
    project_id: uuid.UUID,
    report_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Report:
    report = await get_owned_report(project_id, report_id, current_user, db)
    await reconcile_stale_reports(db, report_id=report.id)
    await db.refresh(report)
    return report


def _require_completed(report: Report) -> None:
    if report.status != ReportStatus.COMPLETED:
        raise ValidationAppError(
            f"This report is not ready yet (status: {report.status.value}). "
            "Wait for generation to complete before downloading."
        )


@management_router.get("/{report_id}/json")
async def get_report_json(
    project_id: uuid.UUID,
    report_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Returns the real, persisted `report_metadata` snapshot verbatim —
    the exact same data the PDF/CSV/bundle for this report were rendered
    from (see `app/services/report_builder.py`), never recomputed or
    re-derived on the fly."""
    report = await get_owned_report(project_id, report_id, current_user, db)
    _require_completed(report)
    return JSONResponse(content=report.report_metadata)


@management_router.get("/{report_id}/pdf")
async def download_report_pdf(
    project_id: uuid.UUID,
    report_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    storage: StorageBackend = Depends(get_storage),
) -> FileResponse:
    report = await get_owned_report(project_id, report_id, current_user, db)
    _require_completed(report)
    if report.pdf_storage_key is None:
        raise NotFoundError("No PDF is available for this report.")
    path = storage.absolute_path(report.pdf_storage_key)
    if not path.is_file():
        raise NotFoundError("Stored report PDF file is missing")
    return FileResponse(path=path, media_type="application/pdf", filename="report.pdf")


@management_router.get("/{report_id}/csv")
async def download_report_csv(
    project_id: uuid.UUID,
    report_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    storage: StorageBackend = Depends(get_storage),
) -> FileResponse:
    report = await get_owned_report(project_id, report_id, current_user, db)
    _require_completed(report)
    if report.csv_storage_key is None:
        raise NotFoundError(
            "No tabular data (measurements, terrain statistics, or hazard class counts) "
            "was available to export as CSV for this report."
        )
    path = storage.absolute_path(report.csv_storage_key)
    if not path.is_file():
        raise NotFoundError("Stored report CSV file is missing")
    return FileResponse(path=path, media_type="text/csv", filename="report.csv")


@management_router.get("/{report_id}/bundle")
async def download_report_bundle(
    project_id: uuid.UUID,
    report_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    storage: StorageBackend = Depends(get_storage),
) -> FileResponse:
    report = await get_owned_report(project_id, report_id, current_user, db)
    _require_completed(report)
    if report.bundle_storage_key is None:
        raise NotFoundError("No export bundle is available for this report.")
    path = storage.absolute_path(report.bundle_storage_key)
    if not path.is_file():
        raise NotFoundError("Stored report bundle file is missing")
    return FileResponse(
        path=path, media_type="application/zip", filename="terrainx_report_bundle.zip"
    )
