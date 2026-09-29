"""P1-9: physical 3D terrain mesh export (binary glTF 2.0, `.glb`).

Builds a triangle mesh directly from the AUTHORITATIVE terrain grid (the
same `extract_terrain_grid` output the 3D view is built from) and writes it
as a self-contained GLB. This is a physical / unitless terrain-grid export,
deliberately a DIFFERENT representation from the browser mesh
(`frontend/src/components/terrain/terrainMesh.ts`), which is a display and
flythrough representation:

- vertices sit at grid CELL CENTRES (correct georeferencing), not at cell
  corners as in the display mesh;
- the vertical coordinate is the RAW grid value — no baseline, no display
  exaggeration, no relative-depth gamma, no display centring;
- a quad is triangulated only when all four corners are finite, split along
  the same (r, c+1)-(r+1, c) diagonal as the display mesh; vertices used by
  no triangle are dropped, so no geometry of any kind exists over NoData or
  sky, and disconnected regions stay disconnected.

Axes (glTF is Y-up, right-handed; front faces counter-clockwise):
    x = column offset * horizontal cell size      (east / image right)
    y = raw grid value                             (up)
    z = row offset * horizontal cell size, positive toward the image bottom
        (south for a north-up raster), i.e. north is -z.
The large map origin is carried in float64 in `asset.extras` (glTF
positions are float32), with the exact axis mapping back to map/pixel
coordinates.

Independent of the backend layer (pure numpy + struct + json); writes no
files.
"""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass

import numpy as np

GLB_MAGIC = 0x46546C67  # "glTF"
GLB_VERSION = 2
CHUNK_JSON = 0x4E4F534A  # "JSON"
CHUNK_BIN = 0x004E4942  # "BIN\0"

FLOAT = 5126
UNSIGNED_SHORT = 5123
UNSIGNED_INT = 5125
ARRAY_BUFFER = 34962
ELEMENT_ARRAY_BUFFER = 34963
TRIANGLES = 4


@dataclass(frozen=True)
class TerrainMesh:
    positions: np.ndarray  # float32 (n, 3)
    normals: np.ndarray  # float32 (n, 3)
    uvs: np.ndarray  # float32 (n, 2) — cell-centre texture coordinates
    indices: np.ndarray  # uint32 (m * 3,)
    grid_rows: np.ndarray  # int64 (n,) — the grid cell each vertex is
    grid_cols: np.ndarray  # int64 (n,)

    @property
    def vertex_count(self) -> int:
        return int(self.positions.shape[0])

    @property
    def triangle_count(self) -> int:
        return int(self.indices.shape[0] // 3)


def build_terrain_mesh(values: np.ndarray, *, step_x: float, step_z: float) -> TerrainMesh:
    """Mesh of a (height, width) grid whose invalid cells are NaN.

    Vertex (r, c) is at (c * step_x, values[r, c], r * step_z) relative to
    the centre of cell (0, 0); `step_x`/`step_z` are the signed horizontal
    cell sizes (both positive for a north-up raster). Winding is
    counter-clockwise seen from +y either way. Raises ValueError when no
    quad is valid."""
    values = np.asarray(values, dtype=np.float32)
    height, width = values.shape
    if height < 2 or width < 2:
        raise ValueError("The terrain grid is too small to form a surface.")
    valid = np.isfinite(values)
    quad = valid[:-1, :-1] & valid[:-1, 1:] & valid[1:, :-1] & valid[1:, 1:]
    qr, qc = np.nonzero(quad)
    if qr.size == 0:
        raise ValueError("The terrain grid has no valid surface to export (all NoData).")

    a = qr * width + qc  # (r, c)
    b = a + 1  # (r, c+1)
    c = a + width  # (r+1, c)
    d = c + 1  # (r+1, c+1)
    # Same split as the display mesh; counter-clockwise seen from +y.
    tri = np.stack([a, c, b, b, c, d], axis=1).reshape(-1)
    if step_x * step_z < 0:  # a mirrored axis flips the winding back
        tri = tri.reshape(-1, 3)[:, [0, 2, 1]].reshape(-1)

    used = np.unique(tri)
    remap = np.full(height * width, -1, dtype=np.int64)
    remap[used] = np.arange(used.size)
    indices = remap[tri].astype(np.uint32)

    rows = used // width
    cols = used % width
    positions = np.empty((used.size, 3), dtype=np.float32)
    positions[:, 0] = cols * step_x
    positions[:, 1] = values.reshape(-1)[used]
    positions[:, 2] = rows * step_z

    uvs = np.empty((used.size, 2), dtype=np.float32)
    uvs[:, 0] = (cols + 0.5) / width
    uvs[:, 1] = (rows + 0.5) / height

    # Area-weighted vertex normals from the physical geometry.
    p = positions.astype(np.float64)
    t = indices.reshape(-1, 3)
    face = np.cross(p[t[:, 1]] - p[t[:, 0]], p[t[:, 2]] - p[t[:, 0]])
    normals = np.zeros_like(p)
    for k in range(3):
        np.add.at(normals, t[:, k], face)
    lengths = np.linalg.norm(normals, axis=1, keepdims=True)
    normals = (normals / np.where(lengths > 0, lengths, 1.0)).astype(np.float32)

    return TerrainMesh(
        positions=positions,
        normals=normals,
        uvs=uvs,
        indices=indices,
        grid_rows=rows.astype(np.int64),
        grid_cols=cols.astype(np.int64),
    )


def _pad(data: bytes, fill: bytes) -> bytes:
    return data + fill * ((4 - len(data) % 4) % 4)


def write_glb(
    mesh: TerrainMesh,
    *,
    extras: dict,
    texture_png: bytes | None = None,
    name: str = "terrain",
) -> bytes:
    """A self-contained GLB: one mesh, one primitive, optional embedded PNG
    base-colour texture, `extras` under `asset.extras.terrainx`."""
    bin_parts: list[bytes] = []
    buffer_views: list[dict] = []
    accessors: list[dict] = []
    offset = 0

    def add_view(data: bytes, target: int | None) -> int:
        nonlocal offset
        view = {"buffer": 0, "byteOffset": offset, "byteLength": len(data)}
        if target is not None:
            view["target"] = target
        buffer_views.append(view)
        padded = _pad(data, b"\x00")
        bin_parts.append(padded)
        offset += len(padded)
        return len(buffer_views) - 1

    def add_accessor(
        array: np.ndarray, component: int, kind: str, target: int, bounds: bool
    ) -> int:
        view = add_view(np.ascontiguousarray(array).tobytes(), target)
        accessor = {
            "bufferView": view,
            "componentType": component,
            "count": int(array.shape[0]),
            "type": kind,
        }
        if bounds:
            accessor["min"] = [float(v) for v in array.min(axis=0)]
            accessor["max"] = [float(v) for v in array.max(axis=0)]
        accessors.append(accessor)
        return len(accessors) - 1

    position = add_accessor(mesh.positions, FLOAT, "VEC3", ARRAY_BUFFER, bounds=True)
    normal = add_accessor(mesh.normals, FLOAT, "VEC3", ARRAY_BUFFER, bounds=False)
    attributes = {"POSITION": position, "NORMAL": normal}
    if mesh.vertex_count <= 65535:
        index_data, index_type = mesh.indices.astype(np.uint16), UNSIGNED_SHORT
    else:
        index_data, index_type = mesh.indices.astype(np.uint32), UNSIGNED_INT
    indices = add_accessor(index_data, index_type, "SCALAR", ELEMENT_ARRAY_BUFFER, bounds=False)

    material: dict = {
        "name": "terrain",
        "doubleSided": True,
        "pbrMetallicRoughness": {"metallicFactor": 0.0, "roughnessFactor": 1.0},
    }
    gltf: dict = {
        "asset": {
            "version": "2.0",
            "generator": "TERRAIN-X P1-9 mesh export",
            "extras": {"terrainx": extras},
        },
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0, "name": name}],
    }
    if texture_png is not None:
        attributes["TEXCOORD_0"] = add_accessor(mesh.uvs, FLOAT, "VEC2", ARRAY_BUFFER, bounds=False)
        image_view = add_view(texture_png, None)
        gltf["images"] = [{"bufferView": image_view, "mimeType": "image/png"}]
        gltf["samplers"] = [{"magFilter": 9729, "minFilter": 9729, "wrapS": 33071, "wrapT": 33071}]
        gltf["textures"] = [{"sampler": 0, "source": 0}]
        material["pbrMetallicRoughness"]["baseColorTexture"] = {"index": 0, "texCoord": 0}
    else:
        material["pbrMetallicRoughness"]["baseColorFactor"] = [0.7, 0.7, 0.7, 1.0]

    gltf["meshes"] = [
        {
            "name": name,
            "primitives": [
                {"attributes": attributes, "indices": indices, "material": 0, "mode": TRIANGLES}
            ],
        }
    ]
    gltf["materials"] = [material]
    gltf["accessors"] = accessors
    gltf["bufferViews"] = buffer_views
    gltf["buffers"] = [{"byteLength": offset}]

    json_chunk = _pad(json.dumps(gltf, separators=(",", ":")).encode("utf-8"), b" ")
    bin_chunk = b"".join(bin_parts)
    total = 12 + 8 + len(json_chunk) + 8 + len(bin_chunk)
    return b"".join(
        [
            struct.pack("<III", GLB_MAGIC, GLB_VERSION, total),
            struct.pack("<II", len(json_chunk), CHUNK_JSON),
            json_chunk,
            struct.pack("<II", len(bin_chunk), CHUNK_BIN),
            bin_chunk,
        ]
    )
