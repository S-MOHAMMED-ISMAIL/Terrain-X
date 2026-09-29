"""Phase 4: metric calibration + DSM generation tests.

Covers both fast unit tests of the pure scientific module
(`geospatial.calibration` — sampling, robust fitting, validation metrics; no
backend, no model, no worker) and real integration tests that go through the
actual API, real database, the real separate worker container, and real
GeoTIFF/CSV files on disk — no mocking of calibration math, sampling, or
raster I/O in the integration tests below.
"""

import asyncio
import uuid

import numpy as np
import pytest
import rasterio
from affine import Affine
from rasterio.crs import CRS
from rasterio.warp import transform_bounds

from app.core.config import get_settings
from app.services.calibration_pipeline import EXPECTED_SCALE_SIGN, quality_gate_policy
from geospatial.calibration import (
    G0_VALIDATION_FEASIBILITY,
    G1_EXPECTED_SCALE_SIGN,
    G2_HELDOUT_SKILL,
    QUALITY_POLICY_STATEMENT,
    CalibrationSamples,
    DegenerateCalibrationError,
    QualityGatePolicy,
    RobustFitResult,
    assign_spatial_blocks,
    average_ranks,
    compute_fit_diagnostics,
    compute_validation_metrics,
    cross_validate_affine,
    cross_validate_samples,
    evaluate_quality_gate,
    fit_robust_affine,
    sample_dem_pairs,
    sample_gcp_pairs,
    spearman_rank_correlation,
)
from tests.fixtures import (
    make_dem_geotiff_bytes,
    make_depth_consistent_dem_geotiff_bytes,
    make_gcp_csv_bytes,
    make_plain_tiff_bytes,
    make_structured_scene_geotiff_bytes,
    make_structured_scene_tiff_bytes_no_georef,
    pixel_center_map_coords,
    real_structured_scene_depth,
)

# --------------------------------------------------------------------------
# Unit tests: geospatial.calibration (pure, no backend/model/worker)
# --------------------------------------------------------------------------


def test_fit_robust_affine_recovers_known_scale_and_offset_exactly():
    """Deterministic synthetic case with a KNOWN a/b: relative depth is an
    arbitrary but fixed pattern, reference elevation is computed EXACTLY as
    Z = a*D + b with no noise. A correct OLS fit must recover a/b to within
    floating-point tolerance — this is the core scientific correctness check
    for the calibration model itself."""
    depth = np.linspace(0.1, 9.9, 50)
    true_a, true_b = 3.5, -12.0
    elevation = true_a * depth + true_b

    fit = fit_robust_affine(depth, elevation, outlier_sigma=2.5, max_iterations=5)

    assert abs(fit.scale - true_a) < 1e-6
    assert abs(fit.offset - true_b) < 1e-6
    assert fit.inlier_mask.all()


def test_fit_robust_affine_rejects_outliers_and_still_recovers_known_fit():
    """The same known a/b relationship, but 10% of points are corrupted with
    large errors. Robust (sigma-clipped) fitting must recognize them as
    outliers and still recover a fit close to the true a/b — this is the
    documented alternative to RANSAC required by the spec."""
    rng = np.random.default_rng(7)
    depth = np.linspace(0.1, 9.9, 200)
    true_a, true_b = 2.0, 5.0
    elevation = true_a * depth + true_b

    outlier_idx = rng.choice(len(depth), size=20, replace=False)
    elevation = elevation.copy()
    elevation[outlier_idx] += rng.choice([-1, 1], size=20) * 50.0

    fit = fit_robust_affine(depth, elevation, outlier_sigma=2.5, max_iterations=8)

    assert abs(fit.scale - true_a) < 0.1
    assert abs(fit.offset - true_b) < 1.0
    # The injected outliers must actually have been excluded from the final
    # inlier set, not just diluted into a slightly-worse-but-still-passing fit.
    assert not fit.inlier_mask[outlier_idx].any()
    assert fit.inlier_mask.sum() >= len(depth) - 30


def test_fit_robust_affine_raises_on_zero_variance_depth():
    """Constant relative depth carries no information to determine a scale
    from — this must be a real, explicit failure, never a fabricated fit."""
    depth = np.full(20, 5.0)
    elevation = np.linspace(0, 100, 20)
    try:
        fit_robust_affine(depth, elevation, outlier_sigma=2.5, max_iterations=5)
        raise AssertionError("expected DegenerateCalibrationError")
    except DegenerateCalibrationError:
        pass


def test_compute_validation_metrics_matches_hand_computed_residuals():
    """Real residuals with known, hand-computed MAE/RMSE/bias/min/max —
    verifies the validation metrics are genuine statistics, not placeholders."""
    depth = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    predicted = 2.0 * depth  # scale=2, offset=0
    residuals = np.array([1.0, -1.0, 2.0, -2.0, 0.0])
    reference = predicted - residuals

    fit = RobustFitResult(
        scale=2.0,
        offset=0.0,
        inlier_mask=np.ones(5, dtype=bool),
        iterations_used=1,
        outlier_sigma=2.5,
    )
    metrics = compute_validation_metrics(depth, reference, fit)

    assert abs(metrics.mae - 1.2) < 1e-9
    assert abs(metrics.rmse - np.sqrt(2.0)) < 1e-9
    assert abs(metrics.bias - 0.0) < 1e-9
    assert metrics.min_residual == -2.0
    assert metrics.max_residual == 2.0
    assert metrics.sample_count == 5
    assert metrics.inlier_count == 5
    assert metrics.outlier_count == 0


def test_sample_dem_pairs_excludes_nodata_pixels(tmp_path):
    """A real DEM with a genuine NoData block must never contaminate the
    calibration pairs — those pixels must be excluded, not turned into 0 or
    left as -9999 in the reference elevation array."""
    width = height = 32
    dem_bytes = make_dem_geotiff_bytes(width=width, height=height, nodata=-9999.0)
    dem_path = tmp_path / "dem.tif"
    dem_path.write_bytes(dem_bytes)

    depth = np.random.default_rng(1).random((height, width)) * 10.0
    source_crs = CRS.from_epsg(32633)
    source_transform = Affine(2.0, 0.0, 500000.0, 0.0, -2.0, 4649984.0)

    samples = sample_dem_pairs(
        depth, source_crs, source_transform, dem_path, max_samples=width * height
    )

    assert samples.total_candidates == width * height
    # The fixture punches a 5x5 NoData hole -> those 25 candidate points must
    # be dropped from the valid set.
    assert samples.valid_count == samples.total_candidates - 25
    assert not np.any(samples.reference_elevation == -9999.0)


def test_sample_dem_pairs_excludes_nan_and_inf_depth_pixels(tmp_path):
    """NaN/Inf values in the relative-depth array itself (not just the DEM)
    must also never enter the calibration statistics."""
    width = height = 16
    dem_bytes = make_dem_geotiff_bytes(width=width, height=height, nodata=-9999.0)
    dem_path = tmp_path / "dem.tif"
    dem_path.write_bytes(dem_bytes)

    depth = np.random.default_rng(2).random((height, width)) * 10.0
    depth[0, 0] = np.nan
    depth[1, 1] = np.inf
    depth[2, 2] = -np.inf

    source_crs = CRS.from_epsg(32633)
    source_transform = Affine(2.0, 0.0, 500000.0, 0.0, -2.0, 4649984.0)

    samples = sample_dem_pairs(
        depth, source_crs, source_transform, dem_path, max_samples=width * height
    )

    assert np.isfinite(samples.relative_depth).all()
    assert np.isfinite(samples.reference_elevation).all()


def test_sample_dem_pairs_reprojects_when_dem_crs_differs(tmp_path):
    """A real DEM in a different CRS than the source image must be
    coordinate-transformed, never index-matched directly. Verifies the
    reprojected flag and that real overlapping-footprint sampling actually
    produces valid pairs after the transform."""
    width = height = 32
    source_crs = CRS.from_epsg(32633)
    source_transform = Affine(2.0, 0.0, 500000.0, 0.0, -2.0, 4649984.0)
    left, bottom, right, top = (
        500000.0,
        4649984.0 - height * 2.0,
        500000.0 + width * 2.0,
        4649984.0,
    )

    # Real reprojected bounds of the source footprint into EPSG:4326, with a
    # margin, so the DEM (a different CRS) genuinely covers the same area.
    wgs_left, wgs_bottom, wgs_right, wgs_top = transform_bounds(
        source_crs, CRS.from_epsg(4326), left, bottom, right, top
    )
    margin_x = (wgs_right - wgs_left) * 0.1
    margin_y = (wgs_top - wgs_bottom) * 0.1

    dem_width = dem_height = 64
    pixel_size_x = (wgs_right - wgs_left + 2 * margin_x) / dem_width
    pixel_size_y = (wgs_top - wgs_bottom + 2 * margin_y) / dem_height
    dem_bytes = make_dem_geotiff_bytes(
        width=dem_width,
        height=dem_height,
        crs="EPSG:4326",
        origin_x=wgs_left - margin_x,
        origin_y=wgs_top + margin_y,
        pixel_size=max(pixel_size_x, pixel_size_y),
        nodata=-9999.0,
    )
    dem_path = tmp_path / "dem_wgs84.tif"
    dem_path.write_bytes(dem_bytes)

    depth = np.random.default_rng(3).random((height, width)) * 10.0
    samples = sample_dem_pairs(depth, source_crs, source_transform, dem_path, max_samples=200)

    assert samples.reprojected is True
    assert samples.source_crs != samples.reference_crs
    assert samples.valid_count > 0


def test_sample_gcp_pairs_rejects_out_of_bounds_points():
    """A GCP whose (x, y) falls outside the source image's actual pixel
    footprint must be dropped, never given a fabricated correspondence."""
    depth = np.random.default_rng(4).random((32, 32)) * 10.0
    source_crs = CRS.from_epsg(32633)
    source_transform = Affine(2.0, 0.0, 500000.0, 0.0, -2.0, 4649984.0)

    gcp_points = [
        {"x": 500010.0, "y": 4649970.0, "z": 100.0},  # inside
        {"x": 600000.0, "y": 4600000.0, "z": 200.0},  # far outside
    ]

    samples = sample_gcp_pairs(depth, source_crs, source_transform, gcp_points, "EPSG:32633")

    assert samples.total_candidates == 2
    assert samples.valid_count == 1


def test_sample_gcp_pairs_reprojects_when_gcp_crs_differs():
    """GCPs declared in a different CRS than the source image must be
    coordinate-transformed before pixel lookup, not compared directly."""
    depth = np.random.default_rng(5).random((32, 32)) * 10.0
    source_crs = CRS.from_epsg(32633)
    source_transform = Affine(2.0, 0.0, 500000.0, 0.0, -2.0, 4649984.0)

    # A real point known to be inside the UTM footprint, expressed in its
    # true WGS84 lon/lat (computed via a real coordinate transform, not
    # invented) so the reprojected-then-sampled point is genuinely in-bounds.
    from rasterio.warp import transform as warp_transform

    lon, lat = warp_transform(source_crs, CRS.from_epsg(4326), [500020.0], [4649970.0])
    gcp_points = [{"x": lon[0], "y": lat[0], "z": 123.4}]

    samples = sample_gcp_pairs(depth, source_crs, source_transform, gcp_points, "EPSG:4326")

    assert samples.reprojected is True
    assert samples.valid_count == 1


# --------------------------------------------------------------------------
# Integration tests: real API, real database, real separate worker container
# --------------------------------------------------------------------------

# All fixtures below share one real-world UTM footprint so a source image,
# DEM, and GCP CSV genuinely overlap.
_ORIGIN_X, _ORIGIN_Y, _PIXEL_SIZE, _UTM_CRS = 500000.0, 4649984.0, 2.0, "EPSG:32633"


async def _register_and_login(client, email: str, password: str = "supersecret123") -> dict:
    await client.post("/api/v1/auth/register", json={"email": email, "password": password})
    resp = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


async def _create_project(client, headers: dict, name: str = "Calibration Test Project") -> str:
    resp = await client.post("/api/v1/projects", json={"name": name}, headers=headers)
    return resp.json()["id"]


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


def _depth_consistent_dem_bytes(scale: float = 2.0, **kwargs) -> bytes:
    """DEM = scale * (real model depth of the uploaded scene) + 100 + small
    noise, on the source image's own grid. A pipeline-behavior fixture, NOT
    accuracy evidence — see make_depth_consistent_dem_geotiff_bytes."""
    return make_depth_consistent_dem_geotiff_bytes(
        real_structured_scene_depth(64, 64),
        scale=scale,
        crs=_UTM_CRS,
        origin_x=_ORIGIN_X,
        origin_y=_ORIGIN_Y,
        pixel_size=_PIXEL_SIZE,
        **kwargs,
    )


async def _upload_dem_reference(
    client, headers, project_id, filename="dem.tif", content: bytes | None = None
) -> dict:
    # Default: a reference with a deliberately depth-consistent (positive)
    # relationship, so the calibration quality gate has a real relationship
    # to accept. The previous default (an unrelated planar ramp) had no
    # physical relationship to the scene at all.
    if content is None:
        content = _depth_consistent_dem_bytes()
    resp = await client.post(
        f"/api/v1/projects/{project_id}/datasets",
        files={"file": (filename, content, "image/tiff")},
        data={"role": "dem_reference"},
        headers=headers,
    )
    return resp.json()


async def _upload_gcp_reference(
    client, headers, project_id, filename="gcps.csv", gcp_crs="EPSG:32633+5703", points=None
) -> dict:
    content = make_gcp_csv_bytes(points)
    resp = await client.post(
        f"/api/v1/projects/{project_id}/datasets",
        files={"file": (filename, content, "text/csv")},
        data={"role": "gcp_reference", "gcp_crs": gcp_crs},
        headers=headers,
    )
    return resp.json()


async def _create_job(client, headers, project_id, dataset_id, parameters=None) -> dict:
    body = {"parameters": {"version": "v1", **(parameters or {})}}
    resp = await client.post(
        f"/api/v1/projects/{project_id}/datasets/{dataset_id}/analysis",
        json=body,
        headers=headers,
    )
    return resp


async def _wait_for_terminal(client, project_id, job_id, headers, timeout=120.0) -> dict:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        resp = await client.get(f"/api/v1/projects/{project_id}/analysis/{job_id}", headers=headers)
        body = resp.json()
        if body["status"] not in ("queued", "running"):
            return body
        await asyncio.sleep(0.3)
    raise AssertionError(f"Job {job_id} did not reach a terminal status within {timeout}s")


async def _list_artifacts(client, project_id, job_id, headers) -> list[dict]:
    resp = await client.get(
        f"/api/v1/projects/{project_id}/analysis/{job_id}/artifacts", headers=headers
    )
    return resp.json()


async def test_dem_calibration_full_pipeline_completes_and_produces_artifacts(client):
    headers = await _register_and_login(client, "cal-owner1@example.com")
    project_id = await _create_project(client, headers)
    source = await _upload_source_image(client, headers, project_id)
    dem = await _upload_dem_reference(client, headers, project_id)
    assert source["status"] == "valid"
    assert dem["status"] == "valid"
    assert dem["role"] == "dem_reference"

    resp = await _create_job(
        client, headers, project_id, source["id"], {"dem_reference_dataset_id": dem["id"]}
    )
    assert resp.status_code == 201
    job = resp.json()

    final = await _wait_for_terminal(client, project_id, job["id"], headers)
    assert final["status"] == "completed"
    assert final["calibration_status"] == "calibrated"

    metadata = final["calibration_metadata"]
    assert metadata["reference_type"] == "dem"
    assert metadata["reference_dataset_id"] == dem["id"]
    assert isinstance(metadata["scale_a"], float)
    assert isinstance(metadata["offset_b"], float)
    assert metadata["valid_samples"] > 0
    assert metadata["inlier_samples"] + metadata["outlier_samples"] == metadata["valid_samples"]
    vm = metadata["validation_metrics"]
    assert all(k in vm for k in ("mae", "rmse", "bias", "min_residual", "max_residual"))
    # Never a fabricated "accuracy percentage" field.
    assert "accuracy" not in metadata and "accuracy_percent" not in vm

    artifacts = await _list_artifacts(client, project_id, job["id"], headers)
    types = {a["artifact_type"] for a in artifacts}
    # P1-3: a gate-passed calibration also yields the DTM/nDSM estimates;
    # P1-5: and the sample-point calibration residuals.
    assert types == {
        "relative_depth",
        "metric_elevation",
        "dsm",
        "dtm",
        "ndsm",
        "calibration_residuals",
    }


# Source pixels spread across the scene's sky/ground/foreground regions, so
# the real model depth genuinely differs between them.
_GCP_PIXELS = [(5, 10), (30, 50), (45, 20), (60, 32)]


def _depth_consistent_gcp_points(pixels, zs=None) -> list[tuple[float, float, float]]:
    """GCPs placed exactly on known source pixels. By default z = 2 * (real
    model depth at that pixel) + 100 — a pipeline-behavior fixture, NOT
    accuracy evidence. `zs` overrides the elevations."""
    depth = real_structured_scene_depth(64, 64)
    points = []
    for i, (row, col) in enumerate(pixels):
        x, y = pixel_center_map_coords(
            row, col, origin_x=_ORIGIN_X, origin_y=_ORIGIN_Y, pixel_size=_PIXEL_SIZE
        )
        z = 2.0 * float(depth[row, col]) + 100.0 if zs is None else zs[i]
        points.append((x, y, z))
    return points


async def test_gcp_calibration_full_pipeline_completes_and_produces_artifacts(client):
    headers = await _register_and_login(client, "cal-owner2@example.com")
    project_id = await _create_project(client, headers)
    source = await _upload_source_image(client, headers, project_id)
    gcp = await _upload_gcp_reference(
        client, headers, project_id, points=_depth_consistent_gcp_points(_GCP_PIXELS)
    )
    assert gcp["status"] == "valid"
    assert gcp["gcp_point_count"] == 4

    resp = await _create_job(
        client, headers, project_id, source["id"], {"gcp_reference_dataset_id": gcp["id"]}
    )
    assert resp.status_code == 201
    job = resp.json()

    final = await _wait_for_terminal(client, project_id, job["id"], headers)
    assert final["status"] == "completed"
    assert final["calibration_status"] == "calibrated", final["calibration_metadata"]
    assert final["calibration_metadata"]["reference_type"] == "gcp"
    assert final["calibration_metadata"]["gcp_point_count"] == 4

    artifacts = await _list_artifacts(client, project_id, job["id"], headers)
    types = {a["artifact_type"] for a in artifacts}
    # P1-3: a gate-passed calibration also yields the DTM/nDSM estimates;
    # P1-5: and the sample-point calibration residuals.
    assert types == {
        "relative_depth",
        "metric_elevation",
        "dsm",
        "dtm",
        "ndsm",
        "calibration_residuals",
    }


async def test_metric_elevation_and_dsm_are_real_georeferenced_float32_rasters(client, tmp_path):
    headers = await _register_and_login(client, "cal-owner3@example.com")
    project_id = await _create_project(client, headers)
    source = await _upload_source_image(client, headers, project_id)
    dem = await _upload_dem_reference(client, headers, project_id)

    resp = await _create_job(
        client, headers, project_id, source["id"], {"dem_reference_dataset_id": dem["id"]}
    )
    job = resp.json()
    final = await _wait_for_terminal(client, project_id, job["id"], headers)
    assert final["calibration_status"] == "calibrated"

    artifacts = await _list_artifacts(client, project_id, job["id"], headers)
    for artifact in artifacts:
        if artifact["artifact_type"] not in ("metric_elevation", "dsm"):
            continue
        dl = await client.get(
            f"/api/v1/projects/{project_id}/analysis/{job['id']}/artifacts/{artifact['id']}/download",
            headers=headers,
        )
        assert dl.status_code == 200
        out_path = tmp_path / f"{artifact['artifact_type']}.tif"
        out_path.write_bytes(dl.content)

        with rasterio.open(out_path) as raster:
            assert raster.count == 1
            assert raster.dtypes[0] == "float32"
            assert raster.width == 64
            assert raster.height == 64
            assert raster.crs is not None
            assert raster.crs.to_string() == _UTM_CRS
            data = raster.read(1)
            assert not np.isnan(data).any()
            assert not np.isinf(data).any()


async def test_dsm_provenance_chain_references_metric_elevation_and_depth(client):
    """Every artifact must be traceable through DSM -> metric elevation ->
    relative depth -> source dataset -> calibration reference, per the
    spec's provenance requirement — checked here by reading real artifact
    metadata, not asserted from memory."""
    headers = await _register_and_login(client, "cal-owner4@example.com")
    project_id = await _create_project(client, headers)
    source = await _upload_source_image(client, headers, project_id)
    dem = await _upload_dem_reference(client, headers, project_id)

    resp = await _create_job(
        client, headers, project_id, source["id"], {"dem_reference_dataset_id": dem["id"]}
    )
    job = resp.json()
    final = await _wait_for_terminal(client, project_id, job["id"], headers)
    assert final["calibration_status"] == "calibrated"

    artifacts = await _list_artifacts(client, project_id, job["id"], headers)
    by_type = {a["artifact_type"]: a for a in artifacts}

    depth_artifact_id = by_type["relative_depth"]["id"]
    metric_id = by_type["metric_elevation"]["id"]
    dsm_id = by_type["dsm"]["id"]

    metric_meta = by_type["metric_elevation"]["artifact_metadata"]
    assert metric_meta["depth_artifact_id"] == depth_artifact_id
    assert metric_meta["source_dataset_id"] == source["id"]

    dsm_meta = by_type["dsm"]["artifact_metadata"]
    assert dsm_meta["metric_elevation_artifact_id"] == metric_id
    assert dsm_meta["depth_artifact_id"] == depth_artifact_id

    assert final["calibration_metadata"]["metric_elevation_artifact_id"] == metric_id
    assert final["calibration_metadata"]["dsm_artifact_id"] == dsm_id
    assert final["calibration_metadata"]["reference_dataset_id"] == dem["id"]


async def test_non_georeferenced_source_calibration_fails_but_job_completes(client):
    """The critical georeferencing rule: a non-georeferenced source image
    must never produce a fabricated CRS/DSM. The job itself still succeeds
    (relative depth alone is a valid result) but calibration_status=failed
    with a real, honest reason, and no metric_elevation/DSM artifact exists."""
    headers = await _register_and_login(client, "cal-owner5@example.com")
    project_id = await _create_project(client, headers)

    plain_resp = await client.post(
        f"/api/v1/projects/{project_id}/datasets",
        files={
            "file": (
                "scene_nogeoref.tif",
                make_structured_scene_tiff_bytes_no_georef(64, 64),
                "image/tiff",
            )
        },
        headers=headers,
    )
    source = plain_resp.json()
    assert source["status"] == "valid"
    assert source["is_georeferenced"] is False

    dem = await _upload_dem_reference(client, headers, project_id)

    resp = await _create_job(
        client, headers, project_id, source["id"], {"dem_reference_dataset_id": dem["id"]}
    )
    assert resp.status_code == 201
    job = resp.json()

    final = await _wait_for_terminal(client, project_id, job["id"], headers)
    assert final["status"] == "completed"
    assert final["calibration_status"] == "failed"
    assert "not georeferenced" in final["calibration_metadata"]["error"]

    artifacts = await _list_artifacts(client, project_id, job["id"], headers)
    types = {a["artifact_type"] for a in artifacts}
    assert types == {"relative_depth"}


async def test_insufficient_gcp_points_rejected_at_upload(client):
    headers = await _register_and_login(client, "cal-owner6@example.com")
    project_id = await _create_project(client, headers)

    dataset = await _upload_gcp_reference(
        client,
        headers,
        project_id,
        points=[(500010.0, 4649970.0, 100.0), (500020.0, 4649960.0, 110.0)],
    )
    assert dataset["status"] == "invalid"
    assert "at least" in dataset["validation_error"]


async def test_invalid_gcp_crs_rejected_at_upload(client):
    headers = await _register_and_login(client, "cal-owner7@example.com")
    project_id = await _create_project(client, headers)

    resp = await client.post(
        f"/api/v1/projects/{project_id}/datasets",
        files={"file": ("gcps.csv", make_gcp_csv_bytes(), "text/csv")},
        data={"role": "gcp_reference", "gcp_crs": "NOT_A_REAL_CRS"},
        headers=headers,
    )
    assert resp.status_code == 422


async def test_dem_reference_must_be_georeferenced(client):
    """A DEM with no CRS cannot possibly be used to align to the source
    image, so it must be rejected (marked invalid) at upload time rather
    than accepted and failing mysteriously later."""
    headers = await _register_and_login(client, "cal-owner8@example.com")
    project_id = await _create_project(client, headers)

    resp = await client.post(
        f"/api/v1/projects/{project_id}/datasets",
        files={"file": ("dem_nogeoref.tif", make_plain_tiff_bytes(32, 32), "image/tiff")},
        data={"role": "dem_reference"},
        headers=headers,
    )
    dataset = resp.json()
    assert dataset["status"] == "invalid"
    assert "georeferenced" in dataset["validation_error"]


async def test_calibration_reference_with_wrong_role_rejected(client):
    """A dataset uploaded as a plain source image cannot be cited as a DEM
    reference just because it happens to be a valid georeferenced raster —
    role is a real, enforced distinction, not a client-chosen label."""
    headers = await _register_and_login(client, "cal-owner9@example.com")
    project_id = await _create_project(client, headers)
    source = await _upload_source_image(client, headers, project_id)
    other_source = await _upload_source_image(client, headers, project_id, filename="scene2.tif")

    resp = await _create_job(
        client,
        headers,
        project_id,
        source["id"],
        {"dem_reference_dataset_id": other_source["id"]},
    )
    assert resp.status_code == 422
    assert "role" in resp.json()["error"]["message"]


async def test_calibration_reference_not_found_rejected(client):
    headers = await _register_and_login(client, "cal-owner10@example.com")
    project_id = await _create_project(client, headers)
    source = await _upload_source_image(client, headers, project_id)

    resp = await _create_job(
        client,
        headers,
        project_id,
        source["id"],
        {"dem_reference_dataset_id": str(uuid.uuid4())},
    )
    assert resp.status_code == 422


async def test_dem_and_gcp_both_set_rejected(client):
    headers = await _register_and_login(client, "cal-owner11@example.com")
    project_id = await _create_project(client, headers)
    source = await _upload_source_image(client, headers, project_id)
    dem = await _upload_dem_reference(client, headers, project_id)
    gcp = await _upload_gcp_reference(client, headers, project_id)

    resp = await _create_job(
        client,
        headers,
        project_id,
        source["id"],
        {"dem_reference_dataset_id": dem["id"], "gcp_reference_dataset_id": gcp["id"]},
    )
    assert resp.status_code == 422


async def test_cross_user_cannot_access_calibration_job_or_artifacts(client):
    owner_headers = await _register_and_login(client, "cal-owner12@example.com")
    intruder_headers = await _register_and_login(client, "cal-intruder12@example.com")
    project_id = await _create_project(client, owner_headers)
    source = await _upload_source_image(client, owner_headers, project_id)
    dem = await _upload_dem_reference(client, owner_headers, project_id)

    resp = await _create_job(
        client, owner_headers, project_id, source["id"], {"dem_reference_dataset_id": dem["id"]}
    )
    job = resp.json()
    final = await _wait_for_terminal(client, project_id, job["id"], owner_headers)
    assert final["calibration_status"] == "calibrated"

    artifacts = await _list_artifacts(client, project_id, job["id"], owner_headers)
    metric_id = next(a["id"] for a in artifacts if a["artifact_type"] == "metric_elevation")

    job_resp = await client.get(
        f"/api/v1/projects/{project_id}/analysis/{job['id']}", headers=intruder_headers
    )
    assert job_resp.status_code == 404

    dl_resp = await client.get(
        f"/api/v1/projects/{project_id}/analysis/{job['id']}/artifacts/{metric_id}/download",
        headers=intruder_headers,
    )
    assert dl_resp.status_code == 404


async def test_reference_dataset_from_other_project_rejected(client):
    headers = await _register_and_login(client, "cal-owner13@example.com")
    project_a = await _create_project(client, headers, "Project A")
    project_b = await _create_project(client, headers, "Project B")
    source = await _upload_source_image(client, headers, project_a)
    dem_in_b = await _upload_dem_reference(client, headers, project_b)

    resp = await _create_job(
        client,
        headers,
        project_a,
        source["id"],
        {"dem_reference_dataset_id": dem_in_b["id"]},
    )
    assert resp.status_code == 422


async def test_job_without_reference_never_attempts_calibration(client):
    """A plain Phase 3-style job (no reference at all) must stay
    uncalibrated and produce only the relative_depth artifact — calibration
    is opt-in, never attempted implicitly."""
    headers = await _register_and_login(client, "cal-owner14@example.com")
    project_id = await _create_project(client, headers)
    source = await _upload_source_image(client, headers, project_id)

    resp = await _create_job(client, headers, project_id, source["id"])
    job = resp.json()
    assert job["calibration_status"] == "uncalibrated"

    final = await _wait_for_terminal(client, project_id, job["id"], headers)
    assert final["status"] == "completed"
    assert final["calibration_status"] == "uncalibrated"
    assert final["calibration_metadata"] is None

    artifacts = await _list_artifacts(client, project_id, job["id"], headers)
    assert {a["artifact_type"] for a in artifacts} == {"relative_depth"}


# --------------------------------------------------------------------------
# P1-2 calibration quality gate — pure unit tests (deterministic, no
# backend/model/worker)
# --------------------------------------------------------------------------

_POLICY = QualityGatePolicy(
    version="v1", min_cv_skill=0.0, expected_scale_sign=1, cv_blocks_per_side=4
)
_SIGMA, _ITERS = 2.5, 5


def _grid(height: int, width: int) -> tuple[np.ndarray, np.ndarray]:
    rows, cols = np.meshgrid(np.arange(height), np.arange(width), indexing="ij")
    return rows.ravel(), cols.ravel()


def _dem_samples(depth, reference, rows, cols) -> CalibrationSamples:
    return CalibrationSamples(
        relative_depth=np.asarray(depth, dtype="float64"),
        reference_elevation=np.asarray(reference, dtype="float64"),
        total_candidates=len(depth),
        valid_count=len(depth),
        source_crs="EPSG:32633",
        reference_crs="EPSG:32633",
        reprojected=False,
        sample_rows=np.asarray(rows),
        sample_cols=np.asarray(cols),
    )


def _gate_dem(depth, reference, height, width, policy=_POLICY):
    rows, cols = _grid(height, width)
    samples = _dem_samples(depth, reference, rows, cols)
    fit = fit_robust_affine(
        samples.relative_depth,
        samples.reference_elevation,
        outlier_sigma=_SIGMA,
        max_iterations=_ITERS,
    )
    cv = cross_validate_samples(
        samples,
        reference_type="dem",
        source_height=height,
        source_width=width,
        policy=policy,
        outlier_sigma=_SIGMA,
        max_iterations=_ITERS,
    )
    return fit, cv, evaluate_quality_gate(fit, cv, policy)


def _gate_gcp(depth, reference):
    depth = np.asarray(depth, dtype="float64")
    reference = np.asarray(reference, dtype="float64")
    samples = CalibrationSamples(
        relative_depth=depth,
        reference_elevation=reference,
        total_candidates=len(depth),
        valid_count=len(depth),
        source_crs="EPSG:32633",
        reference_crs="EPSG:32633",
        reprojected=False,
        sample_rows=np.arange(len(depth)),
        sample_cols=np.arange(len(depth)),
    )
    fit = fit_robust_affine(depth, reference, outlier_sigma=_SIGMA, max_iterations=_ITERS)
    cv = cross_validate_samples(
        samples,
        reference_type="gcp",
        source_height=len(depth),
        source_width=len(depth),
        policy=_POLICY,
        outlier_sigma=_SIGMA,
        max_iterations=_ITERS,
    )
    return fit, cv, evaluate_quality_gate(fit, cv, _POLICY)


def _failed(gate) -> set[str]:
    return {c["criterion"] for c in gate.failed_criteria}


def test_gate_exact_positive_relationship_passes_with_unchanged_fit():
    """(1) Z = 2D + 100 exactly: the gate passes, held-out skill is ~1, and
    the production a/b are bit-identical to calling fit_robust_affine alone
    — the gate never alters the fit it evaluates."""
    rows, cols = _grid(32, 32)
    depth = 0.1 * rows + 0.05 * cols + np.sin(rows * 0.7) * 0.3
    reference = 2.0 * depth + 100.0

    fit, cv, gate = _gate_dem(depth, reference, 32, 32)
    standalone = fit_robust_affine(depth, reference, outlier_sigma=_SIGMA, max_iterations=_ITERS)

    assert gate.passed and gate.failed_criteria == () and gate.not_evaluated == ()
    assert cv.feasible and cv.skill == pytest.approx(1.0, abs=1e-9)
    assert cv.method == "leave_one_spatial_block_out" and cv.fold_count == 16
    assert fit.scale == standalone.scale and fit.offset == standalone.offset


def test_gate_true_negative_relationship_fails_expected_scale_sign():
    """(2) Z = -2D + 100: held-out skill is excellent (the relationship IS
    predictive), but the sign contradicts the pipeline's depth convention —
    only G1 fails, and the failure carries the real fitted scale."""
    rows, cols = _grid(32, 32)
    depth = 0.1 * rows + 0.05 * cols
    fit, cv, gate = _gate_dem(depth, -2.0 * depth + 100.0, 32, 32)

    assert not gate.passed
    assert _failed(gate) == {G1_EXPECTED_SCALE_SIGN}
    failure = gate.failed_criteria[0]
    assert failure["scale_a"] == pytest.approx(-2.0)
    assert failure["expected_scale_sign"] == 1
    assert cv.skill == pytest.approx(1.0, abs=1e-9)


def test_gate_independent_depth_fails_heldout_skill_although_in_sample_r2_is_positive():
    """(3) Depth drawn independently of the reference (seeded). By chance
    the fit has a positive scale and a clearly positive in-sample R² — a
    naive "R² > 0" gate would accept it — but leave-one-out skill is <= 0,
    so G2 rejects it."""
    rng = np.random.default_rng(8)
    depth = rng.uniform(0.0, 5.0, 12)
    reference = rng.normal(100.0, 5.0, 12)

    fit, cv, gate = _gate_gcp(depth, reference)
    diagnostics = compute_fit_diagnostics(depth, reference, fit, effective_sample_count=12)

    assert fit.scale > 0.0
    assert diagnostics.in_sample_r2 > 0.1
    assert cv.skill <= 0.0
    assert _failed(gate) == {G2_HELDOUT_SKILL}
    assert gate.failed_criteria[0]["cv_skill"] == pytest.approx(cv.skill)


def test_dense_independent_noise_fails_heldout_skill():
    """(3, dense DEM-like case) 1600 grid samples of reference noise that is
    independent of depth: held-out skill is not above 0."""
    rng = np.random.default_rng(0)
    depth = rng.uniform(0.0, 5.0, 40 * 40)
    reference = rng.normal(100.0, 5.0, 40 * 40)
    _, cv, gate = _gate_dem(depth, reference, 40, 40)
    assert cv.feasible and cv.skill <= 0.0
    assert G2_HELDOUT_SKILL in _failed(gate)


def test_blocked_cv_is_not_fooled_like_random_split_on_autocorrelated_fields():
    """(4) Two smooth, spatially autocorrelated fields that are not
    generated from each other. A random split leaks each held-out sample's
    near-identical neighbours into training, so its skill just reproduces
    the in-sample R²; leave-one-spatial-block-out holds out whole regions
    and reports far less skill. Block assignment is deterministic."""
    rows, cols = _grid(32, 32)
    depth = np.sin(rows / 32 * np.pi) + 0.3 * np.cos(cols / 32 * np.pi)
    reference = 100 + 10 * np.sin(rows / 32 * np.pi * 1.3 + 0.5) + 2 * np.sin(cols / 32 * 2 * np.pi)

    fit, blocked, _ = _gate_dem(depth, reference, 32, 32)
    diagnostics = compute_fit_diagnostics(depth, reference, fit, effective_sample_count=None)
    random_groups = np.random.default_rng(3).integers(0, 16, rows.size)
    random_split = cross_validate_affine(
        depth,
        reference,
        random_groups,
        method="random",
        outlier_sigma=_SIGMA,
        max_iterations=_ITERS,
    )

    assert random_split.skill == pytest.approx(diagnostics.in_sample_r2, abs=0.01)
    assert blocked.skill < random_split.skill - 0.05
    _, blocked_again, _ = _gate_dem(depth, reference, 32, 32)
    assert blocked_again.as_dict() == blocked.as_dict()


def test_heldout_metrics_include_outliers_and_never_clip_the_heldout_side():
    """(5) Exact Z = 2D + 100 plus ONE gross +50 outlier. Every training
    fold that contains it clips it (so every other held-out prediction is
    exact); the fold that holds it out still scores it. Held-out SSE is
    therefore exactly the outlier's 50² — a clipped held-out side would
    report 0."""
    rows, cols = _grid(16, 16)
    depth = 0.1 * rows + 0.07 * cols
    reference = 2.0 * depth + 100.0
    reference[37] += 50.0

    _, cv, _ = _gate_dem(depth, reference, 16, 16)

    assert cv.heldout_sample_count == 256
    assert cv.sse_cv == pytest.approx(2500.0, rel=1e-6)
    assert cv.heldout_mae == pytest.approx(50.0 / 256, rel=1e-6)
    assert cv.heldout_bias == pytest.approx(-50.0 / 256, rel=1e-6)


def test_assign_spatial_blocks_is_deterministic_complete_and_contiguous():
    """(6) Every sample gets exactly one block, blocks are contiguous
    rectangles in (row, col), and a non-square raster still partitions
    cleanly; out-of-raster samples are rejected, never silently binned."""
    rows, cols = _grid(8, 8)
    blocks = assign_spatial_blocks(rows, cols, height=8, width=8, blocks_per_side=4)
    assert blocks.shape == rows.shape
    assert set(blocks.tolist()) == set(range(16))
    for block in range(16):
        r, c = rows[blocks == block], cols[blocks == block]
        assert (r.max() - r.min() + 1) * (c.max() - c.min() + 1) == r.size == 4
    assert blocks.reshape(8, 8)[0, 0] == 0 and blocks.reshape(8, 8)[7, 7] == 15
    np.testing.assert_array_equal(
        blocks, assign_spatial_blocks(rows, cols, height=8, width=8, blocks_per_side=4)
    )

    rows, cols = _grid(10, 7)
    blocks = assign_spatial_blocks(rows, cols, height=10, width=7, blocks_per_side=3)
    assert set(blocks.tolist()) == set(range(9))

    try:
        assign_spatial_blocks(np.array([10]), np.array([0]), height=10, width=7, blocks_per_side=3)
        raise AssertionError("expected ValueError")
    except ValueError:
        pass


def test_gcp_leave_one_out_small_n_behavior():
    """(7) GCP calibration uses leave-one-out: n=3 is feasible (each fold
    is a two-point line); a strong n=4 relationship passes; a noisy n=4 set
    fails on held-out skill."""
    _, cv3, gate3 = _gate_gcp([1.0, 2.0, 4.0], [102.0, 104.0, 108.0])
    assert cv3.feasible and cv3.method == "leave_one_out" and cv3.fold_count == 3
    assert gate3.passed

    depth = np.array([1.0, 2.0, 3.0, 4.0])
    strong = 2.0 * depth + 100.0 + np.array([0.01, -0.02, 0.015, -0.005])
    _, cv_strong, gate_strong = _gate_gcp(depth, strong)
    assert gate_strong.passed and cv_strong.skill > 0.99

    _, cv_noisy, gate_noisy = _gate_gcp(depth, [100.0, 110.0, 95.0, 105.0])
    assert cv_noisy.skill < 0.0
    assert G2_HELDOUT_SKILL in _failed(gate_noisy)


def test_degenerate_validation_cases_fail_feasibility():
    """(8) G0: constant reference (baseline/skill undefined), all samples in
    a single spatial block, and a training fold with zero depth variance
    each make validation infeasible — G2 is then reported as not evaluated,
    never silently passed."""
    rows, cols = _grid(16, 16)
    depth = 0.1 * rows + 0.07 * cols
    _, cv, gate = _gate_dem(depth, np.full(depth.shape, 100.0), 16, 16)
    assert not cv.feasible and "zero variance" in cv.infeasibility_reason
    assert G0_VALIDATION_FEASIBILITY in _failed(gate)
    assert gate.not_evaluated == (G2_HELDOUT_SKILL,)

    # All samples inside the top-left block of a 64x64 raster.
    small_rows, small_cols = _grid(8, 8)
    samples = _dem_samples(depth[:64], 2.0 * depth[:64] + 100.0, small_rows, small_cols)
    cv_one_block = cross_validate_samples(
        samples,
        reference_type="dem",
        source_height=64,
        source_width=64,
        policy=_POLICY,
        outlier_sigma=_SIGMA,
        max_iterations=_ITERS,
    )
    assert not cv_one_block.feasible and cv_one_block.non_empty_block_count == 1

    # Depth varies only inside block 0; holding it out leaves constant depth.
    rows8, cols8 = _grid(8, 8)
    two_by_two = QualityGatePolicy(
        version="v1", min_cv_skill=0.0, expected_scale_sign=1, cv_blocks_per_side=2
    )
    block = assign_spatial_blocks(rows8, cols8, height=8, width=8, blocks_per_side=2)
    flat_depth = np.where(block == 0, rows8 * 0.5 + cols8 * 0.25, 1.0)
    _, cv_flat, gate_flat = _gate_dem(flat_depth, 2.0 * flat_depth + 100.0, 8, 8, two_by_two)
    assert not cv_flat.feasible and "zero relative-depth variance" in cv_flat.infeasibility_reason
    assert G0_VALIDATION_FEASIBILITY in _failed(gate_flat)


def test_coarse_dem_effective_sample_count_counts_distinct_dem_cells(tmp_path):
    """(9) A 16x16 source at 2 m over a 4x4 DEM at 8 m: every source pixel
    is sampled, but only 16 distinct DEM cells exist — the effective sample
    count must say 16, not 256, and each cell ID must be the cell whose
    value was actually read."""
    source_crs = CRS.from_epsg(32633)
    source_transform = Affine(2.0, 0, 500000.0, 0, -2.0, 4649984.0)
    dem_values = (100.0 + np.arange(16, dtype="float32")).reshape(4, 4)
    dem_path = tmp_path / "coarse_dem.tif"
    with rasterio.open(
        dem_path,
        "w",
        driver="GTiff",
        height=4,
        width=4,
        count=1,
        dtype="float32",
        crs=source_crs,
        transform=Affine(8.0, 0, 500000.0, 0, -8.0, 4649984.0),
    ) as dst:
        dst.write(dem_values, 1)

    depth = np.random.default_rng(1).uniform(0.0, 5.0, (16, 16)).astype("float32")
    samples = sample_dem_pairs(depth, source_crs, source_transform, dem_path, max_samples=10_000)

    assert samples.valid_count == 256
    assert samples.effective_sample_count == 16
    expected_cells = (samples.sample_rows // 4) * 4 + samples.sample_cols // 4
    np.testing.assert_array_equal(samples.reference_cell_ids, expected_cells)
    np.testing.assert_array_equal(
        samples.reference_elevation, dem_values.ravel()[samples.reference_cell_ids]
    )
    np.testing.assert_array_equal(
        samples.relative_depth, depth[samples.sample_rows, samples.sample_cols]
    )


def test_spearman_matches_hand_computed_values_including_ties():
    """(10) Hand-checked: one swapped pair in 4 gives rho = 1 - 6*2/60 =
    0.8; tied x values share their mean rank (2.5), giving rho = 3/sqrt(10);
    a constant input has no defined correlation (None, never 0)."""
    assert spearman_rank_correlation(
        np.array([1.0, 2.0, 3.0, 4.0]), np.array([1.0, 3.0, 2.0, 4.0])
    ) == pytest.approx(0.8)
    np.testing.assert_array_equal(
        average_ranks(np.array([10.0, 20.0, 20.0, 30.0])), [1.0, 2.5, 2.5, 4.0]
    )
    assert spearman_rank_correlation(
        np.array([1.0, 2.0, 2.0, 3.0]), np.array([1.0, 2.0, 3.0, 4.0])
    ) == pytest.approx(3.0 / np.sqrt(10.0))
    assert spearman_rank_correlation(np.array([5.0, 5.0, 5.0]), np.array([1.0, 2.0, 3.0])) is None


def test_quality_gate_policy_is_persisted_verbatim():
    """(11) The exact applied policy — including its "not an accuracy
    standard" statement — is part of every gate result."""
    rows, cols = _grid(16, 16)
    depth = 0.1 * rows + 0.07 * cols
    policy = QualityGatePolicy(
        version="v-test", min_cv_skill=0.25, expected_scale_sign=1, cv_blocks_per_side=2
    )
    _, _, gate = _gate_dem(depth, 2.0 * depth + 100.0, 16, 16, policy)
    persisted = gate.as_dict()["policy"]

    assert persisted == {
        "version": "v-test",
        "min_cv_skill": 0.25,
        "expected_scale_sign": 1,
        "cv_blocks_per_side": 2,
        "dem_cv_method": "leave_one_spatial_block_out",
        "gcp_cv_method": "leave_one_out",
        "statement": QUALITY_POLICY_STATEMENT,
    }
    assert "not an empirically validated accuracy standard" in persisted["statement"]


def test_pipeline_policy_comes_from_settings_and_expected_sign_constant():
    settings = get_settings()
    policy = quality_gate_policy(settings)
    assert policy.min_cv_skill == settings.CALIBRATION_MIN_CV_SKILL == 0.0
    assert policy.cv_blocks_per_side == settings.CALIBRATION_CV_BLOCKS_PER_SIDE
    assert policy.version == settings.CALIBRATION_QUALITY_POLICY_VERSION == "v1"
    assert policy.expected_scale_sign == EXPECTED_SCALE_SIGN == 1


# --------------------------------------------------------------------------
# P1-2 calibration quality gate — integration (real API, real worker, real
# Depth Anything). The references below are DELIBERATELY constructed from
# the model's own depth output: they validate pipeline behavior and are NOT
# accuracy evidence.
# --------------------------------------------------------------------------


async def _download_artifact_to(client, headers, project_id, job_id, artifact, path) -> None:
    resp = await client.get(
        f"/api/v1/projects/{project_id}/analysis/{job_id}/artifacts/{artifact['id']}/download",
        headers=headers,
    )
    assert resp.status_code == 200
    path.write_bytes(resp.content)


async def _run_dem_job(client, email, dem_bytes):
    headers = await _register_and_login(client, email)
    project_id = await _create_project(client, headers)
    source = await _upload_source_image(client, headers, project_id)
    dem = await _upload_dem_reference(client, headers, project_id, content=dem_bytes)
    resp = await _create_job(
        client, headers, project_id, source["id"], {"dem_reference_dataset_id": dem["id"]}
    )
    assert resp.status_code == 201
    final = await _wait_for_terminal(client, project_id, resp.json()["id"], headers)
    artifacts = await _list_artifacts(client, project_id, final["id"], headers)
    return headers, project_id, source, final, artifacts


async def test_depth_consistent_dem_passes_gate_with_bit_identical_fit(client, tmp_path):
    """(12) DEM = 2 * D + 100 + small noise, D being the real model's own
    depth: calibrated, quality_gate.passed, metric_elevation/dsm written,
    the new diagnostics persisted — and the persisted a/b are bit-identical
    to re-running the unchanged sampling + fit on the job's own stored depth
    artifact against the same DEM."""
    dem_bytes = _depth_consistent_dem_bytes()
    headers, project_id, _, final, artifacts = await _run_dem_job(
        client, "gate-pass@example.com", dem_bytes
    )

    assert final["status"] == "completed"
    assert final["calibration_status"] == "calibrated", final["calibration_metadata"]
    assert {a["artifact_type"] for a in artifacts} == {
        "relative_depth",
        "metric_elevation",
        "dsm",
        "dtm",
        "ndsm",
        "calibration_residuals",
    }
    assert final["ground_filter_status"] == "completed"

    metadata = final["calibration_metadata"]
    gate = metadata["quality_gate"]
    assert gate["passed"] is True and gate["failed_criteria"] == []
    assert gate["policy"] == quality_gate_policy(get_settings()).as_dict()
    assert "error" not in metadata
    assert metadata["validation_metrics_scope"] == "in_sample_inliers"
    cv = metadata["cross_validation"]
    assert cv["method"] == "leave_one_spatial_block_out" and cv["feasible"] is True
    assert cv["blocks_per_side"] == 4 and cv["fold_count"] == 16 and cv["skill"] > 0.9
    diagnostics = metadata["fit_diagnostics"]
    assert diagnostics["effective_sample_count"] == metadata["valid_samples"]  # same grid
    assert diagnostics["pearson_r"] > 0.9 and diagnostics["spearman_rho"] > 0.9
    assert metadata["scale_a"] == pytest.approx(2.0, abs=0.05)

    depth_artifact = next(a for a in artifacts if a["artifact_type"] == "relative_depth")
    depth_path, dem_path = tmp_path / "depth.tif", tmp_path / "dem.tif"
    await _download_artifact_to(
        client, headers, project_id, final["id"], depth_artifact, depth_path
    )
    dem_path.write_bytes(dem_bytes)
    with rasterio.open(depth_path) as src:
        depth, crs, transform = src.read(1), src.crs, src.transform
    settings = get_settings()
    samples = sample_dem_pairs(
        depth, crs, transform, dem_path, max_samples=settings.CALIBRATION_MAX_SAMPLES
    )
    refit = fit_robust_affine(
        samples.relative_depth,
        samples.reference_elevation,
        outlier_sigma=settings.CALIBRATION_OUTLIER_SIGMA,
        max_iterations=settings.CALIBRATION_MAX_ITERATIONS,
    )
    assert metadata["scale_a"] == refit.scale
    assert metadata["offset_b"] == refit.offset


async def test_inverted_dem_fails_expected_sign_and_falls_back_to_relative_terrain(client):
    """(13) DEM = -2 * D + 100: a strongly predictive but inverted
    relationship. Calibration FAILS on G1 only; no metric_elevation/dsm is
    written; the job still completes; all diagnostics are preserved; the
    terrain context falls back to the real relative_depth artifact."""
    headers, project_id, source, final, artifacts = await _run_dem_job(
        client, "gate-inverted@example.com", _depth_consistent_dem_bytes(scale=-2.0)
    )

    assert final["status"] == "completed"
    assert final["calibration_status"] == "failed"
    assert {a["artifact_type"] for a in artifacts} == {"relative_depth"}

    metadata = final["calibration_metadata"]
    gate = metadata["quality_gate"]
    assert gate["passed"] is False
    assert [c["criterion"] for c in gate["failed_criteria"]] == [G1_EXPECTED_SCALE_SIGN]
    assert gate["failed_criteria"][0]["scale_a"] == metadata["scale_a"] < 0
    assert "G1_expected_scale_sign" in metadata["error"]
    assert f"a={metadata['scale_a']:.6g}" in metadata["error"]
    assert metadata["cross_validation"]["skill"] > 0.9  # predictive, just inverted
    assert "fit_diagnostics" in metadata and "validation_metrics" in metadata
    assert "metric_elevation_artifact_id" not in metadata

    context = (
        await client.get(
            f"/api/v1/projects/{project_id}/datasets/{source['id']}/visualization/context",
            headers=headers,
        )
    ).json()
    assert context["terrain"]["available"] is True
    assert context["terrain"]["height_kind"] == "relative_depth"
    dsm_layer = next(layer for layer in context["layers"] if layer["layer_type"] == "dsm")
    assert dsm_layer["available"] is False
    assert "G1_expected_scale_sign" in dsm_layer["unavailable_reason"]


async def test_unrelated_dem_fails_heldout_skill(client):
    """(14) A DEM of seeded noise with no relationship to the scene's depth:
    calibration fails on G2 (held-out skill), with no metric output."""
    noise = np.random.default_rng(42).normal(100.0, 5.0, (64, 64))
    dem_bytes = make_dem_geotiff_bytes(
        crs=_UTM_CRS,
        origin_x=_ORIGIN_X,
        origin_y=_ORIGIN_Y,
        pixel_size=_PIXEL_SIZE,
        elevation_fn=lambda xx, yy: noise,
    )
    _, _, _, final, artifacts = await _run_dem_job(client, "gate-unrelated@example.com", dem_bytes)

    assert final["status"] == "completed"
    assert final["calibration_status"] == "failed"
    assert {a["artifact_type"] for a in artifacts} == {"relative_depth"}
    metadata = final["calibration_metadata"]
    assert G2_HELDOUT_SKILL in {c["criterion"] for c in metadata["quality_gate"]["failed_criteria"]}
    assert metadata["cross_validation"]["skill"] <= 0.0
    assert "G2_heldout_skill" in metadata["error"]


async def test_small_n_gcp_calibration_uses_leave_one_out(client):
    """(15) Three depth-consistent GCPs: leave-one-out is feasible and the
    calibration passes. Four GCPs whose elevations are unrelated to depth:
    leave-one-out skill is negative and the calibration fails."""
    headers = await _register_and_login(client, "gate-gcp@example.com")
    project_id = await _create_project(client, headers)
    source = await _upload_source_image(client, headers, project_id)

    consistent = await _upload_gcp_reference(
        client, headers, project_id, points=_depth_consistent_gcp_points(_GCP_PIXELS[:3])
    )
    resp = await _create_job(
        client, headers, project_id, source["id"], {"gcp_reference_dataset_id": consistent["id"]}
    )
    final = await _wait_for_terminal(client, project_id, resp.json()["id"], headers)
    assert final["calibration_status"] == "calibrated", final["calibration_metadata"]
    cv = final["calibration_metadata"]["cross_validation"]
    assert cv["method"] == "leave_one_out" and cv["fold_count"] == 3 and cv["feasible"]
    assert final["calibration_metadata"]["fit_diagnostics"]["effective_sample_count"] == 3

    depth = real_structured_scene_depth(64, 64)
    by_depth = sorted(_GCP_PIXELS, key=lambda rc: depth[rc])
    noisy = await _upload_gcp_reference(
        client,
        headers,
        project_id,
        filename="noisy.csv",
        points=_depth_consistent_gcp_points(by_depth, zs=[100.0, 110.0, 95.0, 105.0]),
    )
    resp = await _create_job(
        client, headers, project_id, source["id"], {"gcp_reference_dataset_id": noisy["id"]}
    )
    final = await _wait_for_terminal(client, project_id, resp.json()["id"], headers)
    assert final["status"] == "completed"
    assert final["calibration_status"] == "failed"
    metadata = final["calibration_metadata"]
    assert metadata["quality_gate"]["passed"] is False
    assert metadata["cross_validation"]["skill"] < 0.0
    artifacts = await _list_artifacts(client, project_id, final["id"], headers)
    assert {a["artifact_type"] for a in artifacts} == {"relative_depth"}
