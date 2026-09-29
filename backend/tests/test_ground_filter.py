"""P1-3: raster bare-earth approximation (DTM) + nDSM tests.

Pure unit tests of `geospatial/ground_filter.py` against synthetic rasters
whose ground/surface values make the expected DTM/nDSM obvious, plus
integration tests through the real API/database/storage:

- in-process jobs (a directly inserted, never-enqueued job row executed by
  `execute_analysis_job`, with a deterministic fake depth model — the
  race-free technique tests/test_semantic_segmentation.py uses), and
- one real end-to-end job through the separate worker with the real Depth
  Anything model.

All calibration references here are DELIBERATELY constructed from the depth
itself (DEM = scale * D + offset): they validate pipeline behavior and are
NOT accuracy evidence. DTM/nDSM are raster-derived estimates, never measured
bare earth or measured heights.
"""

import asyncio
import io
import uuid
import zipfile

import numpy as np
import pytest
import rasterio
from affine import Affine
from pypdf import PdfReader
from rasterio.crs import CRS

from app.core.config import get_settings
from app.db.session import AsyncSessionLocal
from app.models.analysis_job import AnalysisJob, AnalysisJobStatus
from app.services import analysis_execution, ground_filter_pipeline
from app.services.ground_filter_pipeline import ground_filter_policy
from app.services.report_execution import generate_report
from geospatial.ground_filter import (
    GROUND_FILTER_NODATA,
    CellSize,
    GroundFilterError,
    GroundFilterParameters,
    grey_opening,
    metric_cell_size,
    progressive_morphological_filter,
    running_max,
    running_min,
    window_schedule,
)
from geospatial.vertical_units import FOOT, KNOWN, METRE, VerticalUnitResolution
from tests.fixtures import (
    make_depth_consistent_dem_geotiff_bytes,
    make_structured_scene_geotiff_bytes,
    real_structured_scene_depth,
)
from tests.test_reports import _create_report_row

_UTM = CRS.from_epsg(32633)
_PARAMS = GroundFilterParameters(
    max_window_m=20.0, slope=0.15, initial_threshold=0.5, max_threshold=3.0
)
_ORIGIN_X, _ORIGIN_Y, _PIXEL_SIZE, _UTM_CRS = 500000.0, 4649984.0, 2.0, "EPSG:32633"
_FT_PER_M = 3.280839895013123


def _transform(cell: float = 1.0) -> Affine:
    return Affine(cell, 0.0, 500000.0, 0.0, -cell, 4650000.0)


def _plane(height=60, width=60, gx=0.05, gy=0.02, base=100.0) -> np.ndarray:
    rows, cols = np.mgrid[0:height, 0:width]
    return (base + gx * cols + gy * rows).astype("float32")


def _filter(dsm, cell=1.0, params=_PARAMS, nodata=None):
    return progressive_morphological_filter(
        dsm,
        crs=_UTM,
        transform=_transform(cell),
        parameters=params,
        vertical_unit=METRE,
        nodata=nodata,
    )


_METRE_RESOLUTION = VerticalUnitResolution(KNOWN, METRE, "test", "metre", None)


# --------------------------------------------------------------------------
# Unit tests — known synthetic surfaces
# --------------------------------------------------------------------------


def test_flat_plane_is_all_ground_with_zero_ndsm():
    dsm = np.full((40, 40), 100.0, dtype="float32")
    result = _filter(dsm)
    np.testing.assert_array_equal(result.dtm, dsm)
    np.testing.assert_array_equal(result.ndsm, np.zeros_like(dsm))
    assert result.ground_mask.all()
    assert result.statistics.ground_fraction == 1.0


def test_tilted_plane_below_slope_parameter_is_all_ground():
    """A plane whose slope (0.1) is below the slope parameter (0.15) is
    terrain everywhere, including the raster edges where the opening's
    truncated windows lower the opened surface slightly."""
    dsm = _plane(gx=0.1, gy=0.05)
    result = _filter(dsm)
    assert result.ground_mask.all()
    np.testing.assert_array_equal(result.dtm, dsm)
    assert float(result.ndsm.max()) == 0.0


def test_narrow_building_on_plane_is_removed_from_dtm():
    """An 8 m x 8 m, 10-unit building on a gently sloping plane (narrower
    than the 20 m window): nDSM is ~10 on the footprint and exactly 0
    elsewhere; the DTM under the footprint is close to the plane."""
    plane = _plane()
    dsm = plane.copy()
    dsm[20:28, 30:38] += 10.0
    result = _filter(dsm)

    footprint = np.zeros(dsm.shape, dtype=bool)
    footprint[20:28, 30:38] = True
    np.testing.assert_allclose(result.ndsm[footprint], 10.0, atol=0.5)
    assert float(np.abs(result.ndsm[~footprint]).max()) == 0.0
    np.testing.assert_allclose(result.dtm[footprint], plane[footprint], atol=0.5)
    assert not result.ground_mask[footprint].any()
    assert result.statistics.ground_count == dsm.size - footprint.sum()


def test_metres_and_feet_produce_equivalent_ground_filter_outputs():
    plane_m = _plane()
    dsm_m = plane_m.copy()
    dsm_m[20:28, 30:38] += 10.0
    result_m = _filter(dsm_m)
    result_ft = progressive_morphological_filter(
        dsm_m * _FT_PER_M,
        crs=_UTM,
        transform=_transform(),
        parameters=_PARAMS,
        vertical_unit=FOOT,
    )

    np.testing.assert_array_equal(result_ft.ground_mask, result_m.ground_mask)
    np.testing.assert_allclose(result_ft.dtm / _FT_PER_M, result_m.dtm, atol=2e-5)
    np.testing.assert_allclose(result_ft.ndsm / _FT_PER_M, result_m.ndsm, atol=2e-5)
    assert result_ft.metadata()["source_vertical_unit"] == "ft"
    assert result_ft.metadata()["vertical_conversion_factor"] == pytest.approx(0.3048)


def test_ground_filter_rejects_unknown_vertical_unit():
    with pytest.raises(GroundFilterError, match="vertical unit could not be established"):
        progressive_morphological_filter(
            _plane(),
            crs=_UTM,
            transform=_transform(),
            parameters=_PARAMS,
            vertical_unit=None,
        )


def test_building_wider_than_max_window_stays_ground():
    """Documented failure mode: an object wider than max_window_m cannot be
    opened away, so its interior remains 'ground' (nDSM 0)."""
    dsm = _plane(height=80, width=80)
    dsm[10:70, 10:70] += 10.0  # 60 m wide at 1 m cells, window is 20 m
    result = _filter(dsm)
    assert result.ndsm[40, 40] == 0.0
    assert result.ground_mask[40, 40]


def test_steep_narrow_ridge_is_partly_cut_down():
    """Documented failure mode: terrain steeper than the slope parameter
    (here a 1.0 dz/dx ridge) is indistinguishable from an object and is
    partly removed, giving a positive nDSM on genuine terrain."""
    rows, cols = np.mgrid[0:60, 0:60]
    ridge = np.clip(8.0 - np.abs(cols - 30).astype("float32"), 0.0, None)
    dsm = (100.0 + ridge + 0.0 * rows).astype("float32")
    result = _filter(dsm)
    assert result.ndsm[30, 30] > 1.0
    assert not result.ground_mask[30, 30]


def test_nodata_hole_propagates_without_sentinel_leakage():
    """A -9999 NoData hole next to a building: DTM/nDSM are NoData exactly
    on the hole, and no neighbouring value is dragged towards -9999 (the
    sentinel is never fed into a minimum)."""
    plane = _plane()
    dsm = plane.copy()
    dsm[20:28, 30:38] += 10.0
    dsm[20:28, 22:28] = GROUND_FILTER_NODATA
    result = _filter(dsm, nodata=GROUND_FILTER_NODATA)

    hole = dsm == GROUND_FILTER_NODATA
    assert (result.dtm[hole] == GROUND_FILTER_NODATA).all()
    assert (result.ndsm[hole] == GROUND_FILTER_NODATA).all()
    assert not result.valid_mask[hole].any()
    assert float(result.dtm[~hole].min()) > 99.0
    np.testing.assert_allclose(result.ndsm[22:26, 32:36], 10.0, atol=0.5)
    assert result.statistics.valid_count == int((~hole).sum())

    nan_dsm = dsm.copy()
    nan_dsm[hole] = np.nan
    nan_result = _filter(nan_dsm)
    np.testing.assert_array_equal(nan_result.dtm, result.dtm)


def test_all_nodata_raster_fails():
    with pytest.raises(GroundFilterError, match="no valid cells"):
        _filter(np.full((20, 20), np.nan, dtype="float32"))


@pytest.mark.parametrize("seed", range(6))
def test_randomized_invariants(seed):
    """DTM <= DSM, nDSM >= 0 (never clipped), nDSM == DSM - DTM exactly,
    nDSM == 0 exactly on ground, and every DTM value is a value the DSM
    itself contains."""
    rng = np.random.default_rng(seed)
    dsm = (_plane(50, 70) + rng.normal(0.0, 0.8, (50, 70))).astype("float32")
    for _ in range(4):
        r, c = rng.integers(0, 44), rng.integers(0, 64)
        dsm[r : r + 6, c : c + 6] += rng.uniform(1.0, 15.0)
    dsm[rng.random(dsm.shape) < 0.03] = np.nan
    result = _filter(dsm)

    valid = result.valid_mask
    assert (result.dtm[valid] <= dsm[valid]).all()
    assert (result.ndsm[valid] >= 0.0).all()
    np.testing.assert_array_equal(result.ndsm[valid], dsm[valid] - result.dtm[valid])
    assert (result.ndsm[result.ground_mask] == 0.0).all()
    assert np.isin(result.dtm[valid], dsm[valid]).all()


@pytest.mark.parametrize(
    "shape,window",
    [((37, 41), (5, 7)), ((10, 12), (3, 3)), ((1, 25), (3, 9)), ((9, 9), (21, 21))],
)
def test_running_min_max_match_brute_force(shape, window):
    rng = np.random.default_rng(sum(shape) + sum(window))
    array = rng.normal(size=shape).astype("float32")
    array[rng.random(shape) < 0.15] = np.nan
    wr, wc = window
    expected_min = np.full(shape, np.nan, dtype="float32")
    expected_max = np.full(shape, np.nan, dtype="float32")
    for i in range(shape[0]):
        for j in range(shape[1]):
            block = array[
                max(0, i - wr // 2) : i + wr // 2 + 1, max(0, j - wc // 2) : j + wc // 2 + 1
            ]
            block = block[np.isfinite(block)]
            if block.size:
                expected_min[i, j], expected_max[i, j] = block.min(), block.max()
    np.testing.assert_array_equal(running_min(array, wr, wc), expected_min)
    np.testing.assert_array_equal(running_max(array, wr, wc), expected_max)


def test_grey_opening_never_exceeds_input_and_rejects_even_windows():
    rng = np.random.default_rng(4)
    surface = rng.normal(size=(30, 30)).astype("float32")
    surface[3, 3] = np.nan
    opened = grey_opening(surface, 5, 5)
    valid = np.isfinite(surface)
    assert (opened[valid] <= surface[valid]).all()
    assert np.isnan(opened[3, 3])
    with pytest.raises(ValueError):
        running_min(surface, 4, 5)


def test_metric_cell_size_projected_metre_crs():
    size = metric_cell_size(_UTM, _transform(2.0), 10, 10)
    assert (size.x_m, size.y_m) == (2.0, 2.0)
    assert size.method == "projected_linear_units" and size.crs_linear_unit == "metre"


def test_metric_cell_size_projected_foot_crs_converts_to_metres():
    crs = CRS.from_epsg(2263)  # NAD83 / New York Long Island (ftUS)
    size = metric_cell_size(crs, Affine(10.0, 0, 1_000_000.0, 0, -10.0, 200_000.0), 10, 10)
    assert size.x_m == pytest.approx(10.0 * 1200.0 / 3937.0)
    assert size.y_m == pytest.approx(10.0 * 1200.0 / 3937.0)


def test_metric_cell_size_geographic_crs_measured_in_local_utm():
    """0.0001 deg at latitude ~13 N: ~10.85 m east-west (scaled by
    cos(lat)) and ~11.06 m north-south — degrees are never used as metres."""
    size = metric_cell_size(
        CRS.from_epsg(4326), Affine(0.0001, 0, 77.5, 0, -0.0001, 13.0), 100, 100
    )
    assert size.method == "geographic_local_utm"
    assert size.x_m == pytest.approx(10.85, abs=0.05)
    assert size.y_m == pytest.approx(11.06, abs=0.05)

    dsm = np.full((50, 50), 100.0, dtype="float32")
    # ~11 m cells: a 20 m window is under 3 cells — refused, never faked.
    with pytest.raises(GroundFilterError, match="3-cell window"):
        progressive_morphological_filter(
            dsm,
            crs=CRS.from_epsg(4326),
            transform=Affine(0.0001, 0, 77.5, 0, -0.0001, 13.0),
            parameters=_PARAMS,
            vertical_unit=METRE,
        )
    # ~1.1 m cells: the 20 m window is 17 cells per axis (half-width 8.86 m);
    # reading 0.00001 deg as 0.00001 m would instead give a window of
    # hundreds of thousands of cells.
    result = progressive_morphological_filter(
        dsm,
        crs=CRS.from_epsg(4326),
        transform=Affine(0.00001, 0, 77.5, 0, -0.00001, 13.0),
        parameters=_PARAMS,
        vertical_unit=METRE,
    )
    assert (result.levels[-1].window_rows, result.levels[-1].window_cols) == (17, 17)
    assert result.ground_mask.all()


def test_metric_cell_size_rejects_non_georeferenced_and_rotated():
    with pytest.raises(GroundFilterError, match="georeferenced"):
        metric_cell_size(None, None, 10, 10)
    with pytest.raises(GroundFilterError, match="Rotated"):
        metric_cell_size(_UTM, Affine(1.0, 0.2, 0.0, 0.1, -1.0, 0.0), 10, 10)


def test_window_schedule_and_thresholds_follow_the_formula():
    """1 m cells, 20 m max window: half-widths 1, 2, 4, 8 then the clamped 9
    (windows 3, 5, 9, 17, 19 cells); dh_1 = dh0, then
    dh_k = min(s * (size_k - size_{k-1}) + dh0, dh_max)."""
    levels = window_schedule(
        CellSize(1.0, 1.0, "projected_linear_units", "metre"), _PARAMS, 200, 200
    )
    assert [lvl.window_rows for lvl in levels] == [3, 5, 9, 17, 19]
    assert [lvl.size_m for lvl in levels] == [3.0, 5.0, 9.0, 17.0, 19.0]
    expected = [0.5]
    for prev, cur in zip([3.0, 5.0, 9.0, 17.0], [5.0, 9.0, 17.0, 19.0], strict=True):
        expected.append(min(0.15 * (cur - prev) + 0.5, 3.0))
    assert [lvl.threshold for lvl in levels] == pytest.approx(expected)
    assert not any(lvl.clamped_to_raster for lvl in levels)


def test_window_schedule_anisotropic_cells_and_raster_clamp():
    levels = window_schedule(
        CellSize(1.0, 2.0, "projected_linear_units", "metre"), _PARAMS, 200, 200
    )
    last = levels[-1]
    assert last.window_cols == 2 * last.window_rows - 1  # same metric size per axis

    small = window_schedule(CellSize(1.0, 1.0, "projected_linear_units", "metre"), _PARAMS, 7, 7)
    assert small[-1].window_rows == 7 and small[-1].clamped_to_raster

    with pytest.raises(GroundFilterError, match="3-cell window"):
        window_schedule(
            CellSize(10.0, 10.0, "projected_linear_units", "metre"),
            GroundFilterParameters(20.0, 0.15, 0.5, 3.0),
            50,
            50,
        )


def test_invalid_parameters_are_rejected():
    for bad in (
        GroundFilterParameters(0.0, 0.15, 0.5, 3.0),
        GroundFilterParameters(20.0, -0.1, 0.5, 3.0),
        GroundFilterParameters(20.0, 0.15, 0.5, 0.2),
        GroundFilterParameters(float("nan"), 0.15, 0.5, 3.0),
    ):
        with pytest.raises(GroundFilterError):
            _filter(np.full((20, 20), 100.0, dtype="float32"), params=bad)


def test_policy_and_parameters_are_persisted_verbatim():
    settings = get_settings()
    policy = ground_filter_policy(settings)
    assert policy["version"] == settings.GROUND_FILTER_POLICY_VERSION == "v1"
    assert policy["parameters"] == {
        "max_window_m": 20.0,
        "slope": 0.15,
        "initial_threshold": 0.5,
        "max_threshold": 3.0,
    }
    assert "not tuned or validated" in policy["statement"]

    outcome = ground_filter_pipeline.run_ground_filter(
        _plane(),
        crs=_UTM,
        transform=_transform(),
        settings=settings,
        vertical_unit=_METRE_RESOLUTION,
    )
    assert outcome.metadata["policy"] == policy
    assert outcome.metadata["parameters"] == policy["parameters"]
    assert [lvl["window_rows"] for lvl in outcome.metadata["window_levels"]] == [3, 5, 9, 17, 19]


def test_pipeline_soft_fails_with_reason_and_policy():
    settings = get_settings()
    outcome = ground_filter_pipeline.run_ground_filter(
        np.full((10, 10), np.nan, dtype="float32"),
        crs=_UTM,
        transform=_transform(),
        settings=settings,
        vertical_unit=_METRE_RESOLUTION,
    )
    assert outcome.status.value == "failed" and outcome.result is None
    assert "no valid cells" in outcome.metadata["error"]
    assert outcome.metadata["policy"] == ground_filter_policy(settings)


# --------------------------------------------------------------------------
# Integration — in-process jobs with a deterministic fake depth model
# --------------------------------------------------------------------------

_BUILDING = (slice(20, 24), slice(30, 34))  # 4x4 cells = 8 m x 8 m at 2 m/pixel


def _fake_depth(height: int, width: int) -> np.ndarray:
    """Gentle plane plus one raised block: with DEM = 2 * D + 100 the DSM is
    a 0.05/m slope carrying a 6-unit, 8 m x 8 m 'building'."""
    rows, cols = np.mgrid[0:height, 0:width]
    depth = (0.05 * cols + 0.02 * rows).astype("float32")
    depth[_BUILDING] += 3.0
    return depth


class _FakeDepthEstimator:
    def load(self):
        pass

    def predict(self, rgb_image):
        from ai.depth_estimator import DepthPrediction

        height, width = rgb_image.shape[0], rgb_image.shape[1]
        return DepthPrediction(
            depth=_fake_depth(height, width),
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


async def _register_and_login(client, email: str) -> dict:
    await client.post("/api/v1/auth/register", json={"email": email, "password": "supersecret123"})
    resp = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": "supersecret123"}
    )
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


async def _setup(client, email: str, *, dem_scale: float | None = 2.0):
    headers = await _register_and_login(client, email)
    project_id = (
        await client.post("/api/v1/projects", json={"name": "GF"}, headers=headers)
    ).json()["id"]
    scene = make_structured_scene_geotiff_bytes(
        width=64,
        height=64,
        crs=_UTM_CRS,
        origin_x=_ORIGIN_X,
        origin_y=_ORIGIN_Y,
        pixel_size=_PIXEL_SIZE,
    )
    source = (
        await client.post(
            f"/api/v1/projects/{project_id}/datasets",
            files={"file": ("scene.tif", scene, "image/tiff")},
            headers=headers,
        )
    ).json()
    parameters = {"version": "v1"}
    if dem_scale is not None:
        dem = make_depth_consistent_dem_geotiff_bytes(
            _fake_depth(64, 64),
            scale=dem_scale,
            noise_std=0.0,
            crs=_UTM_CRS,
            origin_x=_ORIGIN_X,
            origin_y=_ORIGIN_Y,
            pixel_size=_PIXEL_SIZE,
        )
        dem_id = (
            await client.post(
                f"/api/v1/projects/{project_id}/datasets",
                files={"file": ("dem.tif", dem, "image/tiff")},
                data={"role": "dem_reference"},
                headers=headers,
            )
        ).json()["id"]
        parameters["dem_reference_dataset_id"] = dem_id
    user_id = (await client.get("/api/v1/auth/me", headers=headers)).json()["id"]
    return headers, project_id, source["id"], user_id, parameters


async def _run_in_process(client, monkeypatch, email, *, dem_scale=2.0) -> dict:
    """Inserts a queued job row directly (never enqueued, so the suite's
    real worker can't race it) and executes it in-process with the fake
    depth model; returns everything the assertions need."""
    monkeypatch.setattr(analysis_execution, "get_depth_estimator", lambda: _FakeDepthEstimator())
    headers, project_id, source_id, user_id, parameters = await _setup(
        client, email, dem_scale=dem_scale
    )
    async with AsyncSessionLocal() as db:
        job = AnalysisJob(
            project_id=uuid.UUID(project_id),
            dataset_id=uuid.UUID(source_id),
            user_id=uuid.UUID(user_id),
            status=AnalysisJobStatus.QUEUED,
            parameters=parameters,
        )
        db.add(job)
        await db.commit()
        job_id = str(job.id)
    await analysis_execution.execute_analysis_job(uuid.UUID(job_id))

    body = (
        await client.get(f"/api/v1/projects/{project_id}/analysis/{job_id}", headers=headers)
    ).json()
    artifacts = (
        await client.get(
            f"/api/v1/projects/{project_id}/analysis/{job_id}/artifacts", headers=headers
        )
    ).json()
    return {
        "headers": headers,
        "project_id": project_id,
        "source_id": source_id,
        "job_id": job_id,
        "job": body,
        "artifacts": {a["artifact_type"]: a for a in artifacts},
    }


async def _download(client, run, artifact_type, path):
    artifact = run["artifacts"][artifact_type]
    resp = await client.get(
        f"/api/v1/projects/{run['project_id']}/analysis/{run['job_id']}/artifacts/"
        f"{artifact['id']}/download",
        headers=run["headers"],
    )
    assert resp.status_code == 200
    path.write_bytes(resp.content)
    return rasterio.open(path)


async def test_calibrated_job_writes_dtm_and_ndsm_on_the_dsm_grid(client, monkeypatch, tmp_path):
    run = await _run_in_process(client, monkeypatch, "gf-calibrated@example.com")
    job = run["job"]
    assert job["status"] == "completed"
    assert job["calibration_status"] == "calibrated"
    assert job["calibration_metadata"]["quality_gate"]["passed"] is True
    assert job["ground_filter_status"] == "completed"
    assert set(run["artifacts"]) == {
        "relative_depth",
        "metric_elevation",
        "dsm",
        "dtm",
        "ndsm",
        "calibration_residuals",
    }

    meta = job["ground_filter_metadata"]
    assert meta["calibration_quality_gate_passed"] is True
    assert meta["dsm_artifact_id"] == run["artifacts"]["dsm"]["id"]
    assert meta["artifact_ids"] == {
        "dtm": run["artifacts"]["dtm"]["id"],
        "ndsm": run["artifacts"]["ndsm"]["id"],
    }
    assert meta["policy"] == ground_filter_policy(get_settings())

    with (
        await _download(client, run, "dsm", tmp_path / "dsm.tif") as dsm_ds,
        await _download(client, run, "metric_elevation", tmp_path / "me.tif") as me_ds,
        await _download(client, run, "dtm", tmp_path / "dtm.tif") as dtm_ds,
        await _download(client, run, "ndsm", tmp_path / "ndsm.tif") as ndsm_ds,
    ):
        dsm, dtm, ndsm = dsm_ds.read(1), dtm_ds.read(1), ndsm_ds.read(1)
        np.testing.assert_array_equal(dsm, me_ds.read(1))  # DSM semantics unchanged
        for ds in (dtm_ds, ndsm_ds):
            assert ds.dtypes[0] == "float32" and ds.nodata == GROUND_FILTER_NODATA
            assert ds.crs == dsm_ds.crs and ds.transform == dsm_ds.transform
            assert ds.shape == dsm_ds.shape
    assert (dtm <= dsm).all()
    np.testing.assert_array_equal(ndsm, dsm - dtm)
    np.testing.assert_allclose(ndsm[_BUILDING], 6.0, atol=0.3)
    outside = np.ones(dsm.shape, dtype=bool)
    outside[_BUILDING] = False
    assert float(ndsm[outside].max()) == 0.0
    assert run["artifacts"]["ndsm"]["artifact_metadata"]["dtm_artifact_id"] == (
        run["artifacts"]["dtm"]["id"]
    )


async def test_gate_failed_calibration_never_runs_ground_filter(client, monkeypatch):
    run = await _run_in_process(client, monkeypatch, "gf-gatefail@example.com", dem_scale=-2.0)
    job = run["job"]
    assert job["status"] == "completed"
    assert job["calibration_status"] == "failed"
    assert job["calibration_metadata"]["quality_gate"]["passed"] is False
    assert job["ground_filter_status"] == "not_requested"
    assert job["ground_filter_metadata"] is None
    assert set(run["artifacts"]) == {"relative_depth"}


async def test_uncalibrated_job_never_runs_ground_filter(client, monkeypatch):
    run = await _run_in_process(client, monkeypatch, "gf-uncal@example.com", dem_scale=None)
    assert run["job"]["calibration_status"] == "uncalibrated"
    assert run["job"]["ground_filter_status"] == "not_requested"
    assert set(run["artifacts"]) == {"relative_depth"}


async def test_forced_filter_failure_is_soft(client, monkeypatch):
    def _boom(*args, **kwargs):
        raise GroundFilterError("forced test failure")

    monkeypatch.setattr(ground_filter_pipeline, "progressive_morphological_filter", _boom)
    run = await _run_in_process(client, monkeypatch, "gf-forced@example.com")
    job = run["job"]
    assert job["status"] == "completed"
    assert job["calibration_status"] == "calibrated"
    assert job["ground_filter_status"] == "failed"
    assert job["ground_filter_metadata"]["error"] == "forced test failure"
    assert job["ground_filter_metadata"]["policy"] == ground_filter_policy(get_settings())
    # P1-5 residuals come from the gate-passed calibration, not the filter.
    assert set(run["artifacts"]) == {
        "relative_depth",
        "metric_elevation",
        "dsm",
        "calibration_residuals",
    }

    context = (
        await client.get(
            f"/api/v1/projects/{run['project_id']}/datasets/{run['source_id']}/visualization/context",
            headers=run["headers"],
        )
    ).json()
    layers = {layer["layer_type"]: layer for layer in context["layers"]}
    assert layers["dsm"]["available"] is True
    assert layers["dtm"]["available"] is False
    assert "ground filtering failed: forced test failure" in layers["dtm"]["unavailable_reason"]


async def test_write_failure_removes_partial_files_and_is_soft(client, monkeypatch):
    original = analysis_execution._write_ground_filter_raster
    calls = []

    def _fail_on_ndsm(path, array, **kwargs):
        calls.append(path.name)
        if path.name == "ndsm.tif":
            raise OSError("disk full (test)")
        return original(path, array, **kwargs)

    monkeypatch.setattr(analysis_execution, "_write_ground_filter_raster", _fail_on_ndsm)
    run = await _run_in_process(client, monkeypatch, "gf-writefail@example.com")
    assert calls == ["dtm.tif", "ndsm.tif"]
    assert run["job"]["status"] == "completed"
    assert run["job"]["ground_filter_status"] == "failed"
    assert "disk full (test)" in run["job"]["ground_filter_metadata"]["error"]
    # P1-5 residuals come from the gate-passed calibration, not the filter.
    assert set(run["artifacts"]) == {
        "relative_depth",
        "metric_elevation",
        "dsm",
        "calibration_residuals",
    }
    from app.core.storage import get_storage

    base = f"projects/{run['project_id']}/analysis/{run['job_id']}"
    assert not get_storage().exists(f"{base}/dtm.tif")
    assert not get_storage().exists(f"{base}/ndsm.tif")


async def test_visualization_layers_inspection_and_unchanged_terrain_source(client, monkeypatch):
    run = await _run_in_process(client, monkeypatch, "gf-viz@example.com")
    context = (
        await client.get(
            f"/api/v1/projects/{run['project_id']}/datasets/{run['source_id']}/visualization/context",
            headers=run["headers"],
        )
    ).json()
    layers = {layer["layer_type"]: layer for layer in context["layers"]}
    assert layers["dtm"]["display_name"] == "DTM (estimated bare earth)"
    assert layers["ndsm"]["display_name"] == "nDSM (estimated height above ground)"
    assert layers["dtm"]["available"] and layers["ndsm"]["available"]
    assert layers["ndsm"]["min_value"] == 0.0
    assert layers["ndsm"]["max_value"] == pytest.approx(6.0, abs=0.3)
    assert "ESTIMATED" in layers["ndsm"]["notes"]
    assert context["terrain"]["source_artifact_type"] == "dsm"
    assert context["terrain"]["artifact_id"] == run["artifacts"]["dsm"]["id"]

    base = f"/api/v1/projects/{run['project_id']}/analysis/{run['job_id']}/artifacts"
    value = (
        await client.get(
            f"{base}/{run['artifacts']['ndsm']['id']}/visualization/value?row=22&col=32",
            headers=run["headers"],
        )
    ).json()["value"]
    assert value == pytest.approx(6.0, abs=0.3)
    terrain = await client.get(
        f"{base}/{run['artifacts']['ndsm']['id']}/visualization/terrain/metadata",
        headers=run["headers"],
    )
    assert terrain.status_code == 422  # never a 3D terrain source


async def test_unavailable_reason_names_the_calibration_gate(client, monkeypatch):
    run = await _run_in_process(client, monkeypatch, "gf-reason@example.com", dem_scale=-2.0)
    context = (
        await client.get(
            f"/api/v1/projects/{run['project_id']}/datasets/{run['source_id']}/visualization/context",
            headers=run["headers"],
        )
    ).json()
    layers = {layer["layer_type"]: layer for layer in context["layers"]}
    for name in ("dtm", "ndsm"):
        assert layers[name]["available"] is False
        assert "calibration quality gate" in layers[name]["unavailable_reason"]
        assert "ground filtering failed" not in layers[name]["unavailable_reason"]


async def test_dtm_is_measurable_as_elevation_and_ndsm_is_rejected(client, monkeypatch):
    run = await _run_in_process(client, monkeypatch, "gf-measure@example.com")
    base = f"/api/v1/projects/{run['project_id']}/analysis/{run['job_id']}/artifacts"
    dtm_id, ndsm_id = run["artifacts"]["dtm"]["id"], run["artifacts"]["ndsm"]["id"]

    point = await client.get(
        f"{base}/{dtm_id}/measurements/point?row=22&col=32", headers=run["headers"]
    )
    assert point.status_code == 200
    assert point.json()["value_kind"] == "elevation"
    assert "ESTIMATED bare-earth" in point.json()["disclaimer"]

    for url in (
        f"{base}/{ndsm_id}/measurements/point?row=22&col=32",
        f"{base}/{ndsm_id}/measurements/distance?row1=0&col1=0&row2=5&col2=5",
        f"{base}/{ndsm_id}/measurements/profile?row1=0&col1=0&row2=5&col2=5&samples=5",
    ):
        resp = await client.get(url, headers=run["headers"])
        assert resp.status_code == 422
        message = resp.json()["error"]["message"]
        assert "not supported on the nDSM layer" in message
        assert "categorical" not in message

    save = await client.post(
        f"/api/v1/projects/{run['project_id']}/measurements",
        json={
            "measurement_type": "point_elevation",
            "analysis_job_id": run["job_id"],
            "artifact_id": ndsm_id,
            "row": 22,
            "col": 32,
        },
        headers=run["headers"],
    )
    assert save.status_code == 422


async def test_disaster_screening_rejects_dtm_and_ndsm(client, monkeypatch):
    run = await _run_in_process(client, monkeypatch, "gf-disaster@example.com")
    for artifact_type in ("dtm", "ndsm"):
        resp = await client.post(
            f"/api/v1/projects/{run['project_id']}/datasets/{run['source_id']}/analysis",
            json={
                "parameters": {
                    "version": "v1",
                    "disaster_source_artifact_id": run["artifacts"][artifact_type]["id"],
                    "run_flood_screening": True,
                    "water_level": 101.0,
                }
            },
            headers=run["headers"],
        )
        assert resp.status_code == 422, artifact_type


async def test_report_and_bundle_include_ground_filter_section(client, monkeypatch):
    run = await _run_in_process(client, monkeypatch, "gf-report@example.com")
    # Inserted directly (never enqueued) so this test generates the report
    # exactly once — POST /reports would also queue it for the suite's own
    # worker, racing this in-process generate_report() on the same files.
    user_id = (await client.get("/api/v1/auth/me", headers=run["headers"])).json()["id"]
    report_id = str(await _create_report_row(run["project_id"], run["source_id"], user_id))
    await generate_report(uuid.UUID(report_id))
    base = f"/api/v1/projects/{run['project_id']}/reports/{report_id}"

    data = (await client.get(f"{base}/json", headers=run["headers"])).json()
    section = data["depth_analysis"]["ground_filter"]
    assert section["status"] == "completed"
    assert section["parameters"]["max_window_m"] == 20.0
    assert section["policy"]["version"] == "v1"
    assert section["statistics"]["valid_count"] == 64 * 64
    assert section["statistics"]["ground_fraction"] == pytest.approx(1 - 16 / 4096)
    assert "ESTIMATED" in section["ndsm_value_semantics"]
    assert any("Raster-derived estimates" in text for text in data["limitations"])

    pdf = (await client.get(f"{base}/pdf", headers=run["headers"])).content
    text = " ".join("\n".join(p.extract_text() for p in PdfReader(io.BytesIO(pdf)).pages).split())
    for expected in (
        "Bare-Earth Estimate (DTM) and Height Above Ground (nDSM)",
        "ground share",
        "nDSM min / max / mean / p95",
        "Raster-derived estimates, not physical measurements",
        "not tuned or validated",
    ):
        assert expected in text, expected

    csv_text = (await client.get(f"{base}/csv", headers=run["headers"])).text
    assert "Ground Filter (DTM/nDSM estimate) Statistics" in csv_text

    bundle = (await client.get(f"{base}/bundle", headers=run["headers"])).content
    with zipfile.ZipFile(io.BytesIO(bundle)) as zf:
        names = set(zf.namelist())
        assert {"artifacts/dtm.tif", "artifacts/ndsm.tif"} <= names
        with rasterio.io.MemoryFile(zf.read("artifacts/ndsm.tif")) as memfile:
            with memfile.open() as ds:
                assert ds.nodata == GROUND_FILTER_NODATA and ds.width == 64


# --------------------------------------------------------------------------
# Integration — the real worker and the real Depth Anything model
# --------------------------------------------------------------------------


async def test_real_worker_calibrated_job_produces_dtm_and_ndsm(client, tmp_path):
    """Real model + a DEM deliberately derived from its own depth
    (pipeline fixture, NOT accuracy evidence): the gate passes, and the real
    worker writes DTM/nDSM satisfying every invariant on the DSM's grid."""
    headers = await _register_and_login(client, "gf-real@example.com")
    project_id = (
        await client.post("/api/v1/projects", json={"name": "GF real"}, headers=headers)
    ).json()["id"]
    scene = make_structured_scene_geotiff_bytes(
        width=64,
        height=64,
        crs=_UTM_CRS,
        origin_x=_ORIGIN_X,
        origin_y=_ORIGIN_Y,
        pixel_size=_PIXEL_SIZE,
    )
    source_id = (
        await client.post(
            f"/api/v1/projects/{project_id}/datasets",
            files={"file": ("scene.tif", scene, "image/tiff")},
            headers=headers,
        )
    ).json()["id"]
    dem = make_depth_consistent_dem_geotiff_bytes(
        real_structured_scene_depth(64, 64),
        crs=_UTM_CRS,
        origin_x=_ORIGIN_X,
        origin_y=_ORIGIN_Y,
        pixel_size=_PIXEL_SIZE,
    )
    dem_id = (
        await client.post(
            f"/api/v1/projects/{project_id}/datasets",
            files={"file": ("dem.tif", dem, "image/tiff")},
            data={"role": "dem_reference"},
            headers=headers,
        )
    ).json()["id"]
    job_id = (
        await client.post(
            f"/api/v1/projects/{project_id}/datasets/{source_id}/analysis",
            json={"parameters": {"version": "v1", "dem_reference_dataset_id": dem_id}},
            headers=headers,
        )
    ).json()["id"]

    deadline = asyncio.get_running_loop().time() + 120.0
    while asyncio.get_running_loop().time() < deadline:
        job = (
            await client.get(f"/api/v1/projects/{project_id}/analysis/{job_id}", headers=headers)
        ).json()
        if job["status"] not in ("queued", "running"):
            break
        await asyncio.sleep(0.3)
    assert job["status"] == "completed"
    assert job["calibration_status"] == "calibrated"
    assert job["ground_filter_status"] == "completed", job["ground_filter_metadata"]

    artifacts = {
        a["artifact_type"]: a
        for a in (
            await client.get(
                f"/api/v1/projects/{project_id}/analysis/{job_id}/artifacts", headers=headers
            )
        ).json()
    }
    run = {"headers": headers, "project_id": project_id, "job_id": job_id, "artifacts": artifacts}
    with (
        await _download(client, run, "dsm", tmp_path / "dsm.tif") as dsm_ds,
        await _download(client, run, "dtm", tmp_path / "dtm.tif") as dtm_ds,
        await _download(client, run, "ndsm", tmp_path / "ndsm.tif") as ndsm_ds,
    ):
        dsm, dtm, ndsm = dsm_ds.read(1), dtm_ds.read(1), ndsm_ds.read(1)
        assert dtm_ds.crs == dsm_ds.crs and dtm_ds.transform == dsm_ds.transform
        assert ndsm_ds.shape == dsm_ds.shape
    assert (dtm <= dsm).all() and (ndsm >= 0).all()
    np.testing.assert_array_equal(ndsm, dsm - dtm)
