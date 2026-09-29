"""D2: RGB texture is draped on the 3D terrain ONLY when the terrain grid's
pixels are the source image's pixels — decided from raster provenance by the
single rule `geospatial.terrain_grid.rgb_texture_compatibility`, shared by the
browser 3D view (visualization context) and the GLB export.

The key case: a geographic (EPSG:4326) source keeps IDENTICAL array
dimensions in its terrain artifact, but `extract_terrain_grid` reprojects the
grid to UTM, so dimension equality alone must not enable the texture.
"""

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from geospatial.terrain_grid import (
    extract_terrain_grid,
    grid_is_reprojected,
    rgb_texture_compatibility,
)
from tests.fixtures import make_structured_scene_geotiff_bytes
from tests.glb_reader import primitive, read_glb
from tests.test_reports import (
    _create_artifact_row,
    _create_job_row,
    _current_user_id,
    _register_and_login,
    _write_float_raster,
)

N = 32
UTM = "EPSG:32633"
UTM_T = from_origin(500000.0, 4649984.0, 2.0, 2.0)
GEO = "EPSG:4326"
GEO_T = from_origin(17.87, 60.045, 0.0008, 0.0004)


def _raster(path, *, count=3, dtype="uint8", crs=UTM, transform=UTM_T, width=N, height=N):
    profile = {"driver": "GTiff", "width": width, "height": height, "count": count, "dtype": dtype}
    if crs is not None:
        profile.update(crs=crs, transform=transform)
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(np.ones((count, height, width), dtype=dtype))
    return path


def _terrain(path, **kw):
    return _raster(path, count=1, dtype="float32", **kw)


# --------------------------------------------------------------------------
# The contract (pure)
# --------------------------------------------------------------------------


def test_1_projected_source_with_exact_correspondence_is_compatible(tmp_path):
    c = rgb_texture_compatibility(_raster(tmp_path / "s.tif"), _terrain(tmp_path / "t.tif"))
    assert (c.compatible, c.code, c.reason) == (True, None, None)


def test_2_geographic_source_reprojected_grid_is_incompatible_despite_equal_dims(tmp_path):
    src = _raster(tmp_path / "s.tif", crs=GEO, transform=GEO_T)
    ter = _terrain(tmp_path / "t.tif", crs=GEO, transform=GEO_T)
    # Identical dimensions, CRS and transform between source and terrain
    # raster — yet the 3D grid IS reprojected, so its cells are not pixels.
    grid = extract_terrain_grid(ter, max_dimension=256)
    assert grid.source_width == N and grid.source_height == N
    assert grid.local_crs != grid.crs == GEO
    c = rgb_texture_compatibility(src, ter)
    assert c.compatible is False and c.code == "reprojected_terrain_grid"
    assert "reprojected" in c.reason


def test_grid_is_reprojected_is_what_extract_terrain_grid_does(tmp_path):
    for i, (crs, transform) in enumerate([(GEO, GEO_T), (UTM, UTM_T), (None, None)]):
        path = _terrain(tmp_path / f"t{i}.tif", crs=crs, transform=transform)
        grid = extract_terrain_grid(path, max_dimension=256)
        reprojected = grid.is_georeferenced and grid.local_crs != grid.crs
        with rasterio.open(path) as ds:
            assert grid_is_reprojected(ds.crs) is reprojected


def test_3_same_dimensions_different_transform_is_incompatible(tmp_path):
    # Geographic: reprojection is decisive whatever the transforms.
    geo = rgb_texture_compatibility(
        _raster(tmp_path / "gs.tif", crs=GEO, transform=GEO_T),
        _terrain(tmp_path / "gt.tif", crs=GEO, transform=from_origin(17.9, 60.0, 0.0008, 0.0004)),
    )
    assert geo.code == "reprojected_terrain_grid"
    # Projected, same CRS and dims, shifted grid: pixels do not correspond.
    shifted = rgb_texture_compatibility(
        _raster(tmp_path / "ps.tif"),
        _terrain(tmp_path / "pt.tif", transform=from_origin(500001.0, 4649984.0, 2.0, 2.0)),
    )
    assert (shifted.compatible, shifted.code) == (False, "transform_mismatch")


def test_4_crs_mismatch_is_incompatible(tmp_path):
    c = rgb_texture_compatibility(
        _raster(tmp_path / "s.tif"), _terrain(tmp_path / "t.tif", crs="EPSG:32634")
    )
    assert (c.compatible, c.code) == (False, "crs_mismatch")
    only_one = rgb_texture_compatibility(
        _raster(tmp_path / "s2.tif", crs=None), _terrain(tmp_path / "t2.tif")
    )
    assert (only_one.compatible, only_one.code) == (False, "georeferencing_mismatch")


def test_5_dimension_mismatch_is_incompatible(tmp_path):
    c = rgb_texture_compatibility(
        _raster(tmp_path / "s.tif", width=N + 1), _terrain(tmp_path / "t.tif")
    )
    assert (c.compatible, c.code) == (False, "dimension_mismatch")


@pytest.mark.parametrize("dtype", ["uint16", "float32"])
def test_6_rgb_must_be_uint8(tmp_path, dtype):
    c = rgb_texture_compatibility(
        _raster(tmp_path / "s.tif", dtype=dtype), _terrain(tmp_path / "t.tif")
    )
    assert (c.compatible, c.code) == (False, "not_rgb_uint8")


@pytest.mark.parametrize("count", [1, 2])
def test_7_non_rgb_source_is_incompatible(tmp_path, count):
    c = rgb_texture_compatibility(
        _raster(tmp_path / "s.tif", count=count), _terrain(tmp_path / "t.tif")
    )
    assert (c.compatible, c.code) == (False, "not_rgb_uint8")


def test_no_source_and_unreadable_source(tmp_path):
    ter = _terrain(tmp_path / "t.tif")
    assert rgb_texture_compatibility(None, ter).code == "no_source_image"
    (tmp_path / "bad.tif").write_bytes(b"not a raster")
    assert rgb_texture_compatibility(tmp_path / "bad.tif", ter).code == "source_unreadable"


def test_non_georeferenced_behaviour_unchanged(tmp_path):
    # A non-georeferenced RGB and its same-grid artifact stay compatible
    # (terrain pixel (r, c) is source pixel (r, c)), as before D2.
    c = rgb_texture_compatibility(
        _raster(tmp_path / "s.tif", crs=None), _terrain(tmp_path / "t.tif", crs=None)
    )
    assert c.compatible is True


# --------------------------------------------------------------------------
# API: the browser's context and the GLB export make the same decision
# --------------------------------------------------------------------------


async def _job_with_terrain(client, email, *, crs, origin_x, origin_y, pixel_size):
    """A real uploaded RGB dataset and a DSM artifact written, as the
    pipeline does, with the source's own CRS/transform and dimensions."""
    headers = await _register_and_login(client, email)
    project_id = (
        await client.post("/api/v1/projects", json={"name": "D2"}, headers=headers)
    ).json()["id"]
    content = make_structured_scene_geotiff_bytes(
        width=N, height=N, crs=crs, origin_x=origin_x, origin_y=origin_y, pixel_size=pixel_size
    )
    dataset = (
        await client.post(
            f"/api/v1/projects/{project_id}/datasets",
            files={"file": ("scene.tif", content, "image/tiff")},
            headers=headers,
        )
    ).json()
    user_id = await _current_user_id(client, headers)
    job_id = await _create_job_row(project_id, dataset["id"], user_id)
    key = f"projects/{project_id}/analysis/{job_id}/dsm.tif"
    values = np.linspace(100, 140, N * N, dtype="float32").reshape(N, N)
    transform = from_origin(origin_x, origin_y, pixel_size, pixel_size)
    size = _write_float_raster(key, values, crs=crs, transform=transform)
    artifact_id = await _create_artifact_row(
        job_id, "dsm", key, size, metadata={"width": N, "height": N, "crs": crs}
    )
    context = (
        await client.get(
            f"/api/v1/projects/{project_id}/datasets/{dataset['id']}/visualization/context",
            headers=headers,
        )
    ).json()
    base = f"/api/v1/projects/{project_id}/analysis/{job_id}/artifacts/{artifact_id}"
    glb = await client.get(f"{base}/visualization/terrain/mesh.glb", headers=headers)
    return context, glb


async def test_1_api_projected_source_texture_enabled_in_context_and_glb(client):
    context, glb = await _job_with_terrain(
        client,
        "d2-projected@example.com",
        crs=UTM,
        origin_x=500000.0,
        origin_y=4649984.0,
        pixel_size=2.0,
    )
    terrain = context["terrain"]
    assert terrain["available"] is True
    assert terrain["texture_compatible"] is True
    assert terrain["texture_unavailable_code"] is None
    assert terrain["texture_unavailable_reason"] is None
    assert glb.headers["x-terrainx-texture"] == "embedded"
    assert "TEXCOORD_0" in primitive(read_glb(glb.content))["attributes"]


async def test_2_api_geographic_source_texture_disabled_although_dims_match(client):
    context, glb = await _job_with_terrain(
        client,
        "d2-geographic@example.com",
        crs=GEO,
        origin_x=17.87,
        origin_y=60.045,
        pixel_size=0.0005,
    )
    terrain = context["terrain"]
    rgb = next(layer for layer in context["layers"] if layer["layer_type"] == "rgb")
    # The pre-D2 browser rule (equal dimensions) would have enabled it.
    assert (rgb["width"], rgb["height"]) == (terrain["width"], terrain["height"]) == (N, N)
    assert terrain["texture_compatible"] is False
    assert terrain["texture_unavailable_code"] == "reprojected_terrain_grid"
    assert "reprojected" in terrain["texture_unavailable_reason"]
    # 8: the GLB export makes the same decision, with the same reason.
    assert glb.headers["x-terrainx-texture"] == "omitted"
    extras = read_glb(glb.content).gltf["asset"]["extras"]["terrainx"]
    assert extras["texture_omitted_reason"] == terrain["texture_unavailable_reason"]
    assert "TEXCOORD_0" not in primitive(read_glb(glb.content))["attributes"]
