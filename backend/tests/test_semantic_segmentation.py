"""Phase 6: real class-agnostic region segmentation (MobileSAM) tests.

Covers fast unit tests of the pure model/geospatial modules (checkpoint
verification, deterministic rasterization policy, categorical raster I/O,
image-quality metrics — no backend, no network, no real weights needed for
most of these), the soft-failure job-orchestration contract
(`app.services.semantic_pipeline`), real integration tests through
`analysis_execution.execute_analysis_job` (including one that runs genuine
MobileSAM inference — no mocking of the segmentation result itself), and the
categorical visualization API (ownership, metadata, preview, pixel value) —
mirroring the conventions already established in test_depth_pipeline.py,
test_calibration.py, and test_visualization.py.
"""

import subprocess
import uuid
from datetime import UTC, datetime

import numpy as np
import pytest
import rasterio
from PIL import Image
from sqlalchemy import create_engine, text

from ai.exceptions import InferenceError, ModelLoadError, UnsupportedInputError
from ai.mobile_sam import (
    CHECKPOINT_EXPECTED_SIZE_BYTES,
    CHECKPOINT_FILENAME,
    MODEL_LICENSE,
    MODEL_REPOSITORY,
    MODEL_REVISION,
    MODEL_TASK,
    MobileSAMEstimator,
    _cache_dir,
    _ensure_checkpoint,
    _git_blob_sha1,
    _verify_checkpoint,
)
from ai.semantic_estimator import RegionInfo, SemanticModelInfo, SemanticPrediction
from app.core.config import get_settings
from app.db.session import AsyncSessionLocal
from app.models.analysis_artifact import AnalysisArtifact
from app.models.analysis_job import AnalysisJob, AnalysisJobStatus, SemanticStatus
from app.services import analysis_execution, semantic_pipeline
from app.services.semantic_pipeline import (
    REGION_VALUE_SEMANTICS,
    SemanticOutcome,
    run_semantic_segmentation,
)
from geospatial.image_quality import compute_image_quality
from geospatial.raster_preview import _region_color
from tests.fixtures import make_jpeg_bytes, make_structured_scene_jpeg_bytes

# --------------------------------------------------------------------------
# Shared helpers (mirrors test_depth_pipeline.py / test_visualization.py)
# --------------------------------------------------------------------------


async def _register_and_login(client, email: str, password: str = "supersecret123") -> dict:
    await client.post("/api/v1/auth/register", json={"email": email, "password": password})
    resp = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


async def _create_project(client, headers: dict, name: str = "Semantic Test Project") -> str:
    resp = await client.post("/api/v1/projects", json={"name": name}, headers=headers)
    return resp.json()["id"]


async def _current_user_id(client, headers: dict) -> str:
    return (await client.get("/api/v1/auth/me", headers=headers)).json()["id"]


async def _upload(client, headers, project_id, filename, content, mime) -> dict:
    resp = await client.post(
        f"/api/v1/projects/{project_id}/datasets",
        files={"file": (filename, content, mime)},
        headers=headers,
    )
    return resp.json()


async def _create_job_with_semantic(client, headers, project_id, dataset_id) -> dict:
    resp = await client.post(
        f"/api/v1/projects/{project_id}/datasets/{dataset_id}/analysis",
        json={"parameters": {"version": "v1", "enable_semantic_segmentation": True}},
        headers=headers,
    )
    return resp.json()


async def _insert_queued_job(client, headers, project_id, dataset_id, *, enable_semantic=True):
    user_id = await _current_user_id(client, headers)
    async with AsyncSessionLocal() as db:
        job = AnalysisJob(
            project_id=uuid.UUID(project_id),
            dataset_id=uuid.UUID(dataset_id),
            user_id=uuid.UUID(user_id),
            status=AnalysisJobStatus.QUEUED,
            parameters={"version": "v1", "enable_semantic_segmentation": enable_semantic},
        )
        db.add(job)
        await db.commit()
        return job.id


async def _create_job_row(project_id, dataset_id, user_id, **kwargs) -> uuid.UUID:
    status = kwargs.pop("status", AnalysisJobStatus.COMPLETED)
    async with AsyncSessionLocal() as db:
        job = AnalysisJob(
            project_id=uuid.UUID(project_id),
            dataset_id=uuid.UUID(dataset_id),
            user_id=uuid.UUID(user_id),
            status=status,
            parameters={"version": "v1"},
            completed_at=datetime.now(UTC) if status == AnalysisJobStatus.COMPLETED else None,
            **kwargs,
        )
        db.add(job)
        await db.commit()
        return job.id


async def _create_artifact_row(job_id, artifact_type, storage_key, file_size, metadata=None):
    async with AsyncSessionLocal() as db:
        artifact = AnalysisArtifact(
            analysis_job_id=job_id,
            artifact_type=artifact_type,
            storage_key=storage_key,
            mime_type="image/tiff",
            file_size_bytes=file_size,
            artifact_metadata=metadata,
        )
        db.add(artifact)
        await db.commit()
        return artifact.id


def _write_categorical_raster(storage_key, array, *, crs=None, transform=None, nodata=0):
    from app.core.storage import get_storage

    storage = get_storage()
    path = storage.absolute_path(storage_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    profile = {
        "driver": "GTiff",
        "height": array.shape[0],
        "width": array.shape[1],
        "count": 1,
        "dtype": "uint32",
        "nodata": nodata,
    }
    if crs is not None and transform is not None:
        profile["crs"] = crs
        profile["transform"] = transform
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(array.astype("uint32"), 1)
    return path.stat().st_size


class _FakeMaskGenerator:
    """Stands in for `SamAutomaticMaskGenerator` — always returns the same
    fixed, real-shaped mask list regardless of input, so the deterministic
    overlap/sort/rasterization policy in `MobileSAMEstimator.predict` can be
    tested precisely without real weights or a ~45s forward pass."""

    def __init__(self, masks: list[dict]):
        self._masks = masks
        self.call_count = 0

    def generate(self, rgb_image):
        self.call_count += 1
        return self._masks


def _mask(segmentation: np.ndarray, bbox, *, stability_score=None, predicted_iou=None) -> dict:
    mask = {"segmentation": segmentation, "area": int(segmentation.sum()), "bbox": bbox}
    if stability_score is not None:
        mask["stability_score"] = stability_score
    if predicted_iou is not None:
        mask["predicted_iou"] = predicted_iou
    return mask


# --------------------------------------------------------------------------
# Unit tests: ai/mobile_sam.py — checkpoint verification (no network)
# --------------------------------------------------------------------------


def test_git_blob_sha1_matches_real_git_hash_object():
    """Independent verification of `_git_blob_sha1` against the real `git
    hash-object` command (git is installed in this image specifically for
    MobileSAM's pip git-URL install) — not just re-deriving the same formula
    in the test, which would prove nothing."""
    content = b"TerrainX Phase 6 checkpoint verification test content\n"
    expected = (
        subprocess.run(
            ["git", "hash-object", "--stdin"], input=content, capture_output=True, check=True
        )
        .stdout.decode()
        .strip()
    )
    assert _git_blob_sha1(content) == expected


def test_verify_checkpoint_rejects_missing_file(tmp_path):
    assert _verify_checkpoint(tmp_path / "does_not_exist.pt") is False


def test_verify_checkpoint_rejects_wrong_size(tmp_path):
    path = tmp_path / "mobile_sam.pt"
    path.write_bytes(b"x" * 100)
    assert _verify_checkpoint(path) is False


def test_verify_checkpoint_rejects_right_size_wrong_hash(tmp_path):
    path = tmp_path / "mobile_sam.pt"
    path.write_bytes(b"\x00" * CHECKPOINT_EXPECTED_SIZE_BYTES)
    assert _verify_checkpoint(path) is False


def test_verify_checkpoint_accepts_real_cached_checkpoint_if_present():
    """If a real checkpoint has already been downloaded to the persistent
    cache (e.g. by an earlier real inference run), it must pass its own
    real integrity check — never merely "look right" without actually
    matching the pinned blob hash."""
    real_path = _cache_dir() / CHECKPOINT_FILENAME
    if not real_path.is_file():
        pytest.skip("No real cached MobileSAM checkpoint present in this environment")
    assert _verify_checkpoint(real_path) is True


def test_ensure_checkpoint_raises_model_load_error_when_download_fails(monkeypatch, tmp_path):
    """No cached checkpoint and no reachable network: a real, explicit
    ModelLoadError — never a fake/placeholder result."""
    monkeypatch.setattr("ai.mobile_sam._cache_dir", lambda: tmp_path / "empty_cache")

    def _boom(*args, **kwargs):
        raise OSError("simulated: no network access")

    monkeypatch.setattr("ai.mobile_sam.urllib.request.urlopen", _boom)

    with pytest.raises(ModelLoadError, match="no network access"):
        _ensure_checkpoint()


def test_ensure_checkpoint_raises_model_load_error_on_hash_mismatch(monkeypatch, tmp_path):
    """A downloaded file that doesn't match the pinned checkpoint's known-good
    hash must be refused outright, never silently accepted as "close enough"."""
    monkeypatch.setattr("ai.mobile_sam._cache_dir", lambda: tmp_path / "empty_cache2")

    class _FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b"\x00" * CHECKPOINT_EXPECTED_SIZE_BYTES  # right size, wrong content

    monkeypatch.setattr("ai.mobile_sam.urllib.request.urlopen", lambda *a, **k: _FakeResponse())

    with pytest.raises(ModelLoadError, match="does not match the pinned"):
        _ensure_checkpoint()
    # The corrupt download must not be left behind as if it were valid.
    assert not (tmp_path / "empty_cache2" / CHECKPOINT_FILENAME).exists()


# --------------------------------------------------------------------------
# Unit tests: MobileSAMEstimator.predict — deterministic rasterization policy
# --------------------------------------------------------------------------


def test_predict_before_load_raises_inference_error():
    estimator = MobileSAMEstimator()
    with pytest.raises(InferenceError, match="before load"):
        estimator.predict(np.zeros((4, 4, 3), dtype=np.uint8))


def test_predict_rejects_non_rgb_shape():
    estimator = MobileSAMEstimator()
    estimator._mask_generator = _FakeMaskGenerator([])
    with pytest.raises(UnsupportedInputError, match="shape"):
        estimator.predict(np.zeros((4, 4), dtype=np.uint8))


def test_predict_rejects_non_uint8_dtype():
    estimator = MobileSAMEstimator()
    estimator._mask_generator = _FakeMaskGenerator([])
    with pytest.raises(UnsupportedInputError, match="uint8"):
        estimator.predict(np.zeros((4, 4, 3), dtype=np.float32))


def test_predict_deterministic_overlap_policy_smaller_region_painted_on_top():
    """A big region and a small, fully-overlapping region: after
    rasterization, region 1 (larger) must be visible everywhere region 2
    (smaller) doesn't cover, and region 2 must win the overlap — the
    documented policy (see MobileSAMEstimator.predict's docstring)."""
    big = np.ones((10, 10), dtype=bool)
    small = np.zeros((10, 10), dtype=bool)
    small[0:3, 0:3] = True

    estimator = MobileSAMEstimator()
    estimator._mask_generator = _FakeMaskGenerator(
        [
            _mask(small, bbox=[0, 0, 3, 3], stability_score=0.80, predicted_iou=0.75),
            _mask(big, bbox=[0, 0, 10, 10], stability_score=0.95, predicted_iou=0.90),
        ]
    )

    prediction = estimator.predict(np.zeros((10, 10, 3), dtype=np.uint8))

    assert prediction.label_map.shape == (10, 10)
    assert prediction.label_map.dtype == np.uint32
    # region_id 1 = the larger mask (area 100), assigned by area descending
    # regardless of the fake generator's own list order.
    assert (prediction.label_map[3:, 3:] == 1).all()
    # region_id 2 = the smaller mask, painted last, wins the overlap.
    assert (prediction.label_map[0:3, 0:3] == 2).all()

    by_id = {r.region_id: r for r in prediction.regions}
    assert by_id[1].pixel_area == 100 - 9  # real post-overlap count, not raw 100
    assert by_id[2].pixel_area == 9
    assert by_id[1].mask_score == pytest.approx(0.95)
    assert by_id[2].predicted_iou == pytest.approx(0.75)
    # bbox still reflects each mask's own original extent, unshrunk by overlap.
    assert by_id[1].bbox_row_max == 10


def test_predict_reports_none_not_fabricated_when_model_gives_no_score():
    estimator = MobileSAMEstimator()
    mask = np.ones((4, 4), dtype=bool)
    estimator._mask_generator = _FakeMaskGenerator([_mask(mask, bbox=[0, 0, 4, 4])])

    prediction = estimator.predict(np.zeros((4, 4, 3), dtype=np.uint8))

    assert prediction.regions[0].mask_score is None
    assert prediction.regions[0].predicted_iou is None


def test_predict_filters_regions_below_min_area():
    kept = np.zeros((10, 10), dtype=bool)
    kept[0:4, 0:4] = True  # area 16
    dropped = np.zeros((10, 10), dtype=bool)
    dropped[9:10, 9:10] = True  # area 1

    estimator = MobileSAMEstimator()
    estimator._mask_generator = _FakeMaskGenerator(
        [_mask(kept, bbox=[0, 0, 4, 4]), _mask(dropped, bbox=[9, 9, 1, 1])]
    )

    prediction = estimator.predict(np.zeros((10, 10, 3), dtype=np.uint8), min_region_area_px=5)

    assert len(prediction.regions) == 1
    assert prediction.regions[0].pixel_area == 16
    assert (prediction.label_map == 2).sum() == 0  # the dropped region never painted


def test_predict_is_deterministic_across_repeated_calls():
    rng_masks = [
        _mask(np.tri(8, 8, dtype=bool), bbox=[0, 0, 8, 8], stability_score=0.9),
        _mask(np.eye(8, dtype=bool), bbox=[0, 0, 8, 8], stability_score=0.7),
    ]
    estimator = MobileSAMEstimator()
    estimator._mask_generator = _FakeMaskGenerator(rng_masks)

    image = np.zeros((8, 8, 3), dtype=np.uint8)
    first = estimator.predict(image)
    second = estimator.predict(image)

    assert np.array_equal(first.label_map, second.label_map)
    assert [r.region_id for r in first.regions] == [r.region_id for r in second.regions]


def test_info_reports_real_static_metadata_never_a_class_name():
    estimator = MobileSAMEstimator()
    info = estimator.info()
    assert info.repository == MODEL_REPOSITORY
    assert info.revision == MODEL_REVISION
    assert info.checkpoint == CHECKPOINT_FILENAME
    assert info.license == MODEL_LICENSE
    assert info.task == MODEL_TASK
    assert "class-agnostic" in info.task
    assert info.device in ("cpu", "cuda")
    for forbidden in ("building", "road", "vegetation", "water", "bridge"):
        assert forbidden not in info.task.lower()


# --------------------------------------------------------------------------
# Unit tests: geospatial.image_quality (real formulas, no model)
# --------------------------------------------------------------------------


def test_compute_image_quality_flat_image_has_zero_sharpness_and_full_valid_ratio():
    flat = np.full((32, 32, 3), 128, dtype=np.uint8)
    metrics = compute_image_quality(flat)
    assert metrics.sharpness_laplacian_variance == pytest.approx(0.0, abs=1e-6)
    assert metrics.valid_pixel_fraction == 1.0
    assert metrics.underexposed_fraction == 0.0
    assert metrics.overexposed_fraction == 0.0


def test_compute_image_quality_detects_underexposed_and_overexposed_pixels():
    image = np.full((10, 10, 3), 128, dtype=np.uint8)
    image[0:5, :, :] = 0  # half the image is pure black
    image[5:, :, :] = 255  # half is pure white
    metrics = compute_image_quality(image)
    assert metrics.underexposed_fraction == pytest.approx(0.5)
    assert metrics.overexposed_fraction == pytest.approx(0.5)


def test_compute_image_quality_checkerboard_has_higher_sharpness_than_flat():
    flat = np.full((16, 16, 3), 128, dtype=np.uint8)
    checker = np.indices((16, 16)).sum(axis=0) % 2 * 255
    checker_rgb = np.stack([checker] * 3, axis=-1).astype(np.uint8)
    flat_sharpness = compute_image_quality(flat).sharpness_laplacian_variance
    checker_sharpness = compute_image_quality(checker_rgb).sharpness_laplacian_variance
    assert checker_sharpness > flat_sharpness


def test_compute_image_quality_rejects_non_rgb_shape():
    with pytest.raises(ValueError, match="shape"):
        compute_image_quality(np.zeros((10, 10), dtype=np.uint8))


# --------------------------------------------------------------------------
# Unit tests: geospatial.raster_preview categorical color determinism
# --------------------------------------------------------------------------


def test_region_color_is_deterministic_and_distinct_for_different_ids():
    assert _region_color(1) == _region_color(1)
    assert _region_color(1) != _region_color(2)
    for region_id in (1, 2, 3, 4, 5):
        r, g, b = _region_color(region_id)
        assert all(0 <= c <= 255 for c in (r, g, b))


# --------------------------------------------------------------------------
# Unit tests: app.services.semantic_pipeline (soft-failure contract)
# --------------------------------------------------------------------------


class _FakeEstimatorInfo:
    def __init__(self):
        self.device = "cpu"

    def load(self):
        pass

    def info(self):
        return SemanticModelInfo(
            name="fake",
            task="class-agnostic region segmentation",
            repository="n/a",
            revision="n/a",
            checkpoint="n/a",
            license="n/a",
            device="cpu",
        )


class _FakeEstimatorLoadFails(_FakeEstimatorInfo):
    def load(self):
        raise ModelLoadError("simulated: checkpoint unavailable")


class _FakeEstimatorUnexpectedError(_FakeEstimatorInfo):
    def predict(self, rgb_image, *, min_region_area_px=0):
        raise RuntimeError("simulated unexpected failure")


class _FakeEstimatorSuccess(_FakeEstimatorInfo):
    def predict(self, rgb_image, *, min_region_area_px=0):
        label_map = np.zeros((4, 4), dtype=np.uint32)
        label_map[0:2, 0:2] = 1
        label_map[2:4, 2:4] = 2
        return SemanticPrediction(
            label_map=label_map,
            regions=[
                RegionInfo(1, 4, 0, 0, 2, 2, mask_score=0.9, predicted_iou=0.8),
                RegionInfo(2, 4, 2, 2, 4, 4, mask_score=None, predicted_iou=None),
            ],
            inference_seconds=0.01,
            input_width=4,
            input_height=4,
        )


def test_run_semantic_segmentation_returns_failed_outcome_on_model_load_error(monkeypatch):
    monkeypatch.setattr(
        semantic_pipeline, "get_semantic_estimator", lambda: _FakeEstimatorLoadFails()
    )
    outcome = run_semantic_segmentation(
        np.zeros((4, 4, 3), dtype=np.uint8), settings=get_settings()
    )
    assert outcome.status == SemanticStatus.FAILED
    assert "checkpoint unavailable" in outcome.metadata["error"]
    assert outcome.label_map is None


def test_run_semantic_segmentation_returns_failed_outcome_on_unexpected_error(monkeypatch):
    monkeypatch.setattr(
        semantic_pipeline, "get_semantic_estimator", lambda: _FakeEstimatorUnexpectedError()
    )
    outcome = run_semantic_segmentation(
        np.zeros((4, 4, 3), dtype=np.uint8), settings=get_settings()
    )
    assert outcome.status == SemanticStatus.FAILED
    assert "internal error" in outcome.metadata["error"].lower()


def test_run_semantic_segmentation_success_builds_honest_metadata(monkeypatch):
    monkeypatch.setattr(
        semantic_pipeline, "get_semantic_estimator", lambda: _FakeEstimatorSuccess()
    )

    outcome = run_semantic_segmentation(
        np.zeros((4, 4, 3), dtype=np.uint8), settings=get_settings()
    )

    assert outcome.status == SemanticStatus.COMPLETED
    assert outcome.metadata["region_count"] == 2
    assert outcome.metadata["value_semantics"] == REGION_VALUE_SEMANTICS
    # Mean computed only from the one region that actually reported a score.
    assert outcome.metadata["mask_stability_mean"] == pytest.approx(0.9)
    assert outcome.metadata["predicted_iou_mean"] == pytest.approx(0.8)
    region_ids = [r["region_id"] for r in outcome.metadata["regions"]]
    assert region_ids == [1, 2]
    assert outcome.metadata["regions"][1]["mask_score"] is None
    assert np.array_equal(
        outcome.label_map, np.array([[1, 1, 0, 0], [1, 1, 0, 0], [0, 0, 2, 2], [0, 0, 2, 2]])
    )


def test_run_semantic_segmentation_with_no_scores_reports_none_not_zero(monkeypatch):
    class _NoScoreEstimator(_FakeEstimatorInfo):
        def predict(self, rgb_image, *, min_region_area_px=0):
            return SemanticPrediction(
                label_map=np.zeros((2, 2), dtype=np.uint32),
                regions=[],
                inference_seconds=0.01,
                input_width=2,
                input_height=2,
            )

    monkeypatch.setattr(semantic_pipeline, "get_semantic_estimator", lambda: _NoScoreEstimator())
    outcome = run_semantic_segmentation(
        np.zeros((2, 2, 3), dtype=np.uint8), settings=get_settings()
    )
    assert outcome.metadata["mask_stability_mean"] is None
    assert outcome.metadata["region_count"] == 0


# --------------------------------------------------------------------------
# Integration tests: analysis_execution.execute_analysis_job (fast, fake
# depth + fake semantic estimator so these run in well under a second)
# --------------------------------------------------------------------------


class _FastFakeDepthEstimator:
    def load(self):
        pass

    def predict(self, rgb_image):
        from ai.depth_estimator import DepthPrediction

        height, width = rgb_image.shape[0], rgb_image.shape[1]
        return DepthPrediction(
            depth=np.ones((height, width), dtype=np.float32),
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


def _fake_semantic_success(rgb_image, *, settings):
    height, width = rgb_image.shape[0], rgb_image.shape[1]
    label_map = np.zeros((height, width), dtype=np.uint32)
    label_map[: height // 2, : width // 2] = 1
    return SemanticOutcome(
        status=SemanticStatus.COMPLETED,
        metadata={
            "model_name": "fake",
            "device": "cpu",
            "inference_seconds": 0.01,
            "region_count": 1,
            "regions": [
                {
                    "region_id": 1,
                    "pixel_area": int((label_map == 1).sum()),
                    "bbox_row_min": 0,
                    "bbox_col_min": 0,
                    "bbox_row_max": height // 2,
                    "bbox_col_max": width // 2,
                    "mask_score": 0.88,
                    "predicted_iou": None,
                }
            ],
            "mask_stability_mean": 0.88,
            "value_semantics": REGION_VALUE_SEMANTICS,
        },
        label_map=label_map,
    )


def _fake_semantic_failure(rgb_image, *, settings):
    return SemanticOutcome(
        status=SemanticStatus.FAILED, metadata={"error": "simulated segmentation failure"}
    )


async def test_disabled_by_default_leaves_semantic_not_requested(client, monkeypatch):
    monkeypatch.setattr(
        analysis_execution, "get_depth_estimator", lambda: _FastFakeDepthEstimator()
    )
    headers = await _register_and_login(client, "sem-disabled@example.com")
    project_id = await _create_project(client, headers)
    dataset = await _upload(client, headers, project_id, "p.jpg", make_jpeg_bytes(), "image/jpeg")
    job_id = await _insert_queued_job(
        client, headers, project_id, dataset["id"], enable_semantic=False
    )

    await analysis_execution.execute_analysis_job(job_id)

    async with AsyncSessionLocal() as db:
        job = await db.get(AnalysisJob, job_id)
        assert job.status == AnalysisJobStatus.COMPLETED
        assert job.semantic_status == SemanticStatus.NOT_REQUESTED
        assert job.semantic_metadata is None
        artifacts = (
            (
                await db.execute(
                    AnalysisArtifact.__table__.select().where(
                        AnalysisArtifact.analysis_job_id == job_id,
                        AnalysisArtifact.artifact_type == "semantic_segmentation",
                    )
                )
            )
            .mappings()
            .all()
        )
        assert artifacts == []


async def test_semantic_segmentation_completed_creates_real_artifact_with_provenance(
    client, monkeypatch
):
    monkeypatch.setattr(
        analysis_execution, "get_depth_estimator", lambda: _FastFakeDepthEstimator()
    )
    monkeypatch.setattr(analysis_execution, "run_semantic_segmentation", _fake_semantic_success)

    headers = await _register_and_login(client, "sem-success@example.com")
    project_id = await _create_project(client, headers)
    dataset = await _upload(client, headers, project_id, "p.jpg", make_jpeg_bytes(), "image/jpeg")
    job_id = await _insert_queued_job(client, headers, project_id, dataset["id"])

    await analysis_execution.execute_analysis_job(job_id)

    async with AsyncSessionLocal() as db:
        job = await db.get(AnalysisJob, job_id)
        assert job.status == AnalysisJobStatus.COMPLETED
        assert job.semantic_status == SemanticStatus.COMPLETED
        assert job.semantic_metadata["region_count"] == 1
        assert "semantic_artifact_id" in job.semantic_metadata

        rows = (
            (
                await db.execute(
                    AnalysisArtifact.__table__.select().where(
                        AnalysisArtifact.analysis_job_id == job_id,
                        AnalysisArtifact.artifact_type == "semantic_segmentation",
                    )
                )
            )
            .mappings()
            .all()
        )
        assert len(rows) == 1
        artifact = rows[0]
        assert artifact["artifact_metadata"]["display_label"] == "Distinct Surface Regions"
        assert artifact["artifact_metadata"]["source_dataset_id"] == dataset["id"]
        # Provenance traces to the real depth artifact of this same job.
        depth_rows = (
            (
                await db.execute(
                    AnalysisArtifact.__table__.select().where(
                        AnalysisArtifact.analysis_job_id == job_id,
                        AnalysisArtifact.artifact_type == "relative_depth",
                    )
                )
            )
            .mappings()
            .all()
        )
        assert artifact["artifact_metadata"]["depth_artifact_id"] == str(depth_rows[0]["id"])

        from app.core.storage import get_storage

        path = get_storage().absolute_path(artifact["storage_key"])
        with rasterio.open(path) as ds:
            assert ds.count == 1
            assert ds.dtypes[0] == "uint32"
            assert ds.nodata == 0


async def test_semantic_segmentation_soft_failure_does_not_fail_job_or_destroy_depth_artifact(
    client, monkeypatch
):
    monkeypatch.setattr(
        analysis_execution, "get_depth_estimator", lambda: _FastFakeDepthEstimator()
    )
    monkeypatch.setattr(analysis_execution, "run_semantic_segmentation", _fake_semantic_failure)

    headers = await _register_and_login(client, "sem-softfail@example.com")
    project_id = await _create_project(client, headers)
    dataset = await _upload(client, headers, project_id, "p.jpg", make_jpeg_bytes(), "image/jpeg")
    job_id = await _insert_queued_job(client, headers, project_id, dataset["id"])

    await analysis_execution.execute_analysis_job(job_id)

    async with AsyncSessionLocal() as db:
        job = await db.get(AnalysisJob, job_id)
        # The job as a whole still completed — a failed *optional* semantic
        # attempt never invalidates the real depth result.
        assert job.status == AnalysisJobStatus.COMPLETED
        assert job.semantic_status == SemanticStatus.FAILED
        assert job.semantic_metadata == {"error": "simulated segmentation failure"}

        depth_rows = (
            (
                await db.execute(
                    AnalysisArtifact.__table__.select().where(
                        AnalysisArtifact.analysis_job_id == job_id,
                        AnalysisArtifact.artifact_type == "relative_depth",
                    )
                )
            )
            .mappings()
            .all()
        )
        assert len(depth_rows) == 1  # the valid depth result survives

        semantic_rows = (
            (
                await db.execute(
                    AnalysisArtifact.__table__.select().where(
                        AnalysisArtifact.analysis_job_id == job_id,
                        AnalysisArtifact.artifact_type == "semantic_segmentation",
                    )
                )
            )
            .mappings()
            .all()
        )
        assert semantic_rows == []


async def test_cancellation_after_semantic_inference_prevents_persistence(client, monkeypatch):
    """Mirrors test_depth_pipeline.py's `_CancelDuringPredictEstimator`
    technique: cancellation cannot safely interrupt an in-flight model call,
    but the real cancellation checkpoint immediately after inference (and
    before the artifact is ever written) must still catch it — no semantic
    artifact, and the job must never become 'completed', even though its
    real depth artifact was already produced."""
    monkeypatch.setattr(
        analysis_execution, "get_depth_estimator", lambda: _FastFakeDepthEstimator()
    )

    headers = await _register_and_login(client, "sem-cancel@example.com")
    project_id = await _create_project(client, headers)
    dataset = await _upload(client, headers, project_id, "p.jpg", make_jpeg_bytes(), "image/jpeg")
    job_id = await _insert_queued_job(client, headers, project_id, dataset["id"])

    called = {"value": False}

    def _cancel_then_succeed(rgb_image, *, settings):
        called["value"] = True
        engine = create_engine(get_settings().DATABASE_URL_SYNC)
        try:
            with engine.connect() as conn:
                conn.execute(
                    text("UPDATE analysis_jobs SET status = 'cancelled' WHERE id = :id"),
                    {"id": str(job_id)},
                )
                conn.commit()
        finally:
            engine.dispose()
        return _fake_semantic_success(rgb_image, settings=settings)

    monkeypatch.setattr(analysis_execution, "run_semantic_segmentation", _cancel_then_succeed)

    await analysis_execution.execute_analysis_job(job_id)

    assert called["value"] is True
    async with AsyncSessionLocal() as db:
        job = await db.get(AnalysisJob, job_id)
        assert job.status == AnalysisJobStatus.CANCELLED  # never overwritten to completed
        assert job.execution_summary is None

        semantic_rows = (
            (
                await db.execute(
                    AnalysisArtifact.__table__.select().where(
                        AnalysisArtifact.analysis_job_id == job_id,
                        AnalysisArtifact.artifact_type == "semantic_segmentation",
                    )
                )
            )
            .mappings()
            .all()
        )
        assert semantic_rows == []  # the (discarded) result was never persisted

        # The depth artifact, committed earlier in the same job, survives —
        # cancellation during the optional semantic stage doesn't retroactively
        # destroy an already-valid, already-committed result.
        depth_rows = (
            (
                await db.execute(
                    AnalysisArtifact.__table__.select().where(
                        AnalysisArtifact.analysis_job_id == job_id,
                        AnalysisArtifact.artifact_type == "relative_depth",
                    )
                )
            )
            .mappings()
            .all()
        )
        assert len(depth_rows) == 1


# --------------------------------------------------------------------------
# Real integration test: genuine MobileSAM inference, no mocking of the
# segmentation result itself. Slower (~real CPU forward pass) — matches the
# same "at least one real end-to-end model test" precedent already set by
# test_depth_pipeline.py's real Depth Anything V2 tests.
# --------------------------------------------------------------------------


async def test_real_mobilesam_inference_produces_real_categorical_artifact(client):
    headers = await _register_and_login(client, "sem-real@example.com")
    project_id = await _create_project(client, headers)
    dataset = await _upload(
        client,
        headers,
        project_id,
        "scene.jpg",
        make_structured_scene_jpeg_bytes(256, 256),
        "image/jpeg",
    )
    job = await _create_job_with_semantic(client, headers, project_id, dataset["id"])

    import asyncio

    deadline = asyncio.get_running_loop().time() + 180.0
    body = None
    while asyncio.get_running_loop().time() < deadline:
        resp = await client.get(
            f"/api/v1/projects/{project_id}/analysis/{job['id']}", headers=headers
        )
        body = resp.json()
        if body["status"] not in ("queued", "running"):
            break
        await asyncio.sleep(0.5)
    else:
        raise AssertionError("Real semantic segmentation job did not finish within 180s")

    assert body["status"] == "completed"
    assert body["semantic_status"] == "completed"
    metadata = body["semantic_metadata"]
    assert metadata["model_name"] == "MobileSAM (ViT-T)"
    assert metadata["model_revision"] == MODEL_REVISION
    assert metadata["device"] in ("cpu", "cuda")
    assert metadata["inference_seconds"] > 0
    assert metadata["region_count"] >= 0
    assert metadata["value_semantics"] == REGION_VALUE_SEMANTICS

    artifacts = await client.get(
        f"/api/v1/projects/{project_id}/analysis/{job['id']}/artifacts", headers=headers
    )
    semantic_artifacts = [
        a for a in artifacts.json() if a["artifact_type"] == "semantic_segmentation"
    ]
    assert len(semantic_artifacts) == 1
    artifact_id = semantic_artifacts[0]["id"]

    download = await client.get(
        f"/api/v1/projects/{project_id}/analysis/{job['id']}/artifacts/{artifact_id}/download",
        headers=headers,
    )
    assert download.status_code == 200
    import io

    with rasterio.open(io.BytesIO(download.content)) as ds:
        assert ds.count == 1
        assert ds.dtypes[0] == "uint32"
        assert ds.nodata == 0
        assert ds.width == 256 and ds.height == 256
        band = ds.read(1)
        real_ids = set(np.unique(band)) - {0}
        assert real_ids == {r["region_id"] for r in metadata["regions"]}


# --------------------------------------------------------------------------
# Visualization API: categorical layer context/metadata/preview/value +
# ownership enforcement (row-insertion technique from test_visualization.py)
# --------------------------------------------------------------------------


async def test_semantic_layer_context_reports_categorical_fields(client):
    headers = await _register_and_login(client, "sem-viz1@example.com")
    project_id = await _create_project(client, headers)
    dataset = await _upload(
        client, headers, project_id, "scene.jpg", make_jpeg_bytes(64, 64), "image/jpeg"
    )
    user_id = await _current_user_id(client, headers)
    job_id = await _create_job_row(
        project_id, dataset["id"], user_id, semantic_status=SemanticStatus.COMPLETED
    )

    array = np.zeros((8, 8), dtype="uint32")
    array[0:4, 0:4] = 1
    array[4:8, 4:8] = 2
    key = f"projects/{project_id}/analysis/{job_id}/semantic.tif"
    size = _write_categorical_raster(key, array)
    await _create_artifact_row(
        job_id, "semantic_segmentation", key, size, metadata={"region_count": 2}
    )

    resp = await client.get(
        f"/api/v1/projects/{project_id}/datasets/{dataset['id']}/visualization/context",
        headers=headers,
    )
    layers = {layer["layer_type"]: layer for layer in resp.json()["layers"]}
    semantic = layers["semantic_segmentation"]
    assert semantic["available"] is True
    assert semantic["is_categorical"] is True
    assert semantic["region_count"] == 2
    assert semantic["display_name"] == "Distinct Surface Regions"
    assert semantic["min_value"] is None
    assert semantic["max_value"] is None


@pytest.mark.parametrize(
    ("status", "expected_snippet"),
    [
        (SemanticStatus.PROCESSING, "still processing"),
        (SemanticStatus.NOT_REQUESTED, "not requested"),
    ],
)
async def test_semantic_layer_unavailable_reasons(client, status, expected_snippet):
    headers = await _register_and_login(client, f"sem-viz-{status.value}@example.com")
    project_id = await _create_project(client, headers)
    dataset = await _upload(
        client, headers, project_id, "scene.jpg", make_jpeg_bytes(64, 64), "image/jpeg"
    )
    user_id = await _current_user_id(client, headers)
    await _create_job_row(project_id, dataset["id"], user_id, semantic_status=status)

    resp = await client.get(
        f"/api/v1/projects/{project_id}/datasets/{dataset['id']}/visualization/context",
        headers=headers,
    )
    layers = {layer["layer_type"]: layer for layer in resp.json()["layers"]}
    semantic = layers["semantic_segmentation"]
    assert semantic["available"] is False
    assert expected_snippet in semantic["unavailable_reason"]


async def test_semantic_layer_unavailable_reason_includes_real_error_on_failure(client):
    headers = await _register_and_login(client, "sem-viz-failed@example.com")
    project_id = await _create_project(client, headers)
    dataset = await _upload(
        client, headers, project_id, "scene.jpg", make_jpeg_bytes(64, 64), "image/jpeg"
    )
    user_id = await _current_user_id(client, headers)
    await _create_job_row(
        project_id,
        dataset["id"],
        user_id,
        semantic_status=SemanticStatus.FAILED,
        semantic_metadata={"error": "checkpoint download failed"},
    )

    resp = await client.get(
        f"/api/v1/projects/{project_id}/datasets/{dataset['id']}/visualization/context",
        headers=headers,
    )
    layers = {layer["layer_type"]: layer for layer in resp.json()["layers"]}
    assert "checkpoint download failed" in layers["semantic_segmentation"]["unavailable_reason"]


async def test_categorical_artifact_metadata_has_no_min_max_and_real_region_count(client):
    headers = await _register_and_login(client, "sem-viz2@example.com")
    project_id = await _create_project(client, headers)
    dataset = await _upload(
        client, headers, project_id, "scene.jpg", make_jpeg_bytes(64, 64), "image/jpeg"
    )
    user_id = await _current_user_id(client, headers)
    job_id = await _create_job_row(project_id, dataset["id"], user_id)

    array = np.array([[0, 1], [2, 2]], dtype="uint32")
    key = f"projects/{project_id}/analysis/{job_id}/semantic.tif"
    size = _write_categorical_raster(key, array)
    artifact_id = await _create_artifact_row(
        job_id, "semantic_segmentation", key, size, metadata={"region_count": 2}
    )

    resp = await client.get(
        f"/api/v1/projects/{project_id}/analysis/{job_id}/artifacts/{artifact_id}/visualization/metadata",
        headers=headers,
    )
    body = resp.json()
    assert body["is_categorical"] is True
    assert body["region_count"] == 2
    assert body["min_value"] is None
    assert body["max_value"] is None
    assert body["dtype"] == "uint32"
    assert body["nodata"] == 0.0


async def test_categorical_preview_uses_deterministic_colors_and_transparent_background(client):
    headers = await _register_and_login(client, "sem-viz3@example.com")
    project_id = await _create_project(client, headers)
    dataset = await _upload(
        client, headers, project_id, "scene.jpg", make_jpeg_bytes(64, 64), "image/jpeg"
    )
    user_id = await _current_user_id(client, headers)
    job_id = await _create_job_row(project_id, dataset["id"], user_id)

    array = np.zeros((4, 4), dtype="uint32")
    array[0:2, 0:2] = 1
    array[2:4, 2:4] = 2
    key = f"projects/{project_id}/analysis/{job_id}/semantic.tif"
    size = _write_categorical_raster(key, array)
    artifact_id = await _create_artifact_row(job_id, "semantic_segmentation", key, size)

    resp = await client.get(
        f"/api/v1/projects/{project_id}/analysis/{job_id}/artifacts/{artifact_id}/visualization/preview",
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    import io

    image = Image.open(io.BytesIO(resp.content)).convert("RGBA")
    pixels = np.array(image)
    # array = [[1,1,0,0],[1,1,0,0],[0,0,2,2],[0,0,2,2]] (row, col)
    # (0, 3) is real background (never assigned to region 1 or 2).
    assert pixels[0, 3, 3] == 0
    assert pixels[0, 0, 3] == 255  # region 1 pixel is opaque
    assert tuple(pixels[0, 0, :3]) == _region_color(1)
    assert pixels[3, 3, 3] == 255  # region 2 pixel is opaque
    assert tuple(pixels[3, 3, :3]) == _region_color(2)


async def test_semantic_value_endpoint_returns_real_region_ids_and_null_for_background(client):
    headers = await _register_and_login(client, "sem-viz4@example.com")
    project_id = await _create_project(client, headers)
    dataset = await _upload(
        client, headers, project_id, "scene.jpg", make_jpeg_bytes(64, 64), "image/jpeg"
    )
    user_id = await _current_user_id(client, headers)
    job_id = await _create_job_row(project_id, dataset["id"], user_id)

    array = np.array([[0, 1, 1], [2, 2, 0], [0, 3, 3]], dtype="uint32")
    key = f"projects/{project_id}/analysis/{job_id}/semantic.tif"
    size = _write_categorical_raster(key, array)
    artifact_id = await _create_artifact_row(job_id, "semantic_segmentation", key, size)

    for row, col, expected in [(0, 0, None), (0, 1, 1.0), (1, 0, 2.0), (2, 1, 3.0)]:
        resp = await client.get(
            f"/api/v1/projects/{project_id}/analysis/{job_id}/artifacts/{artifact_id}"
            f"/visualization/value?row={row}&col={col}",
            headers=headers,
        )
        assert resp.json()["value"] == expected


async def test_semantic_artifact_endpoints_enforce_ownership_chain(client):
    owner_headers = await _register_and_login(client, "sem-owner@example.com")
    intruder_headers = await _register_and_login(client, "sem-intruder@example.com")
    project_id = await _create_project(client, owner_headers)
    dataset = await _upload(
        client, owner_headers, project_id, "scene.jpg", make_jpeg_bytes(64, 64), "image/jpeg"
    )
    user_id = await _current_user_id(client, owner_headers)
    job_id = await _create_job_row(project_id, dataset["id"], user_id)

    array = np.ones((4, 4), dtype="uint32")
    key = f"projects/{project_id}/analysis/{job_id}/semantic.tif"
    size = _write_categorical_raster(key, array)
    artifact_id = await _create_artifact_row(job_id, "semantic_segmentation", key, size)

    base = f"/api/v1/projects/{project_id}/analysis/{job_id}/artifacts/{artifact_id}/visualization"

    # No token.
    for suffix in ("metadata", "preview", "value?row=0&col=0"):
        resp = await client.get(f"{base}/{suffix}")
        assert resp.status_code == 401, suffix

    # Wrong user.
    for suffix in ("metadata", "preview", "value?row=0&col=0"):
        resp = await client.get(f"{base}/{suffix}", headers=intruder_headers)
        assert resp.status_code == 404, suffix

    # Valid owner.
    resp = await client.get(f"{base}/metadata", headers=owner_headers)
    assert resp.status_code == 200
