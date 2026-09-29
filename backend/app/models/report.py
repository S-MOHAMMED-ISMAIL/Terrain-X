import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ReportStatus(str, enum.Enum):
    """A report's own real generation lifecycle — mirrors the
    `CalibrationStatus`/`SemanticStatus`/`DisasterStatus` precedent exactly
    (a small, fixed, real status set, never a free-text column).

    PENDING is the row's state the instant it's created and enqueued;
    GENERATING while the worker is actually building the PDF/CSV/bundle;
    COMPLETED once every applicable file has been written and
    `report_metadata` holds the real frozen data snapshot; FAILED if
    generation raised for real (see `error_message`) — unlike Phase 8's
    disaster jobs, a report has no other purpose to fall back on, so a
    failed report never has a partially-usable PDF.
    """

    PENDING = "pending"
    GENERATING = "generating"
    COMPLETED = "completed"
    FAILED = "failed"


def _enum_values(enum_cls):
    return [member.value for member in enum_cls]


class Report(Base):
    """Phase 9: a real, persisted report/export record for one dataset's
    analysis results within a project.

    Scoped by `dataset_id`, not a single `analysis_job_id` — a dataset can
    have both a real depth/calibration job and a real, independently-run
    Phase 8 disaster-screening job (see `app/services/visualization.py`'s
    `latest_completed_job`/`latest_completed_disaster_job` split query,
    reused here for exactly this reason); a report summarizes whichever of
    those actually exist and completed, never fabricating either.

    `report_metadata` is the full, real, structured data snapshot built at
    generation time from the actual database rows/artifacts that existed at
    that moment (see `app/services/report_builder.py`) — this is the single
    source of truth BOTH the generated PDF and the JSON-export endpoint
    render from, so the two are always mutually consistent and the report
    stays reproducible from the database alone, exactly like
    `AnalysisJob.calibration_metadata`/`disaster_metadata` already work.
    `pdf_storage_key`/`csv_storage_key`/`bundle_storage_key` point to real
    files written through the existing storage backend (same convention as
    `AnalysisArtifact.storage_key`) — `csv_storage_key` is null whenever no
    tabular data (measurements, terrain statistics, hazard class counts)
    was actually available to export, never an empty placeholder file.
    """

    __tablename__ = "reports"
    __table_args__ = (Index("ix_reports_project_created", "project_id", "created_at"),)

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
    # The creating user — provenance only (see docs/ARCHITECTURE.md §5), same
    # rationale as `Measurement.user_id`: projects are single-owner, so
    # `get_owned_project` already fully gates access.
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    status: Mapped[ReportStatus] = mapped_column(
        SAEnum(ReportStatus, name="report_status", values_callable=_enum_values),
        nullable=False,
        default=ReportStatus.PENDING,
        server_default=ReportStatus.PENDING.value,
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # The real, frozen data snapshot this report's PDF/JSON/CSV were all
    # rendered from — see `app/services/report_builder.py::build_report_data`.
    # Null until generation actually completes.
    report_metadata: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    pdf_storage_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    csv_storage_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    bundle_storage_key: Mapped[str | None] = mapped_column(String(500), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
