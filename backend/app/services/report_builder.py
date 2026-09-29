"""Phase 9: assembles the real, structured report data snapshot — one place
that reads already-persisted Phase 1-8 rows/artifacts and arranges them into
the exact shape both the generated PDF (`app/services/report_render.py`) and
the JSON-export endpoint serve. Pure data assembly: every value here already
exists somewhere in the database or was computed for real from a stored
raster (`app/services/visualization.py::get_artifact_metadata`) — nothing is
computed, estimated, or fabricated in this module. A section whose source
data doesn't exist is `None`/omitted with a real reason, never a guessed
value (see docs/ARCHITECTURE.md §3.9).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.models.analysis_artifact import AnalysisArtifact
from app.models.analysis_job import (
    AnalysisJob,
    CalibrationStatus,
    GroundFilterStatus,
    SemanticStatus,
)
from app.models.dataset import Dataset
from app.models.measurement import Measurement
from app.models.project import Project
from app.services.depth_pipeline import RELATIVE_DEPTH_VALUE_SEMANTICS
from app.services.disaster_pipeline import DISASTER_SCOPE_DISCLAIMER
from app.services.visualization import (
    CALIBRATION_RESIDUALS_ARTIFACT_TYPE,
    get_artifact_metadata,
    is_raster_artifact,
)

REPORT_SCHEMA_VERSION = "v1"

# A general, always-included scope statement — never claims survey-grade
# accuracy or ground-truth validation for anything this system produces.
GENERAL_LIMITATIONS = (
    "TERRAIN-X outputs are derived from a single monocular RGB image via a "
    "machine-learning depth model, optionally calibrated against a "
    "user-supplied reference. No output in this report is independently "
    "surveyed, ground-truthed, or certified to survey-grade accuracy."
)


@dataclass
class ReportContext:
    """Everything `build_report_data` needs, already fetched from the
    database by `app/services/report_service.py` — kept as a plain
    dataclass (not a bundle of positional args) so this module stays a pure
    function of real, already-loaded data with no database/storage I/O of
    its own."""

    project: Project
    dataset: Dataset
    generated_by_user_id: uuid.UUID
    depth_job: AnalysisJob | None = None
    disaster_job: AnalysisJob | None = None
    depth_job_artifacts: list[AnalysisArtifact] = field(default_factory=list)
    disaster_job_artifacts: list[AnalysisArtifact] = field(default_factory=list)
    reference_dataset: Dataset | None = None
    measurements: list[Measurement] = field(default_factory=list)
    # P1-5: the depth job's stored calibration_residuals GeoJSON
    # FeatureCollection, already read from storage by the caller (None when
    # the job has no such artifact).
    calibration_residuals: dict | None = None


def _bbox_dict(dataset: Dataset) -> dict | None:
    if None in (dataset.bbox_min_x, dataset.bbox_min_y, dataset.bbox_max_x, dataset.bbox_max_y):
        return None
    return {
        "min_x": dataset.bbox_min_x,
        "min_y": dataset.bbox_min_y,
        "max_x": dataset.bbox_max_x,
        "max_y": dataset.bbox_max_y,
    }


def _dataset_dict(dataset: Dataset) -> dict:
    return {
        "id": str(dataset.id),
        "original_filename": dataset.original_filename,
        "file_type": dataset.file_type,
        "role": dataset.role.value,
        "status": dataset.status.value,
        "width": dataset.width,
        "height": dataset.height,
        "bands": dataset.bands,
        "is_georeferenced": dataset.is_georeferenced,
        "crs": dataset.crs,
        "bounds": _bbox_dict(dataset),
    }


def _depth_estimation_dict(depth_job: AnalysisJob) -> dict | None:
    summary = depth_job.execution_summary or {}
    depth = summary.get("depth_estimation")
    if not depth:
        return None
    return {
        "model_name": depth.get("model_name"),
        "model_revision": depth.get("model_revision"),
        "device": depth.get("device"),
        "inference_seconds": depth.get("inference_seconds"),
        "output_width": depth.get("output_width"),
        "output_height": depth.get("output_height"),
        "artifact_id": depth.get("artifact_id"),
        "value_semantics": RELATIVE_DEPTH_VALUE_SEMANTICS,
    }


_GCP_RESIDUAL_FIELDS = (
    "gcp_index",
    "row",
    "col",
    "reference_elevation",
    "predicted_heldout",
    "residual_heldout",
    "residual_fit",
    "inlier_in_production_fit",
)


def _calibration_residuals_dict(
    depth_job: AnalysisJob,
    artifacts: list[AnalysisArtifact],
    feature_collection: dict | None,
) -> dict:
    """P1-5: residuals at calibration sample locations — the artifact's own
    persisted summary, plus (GCP only) one row per control point. Per-sample
    DEM data is never inlined here; it is in the CSV and the bundled
    GeoJSON."""
    artifact = next(
        (a for a in artifacts if a.artifact_type == CALIBRATION_RESIDUALS_ARTIFACT_TYPE), None
    )
    if artifact is None:
        if depth_job.calibration_status == CalibrationStatus.CALIBRATED:
            note = (depth_job.calibration_metadata or {}).get(
                "calibration_residuals_error"
            ) or "This job was calibrated before calibration residual diagnostics existed."
        else:
            note = (
                "Calibration residuals exist only for a calibration that passed the quality gate."
            )
        return {"available": False, "note": note}
    summary = artifact.artifact_metadata or {}
    result = {
        "available": True,
        "artifact_id": str(artifact.id),
        "reference_type": summary.get("reference_type"),
        "residual_definition": summary.get("residual_definition"),
        "units": summary.get("units"),
        "default_kind": summary.get("default_kind"),
        "kinds": summary.get("kinds"),
        "cv_method": summary.get("cv_method"),
        "blocks_per_side": summary.get("blocks_per_side"),
        "heldout_blocks": summary.get("heldout_blocks"),
        "sample_count": summary.get("sample_count"),
        "total_candidate_samples": summary.get("total_candidate_samples"),
        "disclaimer": summary.get("disclaimer"),
    }
    if summary.get("reference_type") == "gcp" and feature_collection is not None:
        result["gcp_points"] = [
            {key: feature["properties"].get(key) for key in _GCP_RESIDUAL_FIELDS}
            for feature in feature_collection.get("features", [])
        ]
    return result


def _calibration_dict(depth_job: AnalysisJob, reference_dataset: Dataset | None) -> dict:
    status = depth_job.calibration_status
    result: dict = {"status": status.value}
    if status == CalibrationStatus.UNCALIBRATED:
        result["note"] = (
            "No DEM/GCP reference was provided for this analysis job — only "
            "relative depth is available; there is no calibrated metric "
            "elevation or DSM to report."
        )
        return result
    metadata = depth_job.calibration_metadata or {}
    result.update(
        {
            "method": metadata.get("method"),
            "reference_type": metadata.get("reference_type"),
            "reference_dataset_id": metadata.get("reference_dataset_id"),
            "scale_a": metadata.get("scale_a"),
            "offset_b": metadata.get("offset_b"),
            "source_crs": metadata.get("source_crs"),
            "reference_crs": metadata.get("reference_crs"),
            "reprojected": metadata.get("reprojected"),
            "total_candidate_samples": metadata.get("total_candidate_samples"),
            "valid_samples": metadata.get("valid_samples"),
            "inlier_samples": metadata.get("inlier_samples"),
            "outlier_samples": metadata.get("outlier_samples"),
            "outlier_sigma": metadata.get("outlier_sigma"),
            "fit_iterations": metadata.get("fit_iterations"),
            # In-sample statistics over the fit's own inliers (kept under
            # their original key); `validation_metrics_scope` says so.
            "validation_metrics": metadata.get("validation_metrics"),
            "validation_metrics_scope": metadata.get("validation_metrics_scope"),
            # P1-2 calibration quality gate: all-valid-sample reporting
            # diagnostics, held-out cross-validation, and the gate decision
            # with the exact policy that was applied. Absent (None) for a
            # calibration run before the gate existed.
            "fit_diagnostics": metadata.get("fit_diagnostics"),
            "cross_validation": metadata.get("cross_validation"),
            "quality_gate": metadata.get("quality_gate"),
            "calibration_duration_seconds": metadata.get("calibration_duration_seconds"),
            "metric_elevation_artifact_id": metadata.get("metric_elevation_artifact_id"),
            "dsm_artifact_id": metadata.get("dsm_artifact_id"),
            "limitations": metadata.get("limitations"),
            "error": metadata.get("error"),
        }
    )
    if reference_dataset is not None:
        result["reference_dataset"] = {
            "id": str(reference_dataset.id),
            "original_filename": reference_dataset.original_filename,
            "role": reference_dataset.role.value,
            "crs": reference_dataset.crs,
            "is_georeferenced": reference_dataset.is_georeferenced,
        }
    return result


def _semantic_dict(depth_job: AnalysisJob) -> dict:
    status = depth_job.semantic_status
    result: dict = {"status": status.value}
    if status == SemanticStatus.NOT_REQUESTED:
        return result
    metadata = depth_job.semantic_metadata or {}
    result.update(
        {
            "model_name": metadata.get("model_name"),
            "device": metadata.get("device"),
            "inference_seconds": metadata.get("inference_seconds"),
            "region_count": metadata.get("region_count"),
            "mask_stability_mean": metadata.get("mask_stability_mean"),
            "value_semantics": metadata.get("value_semantics"),
            "error": metadata.get("error"),
        }
    )
    return result


def _ground_filter_dict(depth_job: AnalysisJob) -> dict:
    """P1-3 raster bare-earth approximation section. Only ever COMPLETED for
    a job whose calibration passed the quality gate; otherwise it records the
    real status and reason, never an invented result."""
    # An unpersisted job object (column default not yet applied) is the same
    # "never run" state as NOT_REQUESTED.
    status = depth_job.ground_filter_status or GroundFilterStatus.NOT_REQUESTED
    result: dict = {"status": status.value}
    if status == GroundFilterStatus.NOT_REQUESTED:
        result["note"] = (
            "Not run: the DTM/nDSM estimates are only produced from a calibrated DSM "
            "that passed the calibration quality gate."
        )
        return result
    metadata = depth_job.ground_filter_metadata or {}
    result.update(
        {
            "method": metadata.get("method"),
            "policy": metadata.get("policy"),
            "parameters": metadata.get("parameters"),
            "cell_size": metadata.get("cell_size"),
            "window_levels": metadata.get("window_levels"),
            "statistics": metadata.get("statistics"),
            "artifact_ids": metadata.get("artifact_ids"),
            "dtm_value_semantics": metadata.get("dtm_value_semantics"),
            "ndsm_value_semantics": metadata.get("ndsm_value_semantics"),
            "limitations": metadata.get("limitations"),
            "error": metadata.get("error"),
        }
    )
    return result


def _disaster_dict(disaster_job: AnalysisJob) -> dict:
    metadata = disaster_job.disaster_metadata or {}
    return {
        "analysis_job_id": str(disaster_job.id),
        "status": disaster_job.disaster_status.value,
        "source_artifact_id": metadata.get("source_artifact_id"),
        "source_artifact_type": metadata.get("source_artifact_type"),
        "source_crs": metadata.get("source_crs"),
        "analysis_crs": metadata.get("analysis_crs"),
        "reprojected_for_analysis": metadata.get("reprojected_for_analysis"),
        "pixel_width": metadata.get("pixel_width"),
        "pixel_height": metadata.get("pixel_height"),
        "width": metadata.get("width"),
        "height": metadata.get("height"),
        "terrain_statistics": metadata.get("terrain_statistics"),
        "flood": metadata.get("flood"),
        "landslide": metadata.get("landslide"),
        "timings_seconds": metadata.get("timings_seconds"),
        "artifact_ids": metadata.get("artifact_ids"),
        "disclaimer": metadata.get("disclaimer", DISASTER_SCOPE_DISCLAIMER),
    }


def _artifact_dict(artifact: AnalysisArtifact) -> dict:
    """Real, on-demand raster statistics (`get_artifact_metadata` re-reads
    the actual stored file) plus the artifact's own persisted provenance —
    never a cached/duplicated copy that could drift from the real file.
    A non-raster artifact (P1-5 calibration residuals) has no raster
    statistics; it is listed with its own sample count instead."""
    if not is_raster_artifact(artifact):
        metadata = artifact.artifact_metadata or {}
        return {
            "artifact_id": str(artifact.id),
            "artifact_type": artifact.artifact_type,
            "display_name": "Calibration residuals (sample points)",
            "mime_type": artifact.mime_type,
            "file_size_bytes": artifact.file_size_bytes,
            "sample_count": metadata.get("sample_count"),
            "crs": metadata.get("geojson_crs"),
            "width": None,
            "height": None,
            "dtype": None,
            "min_value": None,
            "max_value": None,
            "analysis_job_id": str(artifact.analysis_job_id),
            "created_at": artifact.created_at.isoformat(),
        }
    metadata_out = get_artifact_metadata(artifact)
    data = metadata_out.model_dump(mode="json")
    data["analysis_job_id"] = str(artifact.analysis_job_id)
    data["created_at"] = artifact.created_at.isoformat()
    return data


def _measurement_dict(measurement: Measurement) -> dict:
    return {
        "id": str(measurement.id),
        "measurement_type": measurement.measurement_type.value,
        "analysis_job_id": str(measurement.analysis_job_id),
        "artifact_id": str(measurement.artifact_id),
        "input_data": measurement.input_data,
        "result_data": measurement.result_data,
        "created_at": measurement.created_at.isoformat(),
    }


def build_report_data(ctx: ReportContext) -> dict:
    """Builds the complete, real report data dict — the single source of
    truth for the PDF, JSON export, and CSV export of one report. Never
    raises for a genuinely missing optional section (no depth job, no
    disaster job, no measurements); those simply come back as `None`/`[]`
    with the real reason, not a fabricated result."""
    depth_job = ctx.depth_job
    disaster_job = ctx.disaster_job

    depth_section: dict | None = None
    if depth_job is not None:
        depth_section = {
            "analysis_job_id": str(depth_job.id),
            "status": depth_job.status.value,
            "current_stage": depth_job.current_stage.value,
            "created_at": depth_job.created_at.isoformat(),
            "started_at": depth_job.started_at.isoformat() if depth_job.started_at else None,
            "completed_at": (
                depth_job.completed_at.isoformat() if depth_job.completed_at else None
            ),
            "error_message": depth_job.error_message,
            "depth_estimation": _depth_estimation_dict(depth_job),
            "calibration": {
                **_calibration_dict(depth_job, ctx.reference_dataset),
                "residuals": _calibration_residuals_dict(
                    depth_job, ctx.depth_job_artifacts, ctx.calibration_residuals
                ),
            },
            "ground_filter": _ground_filter_dict(depth_job),
            "semantic_segmentation": _semantic_dict(depth_job),
            "image_quality": (depth_job.execution_summary or {}).get("image_quality"),
        }

    disaster_section = _disaster_dict(disaster_job) if disaster_job is not None else None

    artifacts = [_artifact_dict(a) for a in [*ctx.depth_job_artifacts, *ctx.disaster_job_artifacts]]
    measurements = [_measurement_dict(m) for m in ctx.measurements]

    limitations = [GENERAL_LIMITATIONS]
    if depth_job is not None:
        limitations.append(RELATIVE_DEPTH_VALUE_SEMANTICS)
        calibration_limitations = (depth_job.calibration_metadata or {}).get("limitations")
        if calibration_limitations:
            limitations.append(calibration_limitations)
        residuals_section = depth_section["calibration"]["residuals"]
        if residuals_section.get("available") and residuals_section.get("disclaimer"):
            limitations.append(residuals_section["disclaimer"])
        ground_filter_limitations = (depth_job.ground_filter_metadata or {}).get("limitations")
        if ground_filter_limitations:
            limitations.append(ground_filter_limitations)
    if disaster_job is not None:
        metadata = disaster_job.disaster_metadata or {}
        limitations.append(metadata.get("disclaimer", DISASTER_SCOPE_DISCLAIMER))
        flood = metadata.get("flood")
        if flood and flood.get("disclaimer"):
            limitations.append(flood["disclaimer"])
        landslide = metadata.get("landslide")
        if landslide and landslide.get("disclaimer"):
            limitations.append(landslide["disclaimer"])

    return {
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "project": {
            "id": str(ctx.project.id),
            "name": ctx.project.name,
            "description": ctx.project.description,
            "created_at": ctx.project.created_at.isoformat(),
        },
        "dataset": _dataset_dict(ctx.dataset),
        "depth_analysis": depth_section,
        "disaster_screening": disaster_section,
        "artifacts": artifacts,
        "measurements": measurements,
        "provenance": {
            "source_dataset_id": str(ctx.dataset.id),
            "depth_analysis_job_id": str(depth_job.id) if depth_job else None,
            "disaster_analysis_job_id": str(disaster_job.id) if disaster_job else None,
            "generated_by_user_id": str(ctx.generated_by_user_id),
        },
        "limitations": limitations,
    }
