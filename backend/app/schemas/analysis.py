import math
import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.analysis_job import (
    STAGE_PROGRESS,
    AnalysisJobStatus,
    AnalysisStage,
    CalibrationStatus,
    DisasterStatus,
    GroundFilterStatus,
    SemanticStatus,
)


class LandslideThresholdsIn(BaseModel):
    """Real, client-overridable slope-degree thresholds for the landslide
    susceptibility screening index — see
    geospatial.terrain_derivatives.LandslideThresholds for the defaults and
    the documented rationale. `extra="forbid"` for the same reason as every
    other request schema in this project — arbitrary client JSON is never
    persisted as trusted configuration."""

    model_config = ConfigDict(extra="forbid")
    low_max_deg: float = 10.0
    moderate_max_deg: float = 20.0
    high_max_deg: float = 30.0


class AnalysisParametersV1(BaseModel):
    """Versioned, validated job configuration.

    `dem_reference_dataset_id`/`gcp_reference_dataset_id` (Phase 4, both
    optional) are how a DEM or GCP reference is "attached" to a job: cite
    the ID of a dataset already uploaded with the matching role
    (`dem_reference`/`gcp_reference` — see app/models/dataset.py) in the
    same project. This deliberately reuses job creation as the single place
    calibration is both configured and started, rather than inventing a
    separate stateful "attach a reference, then start" workflow — the whole
    pipeline (depth -> calibration -> DSM) is one job. Neither field breaks
    a Phase 2/3 payload that omits them (they default to None, meaning "no
    calibration requested" — the job still completes with just relative
    depth, exactly as before).

    `extra="forbid"` ensures the API never accepts arbitrary, unvalidated
    frontend JSON as trusted configuration; new fields are added to this
    same v1 schema (as here) when they're backward compatible, or a new
    version is introduced when they're not.
    """

    model_config = ConfigDict(extra="forbid")
    version: Literal["v1"] = "v1"
    dem_reference_dataset_id: uuid.UUID | None = None
    gcp_reference_dataset_id: uuid.UUID | None = None
    # Phase 6: opt-in real class-agnostic region segmentation (MobileSAM).
    # Defaults to False so an omitted field reproduces Phase 3/4/5 behavior
    # exactly — no calibration reference is needed for this (unlike DEM/GCP
    # calibration, segmentation runs directly on the source RGB image).
    enable_semantic_segmentation: bool = False

    # Phase 8: real terrain-derived hazard screening. Unlike calibration/
    # segmentation (both add-ons to a depth-estimation job), a disaster-
    # screening job is ALWAYS standalone — it loads an already-produced
    # metric_elevation/dsm artifact directly and never re-runs depth
    # estimation. Presence of `disaster_source_artifact_id` is what
    # requests it, mirroring the existing dem_reference_dataset_id/
    # gcp_reference_dataset_id "presence implies intent" pattern rather
    # than a separate, potentially-inconsistent boolean flag.
    disaster_source_artifact_id: uuid.UUID | None = None
    run_flood_screening: bool = False
    water_level: float | None = None
    run_landslide_screening: bool = False
    landslide_thresholds: LandslideThresholdsIn | None = None

    @model_validator(mode="after")
    def _at_most_one_reference(self) -> "AnalysisParametersV1":
        if self.dem_reference_dataset_id and self.gcp_reference_dataset_id:
            raise ValueError(
                "Provide at most one calibration reference per job "
                "(dem_reference_dataset_id OR gcp_reference_dataset_id, not both)."
            )
        return self

    @model_validator(mode="after")
    def _validate_disaster_screening(self) -> "AnalysisParametersV1":
        is_disaster_job = self.disaster_source_artifact_id is not None
        if is_disaster_job:
            if (
                self.dem_reference_dataset_id
                or self.gcp_reference_dataset_id
                or self.enable_semantic_segmentation
            ):
                raise ValueError(
                    "A disaster-screening job (disaster_source_artifact_id set) is always "
                    "standalone — it cannot also request calibration or semantic segmentation "
                    "in the same job."
                )
            if not self.run_flood_screening and not self.run_landslide_screening:
                raise ValueError(
                    "Requesting disaster screening (disaster_source_artifact_id set) requires "
                    "at least one of run_flood_screening or run_landslide_screening."
                )
            if self.run_flood_screening and self.water_level is None:
                raise ValueError("water_level is required when run_flood_screening is true.")
            if self.water_level is not None and not math.isfinite(self.water_level):
                raise ValueError("water_level must be a real, finite number.")
        else:
            if (
                self.run_flood_screening
                or self.run_landslide_screening
                or self.water_level is not None
            ):
                raise ValueError(
                    "run_flood_screening/run_landslide_screening/water_level require "
                    "disaster_source_artifact_id to be set."
                )
        return self


class AnalysisJobCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    parameters: AnalysisParametersV1 = Field(default_factory=AnalysisParametersV1)


class AnalysisJobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    dataset_id: uuid.UUID
    user_id: uuid.UUID
    status: AnalysisJobStatus
    current_stage: AnalysisStage
    progress: int
    error_message: str | None
    parameters: dict
    execution_summary: dict | None
    calibration_status: CalibrationStatus
    calibration_metadata: dict | None
    semantic_status: SemanticStatus
    semantic_metadata: dict | None
    ground_filter_status: GroundFilterStatus
    ground_filter_metadata: dict | None
    disaster_status: DisasterStatus
    disaster_metadata: dict | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    updated_at: datetime

    @model_validator(mode="before")
    @classmethod
    def _compute_progress(cls, obj):
        if isinstance(obj, dict):
            return obj
        return {
            "id": obj.id,
            "project_id": obj.project_id,
            "dataset_id": obj.dataset_id,
            "user_id": obj.user_id,
            "status": obj.status,
            "current_stage": obj.current_stage,
            "progress": STAGE_PROGRESS[obj.current_stage],
            "error_message": obj.error_message,
            "parameters": obj.parameters,
            "execution_summary": obj.execution_summary,
            "calibration_status": obj.calibration_status,
            "calibration_metadata": obj.calibration_metadata,
            "semantic_status": obj.semantic_status,
            "semantic_metadata": obj.semantic_metadata,
            "ground_filter_status": obj.ground_filter_status,
            "ground_filter_metadata": obj.ground_filter_metadata,
            "disaster_status": obj.disaster_status,
            "disaster_metadata": obj.disaster_metadata,
            "created_at": obj.created_at,
            "started_at": obj.started_at,
            "completed_at": obj.completed_at,
            "updated_at": obj.updated_at,
        }
