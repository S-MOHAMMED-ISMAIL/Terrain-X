import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class AnalysisArtifactRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    analysis_job_id: uuid.UUID
    artifact_type: str
    mime_type: str
    file_size_bytes: int
    artifact_metadata: dict | None
    created_at: datetime
