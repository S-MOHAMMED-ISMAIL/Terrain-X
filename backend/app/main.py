import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.exceptions import AppError
from app.core.logging import setup_logging
from app.db.session import AsyncSessionLocal
from app.services.job_reconciliation import reconcile_stale_running_jobs
from app.services.report_reconciliation import reconcile_stale_reports

settings = get_settings()
setup_logging(settings.LOG_LEVEL)
logger = logging.getLogger("terrainx.backend")


async def _stale_job_sweep_loop() -> None:
    """Periodic reconciliation of orphaned analysis jobs and reports.

    Runs inside the backend process so recovery does not depend on the RQ
    worker that may have disappeared.
    """
    interval = settings.STALE_JOB_SWEEP_INTERVAL_SECONDS
    while True:
        try:
            async with AsyncSessionLocal() as db:
                await reconcile_stale_running_jobs(db)
                await reconcile_stale_reports(db)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Stale work reconciliation sweep failed; will retry next interval")
        await asyncio.sleep(interval)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    sweep_task = asyncio.create_task(_stale_job_sweep_loop())
    try:
        yield
    finally:
        sweep_task.cancel()
        try:
            await sweep_task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="TERRAIN-X API", version="0.1.0", lifespan=_lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    logger.info("app_error", extra={"path": request.url.path, "method": request.method})
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": exc.code, "message": exc.message}},
    )


def _json_safe_validation_errors(exc: RequestValidationError) -> list[dict]:
    """Pydantic puts the raw exception instance in `ctx` for errors raised as
    a plain `raise ValueError(...)` inside a `@model_validator` (e.g.
    AnalysisParametersV1's at-most-one-reference check) — that instance isn't
    JSON serializable, so it must never be handed to JSONResponse as-is.
    """
    safe_errors = []
    for error in exc.errors():
        error = dict(error)
        ctx = error.get("ctx")
        if isinstance(ctx, dict):
            error["ctx"] = {
                key: str(value) if isinstance(value, BaseException) else value
                for key, value in ctx.items()
            }
        safe_errors.append(error)
    return safe_errors


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "error": {
                "code": "validation_error",
                "message": "Request validation failed",
                "details": _json_safe_validation_errors(exc),
            }
        },
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception(
        "unhandled_exception", extra={"path": request.url.path, "method": request.method}
    )
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"error": {"code": "internal_error", "message": "An unexpected error occurred"}},
    )


app.include_router(api_router, prefix="/api/v1")
