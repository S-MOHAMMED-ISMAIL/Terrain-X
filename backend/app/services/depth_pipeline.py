"""Phase 3 backend-side policy: decides whether a dataset's real pixel data
is eligible to feed to the depth model, and prepares it if so. Model
mechanics live in ai/; raster I/O lives in geospatial/; this module is the
backend-side glue between them — see docs/ARCHITECTURE.md for the full
input-eligibility policy and its rationale.
"""

import numpy as np

from geospatial.raster_io import RasterArray

# Verbatim, persisted into every relative_depth artifact's own metadata (see
# app/services/analysis_execution.py) and reused as-is by Phase 7's
# measurement service (app/services/measurement_service.py) so a
# point-elevation/profile reading taken from a relative_depth artifact
# carries the exact same disclaimer, never a re-worded (and possibly
# drifted) copy of it.
RELATIVE_DEPTH_VALUE_SEMANTICS = (
    "Relative, unitless inverse depth (disparity-like): LARGER values "
    "indicate objects CLOSER to the camera, SMALLER values indicate objects "
    "FARTHER away. This is monocular relative depth, NOT metric elevation, "
    "NOT a DSM, and has undergone no DEM/GCP calibration."
)


class UnsupportedDepthInputError(Exception):
    """A dataset's band/dtype configuration is not eligible for depth
    estimation under the documented Phase 3 policy. Distinct from a
    corrupt/unreadable file (that's RasterValidationError) — this is a file
    GDAL reads just fine, just not one this phase supports feeding to the
    model.
    """


def extract_rgb_uint8(raster: RasterArray, file_type: str) -> np.ndarray:
    """Applies the Phase 3 input policy and returns an (H, W, 3) uint8 RGB
    array, or raises UnsupportedDepthInputError.

    Policy (see docs/ARCHITECTURE.md for the full rationale):
      - dtype must be uint8 (8-bit imagery) — no implicit rescaling of
        16-bit/float remote-sensing products, which would require a
        deliberate, documented normalization scheme this phase does not
        implement.
      - PNG with 4 bands (RGBA): alpha is dropped (it carries no radiometric
        information) and the remaining 3 bands are used as-is.
      - Exactly 3 bands (any supported file type): used as-is, assumed RGB.
      - Anything else (1-band grayscale, 2-band, TIFF/GeoTIFF with 4+ bands,
        ...) is rejected — for TIFF/GeoTIFF in particular, a 4th band could
        be alpha or genuine spectral data and there is no reliable way to
        tell which from metadata alone, so it is rejected rather than
        guessed at.
    """
    bands = raster.data.shape[0]

    if raster.dtype != "uint8":
        raise UnsupportedDepthInputError(
            f"Depth estimation currently requires 8-bit (uint8) imagery; "
            f"this dataset is {raster.dtype}."
        )

    if file_type == "png" and bands == 4:
        array = raster.data[:3]
    elif bands == 3:
        array = raster.data
    else:
        raise UnsupportedDepthInputError(
            f"Depth estimation requires a 3-band RGB image (or 4-band RGBA "
            f"for PNG, with alpha dropped); this dataset has {bands} band(s)."
        )

    # rasterio reads (bands, H, W); PIL/the model expect (H, W, bands).
    return np.transpose(array, (1, 2, 0)).copy()
