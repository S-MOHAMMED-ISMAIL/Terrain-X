"""R1 terminal-state recovery for orphaned report generation."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.storage import get_storage
from app.db.session import AsyncSessionLocal
from app.models.report import Report, ReportStatus

logger = logging.getLogger("terrainx.backend.report_reconciliation")

REPORT_TIMEOUT_ERROR_MESSAGE = "Report generation timed out."
REPORT_WORKER_FAILURE_ERROR_MESSAGE = "Report generation failed in the worker."
STALE_REPORT_ERROR_MESSAGE = (
    "Report generation stopped responding because its worker disappeared or ran too long. "
    "No completed report was produced. Please generate the report again."
)

_ACTIVE_REPORT_STATUSES = (ReportStatus.PENDING, ReportStatus.GENERATING)


def _cleanup_partial_report(project_id: uuid.UUID, report_id: uuid.UUID) -> None:
    try:
        get_storage().delete_prefix(f"projects/{project_id}/reports/{report_id}")
    except Exception:
        logger.exception("Failed to clean partial files for report %s", report_id)


async def transition_active_report_to_failed(
    db: AsyncSession,
    report_id: uuid.UUID,
    error_message: str,
    *,
    now: datetime | None = None,
) -> uuid.UUID | None:
    """Atomically fail one active report and return its project ID.

    Terminal reports never match, so a delayed callback cannot overwrite a
    successful completion or an earlier failure.
    """
    failed_at = now or datetime.now(UTC)
    result = await db.execute(
        update(Report)
        .where(Report.id == report_id, Report.status.in_(_ACTIVE_REPORT_STATUSES))
        .values(
            status=ReportStatus.FAILED,
            error_message=error_message,
            report_metadata=None,
            pdf_storage_key=None,
            csv_storage_key=None,
            bundle_storage_key=None,
            completed_at=failed_at,
            updated_at=failed_at,
        )
        .returning(Report.project_id)
    )
    project_id = result.scalar_one_or_none()
    await db.commit()
    return project_id


async def fail_report_job(report_id: uuid.UUID, error_message: str) -> bool:
    """Failure-callback entrypoint with its own database session."""
    async with AsyncSessionLocal() as db:
        project_id = await transition_active_report_to_failed(db, report_id, error_message)
    if project_id is None:
        return False
    _cleanup_partial_report(project_id, report_id)
    logger.warning("Report %s was failed by its RQ failure callback", report_id)
    return True


async def reconcile_stale_reports(
    db: AsyncSession,
    *,
    now: datetime | None = None,
    project_id: uuid.UUID | None = None,
    report_id: uuid.UUID | None = None,
) -> list[str]:
    """Atomically fail stale pending/generating reports.

    Optional project/report filters let polling endpoints reconcile only rows
    the authenticated caller already owns. The periodic sweep omits filters.
    """
    settings = get_settings()
    checked_at = now or datetime.now(UTC)
    threshold = checked_at - timedelta(seconds=settings.STALE_REPORT_AFTER_SECONDS)
    conditions = [
        Report.status.in_(_ACTIVE_REPORT_STATUSES),
        Report.updated_at < threshold,
    ]
    if project_id is not None:
        conditions.append(Report.project_id == project_id)
    if report_id is not None:
        conditions.append(Report.id == report_id)

    result = await db.execute(
        update(Report)
        .where(*conditions)
        .values(
            status=ReportStatus.FAILED,
            error_message=STALE_REPORT_ERROR_MESSAGE,
            report_metadata=None,
            pdf_storage_key=None,
            csv_storage_key=None,
            bundle_storage_key=None,
            completed_at=checked_at,
            updated_at=checked_at,
        )
        .returning(Report.id, Report.project_id)
    )
    reconciled = [(row[0], row[1]) for row in result.all()]
    await db.commit()

    for stale_report_id, stale_project_id in reconciled:
        _cleanup_partial_report(stale_project_id, stale_report_id)

    if reconciled:
        logger.warning(
            "Reconciled %d stale report(s) to failed after %ds: %s",
            len(reconciled),
            settings.STALE_REPORT_AFTER_SECONDS,
            [str(item[0]) for item in reconciled],
        )
    return [str(item[0]) for item in reconciled]
