"""P1-5: calibration residuals at calibration sample locations.

Pure unit tests of `geospatial.calibration`'s residual functions (sign
convention, exact values, the no-leakage property of held-out residuals,
aggregate invariants against P1-2's own persisted numbers, DEM/GCP sample
locations, outlier flags), plus integration tests through the real API: the
persisted GeoJSON artifact, the residuals endpoint, visualization context
availability/unavailability, ownership, raster-endpoint rejection, and
report/ZIP generation.

The references below are DELIBERATELY constructed from the depth the
pipeline will produce: they validate pipeline behavior and are NOT
accuracy evidence.
"""

import csv
import io
import json
import math
import uuid
import zipfile

import numpy as np
import pytest
import rasterio
from pypdf import PdfReader
from rasterio.crs import CRS
from rasterio.transform import from_origin
from rasterio.warp import transform as warp_transform

from app.db.session import AsyncSessionLocal
from app.models.analysis_job import AnalysisJob, CalibrationStatus
from app.services import analysis_execution
from app.services.calibration_pipeline import build_residuals_payload
from app.services.report_execution import generate_report
from app.services.visualization import _calibration_residuals_context
from geospatial.calibration import (
    RESIDUAL_DEFINITION,
    RESIDUALS_DISCLAIMER,
    CalibrationSamples,
    QualityGatePolicy,
    assign_spatial_blocks,
    build_residual_feature_collection,
    compute_calibration_residuals,
    compute_fit_diagnostics,
    compute_validation_metrics,
    cross_validate_samples,
    fit_robust_affine,
    sample_dem_pairs,
    sample_gcp_pairs,
    summarize_residuals,
)
from tests.fixtures import (
    make_depth_consistent_dem_geotiff_bytes,
    make_gcp_csv_bytes,
    make_structured_scene_geotiff_bytes,
    pixel_center_map_coords,
)
from tests.test_calibration import _run_dem_job, _upload_source_image
from tests.test_reports import _create_report_row, _fake_depth, _FastFakeDepthEstimator

_ORIGIN_X, _ORIGIN_Y, _PIXEL_SIZE, _UTM_CRS = 500000.0, 4649984.0, 2.0, "EPSG:32633"
_TRANSFORM = from_origin(_ORIGIN_X, _ORIGIN_Y, _PIXEL_SIZE, _PIXEL_SIZE)
_POLICY = QualityGatePolicy(
    version="v1", min_cv_skill=0.0, expected_scale_sign=1, cv_blocks_per_side=4
)
_SIGMA, _ITERS = 2.5, 5


# --------------------------------------------------------------------------
# Pure helpers
# --------------------------------------------------------------------------


def _grid(height: int, width: int) -> tuple[np.ndarray, np.ndarray]:
    rows, cols = np.meshgrid(np.arange(height), np.arange(width), indexing="ij")
    return rows.ravel(), cols.ravel()


def _dem_samples(depth, reference, height, width) -> CalibrationSamples:
    rows, cols = _grid(height, width)
    xs, ys = _TRANSFORM * (cols + 0.5, rows + 0.5)
    return CalibrationSamples(
        relative_depth=np.asarray(depth, dtype="float64"),
        reference_elevation=np.asarray(reference, dtype="float64"),
        total_candidates=rows.size,
        valid_count=rows.size,
        source_crs=_UTM_CRS,
        reference_crs=_UTM_CRS,
        reprojected=False,
        sample_rows=rows,
        sample_cols=cols,
        reference_cell_ids=rows * width + cols,
        effective_sample_count=rows.size,
        sample_map_xs=np.asarray(xs, dtype="float64"),
        sample_map_ys=np.asarray(ys, dtype="float64"),
    )


def _run(samples, reference_type, height, width):
    fit = fit_robust_affine(
        samples.relative_depth,
        samples.reference_elevation,
        outlier_sigma=_SIGMA,
        max_iterations=_ITERS,
    )
    cv = cross_validate_samples(
        samples,
        reference_type=reference_type,
        source_height=height,
        source_width=width,
        policy=_POLICY,
        outlier_sigma=_SIGMA,
        max_iterations=_ITERS,
    )
    return fit, cv, compute_calibration_residuals(samples, fit, cv)


def _smooth_dem_case(height=32, width=32, seed=0):
    rows, cols = _grid(height, width)
    depth = 0.1 * rows + 0.05 * cols + 0.3 * np.sin(rows * 0.7)
    noise = np.random.default_rng(seed).normal(0.0, 0.2, rows.size)
    return depth, 2.0 * depth + 100.0 + noise


# --------------------------------------------------------------------------
# Pure unit tests
# --------------------------------------------------------------------------


def test_residual_sign_is_predicted_minus_reference():
    """A reference sample LOWERED by 5 sits below the calibrated surface:
    positive residual. One RAISED by 5: negative residual. Both kinds."""
    depth, reference = _smooth_dem_case()
    reference = 2.0 * depth + 100.0  # exact relationship
    reference[100] -= 5.0
    reference[900] += 5.0
    samples = _dem_samples(depth, reference, 32, 32)
    _, _, residuals = _run(samples, "dem", 32, 32)

    assert residuals.residual_fit[100] > 4.0 and residuals.residual_heldout[100] > 4.0
    assert residuals.residual_fit[900] < -4.0 and residuals.residual_heldout[900] < -4.0
    assert "predicted calibrated elevation - reference elevation" in RESIDUAL_DEFINITION


def test_exact_residual_values_use_the_existing_expressions():
    depth, reference = _smooth_dem_case()
    samples = _dem_samples(depth, reference, 32, 32)
    fit, cv, residuals = _run(samples, "dem", 32, 32)

    np.testing.assert_array_equal(
        residuals.predicted_fit, fit.scale * samples.relative_depth + fit.offset
    )
    np.testing.assert_array_equal(
        residuals.residual_fit, residuals.predicted_fit - samples.reference_elevation
    )
    # Held-out predictions are P1-2's own, verbatim — the very array object.
    assert residuals.predicted_heldout is cv.heldout_predictions
    np.testing.assert_array_equal(
        residuals.residual_heldout, cv.heldout_predictions - samples.reference_elevation
    )
    np.testing.assert_array_equal(residuals.inlier_in_production_fit, fit.inlier_mask)


def test_heldout_predictions_come_from_each_samples_own_fold_model():
    """Every held-out prediction equals that sample's fold a/b (persisted in
    P1-2's cross_validation.folds) applied to its depth — bit for bit — and
    never the production fit."""
    depth, reference = _smooth_dem_case()
    samples = _dem_samples(depth, reference, 32, 32)
    fit, cv, residuals = _run(samples, "dem", 32, 32)
    folds = {fold.fold_id: fold for fold in cv.folds}

    expected = np.array(
        [
            folds[int(f)].scale * samples.relative_depth[i] + folds[int(f)].offset
            for i, f in enumerate(residuals.fold_ids)
        ]
    )
    np.testing.assert_array_equal(residuals.predicted_heldout, expected)
    assert not np.array_equal(residuals.predicted_heldout, residuals.predicted_fit)
    np.testing.assert_array_equal(
        residuals.fold_ids,
        assign_spatial_blocks(
            samples.sample_rows, samples.sample_cols, height=32, width=32, blocks_per_side=4
        ),
    )


def test_heldout_residuals_have_no_leakage_from_their_own_block():
    """Changing the reference inside block k never changes any held-out
    PREDICTION in block k (its fold model never saw block k); only those
    samples' residuals change, through their own reference term. The
    in-sample fit prediction there DOES change — that is exactly why fit
    residuals are not validation."""
    depth, reference = _smooth_dem_case()
    base = _dem_samples(depth, reference, 32, 32)
    _, _, before = _run(base, "dem", 32, 32)

    block = 5
    in_block = before.fold_ids == block
    perturbed_reference = reference.copy()
    perturbed_reference[in_block] += 7.0
    _, _, after = _run(_dem_samples(depth, perturbed_reference, 32, 32), "dem", 32, 32)

    np.testing.assert_array_equal(
        after.predicted_heldout[in_block], before.predicted_heldout[in_block]
    )
    np.testing.assert_allclose(
        after.residual_heldout[in_block], before.residual_heldout[in_block] - 7.0, atol=1e-9
    )
    assert not np.array_equal(after.predicted_fit[in_block], before.predicted_fit[in_block])


def test_aggregate_invariants_reproduce_p1_2_persisted_numbers_exactly():
    depth, reference = _smooth_dem_case()
    reference[37] += 40.0  # one gross outlier
    samples = _dem_samples(depth, reference, 32, 32)
    fit, cv, residuals = _run(samples, "dem", 32, 32)

    heldout = summarize_residuals(residuals.residual_heldout)
    assert heldout["count"] == cv.heldout_sample_count == samples.valid_count
    assert heldout["mae"] == cv.heldout_mae
    assert heldout["rmse"] == cv.heldout_rmse
    assert heldout["bias"] == cv.heldout_bias
    for fold in cv.folds:
        in_fold = residuals.fold_ids == fold.fold_id
        assert int(in_fold.sum()) == fold.heldout_count
        assert float(np.sum(residuals.residual_heldout[in_fold] ** 2)) == fold.heldout_sse

    metrics = compute_validation_metrics(samples.relative_depth, samples.reference_elevation, fit)
    inlier_fit = summarize_residuals(residuals.residual_fit[fit.inlier_mask])
    assert inlier_fit["mae"] == metrics.mae
    assert inlier_fit["rmse"] == metrics.rmse
    assert inlier_fit["bias"] == metrics.bias

    diagnostics = compute_fit_diagnostics(
        samples.relative_depth, samples.reference_elevation, fit, effective_sample_count=None
    )
    all_fit = summarize_residuals(residuals.residual_fit)
    assert all_fit["mae"] == diagnostics.all_sample_mae
    assert all_fit["rmse"] == diagnostics.all_sample_rmse
    assert all_fit["bias"] == diagnostics.all_sample_bias


def test_outlier_is_flagged_but_kept_with_its_heldout_residual():
    depth, reference = _smooth_dem_case()
    reference[37] += 40.0
    samples = _dem_samples(depth, reference, 32, 32)
    _, _, residuals = _run(samples, "dem", 32, 32)
    features = build_residual_feature_collection(samples, residuals, reference_type="dem")[
        "features"
    ]

    assert len(features) == samples.valid_count
    assert residuals.inlier_in_production_fit[37] == np.False_
    assert features[37]["properties"]["inlier_in_production_fit"] is False
    assert features[37]["properties"]["residual_heldout"] < -35.0
    assert sum(not f["properties"]["inlier_in_production_fit"] for f in features) == int(
        (~residuals.inlier_in_production_fit).sum()
    )


def test_cross_validation_as_dict_never_serializes_per_sample_arrays():
    depth, reference = _smooth_dem_case()
    _, cv, _ = _run(_dem_samples(depth, reference, 32, 32), "dem", 32, 32)
    data = cv.as_dict()
    assert "heldout_predictions" not in data and "sample_fold_ids" not in data
    json.dumps(data)  # still plain JSON, exactly as P1-2 persisted it


def test_infeasible_cross_validation_has_no_residuals():
    depth = np.array([1.0, 2.0, 3.0, 4.0])
    reference = np.full(4, 100.0)  # zero variance -> CV infeasible
    samples = _dem_samples(depth, reference, 2, 2)
    fit = fit_robust_affine(depth, reference, outlier_sigma=_SIGMA, max_iterations=_ITERS)
    cv = cross_validate_samples(
        samples,
        reference_type="dem",
        source_height=2,
        source_width=2,
        policy=_POLICY,
        outlier_sigma=_SIGMA,
        max_iterations=_ITERS,
    )
    assert not cv.feasible and cv.heldout_predictions is None
    with pytest.raises(ValueError):
        compute_calibration_residuals(samples, fit, cv)


def _write_dem(path, array, *, nodata=-9999.0):
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=array.shape[1],
        height=array.shape[0],
        count=1,
        dtype="float32",
        crs=_UTM_CRS,
        transform=_TRANSFORM,
        nodata=nodata,
    ) as dst:
        dst.write(array.astype("float32"), 1)


def test_dem_features_sit_at_real_sample_pixel_centres_with_invalid_samples_excluded(tmp_path):
    height = width = 16
    rows, cols = np.meshgrid(np.arange(height), np.arange(width), indexing="ij")
    depth = (0.1 * rows + 0.07 * cols).astype("float32")
    dem = 2.0 * depth + 100.0 + np.random.default_rng(1).normal(0, 0.05, depth.shape)
    dem[:3, :3] = -9999.0  # NoData corner: 9 invalid candidates
    dem_path = tmp_path / "dem.tif"
    _write_dem(dem_path, dem)

    samples = sample_dem_pairs(
        depth, CRS.from_user_input(_UTM_CRS), _TRANSFORM, dem_path, max_samples=10_000
    )
    _, cv, residuals = _run(samples, "dem", height, width)
    collection = build_residual_feature_collection(samples, residuals, reference_type="dem")
    features = collection["features"]

    assert collection["type"] == "FeatureCollection"
    assert samples.total_candidates == 256 and len(features) == samples.valid_count == 247
    lons, lats = warp_transform(
        CRS.from_user_input(_UTM_CRS),
        CRS.from_epsg(4326),
        samples.sample_map_xs.tolist(),
        samples.sample_map_ys.tolist(),
    )
    for i, feature in enumerate(features):
        props = feature["properties"]
        assert (props["row"], props["col"]) != (0, 0)
        assert not (props["row"] < 3 and props["col"] < 3)
        x, y = pixel_center_map_coords(
            props["row"],
            props["col"],
            origin_x=_ORIGIN_X,
            origin_y=_ORIGIN_Y,
            pixel_size=_PIXEL_SIZE,
        )
        assert (props["source_x"], props["source_y"]) == (x, y)
        assert feature["geometry"] == {"type": "Point", "coordinates": [lons[i], lats[i]]}
        assert props["block_id"] == props["fold_id"]
        assert props["reference_cell_id"] == props["row"] * width + props["col"]
        assert props["reference_elevation"] == pytest.approx(dem[props["row"], props["col"]])
        assert (
            props["residual_heldout"] == props["predicted_heldout"] - props["reference_elevation"]
        )
        assert "gcp_index" not in props


def test_gcp_features_keep_point_identity_and_exclude_out_of_image_points():
    height = width = 32
    depth = np.tile(np.linspace(0.2, 1.0, width), (height, 1))
    pixels = [(2, 3), (10, 20), (25, 8), (30, 30)]
    points = []
    for row, col in pixels:
        x, y = pixel_center_map_coords(
            row, col, origin_x=_ORIGIN_X, origin_y=_ORIGIN_Y, pixel_size=_PIXEL_SIZE
        )
        points.append({"x": x, "y": y, "z": 2.0 * depth[row, col] + 100.0 + 0.01 * row})
    # Index 2 falls outside the image: never given an invented correspondence.
    points.insert(2, {"x": _ORIGIN_X - 500.0, "y": _ORIGIN_Y + 500.0, "z": 50.0})

    samples = sample_gcp_pairs(depth, CRS.from_user_input(_UTM_CRS), _TRANSFORM, points, _UTM_CRS)
    _, cv, residuals = _run(samples, "gcp", height, width)
    features = build_residual_feature_collection(samples, residuals, reference_type="gcp")[
        "features"
    ]

    assert cv.method == "leave_one_out"
    assert [f["properties"]["gcp_index"] for f in features] == [0, 1, 3, 4]
    for i, feature in enumerate(features):
        props = feature["properties"]
        point = points[props["gcp_index"]]
        assert (props["source_x"], props["source_y"]) == (point["x"], point["y"])
        assert props["reference_elevation"] == point["z"]
        assert props["fold_id"] == props["sample_index"] == i  # leave-one-out
        assert "block_id" not in props and "reference_cell_id" not in props


def test_residuals_payload_summary_matches_cross_validation_and_is_labelled():
    depth, reference = _smooth_dem_case()
    samples = _dem_samples(depth, reference, 32, 32)
    fit, cv, _ = _run(samples, "dem", 32, 32)
    payload = build_residuals_payload(samples, fit, cv, reference_type="dem", policy=_POLICY)
    summary = payload.summary

    assert summary["default_kind"] == "heldout"
    assert summary["units"] == "same units as the calibration reference"
    assert summary["disclaimer"] == RESIDUALS_DISCLAIMER
    heldout = summary["kinds"]["heldout"]["statistics"]
    assert (heldout["mae"], heldout["rmse"], heldout["bias"]) == (
        cv.heldout_mae,
        cv.heldout_rmse,
        cv.heldout_bias,
    )
    assert "not validation" in summary["kinds"]["fit"]["label"]
    assert len(summary["heldout_blocks"]) == cv.fold_count == 16
    for block, fold in zip(summary["heldout_blocks"], cv.folds, strict=True):
        assert block["heldout_rmse"] == math.sqrt(fold.heldout_sse / fold.heldout_count)
        assert (block["block_row"], block["block_col"]) == divmod(fold.fold_id, 4)
    json.dumps(summary)
    assert len(payload.feature_collection["features"]) == samples.valid_count


def test_pre_p1_5_calibrated_job_has_no_backfilled_residuals():
    job = AnalysisJob(
        id=uuid.uuid4(),
        calibration_status=CalibrationStatus.CALIBRATED,
        calibration_metadata={"scale_a": 2.0},
    )
    context = _calibration_residuals_context(job, {})
    assert context.available is False
    assert "before calibration residual diagnostics existed" in context.unavailable_reason


# --------------------------------------------------------------------------
# Integration: real API, real storage, real calibration pipeline
# --------------------------------------------------------------------------


async def _register_and_login(client, email: str) -> dict:
    await client.post("/api/v1/auth/register", json={"email": email, "password": "supersecret123"})
    resp = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": "supersecret123"}
    )
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


async def _create_project(client, headers) -> str:
    resp = await client.post("/api/v1/projects", json={"name": "Residuals"}, headers=headers)
    return resp.json()["id"]


async def _upload(client, headers, project_id, filename, content, mime, data=None) -> dict:
    resp = await client.post(
        f"/api/v1/projects/{project_id}/datasets",
        files={"file": (filename, content, mime)},
        data=data or {},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _fast_calibrated_run(client, monkeypatch, email, *, reference="dem"):
    """A real calibration pipeline run in-process, with only the depth MODEL
    replaced by the fast deterministic stand-in test_reports.py uses."""
    monkeypatch.setattr(
        analysis_execution, "get_depth_estimator", lambda: _FastFakeDepthEstimator()
    )
    headers = await _register_and_login(client, email)
    project_id = await _create_project(client, headers)
    scene = make_structured_scene_geotiff_bytes(
        width=32,
        height=32,
        crs=_UTM_CRS,
        origin_x=_ORIGIN_X,
        origin_y=_ORIGIN_Y,
        pixel_size=_PIXEL_SIZE,
    )
    source = await _upload(client, headers, project_id, "scene.tif", scene, "image/tiff")
    depth = _fake_depth(32, 32)
    if reference == "dem":
        dem = make_depth_consistent_dem_geotiff_bytes(
            depth,
            scale=2.0,
            noise_std=0.05,
            crs=_UTM_CRS,
            origin_x=_ORIGIN_X,
            origin_y=_ORIGIN_Y,
            pixel_size=_PIXEL_SIZE,
        )
        ref = await _upload(
            client, headers, project_id, "dem.tif", dem, "image/tiff", {"role": "dem_reference"}
        )
        parameters = {"dem_reference_dataset_id": ref["id"]}
    else:
        pixels = [(2, 3), (10, 20), (25, 8), (30, 30), (16, 16)]
        points = []
        for i, (row, col) in enumerate(pixels):
            x, y = pixel_center_map_coords(
                row, col, origin_x=_ORIGIN_X, origin_y=_ORIGIN_Y, pixel_size=_PIXEL_SIZE
            )
            points.append((x, y, 2.0 * float(depth[row, col]) + 100.0 + 0.01 * i))
        ref = await _upload(
            client,
            headers,
            project_id,
            "gcps.csv",
            make_gcp_csv_bytes(points),
            "text/csv",
            {"role": "gcp_reference", "gcp_crs": f"{_UTM_CRS}+5703"},
        )
        parameters = {"gcp_reference_dataset_id": ref["id"]}
    resp = await client.post(
        f"/api/v1/projects/{project_id}/datasets/{source['id']}/analysis",
        json={"parameters": {"version": "v1", **parameters}},
        headers=headers,
    )
    job_id = resp.json()["id"]
    await analysis_execution.execute_analysis_job(uuid.UUID(job_id))
    job = (
        await client.get(f"/api/v1/projects/{project_id}/analysis/{job_id}", headers=headers)
    ).json()
    artifacts = (
        await client.get(
            f"/api/v1/projects/{project_id}/analysis/{job_id}/artifacts", headers=headers
        )
    ).json()
    return headers, project_id, source, job, artifacts


def _residual_artifact(artifacts) -> dict:
    return next(a for a in artifacts if a["artifact_type"] == "calibration_residuals")


def _assert_matches_stored_cross_validation(job: dict, body: dict) -> None:
    """The residuals the API serves are the stored P1-2 held-out predictions:
    each equals its fold's persisted a/b applied to the sample's depth, bit
    for bit, and the aggregates reproduce the persisted CV metrics."""
    cv = job["calibration_metadata"]["cross_validation"]
    folds = {fold["fold_id"]: fold for fold in cv["folds"]}
    features = body["feature_collection"]["features"]
    assert len(features) == cv["heldout_sample_count"]
    residuals = []
    for feature in features:
        p = feature["properties"]
        fold = folds[p["fold_id"]]
        assert p["predicted_heldout"] == fold["scale"] * p["relative_depth"] + fold["offset"]
        assert p["residual_heldout"] == p["predicted_heldout"] - p["reference_elevation"]
        assert p["residual_fit"] == p["predicted_fit"] - p["reference_elevation"]
        residuals.append(p["residual_heldout"])
    stats = summarize_residuals(np.array(residuals))
    assert stats["mae"] == cv["heldout_mae"]
    assert stats["rmse"] == cv["heldout_rmse"]
    assert stats["bias"] == cv["heldout_bias"]


async def test_dem_job_persists_residual_geojson_served_by_the_api(client, monkeypatch):
    headers, project_id, source, job, artifacts = await _fast_calibrated_run(
        client, monkeypatch, "resid-dem@example.com"
    )
    assert job["calibration_status"] == "calibrated"
    artifact = _residual_artifact(artifacts)
    assert artifact["mime_type"] == "application/geo+json"

    metadata = job["calibration_metadata"]
    assert metadata["calibration_residuals_artifact_id"] == artifact["id"]
    # Only the ID was added: no per-sample array ever reaches the job row.
    assert not any(
        isinstance(v, list) and len(v) > 100 for v in metadata.values()
    ), "per-sample data leaked into calibration_metadata"
    assert "heldout_predictions" not in metadata["cross_validation"]

    body_resp = await client.get(
        f"/api/v1/projects/{project_id}/analysis/{job['id']}/artifacts/{artifact['id']}"
        "/calibration-residuals",
        headers=headers,
    )
    assert body_resp.status_code == 200, body_resp.text
    body = body_resp.json()
    assert body["artifact_id"] == artifact["id"]
    assert body["summary"]["reference_type"] == "dem"
    assert (
        body["summary"]["metric_elevation_artifact_id"] == metadata["metric_elevation_artifact_id"]
    )
    assert body["summary"]["reference_dataset_id"] == metadata["reference_dataset_id"]
    _assert_matches_stored_cross_validation(job, body)
    features = body["feature_collection"]["features"]
    assert {f["properties"]["block_id"] for f in features} == set(range(16))

    # The stored file is exactly what the API served, and downloads as GeoJSON.
    download = await client.get(
        f"/api/v1/projects/{project_id}/analysis/{job['id']}/artifacts/{artifact['id']}/download",
        headers=headers,
    )
    assert download.status_code == 200
    assert download.headers["content-type"].startswith("application/geo+json")
    assert 'filename="calibration_residuals.geojson"' in download.headers["content-disposition"]
    stored = json.loads(download.content)
    assert len(stored["features"]) == len(features)
    for stored_feature, served in zip(stored["features"], features, strict=True):
        assert stored_feature["geometry"] == served["geometry"]
        # The typed response lists optional keys the file omits (e.g. a DEM
        # sample's gcp_index) as null; every stored value is served unchanged.
        assert {
            key: served["properties"][key] for key in stored_feature["properties"]
        } == stored_feature["properties"]
        assert all(
            value is None
            for key, value in served["properties"].items()
            if key not in stored_feature["properties"]
        )

    context = (
        await client.get(
            f"/api/v1/projects/{project_id}/datasets/{source['id']}/visualization/context",
            headers=headers,
        )
    ).json()
    residual_context = context["calibration_residuals"]
    assert residual_context["available"] is True
    assert residual_context["artifact_id"] == artifact["id"]
    assert residual_context["sample_count"] == len(features)
    assert residual_context["reference_type"] == "dem"
    assert all(layer["layer_type"] != "calibration_residuals" for layer in context["layers"])


async def test_gcp_job_residuals_are_per_point_leave_one_out(client, monkeypatch):
    headers, project_id, _, job, artifacts = await _fast_calibrated_run(
        client, monkeypatch, "resid-gcp@example.com", reference="gcp"
    )
    assert job["calibration_status"] == "calibrated", job["calibration_metadata"]
    artifact = _residual_artifact(artifacts)
    body = (
        await client.get(
            f"/api/v1/projects/{project_id}/analysis/{job['id']}/artifacts/{artifact['id']}"
            "/calibration-residuals",
            headers=headers,
        )
    ).json()
    features = body["feature_collection"]["features"]
    assert [f["properties"]["gcp_index"] for f in features] == [0, 1, 2, 3, 4]
    assert [f["properties"]["fold_id"] for f in features] == [0, 1, 2, 3, 4]
    assert body["summary"]["cv_method"] == "leave_one_out"
    assert body["summary"]["heldout_blocks"] == []
    _assert_matches_stored_cross_validation(job, body)


async def test_residual_artifact_is_rejected_by_raster_endpoints_and_wrong_types(
    client, monkeypatch
):
    headers, project_id, _, job, artifacts = await _fast_calibrated_run(
        client, monkeypatch, "resid-raster@example.com"
    )
    base = f"/api/v1/projects/{project_id}/analysis/{job['id']}/artifacts"
    residual_id = _residual_artifact(artifacts)["id"]
    for path in (
        f"{base}/{residual_id}/visualization/preview",
        f"{base}/{residual_id}/visualization/metadata",
        f"{base}/{residual_id}/visualization/value?row=1&col=1",
        f"{base}/{residual_id}/measurements/point?row=1&col=1",
    ):
        resp = await client.get(path, headers=headers)
        assert resp.status_code == 422, (path, resp.status_code, resp.text)

    dsm_id = next(a["id"] for a in artifacts if a["artifact_type"] == "dsm")
    resp = await client.get(f"{base}/{dsm_id}/calibration-residuals", headers=headers)
    assert resp.status_code == 422
    assert "calibration_residuals" in resp.json()["error"]["message"]


async def test_residuals_ownership_is_enforced(client, monkeypatch):
    _, project_id, _, job, artifacts = await _fast_calibrated_run(
        client, monkeypatch, "resid-owner@example.com"
    )
    intruder = await _register_and_login(client, "resid-intruder@example.com")
    resp = await client.get(
        f"/api/v1/projects/{project_id}/analysis/{job['id']}/artifacts/"
        f"{_residual_artifact(artifacts)['id']}/calibration-residuals",
        headers=intruder,
    )
    assert resp.status_code == 404


async def test_uncalibrated_and_gate_rejected_jobs_have_no_residuals(client):
    """Real worker, real model. No reference -> uncalibrated; an inverted
    DEM -> rejected by the quality gate. Neither writes a residual artifact
    and the context says why."""
    headers = await _register_and_login(client, "resid-none@example.com")
    project_id = await _create_project(client, headers)
    source = await _upload_source_image(client, headers, project_id)
    resp = await client.post(
        f"/api/v1/projects/{project_id}/datasets/{source['id']}/analysis",
        json={"parameters": {"version": "v1"}},
        headers=headers,
    )
    job_id = resp.json()["id"]
    await analysis_execution.execute_analysis_job(uuid.UUID(job_id))
    context = (
        await client.get(
            f"/api/v1/projects/{project_id}/datasets/{source['id']}/visualization/context",
            headers=headers,
        )
    ).json()
    assert context["calibration_residuals"]["available"] is False
    assert "No calibration reference" in context["calibration_residuals"]["unavailable_reason"]

    from tests.test_calibration import _depth_consistent_dem_bytes

    headers, project_id, source, final, artifacts = await _run_dem_job(
        client, "resid-rejected@example.com", _depth_consistent_dem_bytes(scale=-2.0)
    )
    assert final["calibration_status"] == "failed"
    assert final["calibration_metadata"]["quality_gate"]["passed"] is False
    assert {a["artifact_type"] for a in artifacts} == {"relative_depth"}
    assert "calibration_residuals_artifact_id" not in final["calibration_metadata"]
    context = (
        await client.get(
            f"/api/v1/projects/{project_id}/datasets/{source['id']}/visualization/context",
            headers=headers,
        )
    ).json()
    assert context["calibration_residuals"]["available"] is False
    assert "quality gate" in context["calibration_residuals"]["unavailable_reason"]


async def test_report_json_pdf_csv_and_zip_include_residuals(client, monkeypatch):
    headers, project_id, source, job, artifacts = await _fast_calibrated_run(
        client, monkeypatch, "resid-report@example.com"
    )
    # Inserted directly (never enqueued) so this test generates the report
    # exactly once — POST /reports would also queue it for the suite's own
    # worker, racing this in-process generate_report() on the same files.
    user_id = (await client.get("/api/v1/auth/me", headers=headers)).json()["id"]
    report_id = str(await _create_report_row(project_id, source["id"], user_id))
    await generate_report(uuid.UUID(report_id))
    base = f"/api/v1/projects/{project_id}/reports/{report_id}"
    status = (await client.get(base, headers=headers)).json()
    assert status["status"] == "completed", status
    assert status["csv_available"] is True

    data = (await client.get(f"{base}/json", headers=headers)).json()
    residuals = data["depth_analysis"]["calibration"]["residuals"]
    artifact = _residual_artifact(artifacts)
    assert residuals["available"] is True
    assert residuals["artifact_id"] == artifact["id"]
    assert residuals["residual_definition"] == RESIDUAL_DEFINITION
    assert len(residuals["heldout_blocks"]) == 16
    assert "features" not in json.dumps(residuals)  # no per-sample DEM data inlined
    assert RESIDUALS_DISCLAIMER in data["limitations"]
    inventory = next(a for a in data["artifacts"] if a["artifact_type"] == "calibration_residuals")
    assert inventory["sample_count"] == residuals["sample_count"]

    pdf = (await client.get(f"{base}/pdf", headers=headers)).content
    text = " ".join(
        "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(pdf)).pages).split()
    )
    assert "Calibration residuals" in text
    assert "Held-out residual (spatial-block cross-validation)" in text
    assert "not validation" in text

    csv_text = (await client.get(f"{base}/csv", headers=headers)).content.decode("utf-8")
    rows = list(csv.reader(io.StringIO(csv_text)))
    title_index = next(
        i for i, row in enumerate(rows) if row and row[0].startswith("Calibration Residual")
    )
    assert "not measurements" in rows[title_index][0]
    header = rows[title_index + 1]
    body_rows = []
    for row in rows[title_index + 2 :]:
        if not row:
            break
        body_rows.append(dict(zip(header, row, strict=True)))
    assert len(body_rows) == residuals["sample_count"]
    first = body_rows[0]
    assert float(first["residual_heldout"]) == float(first["predicted_heldout"]) - float(
        first["reference_elevation"]
    )

    bundle = (await client.get(f"{base}/bundle", headers=headers)).content
    with zipfile.ZipFile(io.BytesIO(bundle)) as zf:
        names = set(zf.namelist())
        assert "measurements.csv" in names  # filename kept for compatibility
        assert "artifacts/calibration_residuals.geojson" in names
        assert "artifacts/calibration_residuals.tif" not in names
        assert "artifacts/dsm.tif" in names
        geojson = json.loads(zf.read("artifacts/calibration_residuals.geojson"))
        assert len(geojson["features"]) == residuals["sample_count"]


async def test_calibrated_job_row_carries_residual_id_after_real_worker_run(client):
    """The real separate worker container (real Depth Anything) writes the
    artifact too — not only the in-process path."""
    from tests.test_calibration import _depth_consistent_dem_bytes

    headers, project_id, _, final, artifacts = await _run_dem_job(
        client, "resid-worker@example.com", _depth_consistent_dem_bytes()
    )
    assert final["calibration_status"] == "calibrated"
    artifact = _residual_artifact(artifacts)
    body = (
        await client.get(
            f"/api/v1/projects/{project_id}/analysis/{final['id']}/artifacts/{artifact['id']}"
            "/calibration-residuals",
            headers=headers,
        )
    ).json()
    _assert_matches_stored_cross_validation(final, body)

    async with AsyncSessionLocal() as db:
        job = await db.get(AnalysisJob, uuid.UUID(final["id"]))
        assert job.calibration_metadata["calibration_residuals_artifact_id"] == artifact["id"]
