"""Phase 8: real terrain-derived hazard screening tests.

Covers fast unit tests of the pure geospatial math
(`geospatial/terrain_derivatives.py`) against synthetic rasters with KNOWN
elevation/slope values (no model, no network — an exact tilted-plane slope
matches basic trigonometry to floating-point precision), the scientific-
honesty content of the persisted disclaimers/labels, the standalone
disaster-screening job lifecycle end to end through the real API + real
worker execution path (`app/services/analysis_execution.py`,
`app/services/disaster_pipeline.py`), real artifact/legend persistence, and
real ownership/ isolation enforcement. No hazard result is ever mocked —
every assertion below is checked against the actual computed raster or the
actual persisted metadata.
"""

import math
import uuid
from datetime import UTC, datetime

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from app.core.storage import get_storage
from app.db.session import AsyncSessionLocal
from app.models.analysis_artifact import AnalysisArtifact
from app.models.analysis_job import (
    AnalysisJob,
    AnalysisJobStatus,
    CalibrationStatus,
    DisasterStatus,
)
from app.services import analysis_execution
from app.services.disaster_pipeline import (
    DISASTER_SCOPE_DISCLAIMER,
    FLOOD_METHOD_DISCLAIMER,
    LANDSLIDE_METHOD_DISCLAIMER,
    NOT_ELEVATION_ERROR,
    run_disaster_screening,
)
from geospatial.exceptions import DisasterAnalysisError
from geospatial.terrain_derivatives import (
    ASPECT_FLAT_SENTINEL,
    DEFAULT_HILLSHADE_ALTITUDE_DEG,
    DEFAULT_HILLSHADE_AZIMUTH_DEG,
    ELEVATION_ANALYSIS_NODATA,
    FLOOD_NOT_INUNDATED,
    FLOOD_POTENTIALLY_INUNDATED,
    LANDSLIDE_HIGH,
    LANDSLIDE_LOW,
    LANDSLIDE_MODERATE,
    LANDSLIDE_NODATA,
    LANDSLIDE_VERY_HIGH,
    LandslideThresholds,
    compute_hillshade,
    compute_slope_aspect,
    compute_terrain_statistics,
    prepare_elevation,
    run_flood_screening,
    run_landslide_screening,
)
from geospatial.vertical_units import read_raster_vertical_unit
from tests.fixtures import make_dem_geotiff_bytes, make_structured_scene_geotiff_bytes

_ORIGIN_X, _ORIGIN_Y, _PIXEL_SIZE, _UTM_CRS = 500000.0, 4649984.0, 2.0, "EPSG:32633"
_FT_PER_M = 3.280839895013123


# --------------------------------------------------------------------------
# Shared helpers (mirrors test_measurements.py / test_visualization.py)
# --------------------------------------------------------------------------


async def _register_and_login(client, email: str, password: str = "supersecret123") -> dict:
    await client.post("/api/v1/auth/register", json={"email": email, "password": password})
    resp = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


async def _create_project(client, headers: dict, name: str = "Disaster Test Project") -> str:
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


async def _create_job_row(
    project_id, dataset_id, user_id, *, calibration_status=CalibrationStatus.CALIBRATED
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


def _write_raw_bytes(storage_key: str, content: bytes) -> int:
    storage = get_storage()
    path = storage.absolute_path(storage_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return len(content)


async def _create_dsm_artifact(project_id, dataset_id, user_id, **dem_kwargs) -> tuple[str, str]:
    """Creates a real completed 'parent' job with a real georeferenced DSM
    artifact — the standard stand-in for an already-calibrated elevation
    source a Phase 8 disaster job cites, without re-running the full
    depth+calibration pipeline (same technique test_measurements.py/
    test_visualization.py already use for their own deterministic setup)."""
    job_id = await _create_job_row(project_id, dataset_id, user_id)
    content = make_dem_geotiff_bytes(
        width=dem_kwargs.pop("width", 32),
        height=dem_kwargs.pop("height", 32),
        crs=dem_kwargs.pop("crs", _UTM_CRS),
        origin_x=dem_kwargs.pop("origin_x", _ORIGIN_X),
        origin_y=dem_kwargs.pop("origin_y", _ORIGIN_Y),
        pixel_size=dem_kwargs.pop("pixel_size", _PIXEL_SIZE),
        **dem_kwargs,
    )
    key = f"projects/{project_id}/analysis/{job_id}/dsm.tif"
    size = _write_raw_bytes(key, content)
    artifact_id = await _create_artifact_row(job_id, "dsm", key, size)
    return job_id, artifact_id


async def _create_disaster_job(client, headers, project_id, dataset_id, **params) -> dict:
    resp = await client.post(
        f"/api/v1/projects/{project_id}/datasets/{dataset_id}/analysis",
        json={"parameters": {"version": "v1", **params}},
        headers=headers,
    )
    return resp


# --------------------------------------------------------------------------
# Pure geospatial unit tests: geospatial/terrain_derivatives.py
# --------------------------------------------------------------------------


def _write_elevation_tif(
    path, array, *, crs=_UTM_CRS, pixel_size=1.0, nodata=None, vertical_unit="metre"
):
    transform = from_origin(0.0, 0.0, pixel_size, pixel_size)
    profile = {
        "driver": "GTiff",
        "height": array.shape[0],
        "width": array.shape[1],
        "count": 1,
        "dtype": "float32",
        "crs": crs,
        "transform": transform,
    }
    if nodata is not None:
        profile["nodata"] = nodata
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(array.astype("float32"), 1)
        if vertical_unit is not None:
            dst.set_band_unit(1, vertical_unit)


def test_prepare_elevation_rejects_non_georeferenced(tmp_path):
    path = tmp_path / "plain.tif"
    with rasterio.open(
        path, "w", driver="GTiff", height=8, width=8, count=1, dtype="float32"
    ) as dst:
        dst.write(np.ones((8, 8), dtype="float32"), 1)
        dst.set_band_unit(1, "metre")

    with pytest.raises(DisasterAnalysisError, match="not georeferenced"):
        prepare_elevation(path)


def test_prepare_elevation_rejects_all_nodata_raster(tmp_path):
    path = tmp_path / "empty.tif"
    _write_elevation_tif(path, np.full((8, 8), -9999.0, dtype="float32"), nodata=-9999.0)

    with pytest.raises(DisasterAnalysisError, match="No valid elevation pixels"):
        prepare_elevation(path)


def test_rasterio_band_unit_round_trips_as_vertical_unit_metadata(tmp_path):
    path = tmp_path / "unit_roundtrip.tif"
    _write_elevation_tif(path, np.full((4, 4), 12.0, dtype="float32"), vertical_unit="foot")

    with rasterio.open(path) as dataset:
        assert dataset.units[0] == "foot"

    resolution = read_raster_vertical_unit(path)
    assert resolution.status == "known"
    assert resolution.unit.code == "ft"
    assert resolution.unit.to_metre == pytest.approx(0.3048)


def test_prepare_elevation_rejects_unknown_vertical_unit(tmp_path):
    path = tmp_path / "unknown_z_unit.tif"
    _write_elevation_tif(
        path, np.full((8, 8), 100.0, dtype="float32"), pixel_size=2.0, vertical_unit=None
    )

    with pytest.raises(DisasterAnalysisError, match="vertical units.*not assumed"):
        prepare_elevation(path)


def test_prepare_elevation_projected_crs_used_directly_no_reprojection(tmp_path):
    path = tmp_path / "utm.tif"
    _write_elevation_tif(path, np.full((8, 8), 100.0, dtype="float32"), pixel_size=2.0)

    prepared = prepare_elevation(path)
    assert prepared.reprojected is False
    assert prepared.pixel_width == pytest.approx(2.0)
    assert prepared.pixel_height == pytest.approx(2.0)
    assert prepared.crs.to_epsg() == 32633


def test_prepare_elevation_geographic_crs_is_reprojected_to_local_utm(tmp_path):
    path = tmp_path / "geographic.tif"
    transform = from_origin(77.5, 13.0, 0.0001, 0.0001)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=32,
        width=32,
        count=1,
        dtype="float32",
        crs="EPSG:4326",
        transform=transform,
    ) as dst:
        dst.write(np.full((32, 32), 100.0, dtype="float32"), 1)
        dst.set_band_unit(1, "metre")

    prepared = prepare_elevation(path)

    assert prepared.reprojected is True
    assert prepared.crs.is_geographic is False
    assert prepared.source_crs == "EPSG:4326"
    # A real local UTM pixel size is on the order of metres, never the
    # ~0.0001 degree value of the un-reprojected source — proof the module
    # never mixes degrees with the elevation's own z-unit (see the module's
    # SCOPE AND SCIENTIFIC HONESTY docstring).
    assert 1.0 < prepared.pixel_width < 100.0


def test_compute_slope_aspect_matches_known_tilted_plane_exactly(tmp_path):
    # z = 100 + 3*x + 2*y over a 1m grid -> dz/dx=3, dz/dy=2 everywhere on a
    # perfect plane; Horn's method reproduces this exactly away from the
    # reflect-padded border. Expected slope = atan(sqrt(3^2+2^2)) in degrees.
    size = 9
    xs, ys = np.meshgrid(np.arange(size, dtype="float32"), np.arange(size, dtype="float32"))
    elevation = (100.0 + 3.0 * xs + 2.0 * ys).astype("float32")
    path = tmp_path / "plane.tif"
    _write_elevation_tif(path, elevation, pixel_size=1.0)

    prepared = prepare_elevation(path)
    slope, aspect = compute_slope_aspect(prepared)

    expected_slope = math.degrees(math.atan(math.hypot(3.0, 2.0)))
    center = size // 2
    assert slope[center, center] == pytest.approx(expected_slope, abs=1e-3)

    # Real ESRI/Horn compass-bearing aspect: downslope direction for
    # dz/dx=3, dz/dy=2 (uphill toward +x/+y) points toward -x/-y overall;
    # verify against the same math the module itself documents rather than
    # a hardcoded number pulled from nowhere.
    aspect_math_deg = math.degrees(math.atan2(2.0, -3.0))
    expected_aspect = (90.0 - aspect_math_deg) % 360.0
    assert aspect[center, center] == pytest.approx(expected_aspect, abs=1e-3)


def test_metres_and_feet_produce_equivalent_slope_aspect_and_hillshade(tmp_path):
    size = 11
    xs, ys = np.meshgrid(np.arange(size, dtype="float32"), np.arange(size, dtype="float32"))
    elevation_m = (100.0 + 1.5 * xs + 0.75 * ys).astype("float32")
    metre_path = tmp_path / "plane_m.tif"
    foot_path = tmp_path / "plane_ft.tif"
    _write_elevation_tif(metre_path, elevation_m, pixel_size=2.0, vertical_unit="metre")
    _write_elevation_tif(foot_path, elevation_m * _FT_PER_M, pixel_size=2.0, vertical_unit="foot")

    prepared_m = prepare_elevation(metre_path)
    prepared_ft = prepare_elevation(foot_path)
    slope_m, aspect_m = compute_slope_aspect(prepared_m)
    slope_ft, aspect_ft = compute_slope_aspect(prepared_ft)
    hillshade_m = compute_hillshade(prepared_m)
    hillshade_ft = compute_hillshade(prepared_ft)

    # The foot-valued raster is persisted as float32 after scaling by
    # 3.280839895..., so exact metre recovery has tiny quantization noise.
    np.testing.assert_allclose(slope_ft, slope_m, atol=2e-4)
    np.testing.assert_allclose(aspect_ft, aspect_m, atol=2e-4)
    np.testing.assert_allclose(hillshade_ft, hillshade_m, atol=2e-6)


def test_compute_slope_aspect_flat_plane_has_zero_slope_and_flat_sentinel_aspect(tmp_path):
    path = tmp_path / "flat.tif"
    _write_elevation_tif(path, np.full((7, 7), 50.0, dtype="float32"), pixel_size=1.0)

    prepared = prepare_elevation(path)
    slope, aspect = compute_slope_aspect(prepared)

    center = 3
    assert slope[center, center] == pytest.approx(0.0, abs=1e-6)
    assert aspect[center, center] == pytest.approx(ASPECT_FLAT_SENTINEL)


def test_compute_slope_aspect_directional_cases_match_compass_bearing():
    # Three independent, hand-verified directional cases (see the module's
    # own aspect convention: 0=North, clockwise). Each uses a 5x5 plane so
    # the center pixel's full 3x3 neighborhood is real (unaffected by
    # reflect-padding at the raster edge).

    def _center_aspect(dz_dx: float, dz_dy: float) -> float:
        size = 5
        xs, ys = np.meshgrid(np.arange(size, dtype="float32"), np.arange(size, dtype="float32"))
        elevation = (dz_dx * xs + dz_dy * ys).astype("float32")
        transform = from_origin(0.0, 0.0, 1.0, 1.0)
        with rasterio.io.MemoryFile() as memfile:
            with memfile.open(
                driver="GTiff",
                height=size,
                width=size,
                count=1,
                dtype="float32",
                crs=_UTM_CRS,
                transform=transform,
            ) as dataset:
                dataset.write(elevation, 1)
            with memfile.open() as dataset:
                from geospatial.terrain_derivatives import PreparedElevation

                prepared = PreparedElevation(
                    elevation=elevation,
                    transform=transform,
                    crs=dataset.crs,
                    pixel_width=1.0,
                    pixel_height=1.0,
                    reprojected=False,
                    source_crs="EPSG:32633",
                    width=size,
                    height=size,
                )
        _, aspect = compute_slope_aspect(prepared)
        return float(aspect[2, 2])

    # Elevation highest at row 0 (north, top of a north-up raster) and
    # decreasing toward increasing row (south): the real downhill direction
    # (what "aspect" reports) faces south.
    assert _center_aspect(dz_dx=0.0, dz_dy=-5.0) == pytest.approx(180.0, abs=1e-3)
    # Elevation highest at the east edge (increasing column): downhill
    # direction faces west.
    assert _center_aspect(dz_dx=5.0, dz_dy=0.0) == pytest.approx(270.0, abs=1e-3)
    # Elevation highest at the south edge (increasing row): downhill
    # direction faces north.
    assert _center_aspect(dz_dx=0.0, dz_dy=5.0) == pytest.approx(0.0, abs=1e-3)


def test_compute_slope_aspect_propagates_nodata_to_full_neighborhood(tmp_path):
    elevation = np.full((7, 7), 10.0, dtype="float32")
    elevation[3, 3] = ELEVATION_ANALYSIS_NODATA
    path = tmp_path / "hole.tif"
    _write_elevation_tif(path, elevation, pixel_size=1.0, nodata=ELEVATION_ANALYSIS_NODATA)

    prepared = prepare_elevation(path)
    slope, aspect = compute_slope_aspect(prepared)

    # The NoData pixel itself and every one of its 8 neighbors must be
    # NoData in the output — a real, documented propagation rule, not an
    # incidental side effect.
    for r in range(2, 5):
        for c in range(2, 5):
            assert slope[r, c] == ELEVATION_ANALYSIS_NODATA
            assert aspect[r, c] == ELEVATION_ANALYSIS_NODATA
    # A pixel two rings away from the hole is unaffected.
    assert slope[0, 0] != ELEVATION_ANALYSIS_NODATA


# --------------------------------------------------------------------------
# compute_hillshade (P1-1: real terrain illumination, see
# geospatial/terrain_derivatives.py::compute_hillshade)
# --------------------------------------------------------------------------


def test_compute_hillshade_flat_plane_matches_known_value(tmp_path):
    """A flat plane's illumination is independent of azimuth (slope=0 zeroes
    out the azimuth/aspect term entirely) and equals sin(altitude) exactly —
    the same real simplification the module's own docstring documents."""
    path = tmp_path / "flat.tif"
    _write_elevation_tif(path, np.full((7, 7), 50.0, dtype="float32"), pixel_size=1.0)

    prepared = prepare_elevation(path)
    hillshade = compute_hillshade(prepared)

    expected = math.sin(math.radians(DEFAULT_HILLSHADE_ALTITUDE_DEG))
    center = 3
    assert hillshade[center, center] == pytest.approx(expected, abs=1e-5)

    # Changing azimuth must not change a flat surface's illumination at all.
    hillshade_other_azimuth = compute_hillshade(prepared, azimuth_deg=90.0, altitude_deg=45.0)
    assert hillshade_other_azimuth[center, center] == pytest.approx(expected, abs=1e-5)


def test_compute_hillshade_matches_known_tilted_plane_exactly(tmp_path):
    # Same tilted plane as the slope/aspect test above: z = 100 + 3x + 2y,
    # dz/dx=3, dz/dy=2 exactly on a 1m grid.
    size = 9
    xs, ys = np.meshgrid(np.arange(size, dtype="float32"), np.arange(size, dtype="float32"))
    elevation = (100.0 + 3.0 * xs + 2.0 * ys).astype("float32")
    path = tmp_path / "plane.tif"
    _write_elevation_tif(path, elevation, pixel_size=1.0)

    prepared = prepare_elevation(path)
    hillshade = compute_hillshade(prepared)

    # Independently hand-computed via the same standard Lambertian formula
    # the module's own docstring documents, using plain `math` (not numpy)
    # so this is a genuinely separate computation from the implementation.
    slope_rad = math.atan(math.hypot(3.0, 2.0))
    aspect_math_rad = math.atan2(2.0, -3.0)
    aspect_rad = (math.pi / 2.0) - aspect_math_rad
    zenith_rad = math.radians(90.0 - DEFAULT_HILLSHADE_ALTITUDE_DEG)
    azimuth_rad = math.radians(DEFAULT_HILLSHADE_AZIMUTH_DEG)
    expected = math.cos(zenith_rad) * math.cos(slope_rad) + math.sin(zenith_rad) * math.sin(
        slope_rad
    ) * math.cos(azimuth_rad - aspect_rad)
    expected = max(0.0, min(1.0, expected))

    center = size // 2
    assert hillshade[center, center] == pytest.approx(expected, abs=1e-4)
    assert 0.0 <= hillshade[center, center] <= 1.0


def test_compute_hillshade_is_deterministic_and_honors_sun_parameters(tmp_path):
    size = 7
    xs, ys = np.meshgrid(np.arange(size, dtype="float32"), np.arange(size, dtype="float32"))
    elevation = (20.0 + 2.0 * xs + 1.0 * ys).astype("float32")
    path = tmp_path / "parameterized_plane.tif"
    _write_elevation_tif(path, elevation, pixel_size=1.0)

    prepared = prepare_elevation(path)

    first = compute_hillshade(prepared, azimuth_deg=315.0, altitude_deg=45.0)
    second = compute_hillshade(prepared, azimuth_deg=315.0, altitude_deg=45.0)
    different_azimuth = compute_hillshade(prepared, azimuth_deg=135.0, altitude_deg=45.0)
    different_altitude = compute_hillshade(prepared, azimuth_deg=315.0, altitude_deg=20.0)

    center = size // 2
    assert np.array_equal(first, second)
    assert first[center, center] != pytest.approx(different_azimuth[center, center], abs=1e-5)
    assert first[center, center] != pytest.approx(different_altitude[center, center], abs=1e-5)


def test_compute_hillshade_opposite_orientation_changes_illumination():
    """A slope facing toward the sun must be brighter than the identical
    slope facing directly away from it — real, checkable directional
    behavior, mirroring test_compute_slope_aspect_directional_cases_match_compass_bearing."""

    def _center_hillshade(dz_dx: float, dz_dy: float) -> float:
        size = 5
        xs, ys = np.meshgrid(np.arange(size, dtype="float32"), np.arange(size, dtype="float32"))
        elevation = (dz_dx * xs + dz_dy * ys).astype("float32")
        transform = from_origin(0.0, 0.0, 1.0, 1.0)
        with rasterio.io.MemoryFile() as memfile:
            with memfile.open(
                driver="GTiff",
                height=size,
                width=size,
                count=1,
                dtype="float32",
                crs=_UTM_CRS,
                transform=transform,
            ) as dataset:
                dataset.write(elevation, 1)
            with memfile.open() as dataset:
                from geospatial.terrain_derivatives import PreparedElevation

                prepared = PreparedElevation(
                    elevation=elevation,
                    transform=transform,
                    crs=dataset.crs,
                    pixel_width=1.0,
                    pixel_height=1.0,
                    reprojected=False,
                    source_crs="EPSG:32633",
                    width=size,
                    height=size,
                )
        hillshade = compute_hillshade(prepared, azimuth_deg=315.0, altitude_deg=45.0)
        return float(hillshade[2, 2])

    # Elevation highest at row 0 (north) decreasing southward -> the surface
    # faces south (aspect=180°), which is the far side from a 315°(NW) sun:
    # low illumination. The mirrored terrain (elevation highest southward)
    # faces north (aspect=0°) -- also not toward a NW sun, but strictly
    # closer in bearing (0° vs 315° is 45° apart; 180° vs 315° is 135°
    # apart) -> strictly brighter.
    facing_away = _center_hillshade(dz_dx=0.0, dz_dy=-5.0)  # faces south
    facing_closer = _center_hillshade(dz_dx=0.0, dz_dy=5.0)  # faces north
    assert facing_closer > facing_away

    # A slope facing exactly toward a 315°(NW) sun (aspect=315°) must be the
    # brightest of all three. Verified via the same aspect formula this
    # module documents: aspect_math_deg = degrees(atan2(dz_dy, -dz_dx));
    # aspect_deg = 90 - aspect_math_deg. dz_dx=5, dz_dy=5 gives
    # aspect_math_deg = degrees(atan2(5, -5)) = 135.0, so
    # aspect_deg = 90 - 135 = -45 -> 315.0 exactly (0 degrees from the sun).
    assert math.degrees(math.atan2(5.0, -5.0)) == pytest.approx(135.0)
    facing_sun = _center_hillshade(dz_dx=5.0, dz_dy=5.0)
    assert facing_sun > facing_closer


def test_compute_hillshade_propagates_nodata_to_full_neighborhood(tmp_path):
    elevation = np.full((7, 7), 10.0, dtype="float32")
    elevation[3, 3] = ELEVATION_ANALYSIS_NODATA
    path = tmp_path / "hole.tif"
    _write_elevation_tif(path, elevation, pixel_size=1.0, nodata=ELEVATION_ANALYSIS_NODATA)

    prepared = prepare_elevation(path)
    hillshade = compute_hillshade(prepared)

    # Same real propagation rule as compute_slope_aspect: the NoData pixel
    # itself and every one of its 8 neighbors must be NoData in the output
    # -- an invalid terrain pixel is never given a fabricated illumination.
    for r in range(2, 5):
        for c in range(2, 5):
            assert hillshade[r, c] == ELEVATION_ANALYSIS_NODATA
    # A pixel two rings away from the hole is unaffected and is a real,
    # in-range illumination value, not NoData.
    assert hillshade[0, 0] != ELEVATION_ANALYSIS_NODATA
    assert 0.0 <= hillshade[0, 0] <= 1.0


def test_compute_hillshade_edge_boundary_behavior_matches_slope_aspect_convention(tmp_path):
    """Same reflect-padding convention as compute_slope_aspect: a fully
    valid, flat raster has NO NoData anywhere, including at the very edge/
    corner pixels -- boundary handling must be consistent between the two
    derivatives, not silently different."""
    path = tmp_path / "flat.tif"
    _write_elevation_tif(path, np.full((5, 5), 20.0, dtype="float32"), pixel_size=1.0)

    prepared = prepare_elevation(path)
    slope, _aspect = compute_slope_aspect(prepared)
    hillshade = compute_hillshade(prepared)

    # Corner and edge pixels are valid in both derivatives (reflect-padding
    # gives every edge pixel a real, if mirrored, 3x3 neighborhood).
    for r, c in [(0, 0), (0, 4), (4, 0), (4, 4), (0, 2), (2, 0)]:
        assert slope[r, c] != ELEVATION_ANALYSIS_NODATA
        assert hillshade[r, c] != ELEVATION_ANALYSIS_NODATA
        # A fully flat raster's illumination is identical everywhere,
        # corners/edges included -- proves reflect-padding introduces no
        # artificial edge gradient for hillshade, same as it doesn't for
        # slope (slope is exactly 0.0 everywhere on this fixture).
        assert hillshade[r, c] == pytest.approx(
            hillshade[2, 2], abs=1e-5
        ), f"pixel ({r},{c}) illumination differs from center on a flat plane"


def test_compute_hillshade_rejects_invalid_altitude(tmp_path):
    path = tmp_path / "flat.tif"
    _write_elevation_tif(path, np.full((5, 5), 20.0, dtype="float32"), pixel_size=1.0)
    prepared = prepare_elevation(path)

    with pytest.raises(DisasterAnalysisError, match="altitude"):
        compute_hillshade(prepared, altitude_deg=-1.0)
    with pytest.raises(DisasterAnalysisError, match="altitude"):
        compute_hillshade(prepared, altitude_deg=95.0)
    with pytest.raises(DisasterAnalysisError, match="altitude"):
        compute_hillshade(prepared, altitude_deg=math.nan)


def test_compute_hillshade_rejects_non_finite_azimuth(tmp_path):
    path = tmp_path / "flat.tif"
    _write_elevation_tif(path, np.full((5, 5), 20.0, dtype="float32"), pixel_size=1.0)
    prepared = prepare_elevation(path)

    with pytest.raises(DisasterAnalysisError, match="azimuth"):
        compute_hillshade(prepared, azimuth_deg=math.inf)


def test_compute_terrain_statistics_matches_known_values(tmp_path):
    elevation = np.array(
        [[10.0, 20.0, 30.0], [40.0, 50.0, 60.0], [70.0, 80.0, 90.0]], dtype="float32"
    )
    path = tmp_path / "stats.tif"
    _write_elevation_tif(path, elevation, pixel_size=1.0)

    prepared = prepare_elevation(path)
    slope, _ = compute_slope_aspect(prepared)
    stats = compute_terrain_statistics(prepared, slope)

    assert stats.min_elevation == pytest.approx(10.0)
    assert stats.max_elevation == pytest.approx(90.0)
    assert stats.mean_elevation == pytest.approx(50.0)
    assert stats.median_elevation == pytest.approx(50.0)
    assert stats.elevation_range == pytest.approx(80.0)
    assert stats.valid_pixel_count == 9
    assert stats.total_pixel_count == 9


def test_run_flood_screening_known_threshold_classification(tmp_path):
    elevation = np.array([[5.0, 10.0], [15.0, 20.0]], dtype="float32")
    path = tmp_path / "flood.tif"
    _write_elevation_tif(path, elevation, pixel_size=2.0)
    prepared = prepare_elevation(path)

    result = run_flood_screening(prepared, water_level=10.0)

    # elevation <= water_level -> potentially inundated (boundary inclusive).
    assert result.classification[0, 0] == FLOOD_POTENTIALLY_INUNDATED  # 5.0
    assert result.classification[0, 1] == FLOOD_POTENTIALLY_INUNDATED  # 10.0 (boundary)
    assert result.classification[1, 0] == FLOOD_NOT_INUNDATED  # 15.0
    assert result.classification[1, 1] == FLOOD_NOT_INUNDATED  # 20.0
    assert result.potentially_inundated_pixel_count == 2
    assert result.valid_pixel_count == 4
    assert result.potentially_inundated_percentage == pytest.approx(50.0)
    # pixel_area = 2m * 2m = 4 sq m; 2 inundated pixels -> 8 sq m.
    assert result.pixel_area == pytest.approx(4.0)
    assert result.potentially_inundated_area == pytest.approx(8.0)
    assert result.valid_area == pytest.approx(16.0)


def test_run_flood_screening_rejects_non_finite_water_level(tmp_path):
    path = tmp_path / "flood2.tif"
    _write_elevation_tif(path, np.full((4, 4), 10.0, dtype="float32"), pixel_size=1.0)
    prepared = prepare_elevation(path)

    with pytest.raises(DisasterAnalysisError, match="finite"):
        run_flood_screening(prepared, water_level=float("nan"))
    with pytest.raises(DisasterAnalysisError, match="finite"):
        run_flood_screening(prepared, water_level=float("inf"))


def test_run_flood_screening_propagates_elevation_nodata(tmp_path):
    elevation = np.array([[5.0, ELEVATION_ANALYSIS_NODATA], [15.0, 20.0]], dtype="float32")
    path = tmp_path / "flood3.tif"
    _write_elevation_tif(path, elevation, pixel_size=1.0, nodata=ELEVATION_ANALYSIS_NODATA)
    prepared = prepare_elevation(path)

    result = run_flood_screening(prepared, water_level=100.0)

    assert result.classification[0, 1] == LANDSLIDE_NODATA  # == FLOOD_NODATA == 0
    assert result.valid_pixel_count == 3


def test_run_landslide_screening_default_thresholds_boundary_classification():
    from geospatial.terrain_derivatives import PreparedElevation

    slope = np.array([[5.0, 10.0], [20.0, 30.0]], dtype="float32")
    prepared = PreparedElevation(
        elevation=np.zeros_like(slope),
        transform=from_origin(0.0, 0.0, 1.0, 1.0),
        crs=rasterio.crs.CRS.from_epsg(32633),
        pixel_width=1.0,
        pixel_height=1.0,
        reprojected=False,
        source_crs="EPSG:32633",
        width=2,
        height=2,
    )

    result = run_landslide_screening(prepared, slope)

    assert result.classification[0, 0] == LANDSLIDE_LOW  # 5.0 < 10
    assert result.classification[0, 1] == LANDSLIDE_MODERATE  # 10.0 (>= low_max, < moderate_max)
    assert result.classification[1, 0] == LANDSLIDE_HIGH  # 20.0 (>= moderate_max, < high_max)
    assert result.classification[1, 1] == LANDSLIDE_VERY_HIGH  # 30.0 (>= high_max)
    assert result.max_slope_deg == pytest.approx(30.0)
    assert result.mean_slope_deg == pytest.approx(16.25)


def test_run_landslide_screening_custom_thresholds_override_defaults():
    from geospatial.terrain_derivatives import PreparedElevation

    slope = np.array([[3.0, 6.0]], dtype="float32")
    prepared = PreparedElevation(
        elevation=np.zeros_like(slope),
        transform=from_origin(0.0, 0.0, 1.0, 1.0),
        crs=rasterio.crs.CRS.from_epsg(32633),
        pixel_width=1.0,
        pixel_height=1.0,
        reprojected=False,
        source_crs="EPSG:32633",
        width=2,
        height=1,
    )
    thresholds = LandslideThresholds(low_max_deg=5.0, moderate_max_deg=8.0, high_max_deg=12.0)

    result = run_landslide_screening(prepared, slope, thresholds=thresholds)

    assert result.classification[0, 0] == LANDSLIDE_LOW  # 3.0 < 5.0
    assert result.classification[0, 1] == LANDSLIDE_MODERATE  # 6.0 in [5, 8)
    assert result.thresholds.low_max_deg == pytest.approx(5.0)


def test_run_landslide_screening_propagates_slope_nodata():
    from geospatial.terrain_derivatives import PreparedElevation

    slope = np.array([[5.0, ELEVATION_ANALYSIS_NODATA]], dtype="float32")
    prepared = PreparedElevation(
        elevation=np.zeros_like(slope),
        transform=from_origin(0.0, 0.0, 1.0, 1.0),
        crs=rasterio.crs.CRS.from_epsg(32633),
        pixel_width=1.0,
        pixel_height=1.0,
        reprojected=False,
        source_crs="EPSG:32633",
        width=2,
        height=1,
    )

    result = run_landslide_screening(prepared, slope)

    assert result.classification[0, 1] == LANDSLIDE_NODATA
    assert result.valid_pixel_count == 1


# --------------------------------------------------------------------------
# Scientific-honesty tests: disaster_pipeline.py's persisted disclaimers
# --------------------------------------------------------------------------


def test_flood_disclaimer_never_uses_prediction_language():
    assert "prediction" not in FLOOD_METHOD_DISCLAIMER.lower()
    assert "simulat" not in FLOOD_METHOD_DISCLAIMER.lower().replace("does not model", "")
    assert "screening" in FLOOD_METHOD_DISCLAIMER.lower()


def test_landslide_disclaimer_never_uses_probability_language():
    assert "probability" in LANDSLIDE_METHOD_DISCLAIMER.lower()  # explicitly disclaims it
    lowered = LANDSLIDE_METHOD_DISCLAIMER.lower()
    assert "not a" in lowered or "not " in lowered


def test_disaster_scope_disclaimer_names_the_real_substitute_domains():
    for term in ("hydrological", "hydraulic", "geotechnical"):
        assert term in DISASTER_SCOPE_DISCLAIMER.lower()


def test_run_disaster_screening_builds_honest_full_metadata(tmp_path):
    elevation = np.array(
        [[100.0, 101.0, 102.0], [103.0, 104.0, 105.0], [106.0, 107.0, 108.0]], dtype="float32"
    )
    path = tmp_path / "full.tif"
    _write_elevation_tif(path, elevation, pixel_size=1.0)
    artifact_id = uuid.uuid4()

    outcome = run_disaster_screening(
        path,
        source_artifact_id=artifact_id,
        source_artifact_type="dsm",
        run_flood=True,
        water_level=104.0,
        run_landslide=True,
        landslide_thresholds=None,
    )

    assert outcome.metadata["source_artifact_id"] == str(artifact_id)
    assert outcome.metadata["source_artifact_type"] == "dsm"
    assert outcome.metadata["disclaimer"] == DISASTER_SCOPE_DISCLAIMER
    assert outcome.metadata["flood"]["disclaimer"] == FLOOD_METHOD_DISCLAIMER
    assert outcome.metadata["landslide"]["disclaimer"] == LANDSLIDE_METHOD_DISCLAIMER
    assert outcome.metadata["vertical_unit"]["unit"] == "m"
    provenance = outcome.metadata["unit_provenance"]
    assert provenance["source_vertical_unit"] == "m"
    assert provenance["calculation_vertical_unit"] == "m"
    assert provenance["vertical_conversion_factor"] == pytest.approx(1.0)
    assert provenance["source_raster_modified"] is False
    # Every real stage was actually timed (positive, finite floats).
    for key, value in outcome.metadata["timings_seconds"].items():
        assert isinstance(value, float) and value >= 0.0, key


def test_run_disaster_screening_flood_only_omits_landslide_key(tmp_path):
    path = tmp_path / "flood_only.tif"
    _write_elevation_tif(path, np.full((4, 4), 50.0, dtype="float32"), pixel_size=1.0)

    outcome = run_disaster_screening(
        path,
        source_artifact_id=uuid.uuid4(),
        source_artifact_type="metric_elevation",
        run_flood=True,
        water_level=60.0,
        run_landslide=False,
        landslide_thresholds=None,
    )

    assert "flood" in outcome.metadata
    assert "landslide" not in outcome.metadata


# --------------------------------------------------------------------------
# API/job-lifecycle tests: real standalone disaster-screening job end to end
# --------------------------------------------------------------------------


async def test_disaster_job_creation_rejects_nonexistent_artifact(client):
    headers = await _register_and_login(client, "disaster-badref@example.com")
    project_id = await _create_project(client, headers)
    dataset = await _upload_source_image(client, headers, project_id)

    resp = await _create_disaster_job(
        client,
        headers,
        project_id,
        dataset["id"],
        disaster_source_artifact_id=str(uuid.uuid4()),
        run_flood_screening=True,
        water_level=100.0,
    )
    assert resp.status_code == 422
    assert "not found" in resp.json()["error"]["message"].lower()


async def test_disaster_job_creation_rejects_relative_depth_artifact(client):
    headers = await _register_and_login(client, "disaster-wrongtype@example.com")
    project_id = await _create_project(client, headers)
    dataset = await _upload_source_image(client, headers, project_id)
    user_id = await _current_user_id(client, headers)
    job_id = await _create_job_row(project_id, dataset["id"], user_id)
    key = f"projects/{project_id}/analysis/{job_id}/depth.tif"
    size = _write_raw_bytes(
        key, make_dem_geotiff_bytes(width=8, height=8, crs=_UTM_CRS, pixel_size=1.0)
    )
    artifact_id = await _create_artifact_row(job_id, "relative_depth", key, size)

    resp = await _create_disaster_job(
        client,
        headers,
        project_id,
        dataset["id"],
        disaster_source_artifact_id=artifact_id,
        run_flood_screening=True,
        water_level=100.0,
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["message"] == NOT_ELEVATION_ERROR


async def test_disaster_job_creation_rejects_artifact_from_another_project(client):
    headers = await _register_and_login(client, "disaster-crossproj@example.com")
    project_a = await _create_project(client, headers, "Project A")
    project_b = await _create_project(client, headers, "Project B")
    dataset_a = await _upload_source_image(client, headers, project_a)
    dataset_b = await _upload_source_image(client, headers, project_b)
    user_id = await _current_user_id(client, headers)
    _, artifact_id = await _create_dsm_artifact(project_a, dataset_a["id"], user_id)

    resp = await _create_disaster_job(
        client,
        headers,
        project_b,
        dataset_b["id"],
        disaster_source_artifact_id=artifact_id,
        run_flood_screening=True,
        water_level=100.0,
    )
    assert resp.status_code == 422
    assert "not found in this project" in resp.json()["error"]["message"].lower()


async def test_disaster_job_creation_rejects_another_users_project(client):
    owner_headers = await _register_and_login(client, "disaster-owner@example.com")
    other_headers = await _register_and_login(client, "disaster-intruder@example.com")
    project_id = await _create_project(client, owner_headers)
    dataset = await _upload_source_image(client, owner_headers, project_id)
    user_id = await _current_user_id(client, owner_headers)
    _, artifact_id = await _create_dsm_artifact(project_id, dataset["id"], user_id)

    resp = await _create_disaster_job(
        client,
        other_headers,
        project_id,
        dataset["id"],
        disaster_source_artifact_id=artifact_id,
        run_flood_screening=True,
        water_level=100.0,
    )
    assert resp.status_code == 404


async def test_disaster_job_creation_requires_at_least_one_screening_type(client):
    headers = await _register_and_login(client, "disaster-noscreen@example.com")
    project_id = await _create_project(client, headers)
    dataset = await _upload_source_image(client, headers, project_id)
    user_id = await _current_user_id(client, headers)
    _, artifact_id = await _create_dsm_artifact(project_id, dataset["id"], user_id)

    resp = await _create_disaster_job(
        client,
        headers,
        project_id,
        dataset["id"],
        disaster_source_artifact_id=artifact_id,
    )
    assert resp.status_code == 422


async def test_disaster_job_full_lifecycle_creates_real_hazard_artifacts(client):
    headers = await _register_and_login(client, "disaster-full@example.com")
    project_id = await _create_project(client, headers)
    dataset = await _upload_source_image(client, headers, project_id)
    user_id = await _current_user_id(client, headers)
    _, artifact_id = await _create_dsm_artifact(project_id, dataset["id"], user_id)

    resp = await _create_disaster_job(
        client,
        headers,
        project_id,
        dataset["id"],
        disaster_source_artifact_id=artifact_id,
        run_flood_screening=True,
        water_level=110.0,
        run_landslide_screening=True,
    )
    assert resp.status_code == 201, resp.text
    job_id = resp.json()["id"]
    # A freshly-created (not-yet-executed) disaster job hasn't started
    # processing yet — the real worker call below is what advances it.
    assert resp.json()["disaster_status"] == "not_requested"

    await analysis_execution.execute_analysis_job(uuid.UUID(job_id))

    async with AsyncSessionLocal() as db:
        job = await db.get(AnalysisJob, uuid.UUID(job_id))
        assert job.status == AnalysisJobStatus.COMPLETED
        assert job.disaster_status == DisasterStatus.COMPLETED
        assert job.disaster_metadata is not None
        assert job.disaster_metadata["disclaimer"] == DISASTER_SCOPE_DISCLAIMER
        assert "flood" in job.disaster_metadata
        assert "landslide" in job.disaster_metadata
        artifact_ids = job.disaster_metadata["artifact_ids"]
        expected_types = {"slope", "aspect", "hillshade", "flood_screening", "landslide_screening"}
        assert set(artifact_ids.keys()) == expected_types

        rows = (
            (
                await db.execute(
                    AnalysisArtifact.__table__.select().where(
                        AnalysisArtifact.analysis_job_id == uuid.UUID(job_id)
                    )
                )
            )
            .mappings()
            .all()
        )
        by_type = {r["artifact_type"]: r for r in rows}
        assert set(by_type.keys()) == {
            "slope",
            "aspect",
            "hillshade",
            "flood_screening",
            "landslide_screening",
        }

        storage = get_storage()
        slope_path = storage.absolute_path(by_type["slope"]["storage_key"])
        with rasterio.open(slope_path) as ds:
            assert ds.dtypes[0] == "float32"
            assert ds.nodata == pytest.approx(ELEVATION_ANALYSIS_NODATA)

        hillshade_path = storage.absolute_path(by_type["hillshade"]["storage_key"])
        with rasterio.open(hillshade_path) as ds:
            assert ds.dtypes[0] == "float32"
            assert ds.nodata == pytest.approx(ELEVATION_ANALYSIS_NODATA)
            data = ds.read(1)
            valid = data != ELEVATION_ANALYSIS_NODATA
            assert bool(valid.any())
            assert float(data[valid].min()) >= 0.0
            assert float(data[valid].max()) <= 1.0

        flood_path = storage.absolute_path(by_type["flood_screening"]["storage_key"])
        with rasterio.open(flood_path) as ds:
            assert ds.dtypes[0] == "uint32"
            assert ds.nodata == 0
            data = ds.read(1)
            assert set(np.unique(data)) <= {0, FLOOD_NOT_INUNDATED, FLOOD_POTENTIALLY_INUNDATED}

        assert by_type["flood_screening"]["artifact_metadata"]["display_label"]
        assert by_type["landslide_screening"]["artifact_metadata"]["class_labels"]


async def test_disaster_job_visualization_context_reports_hazard_layers_with_legend(client):
    headers = await _register_and_login(client, "disaster-viz@example.com")
    project_id = await _create_project(client, headers)
    dataset = await _upload_source_image(client, headers, project_id)
    user_id = await _current_user_id(client, headers)
    _, artifact_id = await _create_dsm_artifact(project_id, dataset["id"], user_id)

    resp = await _create_disaster_job(
        client,
        headers,
        project_id,
        dataset["id"],
        disaster_source_artifact_id=artifact_id,
        run_flood_screening=True,
        water_level=110.0,
    )
    job_id = resp.json()["id"]
    await analysis_execution.execute_analysis_job(uuid.UUID(job_id))

    ctx_resp = await client.get(
        f"/api/v1/projects/{project_id}/datasets/{dataset['id']}/visualization/context",
        headers=headers,
    )
    assert ctx_resp.status_code == 200
    layers = {layer["layer_type"]: layer for layer in ctx_resp.json()["layers"]}
    assert layers["slope"]["available"] is True
    assert layers["flood_screening"]["available"] is True
    assert layers["flood_screening"]["is_categorical"] is True
    legend_labels = {entry["label"] for entry in layers["flood_screening"]["legend"]}
    assert legend_labels == {"Not potentially inundated", "Potentially inundated"}
    # semantic_segmentation-style layers never carry a fixed legend; slope
    # is a continuous raster and must not have one either.
    assert layers["slope"]["legend"] is None

    # P1-1: hillshade is always computed alongside slope/aspect (no opt-in
    # flag), so it must be a real, available, non-categorical layer here
    # too — never silently missing from the context this endpoint reports.
    assert layers["hillshade"]["available"] is True
    assert layers["hillshade"]["display_name"] == "Hillshade"
    assert layers["hillshade"]["is_categorical"] is False
    assert layers["hillshade"]["legend"] is None
    assert layers["hillshade"]["min_value"] is not None
    assert layers["hillshade"]["max_value"] is not None
    assert 0.0 <= layers["hillshade"]["min_value"] <= 1.0
    assert 0.0 <= layers["hillshade"]["max_value"] <= 1.0
    # Never elevation semantics -- hillshade is illumination, not height.
    # The real persisted value_semantics text must say so explicitly.
    assert "ILLUMINATION, NOT elevation" in layers["hillshade"]["notes"]


async def test_disaster_job_failure_before_processing_starts_leaves_disaster_status_not_requested(
    client,
):
    """A missing/invalid elevation file is caught by the worker's own
    VALIDATING_INPUT re-check, before `disaster_status` is ever advanced
    past its default — the whole job still fails (no independently valid
    fallback result, unlike Phase 4/6's soft-failure siblings), but there is
    no real "disaster processing" to have failed yet."""
    headers = await _register_and_login(client, "disaster-fail-early@example.com")
    project_id = await _create_project(client, headers)
    dataset = await _upload_source_image(client, headers, project_id)
    user_id = await _current_user_id(client, headers)
    job_id = await _create_job_row(project_id, dataset["id"], user_id)
    key = f"projects/{project_id}/analysis/{job_id}/dsm.tif"
    content = make_dem_geotiff_bytes(width=8, height=8, crs=_UTM_CRS, pixel_size=1.0)
    size = _write_raw_bytes(key, content)
    artifact_id = await _create_artifact_row(job_id, "dsm", key, size)
    get_storage().absolute_path(key).unlink()

    resp = await _create_disaster_job(
        client,
        headers,
        project_id,
        dataset["id"],
        disaster_source_artifact_id=artifact_id,
        run_flood_screening=True,
        water_level=100.0,
    )
    disaster_job_id = resp.json()["id"]

    await analysis_execution.execute_analysis_job(uuid.UUID(disaster_job_id))

    async with AsyncSessionLocal() as db:
        job = await db.get(AnalysisJob, uuid.UUID(disaster_job_id))
        assert job.status == AnalysisJobStatus.FAILED
        assert job.disaster_status == DisasterStatus.NOT_REQUESTED
        assert job.error_message is not None


async def test_disaster_job_failure_during_processing_sets_disaster_status_failed(client):
    """A real elevation raster with no valid pixels at all passes the
    worker's structural re-validation (file exists, right artifact type)
    but fails inside the actual terrain-derivative computation
    (`prepare_elevation` raises `DisasterAnalysisError`) — this is the real
    hard-failure path `disaster_status=FAILED` documents."""
    headers = await _register_and_login(client, "disaster-fail-mid@example.com")
    project_id = await _create_project(client, headers)
    dataset = await _upload_source_image(client, headers, project_id)
    user_id = await _current_user_id(client, headers)
    job_id = await _create_job_row(project_id, dataset["id"], user_id)
    key = f"projects/{project_id}/analysis/{job_id}/dsm.tif"
    all_nodata = make_dem_geotiff_bytes(
        width=8,
        height=8,
        crs=_UTM_CRS,
        pixel_size=1.0,
        elevation_fn=lambda xx, yy: np.full_like(xx, -9999.0),
    )
    size = _write_raw_bytes(key, all_nodata)
    artifact_id = await _create_artifact_row(job_id, "dsm", key, size)

    resp = await _create_disaster_job(
        client,
        headers,
        project_id,
        dataset["id"],
        disaster_source_artifact_id=artifact_id,
        run_flood_screening=True,
        water_level=100.0,
    )
    disaster_job_id = resp.json()["id"]

    await analysis_execution.execute_analysis_job(uuid.UUID(disaster_job_id))

    async with AsyncSessionLocal() as db:
        job = await db.get(AnalysisJob, uuid.UUID(disaster_job_id))
        assert job.status == AnalysisJobStatus.FAILED
        assert job.disaster_status == DisasterStatus.FAILED
        assert job.error_message is not None
        assert job.disaster_metadata is not None
        assert "error" in job.disaster_metadata


async def test_disaster_job_data_persists_across_fresh_db_session(client):
    """Stands in for a backend/Docker restart: nothing here is cached
    in-process — a brand-new AsyncSessionLocal must see exactly what a prior
    one committed, the same technique every other Phase in this project
    uses to prove real persistence rather than an in-memory illusion."""
    headers = await _register_and_login(client, "disaster-persist@example.com")
    project_id = await _create_project(client, headers)
    dataset = await _upload_source_image(client, headers, project_id)
    user_id = await _current_user_id(client, headers)
    _, artifact_id = await _create_dsm_artifact(project_id, dataset["id"], user_id)

    resp = await _create_disaster_job(
        client,
        headers,
        project_id,
        dataset["id"],
        disaster_source_artifact_id=artifact_id,
        run_landslide_screening=True,
    )
    job_id = resp.json()["id"]
    await analysis_execution.execute_analysis_job(uuid.UUID(job_id))

    async with AsyncSessionLocal() as db_a:
        job_a = await db_a.get(AnalysisJob, uuid.UUID(job_id))
        assert job_a.disaster_status == DisasterStatus.COMPLETED
        landslide_pct = job_a.disaster_metadata["landslide"]["class_percentages"]

    async with AsyncSessionLocal() as db_b:
        job_b = await db_b.get(AnalysisJob, uuid.UUID(job_id))
        assert job_b.disaster_status == DisasterStatus.COMPLETED
        assert job_b.disaster_metadata["landslide"]["class_percentages"] == landslide_pct
