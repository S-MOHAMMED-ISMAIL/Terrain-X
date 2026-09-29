"""Phase 5: builds real visualization context/metadata from actual Phase 1-4
rows and artifacts. Never fabricates a layer, a CRS, or a statistic — every
field either comes straight from the database or is computed for real from
the stored raster file (geospatial/raster_preview.py, geospatial/terrain_grid.py).
"""

import dataclasses
import io
import math
import threading
import uuid
from pathlib import Path

import numpy as np
import rasterio
from PIL import Image
from rasterio.crs import CRS
from rasterio.enums import Resampling
from rasterio.errors import CRSError
from rasterio.warp import transform as warp_transform
from rasterio.warp import transform_bounds
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.storage import get_storage
from app.models.analysis_artifact import AnalysisArtifact
from app.models.analysis_job import (
    AnalysisJob,
    AnalysisJobStatus,
    CalibrationStatus,
    DisasterStatus,
    GroundFilterStatus,
    SemanticStatus,
)
from app.models.dataset import Dataset
from app.schemas.visualization import (
    BoundingBoxOut,
    CalibrationResidualsContextOut,
    DatasetContextOut,
    LayerContextOut,
    PixelValueOut,
    RasterMetadataOut,
    TerrainContextOut,
    TerrainLocalCoordinateOut,
    TerrainMetadataOut,
    VisualizationContextOut,
)
from geospatial.exceptions import RasterValidationError
from geospatial.map_overlay import generate_web_mercator_overlay_png, web_mercator_grid
from geospatial.mesh_export import build_terrain_mesh, write_glb
from geospatial.raster_preview import (
    compute_raster_statistics,
    generate_categorical_preview_png,
    generate_categorical_window_png,
    generate_preview_png,
    generate_window_png,
    sample_pixel_value,
)
from geospatial.sky_mask import detect_sky_mask
from geospatial.terrain_grid import (
    TerrainGrid,
    TextureCompatibility,
    extract_terrain_grid,
    rgb_texture_compatibility,
)

# P1-5: calibration residuals at sample locations — a GeoJSON point artifact,
# never a raster. Kept out of every raster code path (preview, pixel value,
# terrain, measurements, report raster statistics) and out of `layers`.
CALIBRATION_RESIDUALS_ARTIFACT_TYPE = "calibration_residuals"
GEOJSON_MIME_TYPE = "application/geo+json"
NON_RASTER_ARTIFACT_TYPES = {CALIBRATION_RESIDUALS_ARTIFACT_TYPE}


def is_raster_artifact(artifact: AnalysisArtifact) -> bool:
    return artifact.artifact_type not in NON_RASTER_ARTIFACT_TYPES


def artifact_filename(artifact: AnalysisArtifact) -> str:
    """Download/bundle filename, with the extension of what the stored file
    actually is."""
    extension = "geojson" if artifact.mime_type == GEOJSON_MIME_TYPE else "tif"
    return f"{artifact.artifact_type}.{extension}"


DISPLAY_NAMES = {
    "rgb": "Source Imagery (RGB)",
    "relative_depth": "Relative Depth (Uncalibrated)",
    "metric_elevation": "Metric Elevation",
    "dsm": "DSM",
    # P1-3: raster-filter ESTIMATES derived from the calibrated DSM — never
    # a measured bare-earth model or measured object heights (see
    # docs/ARCHITECTURE.md §3.12).
    "dtm": "DTM (estimated bare earth)",
    "ndsm": "nDSM (estimated height above ground)",
    # Phase 6: MobileSAM's output is deliberately never called "semantic
    # classification" anywhere a user sees it — see docs/ARCHITECTURE.md §3.6.
    "semantic_segmentation": "Distinct Surface Regions",
    # Phase 8: real terrain derivatives + hazard screening — see
    # docs/ARCHITECTURE.md §3.8.
    "slope": "Slope",
    "aspect": "Aspect",
    "hillshade": "Hillshade",
    "flood_screening": "Flood Screening",
    "landslide_screening": "Landslide Screening",
}

# Artifact types whose raster is a categorical region/class-ID map, not a
# continuous scientific field — these must always render with a
# deterministic per-ID palette (geospatial/raster_preview.py's
# `generate_categorical_preview_png`/`generate_categorical_window_png`),
# never the continuous min/max ramp used for depth/elevation/DSM/slope/aspect.
# flood_screening/landslide_screening have real, FIXED named classes (see
# LEGEND_ARTIFACT_TYPES below) — unlike semantic_segmentation's arbitrary
# region IDs, but the same categorical-rendering rule applies to both.
CATEGORICAL_ARTIFACT_TYPES = {"semantic_segmentation", "flood_screening", "landslide_screening"}

# Artifact types with a real, fixed, named class set — these get a real
# `legend` (value -> label) in their layer context/metadata, taken verbatim
# from the artifact's own persisted `class_labels`. semantic_segmentation is
# deliberately excluded: its region IDs are arbitrary per-image labels, not
# fixed named classes, so a "legend" would misrepresent them.
LEGEND_ARTIFACT_TYPES = {"flood_screening", "landslide_screening"}

# Non-raster dataset file types (see app/services/gcp_ingestion.py) that were
# never intended to be viewed as imagery — asking for a preview/context "rgb"
# layer on one of these is a real, distinct condition, not a raster read
# failure.
NON_RASTER_FILE_TYPES = {"csv"}


def _bbox_from_tuple(bounds: tuple[float, float, float, float] | None) -> BoundingBoxOut | None:
    if bounds is None:
        return None
    min_x, min_y, max_x, max_y = bounds
    return BoundingBoxOut(min_x=min_x, min_y=min_y, max_x=max_x, max_y=max_y)


def _dataset_bbox(dataset: Dataset) -> BoundingBoxOut | None:
    if None in (dataset.bbox_min_x, dataset.bbox_min_y, dataset.bbox_max_x, dataset.bbox_max_y):
        return None
    return BoundingBoxOut(
        min_x=dataset.bbox_min_x,
        min_y=dataset.bbox_min_y,
        max_x=dataset.bbox_max_x,
        max_y=dataset.bbox_max_y,
    )


def _dataset_bbox_wgs84(
    dataset: Dataset, native_bbox: BoundingBoxOut | None
) -> BoundingBoxOut | None:
    """Reprojects the dataset's real bounds to EPSG:4326 via rasterio/GDAL/
    PROJ (never an ad-hoc degree/meter approximation) purely so the frontend
    can place an image overlay on a standard lat/lon web basemap. Returns
    None for anything not georeferenced, or if the stored CRS string turns
    out to be unparseable (a real, if unlikely, data problem — never
    silently guessed at)."""
    if native_bbox is None or not dataset.crs:
        return None
    try:
        source_crs = CRS.from_user_input(dataset.crs)
        min_x, min_y, max_x, max_y = transform_bounds(
            source_crs,
            "EPSG:4326",
            native_bbox.min_x,
            native_bbox.min_y,
            native_bbox.max_x,
            native_bbox.max_y,
        )
    except (CRSError, ValueError):
        return None
    return BoundingBoxOut(min_x=min_x, min_y=min_y, max_x=max_x, max_y=max_y)


async def latest_completed_job(db: AsyncSession, dataset_id: uuid.UUID) -> AnalysisJob | None:
    """The dataset's most recently *completed* depth/calibration/semantic
    job — visualization reflects the current, real state of the dataset's
    latest finished analysis, not every historical job it has ever had.
    Excludes Phase 8 disaster-screening jobs: those are always standalone
    (never produce a relative_depth/metric_elevation/dsm/semantic_segmentation
    artifact of their own — see docs/ARCHITECTURE.md §3.8), so including one
    here would incorrectly hide a real, earlier depth-family result behind a
    disaster job that happens to be more recent. See
    `latest_completed_disaster_job` for the equivalent hazard-layer lookup.
    """
    result = await db.execute(
        select(AnalysisJob)
        .where(
            AnalysisJob.dataset_id == dataset_id,
            AnalysisJob.status == AnalysisJobStatus.COMPLETED,
            AnalysisJob.disaster_status == DisasterStatus.NOT_REQUESTED,
        )
        .order_by(AnalysisJob.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def latest_completed_disaster_job(
    db: AsyncSession, dataset_id: uuid.UUID
) -> AnalysisJob | None:
    """The dataset's most recently *completed* Phase 8 disaster-screening
    job, independent of `latest_completed_job` above — a dataset can have
    both a real depth-family job and a real, separately-run disaster job
    (screening an elevation artifact from any job in the same project, not
    necessarily this dataset's own latest one) — see
    docs/ARCHITECTURE.md §3.8."""
    result = await db.execute(
        select(AnalysisJob)
        .where(
            AnalysisJob.dataset_id == dataset_id,
            AnalysisJob.status == AnalysisJobStatus.COMPLETED,
            AnalysisJob.disaster_status == DisasterStatus.COMPLETED,
        )
        .order_by(AnalysisJob.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


def _map_overlay_bounds(path: Path, settings: Settings) -> BoundingBoxOut | None:
    """P1-6: the layer's own EPSG:3857 overlay-grid corners (header read
    only). None for a non-georeferenced raster or an unreadable one — the
    frontend then never places an overlay, and `/map-preview` reports the
    real error; nothing falls back to the old envelope stretch."""
    try:
        grid = web_mercator_grid(path, max_dimension=settings.PREVIEW_MAX_DIMENSION)
    except RasterValidationError:
        return None
    return _bbox_from_tuple(grid.bounds_wgs84)


def _rgb_layer(dataset: Dataset) -> LayerContextOut:
    if dataset.status.value != "valid":
        return LayerContextOut(
            layer_type="rgb",
            display_name=DISPLAY_NAMES["rgb"],
            available=False,
            unavailable_reason=f"Dataset status is '{dataset.status.value}', not 'valid'.",
        )
    if dataset.file_type in NON_RASTER_FILE_TYPES:
        return LayerContextOut(
            layer_type="rgb",
            display_name=DISPLAY_NAMES["rgb"],
            available=False,
            unavailable_reason="This dataset is not an image/raster (it's a GCP reference CSV).",
        )
    return LayerContextOut(
        layer_type="rgb",
        display_name=DISPLAY_NAMES["rgb"],
        available=True,
        width=dataset.width,
        height=dataset.height,
        dtype=None,  # not persisted on Dataset; real value is available via the metadata endpoint
        is_georeferenced=dataset.is_georeferenced,
        crs=dataset.crs,
        bounds=_dataset_bbox(dataset),
        map_overlay_bounds=(
            _map_overlay_bounds(dataset_raster_path(dataset), get_settings())
            if dataset.is_georeferenced
            else None
        ),
    )


def _build_legend(layer_type: str, metadata: dict) -> list[dict] | None:
    """Real class-value -> label pairs for a fixed-class categorical layer
    (flood_screening/landslide_screening), taken verbatim from the
    artifact's own persisted `class_labels` — never invented here. `None`
    for any other layer type, or if the artifact genuinely has no
    `class_labels` recorded."""
    if layer_type not in LEGEND_ARTIFACT_TYPES:
        return None
    class_labels = metadata.get("class_labels")
    if not class_labels:
        return None
    return [{"value": int(value), "label": label} for value, label in class_labels.items()]


def _artifact_layer(
    layer_type: str, job: AnalysisJob | None, artifact: AnalysisArtifact | None, reason: str
) -> LayerContextOut:
    if artifact is None:
        return LayerContextOut(
            layer_type=layer_type,
            display_name=DISPLAY_NAMES[layer_type],
            available=False,
            unavailable_reason=reason,
        )
    metadata = artifact.artifact_metadata or {}
    # relative_depth/metric_elevation/dsm/semantic_segmentation artifacts
    # persist width/height/crs directly in artifact_metadata (see
    # app/services/analysis_execution.py) but not bounds — left None here
    # rather than fabricated; the artifact's own /metadata endpoint computes
    # real bounds straight from the GeoTIFF.
    bbox = None
    # relative_depth's own metadata dict uses output_width/output_height
    # (it also records model input dimensions separately), while
    # metric_elevation/dsm/semantic_segmentation use plain width/height —
    # check both real keys rather than fabricating a value when the first
    # one is absent.
    width = metadata.get("width", metadata.get("output_width"))
    height = metadata.get("height", metadata.get("output_height"))
    is_categorical = layer_type in CATEGORICAL_ARTIFACT_TYPES

    min_value = max_value = None
    region_count = None
    if is_categorical:
        # A region-ID raster has no meaningful continuous min/max — never
        # compute one. `region_count` is a real, already-persisted fact
        # instead (see app/services/semantic_pipeline.py).
        region_count = metadata.get("region_count")
    else:
        value_key = {"relative_depth": "depth", "ndsm": "height_above_ground"}.get(
            layer_type, "elevation"
        )
        min_value = metadata.get(f"{value_key}_min")
        max_value = metadata.get(f"{value_key}_max")
        if min_value is None or max_value is None:
            # Not every artifact type persists its own min/max (e.g. Phase
            # 4's `dsm` artifact does not) — compute the real statistic on
            # demand from the actual stored file rather than leaving it
            # fabricated or borrowing another artifact's value.
            try:
                stats = compute_raster_statistics(artifact_raster_path(artifact))
                min_value = stats.min_value
                max_value = stats.max_value
            except RasterValidationError:
                pass  # leave as None; the dedicated /metadata endpoint surfaces the real error

    return LayerContextOut(
        layer_type=layer_type,
        display_name=DISPLAY_NAMES[layer_type],
        available=True,
        artifact_id=artifact.id,
        analysis_job_id=job.id if job else None,
        width=width,
        height=height,
        dtype=metadata.get("dtype"),
        is_georeferenced=metadata.get("is_georeferenced"),
        crs=metadata.get("crs"),
        bounds=bbox,
        min_value=min_value,
        max_value=max_value,
        is_categorical=is_categorical,
        region_count=region_count,
        legend=_build_legend(layer_type, metadata),
        map_overlay_bounds=_map_overlay_bounds(artifact_raster_path(artifact), get_settings()),
        notes=metadata.get("value_semantics")
        or metadata.get("limitations")
        or metadata.get("disclaimer"),
    )


def _ground_filter_unavailable_reason(job: AnalysisJob) -> str:
    """Why the P1-3 DTM/nDSM layers are missing — distinguishing a job whose
    calibration never produced a gate-passed DSM from one whose ground
    filtering itself failed."""
    if job.calibration_status != CalibrationStatus.CALIBRATED:
        if job.calibration_status == CalibrationStatus.FAILED:
            state = "calibration failed or was rejected by the calibration quality gate"
        elif job.calibration_status == CalibrationStatus.UNCALIBRATED:
            state = "this job was not calibrated"
        else:
            state = "calibration has not completed"
        return (
            f"Unavailable — ground filtering needs a calibrated DSM that passed the "
            f"calibration quality gate; {state}."
        )
    if job.ground_filter_status == GroundFilterStatus.FAILED:
        error = (job.ground_filter_metadata or {}).get("error")
        return (
            f"Unavailable — ground filtering failed: {error}"
            if error
            else "Unavailable — ground filtering failed."
        )
    if job.ground_filter_status == GroundFilterStatus.PROCESSING:
        return "Ground filtering is still processing."
    if job.ground_filter_status == GroundFilterStatus.NOT_REQUESTED:
        return (
            "Unavailable — this calibrated job predates ground filtering; re-run the "
            "analysis to produce the DTM/nDSM estimates."
        )
    return "Unavailable — the ground-filter artifact is missing despite a completed filter."


def _ground_filter_layers(job: AnalysisJob, artifacts: dict) -> list[LayerContextOut]:
    reason = _ground_filter_unavailable_reason(job)
    return [
        _artifact_layer("dtm", job, artifacts.get("dtm"), reason),
        _artifact_layer("ndsm", job, artifacts.get("ndsm"), reason),
    ]


async def build_visualization_context(
    db: AsyncSession, dataset: Dataset
) -> VisualizationContextOut:
    native_bbox = _dataset_bbox(dataset)
    dataset_out = DatasetContextOut(
        id=dataset.id,
        original_filename=dataset.original_filename,
        file_type=dataset.file_type,
        width=dataset.width,
        height=dataset.height,
        is_georeferenced=dataset.is_georeferenced,
        crs=dataset.crs,
        bounds=native_bbox,
        bounds_wgs84=_dataset_bbox_wgs84(dataset, native_bbox),
    )

    rgb_layer = _rgb_layer(dataset)

    disaster_job = await latest_completed_disaster_job(db, dataset.id)
    if disaster_job is None:
        no_disaster_reason = "No completed disaster-screening job for this dataset yet."
        slope_layer = _artifact_layer("slope", None, None, no_disaster_reason)
        aspect_layer = _artifact_layer("aspect", None, None, no_disaster_reason)
        hillshade_layer = _artifact_layer("hillshade", None, None, no_disaster_reason)
        flood_layer = _artifact_layer("flood_screening", None, None, no_disaster_reason)
        landslide_layer = _artifact_layer("landslide_screening", None, None, no_disaster_reason)
    else:
        disaster_result = await db.execute(
            select(AnalysisArtifact).where(AnalysisArtifact.analysis_job_id == disaster_job.id)
        )
        disaster_artifacts = {a.artifact_type: a for a in disaster_result.scalars().all()}
        no_flood_reason = "Flood screening was not requested for this job."
        no_landslide_reason = "Landslide screening was not requested for this job."
        slope_layer = _artifact_layer(
            "slope",
            disaster_job,
            disaster_artifacts.get("slope"),
            "This disaster job did not produce a slope artifact.",
        )
        aspect_layer = _artifact_layer(
            "aspect",
            disaster_job,
            disaster_artifacts.get("aspect"),
            "This disaster job did not produce an aspect artifact.",
        )
        hillshade_layer = _artifact_layer(
            "hillshade",
            disaster_job,
            disaster_artifacts.get("hillshade"),
            "This disaster job did not produce a hillshade artifact.",
        )
        flood_layer = _artifact_layer(
            "flood_screening",
            disaster_job,
            disaster_artifacts.get("flood_screening"),
            no_flood_reason,
        )
        landslide_layer = _artifact_layer(
            "landslide_screening",
            disaster_job,
            disaster_artifacts.get("landslide_screening"),
            no_landslide_reason,
        )
    hazard_layers = [slope_layer, aspect_layer, hillshade_layer, flood_layer, landslide_layer]

    job = await latest_completed_job(db, dataset.id)
    if job is None:
        no_job_reason = "No completed analysis job for this dataset yet."
        return VisualizationContextOut(
            dataset=dataset_out,
            layers=[
                rgb_layer,
                _artifact_layer("relative_depth", None, None, no_job_reason),
                _artifact_layer("metric_elevation", None, None, no_job_reason),
                _artifact_layer("dsm", None, None, no_job_reason),
                _artifact_layer("dtm", None, None, no_job_reason),
                _artifact_layer("ndsm", None, None, no_job_reason),
                _artifact_layer("semantic_segmentation", None, None, no_job_reason),
                *hazard_layers,
            ],
            terrain=TerrainContextOut(available=False, unavailable_reason=no_job_reason),
            calibration_residuals=_calibration_residuals_context(None, {}),
        )

    result = await db.execute(
        select(AnalysisArtifact).where(AnalysisArtifact.analysis_job_id == job.id)
    )
    artifacts = {a.artifact_type: a for a in result.scalars().all()}

    depth_artifact = artifacts.get("relative_depth")
    depth_layer = _artifact_layer(
        "relative_depth", job, depth_artifact, "This job did not produce a relative depth artifact."
    )

    if job.calibration_status == CalibrationStatus.CALIBRATED:
        metric_reason = "Metric elevation artifact is missing despite a calibrated job."
        dsm_reason = "DSM artifact is missing despite a calibrated job."
    elif job.calibration_status == CalibrationStatus.FAILED:
        error = (job.calibration_metadata or {}).get("error")
        metric_reason = dsm_reason = (
            f"Metric elevation unavailable — calibration failed: {error}"
            if error
            else "Metric elevation unavailable — calibration failed."
        )
    else:
        metric_reason = dsm_reason = (
            "Metric elevation unavailable — calibration has not completed successfully."
        )

    metric_layer = _artifact_layer(
        "metric_elevation", job, artifacts.get("metric_elevation"), metric_reason
    )
    dsm_artifact = artifacts.get("dsm")
    dsm_layer = _artifact_layer("dsm", job, dsm_artifact, dsm_reason)

    if dsm_artifact is not None:
        # Reuse dsm_layer's own width/height/min/max — it already applies the
        # same real-metadata-with-computed-fallback logic (see
        # `_artifact_layer`), so this stays consistent with what the layers
        # list itself reports for the DSM.
        terrain = TerrainContextOut(
            available=True,
            height_kind="elevation",
            source_artifact_type="dsm",
            artifact_id=dsm_artifact.id,
            analysis_job_id=job.id,
            **_texture_context_fields(dataset, dsm_artifact),
            width=dsm_layer.width,
            height=dsm_layer.height,
            is_georeferenced=dsm_layer.is_georeferenced,
            crs=dsm_layer.crs,
            min_elevation=dsm_layer.min_value,
            max_elevation=dsm_layer.max_value,
            min_height_value=dsm_layer.min_value,
            max_height_value=dsm_layer.max_value,
        )
    elif depth_artifact is not None:
        # Phase 10: no calibration has succeeded for this job (or none was
        # requested) — fall back to the job's own real relative_depth
        # artifact as a REAL, if unitless and uncalibrated, terrain source.
        # Never a fabricated DSM, never elevation semantics: `height_kind`
        # and the omitted `min_elevation`/`max_elevation` make this
        # structurally impossible to mistake for the calibrated case (see
        # TerrainContextOut's docstring).
        terrain = TerrainContextOut(
            available=True,
            height_kind="relative_depth",
            source_artifact_type="relative_depth",
            artifact_id=depth_artifact.id,
            analysis_job_id=job.id,
            **_texture_context_fields(dataset, depth_artifact),
            width=depth_layer.width,
            height=depth_layer.height,
            is_georeferenced=depth_layer.is_georeferenced,
            crs=depth_layer.crs,
            min_height_value=depth_layer.min_value,
            max_height_value=depth_layer.max_value,
        )
    else:
        terrain = TerrainContextOut(
            available=False, unavailable_reason=depth_layer.unavailable_reason or dsm_reason
        )

    if job.semantic_status == SemanticStatus.PROCESSING:
        semantic_reason = "Region segmentation is still processing."
    elif job.semantic_status == SemanticStatus.FAILED:
        error = (job.semantic_metadata or {}).get("error")
        semantic_reason = (
            f"Distinct Surface Regions unavailable — segmentation failed: {error}"
            if error
            else "Distinct Surface Regions unavailable — segmentation failed."
        )
    else:
        semantic_reason = "Region segmentation was not requested for this job."
    semantic_layer = _artifact_layer(
        "semantic_segmentation", job, artifacts.get("semantic_segmentation"), semantic_reason
    )

    return VisualizationContextOut(
        dataset=dataset_out,
        layers=[
            rgb_layer,
            depth_layer,
            metric_layer,
            dsm_layer,
            *_ground_filter_layers(job, artifacts),
            semantic_layer,
            *hazard_layers,
        ],
        terrain=terrain,
        calibration_residuals=_calibration_residuals_context(job, artifacts),
    )


def dataset_raster_path(dataset: Dataset) -> Path:
    return get_storage().absolute_path(dataset.storage_key)


def artifact_raster_path(artifact: AnalysisArtifact) -> Path:
    if not is_raster_artifact(artifact):
        raise RasterValidationError(
            f"Artifact type '{artifact.artifact_type}' is not a raster; it has no raster "
            "preview, pixel values, terrain or measurements."
        )
    return get_storage().absolute_path(artifact.storage_key)


def _calibration_residuals_context(
    job: AnalysisJob | None, artifacts: dict
) -> CalibrationResidualsContextOut:
    """P1-5: residuals exist only for a CALIBRATED (gate-passed) job
    produced after P1-5; every other state gets its real reason."""
    if job is None:
        return CalibrationResidualsContextOut(
            available=False, unavailable_reason="No completed analysis job for this dataset yet."
        )
    metadata = job.calibration_metadata or {}
    artifact = artifacts.get(CALIBRATION_RESIDUALS_ARTIFACT_TYPE)
    if job.calibration_status == CalibrationStatus.CALIBRATED:
        if artifact is not None:
            summary = artifact.artifact_metadata or {}
            return CalibrationResidualsContextOut(
                available=True,
                artifact_id=artifact.id,
                analysis_job_id=job.id,
                reference_type=summary.get("reference_type"),
                sample_count=summary.get("sample_count"),
            )
        reason = metadata.get("calibration_residuals_error") or (
            "This job was calibrated before calibration residual diagnostics existed; "
            "re-run the analysis to produce them."
        )
    elif job.calibration_status == CalibrationStatus.FAILED:
        reason = (
            "Calibration failed or was rejected by the calibration quality gate, so there is "
            "no calibrated surface to compare with the reference. See the job's calibration "
            "diagnostics."
        )
    else:
        reason = "No calibration reference (DEM/GCP) was used for this analysis job."
    return CalibrationResidualsContextOut(
        available=False, analysis_job_id=job.id, unavailable_reason=reason
    )


def get_artifact_metadata(artifact: AnalysisArtifact) -> RasterMetadataOut:
    stats = compute_raster_statistics(artifact_raster_path(artifact))
    artifact_metadata = artifact.artifact_metadata or {}
    is_categorical = artifact.artifact_type in CATEGORICAL_ARTIFACT_TYPES
    return RasterMetadataOut(
        artifact_id=artifact.id,
        artifact_type=artifact.artifact_type,
        display_name=DISPLAY_NAMES.get(artifact.artifact_type, artifact.artifact_type),
        driver=stats.driver,
        width=stats.width,
        height=stats.height,
        count=stats.count,
        dtype=stats.dtype,
        nodata=stats.nodata,
        is_georeferenced=stats.is_georeferenced,
        crs=stats.crs,
        bounds=_bbox_from_tuple(stats.bounds),
        resolution_x=stats.resolution[0] if stats.resolution else None,
        resolution_y=stats.resolution[1] if stats.resolution else None,
        # A categorical raster's real min/max ID values aren't a meaningful
        # "range" for display — omit rather than let a client mistake them
        # for a continuous scale.
        min_value=None if is_categorical else stats.min_value,
        max_value=None if is_categorical else stats.max_value,
        has_finite_data=stats.has_finite_data,
        stats_sampled=stats.stats_sampled,
        is_categorical=is_categorical,
        region_count=artifact_metadata.get("region_count") if is_categorical else None,
        legend=_build_legend(artifact.artifact_type, artifact_metadata),
        notes=(
            artifact_metadata.get("value_semantics")
            or artifact_metadata.get("limitations")
            or artifact_metadata.get("disclaimer")
        ),
    )


def get_artifact_preview(artifact: AnalysisArtifact, settings: Settings) -> bytes:
    if artifact.artifact_type in CATEGORICAL_ARTIFACT_TYPES:
        return generate_categorical_preview_png(
            artifact_raster_path(artifact), max_dimension=settings.PREVIEW_MAX_DIMENSION
        )
    return generate_preview_png(
        artifact_raster_path(artifact), max_dimension=settings.PREVIEW_MAX_DIMENSION
    )


def get_artifact_window(
    artifact: AnalysisArtifact,
    settings: Settings,
    *,
    col_off: int,
    row_off: int,
    width: int,
    height: int,
) -> bytes:
    if artifact.artifact_type in CATEGORICAL_ARTIFACT_TYPES:
        return generate_categorical_window_png(
            artifact_raster_path(artifact),
            col_off=col_off,
            row_off=row_off,
            width=width,
            height=height,
            max_dimension=settings.PREVIEW_MAX_DIMENSION,
        )
    return generate_window_png(
        artifact_raster_path(artifact),
        col_off=col_off,
        row_off=row_off,
        width=width,
        height=height,
        max_dimension=settings.PREVIEW_MAX_DIMENSION,
    )


def get_pixel_value(artifact: AnalysisArtifact, *, row: int, col: int) -> PixelValueOut:
    """Real cursor-inspection support (Part 25): the exact stored value at
    one pixel, or None for out-of-bounds/NoData/NaN/Inf — never a fabricated
    number standing in for "no data here"."""
    value = sample_pixel_value(artifact_raster_path(artifact), row=row, col=col)
    return PixelValueOut(row=row, col=col, value=value)


def get_artifact_map_preview(artifact: AnalysisArtifact, settings: Settings) -> bytes:
    """P1-6: presentation-only overlay reprojected to EPSG:3857 (see
    geospatial/map_overlay.py). Never a source of any analytical value."""
    png, _grid = generate_web_mercator_overlay_png(
        artifact_raster_path(artifact),
        max_dimension=settings.PREVIEW_MAX_DIMENSION,
        categorical=artifact.artifact_type in CATEGORICAL_ARTIFACT_TYPES,
    )
    return png


def get_dataset_map_preview(dataset: Dataset, settings: Settings) -> bytes:
    png, _grid = generate_web_mercator_overlay_png(
        dataset_raster_path(dataset),
        max_dimension=settings.PREVIEW_MAX_DIMENSION,
        categorical=False,
    )
    return png


def get_dataset_preview(dataset: Dataset, settings: Settings) -> bytes:
    return generate_preview_png(
        dataset_raster_path(dataset), max_dimension=settings.PREVIEW_MAX_DIMENSION
    )


# Phase 10: a real 3D terrain mesh can now come from either a calibrated
# `dsm` artifact (real elevation) or — when no calibration has succeeded —
# the job's own `relative_depth` artifact (real, but unitless, relative
# height). `geospatial/terrain_grid.py::extract_terrain_grid` was already
# fully generic (reads band 1 of whatever raster path it's given); this set
# is what the API/service layer now accepts as a valid terrain source,
# never any other artifact type (never semantic_segmentation, never a
# hazard raster — none of those are real height fields).
#
# Deliberately does NOT include "remote_sensing_height" yet: that artifact
# type has no producer anywhere in this codebase (see
# ai/rs_height_estimator.py — an interface with no concrete implementation),
# so there is nothing real to accept as a terrain source for it. Widening
# this set is real future work for whenever a concrete, validated
# remote-sensing height estimator exists — see
# docs/ARCHITECTURE_NOTE_RS_HEIGHT.md — not something to pre-wire against an
# artifact type nothing can currently produce.
TERRAIN_SOURCE_ARTIFACT_TYPES = {"dsm", "relative_depth"}


def height_kind_for_artifact_type(artifact_type: str) -> str:
    """Real, structural classification of what an artifact's values mean as
    terrain height — never inferred from anything but the artifact's own
    real, persisted type. See TerrainContextOut's docstring.

    `"remote_sensing_height"` is classified correctly here (as itself, not
    silently folded into `"relative_depth"`) even though no artifact of
    that type is ever produced today — this keeps the classification
    function itself correct/future-proof independent of when
    TERRAIN_SOURCE_ARTIFACT_TYPES above is eventually widened."""
    if artifact_type == "dsm":
        return "elevation"
    if artifact_type == "remote_sensing_height":
        return "remote_sensing_height"
    return "relative_depth"


def _terrain_grid_to_metadata(
    artifact_id: uuid.UUID, artifact_type: str, grid: TerrainGrid
) -> TerrainMetadataOut:
    height_kind = height_kind_for_artifact_type(artifact_type)
    is_elevation = height_kind == "elevation"
    return TerrainMetadataOut(
        artifact_id=artifact_id,
        height_kind=height_kind,
        source_artifact_type=artifact_type,
        width=grid.width,
        height=grid.height,
        source_width=grid.source_width,
        source_height=grid.source_height,
        nodata_present=grid.nodata_present,
        is_georeferenced=grid.is_georeferenced,
        crs=grid.crs,
        local_crs=grid.local_crs,
        origin_x=grid.origin_x,
        origin_y=grid.origin_y,
        cell_size_x=grid.cell_size_x,
        cell_size_y=grid.cell_size_y,
        bounds=_bbox_from_tuple(grid.bounds),
        min_elevation=grid.min_elevation if is_elevation else None,
        max_elevation=grid.max_elevation if is_elevation else None,
        min_height_value=grid.min_elevation,
        max_height_value=grid.max_elevation,
    )


def _apply_sky_mask(grid: TerrainGrid) -> TerrainGrid:
    """UNCALIBRATED relative-depth terrain only (see get_terrain below —
    never called for a DSM/metric-elevation grid). Depth Anything still
    predicts a real depth value for sky pixels (physically pushed to the
    lowest values in the image — see geospatial/sky_mask.py's docstring for
    the full reasoning), which is not a real terrain sample. Masks those
    cells to NaN using the grid's own existing nodata/invalid convention —
    no new "sky" concept for downstream code to special-case, and every
    real terrain value is passed through completely unmodified."""
    sky = detect_sky_mask(grid.elevations)
    if not sky.any():
        return grid
    masked = np.where(sky, np.nan, grid.elevations).astype("float32")
    valid = np.isfinite(masked)
    has_finite = bool(valid.any())
    return dataclasses.replace(
        grid,
        elevations=masked,
        min_elevation=float(masked[valid].min()) if has_finite else None,
        max_elevation=float(masked[valid].max()) if has_finite else None,
        nodata_present=True,
    )


def get_terrain_local_coordinate(
    artifact: AnalysisArtifact, settings: Settings, *, lng: float, lat: float
) -> TerrainLocalCoordinateOut:
    """P1-8: (lng, lat) in EPSG:4326 -> the terrain's own local frame. The
    local CRS and origin come from the very same `extract_terrain_grid` call
    the 3D terrain is built from (via `get_terrain`), never re-derived here."""
    if not (
        math.isfinite(lng)
        and math.isfinite(lat)
        and -180.0 <= lng <= 180.0
        and -90.0 <= lat <= 90.0
    ):
        raise RasterValidationError(f"({lng}, {lat}) is not a valid WGS84 longitude/latitude.")
    _metadata, grid = get_terrain(artifact, settings)
    if not grid.is_georeferenced or grid.local_crs is None or grid.origin_x is None:
        raise RasterValidationError(
            "This terrain is not georeferenced, so a geographic coordinate has no place in "
            "it; it uses local pixel coordinates instead."
        )
    xs, ys = warp_transform("EPSG:4326", grid.local_crs, [lng], [lat])
    map_x, map_y = float(xs[0]), float(ys[0])
    col = (map_x - grid.origin_x) / grid.cell_size_x
    row = (map_y - grid.origin_y) / grid.cell_size_y
    # D1: the 3D mesh's vertices are the grid cell centres (col, row + 0.5 in
    # these continuous grid coordinates), so its surface spans [0.5, n - 0.5].
    in_footprint = 0.5 <= col <= grid.width - 0.5 and 0.5 <= row <= grid.height - 0.5
    return TerrainLocalCoordinateOut(
        local_crs=grid.local_crs, map_x=map_x, map_y=map_y, in_footprint=in_footprint
    )


# P1-9: bounds concurrent mesh exports (each holds grid + mesh + texture in
# memory); a request beyond the limit waits in its worker thread.
_mesh_export_slots: threading.BoundedSemaphore | None = None
_mesh_export_slots_lock = threading.Lock()


def _mesh_export_semaphore(settings: Settings) -> threading.BoundedSemaphore:
    global _mesh_export_slots
    with _mesh_export_slots_lock:
        if _mesh_export_slots is None:
            _mesh_export_slots = threading.BoundedSemaphore(settings.MESH_EXPORT_MAX_CONCURRENT)
        return _mesh_export_slots


_DSM_MESH_SEMANTICS = (
    "Calibrated monocular surface elevation (DSM) exactly as stored in the terrain grid, in "
    "the calibration reference's own vertical units (not independently verified). A "
    "calibrated version of whatever surface the depth model saw — not a bare-earth model "
    "and not survey-grade."
)
_RELATIVE_MESH_SEMANTICS = (
    "Raw relative depth (unitless) exactly as stored in the terrain grid. Not a physical "
    "height: horizontal and vertical units are not comparable, and no display scaling "
    "or gamma is applied."
)


def _texture_context_fields(dataset: Dataset, terrain_artifact: AnalysisArtifact) -> dict:
    """D2: the terrain context's texture fields, from the shared rule."""
    compatibility = texture_compatibility(dataset, terrain_artifact)
    return {
        "texture_compatible": compatibility.compatible,
        "texture_unavailable_code": compatibility.code,
        "texture_unavailable_reason": compatibility.reason,
    }


def texture_compatibility(
    dataset: Dataset | None, terrain_artifact: AnalysisArtifact
) -> TextureCompatibility:
    """D2: whether the dataset's RGB image may be draped on this terrain —
    the single rule shared by the browser 3D view (visualization context)
    and the GLB export (see geospatial.terrain_grid.rgb_texture_compatibility)."""
    source = (
        None
        if dataset is None or dataset.file_type in NON_RASTER_FILE_TYPES
        else dataset_raster_path(dataset)
    )
    return rgb_texture_compatibility(source, artifact_raster_path(terrain_artifact))


def _mesh_texture_png(
    dataset: Dataset | None, terrain_artifact: AnalysisArtifact, settings: Settings
) -> tuple[bytes | None, str | None]:
    """The source RGB image as an embedded texture — only when it aligns
    with the terrain grid pixel-for-pixel; otherwise (None, reason)."""
    compatibility = texture_compatibility(dataset, terrain_artifact)
    if not compatibility.compatible:
        return None, compatibility.reason
    try:
        with rasterio.open(dataset_raster_path(dataset)) as src:
            longest = max(src.width, src.height)
            scale = min(1.0, settings.MESH_EXPORT_MAX_TEXTURE_DIMENSION / longest)
            out = (3, max(1, round(src.height * scale)), max(1, round(src.width * scale)))
            rgb = src.read([1, 2, 3], out_shape=out, resampling=Resampling.average)
    except Exception as exc:  # noqa: BLE001 — a real read failure becomes the reason
        return None, f"The source image could not be read: {exc}"
    buffer = io.BytesIO()
    Image.fromarray(np.transpose(rgb, (1, 2, 0)), mode="RGB").save(buffer, format="PNG")
    return buffer.getvalue(), None


def export_terrain_mesh(
    artifact: AnalysisArtifact,
    job: AnalysisJob,
    dataset: Dataset | None,
    settings: Settings,
    *,
    resolution: int,
    texture: bool,
) -> tuple[bytes, dict]:
    """P1-9: physical GLB of the terrain grid (see geospatial/mesh_export.py)
    built from the same extract_terrain_grid (+ sky mask for relative depth)
    the 3D view uses, at `resolution` (256 or 512). Nothing is written to
    disk. Raises RasterValidationError for an unusable grid."""
    if resolution not in settings.MESH_EXPORT_RESOLUTIONS:
        raise RasterValidationError(
            f"resolution must be one of {settings.MESH_EXPORT_RESOLUTIONS}, not {resolution}."
        )
    with _mesh_export_semaphore(settings):
        grid = extract_terrain_grid(artifact_raster_path(artifact), max_dimension=resolution)
        height_kind = height_kind_for_artifact_type(artifact.artifact_type)
        sky_masked = height_kind == "relative_depth"
        if sky_masked:
            grid = _apply_sky_mask(grid)
        cell_x = grid.cell_size_x if grid.cell_size_x is not None else 1.0
        cell_y = grid.cell_size_y if grid.cell_size_y is not None else 1.0
        if grid.is_georeferenced:
            # map_x = origin_x + x ; map_y = origin_y - z  (origin = cell (0,0) centre)
            step_x, step_z = cell_x, -cell_y
            origin_x = grid.origin_x + 0.5 * cell_x
            origin_y = grid.origin_y + 0.5 * cell_y
        else:
            # Source-pixel units: pixel_col = origin_x + x ; pixel_row = origin_y + z
            step_x = grid.source_width / grid.width
            step_z = grid.source_height / grid.height
            origin_x, origin_y = 0.5 * step_x, 0.5 * step_z
        try:
            mesh = build_terrain_mesh(grid.elevations, step_x=step_x, step_z=step_z)
        except ValueError as exc:
            raise RasterValidationError(str(exc)) from exc

        png, omitted = (None, "Texture not requested.")
        if texture:
            png, omitted = _mesh_texture_png(dataset, artifact, settings)

        is_elevation = height_kind == "elevation"
        local_crs = grid.local_crs if grid.is_georeferenced else None
        crs_obj = CRS.from_user_input(local_crs) if local_crs else None
        quality_gate = (job.calibration_metadata or {}).get("quality_gate") or {}
        extras = {
            "format": "terrainx-terrain-mesh",
            "version": 1,
            "representation": (
                "Physical terrain-grid export: vertices at grid cell centres with the raw "
                "grid values. A different representation from the browser 3D view, which is "
                "a display/flythrough mesh (the same cell centres, display scaling, centred)."
            ),
            "height_kind": height_kind,
            "vertical_units": (
                "units of the calibration reference (not independently verified)"
                if is_elevation
                else "unitless"
            ),
            "vertical_semantics": _DSM_MESH_SEMANTICS if is_elevation else _RELATIVE_MESH_SEMANTICS,
            "physical_height": is_elevation,
            "horizontal_vertical_units_comparable": False if not is_elevation else None,
            "display_exaggeration_applied": False,
            "display_gamma_applied": False,
            "georeferenced": grid.is_georeferenced,
            "source_crs": grid.crs,
            "local_crs": local_crs,
            "local_crs_wkt": crs_obj.to_wkt() if crs_obj else None,
            "horizontal_units": (
                f"{crs_obj.linear_units} (units of {local_crs})"
                if crs_obj
                else "source image pixels"
            ),
            "origin_x": origin_x,
            "origin_y": origin_y,
            "origin_is": "centre of grid cell (0, 0)",
            "cell_size_x": cell_x if grid.is_georeferenced else step_x,
            "cell_size_y": cell_y if grid.is_georeferenced else step_z,
            "axis_mapping": (
                {"map_x": "origin_x + x", "map_y": "origin_y - z", "value": "y"}
                if grid.is_georeferenced
                else {"pixel_col": "origin_x + x", "pixel_row": "origin_y + z", "value": "y"}
            ),
            "grid_width": grid.width,
            "grid_height": grid.height,
            "source_width": grid.source_width,
            "source_height": grid.source_height,
            "resolution_requested": resolution,
            "decimated": (grid.width, grid.height) != (grid.source_width, grid.source_height),
            "resampling": "block average (decimated read)",
            "reprojected": bool(grid.is_georeferenced and grid.local_crs != grid.crs),
            "sky_mask_applied": sky_masked,
            "nodata": "cells without a value have no vertices and no triangles",
            "vertex_count": mesh.vertex_count,
            "triangle_count": mesh.triangle_count,
            "texture": "embedded" if png is not None else "omitted",
            "texture_omitted_reason": None if png is not None else omitted,
            "artifact_id": str(artifact.id),
            "artifact_type": artifact.artifact_type,
            "analysis_job_id": str(job.id),
            "dataset_id": str(job.dataset_id),
            "calibration_status": job.calibration_status.value,
            "calibration_quality_gate_passed": quality_gate.get("passed"),
        }
        return write_glb(mesh, extras=extras, texture_png=png), extras


def get_terrain(
    artifact: AnalysisArtifact, settings: Settings
) -> tuple[TerrainMetadataOut, TerrainGrid]:
    grid = extract_terrain_grid(
        artifact_raster_path(artifact), max_dimension=settings.MAX_TERRAIN_DIMENSION
    )
    if height_kind_for_artifact_type(artifact.artifact_type) == "relative_depth":
        grid = _apply_sky_mask(grid)
    return _terrain_grid_to_metadata(artifact.id, artifact.artifact_type, grid), grid
