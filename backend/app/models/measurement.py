import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class MeasurementType(str, enum.Enum):
    """The four real Phase 7 measurement kinds — see
    `app/services/measurement_service.py` for what each actually computes
    and `geospatial/measurements.py` for the underlying raster math.

    Deliberately a fixed, small, real set of categories (mirrors the
    `CalibrationStatus`/`SemanticStatus` precedent) rather than a free-text
    column — a client-supplied arbitrary string is never persisted as a
    trusted measurement type.
    """

    POINT_ELEVATION = "point_elevation"
    DISTANCE = "distance"
    PROFILE = "profile"
    COORDINATE = "coordinate"
    # P1-4: the stored value of an existing slope raster at a map coordinate
    # resolved against that raster's own CRS/transform — never elevation.
    POINT_SLOPE = "point_slope"


def _enum_values(enum_cls):
    return [member.value for member in enum_cls]


class Measurement(Base):
    """A real, persisted measurement result — never a placeholder row.

    `input_data`/`result_data` are JSONB (consistent with this project's
    existing convention for structured-but-varying data — `parameters`,
    `execution_summary`, `calibration_metadata`, `semantic_metadata` all use
    the same pattern) rather than a PostGIS geometry column: this codebase
    has PostGIS enabled at the database level but no ORM model anywhere
    uses a geometry column yet, and Phase 7 deliberately doesn't introduce
    that as a first (see docs/ARCHITECTURE.md §3.7). `result_data` always
    includes real `units`/`calibration_state`/`disclaimer` keys — see
    `app/services/measurement_service.py` — so provenance is inspectable
    from the stored JSON alone, without a dedicated column per concern.

    All four FKs cascade on delete, exactly like every other child row in
    this project (`analysis_jobs`, `analysis_artifacts`, ...) — a deleted
    project/job/artifact/user takes its measurements with it, never leaving
    an orphaned or dangling record.
    """

    __tablename__ = "measurements"
    __table_args__ = (Index("ix_measurements_project_created", "project_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    analysis_job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("analysis_jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    artifact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("analysis_artifacts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # The creating user — provenance only (see docs/ARCHITECTURE.md §5):
    # projects in this codebase are single-owner (no sharing/collaborator
    # feature exists), so `get_owned_project` already fully gates access;
    # this column records *who* created the measurement, it is not a second
    # authorization dimension.
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    measurement_type: Mapped[MeasurementType] = mapped_column(
        SAEnum(MeasurementType, name="measurement_type", values_callable=_enum_values),
        nullable=False,
    )
    # The exact, real request this measurement was computed from (row/col,
    # or row1/col1/row2/col2/samples) — persisted verbatim so a saved
    # measurement is always reproducible from the database alone.
    input_data: Mapped[dict] = mapped_column(JSONB, nullable=False)
    # The full real computed result — see `app/services/measurement_service.py`
    # for the exact shape per measurement_type. Never a placeholder; always
    # produced by a real backend calculation over the real stored raster at
    # save time (the server recomputes from `input_data`, it never trusts a
    # client-supplied result).
    result_data: Mapped[dict] = mapped_column(JSONB, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
