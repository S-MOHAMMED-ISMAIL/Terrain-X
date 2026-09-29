"""Phase 7: real point elevation / distance / terrain-profile / coordinate
measurement tests.

Covers fast unit tests of the pure geospatial math (`geospatial/measurements.py`)
against synthetic rasters with KNOWN values/CRS/transforms (no model, no
network), the scientific-honesty service layer
(`app/services/measurement_service.py` — relative depth vs. calibrated
elevation labeling, unit disclaimers), the persisted CRUD API (real
ownership/ isolation, ownership across projects/users), the regression fix
for the Phase 5/6 3D-terrain coordinate-space bug, and one real end-to-end
integration test through the actual API + real depth model + real DEM
calibration — no mocking of the measurement calculation itself anywhere.
"""

import math
import uuid
from datetime import UTC, datetime

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from app.core.config import get_settings
from app.core.storage import get_storage
from app.db.session import AsyncSessionLocal
from app.models.analysis_artifact import AnalysisArtifact
from app.models.analysis_job import AnalysisJob, AnalysisJobStatus, CalibrationStatus
from app.models.measurement import Measurement
from app.services import measurement_service
from geospatial.exceptions import MeasurementInputError
from geospatial.measurements import (
    compute_distance,
    coordinate_to_pixel,
    pixel_to_coordinate,
    sample_point_elevation,
    sample_profile,
)
from geospatial.terrain_grid import extract_terrain_grid
from tests.fixtures import (
    make_depth_consistent_dem_geotiff_bytes,
    make_structured_scene_geotiff_bytes,
    real_structured_scene_depth,
)

_ORIGIN_X, _ORIGIN_Y, _PIXEL_SIZE, _UTM_CRS = 500000.0, 4649984.0, 2.0, "EPSG:32633"


# --------------------------------------------------------------------------
# Shared helpers (mirrors test_visualization.py / test_semantic_segmentation.py)
# --------------------------------------------------------------------------


async def _register_and_login(client, email: str, password: str = "supersecret123") -> dict:
    await client.post("/api/v1/auth/register", json={"email": email, "password": password})
    resp = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


async def _create_project(client, headers: dict, name: str = "Measurement Test Project") -> str:
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
    project_id, dataset_id, user_id, *, calibration_status=CalibrationStatus.UNCALIBRATED
) -> str:
    async with AsyncSessionLocal() as db:
        job = AnalysisJob(
            project_id=uuid.UUID(project_id),
            dataset_id=uuid.UUID(dataset_id),
            user_id=uuid.UUID(user_id),
            status=AnalysisJobStatus.COMPLETED,
            parameters={"version": "v1"},
            calibration_status=calibration_status,
            completed_at=datetime.now(UTC),
        )
        db.add(job)
        await db.commit()
        return str(job.id)


async def _create_artifact_row(job_id, artifact_type, storage_key, file_size, metadata=None) -> str:
    async with AsyncSessionLocal() as db:
        artifact = AnalysisArtifact(
            analysis_job_id=uuid.UUID(job_id),
            artifact_type=artifact_type,
            storage_key=storage_key,
            mime_type="image/tiff",
            file_size_bytes=file_size,
            artifact_metadata=metadata,
        )
        db.add(artifact)
        await db.commit()
        return str(artifact.id)


def _write_float_raster(storage_key, array, *, crs=None, transform=None, nodata=None) -> int:
    storage = get_storage()
    path = storage.absolute_path(storage_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    profile = {
        "driver": "GTiff",
        "height": array.shape[0],
        "width": array.shape[1],
        "count": 1,
        "dtype": "float32",
    }
    if crs is not None and transform is not None:
        profile["crs"] = crs
        profile["transform"] = transform
    if nodata is not None:
        profile["nodata"] = nodata
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(array.astype("float32"), 1)
    return path.stat().st_size


# --------------------------------------------------------------------------
# Unit tests: geospatial/measurements.py (pure, synthetic rasters)
# --------------------------------------------------------------------------


def test_sample_point_elevation_returns_known_real_value(tmp_path):
    array = np.arange(64, dtype="float32").reshape(8, 8)
    path = tmp_path / "known.tif"
    with rasterio.open(
        path, "w", driver="GTiff", height=8, width=8, count=1, dtype="float32"
    ) as dst:
        dst.write(array, 1)

    result = sample_point_elevation(path, row=3, col=5)
    assert result.value == pytest.approx(float(array[3, 5]))
    assert result.in_bounds is True


def test_sample_point_elevation_out_of_bounds(tmp_path):
    path = tmp_path / "small.tif"
    with rasterio.open(
        path, "w", driver="GTiff", height=4, width=4, count=1, dtype="float32"
    ) as dst:
        dst.write(np.zeros((4, 4), dtype="float32"), 1)

    result = sample_point_elevation(path, row=100, col=100)
    assert result.in_bounds is False
    assert result.value is None


def test_sample_point_elevation_real_nodata_reports_none_but_in_bounds(tmp_path):
    array = np.full((4, 4), 5.0, dtype="float32")
    array[1, 1] = -9999.0
    path = tmp_path / "nodata.tif"
    with rasterio.open(
        path, "w", driver="GTiff", height=4, width=4, count=1, dtype="float32", nodata=-9999.0
    ) as dst:
        dst.write(array, 1)

    result = sample_point_elevation(path, row=1, col=1)
    assert result.in_bounds is True
    assert result.value is None


def test_pixel_to_coordinate_matches_manual_transform(tmp_path):
    transform = from_origin(_ORIGIN_X, _ORIGIN_Y, _PIXEL_SIZE, _PIXEL_SIZE)
    path = tmp_path / "geo.tif"
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=8,
        width=8,
        count=1,
        dtype="float32",
        crs=_UTM_CRS,
        transform=transform,
    ) as dst:
        dst.write(np.zeros((8, 8), dtype="float32"), 1)

    result = pixel_to_coordinate(path, row=2.0, col=3.0)
    expected_x, expected_y = transform * (3.0 + 0.5, 2.0 + 0.5)
    assert result.native_x == pytest.approx(expected_x)
    assert result.native_y == pytest.approx(expected_y)
    assert result.crs == "EPSG:32633"
    assert result.wgs84_lat is not None and result.wgs84_lon is not None


def test_pixel_to_coordinate_non_georeferenced_returns_none_fields(tmp_path):
    path = tmp_path / "plain.tif"
    with rasterio.open(
        path, "w", driver="GTiff", height=4, width=4, count=1, dtype="float32"
    ) as dst:
        dst.write(np.zeros((4, 4), dtype="float32"), 1)

    result = pixel_to_coordinate(path, row=1.0, col=1.0)
    assert result.is_georeferenced is False
    assert result.native_x is None and result.wgs84_lat is None


def test_compute_distance_non_georeferenced_reports_pixels_only(tmp_path):
    path = tmp_path / "plain.tif"
    with rasterio.open(
        path, "w", driver="GTiff", height=20, width=20, count=1, dtype="float32"
    ) as dst:
        dst.write(np.zeros((20, 20), dtype="float32"), 1)

    result = compute_distance(path, row1=0, col1=0, row2=3, col2=4)
    assert result.pixel_distance == pytest.approx(5.0)  # real 3-4-5 triangle
    assert result.distance is None
    assert result.units == "pixels"
    assert result.is_georeferenced is False


def test_compute_distance_projected_crs_is_exact_real_metres(tmp_path):
    transform = from_origin(_ORIGIN_X, _ORIGIN_Y, _PIXEL_SIZE, _PIXEL_SIZE)
    path = tmp_path / "utm.tif"
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=20,
        width=20,
        count=1,
        dtype="float32",
        crs=_UTM_CRS,
        transform=transform,
    ) as dst:
        dst.write(np.zeros((20, 20), dtype="float32"), 1)

    result = compute_distance(path, row1=0, col1=0, row2=10, col2=0)
    assert result.distance == pytest.approx(20.0)  # 10 rows * 2m/pixel, real UTM metres
    assert result.units == "metre"
    assert result.reprojected is False


def test_compute_distance_geographic_crs_reprojects_never_naive_degrees(tmp_path):
    """A real geographic-CRS raster: verifies the distance is NOT computed
    by treating degrees as metres (a naive `hypot` of the raw lon/lat delta
    would be wildly wrong at any latitude), and is close to an
    independently-computed haversine distance for the same two points."""
    pixel_deg = 0.001
    transform = from_origin(10.0, 45.0, pixel_deg, pixel_deg)
    path = tmp_path / "geographic.tif"
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=50,
        width=50,
        count=1,
        dtype="float32",
        crs="EPSG:4326",
        transform=transform,
    ) as dst:
        dst.write(np.zeros((50, 50), dtype="float32"), 1)

    result = compute_distance(path, row1=0, col1=0, row2=10, col2=0)
    assert result.reprojected is True
    assert result.units == "metre"

    # Naive "degrees as metres": 10 rows * 0.001 deg = 0.01 (meaningless unit)
    naive_wrong = 10 * pixel_deg
    assert result.distance is not None
    assert result.distance > naive_wrong * 1000  # real metres, many orders larger

    # Independent haversine check against the real lat of both points (both
    # points share the same longitude, so this is a pure north-south distance).
    lat1 = 45.0 - 0.5 * pixel_deg
    lat2 = 45.0 - 10.5 * pixel_deg
    r_earth = 6371000.0
    d_lat = math.radians(lat2 - lat1)
    a = math.sin(d_lat / 2) ** 2
    haversine_distance = 2 * r_earth * math.asin(math.sqrt(a))
    assert result.distance == pytest.approx(haversine_distance, rel=0.01)


def test_compute_distance_out_of_bounds_raises_measurement_input_error(tmp_path):
    path = tmp_path / "small.tif"
    with rasterio.open(
        path, "w", driver="GTiff", height=4, width=4, count=1, dtype="float32"
    ) as dst:
        dst.write(np.zeros((4, 4), dtype="float32"), 1)

    with pytest.raises(MeasurementInputError):
        compute_distance(path, row1=0, col1=0, row2=999, col2=0)


def test_sample_profile_matches_known_linear_gradient(tmp_path):
    """A raster whose value at (row, col) is EXACTLY `row*10 + col` — real,
    exact expected values at every sampled pixel (nearest-pixel rounding is
    deterministic along this axis-aligned line)."""
    height, width = 20, 20
    array = np.fromfunction(lambda r, c: r * 10 + c, (height, width), dtype=np.float32)
    path = tmp_path / "gradient.tif"
    with rasterio.open(
        path, "w", driver="GTiff", height=height, width=width, count=1, dtype="float32"
    ) as dst:
        dst.write(array, 1)

    result = sample_profile(path, row1=0, col1=0, row2=0, col2=10, samples=6)
    assert result.sample_count == 6
    for sample in result.samples:
        expected = round(sample.row) * 10 + round(sample.col)
        assert sample.value == pytest.approx(expected)
    assert result.total_distance == pytest.approx(10.0)  # pure pixel distance, not georeferenced
    assert result.units == "pixels"


def test_sample_profile_total_distance_matches_compute_distance(tmp_path):
    transform = from_origin(_ORIGIN_X, _ORIGIN_Y, _PIXEL_SIZE, _PIXEL_SIZE)
    path = tmp_path / "utm.tif"
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=30,
        width=30,
        count=1,
        dtype="float32",
        crs=_UTM_CRS,
        transform=transform,
    ) as dst:
        dst.write(np.zeros((30, 30), dtype="float32"), 1)

    profile = sample_profile(path, row1=2, col1=3, row2=20, col2=15, samples=25)
    distance = compute_distance(path, row1=2, col1=3, row2=20, col2=15)
    assert profile.samples[-1].distance_along == pytest.approx(distance.distance, rel=1e-6)
    assert profile.samples[0].distance_along == pytest.approx(0.0)


def test_sample_profile_reports_real_nodata_as_none(tmp_path):
    array = np.full((10, 10), 5.0, dtype="float32")
    array[5, 5] = -9999.0
    path = tmp_path / "nodata_line.tif"
    with rasterio.open(
        path, "w", driver="GTiff", height=10, width=10, count=1, dtype="float32", nodata=-9999.0
    ) as dst:
        dst.write(array, 1)

    result = sample_profile(path, row1=5, col1=0, row2=5, col2=9, samples=10)
    values = [s.value for s in result.samples]
    assert None in values  # the real NoData pixel is reported as None
    assert any(v == pytest.approx(5.0) for v in values if v is not None)


def test_sample_profile_rejects_too_few_samples(tmp_path):
    path = tmp_path / "small.tif"
    with rasterio.open(
        path, "w", driver="GTiff", height=4, width=4, count=1, dtype="float32"
    ) as dst:
        dst.write(np.zeros((4, 4), dtype="float32"), 1)

    with pytest.raises(MeasurementInputError):
        sample_profile(path, row1=0, col1=0, row2=1, col2=1, samples=1)


def test_sample_profile_rejects_out_of_bounds(tmp_path):
    path = tmp_path / "small.tif"
    with rasterio.open(
        path, "w", driver="GTiff", height=4, width=4, count=1, dtype="float32"
    ) as dst:
        dst.write(np.zeros((4, 4), dtype="float32"), 1)

    with pytest.raises(MeasurementInputError):
        sample_profile(path, row1=0, col1=0, row2=99, col2=99, samples=5)


def test_sample_profile_reads_full_resolution_not_decimated(tmp_path):
    """A real single-pixel spike in an otherwise-flat 300x300 raster — real
    proof the profile sampler reads the FULL-resolution raster directly
    (never the ~256px-capped display terrain grid, which would average this
    spike away via Resampling.average decimation)."""
    height, width = 300, 300
    array = np.zeros((height, width), dtype="float32")
    array[150, 150] = 999.0  # a real, narrow spike
    path = tmp_path / "spike.tif"
    with rasterio.open(
        path, "w", driver="GTiff", height=height, width=width, count=1, dtype="float32"
    ) as dst:
        dst.write(array, 1)

    result = sample_profile(path, row1=150, col1=140, row2=150, col2=160, samples=21)
    values = [s.value for s in result.samples]
    assert any(v is not None and v == pytest.approx(999.0) for v in values)


def test_coordinate_to_pixel_round_trip(tmp_path):
    transform = from_origin(_ORIGIN_X, _ORIGIN_Y, _PIXEL_SIZE, _PIXEL_SIZE)
    path = tmp_path / "utm.tif"
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=40,
        width=40,
        count=1,
        dtype="float32",
        crs=_UTM_CRS,
        transform=transform,
    ) as dst:
        dst.write(np.zeros((40, 40), dtype="float32"), 1)

    coord = pixel_to_coordinate(path, row=15.0, col=25.0)
    pixel = coordinate_to_pixel(path, x=coord.native_x, y=coord.native_y, crs=coord.crs)
    assert pixel.row == 15
    assert pixel.col == 25
    assert pixel.in_bounds is True


def test_coordinate_to_pixel_reprojects_when_crs_differs(tmp_path):
    transform = from_origin(_ORIGIN_X, _ORIGIN_Y, _PIXEL_SIZE, _PIXEL_SIZE)
    path = tmp_path / "utm.tif"
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=40,
        width=40,
        count=1,
        dtype="float32",
        crs=_UTM_CRS,
        transform=transform,
    ) as dst:
        dst.write(np.zeros((40, 40), dtype="float32"), 1)

    coord = pixel_to_coordinate(path, row=10.0, col=10.0)
    # Ask using the point's real WGS84 lon/lat instead of its native UTM
    # coordinates — coordinate_to_pixel must reproject it back correctly.
    pixel = coordinate_to_pixel(path, x=coord.wgs84_lon, y=coord.wgs84_lat, crs="EPSG:4326")
    assert pixel.row == 10
    assert pixel.col == 10


def test_coordinate_to_pixel_non_georeferenced_raises(tmp_path):
    path = tmp_path / "plain.tif"
    with rasterio.open(
        path, "w", driver="GTiff", height=4, width=4, count=1, dtype="float32"
    ) as dst:
        dst.write(np.zeros((4, 4), dtype="float32"), 1)

    with pytest.raises(MeasurementInputError):
        coordinate_to_pixel(path, x=1.0, y=1.0)


def test_coordinate_to_pixel_out_of_bounds_reports_false(tmp_path):
    transform = from_origin(_ORIGIN_X, _ORIGIN_Y, _PIXEL_SIZE, _PIXEL_SIZE)
    path = tmp_path / "utm.tif"
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=10,
        width=10,
        count=1,
        dtype="float32",
        crs=_UTM_CRS,
        transform=transform,
    ) as dst:
        dst.write(np.zeros((10, 10), dtype="float32"), 1)

    pixel = coordinate_to_pixel(path, x=_ORIGIN_X - 10000, y=_ORIGIN_Y - 10000, crs=_UTM_CRS)
    assert pixel.in_bounds is False


# --------------------------------------------------------------------------
# 3D coordinate-space regression tests (the Phase 5/6 bug fix)
# --------------------------------------------------------------------------


def _grid_click_to_full_res_pixel(path, *, grid_row, grid_col, max_dimension):
    """Mirrors exactly what TerrainView3D.tsx now does: extract the real
    (possibly downsampled) terrain grid, compute the real map coordinate of
    one of its own grid cells, then resolve that coordinate back to a
    full-resolution pixel via the authoritative backend transform."""
    grid = extract_terrain_grid(path, max_dimension=max_dimension)
    # D1: the mesh vertex of grid cell (row, col) is that cell's centre.
    grid_local_x = (grid_col + 0.5) * (grid.cell_size_x or 1.0)
    grid_local_z = (grid_row + 0.5) * (grid.cell_size_y or 1.0)
    map_x = (grid.origin_x or 0.0) + grid_local_x
    map_y = (grid.origin_y or 0.0) + grid_local_z
    return coordinate_to_pixel(path, x=map_x, y=map_y, crs=grid.local_crs), grid


def test_3d_coordinate_fix_square_raster_corners_and_center(tmp_path):
    size = 2048
    max_dim = 256
    transform = from_origin(_ORIGIN_X, _ORIGIN_Y, 1.0, 1.0)
    path = tmp_path / "big_square.tif"
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=size,
        width=size,
        count=1,
        dtype="float32",
        crs=_UTM_CRS,
        transform=transform,
    ) as dst:
        dst.write(np.zeros((size, size), dtype="float32"), 1)

    ratio = size / max_dim  # 8.0

    for grid_row, grid_col, label in [
        (0, 0, "top-left corner"),
        (max_dim - 1, max_dim - 1, "bottom-right corner"),
        (0, max_dim - 1, "top-right corner"),
        (max_dim - 1, 0, "bottom-left corner"),
        (max_dim // 2, max_dim // 2, "center"),
    ]:
        pixel, grid = _grid_click_to_full_res_pixel(
            path, grid_row=grid_row, grid_col=grid_col, max_dimension=max_dim
        )
        assert grid.width == max_dim and grid.height == max_dim
        assert pixel.in_bounds, label
        # The grid cell's centre is the centre of its `ratio`-pixel block:
        # exactly full-resolution pixel floor((index + 0.5) * ratio).
        assert pixel.row == int((grid_row + 0.5) * ratio), label
        assert pixel.col == int((grid_col + 0.5) * ratio), label


def test_3d_coordinate_fix_non_square_raster(tmp_path):
    width, height = 2048, 1024
    max_dim = 256
    transform = from_origin(_ORIGIN_X, _ORIGIN_Y, 1.0, 1.0)
    path = tmp_path / "big_nonsquare.tif"
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=height,
        width=width,
        count=1,
        dtype="float32",
        crs=_UTM_CRS,
        transform=transform,
    ) as dst:
        dst.write(np.zeros((height, width), dtype="float32"), 1)

    grid = extract_terrain_grid(path, max_dimension=max_dim)
    row_ratio = height / grid.height
    col_ratio = width / grid.width

    for grid_row, grid_col in [
        (0, 0),
        (grid.height - 1, grid.width - 1),
        (grid.height // 2, grid.width // 2),
    ]:
        pixel, _ = _grid_click_to_full_res_pixel(
            path, grid_row=grid_row, grid_col=grid_col, max_dimension=max_dim
        )
        assert pixel.in_bounds
        assert pixel.row == math.floor((grid_row + 0.5) * row_ratio)
        assert pixel.col == math.floor((grid_col + 0.5) * col_ratio)


def test_3d_coordinate_fix_out_of_bounds_map_coordinate(tmp_path):
    size = 512
    transform = from_origin(_ORIGIN_X, _ORIGIN_Y, 1.0, 1.0)
    path = tmp_path / "medium.tif"
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=size,
        width=size,
        count=1,
        dtype="float32",
        crs=_UTM_CRS,
        transform=transform,
    ) as dst:
        dst.write(np.zeros((size, size), dtype="float32"), 1)

    pixel = coordinate_to_pixel(path, x=_ORIGIN_X - 100000, y=_ORIGIN_Y - 100000, crs=_UTM_CRS)
    assert pixel.in_bounds is False


def test_3d_coordinate_fix_no_downsampling_is_still_exact(tmp_path):
    """A source small enough that extract_terrain_grid does NOT downsample
    at all (grid dims == source dims) — the fix must still be exact, not
    just 'close', when there's no decimation to tolerate."""
    size = 64
    transform = from_origin(_ORIGIN_X, _ORIGIN_Y, 1.0, 1.0)
    path = tmp_path / "no_downsample.tif"
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=size,
        width=size,
        count=1,
        dtype="float32",
        crs=_UTM_CRS,
        transform=transform,
    ) as dst:
        dst.write(np.zeros((size, size), dtype="float32"), 1)

    grid = extract_terrain_grid(path, max_dimension=256)
    assert grid.width == size and grid.height == size  # confirms no decimation happened

    pixel, _ = _grid_click_to_full_res_pixel(path, grid_row=30, grid_col=40, max_dimension=256)
    assert pixel.row == 30
    assert pixel.col == 40


# --------------------------------------------------------------------------
# Service layer: scientific-honesty labeling + calibration gating
# --------------------------------------------------------------------------


def _make_artifact(
    artifact_type: str, storage_key: str, metadata: dict | None = None
) -> AnalysisArtifact:
    return AnalysisArtifact(
        id=uuid.uuid4(),
        analysis_job_id=uuid.uuid4(),
        artifact_type=artifact_type,
        storage_key=storage_key,
        mime_type="image/tiff",
        file_size_bytes=100,
        artifact_metadata=metadata,
    )


def _make_job(calibration_status=CalibrationStatus.CALIBRATED) -> AnalysisJob:
    return AnalysisJob(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        dataset_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        parameters={"version": "v1"},
        calibration_status=calibration_status,
    )


def test_point_elevation_on_relative_depth_never_labeled_elevation(tmp_path):
    key = "test/relative_depth.tif"
    _write_float_raster(key, np.full((4, 4), 0.5, dtype="float32"))
    artifact = _make_artifact("relative_depth", key)
    job = _make_job(calibration_status=CalibrationStatus.UNCALIBRATED)

    result = measurement_service.compute_point_elevation(artifact, job, row=1, col=1)
    assert result.value_kind == "relative_depth"
    assert "NOT elevation" in result.units
    assert "elevation" not in result.value_kind


def test_point_elevation_on_dsm_never_claims_metres(tmp_path):
    key = "test/dsm.tif"
    _write_float_raster(key, np.full((4, 4), 120.0, dtype="float32"))
    artifact = _make_artifact("dsm", key)
    job = _make_job(calibration_status=CalibrationStatus.CALIBRATED)

    result = measurement_service.compute_point_elevation(artifact, job, row=1, col=1)
    assert result.value_kind == "elevation"
    assert "metres" not in result.units.replace("not independently verified as metres", "")
    assert "unspecified" in result.units
    assert result.calibration_state == "calibrated"


def test_point_elevation_rejects_semantic_segmentation():
    from app.core.exceptions import ValidationAppError

    key = "test/semantic.tif"
    _write_float_raster(key, np.zeros((4, 4), dtype="float32"))
    artifact = _make_artifact("semantic_segmentation", key)
    job = _make_job()

    with pytest.raises(ValidationAppError):
        measurement_service.compute_point_elevation(artifact, job, row=0, col=0)


def test_distance_works_on_semantic_segmentation_artifact():
    """Distance is purely geometric — it must work on ANY artifact type,
    including categorical ones that have no scalar value to sample."""
    key = "test/semantic_dist.tif"
    transform = from_origin(_ORIGIN_X, _ORIGIN_Y, _PIXEL_SIZE, _PIXEL_SIZE)
    _write_float_raster(key, np.zeros((10, 10), dtype="float32"), crs=_UTM_CRS, transform=transform)
    artifact = _make_artifact("semantic_segmentation", key)
    job = _make_job()

    result = measurement_service.compute_distance(artifact, job, row1=0, col1=0, row2=5, col2=0)
    assert result.distance == pytest.approx(10.0)


def test_profile_uses_real_calibration_disclaimer_from_artifact_metadata():
    key = "test/dsm2.tif"
    _write_float_raster(key, np.full((10, 10), 100.0, dtype="float32"))
    artifact = _make_artifact(
        "dsm", key, metadata={"limitations": "A real, specific limitations string for this job."}
    )
    job = _make_job(calibration_status=CalibrationStatus.CALIBRATED)

    result = measurement_service.compute_profile(
        artifact, job, row1=0, col1=0, row2=0, col2=5, samples=6, settings=get_settings()
    )
    assert result.disclaimer == "A real, specific limitations string for this job."


def test_profile_rejects_samples_over_configured_maximum():
    from app.core.exceptions import ValidationAppError

    key = "test/dsm3.tif"
    _write_float_raster(key, np.zeros((10, 10), dtype="float32"))
    artifact = _make_artifact("dsm", key)
    job = _make_job()
    settings = get_settings()

    with pytest.raises(ValidationAppError):
        measurement_service.compute_profile(
            artifact,
            job,
            row1=0,
            col1=0,
            row2=0,
            col2=5,
            samples=settings.MAX_PROFILE_SAMPLES + 1,
            settings=settings,
        )


# --------------------------------------------------------------------------
# API integration: live compute + ownership (row-insertion technique)
# --------------------------------------------------------------------------


async def test_live_point_elevation_endpoint_matches_raster(client):
    headers = await _register_and_login(client, "meas-owner1@example.com")
    project_id = await _create_project(client, headers)
    dataset = await _upload_source_image(client, headers, project_id)
    user_id = await _current_user_id(client, headers)
    job_id = await _create_job_row(
        project_id, dataset["id"], user_id, calibration_status=CalibrationStatus.CALIBRATED
    )

    array = np.arange(64, dtype="float32").reshape(8, 8)
    key = f"projects/{project_id}/analysis/{job_id}/dsm.tif"
    size = _write_float_raster(key, array)
    artifact_id = await _create_artifact_row(job_id, "dsm", key, size)

    resp = await client.get(
        f"/api/v1/projects/{project_id}/analysis/{job_id}/artifacts/{artifact_id}/measurements/point"
        f"?row=3&col=5",
        headers=headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["value"] == pytest.approx(float(array[3, 5]))
    assert body["value_kind"] == "elevation"
    assert body["calibration_state"] == "calibrated"


async def test_live_relative_depth_point_never_reports_elevation(client):
    headers = await _register_and_login(client, "meas-owner2@example.com")
    project_id = await _create_project(client, headers)
    dataset = await _upload_source_image(client, headers, project_id)
    user_id = await _current_user_id(client, headers)
    job_id = await _create_job_row(
        project_id, dataset["id"], user_id, calibration_status=CalibrationStatus.UNCALIBRATED
    )

    key = f"projects/{project_id}/analysis/{job_id}/depth.tif"
    size = _write_float_raster(key, np.full((8, 8), 0.42, dtype="float32"))
    artifact_id = await _create_artifact_row(job_id, "relative_depth", key, size)

    resp = await client.get(
        f"/api/v1/projects/{project_id}/analysis/{job_id}/artifacts/{artifact_id}/measurements/point"
        f"?row=0&col=0",
        headers=headers,
    )
    body = resp.json()
    assert body["value_kind"] == "relative_depth"
    assert "NOT elevation" in body["units"]  # explicit negation, never a bare positive claim


async def test_live_distance_and_profile_endpoints(client):
    headers = await _register_and_login(client, "meas-owner3@example.com")
    project_id = await _create_project(client, headers)
    dataset = await _upload_source_image(client, headers, project_id)
    user_id = await _current_user_id(client, headers)
    job_id = await _create_job_row(
        project_id, dataset["id"], user_id, calibration_status=CalibrationStatus.CALIBRATED
    )

    transform = from_origin(_ORIGIN_X, _ORIGIN_Y, _PIXEL_SIZE, _PIXEL_SIZE)
    array = np.full((20, 20), 100.0, dtype="float32")
    key = f"projects/{project_id}/analysis/{job_id}/dsm.tif"
    size = _write_float_raster(key, array, crs=_UTM_CRS, transform=transform)
    artifact_id = await _create_artifact_row(job_id, "dsm", key, size)

    base = f"/api/v1/projects/{project_id}/analysis/{job_id}/artifacts/{artifact_id}/measurements"

    resp = await client.get(f"{base}/distance?row1=0&col1=0&row2=10&col2=0", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["distance"] == pytest.approx(20.0)

    resp = await client.get(
        f"{base}/profile?row1=0&col1=0&row2=0&col2=10&samples=11", headers=headers
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["sample_count"] == 11
    assert all(s["value"] == pytest.approx(100.0) for s in body["samples"])

    resp = await client.get(f"{base}/coordinate?row=0&col=0", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["crs"] == "EPSG:32633"

    # Out-of-bounds distance is a real, explicit 422 — never a fabricated result.
    resp = await client.get(f"{base}/distance?row1=0&col1=0&row2=999&col2=0", headers=headers)
    assert resp.status_code == 422


async def test_measurement_endpoints_enforce_full_ownership_chain(client):
    owner_headers = await _register_and_login(client, "meas-owner4@example.com")
    intruder_headers = await _register_and_login(client, "meas-intruder1@example.com")
    project_id = await _create_project(client, owner_headers)
    dataset = await _upload_source_image(client, owner_headers, project_id)
    user_id = await _current_user_id(client, owner_headers)
    job_id = await _create_job_row(project_id, dataset["id"], user_id)

    key = f"projects/{project_id}/analysis/{job_id}/dsm.tif"
    size = _write_float_raster(key, np.zeros((8, 8), dtype="float32"))
    artifact_id = await _create_artifact_row(job_id, "dsm", key, size)

    base = f"/api/v1/projects/{project_id}/analysis/{job_id}/artifacts/{artifact_id}/measurements"

    for suffix in ("point?row=0&col=0", "coordinate?row=0&col=0"):
        resp = await client.get(f"{base}/{suffix}")
        assert resp.status_code == 401, suffix
        resp = await client.get(f"{base}/{suffix}", headers=intruder_headers)
        assert resp.status_code == 404, suffix

    resp = await client.get(f"{base}/point?row=0&col=0", headers=owner_headers)
    assert resp.status_code == 200


# --------------------------------------------------------------------------
# Persisted CRUD + cross-project protection
# --------------------------------------------------------------------------


async def test_create_list_get_delete_measurement(client):
    headers = await _register_and_login(client, "meas-crud1@example.com")
    project_id = await _create_project(client, headers)
    dataset = await _upload_source_image(client, headers, project_id)
    user_id = await _current_user_id(client, headers)
    job_id = await _create_job_row(
        project_id, dataset["id"], user_id, calibration_status=CalibrationStatus.CALIBRATED
    )

    transform = from_origin(_ORIGIN_X, _ORIGIN_Y, _PIXEL_SIZE, _PIXEL_SIZE)
    key = f"projects/{project_id}/analysis/{job_id}/dsm.tif"
    size = _write_float_raster(
        key, np.full((10, 10), 50.0, dtype="float32"), crs=_UTM_CRS, transform=transform
    )
    artifact_id = await _create_artifact_row(job_id, "dsm", key, size)

    create_resp = await client.post(
        f"/api/v1/projects/{project_id}/measurements",
        json={
            "measurement_type": "distance",
            "analysis_job_id": job_id,
            "artifact_id": artifact_id,
            "row1": 0,
            "col1": 0,
            "row2": 5,
            "col2": 0,
        },
        headers=headers,
    )
    assert create_resp.status_code == 201
    body = create_resp.json()
    assert body["measurement_type"] == "distance"
    assert body["result_data"]["distance"] == pytest.approx(10.0)
    assert body["user_id"] == user_id
    measurement_id = body["id"]

    list_resp = await client.get(f"/api/v1/projects/{project_id}/measurements", headers=headers)
    assert list_resp.status_code == 200
    assert any(m["id"] == measurement_id for m in list_resp.json())

    get_resp = await client.get(
        f"/api/v1/projects/{project_id}/measurements/{measurement_id}", headers=headers
    )
    assert get_resp.status_code == 200
    assert get_resp.json()["id"] == measurement_id

    del_resp = await client.delete(
        f"/api/v1/projects/{project_id}/measurements/{measurement_id}", headers=headers
    )
    assert del_resp.status_code == 204

    gone_resp = await client.get(
        f"/api/v1/projects/{project_id}/measurements/{measurement_id}", headers=headers
    )
    assert gone_resp.status_code == 404

    # Real DB confirmation the row is actually gone, not just hidden from the API.
    async with AsyncSessionLocal() as db:
        assert await db.get(Measurement, uuid.UUID(measurement_id)) is None


async def test_create_measurement_recomputes_server_side_ignores_fake_result(client):
    """A client cannot persist a fabricated result — the server always
    recomputes from the real input coordinates against the real raster."""
    headers = await _register_and_login(client, "meas-crud2@example.com")
    project_id = await _create_project(client, headers)
    dataset = await _upload_source_image(client, headers, project_id)
    user_id = await _current_user_id(client, headers)
    job_id = await _create_job_row(
        project_id, dataset["id"], user_id, calibration_status=CalibrationStatus.CALIBRATED
    )
    key = f"projects/{project_id}/analysis/{job_id}/dsm.tif"
    size = _write_float_raster(key, np.full((8, 8), 77.0, dtype="float32"))
    artifact_id = await _create_artifact_row(job_id, "dsm", key, size)

    resp = await client.post(
        f"/api/v1/projects/{project_id}/measurements",
        json={
            "measurement_type": "point_elevation",
            "analysis_job_id": job_id,
            "artifact_id": artifact_id,
            "row": 2,
            "col": 2,
            # Even if a client tried to smuggle a fake result in extra
            # fields, the schema's extra="forbid" rejects it outright.
        },
        headers=headers,
    )
    assert resp.status_code == 201
    assert resp.json()["result_data"]["value"] == pytest.approx(77.0)


async def test_measurement_crud_enforces_cross_project_and_wrong_user_protection(client):
    owner_headers = await _register_and_login(client, "meas-owner5@example.com")
    intruder_headers = await _register_and_login(client, "meas-intruder2@example.com")
    project_id = await _create_project(client, owner_headers)
    dataset = await _upload_source_image(client, owner_headers, project_id)
    user_id = await _current_user_id(client, owner_headers)
    job_id = await _create_job_row(project_id, dataset["id"], user_id)
    key = f"projects/{project_id}/analysis/{job_id}/dsm.tif"
    size = _write_float_raster(key, np.zeros((8, 8), dtype="float32"))
    artifact_id = await _create_artifact_row(job_id, "dsm", key, size)

    create_resp = await client.post(
        f"/api/v1/projects/{project_id}/measurements",
        json={
            "measurement_type": "coordinate",
            "analysis_job_id": job_id,
            "artifact_id": artifact_id,
            "row": 0,
            "col": 0,
        },
        headers=owner_headers,
    )
    measurement_id = create_resp.json()["id"]

    # Intruder's own project, citing the victim's job/artifact.
    intruder_project_id = await _create_project(client, intruder_headers, name="Intruder Project")
    resp = await client.post(
        f"/api/v1/projects/{intruder_project_id}/measurements",
        json={
            "measurement_type": "coordinate",
            "analysis_job_id": job_id,
            "artifact_id": artifact_id,
            "row": 0,
            "col": 0,
        },
        headers=intruder_headers,
    )
    assert resp.status_code == 404  # the job doesn't belong to the intruder's project

    for method, path in [
        ("GET", f"/api/v1/projects/{project_id}/measurements/{measurement_id}"),
        ("DELETE", f"/api/v1/projects/{project_id}/measurements/{measurement_id}"),
        ("GET", f"/api/v1/projects/{project_id}/measurements"),
    ]:
        resp = await client.request(method, path, headers=intruder_headers)
        assert resp.status_code == 404, f"{method} {path}"

    # Valid owner can still access it after all the failed intrusion attempts.
    resp = await client.get(
        f"/api/v1/projects/{project_id}/measurements/{measurement_id}", headers=owner_headers
    )
    assert resp.status_code == 200


async def test_measurement_cascade_deletes_with_project(client):
    """Real deletion semantics: deleting the parent project takes its
    measurements with it (ON DELETE CASCADE), never leaving an orphan row."""
    headers = await _register_and_login(client, "meas-cascade@example.com")
    project_id = await _create_project(client, headers)
    dataset = await _upload_source_image(client, headers, project_id)
    user_id = await _current_user_id(client, headers)
    job_id = await _create_job_row(project_id, dataset["id"], user_id)
    key = f"projects/{project_id}/analysis/{job_id}/dsm.tif"
    size = _write_float_raster(key, np.zeros((8, 8), dtype="float32"))
    artifact_id = await _create_artifact_row(job_id, "dsm", key, size)

    create_resp = await client.post(
        f"/api/v1/projects/{project_id}/measurements",
        json={
            "measurement_type": "coordinate",
            "analysis_job_id": job_id,
            "artifact_id": artifact_id,
            "row": 0,
            "col": 0,
        },
        headers=headers,
    )
    measurement_id = uuid.UUID(create_resp.json()["id"])

    del_resp = await client.delete(f"/api/v1/projects/{project_id}", headers=headers)
    assert del_resp.status_code == 204

    async with AsyncSessionLocal() as db:
        assert await db.get(Measurement, measurement_id) is None


async def test_invalid_measurement_input_rejected(client):
    headers = await _register_and_login(client, "meas-invalid@example.com")
    project_id = await _create_project(client, headers)
    dataset = await _upload_source_image(client, headers, project_id)
    user_id = await _current_user_id(client, headers)
    job_id = await _create_job_row(project_id, dataset["id"], user_id)
    key = f"projects/{project_id}/analysis/{job_id}/dsm.tif"
    size = _write_float_raster(key, np.zeros((8, 8), dtype="float32"))
    artifact_id = await _create_artifact_row(job_id, "dsm", key, size)

    resp = await client.post(
        f"/api/v1/projects/{project_id}/measurements",
        json={
            "measurement_type": "distance",
            "analysis_job_id": job_id,
            "artifact_id": artifact_id,
            "row1": 0,
            "col1": 0,
            "row2": 999,
            "col2": 999,
        },
        headers=headers,
    )
    assert resp.status_code == 422

    # A missing required field for the discriminated type is a real 422.
    resp = await client.post(
        f"/api/v1/projects/{project_id}/measurements",
        json={
            "measurement_type": "distance",
            "analysis_job_id": job_id,
            "artifact_id": artifact_id,
        },
        headers=headers,
    )
    assert resp.status_code == 422


# --------------------------------------------------------------------------
# One real end-to-end integration test: real depth model + real DEM
# calibration + real API measurement endpoints (no mocking)
# --------------------------------------------------------------------------


async def test_real_calibrated_job_point_distance_profile_end_to_end(client):
    headers = await _register_and_login(client, "meas-real1@example.com")
    project_id = await _create_project(client, headers)
    source = await _upload_source_image(client, headers, project_id)
    dem = await _upload_dem_reference(client, headers, project_id)

    job_resp = await client.post(
        f"/api/v1/projects/{project_id}/datasets/{source['id']}/analysis",
        json={"parameters": {"version": "v1", "dem_reference_dataset_id": dem["id"]}},
        headers=headers,
    )
    job_id = job_resp.json()["id"]

    import asyncio

    deadline = asyncio.get_running_loop().time() + 60.0
    body = None
    while asyncio.get_running_loop().time() < deadline:
        resp = await client.get(f"/api/v1/projects/{project_id}/analysis/{job_id}", headers=headers)
        body = resp.json()
        if body["status"] not in ("queued", "running"):
            break
        await asyncio.sleep(0.3)
    assert body["status"] == "completed"
    assert body["calibration_status"] == "calibrated"

    artifacts = (
        await client.get(
            f"/api/v1/projects/{project_id}/analysis/{job_id}/artifacts", headers=headers
        )
    ).json()
    dsm_id = next(a["id"] for a in artifacts if a["artifact_type"] == "dsm")

    base = f"/api/v1/projects/{project_id}/analysis/{job_id}/artifacts/{dsm_id}/measurements"
    point_resp = await client.get(f"{base}/point?row=10&col=10", headers=headers)
    assert point_resp.status_code == 200
    assert point_resp.json()["value_kind"] == "elevation"

    distance_resp = await client.get(
        f"{base}/distance?row1=0&col1=0&row2=10&col2=0", headers=headers
    )
    assert distance_resp.status_code == 200
    assert distance_resp.json()["distance"] == pytest.approx(20.0)  # 10 rows * 2m/pixel

    profile_resp = await client.get(
        f"{base}/profile?row1=0&col1=0&row2=20&col2=20&samples=5", headers=headers
    )
    assert profile_resp.status_code == 200
    assert profile_resp.json()["sample_count"] == 5

    save_resp = await client.post(
        f"/api/v1/projects/{project_id}/measurements",
        json={
            "measurement_type": "point_elevation",
            "analysis_job_id": job_id,
            "artifact_id": dsm_id,
            "row": 10,
            "col": 10,
        },
        headers=headers,
    )
    assert save_resp.status_code == 201
