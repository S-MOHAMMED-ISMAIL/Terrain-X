"""Real Ground Control Point (GCP) reference ingestion: a CSV file, not a
raster, so it uses a separate path from app/services/dataset_ingestion.py —
but produces the same Dataset row type (role=gcp_reference), no schema
duplication (see app/models/dataset.py:DatasetRole).

CSV format: a header row with columns x, y, z (case-insensitive; any other
column names are ignored, so extra metadata columns don't break parsing).
The CRS of the (x, y) coordinates is *not* inferred or assumed — it must be
declared explicitly by the caller (`gcp_crs`), since a CSV carries no CRS
information of its own.
"""

import csv
import io
import logging
import uuid
from pathlib import Path

from fastapi import UploadFile
from rasterio.crs import CRS
from rasterio.errors import CRSError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import PayloadTooLargeError, ValidationAppError
from app.core.storage import get_storage
from app.models.dataset import Dataset, DatasetRole, DatasetStatus

logger = logging.getLogger("terrainx.backend.gcp_ingestion")

REQUIRED_COLUMNS = ("x", "y", "z")


def parse_gcp_csv(raw_text: str) -> list[dict]:
    """Parses and validates a GCP CSV's content. Raises ValidationAppError
    with a specific, actionable message on any problem — never silently
    drops or invents a point.
    """
    reader = csv.DictReader(io.StringIO(raw_text))
    if reader.fieldnames is None:
        raise ValidationAppError("GCP CSV is empty or has no header row.")

    normalized = {name.strip().lower(): name for name in reader.fieldnames}
    missing = [col for col in REQUIRED_COLUMNS if col not in normalized]
    if missing:
        raise ValidationAppError(
            f"GCP CSV is missing required column(s): {', '.join(missing)}. "
            f"Expected a header row containing x, y, z (case-insensitive)."
        )

    points: list[dict] = []
    for row_number, row in enumerate(reader, start=2):  # header is row 1
        values = {}
        for col in REQUIRED_COLUMNS:
            raw_value = row.get(normalized[col], "")
            try:
                values[col] = float(raw_value)
            except (TypeError, ValueError):
                raise ValidationAppError(
                    f"GCP CSV row {row_number}: column '{col}' value {raw_value!r} "
                    f"is not a valid number."
                ) from None
        points.append(values)

    return points


async def ingest_gcp_reference(
    upload: UploadFile,
    project_id: uuid.UUID,
    gcp_crs: str,
    db: AsyncSession,
) -> Dataset:
    settings = get_settings()
    storage = get_storage()

    try:
        CRS.from_user_input(gcp_crs)
    except CRSError as exc:
        raise ValidationAppError(f"'{gcp_crs}' is not a valid/recognized CRS: {exc}") from None

    raw_bytes = b""
    chunk = await upload.read(1024 * 1024)
    while chunk:
        raw_bytes += chunk
        if len(raw_bytes) > settings.max_upload_size_bytes:
            raise PayloadTooLargeError(
                f"File exceeds the maximum allowed size of {settings.MAX_UPLOAD_SIZE_MB} MB"
            )
        chunk = await upload.read(1024 * 1024)

    try:
        raw_text = raw_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ValidationAppError("GCP CSV must be UTF-8 encoded text.") from None

    original_filename = Path(upload.filename or "gcp.csv").name

    dataset = Dataset(
        project_id=project_id,
        original_filename=original_filename,
        storage_key="",
        file_type="csv",
        mime_type="text/csv",
        file_size_bytes=len(raw_bytes),
        role=DatasetRole.GCP_REFERENCE,
        status=DatasetStatus.UPLOADED,
    )
    db.add(dataset)
    await db.flush()

    storage_key = f"projects/{project_id}/datasets/{dataset.id}/original.csv"
    dataset.storage_key = storage_key
    with storage.open_writer(storage_key) as writer:
        writer.write(raw_bytes)

    try:
        points = parse_gcp_csv(raw_text)
        if len(points) < settings.MIN_GCP_POINTS:
            raise ValidationAppError(
                f"GCP CSV has {len(points)} point(s); at least "
                f"{settings.MIN_GCP_POINTS} are required for calibration."
            )
    except ValidationAppError as exc:
        dataset.status = DatasetStatus.INVALID
        dataset.validation_error = str(exc)
    except Exception:
        logger.exception("Unexpected error validating GCP dataset %s", dataset.id)
        dataset.status = DatasetStatus.FAILED
        dataset.validation_error = "Internal error while validating this GCP reference."
    else:
        dataset.status = DatasetStatus.VALID
        dataset.gcp_crs = gcp_crs
        dataset.gcp_points = points

    await db.commit()
    await db.refresh(dataset)
    return dataset
