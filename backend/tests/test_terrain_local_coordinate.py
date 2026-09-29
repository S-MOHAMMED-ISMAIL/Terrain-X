"""P1-8: GET .../visualization/terrain/local-coordinate — a WGS84 point in a
terrain artifact's own local 3D frame (used for georeferenced 2D flythrough
waypoints). The local CRS/origin must be exactly those of the terrain grid
the 3D view is built from (geospatial/terrain_grid.py), for both a projected
source (its own CRS) and a geographic source (the UTM zone the grid is
reprojected into)."""

import numpy as np
import pytest
from rasterio.transform import from_origin
from rasterio.warp import transform as warp_transform

from app.core.config import get_settings
from app.core.storage import get_storage
from geospatial.terrain_grid import extract_terrain_grid
from tests.test_reports import (
    _create_artifact_row,
    _create_job_row,
    _current_user_id,
    _register_and_login,
    _upload_source_image,
    _write_float_raster,
)

_UTM = "EPSG:32633"
_UTM_TRANSFORM = from_origin(660000.0, 6660000.0, 40.0, 40.0)
_GEO_TRANSFORM = from_origin(17.87, 60.045, 0.0008, 0.0004)


async def _setup(client, email, *, artifact_type="dsm", crs=_UTM, transform=_UTM_TRANSFORM):
    headers = await _register_and_login(client, email)
    project_id = (
        await client.post("/api/v1/projects", json={"name": "Local coord"}, headers=headers)
    ).json()["id"]
    dataset = await _upload_source_image(client, headers, project_id)
    user_id = await _current_user_id(client, headers)
    job_id = await _create_job_row(project_id, dataset["id"], user_id)
    key = f"projects/{project_id}/analysis/{job_id}/{artifact_type}.tif"
    values = (np.arange(64 * 64, dtype="float32").reshape(64, 64) / 10.0) + 100.0
    size = _write_float_raster(key, values, crs=crs, transform=transform)
    artifact_id = await _create_artifact_row(job_id, artifact_type, key, size)
    base = f"/api/v1/projects/{project_id}/analysis/{job_id}/artifacts/{artifact_id}"
    return headers, base, get_storage().absolute_path(key)


async def _local(client, headers, base, lng, lat):
    return await client.get(
        f"{base}/visualization/terrain/local-coordinate",
        params={"lng": lng, "lat": lat},
        headers=headers,
    )


async def test_projected_source_uses_its_own_crs_and_terrain_origin(client):
    headers, base, path = await _setup(client, "lc-projected@example.com")
    grid = extract_terrain_grid(path, max_dimension=get_settings().MAX_TERRAIN_DIMENSION)
    metadata = (await client.get(f"{base}/visualization/terrain/metadata", headers=headers)).json()
    assert metadata["local_crs"] == grid.local_crs == _UTM

    # True positions of some pixel centres, expressed in WGS84.
    xs = [660000 + 40 * (c + 0.5) for c in (0, 17, 40, 62)]
    ys = [6660000 - 40 * (r + 0.5) for r in (0, 30, 5, 62)]
    lngs, lats = warp_transform(_UTM, "EPSG:4326", xs, ys)
    for x, y, lng, lat in zip(xs, ys, lngs, lats, strict=True):
        body = (await _local(client, headers, base, lng, lat)).json()
        assert body["local_crs"] == _UTM
        assert body["map_x"] == pytest.approx(x, abs=1e-6)
        assert body["map_y"] == pytest.approx(y, abs=1e-6)
        assert body["in_footprint"] is True
        # grid-local = map - origin; a pixel centre is at (index + 0.5) cells,
        # exactly where the mesh places that pixel's vertex (D1).
        col = (body["map_x"] - metadata["origin_x"]) / metadata["cell_size_x"]
        row = (body["map_y"] - metadata["origin_y"]) / metadata["cell_size_y"]
        assert col - 0.5 == pytest.approx(round(col - 0.5), abs=1e-9)
        assert row - 0.5 == pytest.approx(round(row - 0.5), abs=1e-9)
        assert 0.5 - 1e-9 <= col <= metadata["width"] - 0.5 + 1e-9
        assert 0.5 - 1e-9 <= row <= metadata["height"] - 0.5 + 1e-9

    # The mesh's vertices are the cell centres, so the LAST pixel's centre is
    # on the rendered surface (its last vertex) ...
    (lng,), (lat,) = warp_transform(_UTM, "EPSG:4326", [660000 + 40 * 63.5], [6660000 - 40 * 10.5])
    last = (await _local(client, headers, base, lng, lat)).json()
    assert last["in_footprint"] is True
    assert last["map_x"] == pytest.approx(660000 + 40 * 63.5, abs=1e-6)
    # ... while the outer half of the last pixel, beyond its centre, is not
    # (reported as outside, never clamped).
    (lng,), (lat,) = warp_transform(_UTM, "EPSG:4326", [660000 + 40 * 63.9], [6660000 - 40 * 10.5])
    rim = (await _local(client, headers, base, lng, lat)).json()
    assert rim["in_footprint"] is False
    assert rim["map_x"] == pytest.approx(660000 + 40 * 63.9, abs=1e-6)
    (lng,), (lat,) = warp_transform(_UTM, "EPSG:4326", [660000 + 40 * 0.2], [6660000 - 40 * 10.5])
    assert (await _local(client, headers, base, lng, lat)).json()["in_footprint"] is False


async def test_geographic_source_uses_the_terrain_grids_utm_frame(client):
    headers, base, path = await _setup(
        client, "lc-geographic@example.com", crs="EPSG:4326", transform=_GEO_TRANSFORM
    )
    grid = extract_terrain_grid(path, max_dimension=get_settings().MAX_TERRAIN_DIMENSION)
    assert grid.local_crs is not None and grid.local_crs != "EPSG:4326"
    lng, lat = 17.87 + 0.0008 * 20.5, 60.045 - 0.0004 * 40.5
    body = (await _local(client, headers, base, lng, lat)).json()
    assert body["local_crs"] == grid.local_crs
    (ex,), (ey,) = warp_transform("EPSG:4326", grid.local_crs, [lng], [lat])
    assert body["map_x"] == pytest.approx(ex, abs=1e-6)
    assert body["map_y"] == pytest.approx(ey, abs=1e-6)
    assert body["in_footprint"] is True


async def test_local_coordinate_resolves_to_the_same_pixel_as_the_raw_wgs84_point(client):
    """The 2D waypoint conversion agrees with the backend-authoritative pixel
    resolution: resolving the returned local coordinate (in local_crs) and
    resolving the original lng/lat give the same full-resolution pixel."""
    for email, crs, transform in (
        ("lc-rt-utm@example.com", _UTM, _UTM_TRANSFORM),
        ("lc-rt-geo@example.com", "EPSG:4326", _GEO_TRANSFORM),
    ):
        headers, base, _ = await _setup(client, email, crs=crs, transform=transform)
        rng = np.random.default_rng(3)
        for _ in range(8):
            col, row = rng.uniform(1, 63), rng.uniform(1, 63)
            x, y = transform * (col, row)
            (lng,), (lat,) = warp_transform(crs, "EPSG:4326", [x], [y])
            local = (await _local(client, headers, base, lng, lat)).json()
            via_local = (
                await client.get(
                    f"{base}/measurements/pixel",
                    params={"x": local["map_x"], "y": local["map_y"], "crs": local["local_crs"]},
                    headers=headers,
                )
            ).json()
            via_wgs84 = (
                await client.get(
                    f"{base}/measurements/pixel",
                    params={"x": lng, "y": lat, "crs": "EPSG:4326"},
                    headers=headers,
                )
            ).json()
            assert (via_local["row"], via_local["col"]) == (via_wgs84["row"], via_wgs84["col"])
            assert via_local["in_bounds"] and via_wgs84["in_bounds"]


async def test_outside_point_is_reported_not_adjusted(client):
    headers, base, _ = await _setup(client, "lc-outside@example.com")
    (x,), (y,) = [700000.0], [6700000.0]
    (lng,), (lat,) = warp_transform(_UTM, "EPSG:4326", [x], [y])
    body = (await _local(client, headers, base, lng, lat)).json()
    assert body["in_footprint"] is False
    assert body["map_x"] == pytest.approx(x, abs=1e-6)
    assert body["map_y"] == pytest.approx(y, abs=1e-6)


async def test_rejections_and_ownership(client):
    headers, base, _ = await _setup(client, "lc-owner@example.com")
    assert (await _local(client, headers, base, 17.9, 60.02)).status_code == 200
    # Invalid coordinates are refused, never clamped.
    for lng, lat in ((200.0, 10.0), (10.0, 95.0)):
        resp = await _local(client, headers, base, lng, lat)
        assert resp.status_code == 422
        assert "not a valid WGS84" in resp.json()["error"]["message"]
    intruder = await _register_and_login(client, "lc-intruder@example.com")
    assert (await _local(client, intruder, base, 17.9, 60.02)).status_code == 404

    # A non-terrain artifact.
    headers2, slope_base, _ = await _setup(client, "lc-slope@example.com", artifact_type="slope")
    resp = await _local(client, headers2, slope_base, 17.9, 60.02)
    assert resp.status_code == 422
    assert "3D terrain can only be generated" in resp.json()["error"]["message"]


async def test_non_georeferenced_terrain_is_rejected(client):
    headers = await _register_and_login(client, "lc-plain@example.com")
    project_id = (
        await client.post("/api/v1/projects", json={"name": "Plain"}, headers=headers)
    ).json()["id"]
    dataset = await _upload_source_image(client, headers, project_id)
    user_id = await _current_user_id(client, headers)
    job_id = await _create_job_row(project_id, dataset["id"], user_id)
    key = f"projects/{project_id}/analysis/{job_id}/depth.tif"
    size = _write_float_raster(key, np.linspace(0, 1, 32 * 32, dtype="float32").reshape(32, 32))
    artifact_id = await _create_artifact_row(job_id, "relative_depth", key, size)
    resp = await client.get(
        f"/api/v1/projects/{project_id}/analysis/{job_id}/artifacts/{artifact_id}"
        "/visualization/terrain/local-coordinate",
        params={"lng": 10.0, "lat": 10.0},
        headers=headers,
    )
    assert resp.status_code == 422
    assert "not georeferenced" in resp.json()["error"]["message"]
