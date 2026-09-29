"""Phase 9: report creation — validates a completed analysis actually exists
for the target dataset, persists a real `Report` row, and enqueues real
generation to the existing RQ "default" queue/worker. Mirrors
`app/services/analysis_jobs.py`'s role for the analysis-job domain; the
actual generation pipeline lives in `app/services/report_execution.py` (kept
separate to avoid a circular import — see that module's docstring).
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from rq.job import Callback
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import ValidationAppError
from app.jobs.queue import get_queue
from app.jobs.tasks import handle_report_generation_failure, run_report_generation
from app.models.dataset import Dataset
from app.models.report import Report, ReportStatus
from app.models.user import User
from app.services.visualization import latest_completed_disaster_job, latest_completed_job

logger = logging.getLogger("terrainx.backend.reports")


async def create_report(
    project_id: uuid.UUID,
    dataset: Dataset,
    current_user: User,
    db: AsyncSession,
) -> Report:
    """Validates a completed analysis actually exists for this dataset
    (immediate 422 otherwise — fast, friendly feedback, mirroring
    `analysis_jobs.py::_validate_reference_dataset`), persists a real
    `Report` row, and enqueues real generation. The same real availability
    check runs again inside `report_execution.py::generate_report` at
    execution time, since time passes between creation and execution and
    the dataset's analysis state could change in between."""
    depth_job = await latest_completed_job(db, dataset.id)
    disaster_job = await latest_completed_disaster_job(db, dataset.id)
    if depth_job is None and disaster_job is None:
        raise ValidationAppError(
            "No completed analysis exists for this dataset yet — run an analysis "
            "(and, optionally, disaster screening) before generating a report."
        )

    report = Report(
        project_id=project_id,
        dataset_id=dataset.id,
        user_id=current_user.id,
        status=ReportStatus.PENDING,
    )
    db.add(report)
    await db.commit()
    await db.refresh(report)

    try:
        get_queue().enqueue(
            run_report_generation,
            str(report.id),
            job_id=f"report-{report.id}",
            job_timeout=get_settings().REPORT_GENERATION_TIMEOUT_SECONDS,
            on_failure=Callback(handle_report_generation_failure),
        )
    except Exception:
        # The row exists and is real, but nothing will ever process it —
        # reflect that truthfully rather than leaving it "pending" forever
        # (same pattern as analysis_jobs.py::create_analysis_job).
        logger.exception("Failed to enqueue report generation %s", report.id)
        report.status = ReportStatus.FAILED
        report.error_message = "Failed to enqueue this report for generation. Please try again."
        report.completed_at = datetime.now(UTC)
        await db.commit()
        await db.refresh(report)

    return report
