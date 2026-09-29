import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.project import Project


class DatasetStatus(str, enum.Enum):
    UPLOADED = "uploaded"
    VALIDATING = "validating"
    VALID = "valid"
    INVALID = "invalid"
    FAILED = "failed"


class DatasetRole(str, enum.Enum):
    """What a dataset is used *for* — not a client-supplied free-text field,
    a validated enum. `SOURCE_IMAGE` is the Phase 1 default (existing rows
    backfill to it via server_default, so old datasets keep working
    unchanged). `DEM_REFERENCE` datasets are real georeferenced elevation
    rasters, ingested through the same raster pipeline as source images
    (see app/services/dataset_ingestion.py) — no schema duplication needed,
    since a DEM *is* just a raster with a different purpose. `GCP_REFERENCE`
    datasets are CSV point lists and use a separate ingestion path
    (app/services/gcp_ingestion.py) since a CSV isn't a raster at all."""

    SOURCE_IMAGE = "source_image"
    DEM_REFERENCE = "dem_reference"
    GCP_REFERENCE = "gcp_reference"


class Dataset(Base):
    __tablename__ = "datasets"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(500), nullable=False)
    file_type: Mapped[str] = mapped_column(String(20), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)

    role: Mapped[DatasetRole] = mapped_column(
        SAEnum(
            DatasetRole,
            name="dataset_role",
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
        default=DatasetRole.SOURCE_IMAGE,
        server_default=DatasetRole.SOURCE_IMAGE.value,
    )

    status: Mapped[DatasetStatus] = mapped_column(
        SAEnum(
            DatasetStatus,
            name="dataset_status",
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
        default=DatasetStatus.UPLOADED,
        server_default=DatasetStatus.UPLOADED.value,
    )
    validation_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bands: Mapped[int | None] = mapped_column(Integer, nullable=True)

    is_georeferenced: Mapped[bool] = mapped_column(
        nullable=False, default=False, server_default="false"
    )
    crs: Mapped[str | None] = mapped_column(String(255), nullable=True)
    bbox_min_x: Mapped[float | None] = mapped_column(Float, nullable=True)
    bbox_min_y: Mapped[float | None] = mapped_column(Float, nullable=True)
    bbox_max_x: Mapped[float | None] = mapped_column(Float, nullable=True)
    bbox_max_y: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Populated only for role=gcp_reference. A CSV has no inherent CRS, so
    # the user declares one explicitly at upload time (gcp_crs) — never
    # assumed. gcp_points is the parsed [{x,y,z}, ...] list; the original
    # CSV bytes are also kept in storage (storage_key) for provenance/
    # download, exactly like any other dataset.
    gcp_crs: Mapped[str | None] = mapped_column(String(255), nullable=True)
    gcp_points: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    project: Mapped["Project"] = relationship(back_populates="datasets")
