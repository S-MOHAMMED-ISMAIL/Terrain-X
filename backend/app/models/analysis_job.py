import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Text, func, text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AnalysisJobStatus(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


ACTIVE_STATUSES = (AnalysisJobStatus.QUEUED, AnalysisJobStatus.RUNNING)


class AnalysisStage(str, enum.Enum):
    """Real orchestration lifecycle stages.

    EXECUTING was Phase 2's single generic "doing work" stage. Phase 3 splits
    that work into real, granular depth-pipeline stages (LOADING_MODEL
    through WRITING_DEPTH) and no longer produces EXECUTING for new jobs —
    but EXECUTING is kept in the enum permanently: Postgres enum types are
    append-only (values can't be removed without recreating the type), and
    removing it from the Python side would break deserializing any
    historical row still holding that value. Future phases add new stage
    values the same way (append, never remove/reuse) without needing to
    change the job/status model itself.
    """

    QUEUED = "queued"
    PREPARING = "preparing"
    VALIDATING_INPUT = "validating_input"
    LOADING_MODEL = "loading_model"
    PREPROCESSING = "preprocessing"
    INFERENCE = "inference"
    WRITING_DEPTH = "writing_depth"
    CALIBRATING = "calibrating"
    WRITING_METRIC_ELEVATION = "writing_metric_elevation"
    WRITING_DSM = "writing_dsm"
    VALIDATING_RESULTS = "validating_results"
    # P1-3: raster bare-earth approximation (DTM) + nDSM, only entered after a
    # calibration that passed the quality gate — see
    # app/services/ground_filter_pipeline.py and docs/ARCHITECTURE.md §3.12.
    FILTERING_GROUND = "filtering_ground"
    WRITING_DTM = "writing_dtm"
    WRITING_NDSM = "writing_ndsm"
    # Phase 6: real class-agnostic region segmentation (MobileSAM), only
    # entered if the job's parameters requested it — see
    # AnalysisParametersV1.enable_semantic_segmentation and
    # docs/ARCHITECTURE.md §3.6. Image-quality metrics are NOT a separate
    # stage: they're cheap (pure numpy over the array already read for
    # depth) and computed inline during PREPROCESSING for every job,
    # regardless of whether semantic segmentation was requested.
    LOADING_SEMANTIC_MODEL = "loading_semantic_model"
    SEMANTIC_PREPROCESSING = "semantic_preprocessing"
    SEMANTIC_INFERENCE = "semantic_inference"
    WRITING_SEMANTIC = "writing_semantic"
    # Phase 8: real terrain-derived hazard screening, only entered if the
    # job's parameters requested it (AnalysisParametersV1.enable_disaster_screening)
    # — see app/services/disaster_pipeline.py and docs/ARCHITECTURE.md §3.8.
    # A disaster-screening job is a standalone job (never combined with
    # depth/calibration/semantic in the same run) that loads a real,
    # already-produced metric_elevation/dsm artifact cited by
    # `disaster_source_artifact_id` — these stages are the ENTIRE pipeline
    # for that job, not an add-on tail.
    LOADING_TERRAIN_DATA = "loading_terrain_data"
    COMPUTING_SLOPE = "computing_slope"
    COMPUTING_ASPECT = "computing_aspect"
    COMPUTING_TERRAIN_STATISTICS = "computing_terrain_statistics"
    RUNNING_FLOOD_SCREENING = "running_flood_screening"
    RUNNING_LANDSLIDE_SCREENING = "running_landslide_screening"
    WRITING_HAZARD_ARTIFACTS = "writing_hazard_artifacts"
    EXECUTING = "executing"  # legacy Phase 2 value; superseded by the stages above
    FINALIZING = "finalizing"
    COMPLETED = "completed"


# Documented stage -> progress-percent boundaries. A job's exposed `progress`
# is derived from this mapping (never a separately-tracked, driftable
# number), and only advances when the corresponding stage has actually
# started/finished for real. The four calibration stages only apply to jobs
# that were given a DEM/GCP reference (see AnalysisParametersV1); a job with
# no reference goes WRITING_DEPTH -> FINALIZING directly, so its progress
# still reaches 100 without passing through the calibration percentages.
STAGE_PROGRESS: dict[AnalysisStage, int] = {
    AnalysisStage.QUEUED: 0,
    AnalysisStage.PREPARING: 5,
    AnalysisStage.VALIDATING_INPUT: 10,
    AnalysisStage.LOADING_MODEL: 18,
    AnalysisStage.PREPROCESSING: 26,
    AnalysisStage.INFERENCE: 36,
    AnalysisStage.WRITING_DEPTH: 44,
    AnalysisStage.CALIBRATING: 50,
    AnalysisStage.WRITING_METRIC_ELEVATION: 56,
    AnalysisStage.WRITING_DSM: 60,
    AnalysisStage.VALIDATING_RESULTS: 64,
    AnalysisStage.FILTERING_GROUND: 66,
    AnalysisStage.WRITING_DTM: 67,
    AnalysisStage.WRITING_NDSM: 68,
    AnalysisStage.LOADING_SEMANTIC_MODEL: 70,
    AnalysisStage.SEMANTIC_PREPROCESSING: 76,
    AnalysisStage.SEMANTIC_INFERENCE: 88,
    AnalysisStage.WRITING_SEMANTIC: 94,
    # Phase 8: a disaster-screening job never visits the depth/calibration/
    # semantic stages above (it's a standalone job over an already-produced
    # elevation artifact — see AnalysisStage's own Phase 8 docstring), so
    # these values only need to be monotonic along ITS OWN real stage path
    # (queued -> preparing -> validating_input -> these -> finalizing ->
    # completed); numeric overlap with the depth-pipeline values above is
    # harmless since no single job ever visits both sets.
    AnalysisStage.LOADING_TERRAIN_DATA: 15,
    AnalysisStage.COMPUTING_SLOPE: 30,
    AnalysisStage.COMPUTING_ASPECT: 40,
    AnalysisStage.COMPUTING_TERRAIN_STATISTICS: 58,
    AnalysisStage.RUNNING_FLOOD_SCREENING: 72,
    AnalysisStage.RUNNING_LANDSLIDE_SCREENING: 84,
    AnalysisStage.WRITING_HAZARD_ARTIFACTS: 92,
    AnalysisStage.EXECUTING: 70,  # legacy Phase 2 value, kept for old rows
    AnalysisStage.FINALIZING: 97,
    AnalysisStage.COMPLETED: 100,
}


class CalibrationStatus(str, enum.Enum):
    """Whether this job's relative-depth output was converted to metric
    elevation, and if so how well the conversion is understood to fit.

    UNCALIBRATED is the default and the outcome for any job that wasn't
    given a DEM/GCP reference — it is not an error, it's simply "no metric
    calibration was requested." FAILED means calibration was requested but
    could not be completed (non-georeferenced source, bad CRS, insufficient
    valid reference samples, ...) or its fit did not pass the calibration
    quality gate (`calibration_metadata.quality_gate`, see
    geospatial/calibration.py) — the job itself can still be `completed`
    with a real relative-depth artifact; only the calibration attempt failed.
    """

    UNCALIBRATED = "uncalibrated"
    CALIBRATING = "calibrating"
    CALIBRATED = "calibrated"
    FAILED = "failed"


class SemanticStatus(str, enum.Enum):
    """Whether this job's real class-agnostic region segmentation
    (MobileSAM — see ai/mobile_sam.py) was requested and how it went.

    NOT_REQUESTED is the default and the outcome for any job that didn't
    opt in (`AnalysisParametersV1.enable_semantic_segmentation=False`) — not
    an error. FAILED means segmentation was requested but could not be
    completed (model/weights unavailable, unsupported input, inference
    error, ...) — the job itself can still be `completed` with a real
    depth/DSM result; only the segmentation attempt failed. Mirrors the
    `CalibrationStatus` soft-failure pattern above exactly.
    """

    NOT_REQUESTED = "not_requested"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class GroundFilterStatus(str, enum.Enum):
    """Whether this job's P1-3 raster bare-earth approximation (DTM) and
    nDSM were produced (geospatial/ground_filter.py).

    NOT_REQUESTED is the default and stays the outcome for every job whose
    calibration did not end CALIBRATED (not requested, failed, or rejected by
    the calibration quality gate) — ground filtering only ever runs on a
    gate-passed DSM. FAILED means filtering was attempted but produced no
    result; like SemanticStatus it is a soft failure — the job still
    completes and the DSM/metric elevation survive.
    """

    NOT_REQUESTED = "not_requested"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class DisasterStatus(str, enum.Enum):
    """Whether this job's real terrain-derived hazard screening (Phase 8 —
    see geospatial/terrain_derivatives.py) was requested and how it went.

    Unlike CalibrationStatus/SemanticStatus (soft-failure add-ons to an
    otherwise-independently-valid depth job), a disaster-screening job is
    ALWAYS a standalone job whose entire purpose is the hazard screening
    itself — see AnalysisParametersV1.enable_disaster_screening. So a real
    FAILED disaster attempt also fails the job as a whole
    (`AnalysisJobStatus.FAILED`), not just this status; DisasterStatus still
    exists as its own field (matching the CalibrationStatus/SemanticStatus
    precedent) so the real reason is queryable/displayable the same way.

    NOT_REQUESTED is the default and the outcome for any job that didn't
    opt in — not an error, and the normal value for every pre-Phase-8 job
    and every depth/calibration/semantic job that never requested hazard
    screening.
    """

    NOT_REQUESTED = "not_requested"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


def _enum_values(enum_cls):
    return [member.value for member in enum_cls]


class AnalysisJob(Base):
    __tablename__ = "analysis_jobs"
    __table_args__ = (
        # At most one active (queued/running) job per dataset, enforced by
        # the database itself (not just an app-level check-then-insert,
        # which would race) — see docs/ARCHITECTURE.md for the policy.
        Index(
            "ux_analysis_jobs_active_dataset",
            "dataset_id",
            unique=True,
            postgresql_where=text("status IN ('queued', 'running')"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    dataset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("datasets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    status: Mapped[AnalysisJobStatus] = mapped_column(
        SAEnum(AnalysisJobStatus, name="analysis_job_status", values_callable=_enum_values),
        nullable=False,
        default=AnalysisJobStatus.QUEUED,
        server_default=AnalysisJobStatus.QUEUED.value,
        index=True,
    )
    current_stage: Mapped[AnalysisStage] = mapped_column(
        SAEnum(AnalysisStage, name="analysis_stage", values_callable=_enum_values),
        nullable=False,
        default=AnalysisStage.QUEUED,
        server_default=AnalysisStage.QUEUED.value,
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # The exact, validated parameters this job was created with (see
    # app/schemas/analysis.py:AnalysisParametersV1) — persisted verbatim so a
    # job's configuration is always reproducible from the database alone.
    parameters: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    # Real execution metadata produced by this job's own run (files reopened,
    # checks performed, band statistics, timing) — explicitly NOT scientific/
    # geospatial output. See AnalysisArtifact for where real future terrain
    # outputs will be recorded.
    execution_summary: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # A top-level, directly queryable calibration outcome (see
    # CalibrationStatus), plus the full detail (method, reference used,
    # fitted a/b, sample counts, validation metrics, CRS transformations
    # performed) in calibration_metadata — see docs/ARCHITECTURE.md §3.4.
    calibration_status: Mapped[CalibrationStatus] = mapped_column(
        SAEnum(CalibrationStatus, name="calibration_status", values_callable=_enum_values),
        nullable=False,
        default=CalibrationStatus.UNCALIBRATED,
        server_default=CalibrationStatus.UNCALIBRATED.value,
    )
    calibration_metadata: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # Phase 6: a second, independent soft-failure outcome (see
    # SemanticStatus) — deliberately not folded into calibration_status,
    # since segmentation and calibration are unrelated pipeline branches
    # that can each succeed or fail on their own. semantic_metadata carries
    # model/repository/revision/checkpoint/license/device/timing, real
    # region count, and image-quality metrics — see
    # app/services/semantic_pipeline.py.
    semantic_status: Mapped[SemanticStatus] = mapped_column(
        SAEnum(SemanticStatus, name="semantic_status", values_callable=_enum_values),
        nullable=False,
        default=SemanticStatus.NOT_REQUESTED,
        server_default=SemanticStatus.NOT_REQUESTED.value,
    )
    semantic_metadata: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # P1-3: raster bare-earth approximation outcome (see GroundFilterStatus).
    # ground_filter_metadata carries the method, applied policy/parameters,
    # metric cell size, window/threshold schedule, statistics, the dtm/ndsm
    # artifact IDs, and the limitations text — or the real failure reason.
    ground_filter_status: Mapped[GroundFilterStatus] = mapped_column(
        SAEnum(GroundFilterStatus, name="ground_filter_status", values_callable=_enum_values),
        nullable=False,
        default=GroundFilterStatus.NOT_REQUESTED,
        server_default=GroundFilterStatus.NOT_REQUESTED.value,
    )
    ground_filter_metadata: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # Phase 8: real terrain-derived hazard screening outcome (see
    # DisasterStatus, geospatial/terrain_derivatives.py). disaster_metadata
    # carries the source elevation artifact ID, the real methodology/
    # thresholds used, real terrain statistics, and real flood/landslide
    # statistics — see app/services/disaster_pipeline.py.
    disaster_status: Mapped[DisasterStatus] = mapped_column(
        SAEnum(DisasterStatus, name="disaster_status", values_callable=_enum_values),
        nullable=False,
        default=DisasterStatus.NOT_REQUESTED,
        server_default=DisasterStatus.NOT_REQUESTED.value,
    )
    disaster_metadata: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
