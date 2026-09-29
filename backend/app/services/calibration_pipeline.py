"""Phase 4 backend-side orchestration for metric calibration + DSM
generation. Scientific fitting/sampling logic lives in
geospatial/calibration.py (pure, backend-independent); this module loads the
right reference dataset from the database, calls into that logic, and turns
the result into the job's calibration_status/calibration_metadata — the same
separation of concerns as app/services/depth_pipeline.py for Phase 3.
"""

import logging
import time
import uuid
from dataclasses import dataclass

import numpy as np
from rasterio.crs import CRS
from rasterio.errors import CRSError
from rasterio.transform import Affine
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.storage import get_storage
from app.models.analysis_job import CalibrationStatus
from app.models.dataset import Dataset, DatasetRole, DatasetStatus
from geospatial.calibration import (
    RESIDUAL_DEFINITION,
    RESIDUAL_UNITS,
    RESIDUALS_DISCLAIMER,
    CalibrationSamples,
    CrossValidationResult,
    DegenerateCalibrationError,
    QualityGatePolicy,
    RobustFitResult,
    build_residual_feature_collection,
    compute_calibration_residuals,
    compute_fit_diagnostics,
    compute_validation_metrics,
    cross_validate_samples,
    describe_quality_gate_failure,
    evaluate_quality_gate,
    fit_robust_affine,
    heldout_block_summaries,
    sample_dem_pairs,
    sample_gcp_pairs,
    summarize_residuals,
)
from geospatial.exceptions import RasterValidationError
from geospatial.vertical_units import (
    VerticalUnitResolution,
    crs_vertical_unit,
    read_raster_vertical_unit,
)

logger = logging.getLogger("terrainx.backend.calibration")

# Expected sign of the affine scale `a` in Z = a*D + b (quality-gate
# criterion G1). NOT a universal rule about monocular depth — it follows
# from two properties of THIS pipeline:
#   1. The current depth estimator's declared convention: larger relative
#      depth means CLOSER to the camera (ai/depth_anything.py docstring;
#      RELATIVE_DEPTH_VALUE_SEMANTICS in app/services/depth_pipeline.py).
#   2. The calibration geometry: run_calibration only accepts a source with a
#      real CRS and affine geotransform (see below), i.e. a map-projected,
#      overhead product. Looking down, a surface closer to the sensor is
#      higher, so reference elevation must increase with relative depth.
# The existing relative-terrain mesh already assumes the same direction
# (frontend terrainMesh.ts maps larger depth to larger height). If a future
# estimator declared the opposite convention, this constant changes with it.
EXPECTED_SCALE_SIGN = 1

# Stored verbatim on every calibration result so the in-sample, inlier-only
# nature of `validation_metrics` (kept under its original key for
# compatibility) is never mistaken for held-out accuracy.
VALIDATION_METRICS_SCOPE = "in_sample_inliers"


def quality_gate_policy(settings: Settings) -> QualityGatePolicy:
    return QualityGatePolicy(
        version=settings.CALIBRATION_QUALITY_POLICY_VERSION,
        min_cv_skill=settings.CALIBRATION_MIN_CV_SKILL,
        expected_scale_sign=EXPECTED_SCALE_SIGN,
        cv_blocks_per_side=settings.CALIBRATION_CV_BLOCKS_PER_SIDE,
    )


class CalibrationNotPossibleError(Exception):
    """A real, expected reason calibration cannot proceed (non-georeferenced
    source, missing/invalid reference, insufficient valid samples, ...).
    Always caught by the caller and recorded as calibration_status=failed —
    never a job-level failure, since the source depth estimation already
    succeeded independently of whether calibration works.
    """


@dataclass
class CalibrationResidualsPayload:
    """P1-5: the calibration_residuals artifact's content — the GeoJSON
    FeatureCollection and the summary stored as its artifact metadata."""

    feature_collection: dict
    summary: dict


@dataclass
class CalibrationOutcome:
    status: CalibrationStatus
    metadata: dict
    metric_elevation: np.ndarray | None = None
    # P1-5: only ever set for a CALIBRATED (gate-passed) outcome.
    residuals: CalibrationResidualsPayload | None = None
    # D3: the calibration reference's vertical unit — the unit of the
    # calibrated elevation, since Z = a*D + b is fitted to reference values.
    # Resolved from documented metadata only; never assumed.
    vertical_unit: VerticalUnitResolution | None = None


def build_residuals_payload(
    samples: CalibrationSamples,
    fit: RobustFitResult,
    cross_validation: CrossValidationResult,
    *,
    reference_type: str,
    policy: QualityGatePolicy,
) -> CalibrationResidualsPayload:
    residuals = compute_calibration_residuals(samples, fit, cross_validation)
    summary = {
        "residual_definition": RESIDUAL_DEFINITION,
        "units": RESIDUAL_UNITS,
        "disclaimer": RESIDUALS_DISCLAIMER,
        "reference_type": reference_type,
        "default_kind": "heldout",
        "kinds": {
            "heldout": {
                "label": (
                    "Held-out residual (spatial-block cross-validation)"
                    if reference_type == "dem"
                    else "Held-out residual (leave-one-out)"
                ),
                "statistics": summarize_residuals(residuals.residual_heldout),
            },
            "fit": {
                "label": "In-sample fit residual - not validation",
                "statistics": summarize_residuals(residuals.residual_fit),
            },
        },
        "cv_method": cross_validation.method,
        "blocks_per_side": cross_validation.blocks_per_side,
        "quality_policy_version": policy.version,
        "heldout_blocks": heldout_block_summaries(residuals, cross_validation),
        "sample_count": samples.valid_count,
        "total_candidate_samples": samples.total_candidates,
        "inlier_samples": int(residuals.inlier_in_production_fit.sum()),
        "outlier_samples": int((~residuals.inlier_in_production_fit).sum()),
        "source_crs": samples.source_crs,
        "reference_crs": samples.reference_crs,
        "geojson_crs": "EPSG:4326",
    }
    return CalibrationResidualsPayload(
        feature_collection=build_residual_feature_collection(
            samples, residuals, reference_type=reference_type
        ),
        summary=summary,
    )


async def _load_reference_dataset(
    db: AsyncSession, project_id: uuid.UUID, dataset_id: uuid.UUID, expected_role: DatasetRole
) -> Dataset:
    """Defensive re-verification at execution time — the same checks
    app.services.analysis_jobs already ran at job-creation time, repeated
    here because time has passed and the reference could have been deleted
    or changed since (the same "re-verify everything the worker touches"
    principle Phase 2/3 already apply to the source dataset).
    """
    reference = await db.get(Dataset, dataset_id)
    if reference is None or reference.project_id != project_id:
        raise CalibrationNotPossibleError(
            f"Reference dataset {dataset_id} no longer exists in this project."
        )
    if reference.role != expected_role:
        raise CalibrationNotPossibleError(
            f"Reference dataset {dataset_id} no longer has role '{expected_role.value}'."
        )
    if reference.status != DatasetStatus.VALID:
        raise CalibrationNotPossibleError(
            f"Reference dataset {dataset_id} status is now '{reference.status.value}', not 'valid'."
        )
    return reference


async def run_calibration(
    db: AsyncSession,
    *,
    project_id: uuid.UUID,
    depth: np.ndarray,
    source_crs: CRS | None,
    source_transform: Affine | None,
    dem_reference_dataset_id: uuid.UUID | None,
    gcp_reference_dataset_id: uuid.UUID | None,
    settings: Settings,
) -> CalibrationOutcome:
    """Runs the full calibration attempt and always returns a
    CalibrationOutcome (never raises) — every failure mode this phase
    anticipates is a real, informative `CalibrationOutcome(status=FAILED,
    metadata={"error": ...})`, not an exception the caller must also handle.
    """
    start = time.monotonic()

    if source_crs is None or source_transform is None:
        return CalibrationOutcome(
            status=CalibrationStatus.FAILED,
            metadata={
                "error": (
                    "Source image is not georeferenced; DEM/GCP correspondence to "
                    "real-world coordinates cannot be established, so calibration is "
                    "not possible for this dataset."
                )
            },
        )

    reference_type = "dem" if dem_reference_dataset_id is not None else "gcp"

    try:
        if dem_reference_dataset_id is not None:
            reference = await _load_reference_dataset(
                db, project_id, dem_reference_dataset_id, DatasetRole.DEM_REFERENCE
            )
            storage = get_storage()
            dem_path = storage.absolute_path(reference.storage_key)
            if not storage.exists(reference.storage_key):
                raise CalibrationNotPossibleError("The DEM reference's stored file is missing.")
            samples = sample_dem_pairs(
                depth,
                source_crs,
                source_transform,
                dem_path,
                max_samples=settings.CALIBRATION_MAX_SAMPLES,
            )
            reference_info = {"reference_dataset_id": str(reference.id)}
            vertical_unit = read_raster_vertical_unit(dem_path)
        else:
            reference = await _load_reference_dataset(
                db, project_id, gcp_reference_dataset_id, DatasetRole.GCP_REFERENCE
            )
            if not reference.gcp_points or not reference.gcp_crs:
                raise CalibrationNotPossibleError(
                    "The GCP reference has no parsed points; it may have failed validation."
                )
            try:
                CRS.from_user_input(reference.gcp_crs)
            except CRSError as exc:
                raise CalibrationNotPossibleError(
                    f"The GCP reference's declared CRS is invalid: {exc}"
                ) from exc
            samples = sample_gcp_pairs(
                depth, source_crs, source_transform, reference.gcp_points, reference.gcp_crs
            )
            reference_info = {
                "reference_dataset_id": str(reference.id),
                "gcp_point_count": len(reference.gcp_points),
            }
            vertical_unit = crs_vertical_unit(reference.gcp_crs, source="gcp_crs")

        if samples.valid_count < settings.MIN_CALIBRATION_SAMPLES:
            raise CalibrationNotPossibleError(
                f"Only {samples.valid_count} valid reference sample(s) after filtering "
                f"NoData/NaN/out-of-bounds points (out of {samples.total_candidates} "
                f"candidates); at least {settings.MIN_CALIBRATION_SAMPLES} are required."
            )

        fit = fit_robust_affine(
            samples.relative_depth,
            samples.reference_elevation,
            outlier_sigma=settings.CALIBRATION_OUTLIER_SIGMA,
            max_iterations=settings.CALIBRATION_MAX_ITERATIONS,
        )
        metrics = compute_validation_metrics(
            samples.relative_depth, samples.reference_elevation, fit
        )

        # P1-2 quality gate: reporting diagnostics over all valid samples,
        # held-out cross-validation of the same fitting procedure, then the
        # G0/G1/G2 decision. None of this alters `fit` — the production a/b
        # above are exactly what they were before the gate existed.
        policy = quality_gate_policy(settings)
        diagnostics = compute_fit_diagnostics(
            samples.relative_depth,
            samples.reference_elevation,
            fit,
            effective_sample_count=samples.effective_sample_count,
        )
        cross_validation = cross_validate_samples(
            samples,
            reference_type=reference_type,
            source_height=depth.shape[0],
            source_width=depth.shape[1],
            policy=policy,
            outlier_sigma=settings.CALIBRATION_OUTLIER_SIGMA,
            max_iterations=settings.CALIBRATION_MAX_ITERATIONS,
        )
        gate = evaluate_quality_gate(fit, cross_validation, policy)
    except (CalibrationNotPossibleError, RasterValidationError, DegenerateCalibrationError) as exc:
        return CalibrationOutcome(
            status=CalibrationStatus.FAILED,
            metadata={"error": str(exc), "reference_type": reference_type},
        )
    except Exception:
        logger.exception("Unexpected error during calibration (project %s)", project_id)
        return CalibrationOutcome(
            status=CalibrationStatus.FAILED,
            metadata={
                "error": "An internal error occurred during calibration.",
                "reference_type": reference_type,
            },
        )

    duration_seconds = time.monotonic() - start

    metadata = {
        "method": "affine_least_squares_with_iterative_sigma_clipping",
        "reference_type": reference_type,
        **reference_info,
        "scale_a": fit.scale,
        "offset_b": fit.offset,
        "outlier_sigma": fit.outlier_sigma,
        "fit_iterations": fit.iterations_used,
        "total_candidate_samples": samples.total_candidates,
        "valid_samples": samples.valid_count,
        "inlier_samples": metrics.inlier_count,
        "outlier_samples": metrics.outlier_count,
        "validation_metrics": {
            "mae": metrics.mae,
            "rmse": metrics.rmse,
            "bias": metrics.bias,
            "min_residual": metrics.min_residual,
            "max_residual": metrics.max_residual,
        },
        "validation_metrics_scope": VALIDATION_METRICS_SCOPE,
        "fit_diagnostics": diagnostics.as_dict(),
        "cross_validation": cross_validation.as_dict(),
        "quality_gate": gate.as_dict(),
        "source_crs": samples.source_crs,
        "reference_crs": samples.reference_crs,
        "reprojected": samples.reprojected,
        # D3: unit of the reference elevations = unit of the calibrated output.
        "vertical_unit": vertical_unit.as_dict(),
        "calibration_duration_seconds": duration_seconds,
        "limitations": (
            "This affine fit cannot distinguish terrain from object tops (buildings, "
            "trees, ...) visible in the source image; it is a calibrated version of "
            "whatever the monocular depth model saw, not a bare-earth model. "
            "`validation_metrics` are in-sample statistics over the fit's own inliers; "
            "`cross_validation` reports held-out statistics of the same fitting "
            "procedure. Both describe agreement with the supplied reference only, not "
            "independently validated absolute accuracy, and passing the quality gate "
            "is not an accuracy guarantee."
        ),
    }

    if not gate.passed:
        # Every computed diagnostic is kept so the rejection is fully
        # inspectable; no metric elevation array is ever produced, so the
        # caller writes no metric_elevation/dsm artifact and the job's
        # relative_depth terrain fallback applies unchanged.
        return CalibrationOutcome(
            status=CalibrationStatus.FAILED,
            metadata={**metadata, "error": describe_quality_gate_failure(gate)},
            vertical_unit=vertical_unit,
        )

    metric_elevation = (fit.scale * depth + fit.offset).astype("float32")

    # P1-5: residuals at the calibration sample locations. A soft add-on: a
    # failure here is recorded but never changes the P1-2 outcome above.
    residuals_payload: CalibrationResidualsPayload | None = None
    try:
        residuals_payload = build_residuals_payload(
            samples, fit, cross_validation, reference_type=reference_type, policy=policy
        )
    except Exception as exc:
        logger.exception("Could not build calibration residuals (project %s)", project_id)
        metadata["calibration_residuals_error"] = f"Could not build calibration residuals: {exc}"

    return CalibrationOutcome(
        status=CalibrationStatus.CALIBRATED,
        metadata=metadata,
        metric_elevation=metric_elevation,
        residuals=residuals_payload,
        vertical_unit=vertical_unit,
    )
