"""Phase 5 visualization API response schemas.

Every field here is either read directly from a `Dataset`/`AnalysisJob`/
`AnalysisArtifact` row, or computed for real from the actual stored raster
(see geospatial/raster_preview.py, geospatial/terrain_grid.py). Nothing is
a placeholder/default standing in for data that doesn't exist — an
unavailable value is `None`, never a fabricated number.
"""

import uuid
from typing import Literal

from pydantic import BaseModel


class BoundingBoxOut(BaseModel):
    min_x: float
    min_y: float
    max_x: float
    max_y: float


class DatasetContextOut(BaseModel):
    id: uuid.UUID
    original_filename: str
    file_type: str
    width: int | None
    height: int | None
    is_georeferenced: bool
    crs: str | None
    bounds: BoundingBoxOut | None  # in the dataset's own (native) CRS
    # The same bounds transformed to EPSG:4326 via rasterio/GDAL/PROJ
    # (never an ad-hoc degree/meter approximation) — purely so the frontend
    # can position an image overlay on a standard lat/lon web basemap.
    # None whenever `bounds` itself is None (not georeferenced). Every
    # relative_depth/metric_elevation/dsm artifact shares this exact
    # footprint by construction (Phase 3/4 always carry the source's own
    # CRS/transform through unchanged), so the frontend reuses this one
    # value for whichever layer is active rather than recomputing it.
    bounds_wgs84: BoundingBoxOut | None


class LayerContextOut(BaseModel):
    """One visualization layer. `available=False` means exactly what
    `unavailable_reason` says — never a disabled entry pretending data
    exists that doesn't (see docs/ARCHITECTURE.md §3.5)."""

    # "rgb" | "relative_depth" | "metric_elevation" | "dsm" | "semantic_segmentation"
    layer_type: str
    display_name: str
    available: bool
    unavailable_reason: str | None = None
    # True for a categorical (region-ID) raster — the frontend must use a
    # deterministic per-ID color legend, never the continuous min/max ramp
    # used for relative_depth/metric_elevation/dsm. See
    # docs/ARCHITECTURE.md §3.6.
    is_categorical: bool = False
    # Only meaningful when is_categorical=True: the real number of distinct
    # detected regions in this layer's raster (excludes background/NoData).
    region_count: int | None = None
    # Only meaningful when is_categorical=True AND this layer has a real,
    # FIXED set of class labels (Phase 8's flood_screening/
    # landslide_screening — never populated for semantic_segmentation,
    # whose region IDs are arbitrary, not fixed named classes). Each entry
    # is `{"value": <int>, "label": <str>}`, taken verbatim from the
    # artifact's own persisted `class_labels` metadata.
    legend: list[dict] | None = None

    # Only meaningful when available=True. artifact_id is None for the
    # "rgb" layer, which is the dataset's own source file, not an
    # AnalysisArtifact row.
    artifact_id: uuid.UUID | None = None
    analysis_job_id: uuid.UUID | None = None
    width: int | None = None
    height: int | None = None
    dtype: str | None = None
    nodata: float | None = None
    is_georeferenced: bool | None = None
    crs: str | None = None
    bounds: BoundingBoxOut | None = None
    min_value: float | None = None
    max_value: float | None = None
    # P1-6: exact WGS84 corners (min_x=west, min_y=south, max_x=east,
    # max_y=north) of this layer's OWN EPSG:3857 map-overlay grid (see
    # geospatial/map_overlay.py) — where the `/map-preview` PNG must be
    # placed on the 2D map. None when the layer's raster is not
    # georeferenced (or the overlay grid could not be computed; the
    # map-preview endpoint then returns the real error).
    map_overlay_bounds: BoundingBoxOut | None = None
    # Verbatim value-semantics/limitations text already persisted on the
    # artifact by Phase 3/4 (e.g. "relative, unitless inverse depth...",
    # the DSM limitations string) — never re-authored here.
    notes: str | None = None


class TerrainContextOut(BaseModel):
    """Phase 10: a real 3D terrain source is now either a calibrated `dsm`
    artifact (`height_kind="elevation"`) OR — when no calibration has
    succeeded — the job's own `relative_depth` artifact
    (`height_kind="relative_depth"`), reusing the exact same generic
    terrain-grid extraction machinery (`geospatial/terrain_grid.py`) for
    both. `height_kind` is `None` only when `available=False`. See
    docs/ARCHITECTURE.md §3.5/§3.10 for the full scientific-honesty
    rationale — a relative-depth-backed terrain is NEVER elevation, NEVER a
    DSM, and NEVER assigned a physical unit.

    `"remote_sensing_height"` is a reserved, currently-UNUSED third value —
    nothing in this codebase ever produces an artifact/job state that maps
    to it today (see `app/services/visualization.py::height_kind_for_artifact_type`
    and `ai/rs_height_estimator.py`). It exists so the response contract can
    already represent a future remote-sensing-specific height model's
    output without a breaking schema change once one exists, and so it is
    never collapsed into (mistaken for) `"relative_depth"` or `"elevation"`
    when it does. See docs/ARCHITECTURE_NOTE_RS_HEIGHT.md.
    """

    available: bool
    unavailable_reason: str | None = None
    artifact_id: uuid.UUID | None = None
    analysis_job_id: uuid.UUID | None = None
    # Which real artifact backs this terrain, and what its height values
    # actually mean — see the class docstring. Both `None` iff `available`
    # is False.
    height_kind: Literal["elevation", "relative_depth", "remote_sensing_height"] | None = None
    source_artifact_type: str | None = None  # "dsm" or "relative_depth"
    width: int | None = None  # the source artifact's real source pixel dimensions
    height: int | None = None
    # D2: whether the dataset's RGB image may be draped on this terrain
    # pixel-for-pixel (geospatial.terrain_grid.rgb_texture_compatibility —
    # the same rule the GLB export uses). `texture_unavailable_code` is
    # machine-readable (e.g. "reprojected_terrain_grid"); both it and the
    # reason are None when compatible. All None iff `available` is False.
    texture_compatible: bool | None = None
    texture_unavailable_code: str | None = None
    texture_unavailable_reason: str | None = None
    is_georeferenced: bool | None = None
    crs: str | None = None
    bounds: BoundingBoxOut | None = None
    # Populated ONLY when height_kind == "elevation" — never for a
    # relative-depth-backed terrain, so no consumer can mistake a unitless
    # relative value for a real elevation range by field name alone.
    min_elevation: float | None = None
    max_elevation: float | None = None
    # Real min/max of whatever the terrain's actual height values are,
    # regardless of height_kind — scientifically neutral naming (no
    # "elevation" in the name) so it's always honest to populate, for
    # either a calibrated DSM or a relative-depth-backed relative terrain.
    min_height_value: float | None = None
    max_height_value: float | None = None


class CalibrationResidualsContextOut(BaseModel):
    """P1-5: availability of the job's calibration_residuals point artifact.
    Deliberately NOT a `LayerContextOut` — it is not a raster, so it must
    never reach any raster consumer (preview, pixel value, terrain,
    measurements) that iterates `layers`."""

    available: bool
    unavailable_reason: str | None = None
    artifact_id: uuid.UUID | None = None
    analysis_job_id: uuid.UUID | None = None
    reference_type: Literal["dem", "gcp"] | None = None
    sample_count: int | None = None


class VisualizationContextOut(BaseModel):
    dataset: DatasetContextOut
    layers: list[LayerContextOut]
    terrain: TerrainContextOut
    calibration_residuals: CalibrationResidualsContextOut


class RasterMetadataOut(BaseModel):
    """Real statistics for one artifact's raster file (see
    geospatial.raster_preview.RasterStatistics)."""

    artifact_id: uuid.UUID
    artifact_type: str
    display_name: str
    driver: str
    width: int
    height: int
    count: int
    dtype: str
    nodata: float | None
    is_georeferenced: bool
    crs: str | None
    bounds: BoundingBoxOut | None
    resolution_x: float | None
    resolution_y: float | None
    min_value: float | None
    max_value: float | None
    has_finite_data: bool
    stats_sampled: bool
    notes: str | None = None
    is_categorical: bool = False
    region_count: int | None = None
    legend: list[dict] | None = None


class PixelValueOut(BaseModel):
    row: int
    col: int
    value: float | None  # None means out-of-bounds or a real NoData/NaN/Inf pixel — never 0


class TerrainLocalCoordinateOut(BaseModel):
    """P1-8: a WGS84 point expressed in a terrain artifact's own local 3D
    frame (see geospatial/terrain_grid.py). `map_x`/`map_y` are in
    `local_crs` — the same frame the terrain metadata's `origin_x/origin_y`
    and the 3D click path use, so grid-local = map - origin. `in_footprint`
    says whether the point lies inside the terrain grid's vertex extent; the
    coordinate is never adjusted to fit."""

    local_crs: str
    map_x: float
    map_y: float
    in_footprint: bool


class TerrainMetadataOut(BaseModel):
    """Metadata accompanying the binary height-grid response — see
    geospatial.terrain_grid.TerrainGrid. The frontend fetches this first,
    then the binary grid, using `width`/`height` to interpret the payload.

    Phase 10: this same endpoint now also serves a `relative_depth`
    artifact when no calibrated `dsm` exists yet — `height_kind` says which
    real case applies (see TerrainContextOut's docstring for the full
    scientific-honesty rationale)."""

    artifact_id: uuid.UUID
    # See TerrainContextOut's docstring — "remote_sensing_height" is
    # reserved for a future model, currently unused.
    height_kind: Literal["elevation", "relative_depth", "remote_sensing_height"]
    source_artifact_type: str  # "dsm" or "relative_depth"
    width: int
    height: int
    source_width: int
    source_height: int
    nodata_present: bool
    is_georeferenced: bool
    crs: str | None
    local_crs: str | None
    origin_x: float | None
    origin_y: float | None
    cell_size_x: float | None
    cell_size_y: float | None
    bounds: BoundingBoxOut | None
    # Populated ONLY when height_kind == "elevation" — see
    # TerrainContextOut's docstring.
    min_elevation: float | None
    max_elevation: float | None
    # Real min/max of the actual returned grid values regardless of
    # height_kind — scientifically neutral naming.
    min_height_value: float | None
    max_height_value: float | None
    encoding: str = "float32_row_major_nan_nodata"
