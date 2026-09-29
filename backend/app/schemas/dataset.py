import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, model_validator

from app.models.dataset import DatasetRole, DatasetStatus


class BoundingBoxRead(BaseModel):
    min_x: float
    min_y: float
    max_x: float
    max_y: float


class DatasetRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    original_filename: str
    file_type: str
    mime_type: str
    file_size_bytes: int
    role: DatasetRole
    status: DatasetStatus
    validation_error: str | None
    width: int | None
    height: int | None
    bands: int | None
    is_georeferenced: bool
    crs: str | None
    bbox: BoundingBoxRead | None = None
    gcp_crs: str | None
    gcp_point_count: int | None
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="before")
    @classmethod
    def _assemble_bbox(cls, obj):
        if isinstance(obj, dict):
            return obj
        min_x = getattr(obj, "bbox_min_x", None)
        min_y = getattr(obj, "bbox_min_y", None)
        max_x = getattr(obj, "bbox_max_x", None)
        max_y = getattr(obj, "bbox_max_y", None)
        bbox = None
        if None not in (min_x, min_y, max_x, max_y):
            bbox = BoundingBoxRead(min_x=min_x, min_y=min_y, max_x=max_x, max_y=max_y)
        return {
            "id": obj.id,
            "project_id": obj.project_id,
            "original_filename": obj.original_filename,
            "file_type": obj.file_type,
            "mime_type": obj.mime_type,
            "file_size_bytes": obj.file_size_bytes,
            "role": obj.role,
            "status": obj.status,
            "validation_error": obj.validation_error,
            "width": obj.width,
            "height": obj.height,
            "bands": obj.bands,
            "is_georeferenced": obj.is_georeferenced,
            "crs": obj.crs,
            "bbox": bbox,
            "gcp_crs": obj.gcp_crs,
            # The full point list isn't exposed via the API (could be large,
            # and isn't needed by the frontend) — only a count, computed
            # here rather than stored redundantly.
            "gcp_point_count": len(obj.gcp_points) if obj.gcp_points is not None else None,
            "created_at": obj.created_at,
            "updated_at": obj.updated_at,
        }
