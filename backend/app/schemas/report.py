"""Phase 9 report/export schemas.

`ReportRead` never includes the (potentially large) `report_metadata` blob —
that is served on demand via the dedicated JSON-export endpoint
(`GET .../reports/{report_id}/json`) so listing/polling a report's status
stays cheap. Every field here is either read straight from the persisted
`Report` row or is `None`/absent when generation genuinely hasn't produced
that output yet — never a fabricated placeholder.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, model_validator

from app.models.report import ReportStatus


class ReportRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    dataset_id: uuid.UUID
    user_id: uuid.UUID
    status: ReportStatus
    error_message: str | None
    # Real, queryable availability flags — the frontend uses these rather
    # than guessing from `status` alone, since a `completed` report might
    # still have no CSV (e.g. a dataset with no measurements and no
    # disaster screening genuinely has no tabular data to export).
    pdf_available: bool
    csv_available: bool
    bundle_available: bool
    created_at: datetime
    completed_at: datetime | None
    updated_at: datetime

    @model_validator(mode="before")
    @classmethod
    def _derive_availability(cls, obj):
        if isinstance(obj, dict):
            return obj
        return {
            "id": obj.id,
            "project_id": obj.project_id,
            "dataset_id": obj.dataset_id,
            "user_id": obj.user_id,
            "status": obj.status,
            "error_message": obj.error_message,
            "pdf_available": obj.pdf_storage_key is not None,
            "csv_available": obj.csv_storage_key is not None,
            "bundle_available": obj.bundle_storage_key is not None,
            "created_at": obj.created_at,
            "completed_at": obj.completed_at,
            "updated_at": obj.updated_at,
        }


class ReportCreate(BaseModel):
    """Empty today — a report always covers everything currently available
    for its dataset (see `app/services/report_builder.py`). Kept as a real
    (if currently field-less) request model, matching this project's
    existing convention of every mutating endpoint taking a typed body
    (`extra` is implicitly forbidden by having no fields at all), rather
    than accepting an untyped `{}`."""

    model_config = ConfigDict(extra="forbid")
