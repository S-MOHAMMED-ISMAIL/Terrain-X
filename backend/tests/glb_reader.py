"""Test-only, independent GLB (binary glTF 2.0) reader used to validate the
P1-9 mesh export. Written from the Khronos glTF 2.0 specification; it shares
no code with geospatial/mesh_export.py (the writer), so a writer bug cannot
be masked by the same bug in the reader."""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass

import numpy as np

_COMPONENTS = {5120: "<i1", 5121: "<u1", 5122: "<i2", 5123: "<u2", 5125: "<u4", 5126: "<f4"}
_WIDTH = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}


@dataclass
class Glb:
    version: int
    declared_length: int
    json_chunk_length: int
    bin_chunk_length: int
    gltf: dict
    bin: bytes


def read_glb(data: bytes) -> Glb:
    if len(data) < 20:
        raise ValueError("Too short to be a GLB.")
    magic, version, length = struct.unpack_from("<4sII", data, 0)
    if magic != b"glTF":
        raise ValueError(f"Bad magic {magic!r}.")
    if length != len(data):
        raise ValueError(f"Declared length {length} != actual {len(data)}.")
    json_len, json_type = struct.unpack_from("<I4s", data, 12)
    if json_type != b"JSON":
        raise ValueError("First chunk is not JSON.")
    json_bytes = data[20 : 20 + json_len]
    gltf = json.loads(json_bytes.decode("utf-8"))
    pos = 20 + json_len
    bin_bytes = b""
    bin_len = 0
    if pos < len(data):
        bin_len, bin_type = struct.unpack_from("<I4s", data, pos)
        if bin_type != b"BIN\x00":
            raise ValueError("Second chunk is not BIN.")
        bin_bytes = data[pos + 8 : pos + 8 + bin_len]
    return Glb(version, length, json_len, bin_len, gltf, bin_bytes)


def accessor(glb: Glb, index: int) -> np.ndarray:
    acc = glb.gltf["accessors"][index]
    view = glb.gltf["bufferViews"][acc["bufferView"]]
    dtype = np.dtype(_COMPONENTS[acc["componentType"]])
    width = _WIDTH[acc["type"]]
    start = view.get("byteOffset", 0) + acc.get("byteOffset", 0)
    stride = view.get("byteStride")
    if stride not in (None, dtype.itemsize * width):
        raise ValueError("Interleaved buffer views are not expected here.")
    count = acc["count"]
    end = start + count * width * dtype.itemsize
    if end > view.get("byteOffset", 0) + view["byteLength"] or end > len(glb.bin):
        raise ValueError("Accessor reads past its buffer view.")
    array = np.frombuffer(glb.bin, dtype=dtype, count=count * width, offset=start)
    return array.reshape(count, width) if width > 1 else array


def image_bytes(glb: Glb, index: int = 0) -> bytes:
    image = glb.gltf["images"][index]
    view = glb.gltf["bufferViews"][image["bufferView"]]
    start = view.get("byteOffset", 0)
    return glb.bin[start : start + view["byteLength"]]


def primitive(glb: Glb) -> dict:
    return glb.gltf["meshes"][0]["primitives"][0]
