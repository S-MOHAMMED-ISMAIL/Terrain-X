class RasterValidationError(Exception):
    """Raised when a file cannot be opened/read as a valid raster."""


class DisasterAnalysisError(Exception):
    """Raised when a real, anticipated condition makes terrain-derivative
    or hazard-screening computation impossible for the given elevation
    raster (not georeferenced, no valid elevation pixels, a non-finite
    water level, ...) — a real, explicit failure, never a fabricated
    result standing in for one."""


class MeasurementInputError(Exception):
    """Raised when a measurement request's own inputs are invalid for the
    raster it's being computed against (a point outside the raster bounds,
    too few profile samples, ...) — distinct from RasterValidationError,
    which is about the file itself being unreadable. The raster is fine;
    the request is not, and is rejected outright rather than silently
    clamped or answered with a fabricated value."""
