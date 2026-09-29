"""Phase 11: stale/crashed analysis-job reconciliation.

Problem this closes: if a worker process (or its container) crashes mid-job,
or RQ's own job_timeout (see Settings.ANALYSIS_JOB_TIMEOUT_SECONDS) kills the
work-horse process outright, neither path ever reaches this app's own
exception handling in analysis_execution.py — the job's row is left
`running` in Postgres forever, with no way for a user to retry (the
dataset-level active-job unique index keeps it "blocked") and no way for an
operator to tell it apart from a legitimately long-running job.

Design (deliberately minimal, no new column/migration):

`AnalysisJob.updated_at` already advances on EVERY real stage-transition
commit throughout the pipeline (depth, calibration, semantic, disaster —
see the many `job.current_stage = ...; await db.commit()` sites in
analysis_execution.py) via the column's own `onupdate=func.now()`. It is
already exactly the "worker heartbeat" a reconciliation sweep needs; no
separate heartbeat/timestamp column is required.

A job is reconciled only if BOTH:
  - its status is still `running` at the moment of the check (a job that
    has since completed/failed/been cancelled is never touched — the
    conditional UPDATE's WHERE clause excludes it), AND
  - `updated_at` is older than Settings.STALE_JOB_AFTER_SECONDS, a
    threshold set well above Settings.ANALYSIS_JOB_TIMEOUT_SECONDS (which
    already bounds how long a single real job can legitimately run) plus a
    grace margin — see config.py for the empirical rationale. This keeps
    false positives (reaping a genuinely active job) effectively
    impossible while still bounding recovery time to a small, documented
    window.

The reconciling UPDATE itself is a single atomic, conditional statement
(WHERE status='running' AND updated_at < threshold) — not a read-then-write
— so it cannot race with a concurrent completion, failure, or cancellation:
whichever commits first wins, and the loser's WHERE clause matches no rows.
"""

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.analysis_job import AnalysisJob, AnalysisJobStatus

logger = logging.getLogger("terrainx.backend.job_reconciliation")

STALE_JOB_ERROR_MESSAGE = (
    "This job's worker process stopped responding (crashed, was killed, or the "
    "job ran too long) and made no further progress. It did not complete and "
    "produced no valid completed result. Please retry the analysis."
)


async def reconcile_stale_running_jobs(db: AsyncSession) -> list[str]:
    """Reconciles any `running` job whose `updated_at` is older than
    Settings.STALE_JOB_AFTER_SECONDS to `failed`, with an honest
    error_message. Returns the list of reconciled job IDs (as strings), for
    logging/testing. Never touches a job that isn't currently `running`,
    never deletes any already-written artifact, never reports success.
    """
    settings = get_settings()
    threshold = datetime.now(UTC) - timedelta(seconds=settings.STALE_JOB_AFTER_SECONDS)

    result = await db.execute(
        update(AnalysisJob)
        .where(AnalysisJob.status == AnalysisJobStatus.RUNNING, AnalysisJob.updated_at < threshold)
        .values(
            status=AnalysisJobStatus.FAILED,
            error_message=STALE_JOB_ERROR_MESSAGE,
            completed_at=datetime.now(UTC),
        )
        .returning(AnalysisJob.id)
    )
    reconciled_ids = [str(row[0]) for row in result.all()]
    await db.commit()

    if reconciled_ids:
        logger.warning(
            "Reconciled %d stale 'running' analysis job(s) to 'failed' (no progress "
            "for over %ds): %s",
            len(reconciled_ids),
            settings.STALE_JOB_AFTER_SECONDS,
            reconciled_ids,
        )
    return reconciled_ids
