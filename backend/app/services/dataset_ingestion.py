"""Real dataset ingestion: streamed upload, server-side format detection,
storage, and raster metadata validation.

No step here fabricates a result: a file is only ever marked `valid` because
`geospatial.raster_metadata` actually opened it and read real structural
metadata from it.
"""

import logging
import uuid
from pathlib import Path

from fastapi import UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import PayloadTooLargeError, ValidationAppError
from app.core.storage import get_storage
from app.models.dataset import Dataset, DatasetRole, DatasetStatus
from geospatial.exceptions import RasterValidationError
from geospatial.raster_metadata import extract_raster_metadata

logger = logging.getLogger("terrainx.backend.datasets")

CHUNK_SIZE = 1024 * 1024  # 1 MiB — bounds how much of the upload is in memory at once.

# (magic bytes prefix, normalized file_type, server-determined mime_type).
# TIFF and GeoTIFF share the same magic bytes; whether a TIFF is actually
# georeferenced is decided later, for real, by geospatial.raster_metadata —
# never inferred from the extension or this signature match.
_MAGIC_SIGNATURES: list[tuple[bytes, str, str]] = [
    (b"\xff\xd8\xff", "jpeg", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "png", "image/png"),
    (b"II*\x00", "tiff", "image/tiff"),
    (b"MM\x00*", "tiff", "image/tiff"),
]

_EXTENSION_BY_TYPE = {"jpeg": "jpg", "png": "png", "tiff": "tiff"}


def sniff_format(header: bytes) -> tuple[str, str] | None:
    """Detect the real file format from its leading bytes.

    Never trusts the client-supplied filename or Content-Type: returns None
    if the header doesn't match a supported format's magic bytes.
    """
    for signature, file_type, mime_type in _MAGIC_SIGNATURES:
        if header.startswith(signature):
            return file_type, mime_type
    return None


async def ingest_uploaded_file(
    upload: UploadFile,
    project_id: uuid.UUID,
    db: AsyncSession,
    role: DatasetRole = DatasetRole.SOURCE_IMAGE,
) -> Dataset:
    settings = get_settings()
    storage = get_storage()

    header = await upload.read(CHUNK_SIZE)
    detected = sniff_format(header)
    if detected is None:
        raise ValidationAppError(
            "Unsupported or unrecognized file format. "
            "Supported formats: JPEG, PNG, TIFF/GeoTIFF."
        )
    file_type, mime_type = detected
    original_filename = Path(upload.filename or "upload").name

    dataset = Dataset(
        project_id=project_id,
        original_filename=original_filename,
        storage_key="",
        file_type=file_type,
        mime_type=mime_type,
        file_size_bytes=0,
        role=role,
        status=DatasetStatus.UPLOADED,
    )
    db.add(dataset)
    await db.flush()  # assigns dataset.id without committing the transaction yet

    extension = _EXTENSION_BY_TYPE[file_type]
    storage_key = f"projects/{project_id}/datasets/{dataset.id}/original.{extension}"
    dataset.storage_key = storage_key

    total_bytes = 0
    try:
        with storage.open_writer(storage_key) as writer:
            chunk = header
            while chunk:
                total_bytes += len(chunk)
                if total_bytes > settings.max_upload_size_bytes:
                    raise PayloadTooLargeError(
                        f"File exceeds the maximum allowed size of "
                        f"{settings.MAX_UPLOAD_SIZE_MB} MB"
                    )
                writer.write(chunk)
                chunk = await upload.read(CHUNK_SIZE)
    except PayloadTooLargeError:
        storage.delete(storage_key)
        await db.rollback()
        raise

    dataset.file_size_bytes = total_bytes
    dataset.status = DatasetStatus.VALIDATING
    await db.flush()

    absolute_path = storage.absolute_path(storage_key)
    try:
        metadata = extract_raster_metadata(absolute_path)
    except RasterValidationError as exc:
        dataset.status = DatasetStatus.INVALID
        dataset.validation_error = str(exc)
    except Exception:
        logger.exception("Unexpected error validating dataset %s", dataset.id)
        dataset.status = DatasetStatus.FAILED
        dataset.validation_error = "Internal error while validating this dataset."
    else:
        dataset.width = metadata.width
        dataset.height = metadata.height
        dataset.bands = metadata.bands
        dataset.is_georeferenced = metadata.is_georeferenced
        dataset.crs = metadata.crs
        if metadata.bbox is not None:
            dataset.bbox_min_x = metadata.bbox.min_x
            dataset.bbox_min_y = metadata.bbox.min_y
            dataset.bbox_max_x = metadata.bbox.max_x
            dataset.bbox_max_y = metadata.bbox.max_y

        if role == DatasetRole.DEM_REFERENCE and not metadata.is_georeferenced:
            # A DEM with no CRS can never be used for calibration (there is
            # no way to know which real-world location any elevation value
            # belongs to) — reject at upload time rather than discovering
            # this only when a calibration job later tries to use it.
            dataset.status = DatasetStatus.INVALID
            dataset.validation_error = (
                "A DEM reference must be georeferenced (have a valid CRS); this file has none."
            )
        else:
            dataset.status = DatasetStatus.VALID

    await db.commit()
    await db.refresh(dataset)
    return dataset
