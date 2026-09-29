"""Phase 5: visualization API tests.

Covers real ownership/authentication enforcement, real raster metadata/
preview/window generation (Pillow-decoded PNGs, not just HTTP 200 checks),
and real terrain height-grid extraction independently verified against the
actual stored DSM bytes — no mocking of rasterio/GDAL reads in the tests
that exercise them. A handful of tests insert an `AnalysisJob`/
`AnalysisArtifact` row directly (writing a real raster file to the real
storage backend) to test a specific branch deterministically without
re-running the full depth model — the same technique already used in
tests/test_analysis.py for its own deterministic edge cases.
"""

import io
import uuid
from datetime import UTC, datetime

import numpy as np
import pytest
import rasterio
from PIL import Image
from rasterio.transform import from_origin

from app.core.storage import get_storage
from app.db.session import AsyncSessionLocal
from app.models.analysis_artifact import AnalysisArtifact
from app.models.analysis_job import AnalysisJob, AnalysisJobStatus, CalibrationStatus
from app.services.visualization import TERRAIN_SOURCE_ARTIFACT_TYPES, height_kind_for_artifact_type
from geospatial.terrain_grid import extract_terrain_grid
from tests.fixtures import (
    make_depth_consistent_dem_geotiff_bytes,
    make_structured_scene_geotiff_bytes,
    make_structured_scene_tiff_bytes_no_georef,
    real_structured_scene_depth,
)

_ORIGIN_X, _ORIGIN_Y, _PIXEL_SIZE, _UTM_CRS = 500000.0, 4649984.0, 2.0, "EPSG:32633"


# --------------------------------------------------------------------------
# height_kind_for_artifact_type (pure function, no DB/HTTP) -- covers the
# reserved-but-unused "remote_sensing_height" third value added as part of
# the RS-height architecture extension point (ai/rs_height_estimator.py,
# docs/ARCHITECTURE_NOTE_RS_HEIGHT.md).
# --------------------------------------------------------------------------


def test_height_kind_for_dsm_is_elevation():
    assert height_kind_for_artifact_type("dsm") == "elevation"


def test_height_kind_for_relative_depth_is_relative_depth():
    assert height_kind_for_artifact_type("relative_depth") == "relative_depth"


def test_height_kind_for_remote_sensing_height_is_its_own_kind_not_folded_into_relative_depth():
    """The whole point of extending this function: a future
    "remote_sensing_height" artifact must never be silently misclassified
    as generic relative depth just because it isn't "dsm"."""
    assert height_kind_for_artifact_type("remote_sensing_height") == "remote_sensing_height"


def test_height_kind_for_unrelated_artifact_type_still_defaults_to_relative_depth():
    """Existing behavior for every other artifact_type (metric_elevation,
    semantic_segmentation, ...) is unchanged."""
    assert height_kind_for_artifact_type("metric_elevation") == "relative_depth"
    assert height_kind_for_artifact_type("semantic_segmentation") == "relative_depth"
    assert height_kind_for_artifact_type("flood_screening") == "relative_depth"


def test_terrain_source_artifact_types_does_not_yet_include_remote_sensing_height():
    """Deliberately not widened yet -- no artifact of this type is ever
    produced (ai/registry.py::get_rs_height_estimator() always raises
    NotImplementedError), so there is nothing real to accept as a terrain
    source for it. See TERRAIN_SOURCE_ARTIFACT_TYPES's own comment."""
    assert TERRAIN_SOURCE_ARTIFACT_TYPES == {"dsm", "relative_depth"}
    assert "remote_sensing_height" not in TERRAIN_SOURCE_ARTIFACT_TYPES


async def _register_and_login(client, email: str, password: str = "supersecret123") -> dict:
    await client.post("/api/v1/auth/register", json={"email": email, "password": password})
    resp = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


async def _create_project(client, headers: dict, name: str = "Viz Test Project") -> str:
    resp = await client.post("/api/v1/projects", json={"name": name}, headers=headers)
    return resp.json()["id"]


async def _current_user_id(client, headers: dict) -> str:
    return (await client.get("/api/v1/auth/me", headers=headers)).json()["id"]


async def _upload_source_image(client, headers, project_id, filename="scene.tif") -> dict:
    content = make_structured_scene_geotiff_bytes(
        width=64,
        height=64,
        crs=_UTM_CRS,
        origin_x=_ORIGIN_X,
        origin_y=_ORIGIN_Y,
        pixel_size=_PIXEL_SIZE,
    )
    resp = await client.post(
        f"/api/v1/projects/{project_id}/datasets",
        files={"file": (filename, content, "image/tiff")},
        headers=headers,
    )
    return resp.json()


async def _upload_dem_reference(client, headers, project_id, filename="dem.tif") -> dict:
    # DEM = 2 * (real model depth of the uploaded scene) + 100 + small noise:
    # a deliberately depth-consistent reference so the calibration quality
    # gate has a real relationship to accept. Validates pipeline behavior;
    # NOT accuracy evidence (see make_depth_consistent_dem_geotiff_bytes).
    content = make_depth_consistent_dem_geotiff_bytes(
        real_structured_scene_depth(64, 64),
        crs=_UTM_CRS,
        origin_x=_ORIGIN_X,
        origin_y=_ORIGIN_Y,
        pixel_size=_PIXEL_SIZE,
    )
    resp = await client.post(
        f"/api/v1/projects/{project_id}/datasets",
        files={"file": (filename, content, "image/tiff")},
        data={"role": "dem_reference"},
        headers=headers,
    )
    return resp.json()


async def _create_job_row(
    project_id: str,
    dataset_id: str,
    user_id: str,
    *,
    status=AnalysisJobStatus.COMPLETED,
    calibration_status=CalibrationStatus.UNCALIBRATED,
    calibration_metadata=None,
) -> uuid.UUID:
    async with AsyncSessionLocal() as db:
        job = AnalysisJob(
            project_id=uuid.UUID(project_id),
            dataset_id=uuid.UUID(dataset_id),
            user_id=uuid.UUID(user_id),
            status=status,
            parameters={"version": "v1"},
            calibration_status=calibration_status,
            calibration_metadata=calibration_metadata,
            completed_at=datetime.now(UTC) if status == AnalysisJobStatus.COMPLETED else None,
        )
        db.add(job)
        await db.commit()
        return job.id


def _write_real_raster(
    storage_key: str, array: np.ndarray, *, crs=None, transform=None, nodata=None
):
    storage = get_storage()
    path = storage.absolute_path(storage_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    profile = {
        "driver": "GTiff",
        "height": array.shape[0],
        "width": array.shape[1],
        "count": 1,
        "dtype": str(array.dtype),
    }
    if crs is not None and transform is not None:
        profile["crs"] = crs
        profile["transform"] = transform
    if nodata is not None:
        profile["nodata"] = nodata
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(array, 1)
    return path.stat().st_size


async def _create_artifact_row(
    job_id: uuid.UUID,
    artifact_type: str,
    storage_key: str,
    file_size: int,
    metadata: dict | None = None,
) -> uuid.UUID:
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


# --------------------------------------------------------------------------
# 1-2: authentication and ownership
# --------------------------------------------------------------------------


async def test_context_requires_authentication(client):
    resp = await client.get(
        f"/api/v1/projects/{uuid.uuid4()}/datasets/{uuid.uuid4()}/visualization/context"
    )
    assert resp.status_code == 401


async def test_context_enforces_project_ownership(client):
    owner_headers = await _register_and_login(client, "viz-owner1@example.com")
    intruder_headers = await _register_and_login(client, "viz-intruder1@example.com")
    project_id = await _create_project(client, owner_headers)
    dataset = await _upload_source_image(client, owner_headers, project_id)

    resp = await client.get(
        f"/api/v1/projects/{project_id}/datasets/{dataset['id']}/visualization/context",
        headers=intruder_headers,
    )
    assert resp.status_code == 404


async def test_artifact_endpoints_enforce_ownership_chain(client):
    owner_headers = await _register_and_login(client, "viz-owner2@example.com")
    intruder_headers = await _register_and_login(client, "viz-intruder2@example.com")
    project_id = await _create_project(client, owner_headers)
    dataset = await _upload_source_image(client, owner_headers, project_id)
    user_id = await _current_user_id(client, owner_headers)
    job_id = await _create_job_row(project_id, dataset["id"], user_id)

    key = f"projects/{project_id}/analysis/{job_id}/depth.tif"
    size = _write_real_raster(key, np.ones((8, 8), dtype="float32"))
    artifact_id = await _create_artifact_row(job_id, "relative_depth", key, size)

    for suffix in ("metadata", "preview", "value?row=0&col=0"):
        resp = await client.get(
            f"/api/v1/projects/{project_id}/analysis/{job_id}/artifacts/{artifact_id}/visualization/{suffix}",
            headers=intruder_headers,
        )
        assert resp.status_code == 404, suffix


# --------------------------------------------------------------------------
# Pixel-value endpoint (/value): the authoritative source the frontend's
# cursor-inspection flow must call — see the pickInspectableLayer bugfix in
# frontend/src/components/terrain/TerrainWorkspace.tsx. These tests didn't
# exist before that bug was found; added here to close the gap rather than
# relying only on the manual curl verification done during Phase 5.
# --------------------------------------------------------------------------


async def test_pixel_value_endpoint_returns_real_value_matching_raster(client):
    headers = await _register_and_login(client, "viz-owner10@example.com")
    project_id = await _create_project(client, headers)
    dataset = await _upload_source_image(client, headers, project_id)
    user_id = await _current_user_id(client, headers)
    job_id = await _create_job_row(project_id, dataset["id"], user_id)

    # A real raster with known, distinct values at specific cells — the
    # endpoint's response is checked against these exact numbers, not just
    # "some number came back".
    array = np.arange(64, dtype="float32").reshape(8, 8)
    key = f"projects/{project_id}/analysis/{job_id}/depth.tif"
    size = _write_real_raster(key, array)
    artifact_id = await _create_artifact_row(job_id, "relative_depth", key, size)

    for row, col in [(0, 0), (3, 5), (7, 7)]:
        resp = await client.get(
            f"/api/v1/projects/{project_id}/analysis/{job_id}/artifacts/{artifact_id}"
            f"/visualization/value?row={row}&col={col}",
            headers=headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["row"] == row
        assert body["col"] == col
        assert body["value"] == pytest.approx(float(array[row, col]))


async def test_pixel_value_endpoint_returns_null_for_nodata_and_out_of_bounds(client):
    headers = await _register_and_login(client, "viz-owner11@example.com")
    project_id = await _create_project(client, headers)
    dataset = await _upload_source_image(client, headers, project_id)
    user_id = await _current_user_id(client, headers)
    job_id = await _create_job_row(project_id, dataset["id"], user_id)

    array = np.full((8, 8), 5.0, dtype="float32")
    array[2, 2] = -9999.0  # a real NoData cell
    key = f"projects/{project_id}/analysis/{job_id}/dsm.tif"
    size = _write_real_raster(key, array, nodata=-9999.0)
    artifact_id = await _create_artifact_row(job_id, "dsm", key, size)

    nodata_resp = await client.get(
        f"/api/v1/projects/{project_id}/analysis/{job_id}/artifacts/{artifact_id}"
        "/visualization/value?row=2&col=2",
        headers=headers,
    )
    assert nodata_resp.status_code == 200
    assert nodata_resp.json()["value"] is None  # real NoData, never coerced to 0

    valid_resp = await client.get(
        f"/api/v1/projects/{project_id}/analysis/{job_id}/artifacts/{artifact_id}"
        "/visualization/value?row=0&col=0",
        headers=headers,
    )
    assert valid_resp.status_code == 200
    assert valid_resp.json()["value"] == pytest.approx(5.0)


# --------------------------------------------------------------------------
# 3, 4, 5, 8, 10: real end-to-end pipeline (one real DEM calibration job,
# reused across several assertions to avoid re-running inference)
# --------------------------------------------------------------------------


async def _run_real_calibrated_job(client, email: str) -> dict:
    """Runs one full, real DEM-calibrated analysis job (real model, real
    worker, real calibration) and returns everything needed to test the
    visualization endpoints against it."""
    headers = await _register_and_login(client, email)
    project_id = await _create_project(client, headers)
    source = await _upload_source_image(client, headers, project_id)
    dem = await _upload_dem_reference(client, headers, project_id)

    create_resp = await client.post(
        f"/api/v1/projects/{project_id}/datasets/{source['id']}/analysis",
        json={"parameters": {"version": "v1", "dem_reference_dataset_id": dem["id"]}},
        headers=headers,
    )
    job = create_resp.json()

    import asyncio

    deadline = asyncio.get_running_loop().time() + 120.0
    while asyncio.get_running_loop().time() < deadline:
        resp = await client.get(
            f"/api/v1/projects/{project_id}/analysis/{job['id']}", headers=headers
        )
        body = resp.json()
        if body["status"] not in ("queued", "running"):
            job = body
            break
        await asyncio.sleep(0.3)
    assert job["status"] == "completed"
    assert job["calibration_status"] == "calibrated"

    return {"headers": headers, "project_id": project_id, "source": source, "job": job}


async def test_full_context_metadata_preview_and_terrain_match_real_artifacts(client):
    ctx = await _run_real_calibrated_job(client, "viz-owner3@example.com")
    headers, project_id, source, job = ctx["headers"], ctx["project_id"], ctx["source"], ctx["job"]

    # --- 3: context reflects only real artifacts that actually exist ---
    context_resp = await client.get(
        f"/api/v1/projects/{project_id}/datasets/{source['id']}/visualization/context",
        headers=headers,
    )
    assert context_resp.status_code == 200
    context = context_resp.json()
    layers_by_type = {layer["layer_type"]: layer for layer in context["layers"]}
    assert layers_by_type["rgb"]["available"] is True
    assert layers_by_type["relative_depth"]["available"] is True
    assert layers_by_type["metric_elevation"]["available"] is True
    assert layers_by_type["dsm"]["available"] is True
    assert context["terrain"]["available"] is True
    # Phase 10: the calibrated path must keep reporting real elevation
    # semantics — never the new relative-depth fallback — once a real DSM
    # exists.
    assert context["terrain"]["height_kind"] == "elevation"
    assert context["terrain"]["source_artifact_type"] == "dsm"
    assert context["terrain"]["min_elevation"] is not None
    assert context["terrain"]["max_elevation"] is not None
    dsm_artifact_id = context["terrain"]["artifact_id"]
    depth_artifact_id = layers_by_type["relative_depth"]["artifact_id"]

    # --- 8: georeferenced raster reports real bounds ---
    assert context["dataset"]["is_georeferenced"] is True
    assert context["dataset"]["bounds"] == {
        "min_x": 500000.0,
        "min_y": 4649856.0,
        "max_x": 500128.0,
        "max_y": 4649984.0,
    }

    # --- 4: metadata extraction works on the real GeoTIFF ---
    meta_resp = await client.get(
        f"/api/v1/projects/{project_id}/analysis/{job['id']}/artifacts/{dsm_artifact_id}"
        "/visualization/metadata",
        headers=headers,
    )
    assert meta_resp.status_code == 200
    meta = meta_resp.json()
    assert meta["width"] == 64
    assert meta["height"] == 64
    assert meta["crs"] == _UTM_CRS
    assert meta["has_finite_data"] is True
    assert meta["min_value"] is not None and meta["max_value"] is not None
    assert meta["min_value"] <= meta["max_value"]

    # --- 5: preview endpoint returns a real, decodable PNG of the right size ---
    preview_resp = await client.get(
        f"/api/v1/projects/{project_id}/analysis/{job['id']}/artifacts/{depth_artifact_id}"
        "/visualization/preview",
        headers=headers,
    )
    assert preview_resp.status_code == 200
    assert preview_resp.headers["content-type"] == "image/png"
    image = Image.open(io.BytesIO(preview_resp.content))
    assert image.size == (64, 64)
    assert image.mode == "RGBA"

    # --- 10: terrain grid values come from the actual DSM, independently
    # re-verified against the artifact downloaded via the existing
    # (Phase 4) authenticated download endpoint. ---
    download_resp = await client.get(
        f"/api/v1/projects/{project_id}/analysis/{job['id']}/artifacts/{dsm_artifact_id}/download",
        headers=headers,
    )
    assert download_resp.status_code == 200
    with rasterio.MemoryFile(download_resp.content) as memfile:
        with memfile.open() as dsm_dataset:
            real_dsm = dsm_dataset.read(1)

    grid_resp = await client.get(
        f"/api/v1/projects/{project_id}/analysis/{job['id']}/artifacts/{dsm_artifact_id}"
        "/visualization/terrain/grid",
        headers=headers,
    )
    assert grid_resp.status_code == 200
    grid = np.frombuffer(grid_resp.content, dtype="<f4").reshape((64, 64))
    assert np.allclose(real_dsm.astype("float32"), grid, atol=1e-4, equal_nan=True)

    # Phase 10: the terrain/metadata endpoint for a real calibrated DSM must
    # keep reporting height_kind="elevation" with real elevation fields.
    terrain_meta_resp = await client.get(
        f"/api/v1/projects/{project_id}/analysis/{job['id']}/artifacts/{dsm_artifact_id}"
        "/visualization/terrain/metadata",
        headers=headers,
    )
    assert terrain_meta_resp.status_code == 200
    terrain_meta = terrain_meta_resp.json()
    assert terrain_meta["height_kind"] == "elevation"
    assert terrain_meta["source_artifact_type"] == "dsm"
    assert terrain_meta["min_elevation"] is not None
    assert terrain_meta["max_elevation"] is not None
    assert terrain_meta["min_height_value"] == terrain_meta["min_elevation"]
    assert terrain_meta["max_height_value"] == terrain_meta["max_elevation"]


# --------------------------------------------------------------------------
# 6: preview/statistics exclude nodata (direct geospatial unit test — fast,
# precise, no HTTP/model involved)
# --------------------------------------------------------------------------


def test_raster_statistics_exclude_nodata(tmp_path):
    from geospatial.raster_preview import compute_raster_statistics

    array = np.full((10, 10), 5.0, dtype="float32")
    array[0:3, 0:3] = -9999.0  # a real NoData region
    path = tmp_path / "with_nodata.tif"
    with rasterio.open(
        path, "w", driver="GTiff", height=10, width=10, count=1, dtype="float32", nodata=-9999.0
    ) as dst:
        dst.write(array, 1)

    stats = compute_raster_statistics(path)
    assert stats.min_value == 5.0
    assert stats.max_value == 5.0
    assert stats.has_finite_data is True


# --------------------------------------------------------------------------
# 7: non-georeferenced source never gets a fabricated CRS
# --------------------------------------------------------------------------


async def test_non_georeferenced_dataset_context_has_no_fake_crs(client):
    headers = await _register_and_login(client, "viz-owner4@example.com")
    project_id = await _create_project(client, headers)
    content = make_structured_scene_tiff_bytes_no_georef(32, 32)
    upload_resp = await client.post(
        f"/api/v1/projects/{project_id}/datasets",
        files={"file": ("nogeoref.tif", content, "image/tiff")},
        headers=headers,
    )
    dataset = upload_resp.json()
    assert dataset["is_georeferenced"] is False

    context_resp = await client.get(
        f"/api/v1/projects/{project_id}/datasets/{dataset['id']}/visualization/context",
        headers=headers,
    )
    context = context_resp.json()
    assert context["dataset"]["is_georeferenced"] is False
    assert context["dataset"]["crs"] is None
    assert context["dataset"]["bounds"] is None
    rgb_layer = next(layer for layer in context["layers"] if layer["layer_type"] == "rgb")
    assert rgb_layer["is_georeferenced"] is False
    assert rgb_layer["crs"] is None
    assert rgb_layer["bounds"] is None


# --------------------------------------------------------------------------
# 9: terrain requires an actual DSM artifact
# --------------------------------------------------------------------------


async def test_terrain_endpoint_requires_dsm_or_relative_depth_artifact_type(client):
    """Phase 10: `relative_depth` is now a real, valid terrain source (see
    test_relative_depth_backed_terrain_context_and_endpoints below) — only
    an artifact type that genuinely has no height-field meaning (here,
    `semantic_segmentation`'s categorical region IDs) is still rejected."""
    headers = await _register_and_login(client, "viz-owner5@example.com")
    project_id = await _create_project(client, headers)
    dataset = await _upload_source_image(client, headers, project_id)
    user_id = await _current_user_id(client, headers)
    job_id = await _create_job_row(project_id, dataset["id"], user_id)

    key = f"projects/{project_id}/analysis/{job_id}/regions.tif"
    size = _write_real_raster(key, np.zeros((8, 8), dtype="uint32"))
    artifact_id = await _create_artifact_row(job_id, "semantic_segmentation", key, size)

    resp = await client.get(
        f"/api/v1/projects/{project_id}/analysis/{job_id}/artifacts/{artifact_id}"
        "/visualization/terrain/metadata",
        headers=headers,
    )
    assert resp.status_code == 422
    assert "dsm" in resp.json()["error"]["message"]
    assert "relative_depth" in resp.json()["error"]["message"]


# --------------------------------------------------------------------------
# 9b (Phase 10): relative_depth is a real, honestly-labeled 3D terrain
# source when no calibration has succeeded — never elevation, never a DSM.
# --------------------------------------------------------------------------


async def test_relative_depth_backed_terrain_context_and_endpoints(client):
    """A real uncalibrated job whose ONLY scientific artifact is
    relative_depth must still produce a real, usable 3D terrain — honestly
    labeled height_kind="relative_depth", never exposing elevation-shaped
    fields, and never fabricating a CRS for a genuinely non-georeferenced
    source."""
    headers = await _register_and_login(client, "viz-owner-relterrain@example.com")
    project_id = await _create_project(client, headers)
    content = make_structured_scene_tiff_bytes_no_georef(16, 16)
    upload_resp = await client.post(
        f"/api/v1/projects/{project_id}/datasets",
        files={"file": ("nogeoref.tif", content, "image/tiff")},
        headers=headers,
    )
    dataset = upload_resp.json()
    assert dataset["is_georeferenced"] is False
    user_id = await _current_user_id(client, headers)
    job_id = await _create_job_row(project_id, dataset["id"], user_id)

    array = np.linspace(0.1, 5.0, 16 * 16, dtype="float32").reshape(16, 16)
    key = f"projects/{project_id}/analysis/{job_id}/depth.tif"
    size = _write_real_raster(key, array)
    # Realistic artifact_metadata (matching the real shape
    # analysis_execution.py persists for a genuine relative_depth artifact —
    # see its `is_georeferenced`/`crs`/`width`/`height` keys) rather than
    # leaving it empty, so this test exercises the same real metadata-driven
    # path a real job would.
    depth_artifact_id = await _create_artifact_row(
        job_id,
        "relative_depth",
        key,
        size,
        metadata={
            "is_georeferenced": False,
            "crs": None,
            "width": 16,
            "height": 16,
            "dtype": "float32",
        },
    )

    # --- 1/2/3/4/5/6: real VisualizationContext reflects the fallback ---
    context_resp = await client.get(
        f"/api/v1/projects/{project_id}/datasets/{dataset['id']}/visualization/context",
        headers=headers,
    )
    assert context_resp.status_code == 200
    terrain = context_resp.json()["terrain"]
    assert terrain["available"] is True  # (1)
    assert terrain["height_kind"] == "relative_depth"  # (2)
    assert terrain["source_artifact_type"] == "relative_depth"
    assert terrain["artifact_id"] == str(depth_artifact_id)  # (3)
    assert terrain["min_elevation"] is None  # (4) never elevation semantics
    assert terrain["max_elevation"] is None
    assert terrain["min_height_value"] == pytest.approx(0.1, abs=1e-3)
    assert terrain["max_height_value"] == pytest.approx(5.0, abs=1e-3)
    assert terrain["is_georeferenced"] is False
    assert terrain["crs"] is None  # (5) non-georeferenced source stays null
    assert terrain["bounds"] is None  # (6) no fabricated geographic bounds

    # --- terrain/metadata endpoint: same real honesty rules ---
    meta_resp = await client.get(
        f"/api/v1/projects/{project_id}/analysis/{job_id}/artifacts/{depth_artifact_id}"
        "/visualization/terrain/metadata",
        headers=headers,
    )
    assert meta_resp.status_code == 200
    meta = meta_resp.json()
    assert meta["height_kind"] == "relative_depth"
    assert meta["source_artifact_type"] == "relative_depth"
    assert meta["min_elevation"] is None
    assert meta["max_elevation"] is None
    assert meta["min_height_value"] == pytest.approx(0.1, abs=1e-3)
    assert meta["max_height_value"] == pytest.approx(5.0, abs=1e-3)
    assert meta["is_georeferenced"] is False
    assert meta["crs"] is None
    assert meta["origin_x"] == 0.0  # non-georeferenced: plain pixel coordinates
    assert meta["origin_y"] == 0.0
    assert meta["cell_size_x"] == 1.0
    assert meta["cell_size_y"] == 1.0

    # --- terrain/grid: independently verify the real bytes match the real
    # stored relative_depth raster (16x16 is well under MAX_TERRAIN_DIMENSION,
    # so no decimation occurs and an exact comparison is valid). ---
    grid_resp = await client.get(
        f"/api/v1/projects/{project_id}/analysis/{job_id}/artifacts/{depth_artifact_id}"
        "/visualization/terrain/grid",
        headers=headers,
    )
    assert grid_resp.status_code == 200
    grid = np.frombuffer(grid_resp.content, dtype="<f4").reshape((16, 16))
    assert np.allclose(array, grid, atol=1e-5)


async def test_calibration_failed_job_still_falls_back_to_relative_terrain(client):
    """A job that genuinely attempted and failed calibration must not lose
    its real relative_depth-backed terrain — calibration failing is not the
    same as no analysis existing at all."""
    headers = await _register_and_login(client, "viz-owner-relterrain2@example.com")
    project_id = await _create_project(client, headers)
    dataset = await _upload_source_image(client, headers, project_id)
    user_id = await _current_user_id(client, headers)
    job_id = await _create_job_row(
        project_id,
        dataset["id"],
        user_id,
        calibration_status=CalibrationStatus.FAILED,
        calibration_metadata={"error": "Only 1 valid reference sample(s)."},
    )
    key = f"projects/{project_id}/analysis/{job_id}/depth.tif"
    size = _write_real_raster(key, np.ones((8, 8), dtype="float32"))
    await _create_artifact_row(job_id, "relative_depth", key, size)

    context_resp = await client.get(
        f"/api/v1/projects/{project_id}/datasets/{dataset['id']}/visualization/context",
        headers=headers,
    )
    terrain = context_resp.json()["terrain"]
    assert terrain["available"] is True
    assert terrain["height_kind"] == "relative_depth"


async def test_terrain_unavailable_when_neither_dsm_nor_relative_depth_exists(client):
    """A job that hasn't produced any scientific artifact yet (e.g. still
    running) must correctly report terrain as unavailable — never a
    fabricated fallback."""
    headers = await _register_and_login(client, "viz-owner-relterrain3@example.com")
    project_id = await _create_project(client, headers)
    dataset = await _upload_source_image(client, headers, project_id)
    user_id = await _current_user_id(client, headers)
    await _create_job_row(project_id, dataset["id"], user_id)  # no artifacts at all

    context_resp = await client.get(
        f"/api/v1/projects/{project_id}/datasets/{dataset['id']}/visualization/context",
        headers=headers,
    )
    terrain = context_resp.json()["terrain"]
    assert terrain["available"] is False
    assert terrain["height_kind"] is None
    assert terrain["unavailable_reason"] is not None


# --------------------------------------------------------------------------
# 11: terrain downsampling respects the configured max dimension
# --------------------------------------------------------------------------


async def test_terrain_downsampling_respects_max_dimension(client):
    from app.core.config import get_settings
    from app.main import app

    # FastAPI's dependency-override mechanism is the correct way to swap a
    # `Depends(...)`-injected value in a test — `Depends(get_settings)` binds
    # the function object at route-definition (import) time, so patching the
    # module attribute afterward would not affect the already-registered route.
    app.dependency_overrides[get_settings] = lambda: _settings_with_max_terrain_dimension(64)
    try:
        await _run_terrain_downsampling_case(client)
    finally:
        del app.dependency_overrides[get_settings]


async def _run_terrain_downsampling_case(client):
    headers = await _register_and_login(client, "viz-owner6@example.com")
    project_id = await _create_project(client, headers)
    dataset = await _upload_source_image(client, headers, project_id)
    user_id = await _current_user_id(client, headers)
    job_id = await _create_job_row(
        project_id, dataset["id"], user_id, calibration_status=CalibrationStatus.CALIBRATED
    )

    # A real DSM much larger than the configured max dimension.
    large = np.linspace(100.0, 200.0, 600 * 400, dtype="float32").reshape(600, 400)
    transform = from_origin(500000.0, 4650000.0, 2.0, 2.0)
    key = f"projects/{project_id}/analysis/{job_id}/dsm.tif"
    size = _write_real_raster(key, large, crs=_UTM_CRS, transform=transform)
    artifact_id = await _create_artifact_row(
        job_id, "dsm", key, size, metadata={"width": 400, "height": 600, "crs": _UTM_CRS}
    )

    meta_resp = await client.get(
        f"/api/v1/projects/{project_id}/analysis/{job_id}/artifacts/{artifact_id}"
        "/visualization/terrain/metadata",
        headers=headers,
    )
    assert meta_resp.status_code == 200
    meta = meta_resp.json()
    # Source dimensions are reported truthfully...
    assert meta["source_width"] == 400
    assert meta["source_height"] == 600
    # ...but the actual returned grid never exceeds the configured bound.
    assert max(meta["width"], meta["height"]) <= 64

    grid_resp = await client.get(
        f"/api/v1/projects/{project_id}/analysis/{job_id}/artifacts/{artifact_id}"
        "/visualization/terrain/grid",
        headers=headers,
    )
    assert grid_resp.status_code == 200
    assert len(grid_resp.content) == meta["width"] * meta["height"] * 4


def _settings_with_max_terrain_dimension(value: int):
    from app.core.config import get_settings

    settings = get_settings()
    return settings.model_copy(update={"MAX_TERRAIN_DIMENSION": value})


def test_extract_terrain_grid_respects_max_dimension_directly(tmp_path):
    """Same behavior, exercised directly against geospatial/terrain_grid.py
    (no HTTP/DB involved) for a precise, fast unit check."""
    large = np.linspace(0.0, 50.0, 300 * 300, dtype="float32").reshape(300, 300)
    path = tmp_path / "large_dsm.tif"
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=300,
        width=300,
        count=1,
        dtype="float32",
        crs=_UTM_CRS,
        transform=from_origin(500000.0, 4650000.0, 1.0, 1.0),
    ) as dst:
        dst.write(large, 1)

    grid = extract_terrain_grid(path, max_dimension=100)
    assert grid.source_width == 300
    assert grid.source_height == 300
    assert max(grid.width, grid.height) <= 100
    assert grid.min_elevation is not None
    assert grid.max_elevation is not None


# --------------------------------------------------------------------------
# 13, 14: controlled errors for corrupted/missing raster files
# --------------------------------------------------------------------------


async def test_corrupted_raster_returns_controlled_error(client):
    headers = await _register_and_login(client, "viz-owner7@example.com")
    project_id = await _create_project(client, headers)
    dataset = await _upload_source_image(client, headers, project_id)
    user_id = await _current_user_id(client, headers)
    job_id = await _create_job_row(project_id, dataset["id"], user_id)

    storage = get_storage()
    key = f"projects/{project_id}/analysis/{job_id}/corrupt.tif"
    path = storage.absolute_path(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"not a real tiff file" * 20)
    artifact_id = await _create_artifact_row(job_id, "relative_depth", key, path.stat().st_size)

    resp = await client.get(
        f"/api/v1/projects/{project_id}/analysis/{job_id}/artifacts/{artifact_id}"
        "/visualization/metadata",
        headers=headers,
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


async def test_missing_artifact_file_returns_controlled_error(client):
    headers = await _register_and_login(client, "viz-owner8@example.com")
    project_id = await _create_project(client, headers)
    dataset = await _upload_source_image(client, headers, project_id)
    user_id = await _current_user_id(client, headers)
    job_id = await _create_job_row(project_id, dataset["id"], user_id)

    # A real artifact row pointing at a storage key that was never written.
    key = f"projects/{project_id}/analysis/{job_id}/never_written.tif"
    artifact_id = await _create_artifact_row(job_id, "relative_depth", key, 0)

    resp = await client.get(
        f"/api/v1/projects/{project_id}/analysis/{job_id}/artifacts/{artifact_id}"
        "/visualization/preview",
        headers=headers,
    )
    assert resp.status_code == 404


# --------------------------------------------------------------------------
# 15: metric elevation is never reported when calibration failed
# --------------------------------------------------------------------------


async def test_metric_elevation_not_reported_when_calibration_failed(client):
    headers = await _register_and_login(client, "viz-owner9@example.com")
    project_id = await _create_project(client, headers)
    dataset = await _upload_source_image(client, headers, project_id)
    user_id = await _current_user_id(client, headers)
    job_id = await _create_job_row(
        project_id,
        dataset["id"],
        user_id,
        calibration_status=CalibrationStatus.FAILED,
        calibration_metadata={
            "error": "Only 1 valid reference sample(s); at least 3 are required."
        },
    )
    key = f"projects/{project_id}/analysis/{job_id}/depth.tif"
    size = _write_real_raster(key, np.ones((8, 8), dtype="float32"))
    await _create_artifact_row(job_id, "relative_depth", key, size)

    context_resp = await client.get(
        f"/api/v1/projects/{project_id}/datasets/{dataset['id']}/visualization/context",
        headers=headers,
    )
    context = context_resp.json()
    layers_by_type = {layer["layer_type"]: layer for layer in context["layers"]}
    assert layers_by_type["relative_depth"]["available"] is True
    assert layers_by_type["metric_elevation"]["available"] is False
    assert "calibration failed" in layers_by_type["metric_elevation"]["unavailable_reason"]
    assert "at least 3 are required" in layers_by_type["metric_elevation"]["unavailable_reason"]
    assert layers_by_type["dsm"]["available"] is False
    # Phase 10: calibration failing (never attempted successfully) does not
    # remove the real relative_depth artifact this job still has — terrain
    # falls back to it, honestly labeled, rather than becoming unavailable.
    assert context["terrain"]["available"] is True
    assert context["terrain"]["height_kind"] == "relative_depth"
    assert context["terrain"]["min_elevation"] is None
    assert context["terrain"]["max_elevation"] is None
