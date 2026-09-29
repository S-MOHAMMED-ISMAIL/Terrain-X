"""RQ task entrypoints, executed by the separate worker process/container.

RQ calls these synchronously (no running event loop in the worker's job
execution context), so each async implementation is driven via `asyncio.run`.
"""

import asyncio
import time
import uuid

from rq.timeouts import JobTimeoutException

from app.services.analysis_execution import execute_analysis_job
from app.services.report_execution import generate_report
from app.services.report_reconciliation import (
    REPORT_TIMEOUT_ERROR_MESSAGE,
    REPORT_WORKER_FAILURE_ERROR_MESSAGE,
    fail_report_job,
)


def ping(message: str = "pong") -> dict:
    """Proves the queue/worker infrastructure end-to-end (Phase 0); not part
    of the analysis pipeline."""
    return {"message": message, "processed_at": time.time()}


def run_analysis_job(job_id: str) -> None:
    asyncio.run(execute_analysis_job(uuid.UUID(job_id)))


def run_report_generation(report_id: str) -> None:
    asyncio.run(generate_report(uuid.UUID(report_id)))


def handle_report_generation_failure(
    job, _connection, exc_type, _exc_value, _traceback
) -> None:
    """RQ callback for failures that escape or terminate report execution."""
    if not job.args:
        return
    try:
        report_id = uuid.UUID(str(job.args[0]))
    except (TypeError, ValueError):
        return
    message = (
        REPORT_TIMEOUT_ERROR_MESSAGE
        if issubclass(exc_type, JobTimeoutException)
        else REPORT_WORKER_FAILURE_ERROR_MESSAGE
    )
    asyncio.run(fail_report_job(report_id, message))
