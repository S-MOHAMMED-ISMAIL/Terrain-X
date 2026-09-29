"""P1-6: 2D georegistration + backend-authoritative pixel resolution.

Pure tests of `geospatial.map_overlay` against rasters whose every pixel
holds a UNIQUE value, placed off the UTM central meridian (where the
pre-P1-6 axis-aligned WGS84 envelope stretch is wrong by several pixels):
every overlay pixel centre must show exactly the source pixel that the
raster's own transform puts at that location. Plus API tests for the
`map-preview` endpoints and `map_overlay_bounds` in the visualization
context. The overlay is presentation only; analytical values always come
from `coordinate_to_pixel` on the authoritative raster.
"""

import io
import math
import uuid

import numpy as np
import pytest
import rasterio
from PIL import Image
from rasterio.transform import from_origin
from rasterio.warp import calculate_default_transform, reproject
from rasterio.warp import transform as warp_transform

from app.core.config import get_settings
from app.core.storage import get_storage
from geospatial.exceptions import RasterValidationError
from geospatial.map_overlay import (
    WEB_MERCATOR,
    WGS84,
    generate_web_mercator_overlay_png,
    web_mercator_grid,
)
from geospatial.measurements import coordinate_to_pixel
from geospatial.raster_preview import _colorize_single_band, _region_color
from tests.fixtures import (
    make_structured_scene_geotiff_bytes,
    make_structured_scene_tiff_bytes_no_georef,
)
from tests.test_reports import (
    _create_artifact_row,
    _create_job_row,
    _current_user_id,
    _write_float_raster,
)

# The approved off-meridian placement: UTM 33N, ~60 N, ~2.9 deg east of the
# zone's central meridian (15 E). 128 x 128 px at 40 m.
_UTM = "EPSG:32633"
_X0, _Y0, _PX, _N = 660000.0, 6660000.0, 40.0, 128
_TRANSFORM = from_origin(_X0, _Y0, _PX, _PX)
_MAX_DIM = 1024


def _write(path, array, *, crs=_UTM, transform=_TRANSFORM, nodata=None, dtype=None):
    array = np.asarray(array)
    count = 1 if array.ndim == 2 else array.shape[0]
    height, width = array.shape[-2:]
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=width,
        height=height,
        count=count,
        dtype=dtype or array.dtype,
        crs=crs,
        transform=transform,
        nodata=nodata,
    ) as dst:
        dst.write(array if array.ndim == 3 else array[np.newaxis])
    return path


def _unique_values(n=_N) -> np.ndarray:
    return (np.arange(n * n, dtype="float64").reshape(n, n) + 1.0).astype("float32")


def _decode(png: bytes) -> np.ndarray:
    return np.array(Image.open(io.BytesIO(png)).convert("RGBA"))


def _overlay_centres_in_source(grid, raster_path):
    """For every overlay pixel centre: the source (row, col) it lies in (by
    the raster's own inverse transform, in its own CRS) and whether it is
    inside the raster."""
    rows, cols = np.meshgrid(np.arange(grid.height), np.arange(grid.width), indexing="ij")
    mx, my = grid.transform * (cols.ravel() + 0.5, rows.ravel() + 0.5)
    with rasterio.open(raster_path) as src:
        xs, ys = warp_transform(WEB_MERCATOR, src.crs, list(mx), list(my))
        inv = ~src.transform
        fc, fr = inv * (np.array(xs), np.array(ys))
        src_col, src_row = np.floor(fc).astype(int), np.floor(fr).astype(int)
        inside = (src_col >= 0) & (src_col < src.width) & (src_row >= 0) & (src_row < src.height)
    return src_row, src_col, inside


# --------------------------------------------------------------------------
# Pure overlay tests
# --------------------------------------------------------------------------


def test_old_linear_envelope_mapping_is_wrong_off_meridian_and_backend_resolution_is_exact(
    tmp_path,
):
    """Documents the original defect on the approved fixture placement: the
    pre-P1-6 MapView2D mapping (lng/lat linear inside the WGS84 envelope)
    picks the wrong source pixel for most true pixel locations, while
    coordinate_to_pixel returns the exact pixel for every one."""
    path = _write(tmp_path / "unique.tif", _unique_values())
    left, bottom = _X0, _Y0 - _N * _PX
    right, top = _X0 + _N * _PX, _Y0
    from rasterio.warp import transform_bounds

    minx, miny, maxx, maxy = transform_bounds(_UTM, WGS84, left, bottom, right, top)
    rows, cols = np.meshgrid(np.arange(0, _N, 4), np.arange(0, _N, 4), indexing="ij")
    rows, cols = rows.ravel(), cols.ravel()
    xs, ys = _TRANSFORM * (cols + 0.5, rows + 0.5)
    lons, lats = warp_transform(_UTM, WGS84, list(xs), list(ys))

    old_col = np.floor((np.array(lons) - minx) / (maxx - minx) * _N)
    old_row = np.floor((maxy - np.array(lats)) / (maxy - miny) * _N)
    old_error = np.hypot(old_col - cols, old_row - rows)
    assert old_error.max() >= 4.0
    assert np.mean(old_error > 0) > 0.9

    for lon, lat, row, col in zip(lons, lats, rows, cols, strict=True):
        pixel = coordinate_to_pixel(path, x=lon, y=lat, crs="EPSG:4326")
        assert pixel.in_bounds and (pixel.row, pixel.col) == (row, col)


def test_continuous_overlay_shows_exactly_the_source_pixel_at_every_overlay_centre(tmp_path):
    values = _unique_values()
    path = _write(tmp_path / "unique.tif", values)
    png, grid = generate_web_mercator_overlay_png(path, max_dimension=_MAX_DIM, categorical=False)
    rgba = _decode(png).reshape(-1, 4)
    src_row, src_col, inside = _overlay_centres_in_source(grid, path)

    assert rgba.shape[0] == grid.width * grid.height
    value_range = (float(values.min()), float(values.max()))
    expected = _colorize_single_band(
        values[src_row[inside], src_col[inside]].astype("float64")[np.newaxis, :],
        None,
        value_range,
    )[0]
    np.testing.assert_array_equal(rgba[inside], expected)
    assert inside.sum() > 0.9 * _N * _N
    # Outside the rotated footprint: fully transparent, never a colour.
    assert (rgba[~inside, 3] == 0).all()
    assert (~inside).sum() > 0


def test_categorical_ids_are_preserved_exactly(tmp_path):
    ids = np.arange(_N * _N, dtype="int32").reshape(_N, _N) + 1
    path = _write(tmp_path / "ids.tif", ids, nodata=0)
    png, grid = generate_web_mercator_overlay_png(path, max_dimension=_MAX_DIM, categorical=True)
    rgba = _decode(png).reshape(-1, 4)
    src_row, src_col, inside = _overlay_centres_in_source(grid, path)

    for i in np.flatnonzero(inside):
        assert tuple(rgba[i, :3]) == _region_color(int(ids[src_row[i], src_col[i]]))
        assert rgba[i, 3] == 255
    assert (rgba[~inside, 3] == 0).all()


def test_nodata_is_transparent(tmp_path):
    values = _unique_values()
    values[:40, :40] = -9999.0
    path = _write(tmp_path / "nodata.tif", values, nodata=-9999.0)
    png, grid = generate_web_mercator_overlay_png(path, max_dimension=_MAX_DIM, categorical=False)
    alpha = _decode(png).reshape(-1, 4)[:, 3]
    src_row, src_col, inside = _overlay_centres_in_source(grid, path)
    in_nodata = inside & (src_row < 40) & (src_col < 40)
    in_data = inside & ~((src_row < 40) & (src_col < 40))

    assert in_nodata.sum() > 0
    assert (alpha[in_nodata] == 0).all()
    assert (alpha[in_data] == 255).all()


def test_rgb_imagery_passes_through_with_footprint_alpha(tmp_path):
    rng = np.random.default_rng(0)
    rgb = rng.integers(0, 256, (3, _N, _N), dtype=np.uint8)
    path = _write(tmp_path / "rgb.tif", rgb)
    png, grid = generate_web_mercator_overlay_png(path, max_dimension=_MAX_DIM, categorical=False)
    rgba = _decode(png).reshape(-1, 4)
    src_row, src_col, inside = _overlay_centres_in_source(grid, path)

    expected = np.stack([rgb[b][src_row[inside], src_col[inside]] for b in range(3)], axis=1)
    np.testing.assert_array_equal(rgba[inside, :3], expected)
    assert (rgba[inside, 3] == 255).all() and (rgba[~inside, 3] == 0).all()


def test_map_overlay_bounds_are_the_exact_web_mercator_grid_corners(tmp_path):
    path = _write(tmp_path / "unique.tif", _unique_values())
    grid = web_mercator_grid(path, max_dimension=_MAX_DIM)
    west, south, east, north = grid.bounds_wgs84

    (mx0, mx1), (my0, my1) = warp_transform(WGS84, WEB_MERCATOR, [west, east], [south, north])
    assert mx0 == pytest.approx(grid.transform.c, abs=1e-6)
    assert my1 == pytest.approx(grid.transform.f, abs=1e-6)
    assert mx1 == pytest.approx(grid.transform.c + grid.width * grid.transform.a, abs=1e-6)
    assert my0 == pytest.approx(grid.transform.f + grid.height * grid.transform.e, abs=1e-6)
    # Every raster corner lies inside the overlay grid.
    corners_x = [_X0, _X0 + _N * _PX, _X0, _X0 + _N * _PX]
    corners_y = [_Y0, _Y0, _Y0 - _N * _PX, _Y0 - _N * _PX]
    lons, lats = warp_transform(_UTM, WGS84, corners_x, corners_y)
    assert all(west <= lon <= east for lon in lons)
    assert all(south <= lat <= north for lat in lats)
    # The PNG is rendered on the same grid the context reports.
    png, rendered = generate_web_mercator_overlay_png(
        path, max_dimension=_MAX_DIM, categorical=False
    )
    assert rendered == grid
    assert _decode(png).shape[:2] == (grid.height, grid.width)


def test_geographic_source_and_utm_derivative_each_use_their_own_grid(tmp_path):
    """A geographic (EPSG:4326) source and a UTM-reprojected derivative of
    it (how disaster rasters are stored for a geographic source) each get
    their own overlay grid, and each overlay is exact against its own
    raster — neither is stretched over the other's box."""
    geo_transform = from_origin(17.87, 60.045, 0.0008, 0.0004)
    geo_path = _write(
        tmp_path / "geo.tif", _unique_values(), crs="EPSG:4326", transform=geo_transform
    )
    with rasterio.open(geo_path) as src:
        dst_transform, width, height = calculate_default_transform(
            src.crs, _UTM, src.width, src.height, *src.bounds
        )
        derived = np.full((height, width), -9999.0, dtype="float32")
        reproject(
            source=src.read(1),
            destination=derived,
            src_transform=src.transform,
            src_crs=src.crs,
            dst_transform=dst_transform,
            dst_crs=_UTM,
            dst_nodata=-9999.0,
            resampling=rasterio.enums.Resampling.nearest,
        )
    utm_path = _write(
        tmp_path / "utm.tif", derived, crs=_UTM, transform=dst_transform, nodata=-9999.0
    )

    geo_grid = web_mercator_grid(geo_path, max_dimension=_MAX_DIM)
    utm_grid = web_mercator_grid(utm_path, max_dimension=_MAX_DIM)
    assert geo_grid.bounds_wgs84 != utm_grid.bounds_wgs84

    for path, nodata in ((geo_path, None), (utm_path, -9999.0)):
        png, grid = generate_web_mercator_overlay_png(
            path, max_dimension=_MAX_DIM, categorical=False
        )
        rgba = _decode(png).reshape(-1, 4)
        src_row, src_col, inside = _overlay_centres_in_source(grid, path)
        with rasterio.open(path) as src:
            band = src.read(1).astype("float64")
        valid = band[np.isfinite(band) & (band != -9999.0)]
        sampled = band[src_row[inside], src_col[inside]]
        expected = _colorize_single_band(
            sampled[np.newaxis, :], nodata, (float(valid.min()), float(valid.max()))
        )[0]
        np.testing.assert_array_equal(rgba[inside], expected)


def test_non_georeferenced_raster_has_no_map_overlay(tmp_path):
    path = tmp_path / "plain.tif"
    path.write_bytes(make_structured_scene_tiff_bytes_no_georef(32, 32))
    with pytest.raises(RasterValidationError, match="not georeferenced"):
        web_mercator_grid(path, max_dimension=_MAX_DIM)
    with pytest.raises(RasterValidationError, match="not georeferenced"):
        generate_web_mercator_overlay_png(path, max_dimension=_MAX_DIM, categorical=False)


def test_decimated_overlay_is_still_placed_on_the_raster_footprint(tmp_path):
    """A raster larger than PREVIEW_MAX_DIMENSION is read decimated
    (nearest), but its overlay still covers the same real footprint."""
    big = np.arange(300 * 300, dtype="float32").reshape(300, 300)
    path = _write(tmp_path / "big.tif", big)
    grid = web_mercator_grid(path, max_dimension=100)
    assert (grid.source_width, grid.source_height) == (100, 100)
    full = web_mercator_grid(path, max_dimension=_MAX_DIM)
    # Same origin (the footprint envelope's north-west corner) ...
    assert math.isclose(grid.bounds_wgs84[0], full.bounds_wgs84[0], abs_tol=1e-9)
    assert math.isclose(grid.bounds_wgs84[3], full.bounds_wgs84[3], abs_tol=1e-9)
    # ... and both cover the whole raster footprint.
    corners_x = [_X0, _X0 + 300 * _PX, _X0, _X0 + 300 * _PX]
    corners_y = [_Y0, _Y0, _Y0 - 300 * _PX, _Y0 - 300 * _PX]
    lons, lats = warp_transform(_UTM, WGS84, corners_x, corners_y)
    for west, south, east, north in (grid.bounds_wgs84, full.bounds_wgs84):
        assert all(west <= lon <= east for lon in lons)
        assert all(south <= lat <= north for lat in lats)


# --------------------------------------------------------------------------
# API: map-preview endpoints and map_overlay_bounds in the context
# --------------------------------------------------------------------------


async def _register(client, email: str) -> dict:
    await client.post("/api/v1/auth/register", json={"email": email, "password": "supersecret123"})
    resp = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": "supersecret123"}
    )
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


async def _project_with_source(client, headers, *, georeferenced=True) -> tuple[str, dict]:
    project_id = (
        await client.post("/api/v1/projects", json={"name": "Georeg"}, headers=headers)
    ).json()["id"]
    content = (
        make_structured_scene_geotiff_bytes(
            width=_N, height=_N, crs=_UTM, origin_x=_X0, origin_y=_Y0, pixel_size=_PX
        )
        if georeferenced
        else make_structured_scene_tiff_bytes_no_georef(32, 32)
    )
    dataset = (
        await client.post(
            f"/api/v1/projects/{project_id}/datasets",
            files={"file": ("scene.tif", content, "image/tiff")},
            headers=headers,
        )
    ).json()
    assert dataset["status"] == "valid", dataset
    return project_id, dataset


async def _dsm_job(client, headers, project_id, dataset) -> tuple[str, str]:
    user_id = await _current_user_id(client, headers)
    job_id = await _create_job_row(project_id, dataset["id"], user_id)
    key = f"projects/{project_id}/analysis/{job_id}/dsm.tif"
    size = _write_float_raster(key, _unique_values(), crs=_UTM, transform=_TRANSFORM)
    artifact_id = await _create_artifact_row(
        job_id,
        "dsm",
        key,
        size,
        {"width": _N, "height": _N, "crs": _UTM, "is_georeferenced": True},
    )
    return str(job_id), str(artifact_id)


async def test_context_reports_each_layers_own_overlay_bounds_and_previews_render(client):
    headers = await _register(client, "georeg-ctx@example.com")
    project_id, dataset = await _project_with_source(client, headers)
    job_id, artifact_id = await _dsm_job(client, headers, project_id, dataset)

    context = (
        await client.get(
            f"/api/v1/projects/{project_id}/datasets/{dataset['id']}/visualization/context",
            headers=headers,
        )
    ).json()
    layers = {layer["layer_type"]: layer for layer in context["layers"]}
    settings = get_settings()
    storage = get_storage()

    dsm_path = storage.absolute_path(f"projects/{project_id}/analysis/{job_id}/dsm.tif")
    expected = web_mercator_grid(dsm_path, max_dimension=settings.PREVIEW_MAX_DIMENSION)
    west, south, east, north = expected.bounds_wgs84
    assert layers["dsm"]["map_overlay_bounds"] == {
        "min_x": west,
        "min_y": south,
        "max_x": east,
        "max_y": north,
    }
    assert layers["rgb"]["map_overlay_bounds"] is not None
    # Unavailable layers have no overlay.
    assert layers["slope"]["map_overlay_bounds"] is None

    base = f"/api/v1/projects/{project_id}"
    artifact_png = await client.get(
        f"{base}/analysis/{job_id}/artifacts/{artifact_id}/visualization/map-preview",
        headers=headers,
    )
    assert artifact_png.status_code == 200
    assert artifact_png.headers["content-type"] == "image/png"
    served = _decode(artifact_png.content)
    png, _ = generate_web_mercator_overlay_png(
        dsm_path, max_dimension=settings.PREVIEW_MAX_DIMENSION, categorical=False
    )
    np.testing.assert_array_equal(served, _decode(png))

    dataset_png = await client.get(
        f"{base}/datasets/{dataset['id']}/visualization/map-preview", headers=headers
    )
    assert dataset_png.status_code == 200
    assert _decode(dataset_png.content).shape[2] == 4

    # The authoritative-source preview used by the 3D texture is unchanged.
    source_preview = await client.get(
        f"{base}/datasets/{dataset['id']}/visualization/preview", headers=headers
    )
    assert source_preview.status_code == 200
    assert Image.open(io.BytesIO(source_preview.content)).size == (_N, _N)


async def test_map_preview_ownership_and_rejections(client):
    headers = await _register(client, "georeg-owner@example.com")
    project_id, dataset = await _project_with_source(client, headers)
    job_id, artifact_id = await _dsm_job(client, headers, project_id, dataset)
    base = f"/api/v1/projects/{project_id}"
    intruder = await _register(client, "georeg-intruder@example.com")

    for path in (
        f"{base}/analysis/{job_id}/artifacts/{artifact_id}/visualization/map-preview",
        f"{base}/datasets/{dataset['id']}/visualization/map-preview",
    ):
        assert (await client.get(path, headers=intruder)).status_code == 404

    # A P1-5 residual (GeoJSON) artifact is not a raster.
    key = f"projects/{project_id}/analysis/{job_id}/calibration_residuals.geojson"
    storage = get_storage()
    storage.absolute_path(key).write_text('{"type": "FeatureCollection", "features": []}')
    residual_id = await _create_artifact_row(uuid.UUID(job_id), "calibration_residuals", key, 10)
    resp = await client.get(
        f"{base}/analysis/{job_id}/artifacts/{residual_id}/visualization/map-preview",
        headers=headers,
    )
    assert resp.status_code == 422


async def test_non_georeferenced_dataset_has_no_overlay_bounds_and_no_map_preview(client):
    headers = await _register(client, "georeg-plain@example.com")
    project_id, dataset = await _project_with_source(client, headers, georeferenced=False)
    context = (
        await client.get(
            f"/api/v1/projects/{project_id}/datasets/{dataset['id']}/visualization/context",
            headers=headers,
        )
    ).json()
    rgb = next(layer for layer in context["layers"] if layer["layer_type"] == "rgb")
    assert rgb["available"] is True and rgb["map_overlay_bounds"] is None

    resp = await client.get(
        f"/api/v1/projects/{project_id}/datasets/{dataset['id']}/visualization/map-preview",
        headers=headers,
    )
    assert resp.status_code == 422
    assert "not georeferenced" in resp.json()["error"]["message"]
    # The existing local-pixel preview keeps working.
    plain = await client.get(
        f"/api/v1/projects/{project_id}/datasets/{dataset['id']}/visualization/preview",
        headers=headers,
    )
    assert plain.status_code == 200
