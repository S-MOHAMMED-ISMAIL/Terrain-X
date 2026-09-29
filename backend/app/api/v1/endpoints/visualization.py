"""Phase 5: real visualization context/preview/terrain endpoints, built
strictly from actual Phase 1-4 rows and stored raster artifacts — no mock
data, no fabricated CRS/coordinates/elevation. Every route enforces the full
ownership chain (project -> dataset, or project -> job -> artifact) via
`app/api/deps.py`, exactly like the existing dataset/analysis routes.
"""

import uuid

from fastapi import APIRouter, Depends, Query
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    get_current_user,
    get_owned_analysis_job,
    get_owned_artifact,
    get_owned_dataset,
)
from app.core.config import Settings, get_settings
from app.core.exceptions import NotFoundError, ValidationAppError
from app.core.storage import get_storage
from app.db.session import get_db
from app.models.analysis_artifact import AnalysisArtifact
from app.models.dataset import Dataset
from app.models.user import User
from app.schemas.visualization import (
    PixelValueOut,
    RasterMetadataOut,
    TerrainLocalCoordinateOut,
    TerrainMetadataOut,
    VisualizationContextOut,
)
from app.services import visualization as viz
from geospatial.exceptions import RasterValidationError
from storage import StorageBackend

# Mounted at /projects/{project_id}/datasets/{dataset_id}/visualization
dataset_router = APIRouter()
# Mounted at /projects/{project_id}/analysis/{job_id}/artifacts/{artifact_id}/visualization
artifact_router = APIRouter()


def _require_file(storage: StorageBackend, storage_key: str, label: str) -> None:
    if not storage.exists(storage_key):
        raise NotFoundError(f"Stored {label} file is missing")


@dataset_router.get("/context", response_model=VisualizationContextOut)
async def get_dataset_visualization_context(
    project_id: uuid.UUID,
    dataset_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> VisualizationContextOut:
    dataset = await get_owned_dataset(project_id, dataset_id, current_user, db)
    return await viz.build_visualization_context(db, dataset)


@dataset_router.get("/preview")
async def get_dataset_preview(
    project_id: uuid.UUID,
    dataset_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    storage: StorageBackend = Depends(get_storage),
    settings: Settings = Depends(get_settings),
) -> Response:
    dataset = await get_owned_dataset(project_id, dataset_id, current_user, db)
    if dataset.file_type in viz.NON_RASTER_FILE_TYPES:
        raise ValidationAppError("This dataset is not an image/raster and has no preview.")
    _require_file(storage, dataset.storage_key, "dataset")
    try:
        png_bytes = viz.get_dataset_preview(dataset, settings)
    except RasterValidationError as exc:
        raise ValidationAppError(str(exc)) from exc
    return Response(content=png_bytes, media_type="image/png")


@dataset_router.get("/map-preview")
async def get_dataset_map_preview(
    project_id: uuid.UUID,
    dataset_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    storage: StorageBackend = Depends(get_storage),
    settings: Settings = Depends(get_settings),
) -> Response:
    """P1-6: the dataset's image reprojected onto its EPSG:3857 overlay grid
    (placed at the RGB layer's `map_overlay_bounds`). Presentation only."""
    dataset = await get_owned_dataset(project_id, dataset_id, current_user, db)
    if dataset.file_type in viz.NON_RASTER_FILE_TYPES:
        raise ValidationAppError("This dataset is not an image/raster and has no preview.")
    _require_file(storage, dataset.storage_key, "dataset")
    try:
        png_bytes = viz.get_dataset_map_preview(dataset, settings)
    except RasterValidationError as exc:
        raise ValidationAppError(str(exc)) from exc
    return Response(content=png_bytes, media_type="image/png")


async def _get_owned_artifact_dep(
    project_id: uuid.UUID,
    job_id: uuid.UUID,
    artifact_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AnalysisArtifact:
    return await get_owned_artifact(project_id, job_id, artifact_id, current_user, db)


@artifact_router.get("/metadata", response_model=RasterMetadataOut)
async def get_artifact_raster_metadata(
    artifact: AnalysisArtifact = Depends(_get_owned_artifact_dep),
    storage: StorageBackend = Depends(get_storage),
) -> RasterMetadataOut:
    _require_file(storage, artifact.storage_key, "artifact")
    try:
        return viz.get_artifact_metadata(artifact)
    except RasterValidationError as exc:
        raise ValidationAppError(str(exc)) from exc


@artifact_router.get("/preview")
async def get_artifact_preview(
    artifact: AnalysisArtifact = Depends(_get_owned_artifact_dep),
    storage: StorageBackend = Depends(get_storage),
    settings: Settings = Depends(get_settings),
) -> Response:
    _require_file(storage, artifact.storage_key, "artifact")
    try:
        png_bytes = viz.get_artifact_preview(artifact, settings)
    except RasterValidationError as exc:
        raise ValidationAppError(str(exc)) from exc
    return Response(content=png_bytes, media_type="image/png")


@artifact_router.get("/map-preview")
async def get_artifact_map_preview(
    artifact: AnalysisArtifact = Depends(_get_owned_artifact_dep),
    storage: StorageBackend = Depends(get_storage),
    settings: Settings = Depends(get_settings),
) -> Response:
    """P1-6: the artifact reprojected onto its OWN EPSG:3857 overlay grid
    (placed at the layer's `map_overlay_bounds`). Presentation only — never
    a source of any measured/inspected value."""
    _require_file(storage, artifact.storage_key, "artifact")
    try:
        png_bytes = viz.get_artifact_map_preview(artifact, settings)
    except RasterValidationError as exc:
        raise ValidationAppError(str(exc)) from exc
    return Response(content=png_bytes, media_type="image/png")


@artifact_router.get("/window")
async def get_artifact_window(
    col_off: int = Query(..., ge=0),
    row_off: int = Query(..., ge=0),
    width: int = Query(..., gt=0),
    height: int = Query(..., gt=0),
    artifact: AnalysisArtifact = Depends(_get_owned_artifact_dep),
    storage: StorageBackend = Depends(get_storage),
    settings: Settings = Depends(get_settings),
) -> Response:
    _require_file(storage, artifact.storage_key, "artifact")
    try:
        png_bytes = viz.get_artifact_window(
            artifact, settings, col_off=col_off, row_off=row_off, width=width, height=height
        )
    except RasterValidationError as exc:
        raise ValidationAppError(str(exc)) from exc
    return Response(content=png_bytes, media_type="image/png")


@artifact_router.get("/value", response_model=PixelValueOut)
async def get_artifact_pixel_value(
    row: int = Query(..., ge=0),
    col: int = Query(..., ge=0),
    artifact: AnalysisArtifact = Depends(_get_owned_artifact_dep),
    storage: StorageBackend = Depends(get_storage),
) -> PixelValueOut:
    """Real cursor-inspection support: the actual stored value at one pixel
    (never a fabricated number) — see docs/ARCHITECTURE.md §3.5."""
    _require_file(storage, artifact.storage_key, "artifact")
    try:
        return viz.get_pixel_value(artifact, row=row, col=col)
    except RasterValidationError as exc:
        raise ValidationAppError(str(exc)) from exc


def _require_terrain_source(artifact: AnalysisArtifact) -> None:
    """Phase 10: a real 3D terrain mesh can be built from a calibrated `dsm`
    artifact OR — when no calibration has succeeded — the job's own
    `relative_depth` artifact (see viz.TERRAIN_SOURCE_ARTIFACT_TYPES and
    docs/ARCHITECTURE.md §3.10). No other artifact type is a real height
    field, so every other type is still rejected."""
    if artifact.artifact_type not in viz.TERRAIN_SOURCE_ARTIFACT_TYPES:
        raise ValidationAppError(
            "3D terrain can only be generated from a 'dsm' or 'relative_depth' artifact; "
            f"this artifact is '{artifact.artifact_type}'."
        )


@artifact_router.get("/terrain/metadata", response_model=TerrainMetadataOut)
async def get_terrain_metadata(
    artifact: AnalysisArtifact = Depends(_get_owned_artifact_dep),
    storage: StorageBackend = Depends(get_storage),
    settings: Settings = Depends(get_settings),
) -> TerrainMetadataOut:
    _require_terrain_source(artifact)
    _require_file(storage, artifact.storage_key, f"{artifact.artifact_type} artifact")
    try:
        metadata, _grid = viz.get_terrain(artifact, settings)
    except RasterValidationError as exc:
        raise ValidationAppError(str(exc)) from exc
    return metadata


@artifact_router.get("/terrain/local-coordinate", response_model=TerrainLocalCoordinateOut)
async def get_terrain_local_coordinate(
    lng: float = Query(...),
    lat: float = Query(...),
    artifact: AnalysisArtifact = Depends(_get_owned_artifact_dep),
    storage: StorageBackend = Depends(get_storage),
    settings: Settings = Depends(get_settings),
) -> TerrainLocalCoordinateOut:
    """P1-8: read-only conversion of a WGS84 point (a georeferenced 2D map
    click) into this terrain's own local 3D frame, for flythrough waypoints.
    422 for a non-terrain artifact, a non-georeferenced terrain, or an
    invalid coordinate."""
    _require_terrain_source(artifact)
    _require_file(storage, artifact.storage_key, f"{artifact.artifact_type} artifact")
    try:
        return viz.get_terrain_local_coordinate(artifact, settings, lng=lng, lat=lat)
    except RasterValidationError as exc:
        raise ValidationAppError(str(exc)) from exc


@artifact_router.get("/terrain/mesh.glb")
async def get_terrain_mesh_glb(
    project_id: uuid.UUID,
    job_id: uuid.UUID,
    resolution: int = Query(256),
    texture: bool = Query(True),
    artifact: AnalysisArtifact = Depends(_get_owned_artifact_dep),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    storage: StorageBackend = Depends(get_storage),
    settings: Settings = Depends(get_settings),
) -> Response:
    """P1-9: the terrain grid as a physical GLB mesh (cell-centre vertices,
    raw values, NoData as holes, optional embedded source texture, full
    CRS/semantics/provenance in asset.extras.terrainx). Built in memory in a
    worker thread; nothing is stored."""
    _require_terrain_source(artifact)
    _require_file(storage, artifact.storage_key, f"{artifact.artifact_type} artifact")
    if resolution not in settings.MESH_EXPORT_RESOLUTIONS:
        raise ValidationAppError(
            f"resolution must be one of {settings.MESH_EXPORT_RESOLUTIONS}, not {resolution}."
        )
    job = await get_owned_analysis_job(project_id, job_id, current_user, db)
    dataset = await db.get(Dataset, job.dataset_id)
    if dataset is not None and dataset.project_id != project_id:
        dataset = None
    try:
        data, extras = await run_in_threadpool(
            viz.export_terrain_mesh,
            artifact,
            job,
            dataset,
            settings,
            resolution=resolution,
            texture=texture,
        )
    except RasterValidationError as exc:
        raise ValidationAppError(str(exc)) from exc
    filename = f"terrainx-terrain-{artifact.id}-{resolution}.glb"
    return Response(
        content=data,
        media_type="model/gltf-binary",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-TerrainX-Texture": extras["texture"],
        },
    )


@artifact_router.get("/terrain/grid")
async def get_terrain_grid(
    artifact: AnalysisArtifact = Depends(_get_owned_artifact_dep),
    storage: StorageBackend = Depends(get_storage),
    settings: Settings = Depends(get_settings),
) -> Response:
    """Binary Float32 height grid, row-major, nodata/invalid cells encoded
    as NaN (never 0 — see geospatial/terrain_grid.py). Call
    `/terrain/metadata` first to get `width`/`height` and `height_kind` for
    interpreting this payload, and the real bounds/CRS/height-range context."""
    _require_terrain_source(artifact)
    _require_file(storage, artifact.storage_key, f"{artifact.artifact_type} artifact")
    try:
        _metadata, grid = viz.get_terrain(artifact, settings)
    except RasterValidationError as exc:
        raise ValidationAppError(str(exc)) from exc
    return Response(
        content=grid.elevations.astype("float32").tobytes(),
        media_type="application/octet-stream",
    )
