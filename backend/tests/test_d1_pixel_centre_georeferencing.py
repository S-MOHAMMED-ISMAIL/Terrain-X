"""D1: one raster -> world convention for the 3D terrain — pixel centres.

Grid cell (row, col) of the terrain grid is represented at the CENTRE of that
cell, map = transform * (col + 0.5, row + 0.5). The 3D view's local frame is
local = map - (origin_x, origin_y) (the transform's origin corner), so the
mesh vertex of cell (row, col) is local ((col + 0.5) * cell_size_x,
(row + 0.5) * cell_size_y) — see frontend/src/components/terrain/
terrainMesh.ts and terrainD1.test.ts (the frontend half, built from the SAME
fixture constants as below).

Fixture: an asymmetric 13 x 9 grid, non-square cells (3 m x 2 m), an origin
aligned to nothing (500123.37, 4649987.61), and a single distinctive value
(777) at the off-centre pixel (row 6, col 9). Small enough that the terrain
grid is NOT decimated, so grid cell == source pixel. Every expectation is
computed independently with rasterio's own `xy(..., offset="center")` /
pyproj, never with the code under test.

The OLD (corner) convention put cell (row, col)'s vertex at local
(col * cx, row * cy), i.e. map = its corner, which resolves to a different
pixel than the one whose value the vertex carries.
"""

import math

import numpy as np
import pytest
import rasterio
from rasterio.transform import Affine, rowcol
from rasterio.transform import xy as transform_xy
from rasterio.warp import transform as warp_transform

from app.core.config import get_settings
from geospatial.measurements import coordinate_to_pixel
from geospatial.terrain_grid import extract_terrain_grid
from tests.glb_reader import accessor, primitive, read_glb
from tests.test_reports import (
    _create_artifact_row,
    _create_job_row,
    _current_user_id,
    _register_and_login,
    _upload_source_image,
    _write_float_raster,
)

# --- The D1 fixture (mirrored exactly in terrainD1.test.ts) ----------------
W, H = 13, 9
UTM = "EPSG:32633"
X0, Y0 = 500123.37, 4649987.61
CX, CY = 3.0, -2.0  # non-square, north-up
SPIKE_ROW, SPIKE_COL, SPIKE_VALUE = 6, 9, 777.0
PROJECTED = Affine(CX, 0.0, X0, 0.0, CY, Y0)
GEO = "EPSG:4326"
# Geographic source: non-square degree cells, unaligned origin.
GEOGRAPHIC = Affine(0.00031, 0.0, 17.8713, 0.0, -0.00017, 60.0457)


def fixture_values() -> np.ndarray:
    rows, cols = np.mgrid[0:H, 0:W]
    values = (100.0 + 0.25 * cols - 0.5 * rows).astype("float32")
    values[SPIKE_ROW, SPIKE_COL] = SPIKE_VALUE
    return values


def centre(transform: Affine, row: float, col: float) -> tuple[float, float]:
    """Independent pixel centre (rasterio's own offset='center')."""
    x, y = transform_xy(transform, row, col, offset="center")
    return float(x), float(y)


def mesh_vertex_local(grid, row: int, col: int) -> tuple[float, float]:
    """terrainMesh.ts's vertex placement (D1), in its local frame."""
    return (col + 0.5) * grid.cell_size_x, (row + 0.5) * grid.cell_size_y


def old_mesh_vertex_local(grid, row: int, col: int) -> tuple[float, float]:
    """The pre-D1 corner placement, kept only to prove the fixture detects it."""
    return col * grid.cell_size_x, row * grid.cell_size_y


def _write(path, crs, transform):
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=H,
        width=W,
        count=1,
        dtype="float32",
        crs=crs,
        transform=transform,
    ) as dst:
        dst.write(fixture_values(), 1)
    return path


@pytest.fixture
def projected_path(tmp_path):
    return _write(tmp_path / "d1-projected.tif", UTM, PROJECTED)


@pytest.fixture
def geographic_path(tmp_path):
    return _write(tmp_path / "d1-geographic.tif", GEO, GEOGRAPHIC)


def _grid(path):
    return extract_terrain_grid(path, max_dimension=get_settings().MAX_TERRAIN_DIMENSION)


# --------------------------------------------------------------------------
# Pure: grid transform, mesh vertex, backend coordinate_to_pixel
# --------------------------------------------------------------------------


def test_fixture_is_not_decimated_and_keeps_the_real_transform(projected_path):
    grid = _grid(projected_path)
    assert (grid.width, grid.height) == (W, H)
    assert (grid.origin_x, grid.origin_y) == (X0, Y0)
    assert (grid.cell_size_x, grid.cell_size_y) == (CX, CY)
    assert grid.local_crs == UTM
    assert grid.elevations[SPIKE_ROW, SPIKE_COL] == SPIKE_VALUE


def test_1_every_mesh_vertex_is_its_pixel_centre_world_coordinate(projected_path):
    grid = _grid(projected_path)
    for row in range(H):
        for col in range(W):
            lx, lz = mesh_vertex_local(grid, row, col)
            assert (grid.origin_x + lx, grid.origin_y + lz) == pytest.approx(
                centre(PROJECTED, row, col), abs=1e-6
            )


def test_2_first_pixel_maps_to_its_centre_not_the_raster_corner(projected_path):
    grid = _grid(projected_path)
    lx, lz = mesh_vertex_local(grid, 0, 0)
    assert (grid.origin_x + lx, grid.origin_y + lz) == pytest.approx((X0 + 1.5, Y0 - 1.0), abs=1e-9)
    assert (grid.origin_x + lx, grid.origin_y + lz) != pytest.approx((X0, Y0), abs=0.5)


def test_3_interior_off_centre_pixel_maps_exactly(projected_path):
    grid = _grid(projected_path)
    lx, lz = mesh_vertex_local(grid, SPIKE_ROW, SPIKE_COL)
    expected = (X0 + (SPIKE_COL + 0.5) * CX, Y0 + (SPIKE_ROW + 0.5) * CY)
    assert (grid.origin_x + lx, grid.origin_y + lz) == pytest.approx(expected, abs=1e-9)
    assert expected == pytest.approx(centre(PROJECTED, SPIKE_ROW, SPIKE_COL), abs=1e-9)


def test_4_last_pixel_centre_is_inside_the_raster_footprint(projected_path):
    grid = _grid(projected_path)
    lx, lz = mesh_vertex_local(grid, H - 1, W - 1)
    x, y = grid.origin_x + lx, grid.origin_y + lz
    left, bottom, right, top = grid.bounds
    assert left < x < right and bottom < y < top
    assert (right - x, y - bottom) == pytest.approx((CX / 2, -CY / 2), abs=1e-9)


@pytest.mark.parametrize("which", ["projected", "geographic"])
def test_7_mesh_vertex_round_trips_through_backend_coordinate_to_pixel(
    which, projected_path, geographic_path
):
    path, src_transform, src_crs = (
        (projected_path, PROJECTED, UTM)
        if which == "projected"
        else (geographic_path, GEOGRAPHIC, GEO)
    )
    grid = _grid(path)
    for row in range(grid.height):
        for col in range(grid.width):
            lx, lz = mesh_vertex_local(grid, row, col)
            x, y = grid.origin_x + lx, grid.origin_y + lz
            pixel = coordinate_to_pixel(path, x=x, y=y, crs=grid.local_crs)
            # Independent: reproject the centre to the source CRS (pyproj) and
            # take the source pixel containing it.
            (sx,), (sy,) = warp_transform(grid.local_crs, src_crs, [x], [y])
            fcol, frow = ~src_transform * (sx, sy)
            inside = 0 <= fcol < W and 0 <= frow < H
            assert pixel.in_bounds is inside
            if inside:
                assert (pixel.row, pixel.col) == (math.floor(frow), math.floor(fcol))
    if which == "projected":
        # Not reprojected: the vertex of cell (r, c) resolves to pixel (r, c).
        lx, lz = mesh_vertex_local(grid, SPIKE_ROW, SPIKE_COL)
        pixel = coordinate_to_pixel(path, x=X0 + lx, y=Y0 + lz, crs=UTM)
        assert (pixel.row, pixel.col) == (SPIKE_ROW, SPIKE_COL)
        assert fixture_values()[pixel.row, pixel.col] == SPIKE_VALUE


def test_11_geographic_source_grid_centres_are_its_utm_transform_centres(geographic_path):
    grid = _grid(geographic_path)
    assert grid.local_crs != GEO and grid.local_crs.startswith("EPSG:326")
    dst = Affine(grid.cell_size_x, 0.0, grid.origin_x, 0.0, grid.cell_size_y, grid.origin_y)
    for row, col in [
        (0, 0),
        (grid.height // 2, grid.width // 3),
        (grid.height - 1, grid.width - 1),
    ]:
        lx, lz = mesh_vertex_local(grid, row, col)
        assert (grid.origin_x + lx, grid.origin_y + lz) == pytest.approx(
            centre(dst, row, col), abs=1e-6
        )


def test_13_non_square_cells_use_each_axis_own_size(projected_path):
    grid = _grid(projected_path)
    assert abs(grid.cell_size_x) != abs(grid.cell_size_y)
    lx, lz = mesh_vertex_local(grid, 1, 1)
    assert (lx, lz) == (1.5 * CX, 1.5 * CY) == (4.5, -3.0)


def test_fixture_detects_the_old_corner_convention(projected_path):
    """The old corner vertex of the spike cell resolves to a DIFFERENT map
    point (0.5 cell NW), off the spike's centre, and for a negative cell
    size onto the neighbouring row — so any test above fails on it."""
    grid = _grid(projected_path)
    lx, lz = old_mesh_vertex_local(grid, SPIKE_ROW, SPIKE_COL)
    old = (X0 + lx, Y0 + lz)
    assert old != pytest.approx(centre(PROJECTED, SPIKE_ROW, SPIKE_COL), abs=0.5)
    assert rowcol(PROJECTED, *old) == (SPIKE_ROW, SPIKE_COL)  # on the corner: floor rule
    nudged = coordinate_to_pixel(projected_path, x=old[0] - 1e-6, y=old[1] + 1e-6, crs=UTM)
    assert (nudged.row, nudged.col) == (SPIKE_ROW - 1, SPIKE_COL - 1)


# --------------------------------------------------------------------------
# API: the acceptance chain against real endpoints and a real stored raster
# --------------------------------------------------------------------------


async def _api_fixture(client, email, *, crs=UTM, transform=PROJECTED):
    headers = await _register_and_login(client, email)
    project_id = (
        await client.post("/api/v1/projects", json={"name": "D1"}, headers=headers)
    ).json()["id"]
    dataset = await _upload_source_image(client, headers, project_id)
    user_id = await _current_user_id(client, headers)
    job_id = await _create_job_row(project_id, dataset["id"], user_id)
    key = f"projects/{project_id}/analysis/{job_id}/dsm.tif"
    size = _write_float_raster(key, fixture_values(), crs=crs, transform=transform)
    artifact_id = await _create_artifact_row(job_id, "dsm", key, size)
    base = f"/api/v1/projects/{project_id}/analysis/{job_id}/artifacts/{artifact_id}"
    return headers, base


async def test_acceptance_2d_backend_3d_glb_waypoint_agree_on_the_spike_pixel(client):
    """Known pixel (6, 9) = 777, its centre computed independently. Then:
    2D map click (lng/lat -> backend pixel) == 3D mesh vertex (metadata frame)
    == GLB vertex == 2D waypoint (local-coordinate endpoint) == the centre."""
    headers, base = await _api_fixture(client, "d1-acceptance@example.com")
    expected = centre(PROJECTED, SPIKE_ROW, SPIKE_COL)
    assert expected == pytest.approx((500123.37 + 28.5, 4649987.61 - 13.0), abs=1e-9)

    # 3D mesh vertex of the spike, from the app's own terrain metadata.
    meta = (await client.get(f"{base}/visualization/terrain/metadata", headers=headers)).json()
    assert (meta["width"], meta["height"]) == (W, H)
    assert (meta["cell_size_x"], meta["cell_size_y"]) == (CX, CY)
    mesh_map = (
        meta["origin_x"] + (SPIKE_COL + 0.5) * meta["cell_size_x"],
        meta["origin_y"] + (SPIKE_ROW + 0.5) * meta["cell_size_y"],
    )
    assert mesh_map == pytest.approx(expected, abs=1e-9)
    grid = np.frombuffer(
        (await client.get(f"{base}/visualization/terrain/grid", headers=headers)).content,
        dtype="<f4",
    ).reshape(H, W)
    assert grid[SPIKE_ROW, SPIKE_COL] == SPIKE_VALUE  # the vertex carries the spike

    # 3D click at that vertex -> backend pixel resolution (TerrainView3D path).
    pixel3d = (
        await client.get(
            f"{base}/measurements/pixel",
            params={"x": mesh_map[0], "y": mesh_map[1], "crs": meta["local_crs"]},
            headers=headers,
        )
    ).json()
    assert (pixel3d["row"], pixel3d["col"], pixel3d["in_bounds"]) == (SPIKE_ROW, SPIKE_COL, True)

    # 2D map click at the same place (WGS84, as Leaflet reports it).
    (lng,), (lat,) = warp_transform(UTM, "EPSG:4326", [expected[0]], [expected[1]])
    pixel2d = (
        await client.get(
            f"{base}/measurements/pixel",
            params={"x": lng, "y": lat, "crs": "EPSG:4326"},
            headers=headers,
        )
    ).json()
    assert (pixel2d["row"], pixel2d["col"]) == (SPIKE_ROW, SPIKE_COL)

    # 2D map click -> flythrough waypoint (P1-8 local-coordinate endpoint):
    # local = map - origin lands exactly on the spike's mesh vertex.
    local = (
        await client.get(
            f"{base}/visualization/terrain/local-coordinate",
            params={"lng": lng, "lat": lat},
            headers=headers,
        )
    ).json()
    assert local["in_footprint"] is True
    assert (local["map_x"], local["map_y"]) == pytest.approx(expected, abs=1e-6)
    assert (local["map_x"] - meta["origin_x"], local["map_y"] - meta["origin_y"]) == pytest.approx(
        ((SPIKE_COL + 0.5) * CX, (SPIKE_ROW + 0.5) * CY), abs=1e-6
    )

    # GLB: the vertex carrying 777 is at the same map coordinate.
    glb = read_glb(
        (
            await client.get(
                f"{base}/visualization/terrain/mesh.glb",
                params={"resolution": 256},
                headers=headers,
            )
        ).content
    )
    ex = glb.gltf["asset"]["extras"]["terrainx"]
    pos = accessor(glb, primitive(glb)["attributes"]["POSITION"]).astype(np.float64)
    spike = np.flatnonzero(pos[:, 1] == SPIKE_VALUE)
    assert spike.size == 1
    glb_map = (ex["origin_x"] + pos[spike[0], 0], ex["origin_y"] - pos[spike[0], 2])
    assert glb_map == pytest.approx(expected, abs=1e-6)
    assert glb_map == pytest.approx(mesh_map, abs=1e-6)  # rendered == GLB (up to origin)


async def test_4_last_pixel_centre_is_on_the_mesh_and_its_outer_rim_is_not(client):
    headers, base = await _api_fixture(client, "d1-footprint@example.com")

    async def in_footprint(x, y):
        (lng,), (lat,) = warp_transform(UTM, "EPSG:4326", [x], [y])
        return (
            await client.get(
                f"{base}/visualization/terrain/local-coordinate",
                params={"lng": lng, "lat": lat},
                headers=headers,
            )
        ).json()["in_footprint"]

    assert await in_footprint(*centre(PROJECTED, H - 1, W - 1)) is True
    assert await in_footprint(*centre(PROJECTED, 0, 0)) is True
    # Beyond the last centre (outer half of the last pixel): no mesh surface.
    x, y = centre(PROJECTED, H - 1, W - 1)
    assert await in_footprint(x + 0.4 * CX, y) is False
    assert await in_footprint(x, y + 0.4 * CY) is False
