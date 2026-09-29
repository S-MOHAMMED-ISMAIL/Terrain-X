"""P1-9: physical 3D terrain mesh export (GLB).

Pure tests of geospatial/mesh_export.py plus API tests of
GET .../visualization/terrain/mesh.glb, all validated with the INDEPENDENT
reader in tests/glb_reader.py. Coordinates are checked against each
raster's own transform (including an off-meridian UTM round-trip, the case
P1-6 fixed), heights against the raster/terrain-grid values, and NoData /
sky against the absence of any geometry.
"""

import asyncio
import io
import json
import uuid

import numpy as np
import pytest
import rasterio
from PIL import Image
from rasterio.crs import CRS
from rasterio.transform import from_origin
from rasterio.warp import transform as warp_transform

from app.core.config import get_settings
from app.core.storage import get_storage
from app.services.visualization import _apply_sky_mask
from geospatial.mesh_export import build_terrain_mesh, write_glb
from geospatial.terrain_grid import extract_terrain_grid
from tests.fixtures import make_structured_scene_geotiff_bytes
from tests.glb_reader import accessor, image_bytes, primitive, read_glb
from tests.test_reports import (
    _create_artifact_row,
    _create_job_row,
    _current_user_id,
    _register_and_login,
    _write_float_raster,
)

# The approved off-meridian placement (UTM 33N, ~60 N, 2.9 deg east of CM).
_UTM = "EPSG:32633"
_X0, _Y0, _PX, _N = 660000.0, 6660000.0, 40.0, 128
_TRANSFORM = from_origin(_X0, _Y0, _PX, _PX)


def _unique(h: int, w: int) -> np.ndarray:
    return (np.arange(h * w, dtype="float32").reshape(h, w) / 7.0 + 100.0).astype("float32")


def _triangles(glb):
    return accessor(glb, primitive(glb)["indices"]).reshape(-1, 3).astype(np.int64)


def _positions(glb):
    return accessor(glb, primitive(glb)["attributes"]["POSITION"])


def _extras(glb):
    return glb.gltf["asset"]["extras"]["terrainx"]


def _valid_quad_count(values: np.ndarray) -> int:
    v = np.isfinite(values)
    return int((v[:-1, :-1] & v[:-1, 1:] & v[1:, :-1] & v[1:, 1:]).sum())


def _components(tris: np.ndarray, n: int) -> int:
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for a, b, c in tris:
        for x, y in ((a, b), (b, c)):
            rx, ry = find(int(x)), find(int(y))
            if rx != ry:
                parent[rx] = ry
    return len({find(i) for i in range(n)})


# --------------------------------------------------------------------------
# Pure builder + writer
# --------------------------------------------------------------------------


def test_cell_centre_vertices_raw_values_and_counts():
    values = _unique(6, 7)
    values[2, 3] = np.nan
    mesh = build_terrain_mesh(values, step_x=40.0, step_z=40.0)
    glb = read_glb(write_glb(mesh, extras={"k": 1}))
    pos = _positions(glb)
    tris = _triangles(glb)
    assert tris.shape[0] == 2 * _valid_quad_count(values)
    assert pos.shape[0] == len(np.unique(tris))  # no unused vertices
    rows = np.rint(pos[:, 2] / 40.0).astype(int)
    cols = np.rint(pos[:, 0] / 40.0).astype(int)
    np.testing.assert_array_equal(pos[:, 0], cols * 40.0)
    np.testing.assert_array_equal(pos[:, 2], rows * 40.0)
    np.testing.assert_array_equal(pos[:, 1], values[rows, cols])  # raw, unchanged
    assert not ((rows == 2) & (cols == 3)).any()  # no vertex at the NoData cell
    # Every triangle faces up (+y), counter-clockwise.
    p = pos.astype(np.float64)
    n = np.cross(p[tris[:, 1]] - p[tris[:, 0]], p[tris[:, 2]] - p[tris[:, 0]])
    assert (n[:, 1] > 0).all()
    # Normals are unit length and computed from the physical geometry.
    normals = accessor(glb, primitive(glb)["attributes"]["NORMAL"])
    np.testing.assert_allclose(np.linalg.norm(normals, axis=1), 1.0, atol=1e-5)
    # UVs sit at each vertex's own cell centre.
    mesh_uv = mesh.uvs
    np.testing.assert_allclose(mesh_uv[:, 0], (mesh.grid_cols + 0.5) / 7)
    np.testing.assert_allclose(mesh_uv[:, 1], (mesh.grid_rows + 0.5) / 6)


def test_mirrored_axis_keeps_faces_up():
    mesh = build_terrain_mesh(_unique(4, 4), step_x=2.0, step_z=-2.0)
    p = mesh.positions.astype(np.float64)
    t = mesh.indices.reshape(-1, 3)
    n = np.cross(p[t[:, 1]] - p[t[:, 0]], p[t[:, 2]] - p[t[:, 0]])
    assert (n[:, 1] > 0).all()


def test_nodata_band_leaves_disconnected_regions_and_no_bridging():
    values = _unique(10, 11)
    values[:, 5] = np.nan  # a full NoData column splits the terrain in two
    mesh = build_terrain_mesh(values, step_x=1.0, step_z=1.0)
    tris = mesh.indices.reshape(-1, 3).astype(np.int64)
    assert _components(tris, mesh.vertex_count) == 2
    assert not (mesh.grid_cols == 5).any()
    # No triangle spans the hole.
    cols = mesh.grid_cols[tris]
    assert not ((cols.min(axis=1) < 5) & (cols.max(axis=1) > 5)).any()


def test_all_nodata_refused():
    with pytest.raises(ValueError, match="no valid surface"):
        build_terrain_mesh(np.full((5, 5), np.nan, dtype="float32"), step_x=1, step_z=1)


def test_glb_container_structure_and_index_width():
    small = read_glb(write_glb(build_terrain_mesh(_unique(8, 8), step_x=1, step_z=1), extras={}))
    assert (
        small.version == 2 and small.json_chunk_length % 4 == 0 and small.bin_chunk_length % 4 == 0
    )
    assert small.gltf["asset"]["version"] == "2.0"
    assert small.gltf["accessors"][primitive(small)["indices"]]["componentType"] == 5123
    pos_acc = small.gltf["accessors"][primitive(small)["attributes"]["POSITION"]]
    pos = _positions(small)
    assert pos_acc["min"] == [float(v) for v in pos.min(axis=0)]
    assert pos_acc["max"] == [float(v) for v in pos.max(axis=0)]
    for view in small.gltf["bufferViews"]:
        assert view["byteOffset"] % 4 == 0
    big = read_glb(write_glb(build_terrain_mesh(_unique(300, 300), step_x=1, step_z=1), extras={}))
    assert _positions(big).shape[0] == 90_000
    assert big.gltf["accessors"][primitive(big)["indices"]]["componentType"] == 5125


# --------------------------------------------------------------------------
# API: real artifact rows + rasters
# --------------------------------------------------------------------------


async def _project(client, email, *, source_bytes=None):
    headers = await _register_and_login(client, email)
    project_id = (
        await client.post("/api/v1/projects", json={"name": "Mesh"}, headers=headers)
    ).json()["id"]
    content = source_bytes or make_structured_scene_geotiff_bytes(
        width=_N, height=_N, crs=_UTM, origin_x=_X0, origin_y=_Y0, pixel_size=_PX
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
    return headers, project_id, content, job_id


async def _artifact(
    project_id, job_id, artifact_type, values, *, crs=_UTM, transform=_TRANSFORM, nodata=None
):
    key = f"projects/{project_id}/analysis/{job_id}/{artifact_type}-{uuid.uuid4().hex}.tif"
    size = _write_float_raster(key, values, crs=crs, transform=transform, nodata=nodata)
    artifact_id = await _create_artifact_row(job_id, artifact_type, key, size)
    base = f"/api/v1/projects/{project_id}/analysis/{job_id}/artifacts/{artifact_id}"
    return base, get_storage().absolute_path(key), str(artifact_id)


async def _export(client, headers, base, **params):
    return await client.get(
        f"{base}/visualization/terrain/mesh.glb", params=params, headers=headers
    )


async def test_offmeridian_dsm_round_trip_heights_holes_texture_and_metadata(client):
    headers, project_id, source_bytes, job_id = await _project(client, "mesh-dsm@example.com")
    values = _unique(_N, _N)
    values[:6, :6] = -9999.0
    base, path, artifact_id = await _artifact(project_id, job_id, "dsm", values, nodata=-9999.0)

    resp = await _export(client, headers, base, resolution=256)
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "model/gltf-binary"
    assert (
        f'filename="terrainx-terrain-{artifact_id}-256.glb"' in resp.headers["content-disposition"]
    )
    assert resp.headers["x-terrainx-texture"] == "embedded"
    glb = read_glb(resp.content)
    ex = _extras(glb)
    pos = _positions(glb)
    tris = _triangles(glb)

    # Metadata
    assert ex["height_kind"] == "elevation" and ex["physical_height"] is True
    assert ex["display_exaggeration_applied"] is False and ex["display_gamma_applied"] is False
    assert ex["local_crs"] == _UTM and CRS.from_wkt(ex["local_crs_wkt"]) == CRS.from_user_input(
        _UTM
    )
    assert ex["axis_mapping"] == {"map_x": "origin_x + x", "map_y": "origin_y - z", "value": "y"}
    assert ex["origin_x"] == _X0 + _PX / 2 and ex["origin_y"] == _Y0 - _PX / 2
    assert ex["grid_width"] == _N and ex["decimated"] is False and ex["reprojected"] is False
    assert ex["vertex_count"] == pos.shape[0] and ex["triangle_count"] == tris.shape[0]
    assert ex["artifact_id"] == artifact_id and ex["analysis_job_id"] == str(job_id)

    # Counts and NoData holes
    grid_values = np.where(values == -9999.0, np.nan, values)
    assert tris.shape[0] == 2 * _valid_quad_count(grid_values)

    # Off-meridian round trip: GLB local -> axis mapping -> local CRS ->
    # the raster's own cell centre, index and value.
    map_x = ex["origin_x"] + pos[:, 0].astype(np.float64)
    map_y = ex["origin_y"] - pos[:, 2].astype(np.float64)
    with rasterio.open(path) as src:
        band = src.read(1)
        rows, cols = rasterio.transform.rowcol(src.transform, map_x, map_y)
        rows, cols = np.asarray(rows), np.asarray(cols)
        cx, cy = rasterio.transform.xy(src.transform, rows, cols)  # cell centres
    np.testing.assert_allclose(map_x, cx, atol=1e-3)
    np.testing.assert_allclose(map_y, cy, atol=1e-3)
    np.testing.assert_array_equal(pos[:, 1], band[rows, cols])  # exact DSM values
    assert (band[rows, cols] != -9999.0).all()  # no vertex over NoData
    # ... and through WGS84 back to the same pixel via the backend resolver.
    for k in (0, pos.shape[0] // 3, pos.shape[0] - 1):
        (lng,), (lat,) = warp_transform(_UTM, "EPSG:4326", [map_x[k]], [map_y[k]])
        pixel = (
            await client.get(
                f"{base}/measurements/pixel",
                params={"x": lng, "y": lat, "crs": "EPSG:4326"},
                headers=headers,
            )
        ).json()
        assert (pixel["row"], pixel["col"]) == (int(rows[k]), int(cols[k]))

    # Texture: each vertex's UV samples exactly its own source pixel.
    image = np.array(Image.open(io.BytesIO(image_bytes(glb))).convert("RGB"))
    assert image.shape[:2] == (_N, _N)
    uv = accessor(glb, primitive(glb)["attributes"]["TEXCOORD_0"])
    with rasterio.io.MemoryFile(source_bytes) as memfile, memfile.open() as rgb_src:
        rgb = np.transpose(rgb_src.read([1, 2, 3]), (1, 2, 0))
    px = np.floor(uv[:, 0] * image.shape[1]).astype(int)
    py = np.floor(uv[:, 1] * image.shape[0]).astype(int)
    np.testing.assert_array_equal(px, cols)
    np.testing.assert_array_equal(py, rows)
    np.testing.assert_array_equal(image[py, px], rgb[rows, cols])

    # 512 builds the same (the source is only 128 px); deterministic bytes.
    resp512 = await _export(client, headers, base, resolution=512)
    assert _extras(read_glb(resp512.content))["resolution_requested"] == 512
    assert (await _export(client, headers, base, resolution=256)).content == resp.content


async def test_decimated_export_matches_the_authoritative_terrain_grid(client):
    headers, project_id, _, job_id = await _project(client, "mesh-decimated@example.com")
    values = _unique(600, 600)
    base, path, _ = await _artifact(
        project_id, job_id, "dsm", values, transform=from_origin(_X0, _Y0, 5.0, 5.0)
    )
    for resolution in (256, 512):
        glb = read_glb((await _export(client, headers, base, resolution=resolution)).content)
        ex = _extras(glb)
        grid = extract_terrain_grid(path, max_dimension=resolution)
        assert (
            (ex["grid_width"], ex["grid_height"])
            == (grid.width, grid.height)
            == (
                resolution,
                resolution,
            )
        )
        assert ex["decimated"] is True
        pos = _positions(glb)
        cols = np.rint(pos[:, 0] / ex["cell_size_x"]).astype(int)
        rows = np.rint(-pos[:, 2] / ex["cell_size_y"]).astype(int)
        np.testing.assert_array_equal(pos[:, 1], grid.elevations[rows, cols])
        np.testing.assert_allclose(
            ex["origin_x"] + pos[:, 0], grid.origin_x + (cols + 0.5) * grid.cell_size_x, atol=1e-3
        )


async def test_geographic_source_is_reprojected_and_untextured(client):
    headers, project_id, _, job_id = await _project(client, "mesh-geo@example.com")
    base, path, _ = await _artifact(
        project_id,
        job_id,
        "dsm",
        _unique(64, 64),
        crs="EPSG:4326",
        transform=from_origin(17.87, 60.045, 0.0008, 0.0004),
    )
    resp = await _export(client, headers, base)
    assert resp.headers["x-terrainx-texture"] == "omitted"
    ex = _extras(read_glb(resp.content))
    grid = extract_terrain_grid(path, max_dimension=256)
    assert ex["reprojected"] is True and ex["local_crs"] == grid.local_crs != "EPSG:4326"
    assert "reprojected" in ex["texture_omitted_reason"]
    assert "TEXCOORD_0" not in primitive(read_glb(resp.content))["attributes"]


async def test_relative_depth_is_raw_unitless_and_sky_masked(client):
    headers, project_id, _, job_id = await _project(client, "mesh-relative@example.com")
    depth = np.tile(np.linspace(0.1, 3.0, 96, dtype="float32")[:, None], (1, 80))
    depth[:4, :] = np.nan  # NoData rows at the top
    base, path, _ = await _artifact(
        project_id, job_id, "relative_depth", depth, crs=None, transform=None
    )
    glb = read_glb((await _export(client, headers, base, texture="false")).content)
    ex = _extras(glb)
    assert ex["height_kind"] == "relative_depth"
    assert ex["vertical_units"] == "unitless" and ex["physical_height"] is False
    assert ex["horizontal_vertical_units_comparable"] is False
    assert "Not a physical height" in ex["vertical_semantics"]
    text = json.dumps(ex).lower()
    assert "elevation" not in text and "metre" not in text and "meter" not in text
    assert ex["georeferenced"] is False and ex["local_crs"] is None
    assert ex["axis_mapping"] == {
        "pixel_col": "origin_x + x",
        "pixel_row": "origin_y + z",
        "value": "y",
    }
    assert ex["sky_mask_applied"] is True
    assert ex["texture_omitted_reason"] == "Texture not requested."
    # Heights are the raw (sky-masked) terrain-grid values, in source-pixel units.
    grid = _apply_sky_mask(extract_terrain_grid(path, max_dimension=256))
    pos = _positions(glb)
    cols = np.rint(pos[:, 0] / ex["cell_size_x"]).astype(int)
    rows = np.rint(pos[:, 2] / ex["cell_size_y"]).astype(int)
    np.testing.assert_array_equal(pos[:, 1], grid.elevations[rows, cols])
    assert np.isfinite(grid.elevations[rows, cols]).all()
    assert _triangles(glb).shape[0] == 2 * _valid_quad_count(grid.elevations)


async def test_limits_rejections_ownership_and_concurrency(client):
    headers, project_id, _, job_id = await _project(client, "mesh-limits@example.com")
    base, _, _ = await _artifact(project_id, job_id, "dsm", _unique(40, 40))
    for bad in (1024, 300, 128):
        resp = await _export(client, headers, base, resolution=bad)
        assert resp.status_code == 422
        assert "resolution must be one of [256, 512]" in resp.json()["error"]["message"]
    intruder = await _register_and_login(client, "mesh-intruder@example.com")
    assert (await _export(client, intruder, base)).status_code == 404
    slope_base, _, _ = await _artifact(project_id, job_id, "slope", _unique(40, 40))
    assert (await _export(client, headers, slope_base)).status_code == 422
    empty_base, _, _ = await _artifact(
        project_id, job_id, "dsm", np.full((20, 20), -9999.0, dtype="float32"), nodata=-9999.0
    )
    resp = await _export(client, headers, empty_base)
    assert resp.status_code == 422 and "no valid surface" in resp.json()["error"]["message"]
    # Concurrent exports beyond the limit wait their turn and all succeed identically.
    results = await asyncio.gather(*[_export(client, headers, base) for _ in range(5)])
    assert {r.status_code for r in results} == {200}
    assert len({r.content for r in results}) == 1
    assert get_settings().MESH_EXPORT_MAX_CONCURRENT == 2
