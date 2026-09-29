"""Phase 3: real monocular depth estimation pipeline tests.

Covers both fast unit-style tests of the input-eligibility policy
(`app.services.depth_pipeline.extract_rgb_uint8`, no model involved) and real
integration tests that go through the actual separate worker container,
Redis/RQ, and the real Depth Anything V2 model — no mocking of inference
itself in the integration tests below. Cancellation-around-inference tests
use a lightweight fake DepthEstimator (see docstrings) since they need
precise timing control that racing the real model wouldn't reliably give.
"""

import asyncio
import uuid

import numpy as np
import pytest
import rasterio

from ai.depth_anything import MODEL_NAME, MODEL_REVISION
from app.core.storage import get_storage
from app.db.session import AsyncSessionLocal
from app.models.analysis_artifact import AnalysisArtifact
from app.models.analysis_job import AnalysisJob, AnalysisJobStatus
from app.services import analysis_execution
from app.services.depth_pipeline import UnsupportedDepthInputError, extract_rgb_uint8
from geospatial.raster_io import read_raster_array
from tests.fixtures import (
    make_four_band_tiff_bytes,
    make_jpeg_bytes,
    make_plain_tiff_bytes,
    make_png_bytes,
    make_structured_scene_geotiff_bytes,
    make_structured_scene_jpeg_bytes,
    make_uint16_rgb_tiff_bytes,
)


async def _register_and_login(client, email: str, password: str = "supersecret123") -> dict:
    await client.post("/api/v1/auth/register", json={"email": email, "password": password})
    resp = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


async def _create_project(client, headers: dict, name: str = "Depth Test Project") -> str:
    resp = await client.post("/api/v1/projects", json={"name": name}, headers=headers)
    return resp.json()["id"]


async def _upload(
    client, headers: dict, project_id: str, filename: str, content: bytes, mime: str
) -> dict:
    resp = await client.post(
        f"/api/v1/projects/{project_id}/datasets",
        files={"file": (filename, content, mime)},
        headers=headers,
    )
    return resp.json()


async def _create_job(client, headers, project_id, dataset_id):
    resp = await client.post(
        f"/api/v1/projects/{project_id}/datasets/{dataset_id}/analysis",
        json={},
        headers=headers,
    )
    return resp.json()


async def _wait_for_terminal(client, project_id, job_id, headers, timeout=120.0) -> dict:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        resp = await client.get(f"/api/v1/projects/{project_id}/analysis/{job_id}", headers=headers)
        body = resp.json()
        if body["status"] not in ("queued", "running"):
            return body
        await asyncio.sleep(0.2)
    raise AssertionError(f"Job {job_id} did not reach a terminal status within {timeout}s")


# --------------------------------------------------------------------------
# Unit tests: input-eligibility policy (no model, fast)
# --------------------------------------------------------------------------


def _raster_array(bands: int, dtype: str, height: int = 4, width: int = 4):
    from geospatial.raster_io import RasterArray

    data = np.zeros((bands, height, width), dtype=dtype)
    return RasterArray(data=data, dtype=dtype, crs=None, transform=None, is_georeferenced=False)


def test_extract_rgb_uint8_accepts_3band_uint8():
    raster = _raster_array(bands=3, dtype="uint8")
    result = extract_rgb_uint8(raster, "jpeg")
    assert result.shape == (4, 4, 3)
    assert result.dtype == np.uint8


def test_extract_rgb_uint8_drops_alpha_for_4band_png():
    raster = _raster_array(bands=4, dtype="uint8")
    result = extract_rgb_uint8(raster, "png")
    assert result.shape == (4, 4, 3)


def test_extract_rgb_uint8_rejects_1band_grayscale():
    raster = _raster_array(bands=1, dtype="uint8")
    with pytest.raises(UnsupportedDepthInputError, match="1 band"):
        extract_rgb_uint8(raster, "tiff")


def test_extract_rgb_uint8_rejects_4band_tiff():
    """Unlike PNG, a 4-band TIFF is ambiguous (alpha vs. real spectral data)
    and must be rejected, not guessed at."""
    raster = _raster_array(bands=4, dtype="uint8")
    with pytest.raises(UnsupportedDepthInputError, match="4 band"):
        extract_rgb_uint8(raster, "tiff")


def test_extract_rgb_uint8_rejects_non_uint8_dtype():
    raster = _raster_array(bands=3, dtype="uint16")
    with pytest.raises(UnsupportedDepthInputError, match="uint16"):
        extract_rgb_uint8(raster, "tiff")


# --------------------------------------------------------------------------
# Phase 11: model revision must be an immutable commit SHA, never a moving
# branch/tag reference such as "main" or "master".
# --------------------------------------------------------------------------


def test_model_revision_is_an_immutable_commit_sha_not_a_moving_branch():
    assert MODEL_REVISION not in ("main", "master", "latest", "")
    assert len(MODEL_REVISION) == 40
    assert all(c in "0123456789abcdef" for c in MODEL_REVISION.lower())


# --------------------------------------------------------------------------
# Integration tests: real model, real worker, real Redis/RQ
# --------------------------------------------------------------------------


async def test_structured_rgb_jpeg_produces_real_nonconstant_depth(client):
    headers = await _register_and_login(client, "depth-owner1@example.com")
    project_id = await _create_project(client, headers)
    dataset = await _upload(
        client, headers, project_id, "scene.jpg", make_structured_scene_jpeg_bytes(), "image/jpeg"
    )
    assert dataset["status"] == "valid"

    job = await _create_job(client, headers, project_id, dataset["id"])
    final = await _wait_for_terminal(client, project_id, job["id"], headers)

    assert final["status"] == "completed"
    depth_summary = final["execution_summary"]["depth_estimation"]
    assert depth_summary["output_width"] == dataset["width"]
    assert depth_summary["output_height"] == dataset["height"]
    assert depth_summary["model_name"] == MODEL_NAME
    assert depth_summary["model_revision"] == MODEL_REVISION

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            AnalysisArtifact.__table__.select().where(
                AnalysisArtifact.analysis_job_id == uuid.UUID(job["id"])
            )
        )
        artifact_row = result.mappings().one()

    storage = get_storage()
    absolute_path = storage.absolute_path(artifact_row["storage_key"])
    with rasterio.open(absolute_path) as src:
        assert src.count == 1
        assert src.dtypes[0] == "float32"
        depth_array = src.read(1)

    # A structured scene (foreground disc against a receding gradient) must
    # not produce a perfectly flat prediction — real inference over real
    # spatial structure genuinely varies across the image.
    assert float(depth_array.std()) > 0.0
    assert (
        artifact_row["artifact_metadata"]["depth_min"]
        < artifact_row["artifact_metadata"]["depth_max"]
    )


async def test_png_with_alpha_channel_is_accepted(client):
    headers = await _register_and_login(client, "depth-owner2@example.com")
    project_id = await _create_project(client, headers)
    dataset = await _upload(client, headers, project_id, "scene.png", make_png_bytes(), "image/png")
    assert dataset["bands"] == 4  # RGBA as stored

    job = await _create_job(client, headers, project_id, dataset["id"])
    final = await _wait_for_terminal(client, project_id, job["id"], headers)

    assert final["status"] == "completed"


async def test_georeferenced_geotiff_preserves_crs_on_depth_artifact(client):
    headers = await _register_and_login(client, "depth-owner3@example.com")
    project_id = await _create_project(client, headers)
    dataset = await _upload(
        client,
        headers,
        project_id,
        "scene.tif",
        make_structured_scene_geotiff_bytes(crs="EPSG:4326"),
        "image/tiff",
    )
    assert dataset["is_georeferenced"] is True
    assert dataset["crs"] == "EPSG:4326"

    job = await _create_job(client, headers, project_id, dataset["id"])
    final = await _wait_for_terminal(client, project_id, job["id"], headers)
    assert final["status"] == "completed"

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            AnalysisArtifact.__table__.select().where(
                AnalysisArtifact.analysis_job_id == uuid.UUID(job["id"])
            )
        )
        artifact_row = result.mappings().one()

    assert artifact_row["artifact_metadata"]["is_georeferenced"] is True
    assert artifact_row["artifact_metadata"]["crs"] == "EPSG:4326"

    storage = get_storage()
    output_raster = read_raster_array(storage.absolute_path(artifact_row["storage_key"]))
    assert output_raster.is_georeferenced is True
    assert output_raster.crs.to_epsg() == 4326


async def test_non_georeferenced_input_never_gets_fabricated_crs(client):
    headers = await _register_and_login(client, "depth-owner4@example.com")
    project_id = await _create_project(client, headers)
    dataset = await _upload(
        client, headers, project_id, "photo.jpg", make_jpeg_bytes(), "image/jpeg"
    )
    assert dataset["is_georeferenced"] is False

    job = await _create_job(client, headers, project_id, dataset["id"])
    final = await _wait_for_terminal(client, project_id, job["id"], headers)
    assert final["status"] == "completed"

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            AnalysisArtifact.__table__.select().where(
                AnalysisArtifact.analysis_job_id == uuid.UUID(job["id"])
            )
        )
        artifact_row = result.mappings().one()

    assert artifact_row["artifact_metadata"]["is_georeferenced"] is False
    assert artifact_row["artifact_metadata"]["crs"] is None

    storage = get_storage()
    output_raster = read_raster_array(storage.absolute_path(artifact_row["storage_key"]))
    assert output_raster.is_georeferenced is False
    assert output_raster.crs is None


@pytest.mark.parametrize(
    "email_slug,filename,content,mime,expected_message_fragment",
    [
        ("gray", "gray.tif", make_plain_tiff_bytes(), "image/tiff", "1 band"),
        ("multi", "multi.tif", make_four_band_tiff_bytes(), "image/tiff", "4 band"),
        ("sixteen", "sixteen.tif", make_uint16_rgb_tiff_bytes(), "image/tiff", "uint16"),
    ],
)
async def test_unsupported_input_configurations_fail_the_job_not_the_dataset(
    client, email_slug, filename, content, mime, expected_message_fragment
):
    """These are all structurally valid rasters (Phase 1 marks the dataset
    'valid'); the rejection is Phase 3's own depth-specific policy, which
    only applies once the job actually runs — so job creation succeeds
    (202/201, queued) and the *job* fails with a clear message, exactly like
    any other real, expected pipeline failure."""
    headers = await _register_and_login(client, f"depth-reject-{email_slug}@example.com")
    project_id = await _create_project(client, headers)
    dataset = await _upload(client, headers, project_id, filename, content, mime)
    assert dataset["status"] == "valid"

    job = await _create_job(client, headers, project_id, dataset["id"])
    assert job["status"] in ("queued", "running")

    final = await _wait_for_terminal(client, project_id, job["id"], headers)
    assert final["status"] == "failed"
    assert expected_message_fragment in final["error_message"]

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            AnalysisArtifact.__table__.select().where(
                AnalysisArtifact.analysis_job_id == uuid.UUID(job["id"])
            )
        )
        assert result.mappings().all() == []


async def test_oversized_image_rejected_before_model_work(client):
    import io

    from PIL import Image

    headers = await _register_and_login(client, "depth-oversized@example.com")
    project_id = await _create_project(client, headers)

    buf = io.BytesIO()
    Image.new("RGB", (4200, 2), color=(10, 20, 30)).save(buf, format="JPEG")
    dataset = await _upload(client, headers, project_id, "huge.jpg", buf.getvalue(), "image/jpeg")
    assert dataset["status"] == "valid"

    job = await _create_job(client, headers, project_id, dataset["id"])
    final = await _wait_for_terminal(client, project_id, job["id"], headers)
    assert final["status"] == "failed"
    assert "exceed the maximum" in final["error_message"]


# --------------------------------------------------------------------------
# Artifact API
# --------------------------------------------------------------------------


async def test_list_and_download_artifact_via_api(client):
    headers = await _register_and_login(client, "depth-artifact-api@example.com")
    project_id = await _create_project(client, headers)
    dataset = await _upload(
        client, headers, project_id, "photo.jpg", make_jpeg_bytes(), "image/jpeg"
    )
    job = await _create_job(client, headers, project_id, dataset["id"])
    final = await _wait_for_terminal(client, project_id, job["id"], headers)
    assert final["status"] == "completed"

    list_resp = await client.get(
        f"/api/v1/projects/{project_id}/analysis/{job['id']}/artifacts", headers=headers
    )
    assert list_resp.status_code == 200
    artifacts = list_resp.json()
    assert len(artifacts) == 1
    artifact = artifacts[0]
    assert artifact["artifact_type"] == "relative_depth"
    assert "storage_key" not in artifact  # internal path never exposed

    download_resp = await client.get(
        f"/api/v1/projects/{project_id}/analysis/{job['id']}/artifacts/{artifact['id']}/download",
        headers=headers,
    )
    assert download_resp.status_code == 200
    assert download_resp.headers["content-type"] == "image/tiff"

    # Downloaded bytes are a real, readable single-band float32 GeoTIFF.
    import io as _io

    with rasterio.MemoryFile(_io.BytesIO(download_resp.content)) as memfile, memfile.open() as src:
        assert src.count == 1
        assert src.dtypes[0] == "float32"


async def test_cross_user_cannot_list_or_download_artifact(client):
    headers_owner = await _register_and_login(client, "depth-artifact-owner@example.com")
    headers_intruder = await _register_and_login(client, "depth-artifact-intruder@example.com")
    project_id = await _create_project(client, headers_owner)
    dataset = await _upload(
        client, headers_owner, project_id, "photo.jpg", make_jpeg_bytes(), "image/jpeg"
    )
    job = await _create_job(client, headers_owner, project_id, dataset["id"])
    final = await _wait_for_terminal(client, project_id, job["id"], headers_owner)
    assert final["status"] == "completed"

    artifact_id = (
        await client.get(
            f"/api/v1/projects/{project_id}/analysis/{job['id']}/artifacts", headers=headers_owner
        )
    ).json()[0]["id"]

    list_resp = await client.get(
        f"/api/v1/projects/{project_id}/analysis/{job['id']}/artifacts", headers=headers_intruder
    )
    assert list_resp.status_code == 404

    download_resp = await client.get(
        f"/api/v1/projects/{project_id}/analysis/{job['id']}/artifacts/{artifact_id}/download",
        headers=headers_intruder,
    )
    assert download_resp.status_code == 404


# --------------------------------------------------------------------------
# Cancellation around the expensive inference step
# --------------------------------------------------------------------------


class _SlowFakeEstimator:
    """A DepthEstimator stand-in with controllable delays and call tracking,
    used only to test the cancellation-timing behavior precisely — racing
    the real model wouldn't give reliable enough timing control for this."""

    def __init__(self, load_delay: float = 0.0, predict_delay: float = 0.0):
        self.load_delay = load_delay
        self.predict_delay = predict_delay
        self.predict_called = False

    def load(self) -> None:
        import time

        time.sleep(self.load_delay)

    def predict(self, rgb_image):
        import time

        from ai.depth_estimator import DepthPrediction

        self.predict_called = True
        time.sleep(self.predict_delay)
        height, width = rgb_image.shape[0], rgb_image.shape[1]
        return DepthPrediction(
            depth=np.zeros((height, width), dtype=np.float32),
            inference_seconds=self.predict_delay,
            input_width=width,
            input_height=height,
            model_input_width=width,
            model_input_height=height,
        )

    def info(self):
        from ai.depth_estimator import DepthModelInfo

        return DepthModelInfo(
            name="fake", revision="n/a", source="n/a", license="n/a", device="cpu"
        )


async def _insert_queued_job(client, headers, project_id, dataset_id) -> uuid.UUID:
    user_id = (await client.get("/api/v1/auth/me", headers=headers)).json()["id"]
    async with AsyncSessionLocal() as db:
        job = AnalysisJob(
            project_id=uuid.UUID(project_id),
            dataset_id=uuid.UUID(dataset_id),
            user_id=uuid.UUID(user_id),
            status=AnalysisJobStatus.QUEUED,
            parameters={"version": "v1"},
        )
        db.add(job)
        await db.commit()
        return job.id


async def test_cancellation_before_inference_prevents_inference(client, monkeypatch):
    """A job cancelled while the (slow, fake) model is loading — i.e. before
    inference starts — must never call predict() at all, and must end up
    'cancelled', never 'completed'."""
    from sqlalchemy import update as sa_update

    headers = await _register_and_login(client, "depth-cancel1@example.com")
    project_id = await _create_project(client, headers)
    dataset = await _upload(
        client, headers, project_id, "photo.jpg", make_jpeg_bytes(), "image/jpeg"
    )
    job_id = await _insert_queued_job(client, headers, project_id, dataset["id"])

    fake_estimator = _SlowFakeEstimator(load_delay=0.4)
    monkeypatch.setattr(analysis_execution, "get_depth_estimator", lambda: fake_estimator)

    async def cancel_shortly_after_start():
        await asyncio.sleep(0.1)  # let the job claim itself and start loading the model
        async with AsyncSessionLocal() as db:
            await db.execute(
                sa_update(AnalysisJob)
                .where(AnalysisJob.id == job_id)
                .values(status=AnalysisJobStatus.CANCELLED)
            )
            await db.commit()

    await asyncio.gather(
        cancel_shortly_after_start(), analysis_execution.execute_analysis_job(job_id)
    )

    assert fake_estimator.predict_called is False
    async with AsyncSessionLocal() as db:
        final = await db.get(AnalysisJob, job_id)
        assert final.status == AnalysisJobStatus.CANCELLED
        assert final.execution_summary is None


class _CancelDuringPredictEstimator:
    """A DepthEstimator stand-in that cancels the job itself, synchronously,
    from inside predict() — via a genuinely separate database connection —
    before returning a (real-shaped) prediction.

    This deliberately does not use asyncio concurrency (e.g. gather with a
    sibling task sleeping) to simulate "cancelled while inference is
    in-flight": predict() is a blocking, synchronous call (matching the real
    model), so it monopolizes the single event loop for its duration and no
    sibling coroutine can interleave during it — exactly why cancellation
    can't safely interrupt real in-flight inference either (the documented
    limitation this test exists to verify the *handling* of). Performing the
    cancellation from a separate connection inside predict() itself models
    "an external process cancelled this job while inference was running"
    deterministically, without depending on any timing assumption about how
    long the earlier pipeline stages' real DB round-trips take.
    """

    def __init__(self, job_id: uuid.UUID):
        self.job_id = job_id
        self.predict_called = False

    def load(self) -> None:
        pass

    def predict(self, rgb_image):
        from sqlalchemy import create_engine, text

        from ai.depth_estimator import DepthPrediction
        from app.core.config import get_settings

        self.predict_called = True

        engine = create_engine(get_settings().DATABASE_URL_SYNC)
        try:
            with engine.connect() as conn:
                conn.execute(
                    text("UPDATE analysis_jobs SET status = 'cancelled' WHERE id = :id"),
                    {"id": str(self.job_id)},
                )
                conn.commit()
        finally:
            engine.dispose()

        height, width = rgb_image.shape[0], rgb_image.shape[1]
        return DepthPrediction(
            depth=np.zeros((height, width), dtype=np.float32),
            inference_seconds=0.01,
            input_width=width,
            input_height=height,
            model_input_width=width,
            model_input_height=height,
        )

    def info(self):
        from ai.depth_estimator import DepthModelInfo

        return DepthModelInfo(
            name="fake", revision="n/a", source="n/a", license="n/a", device="cpu"
        )


async def test_cancellation_during_inference_does_not_produce_completed(client, monkeypatch):
    """Cancellation cannot safely interrupt an in-flight model forward pass
    (documented limitation — see docs/ARCHITECTURE.md), but once inference
    returns, the pipeline must notice the cancellation before persisting
    anything: no artifact, and status must never become 'completed'."""
    headers = await _register_and_login(client, "depth-cancel2@example.com")
    project_id = await _create_project(client, headers)
    dataset = await _upload(
        client, headers, project_id, "photo.jpg", make_jpeg_bytes(), "image/jpeg"
    )
    job_id = await _insert_queued_job(client, headers, project_id, dataset["id"])

    fake_estimator = _CancelDuringPredictEstimator(job_id)
    monkeypatch.setattr(analysis_execution, "get_depth_estimator", lambda: fake_estimator)

    await analysis_execution.execute_analysis_job(job_id)

    assert fake_estimator.predict_called is True  # inference did run to completion (undisturbed)
    async with AsyncSessionLocal() as db:
        final = await db.get(AnalysisJob, job_id)
        assert final.status == AnalysisJobStatus.CANCELLED  # never overwritten to completed
        assert final.execution_summary is None
        result = await db.execute(
            AnalysisArtifact.__table__.select().where(AnalysisArtifact.analysis_job_id == job_id)
        )
        assert result.mappings().all() == []  # the (discarded) prediction was never persisted
