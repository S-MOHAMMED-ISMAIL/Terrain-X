"""P1-4: slope-at-point measurement tests.

Service-level tests read synthetic slope rasters with KNOWN per-pixel values
(written directly to storage); API tests read REAL slope artifacts produced
by the actual disaster-screening pipeline (executed in-process from a
directly-inserted, never-enqueued job row, so the suite's worker cannot race
it). The slope value must always be exactly what the stored slope raster
holds at the pixel the map coordinate falls in — resolved against the SLOPE
raster's own CRS/transform, never recomputed or interpolated.
"""

import io
import math
import uuid
from datetime import UTC, datetime

import numpy as np
import psycopg
import pytest
import rasterio
from pypdf import PdfReader
from rasterio.crs import CRS
from rasterio.transform import from_origin
from rasterio.warp import transform as warp_transform

from app.core.config import get_settings
from app.core.exceptions import ValidationAppError
from app.core.storage import get_storage
from app.db.session import AsyncSessionLocal
from app.models.analysis_artifact import AnalysisArtifact
from app.models.analysis_job import (
    AnalysisJob,
    AnalysisJobStatus,
    CalibrationStatus,
    DisasterStatus,
)
from app.services import analysis_execution, measurement_service
from app.services.report_execution import generate_report
from geospatial.exceptions import MeasurementInputError
from tests.fixtures import make_dem_geotiff_bytes, make_structured_scene_geotiff_bytes
from tests.test_reports import _create_report_row

_UTM = "EPSG:32633"
_UTM_ORIGIN_X, _UTM_ORIGIN_Y, _UTM_PIXEL = 500000.0, 4649984.0, 2.0
_GEO_ORIGIN_X, _GEO_ORIGIN_Y, _GEO_PIXEL = 77.5, 13.0, 0.0001
_SLOPE_METADATA = {
    "units": "degrees",
    "method": "Horn (1981) 3x3 weighted finite-difference",
    "source_artifact_id": "dsm-artifact-id",
    "source_artifact_type": "dsm",
    "reprojected_for_analysis": False,
    "value_semantics": "Real terrain slope in degrees.",
}


# --------------------------------------------------------------------------
# Service level — synthetic slope raster with known values
# --------------------------------------------------------------------------


def _write_slope_raster(key: str, array: np.ndarray) -> None:
    path = get_storage().absolute_path(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=array.shape[0],
        width=array.shape[1],
        count=1,
        dtype="float32",
        crs=_UTM,
        transform=from_origin(_UTM_ORIGIN_X, _UTM_ORIGIN_Y, _UTM_PIXEL, _UTM_PIXEL),
        nodata=-9999.0,
    ) as dst:
        dst.write(array.astype("float32"), 1)


def _artifact(artifact_type: str, key: str, metadata: dict | None = None) -> AnalysisArtifact:
    return AnalysisArtifact(
        id=uuid.uuid4(),
        analysis_job_id=uuid.uuid4(),
        artifact_type=artifact_type,
        storage_key=key,
        mime_type="image/tiff",
        file_size_bytes=100,
        artifact_metadata=metadata,
    )


@pytest.fixture
def slope_artifact():
    """A 10x12 UTM slope raster whose value at (row, col) is row*100+col
    degrees/100, with a -9999 NoData pixel at (2, 3) and NaN at (4, 5)."""
    values = (np.arange(10)[:, None] * 100 + np.arange(12)[None, :]).astype("float32") / 100
    values[2, 3] = -9999.0
    values[4, 5] = np.nan
    key = f"test/{uuid.uuid4()}/slope.tif"
    _write_slope_raster(key, values)
    return _artifact("slope", key, dict(_SLOPE_METADATA)), values


def _centre(row: int, col: int) -> tuple[float, float]:
    return (
        _UTM_ORIGIN_X + (col + 0.5) * _UTM_PIXEL,
        _UTM_ORIGIN_Y - (row + 0.5) * _UTM_PIXEL,
    )


def test_valid_utm_point_returns_stored_value_unchanged(slope_artifact):
    artifact, values = slope_artifact
    x, y = _centre(6, 7)
    for crs in (None, _UTM):
        result = measurement_service.compute_point_slope(artifact, x=x, y=y, crs=crs)
        assert (result.row, result.col, result.in_bounds) == (6, 7, True)
        assert result.value == float(values[6, 7])
        assert result.value_kind == "slope"
        assert result.units == "degrees"
        assert result.artifact_type == "slope"
        assert result.coordinate.native_x == pytest.approx(x)
        assert result.coordinate.native_y == pytest.approx(y)
        assert (result.query_x, result.query_y, result.query_crs) == (x, y, crs)
        assert result.source_artifact_type == "dsm"
        assert result.source_artifact_id == "dsm-artifact-id"
        assert result.reprojected_for_analysis is False
        assert result.method.startswith("Horn")
        assert "no interpolation" in result.disclaimer


def test_units_come_from_artifact_metadata_not_hardcoded(slope_artifact):
    artifact, _ = slope_artifact
    artifact.artifact_metadata = {**_SLOPE_METADATA, "units": "test-unit"}
    result = measurement_service.compute_point_slope(
        artifact, x=_centre(1, 1)[0], y=_centre(1, 1)[1], crs=None
    )
    assert result.units == "test-unit"


def test_nodata_and_nan_pixels_report_no_value_but_in_bounds(slope_artifact):
    artifact, _ = slope_artifact
    for row, col in ((2, 3), (4, 5)):
        x, y = _centre(row, col)
        result = measurement_service.compute_point_slope(artifact, x=x, y=y, crs=None)
        assert result.in_bounds is True
        assert result.value is None
        assert (result.row, result.col) == (row, col)
        assert result.coordinate is not None


def test_outside_raster_reports_out_of_bounds_without_value_or_coordinate(slope_artifact):
    artifact, _ = slope_artifact
    for x, y in (_centre(-3, 4), _centre(4, 40), _centre(50, 50)):
        result = measurement_service.compute_point_slope(artifact, x=x, y=y, crs=None)
        assert result.in_bounds is False
        assert result.value is None
        assert result.coordinate is None


def test_wgs84_query_resolves_to_the_same_pixel_as_an_independent_rasterio_index(slope_artifact):
    artifact, values = slope_artifact
    x, y = _centre(8, 10)
    (lon,), (lat,) = warp_transform(CRS.from_string(_UTM), CRS.from_epsg(4326), [x], [y])
    result = measurement_service.compute_point_slope(artifact, x=lon, y=lat, crs="EPSG:4326")

    with rasterio.open(get_storage().absolute_path(artifact.storage_key)) as ds:
        (ux,), (uy,) = warp_transform(CRS.from_epsg(4326), ds.crs, [lon], [lat])
        row, col = ds.index(ux, uy)
    assert (result.row, result.col) == (row, col) == (8, 10)
    assert result.value == float(values[8, 10])


def test_invalid_inputs_raise_measurement_input_error(slope_artifact):
    artifact, _ = slope_artifact
    with pytest.raises(MeasurementInputError):
        measurement_service.compute_point_slope(artifact, x=math.nan, y=1.0, crs=None)
    with pytest.raises(MeasurementInputError):
        measurement_service.compute_point_slope(artifact, x=math.inf, y=1.0, crs=None)
    with pytest.raises(MeasurementInputError, match="Unrecognized CRS"):
        measurement_service.compute_point_slope(artifact, x=1.0, y=1.0, crs="EPSG:not-a-crs")


@pytest.mark.parametrize(
    "artifact_type",
    ["dsm", "metric_elevation", "dtm", "ndsm", "relative_depth", "aspect", "hillshade"],
)
def test_non_slope_artifacts_are_rejected(slope_artifact, artifact_type):
    artifact, _ = slope_artifact
    other = _artifact(artifact_type, artifact.storage_key)
    with pytest.raises(ValidationAppError, match="requires a slope artifact"):
        measurement_service.compute_point_slope(
            other, x=_centre(1, 1)[0], y=_centre(1, 1)[1], crs=None
        )


def test_point_elevation_on_slope_directs_to_slope_at_point(slope_artifact):
    artifact, _ = slope_artifact
    job = AnalysisJob(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        dataset_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        parameters={"version": "v1"},
        calibration_status=CalibrationStatus.UNCALIBRATED,
    )
    with pytest.raises(ValidationAppError) as excinfo:
        measurement_service.compute_point_elevation(artifact, job, row=1, col=1)
    assert "Slope at point" in excinfo.value.message
    assert "categorical" not in excinfo.value.message


def test_migration_added_point_slope_to_the_measurement_type_enum():
    url = get_settings().TEST_DATABASE_URL_SYNC.replace("postgresql+psycopg", "postgresql")
    with psycopg.connect(url) as conn:
        labels = [
            row[0]
            for row in conn.execute(
                "SELECT enumlabel FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid "
                "WHERE t.typname = 'measurement_type' ORDER BY enumsortorder"
            ).fetchall()
        ]
    assert labels == ["point_elevation", "distance", "profile", "coordinate", "point_slope"]


# --------------------------------------------------------------------------
# API — real slope artifacts from the actual disaster-screening pipeline
# --------------------------------------------------------------------------


async def _register_and_login(client, email: str) -> dict:
    await client.post("/api/v1/auth/register", json={"email": email, "password": "supersecret123"})
    resp = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": "supersecret123"}
    )
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _bump(xx, yy):
    # Spatially varying slope: a smooth mound on a gentle ramp.
    return 100.0 + 10.0 * xx + 40.0 * np.exp(-((xx - 0.5) ** 2 + (yy - 0.5) ** 2) / 0.02)


async def _real_slope_run(client, email: str, *, geographic: bool) -> dict:
    """Uploads a source image, inserts a completed calibrated parent job
    with a DSM on the SAME footprint, then executes a real disaster job
    in-process. Geographic=True uses EPSG:4326 so the slope raster is
    genuinely reprojected to local UTM by the disaster pipeline."""
    headers = await _register_and_login(client, email)
    project_id = (
        await client.post("/api/v1/projects", json={"name": "Slope"}, headers=headers)
    ).json()["id"]
    if geographic:
        grid = {
            "crs": "EPSG:4326",
            "origin_x": _GEO_ORIGIN_X,
            "origin_y": _GEO_ORIGIN_Y,
            "pixel_size": _GEO_PIXEL,
        }
    else:
        grid = {
            "crs": _UTM,
            "origin_x": _UTM_ORIGIN_X,
            "origin_y": _UTM_ORIGIN_Y,
            "pixel_size": _UTM_PIXEL,
        }
    scene = make_structured_scene_geotiff_bytes(width=64, height=64, **grid)
    dataset = (
        await client.post(
            f"/api/v1/projects/{project_id}/datasets",
            files={"file": ("scene.tif", scene, "image/tiff")},
            headers=headers,
        )
    ).json()
    user_id = (await client.get("/api/v1/auth/me", headers=headers)).json()["id"]

    async with AsyncSessionLocal() as db:
        parent = AnalysisJob(
            project_id=uuid.UUID(project_id),
            dataset_id=uuid.UUID(dataset["id"]),
            user_id=uuid.UUID(user_id),
            status=AnalysisJobStatus.COMPLETED,
            parameters={"version": "v1"},
            calibration_status=CalibrationStatus.CALIBRATED,
            completed_at=datetime.now(UTC),
        )
        db.add(parent)
        await db.commit()
        parent_id = parent.id
    key = f"projects/{project_id}/analysis/{parent_id}/dsm.tif"
    dsm_bytes = make_dem_geotiff_bytes(width=64, height=64, elevation_fn=_bump, **grid)
    path = get_storage().absolute_path(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(dsm_bytes)
    async with AsyncSessionLocal() as db:
        dsm = AnalysisArtifact(
            analysis_job_id=parent_id,
            artifact_type="dsm",
            storage_key=key,
            mime_type="image/tiff",
            file_size_bytes=len(dsm_bytes),
        )
        db.add(dsm)
        await db.commit()
        dsm_id = dsm.id
        disaster = AnalysisJob(
            project_id=uuid.UUID(project_id),
            dataset_id=uuid.UUID(dataset["id"]),
            user_id=uuid.UUID(user_id),
            status=AnalysisJobStatus.QUEUED,
            parameters={
                "version": "v1",
                "disaster_source_artifact_id": str(dsm_id),
                "run_landslide_screening": True,
            },
        )
        db.add(disaster)
        await db.commit()
        disaster_id = disaster.id
    await analysis_execution.execute_analysis_job(disaster_id)

    async with AsyncSessionLocal() as db:
        job = await db.get(AnalysisJob, disaster_id)
        assert job.disaster_status == DisasterStatus.COMPLETED
    artifacts = (
        await client.get(
            f"/api/v1/projects/{project_id}/analysis/{disaster_id}/artifacts", headers=headers
        )
    ).json()
    slope = next(a for a in artifacts if a["artifact_type"] == "slope")
    base = f"/api/v1/projects/{project_id}/analysis/{disaster_id}/artifacts"
    return {
        "headers": headers,
        "project_id": project_id,
        "dataset_id": dataset["id"],
        "job_id": str(disaster_id),
        "slope": slope,
        "dsm_id": str(dsm_id),
        "parent_id": str(parent_id),
        "slope_url": f"{base}/{slope['id']}/measurements/slope",
        "slope_path": get_storage().absolute_path(
            f"projects/{project_id}/analysis/{disaster_id}/slope.tif"
        ),
        "base": base,
    }


async def test_geographic_source_resolves_against_the_reprojected_slope_grid(client):
    """EPSG:4326 source: the disaster pipeline reprojected slope to UTM, so
    the slope grid differs from the source grid. The endpoint's answer must
    equal the stored value at the pixel an INDEPENDENT rasterio index finds
    in the slope raster — not the pixel the 2D map's source-grid row/col
    arithmetic would pick."""
    run = await _real_slope_run(client, "slope-geo@example.com", geographic=True)
    lon = _GEO_ORIGIN_X + _GEO_PIXEL * 40.3
    lat = _GEO_ORIGIN_Y - _GEO_PIXEL * 20.7
    resp = await client.get(
        f"{run['slope_url']}?x={lon}&y={lat}&crs=EPSG:4326", headers=run["headers"]
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["reprojected_for_analysis"] is True
    assert body["source_artifact_type"] == "dsm"
    assert body["units"] == "degrees" and body["value_kind"] == "slope"

    with rasterio.open(run["slope_path"]) as ds:
        assert ds.crs.to_epsg() != 4326  # really reprojected
        (ux,), (uy,) = warp_transform(CRS.from_epsg(4326), ds.crs, [lon], [lat])
        row, col = ds.index(ux, uy)
        stored = ds.read(1)[row, col]
        source_grid_shape_differs = (ds.height, ds.width) != (64, 64)
    assert (body["row"], body["col"]) == (row, col)
    assert body["value"] == pytest.approx(float(stored), abs=0.0)
    naive_source_grid_pixel = (math.floor(20.7), math.floor(40.3))
    assert source_grid_shape_differs or (row, col) != naive_source_grid_pixel
    assert (row, col) != naive_source_grid_pixel


async def test_api_outside_invalid_and_wrong_artifact(client):
    run = await _real_slope_run(client, "slope-api@example.com", geographic=False)
    h = run["headers"]

    outside = await client.get(f"{run['slope_url']}?x=0&y=0", headers=h)
    assert outside.status_code == 200
    assert outside.json()["in_bounds"] is False and outside.json()["value"] is None
    assert outside.json()["coordinate"] is None

    bad_crs = await client.get(f"{run['slope_url']}?x=1&y=1&crs=EPSG:not-a-crs", headers=h)
    assert bad_crs.status_code == 422
    nan = await client.get(f"{run['slope_url']}?x=nan&y=1", headers=h)
    assert nan.status_code == 422

    dsm_url = (
        f"/api/v1/projects/{run['project_id']}/analysis/{run['parent_id']}/artifacts/"
        f"{run['dsm_id']}/measurements/slope?x={_UTM_ORIGIN_X + 11}&y={_UTM_ORIGIN_Y - 11}"
    )
    wrong = await client.get(dsm_url, headers=h)
    assert wrong.status_code == 422
    assert "requires a slope artifact" in wrong.json()["error"]["message"]

    point_on_slope = await client.get(
        f"{run['base']}/{run['slope']['id']}/measurements/point?row=5&col=5", headers=h
    )
    assert point_on_slope.status_code == 422
    message = point_on_slope.json()["error"]["message"]
    assert "Slope at point" in message and "categorical" not in message


async def test_ownership_is_enforced(client):
    run = await _real_slope_run(client, "slope-owner@example.com", geographic=False)
    intruder = await _register_and_login(client, "slope-intruder@example.com")
    x, y = _UTM_ORIGIN_X + 21.0, _UTM_ORIGIN_Y - 21.0
    resp = await client.get(f"{run['slope_url']}?x={x}&y={y}", headers=intruder)
    assert resp.status_code == 404

    other_project = (
        await client.post("/api/v1/projects", json={"name": "Other"}, headers=run["headers"])
    ).json()["id"]
    wrong_project_url = run["slope_url"].replace(run["project_id"], other_project)
    resp = await client.get(f"{wrong_project_url}?x={x}&y={y}", headers=run["headers"])
    assert resp.status_code == 404

    save = await client.post(
        f"/api/v1/projects/{run['project_id']}/measurements",
        json={
            "measurement_type": "point_slope",
            "analysis_job_id": run["job_id"],
            "artifact_id": run["slope"]["id"],
            "x": x,
            "y": y,
            "crs": _UTM,
        },
        headers=intruder,
    )
    assert save.status_code == 404


async def test_save_list_get_delete_and_report_point_slope(client):
    run = await _real_slope_run(client, "slope-save@example.com", geographic=False)
    h = run["headers"]
    x, y = _UTM_ORIGIN_X + 41.0, _UTM_ORIGIN_Y - 61.0
    live = (await client.get(f"{run['slope_url']}?x={x}&y={y}&crs={_UTM}", headers=h)).json()
    assert live["value"] is not None

    payload = {
        "measurement_type": "point_slope",
        "analysis_job_id": run["job_id"],
        "artifact_id": run["slope"]["id"],
        "x": x,
        "y": y,
        "crs": _UTM,
    }
    fake = await client.post(
        f"/api/v1/projects/{run['project_id']}/measurements",
        json={**payload, "value": 89.0},
        headers=h,
    )
    assert fake.status_code == 422  # a client-supplied result is never accepted

    created = await client.post(
        f"/api/v1/projects/{run['project_id']}/measurements", json=payload, headers=h
    )
    assert created.status_code == 201, created.text
    saved = created.json()
    assert saved["measurement_type"] == "point_slope"
    assert saved["input_data"] == {"x": x, "y": y, "crs": _UTM}
    assert saved["result_data"] == live  # recomputed server-side, identical to the live read

    listed = (
        await client.get(f"/api/v1/projects/{run['project_id']}/measurements", headers=h)
    ).json()
    assert [m["id"] for m in listed] == [saved["id"]]
    got = await client.get(
        f"/api/v1/projects/{run['project_id']}/measurements/{saved['id']}", headers=h
    )
    assert got.status_code == 200 and got.json()["result_data"]["value_kind"] == "slope"

    # Inserted directly (never enqueued) so this test generates the report
    # exactly once — POST /reports would also queue it for the suite's own
    # worker, racing this in-process generate_report() on the same files.
    user_id = (await client.get("/api/v1/auth/me", headers=h)).json()["id"]
    report_id = str(await _create_report_row(run["project_id"], run["dataset_id"], user_id))
    await generate_report(uuid.UUID(report_id))
    base = f"/api/v1/projects/{run['project_id']}/reports/{report_id}"
    data = (await client.get(f"{base}/json", headers=h)).json()
    slope_rows = [m for m in data["measurements"] if m["measurement_type"] == "point_slope"]
    assert len(slope_rows) == 1
    assert slope_rows[0]["result_data"]["value"] == live["value"]
    assert slope_rows[0]["result_data"]["units"] == "degrees"
    csv_text = (await client.get(f"{base}/csv", headers=h)).text
    assert "point_slope" in csv_text
    pdf = (await client.get(f"{base}/pdf", headers=h)).content
    assert "point_slope" in "".join(p.extract_text() for p in PdfReader(io.BytesIO(pdf)).pages)

    deleted = await client.delete(
        f"/api/v1/projects/{run['project_id']}/measurements/{saved['id']}", headers=h
    )
    assert deleted.status_code == 204
    gone = await client.get(
        f"/api/v1/projects/{run['project_id']}/measurements/{saved['id']}", headers=h
    )
    assert gone.status_code == 404
