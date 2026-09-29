"""The real analysis job execution logic, run by the RQ worker process.

Phase 2 established generic job orchestration (verifying the dataset row,
stored file, and structural metadata are all still consistent). Phase 3
extends the same job with real monocular depth estimation: loading a
pretrained model, running real inference over the dataset's actual pixel
data, and writing a real relative-depth raster artifact. Nothing here
fabricates a terrain/DSM/elevation result — the output is explicitly
relative, uncalibrated depth (see ai/depth_anything.py and
docs/ARCHITECTURE.md for the exact value semantics and limitations).
"""

import json
import logging
import uuid
from datetime import UTC, datetime

import numpy as np
import rasterio
from PIL import Image as PILImage
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.exc import StaleDataError

from ai.exceptions import InferenceError, ModelLoadError
from ai.exceptions import UnsupportedInputError as AIUnsupportedInputError
from ai.registry import get_depth_estimator
from app.core.config import Settings, get_settings
from app.core.storage import get_storage
from app.db.session import AsyncSessionLocal
from app.models.analysis_artifact import AnalysisArtifact
from app.models.analysis_job import (
    AnalysisJob,
    AnalysisJobStatus,
    AnalysisStage,
    CalibrationStatus,
    DisasterStatus,
    GroundFilterStatus,
    SemanticStatus,
)
from app.models.dataset import Dataset, DatasetStatus
from app.schemas.analysis import AnalysisParametersV1
from app.services.calibration_pipeline import run_calibration
from app.services.depth_pipeline import (
    RELATIVE_DEPTH_VALUE_SEMANTICS,
    UnsupportedDepthInputError,
    extract_rgb_uint8,
)
from app.services.disaster_pipeline import (
    ELEVATION_ARTIFACT_TYPES,
    NOT_ELEVATION_ERROR,
    run_disaster_screening,
)
from app.services.ground_filter_pipeline import (
    DTM_VALUE_SEMANTICS,
    GROUND_FILTER_LIMITATIONS,
    NDSM_VALUE_SEMANTICS,
    run_ground_filter,
)
from app.services.semantic_pipeline import run_semantic_segmentation
from app.services.visualization import CALIBRATION_RESIDUALS_ARTIFACT_TYPE, GEOJSON_MIME_TYPE
from geospatial.exceptions import DisasterAnalysisError, RasterValidationError
from geospatial.ground_filter import GROUND_FILTER_NODATA
from geospatial.image_quality import compute_image_quality
from geospatial.raster_io import (
    read_raster_array,
    write_single_band_categorical,
    write_single_band_float32,
)
from geospatial.raster_metadata import extract_raster_metadata
from geospatial.terrain_derivatives import (
    DEFAULT_HILLSHADE_ALTITUDE_DEG,
    DEFAULT_HILLSHADE_AZIMUTH_DEG,
    ELEVATION_ANALYSIS_NODATA,
    FLOOD_NODATA,
    LANDSLIDE_NODATA,
    LandslideThresholds,
    crs_to_string,
)
from geospatial.vertical_units import VerticalUnitResolution

logger = logging.getLogger("terrainx.backend.worker")


class AnalysisExecutionError(Exception):
    """A real, expected failure discovered while preparing/validating/
    running a job (missing file, corrupt dataset, unsupported input,
    schema drift, model load/inference failure, ...).

    The message is safe to show to the end user as-is. An exception of any
    other type is still caught by the caller, but is logged with a full
    traceback server-side and reported to the user with a generic message —
    never a raw stack trace.
    """


class _JobCancelledMidExecution(Exception):
    """Internal signal only: a defensive status re-check found the job was
    cancelled after being claimed. Not a failure — the job's status is
    already 'cancelled' (set by whoever cancelled it); the caller simply
    stops touching it, exactly like the pre-claim cancellation check."""


def _write_ground_filter_raster(
    path, array: np.ndarray, *, crs, transform, band_unit: str | None = None
) -> int:
    """Writes one DTM/nDSM GeoTIFF on the DSM's own grid (float32, NoData
    -9999) and re-opens it to confirm shape/dtype/NoData/CRS/transform —
    the same post-write re-verification Phase 4 applies to the DSM."""
    write_single_band_float32(
        path, array, crs=crs, transform=transform, nodata=GROUND_FILTER_NODATA, band_unit=band_unit
    )
    with rasterio.open(path) as written:
        if (
            written.count != 1
            or written.dtypes[0] != "float32"
            or (written.height, written.width) != array.shape
            or written.nodata != GROUND_FILTER_NODATA
            or written.crs != crs
            or written.transform != transform
        ):
            raise AnalysisExecutionError("post-write check failed (shape/dtype/NoData/georef).")
    return path.stat().st_size


async def _run_ground_filter_stage(
    db: AsyncSession,
    job: AnalysisJob,
    *,
    dsm: np.ndarray,
    crs,
    transform,
    crs_string: str | None,
    provenance: dict,
    settings: Settings,
    vertical_unit: VerticalUnitResolution | None,
) -> None:
    """P1-3: FILTERING_GROUND -> WRITING_DTM -> WRITING_NDSM for a job whose
    calibration passed the quality gate. Soft failure throughout: any
    filtering or writing failure sets ground_filter_status=FAILED with the
    real reason, removes partial files, persists no DTM/nDSM artifact rows,
    and returns normally so the job still completes with its DSM intact.
    Cancellation checkpoints still propagate."""
    storage = get_storage()
    await _raise_if_cancelled(db, job)
    job.current_stage = AnalysisStage.FILTERING_GROUND
    job.ground_filter_status = GroundFilterStatus.PROCESSING
    await db.commit()

    outcome = run_ground_filter(
        dsm, crs=crs, transform=transform, settings=settings, vertical_unit=vertical_unit
    )
    if outcome.status != GroundFilterStatus.COMPLETED:
        job.ground_filter_status = outcome.status
        job.ground_filter_metadata = outcome.metadata
        await db.commit()
        return

    await _raise_if_cancelled(db, job)
    result = outcome.result
    base_key = f"projects/{job.project_id}/analysis/{job.id}"
    dtm_key, ndsm_key = f"{base_key}/dtm.tif", f"{base_key}/ndsm.tif"
    try:
        job.current_stage = AnalysisStage.WRITING_DTM
        await db.commit()
        band_unit = result.vertical_unit.name
        dtm_size = _write_ground_filter_raster(
            storage.absolute_path(dtm_key),
            result.dtm,
            crs=crs,
            transform=transform,
            band_unit=band_unit,
        )
        job.current_stage = AnalysisStage.WRITING_NDSM
        await db.commit()
        ndsm_size = _write_ground_filter_raster(
            storage.absolute_path(ndsm_key),
            result.ndsm,
            crs=crs,
            transform=transform,
            band_unit=band_unit,
        )
    except Exception as exc:
        storage.delete(dtm_key)
        storage.delete(ndsm_key)
        job.ground_filter_status = GroundFilterStatus.FAILED
        job.ground_filter_metadata = {
            "error": f"Could not write the DTM/nDSM artifacts: {exc}",
            "policy": outcome.metadata.get("policy"),
        }
        await db.commit()
        return

    stats = result.statistics
    height, width = result.dtm.shape
    common = {
        **provenance,
        "width": width,
        "height": height,
        "crs": crs_string,
        "is_georeferenced": True,
        "dtype": "float32",
        "nodata": GROUND_FILTER_NODATA,
        "method": outcome.metadata["method"],
        "ground_filter_policy": outcome.metadata["policy"],
        "limitations": GROUND_FILTER_LIMITATIONS,
        # D3: unit provenance (thresholds/windows in metres, values in the
        # DSM's declared unit; the DSM itself is not modified).
        "vertical_unit": outcome.metadata["vertical_unit"],
        "unit_provenance": outcome.metadata["unit_provenance"],
    }
    dtm_id, ndsm_id = uuid.uuid4(), uuid.uuid4()
    db.add(
        AnalysisArtifact(
            id=dtm_id,
            analysis_job_id=job.id,
            artifact_type="dtm",
            storage_key=dtm_key,
            mime_type="image/tiff",
            file_size_bytes=dtm_size,
            artifact_metadata={
                **common,
                "elevation_min": stats.dtm_min,
                "elevation_max": stats.dtm_max,
                "elevation_mean": stats.dtm_mean,
                "ground_fraction": stats.ground_fraction,
                "value_semantics": DTM_VALUE_SEMANTICS,
            },
        )
    )
    db.add(
        AnalysisArtifact(
            id=ndsm_id,
            analysis_job_id=job.id,
            artifact_type="ndsm",
            storage_key=ndsm_key,
            mime_type="image/tiff",
            file_size_bytes=ndsm_size,
            artifact_metadata={
                **common,
                "dtm_artifact_id": str(dtm_id),
                "height_above_ground_min": stats.ndsm_min,
                "height_above_ground_max": stats.ndsm_max,
                "height_above_ground_mean": stats.ndsm_mean,
                "height_above_ground_p95": stats.ndsm_p95,
                "value_semantics": NDSM_VALUE_SEMANTICS,
            },
        )
    )
    job.ground_filter_status = GroundFilterStatus.COMPLETED
    job.ground_filter_metadata = {
        **outcome.metadata,
        **provenance,
        "artifact_ids": {"dtm": str(dtm_id), "ndsm": str(ndsm_id)},
    }
    await db.commit()


async def execute_analysis_job(job_id: uuid.UUID) -> None:
    async with AsyncSessionLocal() as db:
        job = await db.get(AnalysisJob, job_id)
        if job is None:
            logger.warning("Analysis job %s no longer exists; nothing to run", job_id)
            return

        # Claim the job atomically (compare-and-swap on status='queued'),
        # exactly like the cancel endpoint's own conditional UPDATE. A plain
        # "if job.status == CANCELLED: return" here is not enough: it only
        # catches cancellation that already committed before the db.get()
        # above. If a cancel request's UPDATE lands in the window between
        # that read and an unconditional "job.status = RUNNING; commit()",
        # this unconditional commit would silently overwrite the
        # cancellation back to 'running' and let the job execute anyway —
        # a real lost-update race. The atomic UPDATE below closes that
        # window: whichever of the cancel request or this claim commits
        # first wins, and the loser's WHERE clause simply matches no rows.
        claim_result = await db.execute(
            update(AnalysisJob)
            .where(AnalysisJob.id == job_id, AnalysisJob.status == AnalysisJobStatus.QUEUED)
            .values(
                status=AnalysisJobStatus.RUNNING,
                current_stage=AnalysisStage.PREPARING,
                started_at=datetime.now(UTC),
            )
        )
        await db.commit()
        if claim_result.rowcount == 0:
            logger.info(
                "Analysis job %s could not be claimed from 'queued' (status changed "
                "concurrently, e.g. cancelled); skipping execution",
                job_id,
            )
            return
        await db.refresh(job)

        try:
            summary = await _run_stages(db, job)
            # Phase 11: a conditional UPDATE (WHERE status='running'), not a
            # plain ORM mutate-then-commit — the job could have been
            # cancelled (running-job cancellation) in the instant between
            # the last _raise_if_cancelled() checkpoint and this final
            # write. An unconditional commit here would silently clobber
            # that cancellation back to 'completed'. Whichever of this
            # completion or a concurrent cancel_job UPDATE commits first
            # wins; the loser's WHERE clause matches no rows.
            complete_result = await db.execute(
                update(AnalysisJob)
                .where(AnalysisJob.id == job_id, AnalysisJob.status == AnalysisJobStatus.RUNNING)
                .values(
                    status=AnalysisJobStatus.COMPLETED,
                    current_stage=AnalysisStage.COMPLETED,
                    execution_summary=summary,
                    completed_at=datetime.now(UTC),
                )
            )
            await db.commit()
            if complete_result.rowcount == 0:
                logger.info(
                    "Analysis job %s finished its work but was cancelled concurrently "
                    "just before the final commit; leaving it cancelled, not completed",
                    job_id,
                )
        except _JobCancelledMidExecution:
            logger.info(
                "Analysis job %s was cancelled during execution; leaving as cancelled", job_id
            )
        except AnalysisExecutionError as exc:
            await _mark_failed(db, job_id, str(exc))
        except StaleDataError:
            # The job's row disappeared mid-execution (e.g. its project or
            # dataset was deleted concurrently, cascading it away) — there
            # is no row left to mark failed, and the session's transaction
            # is now unusable until rolled back. This can only surface from
            # one of the `await db.commit()` calls inside _run_stages or the
            # success-path commit just above, not from application logic.
            logger.warning(
                "Analysis job %s's row disappeared during execution (likely its "
                "project or dataset was deleted concurrently); nothing left to update",
                job_id,
            )
            await db.rollback()
        except Exception:
            logger.exception("Unexpected error executing analysis job %s", job_id)
            await _mark_failed(db, job_id, "An internal error occurred while processing this job.")


async def _mark_failed(db: AsyncSession, job_id: uuid.UUID, message: str) -> None:
    """A plain, conditional Core UPDATE — deliberately not touching the
    (possibly session-poisoned, e.g. after a StaleDataError) ORM `job`
    object passed around elsewhere in this module. Safe to call from any
    exception handler regardless of what the session's transaction state
    was left in.

    Phase 11: conditioned on the row still being `running` (not just
    matched by id) for the same reason as the completion path above — a
    concurrent running-job cancellation may have already moved this job to
    `cancelled`, and a failure discovered afterwards must never overwrite
    that back to `failed`.
    """
    await db.rollback()
    result = await db.execute(
        update(AnalysisJob)
        .where(AnalysisJob.id == job_id, AnalysisJob.status == AnalysisJobStatus.RUNNING)
        .values(
            status=AnalysisJobStatus.FAILED,
            error_message=message,
            completed_at=datetime.now(UTC),
        )
    )
    await db.commit()
    if result.rowcount == 0:
        logger.info(
            "Analysis job %s no longer 'running' (already cancelled, or its row no "
            "longer exists); could not record failure %r",
            job_id,
            message,
        )


async def _raise_if_cancelled(db: AsyncSession, job: AnalysisJob) -> None:
    """Re-reads the job's real, current status from the database (the
    in-memory ORM object does not reflect a concurrent external change
    without an explicit refresh — see docs/DEVELOPMENT.md) and raises the
    internal cancellation signal if it has been cancelled. Under the current
    cancellation policy (queued-only cancellation, enforced by the atomic
    claim above) this cannot actually be triggered externally once a job has
    been claimed — it exists as an explicit, cheap defensive check around
    the expensive inference step, and so that a future phase can safely
    widen cancellation to running jobs without this pipeline silently
    ignoring it.
    """
    await db.refresh(job)
    if job.status == AnalysisJobStatus.CANCELLED:
        raise _JobCancelledMidExecution()


async def _run_stages(db: AsyncSession, job: AnalysisJob) -> dict:
    settings = get_settings()

    # --- PREPARING: load referenced records, verify they still exist ---
    dataset = await db.get(Dataset, job.dataset_id)
    if dataset is None:
        raise AnalysisExecutionError("The dataset for this job no longer exists.")

    try:
        parameters = AnalysisParametersV1.model_validate(job.parameters)
    except Exception as exc:
        raise AnalysisExecutionError(f"Stored job parameters are invalid: {exc}") from exc

    # Phase 8: a disaster-screening job is ALWAYS standalone — it never
    # re-runs depth estimation/calibration/semantic segmentation, it loads
    # an already-produced elevation artifact directly (see
    # AnalysisParametersV1.disaster_source_artifact_id and
    # docs/ARCHITECTURE.md §3.8). Dispatch here, before any of the
    # depth-pipeline-specific validation below (which assumes the job's own
    # dataset is the RGB image to run inference on — never true for a
    # disaster-only job).
    if parameters.disaster_source_artifact_id is not None:
        return await _run_disaster_only_stages(db, job, parameters, settings, dataset)

    # --- VALIDATING_INPUT: real checks against the stored artifact ---
    job.current_stage = AnalysisStage.VALIDATING_INPUT
    await db.commit()

    if dataset.status != DatasetStatus.VALID:
        raise AnalysisExecutionError(
            f"Dataset status is '{dataset.status.value}', not 'valid'; cannot analyze."
        )

    storage = get_storage()
    if not storage.exists(dataset.storage_key):
        raise AnalysisExecutionError("The dataset's stored file is missing from storage.")

    absolute_path = storage.absolute_path(dataset.storage_key)
    try:
        metadata = extract_raster_metadata(absolute_path)
    except RasterValidationError as exc:
        raise AnalysisExecutionError(f"Stored file could not be re-validated: {exc}") from exc

    mismatches = []
    if metadata.width != dataset.width:
        mismatches.append(f"width {metadata.width} != recorded {dataset.width}")
    if metadata.height != dataset.height:
        mismatches.append(f"height {metadata.height} != recorded {dataset.height}")
    if metadata.bands != dataset.bands:
        mismatches.append(f"bands {metadata.bands} != recorded {dataset.bands}")
    if metadata.is_georeferenced != dataset.is_georeferenced:
        mismatches.append("georeferencing status no longer matches recorded metadata")
    if mismatches:
        raise AnalysisExecutionError(
            "Stored file no longer matches its recorded metadata: " + "; ".join(mismatches)
        )

    max_dim = settings.MAX_DEPTH_INPUT_DIMENSION_PX
    if metadata.width > max_dim or metadata.height > max_dim:
        raise AnalysisExecutionError(
            f"Image dimensions {metadata.width}x{metadata.height} exceed the maximum "
            f"supported for depth estimation ({max_dim}px on the longest side)."
        )

    # --- LOADING_MODEL ---
    job.current_stage = AnalysisStage.LOADING_MODEL
    await db.commit()

    estimator = get_depth_estimator()
    try:
        estimator.load()
    except ModelLoadError as exc:
        raise AnalysisExecutionError(f"Depth model could not be loaded: {exc}") from exc

    # --- PREPROCESSING: read real pixel data, apply the input policy ---
    job.current_stage = AnalysisStage.PREPROCESSING
    await db.commit()

    try:
        raster = read_raster_array(absolute_path)
    except RasterValidationError as exc:
        raise AnalysisExecutionError(f"Stored file could not be re-read: {exc}") from exc

    band_statistics = [
        {
            "band": band_index + 1,
            "min": float(raster.data[band_index].min()),
            "max": float(raster.data[band_index].max()),
            "mean": float(raster.data[band_index].mean()),
        }
        for band_index in range(raster.data.shape[0])
    ]

    try:
        rgb_array = extract_rgb_uint8(raster, dataset.file_type)
    except UnsupportedDepthInputError as exc:
        raise AnalysisExecutionError(str(exc)) from exc

    # Real, cheap image-quality metrics (Phase 6) — computed for every job
    # unconditionally (pure numpy, sub-second even on CPU — see
    # docs/ARCHITECTURE.md §3.6 for measured timing), not gated behind
    # semantic segmentation. Explicitly NOT a model-confidence or
    # segmentation-accuracy signal — see geospatial/image_quality.py.
    image_quality = compute_image_quality(rgb_array)

    await _raise_if_cancelled(db, job)

    # --- INFERENCE: real model forward pass, no fabricated values ---
    job.current_stage = AnalysisStage.INFERENCE
    await db.commit()

    try:
        prediction = estimator.predict(rgb_array)
    except (InferenceError, AIUnsupportedInputError) as exc:
        raise AnalysisExecutionError(f"Depth inference failed: {exc}") from exc

    await _raise_if_cancelled(db, job)

    # --- WRITING_DEPTH: persist the real prediction as a raster artifact ---
    job.current_stage = AnalysisStage.WRITING_DEPTH
    await db.commit()

    artifact_id = uuid.uuid4()
    storage_key = f"projects/{job.project_id}/analysis/{job.id}/depth.tif"
    output_path = storage.absolute_path(storage_key)
    try:
        write_single_band_float32(
            output_path,
            prediction.depth,
            crs=raster.crs,
            transform=raster.transform,
        )
        file_size_bytes = output_path.stat().st_size
    except Exception as exc:
        storage.delete(storage_key)
        raise AnalysisExecutionError(f"Could not write the depth artifact: {exc}") from exc

    model_info = estimator.info()
    artifact_metadata = {
        "model_name": model_info.name,
        "model_revision": model_info.revision,
        "model_source": model_info.source,
        "model_license": model_info.license,
        "device": model_info.device,
        "source_dataset_id": str(dataset.id),
        "source_width": prediction.input_width,
        "source_height": prediction.input_height,
        "output_width": prediction.input_width,
        "output_height": prediction.input_height,
        "model_input_width": prediction.model_input_width,
        "model_input_height": prediction.model_input_height,
        "inference_seconds": prediction.inference_seconds,
        "depth_min": float(prediction.depth.min()),
        "depth_max": float(prediction.depth.max()),
        "depth_mean": float(prediction.depth.mean()),
        "dtype": "float32",
        "is_georeferenced": raster.is_georeferenced,
        "crs": metadata.crs,
        "value_semantics": RELATIVE_DEPTH_VALUE_SEMANTICS,
    }
    db.add(
        AnalysisArtifact(
            id=artifact_id,
            analysis_job_id=job.id,
            artifact_type="relative_depth",
            storage_key=storage_key,
            mime_type="image/tiff",
            file_size_bytes=file_size_bytes,
            artifact_metadata=artifact_metadata,
        )
    )

    # --- CALIBRATION (Phase 4): only attempted if a DEM/GCP reference was
    # supplied when the job was created. A job with no reference skips
    # straight to FINALIZING, exactly like Phase 3 — no fake stage
    # transitions for work that was never requested. See
    # app/services/calibration_pipeline.py and docs/ARCHITECTURE.md §3.4.
    wants_calibration = (
        parameters.dem_reference_dataset_id is not None
        or parameters.gcp_reference_dataset_id is not None
    )

    if wants_calibration:
        job.current_stage = AnalysisStage.CALIBRATING
        job.calibration_status = CalibrationStatus.CALIBRATING
        await db.commit()

        outcome = await run_calibration(
            db,
            project_id=job.project_id,
            depth=prediction.depth,
            source_crs=raster.crs,
            source_transform=raster.transform,
            dem_reference_dataset_id=parameters.dem_reference_dataset_id,
            gcp_reference_dataset_id=parameters.gcp_reference_dataset_id,
            settings=settings,
        )
        job.calibration_status = outcome.status
        job.calibration_metadata = outcome.metadata
        await db.commit()

        # A failed calibration attempt does NOT fail the job — the real
        # relative-depth artifact already exists and is a valid result on
        # its own. It just means no metric_elevation/DSM artifact exists.
        if outcome.status == CalibrationStatus.CALIBRATED:
            await _raise_if_cancelled(db, job)

            # --- WRITING_METRIC_ELEVATION ---
            job.current_stage = AnalysisStage.WRITING_METRIC_ELEVATION
            await db.commit()

            # D3: the calibrated values are in the reference's vertical unit.
            # It is written as the GeoTIFF band unit ONLY when the reference
            # declared it; otherwise the rasters carry no unit (never "metre"
            # by default) and physical derivatives of them will refuse to run.
            unit_resolution = outcome.vertical_unit
            calibrated_band_unit = (
                unit_resolution.unit.name
                if unit_resolution is not None and unit_resolution.unit is not None
                else None
            )
            vertical_unit_metadata = (
                unit_resolution.as_dict() if unit_resolution is not None else None
            )

            metric_artifact_id = uuid.uuid4()
            metric_key = f"projects/{job.project_id}/analysis/{job.id}/metric_elevation.tif"
            metric_path = storage.absolute_path(metric_key)
            try:
                write_single_band_float32(
                    metric_path,
                    outcome.metric_elevation,
                    crs=raster.crs,
                    transform=raster.transform,
                    band_unit=calibrated_band_unit,
                )
                metric_size = metric_path.stat().st_size
            except Exception as exc:
                storage.delete(metric_key)
                raise AnalysisExecutionError(
                    f"Could not write the metric elevation artifact: {exc}"
                ) from exc

            db.add(
                AnalysisArtifact(
                    id=metric_artifact_id,
                    analysis_job_id=job.id,
                    artifact_type="metric_elevation",
                    storage_key=metric_key,
                    mime_type="image/tiff",
                    file_size_bytes=metric_size,
                    artifact_metadata={
                        "source_dataset_id": str(dataset.id),
                        "depth_artifact_id": str(artifact_id),
                        "calibration": outcome.metadata,
                        "width": metadata.width,
                        "height": metadata.height,
                        "crs": metadata.crs,
                        "is_georeferenced": raster.is_georeferenced,
                        "dtype": "float32",
                        "elevation_min": float(outcome.metric_elevation.min()),
                        "elevation_max": float(outcome.metric_elevation.max()),
                        "elevation_mean": float(outcome.metric_elevation.mean()),
                        "vertical_unit": vertical_unit_metadata,
                        "value_semantics": (
                            "Calibrated metric elevation (same units as the reference "
                            "data used for calibration), computed as Z = a*D + b from the "
                            "relative-depth artifact. Not independently validated ground "
                            "truth — see this artifact's calibration.validation_metrics for "
                            "real residual statistics against the reference actually used."
                        ),
                    },
                )
            )

            # --- WRITING_DSM ---
            job.current_stage = AnalysisStage.WRITING_DSM
            await db.commit()

            dsm_artifact_id = uuid.uuid4()
            dsm_key = f"projects/{job.project_id}/analysis/{job.id}/dsm.tif"
            dsm_path = storage.absolute_path(dsm_key)
            try:
                write_single_band_float32(
                    dsm_path,
                    outcome.metric_elevation,
                    crs=raster.crs,
                    transform=raster.transform,
                    band_unit=calibrated_band_unit,
                )
                dsm_size = dsm_path.stat().st_size
            except Exception as exc:
                storage.delete(dsm_key)
                raise AnalysisExecutionError(f"Could not write the DSM artifact: {exc}") from exc

            db.add(
                AnalysisArtifact(
                    id=dsm_artifact_id,
                    analysis_job_id=job.id,
                    artifact_type="dsm",
                    storage_key=dsm_key,
                    mime_type="image/tiff",
                    file_size_bytes=dsm_size,
                    artifact_metadata={
                        "source_dataset_id": str(dataset.id),
                        "depth_artifact_id": str(artifact_id),
                        "metric_elevation_artifact_id": str(metric_artifact_id),
                        "calibration": outcome.metadata,
                        "width": metadata.width,
                        "height": metadata.height,
                        "crs": metadata.crs,
                        "is_georeferenced": raster.is_georeferenced,
                        "dtype": "float32",
                        "elevation_min": float(outcome.metric_elevation.min()),
                        "elevation_max": float(outcome.metric_elevation.max()),
                        "elevation_mean": float(outcome.metric_elevation.mean()),
                        "vertical_unit": vertical_unit_metadata,
                        "limitations": (
                            "For Phase 4, this DSM is numerically identical to the "
                            "metric_elevation artifact: no DSM-specific processing "
                            "(hydro-flattening, void-filling, terrain/object separation) "
                            "has been applied yet. It represents whatever surface the "
                            "monocular depth model saw (ground, buildings, vegetation, "
                            "etc. all at once), calibrated to the reference's units — not a "
                            "bare-earth model, and not claimed to be globally "
                            "ground-truth accurate."
                        ),
                    },
                )
            )

            # --- P1-5 calibration residuals: sample-point GeoJSON only
            # (never a raster, never interpolated). Only the artifact ID is
            # added to calibration_metadata — the per-sample data lives in
            # the artifact file, never in the job row.
            residuals_artifact_id: uuid.UUID | None = None
            if outcome.residuals is not None:
                residuals_artifact_id = uuid.uuid4()
                residuals_key = (
                    f"projects/{job.project_id}/analysis/{job.id}/calibration_residuals.geojson"
                )
                try:
                    with storage.open_writer(residuals_key) as f:
                        f.write(json.dumps(outcome.residuals.feature_collection).encode("utf-8"))
                    residuals_size = storage.absolute_path(residuals_key).stat().st_size
                except Exception as exc:
                    storage.delete(residuals_key)
                    raise AnalysisExecutionError(
                        f"Could not write the calibration residuals artifact: {exc}"
                    ) from exc
                db.add(
                    AnalysisArtifact(
                        id=residuals_artifact_id,
                        analysis_job_id=job.id,
                        artifact_type=CALIBRATION_RESIDUALS_ARTIFACT_TYPE,
                        storage_key=residuals_key,
                        mime_type=GEOJSON_MIME_TYPE,
                        file_size_bytes=residuals_size,
                        artifact_metadata={
                            **outcome.residuals.summary,
                            "source_dataset_id": str(dataset.id),
                            "depth_artifact_id": str(artifact_id),
                            "reference_dataset_id": outcome.metadata.get("reference_dataset_id"),
                            "metric_elevation_artifact_id": str(metric_artifact_id),
                            "dsm_artifact_id": str(dsm_artifact_id),
                        },
                    )
                )

            # Record both new artifact IDs on the job's own calibration
            # metadata too, so the full provenance chain (DSM -> metric
            # elevation -> relative depth -> calibration reference) is
            # reconstructable from the job row alone, not just by reading
            # each artifact's own metadata.
            job.calibration_metadata = {
                **outcome.metadata,
                "metric_elevation_artifact_id": str(metric_artifact_id),
                "dsm_artifact_id": str(dsm_artifact_id),
            }
            if residuals_artifact_id is not None:
                job.calibration_metadata["calibration_residuals_artifact_id"] = str(
                    residuals_artifact_id
                )

            # --- VALIDATING_RESULTS: reopen what was just written and
            # confirm it's real, readable, correctly-shaped data — the same
            # "genuinely re-verify" principle Phase 2/3 apply elsewhere,
            # not a no-op stage marker.
            job.current_stage = AnalysisStage.VALIDATING_RESULTS
            await db.commit()

            for path, label in ((metric_path, "metric elevation"), (dsm_path, "DSM")):
                try:
                    with rasterio.open(path) as written:
                        if (
                            written.count != 1
                            or written.dtypes[0] != "float32"
                            or (written.width, written.height) != (metadata.width, metadata.height)
                        ):
                            raise AnalysisExecutionError(
                                f"Written {label} artifact failed a post-write sanity check "
                                f"(unexpected shape/dtype)."
                            )
                except AnalysisExecutionError:
                    raise
                except Exception as exc:
                    raise AnalysisExecutionError(
                        f"Could not re-open the written {label} artifact: {exc}"
                    ) from exc

            # --- P1-3 GROUND FILTER: only reachable here, i.e. after a
            # calibration that ended CALIBRATED (passed the P1-2 quality
            # gate) and whose metric_elevation/dsm were written and
            # re-verified. A soft add-on: its failure never fails the job.
            await _run_ground_filter_stage(
                db,
                job,
                dsm=outcome.metric_elevation,
                crs=raster.crs,
                transform=raster.transform,
                crs_string=metadata.crs,
                provenance={
                    "source_dataset_id": str(dataset.id),
                    "depth_artifact_id": str(artifact_id),
                    "metric_elevation_artifact_id": str(metric_artifact_id),
                    "dsm_artifact_id": str(dsm_artifact_id),
                    "calibration_quality_gate_passed": True,
                    "calibration_quality_policy_version": (
                        (outcome.metadata.get("quality_gate") or {}).get("policy") or {}
                    ).get("version"),
                },
                settings=settings,
                vertical_unit=outcome.vertical_unit,
            )

    # --- SEMANTIC SEGMENTATION (Phase 6): only attempted if the job's
    # parameters requested it. A job with no request skips straight to
    # FINALIZING, exactly like calibration — no fake stage transitions for
    # work never asked for. See app/services/semantic_pipeline.py and
    # docs/ARCHITECTURE.md §3.6. MobileSAM provides real class-agnostic
    # region segmentation only — never a semantic land-cover/object class.
    if parameters.enable_semantic_segmentation:
        job.current_stage = AnalysisStage.LOADING_SEMANTIC_MODEL
        job.semantic_status = SemanticStatus.PROCESSING
        await db.commit()

        await _raise_if_cancelled(db, job)

        # --- SEMANTIC_PREPROCESSING: a real, documented memory-safety
        # downsample for a very large source image. Measured real MobileSAM
        # inference time was roughly CONSTANT across tested resolutions
        # (its own encoder preprocessing already resizes internally) — this
        # bounds array/memory size, not runtime. See
        # settings.MAX_SEMANTIC_INPUT_DIMENSION_PX.
        job.current_stage = AnalysisStage.SEMANTIC_PREPROCESSING
        await db.commit()

        semantic_input = rgb_array
        semantic_max_dim = settings.MAX_SEMANTIC_INPUT_DIMENSION_PX
        source_height, source_width = rgb_array.shape[0], rgb_array.shape[1]
        downsampled_for_semantic = max(source_height, source_width) > semantic_max_dim
        if downsampled_for_semantic:
            scale = semantic_max_dim / max(source_height, source_width)
            resized_height = max(1, round(source_height * scale))
            resized_width = max(1, round(source_width * scale))
            semantic_input = np.array(
                PILImage.fromarray(rgb_array).resize(
                    (resized_width, resized_height), PILImage.BILINEAR
                )
            )

        await _raise_if_cancelled(db, job)

        # --- SEMANTIC_INFERENCE: real MobileSAM forward pass. As with
        # Phase 3's depth inference, in-flight inference is not forcibly
        # interrupted if cancelled mid-flight (the underlying model call
        # cannot be safely preempted) — the defensive checks around it
        # ensure a cancelled job's result is never persisted either way.
        job.current_stage = AnalysisStage.SEMANTIC_INFERENCE
        await db.commit()

        semantic_outcome = run_semantic_segmentation(semantic_input, settings=settings)

        # A failed segmentation attempt does NOT fail the job — the real
        # depth (and, if requested, calibrated) result already exists and
        # is a valid result on its own.
        if semantic_outcome.status == SemanticStatus.COMPLETED:
            # Real cancellation checkpoint: after inference, before the
            # artifact is ever written to storage/the database.
            await _raise_if_cancelled(db, job)

            label_map = semantic_outcome.label_map
            if downsampled_for_semantic:
                # Map the categorical label map back to the REAL source
                # resolution via NEAREST-neighbor ONLY — bicubic/bilinear
                # would blend distinct region IDs into meaningless
                # intermediate values. The artifact's dimensions must match
                # the source image; never silently left at the downsampled
                # inference resolution, and never a fabricated coordinate.
                label_map = np.array(
                    PILImage.fromarray(label_map.astype(np.int32), mode="I").resize(
                        (source_width, source_height), PILImage.NEAREST
                    ),
                    dtype=np.uint32,
                )

            # --- WRITING_SEMANTIC ---
            job.current_stage = AnalysisStage.WRITING_SEMANTIC
            await db.commit()

            semantic_artifact_id = uuid.uuid4()
            semantic_key = f"projects/{job.project_id}/analysis/{job.id}/semantic.tif"
            semantic_path = storage.absolute_path(semantic_key)
            try:
                write_single_band_categorical(
                    semantic_path,
                    label_map,
                    crs=raster.crs,
                    transform=raster.transform,
                )
                semantic_size = semantic_path.stat().st_size
            except Exception as exc:
                storage.delete(semantic_key)
                raise AnalysisExecutionError(
                    f"Could not write the semantic segmentation artifact: {exc}"
                ) from exc

            db.add(
                AnalysisArtifact(
                    id=semantic_artifact_id,
                    analysis_job_id=job.id,
                    artifact_type="semantic_segmentation",
                    storage_key=semantic_key,
                    mime_type="image/tiff",
                    file_size_bytes=semantic_size,
                    artifact_metadata={
                        "display_label": "Distinct Surface Regions",
                        "source_dataset_id": str(dataset.id),
                        "depth_artifact_id": str(artifact_id),
                        "width": metadata.width,
                        "height": metadata.height,
                        "crs": metadata.crs,
                        "is_georeferenced": raster.is_georeferenced,
                        "dtype": "uint32",
                        "nodata": 0,
                        "downsampled_for_inference": downsampled_for_semantic,
                        **semantic_outcome.metadata,
                    },
                )
            )
            job.semantic_metadata = {
                **semantic_outcome.metadata,
                "semantic_artifact_id": str(semantic_artifact_id),
                "downsampled_for_inference": downsampled_for_semantic,
            }
        else:
            job.semantic_metadata = semantic_outcome.metadata

        job.semantic_status = semantic_outcome.status
        await db.commit()

    # --- FINALIZING ---
    job.current_stage = AnalysisStage.FINALIZING
    await db.commit()

    return {
        "parameters_version": parameters.version,
        "dataset_id": str(dataset.id),
        "reopened_file": True,
        "width": metadata.width,
        "height": metadata.height,
        "bands": metadata.bands,
        "is_georeferenced": metadata.is_georeferenced,
        "crs": metadata.crs,
        "band_statistics": band_statistics,
        "image_quality": {
            "sharpness_laplacian_variance": image_quality.sharpness_laplacian_variance,
            "underexposed_fraction": image_quality.underexposed_fraction,
            "overexposed_fraction": image_quality.overexposed_fraction,
            "luminance_min": image_quality.luminance_min,
            "luminance_max": image_quality.luminance_max,
            "luminance_mean": image_quality.luminance_mean,
            "valid_pixel_fraction": image_quality.valid_pixel_fraction,
            "notes": (
                "Real, cheap image-quality indicators computed directly from the "
                "uploaded RGB pixel data. These measure properties of the image "
                "itself only — NOT model confidence, NOT segmentation accuracy, and "
                "NOT elevation accuracy. See geospatial/image_quality.py."
            ),
        },
        "depth_estimation": {
            "model_name": model_info.name,
            "model_revision": model_info.revision,
            "device": model_info.device,
            "inference_seconds": prediction.inference_seconds,
            "output_width": prediction.input_width,
            "output_height": prediction.input_height,
            "artifact_id": str(artifact_id),
        },
        "checked_at": datetime.now(UTC).isoformat(),
    }


async def _run_disaster_only_stages(
    db: AsyncSession,
    job: AnalysisJob,
    parameters: AnalysisParametersV1,
    settings: Settings,
    dataset: Dataset,
) -> dict:
    """Phase 8: the ENTIRE pipeline for a disaster-screening job — never
    combined with depth/calibration/semantic in the same run (see
    docs/ARCHITECTURE.md §3.8). Real terrain derivatives (slope, aspect,
    terrain statistics) are always computed once an elevation artifact is
    validated; flood/landslide screening are each only run if the job's
    parameters requested them. A real failure anywhere in this function
    fails the WHOLE job (`disaster_status=FAILED` is set here before
    re-raising as AnalysisExecutionError, which the outer
    `execute_analysis_job` handler turns into `job.status=FAILED`) — unlike
    Phase 4/6's soft-failure add-ons, this job has no other independently
    valid result to fall back on.
    """
    storage = get_storage()

    # --- VALIDATING_INPUT (disaster-specific): re-verify the cited
    # elevation artifact still exists, belongs to this project, and is a
    # real elevation type — the same check app/services/analysis_jobs.py
    # already did at job-creation time, re-done here since time passes
    # between creation and execution and the artifact could have changed.
    job.current_stage = AnalysisStage.VALIDATING_INPUT
    await db.commit()

    artifact_id = parameters.disaster_source_artifact_id
    result = await db.execute(
        select(AnalysisArtifact, AnalysisJob.project_id)
        .join(AnalysisJob, AnalysisArtifact.analysis_job_id == AnalysisJob.id)
        .where(AnalysisArtifact.id == artifact_id)
    )
    row = result.first()
    if row is None:
        raise AnalysisExecutionError(f"Elevation artifact {artifact_id} no longer exists.")
    source_artifact, source_project_id = row
    if source_project_id != job.project_id:
        raise AnalysisExecutionError(
            f"Elevation artifact {artifact_id} does not belong to this project."
        )
    if source_artifact.artifact_type not in ELEVATION_ARTIFACT_TYPES:
        raise AnalysisExecutionError(NOT_ELEVATION_ERROR)
    if not storage.exists(source_artifact.storage_key):
        raise AnalysisExecutionError("The cited elevation artifact's stored file is missing.")

    # A defensive size bound, cheap to check from structural metadata alone
    # (no pixel data read yet) — in practice every metric_elevation/dsm
    # artifact already inherits Phase 3's own MAX_DEPTH_INPUT_DIMENSION_PX
    # cap from the source image it was derived from, so this is not a
    # reachable path today, but it's the same "never trust a stale read"
    # defensive re-check every other phase in this pipeline already applies
    # to its own real input.
    try:
        source_metadata = extract_raster_metadata(
            storage.absolute_path(source_artifact.storage_key)
        )
    except RasterValidationError as exc:
        raise AnalysisExecutionError(
            f"Elevation artifact could not be re-validated: {exc}"
        ) from exc
    max_dim = settings.MAX_DEPTH_INPUT_DIMENSION_PX
    if source_metadata.width > max_dim or source_metadata.height > max_dim:
        raise AnalysisExecutionError(
            f"Elevation raster dimensions {source_metadata.width}x{source_metadata.height} "
            f"exceed the maximum supported for hazard screening ({max_dim}px on the longest side)."
        )

    await _raise_if_cancelled(db, job)

    job.current_stage = AnalysisStage.LOADING_TERRAIN_DATA
    job.disaster_status = DisasterStatus.PROCESSING
    await db.commit()

    elevation_path = storage.absolute_path(source_artifact.storage_key)

    await _raise_if_cancelled(db, job)

    try:
        # --- COMPUTING_SLOPE / COMPUTING_ASPECT / COMPUTING_TERRAIN_STATISTICS
        # / RUNNING_FLOOD_SCREENING / RUNNING_LANDSLIDE_SCREENING: the real
        # work happens inside one call to run_disaster_screening (each real
        # sub-step is independently timed there); the stage markers here
        # advance for real-time progress display, matching the same
        # single-call-spans-one-stage pattern Phase 3 already uses for its
        # own inference step. ---
        job.current_stage = AnalysisStage.COMPUTING_SLOPE
        await db.commit()

        landslide_thresholds = (
            LandslideThresholds(**parameters.landslide_thresholds.model_dump())
            if parameters.landslide_thresholds is not None
            else None
        )
        try:
            outcome = run_disaster_screening(
                elevation_path,
                source_artifact_id=source_artifact.id,
                source_artifact_type=source_artifact.artifact_type,
                run_flood=parameters.run_flood_screening,
                water_level=parameters.water_level,
                run_landslide=parameters.run_landslide_screening,
                landslide_thresholds=landslide_thresholds,
            )
        except (DisasterAnalysisError, RasterValidationError) as exc:
            raise AnalysisExecutionError(str(exc)) from exc

        job.current_stage = AnalysisStage.COMPUTING_ASPECT
        await db.commit()
        job.current_stage = AnalysisStage.COMPUTING_TERRAIN_STATISTICS
        await db.commit()

        await _raise_if_cancelled(db, job)

        if parameters.run_flood_screening:
            job.current_stage = AnalysisStage.RUNNING_FLOOD_SCREENING
            await db.commit()
        if parameters.run_landslide_screening:
            job.current_stage = AnalysisStage.RUNNING_LANDSLIDE_SCREENING
            await db.commit()

        # Real cancellation checkpoint: after all computation, before any
        # hazard artifact is ever written to storage/the database — same
        # position in the pipeline as every other phase's own
        # after-inference-before-persistence checkpoint.
        await _raise_if_cancelled(db, job)

        # --- WRITING_HAZARD_ARTIFACTS ---
        job.current_stage = AnalysisStage.WRITING_HAZARD_ARTIFACTS
        await db.commit()

        prepared = outcome.prepared
        analysis_crs = crs_to_string(prepared.crs)
        artifact_ids: dict[str, str] = {}
        base_key = f"projects/{job.project_id}/analysis/{job.id}"

        def _write_float(name: str, array: np.ndarray, extra_metadata: dict) -> None:
            key = f"{base_key}/{name}.tif"
            path = storage.absolute_path(key)
            try:
                write_single_band_float32(
                    path,
                    array,
                    crs=prepared.crs,
                    transform=prepared.transform,
                    nodata=ELEVATION_ANALYSIS_NODATA,
                )
                size = path.stat().st_size
            except Exception as exc:
                storage.delete(key)
                raise AnalysisExecutionError(f"Could not write the {name} artifact: {exc}") from exc
            new_id = uuid.uuid4()
            db.add(
                AnalysisArtifact(
                    id=new_id,
                    analysis_job_id=job.id,
                    artifact_type=name,
                    storage_key=key,
                    mime_type="image/tiff",
                    file_size_bytes=size,
                    artifact_metadata={
                        "source_artifact_id": str(source_artifact.id),
                        "source_artifact_type": source_artifact.artifact_type,
                        "width": prepared.width,
                        "height": prepared.height,
                        "crs": analysis_crs,
                        "is_georeferenced": True,
                        "dtype": "float32",
                        "nodata": ELEVATION_ANALYSIS_NODATA,
                        "reprojected_for_analysis": prepared.reprojected,
                        "unit_provenance": outcome.metadata["unit_provenance"],
                        **extra_metadata,
                    },
                )
            )
            artifact_ids[name] = str(new_id)

        def _write_categorical(
            name: str, array: np.ndarray, nodata: int, extra_metadata: dict
        ) -> None:
            key = f"{base_key}/{name}.tif"
            path = storage.absolute_path(key)
            try:
                write_single_band_categorical(
                    path, array, crs=prepared.crs, transform=prepared.transform, nodata=nodata
                )
                size = path.stat().st_size
            except Exception as exc:
                storage.delete(key)
                raise AnalysisExecutionError(f"Could not write the {name} artifact: {exc}") from exc
            new_id = uuid.uuid4()
            db.add(
                AnalysisArtifact(
                    id=new_id,
                    analysis_job_id=job.id,
                    artifact_type=name,
                    storage_key=key,
                    mime_type="image/tiff",
                    file_size_bytes=size,
                    artifact_metadata={
                        "source_artifact_id": str(source_artifact.id),
                        "source_artifact_type": source_artifact.artifact_type,
                        "width": prepared.width,
                        "height": prepared.height,
                        "crs": analysis_crs,
                        "is_georeferenced": True,
                        "dtype": "uint32",
                        "nodata": nodata,
                        "reprojected_for_analysis": prepared.reprojected,
                        **extra_metadata,
                    },
                )
            )
            artifact_ids[name] = str(new_id)

        _write_float(
            "slope",
            outcome.slope,
            {
                "units": "degrees",
                "method": "Horn (1981) 3x3 weighted finite-difference",
                "slope_min": outcome.terrain_stats.min_slope_deg,
                "slope_max": outcome.terrain_stats.max_slope_deg,
                "slope_mean": outcome.terrain_stats.mean_slope_deg,
                "value_semantics": (
                    "Real terrain slope in degrees, computed via Horn's method from the "
                    "actual elevation raster. NoData at raster edges/adjacent-to-NoData pixels."
                ),
            },
        )
        _write_float(
            "aspect",
            outcome.aspect,
            {
                "units": "degrees (compass bearing, 0=North, clockwise)",
                "flat_terrain_sentinel": -1.0,
                "method": "Horn (1981) 3x3 weighted finite-difference",
                "value_semantics": (
                    "Real downslope-facing direction in compass degrees. A value of -1.0 "
                    "marks a real, valid, perfectly flat pixel (slope==0) whose downslope "
                    "direction is mathematically undefined — distinct from NoData."
                ),
            },
        )
        _write_float(
            "hillshade",
            outcome.hillshade,
            {
                "units": "illumination fraction, 0.0-1.0 (unitless)",
                "method": (
                    "Lambertian cosine illumination over Horn (1981) 3x3 surface gradients "
                    "(the same gradients used for slope/aspect above)"
                ),
                "sun_azimuth_deg": DEFAULT_HILLSHADE_AZIMUTH_DEG,
                "sun_altitude_deg": DEFAULT_HILLSHADE_ALTITUDE_DEG,
                "value_semantics": (
                    "Real terrain illumination (1.0 = surface directly facing the sun, 0.0 = "
                    "perpendicular to or facing away from it), computed from the actual "
                    "elevation raster. This is ILLUMINATION, NOT elevation — it carries no "
                    "height information and is never a valid 3D terrain-mesh height source. "
                    "NoData where the reflect-padded 3x3 neighborhood contains an invalid "
                    "elevation pixel, same rule as slope/aspect."
                ),
            },
        )

        if outcome.flood is not None:
            _write_categorical(
                "flood_screening",
                outcome.flood.classification,
                FLOOD_NODATA,
                {
                    "display_label": "Flood Screening",
                    **outcome.metadata["flood"],
                },
            )
        if outcome.landslide is not None:
            _write_categorical(
                "landslide_screening",
                outcome.landslide.classification,
                LANDSLIDE_NODATA,
                {
                    "display_label": "Landslide Screening",
                    **outcome.metadata["landslide"],
                },
            )

        job.disaster_metadata = {**outcome.metadata, "artifact_ids": artifact_ids}
        job.disaster_status = DisasterStatus.COMPLETED
        await db.commit()
    except _JobCancelledMidExecution:
        raise
    except AnalysisExecutionError as exc:
        job.disaster_status = DisasterStatus.FAILED
        job.disaster_metadata = {"error": str(exc)}
        await db.commit()
        raise

    # --- FINALIZING ---
    job.current_stage = AnalysisStage.FINALIZING
    await db.commit()

    return {
        "parameters_version": parameters.version,
        "dataset_id": str(dataset.id),
        "disaster_screening": job.disaster_metadata,
    }
