from fastapi import APIRouter

from app.api.v1.endpoints import (
    analysis,
    auth,
    datasets,
    health,
    measurements,
    projects,
    reports,
    visualization,
)

api_router = APIRouter()
api_router.include_router(health.router, tags=["health"])
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(projects.router, prefix="/projects", tags=["projects"])
api_router.include_router(
    datasets.router, prefix="/projects/{project_id}/datasets", tags=["datasets"]
)
api_router.include_router(
    analysis.creation_router,
    prefix="/projects/{project_id}/datasets/{dataset_id}/analysis",
    tags=["analysis"],
)
api_router.include_router(
    analysis.management_router,
    prefix="/projects/{project_id}/analysis",
    tags=["analysis"],
)
api_router.include_router(
    visualization.dataset_router,
    prefix="/projects/{project_id}/datasets/{dataset_id}/visualization",
    tags=["visualization"],
)
api_router.include_router(
    visualization.artifact_router,
    prefix="/projects/{project_id}/analysis/{job_id}/artifacts/{artifact_id}/visualization",
    tags=["visualization"],
)
api_router.include_router(
    measurements.artifact_router,
    prefix="/projects/{project_id}/analysis/{job_id}/artifacts/{artifact_id}/measurements",
    tags=["measurements"],
)
api_router.include_router(
    measurements.project_router,
    prefix="/projects/{project_id}/measurements",
    tags=["measurements"],
)
api_router.include_router(
    reports.creation_router,
    prefix="/projects/{project_id}/datasets/{dataset_id}/reports",
    tags=["reports"],
)
api_router.include_router(
    reports.management_router,
    prefix="/projects/{project_id}/reports",
    tags=["reports"],
)
