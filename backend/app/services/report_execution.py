"""Phase 9: the real RQ-worker-invoked report-generation pipeline — mirrors
`app/services/analysis_execution.py`'s role for the analysis-job domain.
Kept in its own module, separate from `app/services/report_service.py` (the
"create" side), for the exact same reason `analysis_execution.py` is kept
separate from `analysis_jobs.py`: `app/jobs/tasks.py` imports this module,
and `report_service.py` imports `app/jobs/tasks.py` to enqueue — if this
module instead lived in `report_service.py`, that would cycle back
(report_service -> jobs.tasks -> report_service). This module has no
dependents that could cycle back through it.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import UTC, datetime

from rq.timeouts import JobTimeoutException
from sqlalchemy import func, select, update

from app.core.storage import get_storage
from app.db.session import AsyncSessionLocal
from app.models.analysis_artifact import AnalysisArtifact
from app.models.analysis_job import AnalysisJob
from app.models.dataset import Dataset
from app.models.measurement import Measurement
from app.models.project import Project
from app.models.report import Report, ReportStatus
from app.services.report_builder import ReportContext, build_report_data
from app.services.report_reconciliation import (
    REPORT_TIMEOUT_ERROR_MESSAGE,
    transition_active_report_to_failed,
)
from app.services.report_render import build_bundle_zip, render_csv, render_pdf
from app.services.visualization import (
    CALIBRATION_RESIDUALS_ARTIFACT_TYPE,
    artifact_filename,
    latest_completed_disaster_job,
    latest_completed_job,
)

logger = logging.getLogger("terrainx.backend.reports")


def report_storage_prefix(project_id: uuid.UUID, report_id: uuid.UUID) -> str:
    return f"projects/{project_id}/reports/{report_id}"


async def _fetch_artifacts(db, job_id: uuid.UUID) -> list[AnalysisArtifact]:
    result = await db.execute(
        select(AnalysisArtifact)
        .where(AnalysisArtifact.analysis_job_id == job_id)
        .order_by(AnalysisArtifact.created_at.asc())
    )
    return list(result.scalars().all())


async def generate_report(report_id: uuid.UUID) -> None:
    """Gathers real, already-persisted rows/artifacts for the report's
    dataset, builds the real data snapshot (`report_builder.py`), renders
    the real PDF/CSV/ZIP (`report_render.py`), writes them through the
    existing storage backend, and persists the result. A report has no
    partially-valid fallback (unlike Phase 4/6's soft-failure add-ons): any
    real failure here marks the WHOLE report `failed` with the real reason.
    """
    storage = get_storage()
    async with AsyncSessionLocal() as db:
        claim = await db.execute(
            update(Report)
            .where(Report.id == report_id, Report.status == ReportStatus.PENDING)
            .values(
                status=ReportStatus.GENERATING,
                error_message=None,
                completed_at=None,
                updated_at=func.now(),
            )
            .returning(Report.id)
        )
        claimed = claim.scalar_one_or_none()
        await db.commit()
        if claimed is None:
            logger.warning(
                "Report %s is missing or no longer pending; skipping generation.", report_id
            )
            return

        report = await db.get(Report, report_id)
        if report is None:
            return
        project_id = report.project_id

        try:
            dataset = await db.get(Dataset, report.dataset_id)
            project = await db.get(Project, report.project_id)
            if dataset is None or project is None:
                raise ValueError("The dataset or project this report belongs to no longer exists.")

            depth_job: AnalysisJob | None = await latest_completed_job(db, dataset.id)
            disaster_job: AnalysisJob | None = await latest_completed_disaster_job(db, dataset.id)
            if depth_job is None and disaster_job is None:
                raise ValueError("No completed analysis is currently available for this dataset.")

            depth_artifacts = await _fetch_artifacts(db, depth_job.id) if depth_job else []
            disaster_artifacts = await _fetch_artifacts(db, disaster_job.id) if disaster_job else []

            reference_dataset: Dataset | None = None
            if depth_job is not None and depth_job.calibration_metadata:
                ref_id = depth_job.calibration_metadata.get("reference_dataset_id")
                if ref_id:
                    reference_dataset = await db.get(Dataset, uuid.UUID(ref_id))

            job_ids = [j.id for j in (depth_job, disaster_job) if j is not None]
            measurements: list[Measurement] = []
            if job_ids:
                result = await db.execute(
                    select(Measurement)
                    .where(Measurement.analysis_job_id.in_(job_ids))
                    .order_by(Measurement.created_at.asc())
                )
                measurements = list(result.scalars().all())

            # P1-5: the stored residual GeoJSON, read once here so the builder
            # and CSV renderer stay free of storage I/O.
            calibration_residuals: dict | None = None
            residuals_artifact = next(
                (
                    a
                    for a in depth_artifacts
                    if a.artifact_type == CALIBRATION_RESIDUALS_ARTIFACT_TYPE
                ),
                None,
            )
            if residuals_artifact is not None:
                calibration_residuals = json.loads(
                    storage.absolute_path(residuals_artifact.storage_key).read_text(
                        encoding="utf-8"
                    )
                )

            ctx = ReportContext(
                project=project,
                dataset=dataset,
                generated_by_user_id=report.user_id,
                depth_job=depth_job,
                disaster_job=disaster_job,
                depth_job_artifacts=depth_artifacts,
                disaster_job_artifacts=disaster_artifacts,
                reference_dataset=reference_dataset,
                measurements=measurements,
                calibration_residuals=calibration_residuals,
            )
            # Real per-stage timings, recorded the same way
            # `disaster_pipeline.py::run_disaster_screening` already does for
            # its own stages — never invented, always a real
            # `time.monotonic()` delta around the real call it measures.
            timings: dict[str, float] = {}

            t0 = time.monotonic()
            data = build_report_data(ctx)
            timings["build_report_data_seconds"] = time.monotonic() - t0

            t0 = time.monotonic()
            pdf_bytes = render_pdf(data)
            timings["render_pdf_seconds"] = time.monotonic() - t0

            t0 = time.monotonic()
            csv_bytes = render_csv(data, calibration_residuals)
            timings["render_csv_seconds"] = time.monotonic() - t0

            # Recorded into `data` NOW, before the ZIP bundle is built below,
            # so the bundle's own `analysis.json` and the standalone JSON
            # export always contain byte-identical content (see
            # docs/ARCHITECTURE.md §3.9) — the ZIP-build step's own duration
            # is logged (below) rather than added here, since a bundle can't
            # describe its own not-yet-finished build time inside itself.
            data["generation_timings_seconds"] = timings

            prefix = report_storage_prefix(project_id, report_id)
            pdf_key = f"{prefix}/report.pdf"
            with storage.open_writer(pdf_key) as f:
                f.write(pdf_bytes)

            csv_key: str | None = None
            if csv_bytes is not None:
                csv_key = f"{prefix}/report.csv"
                with storage.open_writer(csv_key) as f:
                    f.write(csv_bytes)

            artifact_files = [
                (artifact_filename(a), storage.absolute_path(a.storage_key))
                for a in [*depth_artifacts, *disaster_artifacts]
            ]
            t0 = time.monotonic()
            bundle_bytes = build_bundle_zip(data, pdf_bytes, csv_bytes, artifact_files)
            logger.info("Report %s: build_bundle_zip took %.4fs", report_id, time.monotonic() - t0)
            bundle_key = f"{prefix}/bundle.zip"
            with storage.open_writer(bundle_key) as f:
                f.write(bundle_bytes)

            completed_at = datetime.now(UTC)
            completion = await db.execute(
                update(Report)
                .where(Report.id == report_id, Report.status == ReportStatus.GENERATING)
                .values(
                    report_metadata=data,
                    pdf_storage_key=pdf_key,
                    csv_storage_key=csv_key,
                    bundle_storage_key=bundle_key,
                    status=ReportStatus.COMPLETED,
                    completed_at=completed_at,
                    error_message=None,
                    updated_at=completed_at,
                )
                .returning(Report.id)
            )
            completed = completion.scalar_one_or_none()
            await db.commit()
            if completed is None:
                storage.delete_prefix(report_storage_prefix(project_id, report_id))
                logger.warning(
                    "Report %s reached a terminal state before completion; "
                    "discarded partial files.",
                    report_id,
                )
        except Exception as exc:
            logger.exception("Report generation failed for %s", report_id)
            await db.rollback()
            storage.delete_prefix(report_storage_prefix(project_id, report_id))
            error_message = (
                REPORT_TIMEOUT_ERROR_MESSAGE if isinstance(exc, JobTimeoutException) else str(exc)
            )
            await transition_active_report_to_failed(db, report_id, error_message)
