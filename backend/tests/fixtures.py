"""Generates real image/raster bytes for tests — no binary fixture files
committed to the repo, and no mocking of the actual file formats."""

import io
import os

import numpy as np
from PIL import Image, ImageDraw
from rasterio.io import MemoryFile
from rasterio.transform import from_origin


def make_jpeg_bytes(width: int = 64, height: int = 48) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color=(120, 60, 200)).save(buf, format="JPEG")
    return buf.getvalue()


def make_png_bytes(width: int = 64, height: int = 48) -> bytes:
    buf = io.BytesIO()
    Image.new("RGBA", (width, height), color=(10, 200, 90, 255)).save(buf, format="PNG")
    return buf.getvalue()


def make_geotiff_bytes(
    width: int = 30, height: int = 20, bands: int = 2, crs: str = "EPSG:4326"
) -> bytes:
    transform = from_origin(77.5, 13.0, 0.001, 0.001)
    data = (np.random.rand(bands, height, width) * 255).astype("uint8")
    with MemoryFile() as memfile:
        with memfile.open(
            driver="GTiff",
            height=height,
            width=width,
            count=bands,
            dtype="uint8",
            crs=crs,
            transform=transform,
        ) as dataset:
            dataset.write(data)
        return memfile.read()


def make_plain_tiff_bytes(width: int = 10, height: int = 10) -> bytes:
    data = (np.random.rand(1, height, width) * 255).astype("uint8")
    with MemoryFile() as memfile:
        with memfile.open(
            driver="GTiff", height=height, width=width, count=1, dtype="uint8"
        ) as dataset:
            dataset.write(data)
        return memfile.read()


def make_corrupt_jpeg_bytes() -> bytes:
    """Real JPEG magic bytes, garbage body — passes the upload-gate sniff,
    fails real GDAL/libjpeg parsing."""
    return b"\xff\xd8\xff" + os.urandom(200)


def make_bogus_bytes() -> bytes:
    """No recognizable magic bytes at all — rejected before any dataset row
    is created."""
    return b"this is definitely not an image" * 10


def make_structured_scene_rgb_array(width: int = 256, height: int = 256) -> np.ndarray:
    """A small synthetic but spatially structured RGB scene: a vertical sky
    gradient (far) behind a solid ground band and a foreground disc (near).

    Suitable for real depth-model testing because it gives the model genuine
    near/far visual cues — a foreground shape with a distinct color against
    a receding gradient background, similar in structure to the kind of
    monocular depth cues (relative size, position, occlusion boundary) these
    models are trained on — unlike a single flat color (trivially constant,
    used elsewhere in this test suite for fast metadata-only tests) or pure
    random noise (no coherent structure for a depth model to key off of).
    This is not a guarantee of any particular output, only a much more
    meaningful input than either of those alternatives for asserting real
    inference produces non-constant, spatially-varying output.
    """
    image = Image.new("RGB", (width, height))
    draw = ImageDraw.Draw(image)

    # Sky gradient: lighter near the top, darker toward the horizon.
    for y in range(height):
        shade = int(200 - 120 * (y / height))
        draw.line([(0, y), (width, y)], fill=(shade, shade, 255))

    # Ground band across the lower third.
    horizon = int(height * 0.65)
    draw.rectangle([0, horizon, width, height], fill=(80, 140, 60))

    # Foreground object: a bright disc partly over the ground/sky boundary.
    cx, cy, r = width // 2, horizon, min(width, height) // 6
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(220, 60, 40))

    return np.array(image, dtype=np.uint8)


def make_structured_scene_jpeg_bytes(width: int = 256, height: int = 256) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(make_structured_scene_rgb_array(width, height), mode="RGB").save(
        buf, format="JPEG", quality=95
    )
    return buf.getvalue()


def make_structured_scene_png_bytes(width: int = 256, height: int = 256) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(make_structured_scene_rgb_array(width, height), mode="RGB").save(
        buf, format="PNG"
    )
    return buf.getvalue()


def make_structured_scene_geotiff_bytes(
    width: int = 256,
    height: int = 256,
    crs: str = "EPSG:4326",
    origin_x: float = 77.5,
    origin_y: float = 13.0,
    pixel_size: float = 0.0001,
) -> bytes:
    """A 3-band (RGB), uint8, georeferenced GeoTIFF of the same structured
    scene — for testing that depth estimation preserves real source
    georeferencing onto its output. The origin/pixel_size/crs defaults match
    historical Phase 3 tests; Phase 4 calibration tests override them to
    share a real-world footprint with a paired DEM/GCP reference fixture."""
    array = make_structured_scene_rgb_array(width, height)  # (H, W, 3)
    bands_first = np.transpose(array, (2, 0, 1))  # (3, H, W)
    transform = from_origin(origin_x, origin_y, pixel_size, pixel_size)
    with MemoryFile() as memfile:
        with memfile.open(
            driver="GTiff",
            height=height,
            width=width,
            count=3,
            dtype="uint8",
            crs=crs,
            transform=transform,
        ) as dataset:
            dataset.write(bands_first)
        return memfile.read()


def make_four_band_tiff_bytes(width: int = 32, height: int = 32) -> bytes:
    """A 4-band uint8 TIFF (no CRS) — ambiguous for depth estimation (could
    be RGBA or genuine 4-band spectral data) and must be rejected for
    TIFF/GeoTIFF, unlike the PNG RGBA case which is unambiguous."""
    data = (np.random.rand(4, height, width) * 255).astype("uint8")
    with MemoryFile() as memfile:
        with memfile.open(
            driver="GTiff", height=height, width=width, count=4, dtype="uint8"
        ) as dataset:
            dataset.write(data)
        return memfile.read()


def make_dem_geotiff_bytes(
    width: int = 64,
    height: int = 64,
    crs: str = "EPSG:32633",
    origin_x: float = 500000.0,
    origin_y: float = 4649984.0,
    pixel_size: float = 2.0,
    nodata: float = -9999.0,
    elevation_fn=None,
    vertical_unit: str | None = "metre",
) -> bytes:
    """A real single-band float32 DEM GeoTIFF with a smooth, non-constant
    elevation surface (a gentle slope plus a bump) and a real NoData region
    in one corner, for testing Phase 4 calibration sampling. `elevation_fn`,
    if given, overrides the default surface with `f(xx, yy) -> elevation`
    over normalized [0, 1] grid coordinates — used by tests that need a
    surface with a specific known relationship to another array.

    D3: `vertical_unit` is DECLARED as the GeoTIFF band unit (the fixture's
    elevations are defined in metres); pass None for a DEM whose vertical
    unit is undeclared, i.e. UNKNOWN.
    """
    transform = from_origin(origin_x, origin_y, pixel_size, pixel_size)
    x = np.linspace(0, 1, width)
    y = np.linspace(0, 1, height)
    xx, yy = np.meshgrid(x, y)
    if elevation_fn is not None:
        dem = elevation_fn(xx, yy).astype(np.float32)
    else:
        dem = (100.0 + xx * 30.0 + yy * 15.0).astype(np.float32)
    dem[0:5, 0:5] = nodata
    with MemoryFile() as memfile:
        with memfile.open(
            driver="GTiff",
            height=height,
            width=width,
            count=1,
            dtype="float32",
            crs=crs,
            transform=transform,
            nodata=nodata,
        ) as dataset:
            dataset.write(dem, 1)
            if vertical_unit is not None:
                dataset.set_band_unit(1, vertical_unit)
        return memfile.read()


_DEPTH_ESTIMATOR = None


def real_structured_scene_depth(width: int = 64, height: int = 64) -> np.ndarray:
    """The REAL Depth Anything V2 relative-depth prediction for
    `make_structured_scene_rgb_array(width, height)` — the exact RGB pixels
    the worker reads back from `make_structured_scene_geotiff_bytes`. Used to
    build calibration references with a deliberately known relationship to
    the depth the worker will actually produce. The model is loaded once per
    test process; a fresh copy is returned on every call."""
    global _DEPTH_ESTIMATOR
    if _DEPTH_ESTIMATOR is None:
        from ai.depth_anything import DepthAnythingV2Estimator

        _DEPTH_ESTIMATOR = DepthAnythingV2Estimator()
        _DEPTH_ESTIMATOR.load()
    rgb = make_structured_scene_rgb_array(width, height)
    return _DEPTH_ESTIMATOR.predict(rgb).depth.copy()


def make_depth_consistent_dem_geotiff_bytes(
    depth: np.ndarray,
    *,
    scale: float = 2.0,
    offset: float = 100.0,
    noise_std: float = 0.05,
    seed: int = 0,
    **dem_kwargs,
) -> bytes:
    """A DEM on the source image's own grid whose elevation is DELIBERATELY
    constructed as `scale * depth + offset + N(0, noise_std)` (same NoData
    corner as `make_dem_geotiff_bytes`).

    These fixtures validate pipeline behavior and are NOT accuracy evidence:
    the "reference" is derived from the model's own output, so a calibration
    against it says nothing about how well depth matches real terrain. A
    positive `scale` gives the relationship the calibration quality gate
    expects; a negative one exercises its expected-scale-sign rejection."""
    height, width = depth.shape
    noise = np.random.default_rng(seed).normal(0.0, noise_std, depth.shape)
    elevation = scale * depth.astype("float64") + offset + noise
    return make_dem_geotiff_bytes(
        width=width, height=height, elevation_fn=lambda xx, yy: elevation, **dem_kwargs
    )


def pixel_center_map_coords(
    row: int, col: int, *, origin_x: float, origin_y: float, pixel_size: float
) -> tuple[float, float]:
    """Map coordinate of a north-up raster pixel's center — for placing a
    GCP exactly on a known source pixel."""
    return origin_x + (col + 0.5) * pixel_size, origin_y - (row + 0.5) * pixel_size


def make_gcp_csv_bytes(points: list[tuple[float, float, float]] | None = None) -> bytes:
    """A real GCP CSV (x, y, z header). The default points all fall inside a
    64x64-pixel, 2m/pixel footprint anchored at (500000, 4649984) in
    EPSG:32633 (i.e. x in [500000, 500128], y in [4649856, 4649984]) — the
    same default footprint make_dem_geotiff_bytes and
    make_structured_scene_geotiff_bytes (with matching origin/pixel_size
    overrides) use in the Phase 4 calibration tests, so a default-sized test
    source image genuinely covers every default GCP."""
    if points is None:
        points = [
            (500010.0, 4649974.0, 102.0),
            (500100.0, 4649920.0, 110.0),
            (500030.0, 4649870.0, 128.0),
            (500080.0, 4649890.0, 122.0),
        ]
    lines = ["name,x,y,z"]
    for i, (x, y, z) in enumerate(points, start=1):
        lines.append(f"p{i},{x},{y},{z}")
    return ("\n".join(lines) + "\n").encode("utf-8")


def make_structured_scene_tiff_bytes_no_georef(width: int = 64, height: int = 64) -> bytes:
    """A 3-band uint8 TIFF of the same structured scene, with NO CRS/transform
    at all — a real, valid depth-estimation input that is genuinely not
    georeferenced, unlike make_plain_tiff_bytes (1-band, rejected by the
    depth pipeline's own input policy before calibration is ever reached).
    Used for testing that calibration correctly refuses to fabricate a CRS
    for a non-georeferenced source."""
    array = make_structured_scene_rgb_array(width, height)
    bands_first = np.transpose(array, (2, 0, 1))
    with MemoryFile() as memfile:
        with memfile.open(
            driver="GTiff", height=height, width=width, count=3, dtype="uint8"
        ) as dataset:
            dataset.write(bands_first)
        return memfile.read()


def make_uint16_rgb_tiff_bytes(width: int = 32, height: int = 32) -> bytes:
    """A structurally valid 3-band RGB TIFF, but 16-bit — must be rejected
    under the Phase 3 uint8-only input policy rather than silently rescaled."""
    data = (np.random.rand(3, height, width) * 65535).astype("uint16")
    with MemoryFile() as memfile:
        with memfile.open(
            driver="GTiff", height=height, width=width, count=3, dtype="uint16"
        ) as dataset:
            dataset.write(data)
        return memfile.read()
