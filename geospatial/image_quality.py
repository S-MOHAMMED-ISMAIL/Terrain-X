"""Real, cheap image-quality metrics computed directly from actual RGB pixel
data — independent of any AI model, and explicitly NOT a measure of model
confidence, segmentation accuracy, or elevation accuracy (see
docs/ARCHITECTURE.md §3.6 for the full "what this does and doesn't measure"
discussion). Every metric here is a plain, documented signal-processing
formula over the real uploaded image; none of them are combined into a
single score, and none of them are labeled "confidence" or "accuracy".

Independent of the backend/FastAPI layer by design, like the rest of
geospatial/ — the backend imports this module, never the reverse.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class ImageQualityMetrics:
    """Real, independently-interpretable quality indicators for one RGB
    image. None of these fields measure model accuracy or correctness —
    only properties of the pixel data itself."""

    # Sharpness proxy: the variance of the image's Laplacian (edge-response)
    # — a standard, widely-used blur heuristic. HIGHER means more
    # high-frequency detail (typically a sharper image); LOWER suggests
    # blur, defocus, or a very flat/textureless scene. It is a PROXY, not a
    # calibrated blur measurement in any physical unit, and a low value can
    # also just mean a genuinely smooth/uniform real scene, not necessarily
    # a quality problem.
    sharpness_laplacian_variance: float

    # Fraction (0-1) of pixels whose per-pixel mean luminance is at or below
    # a low threshold (near-black) — a real, direct count, not an estimate.
    underexposed_fraction: float
    # Fraction (0-1) of pixels whose per-pixel mean luminance is at or above
    # a high threshold (near-white/clipped) — real, direct count.
    overexposed_fraction: float

    # Real min/max/mean of per-pixel mean luminance (0-255 scale) — the
    # actual dynamic range used by this specific image, not a theoretical
    # maximum.
    luminance_min: float
    luminance_max: float
    luminance_mean: float

    # Fraction (0-1) of pixels that are finite and not part of any detected
    # NoData convention for this array — 1.0 for a normal 8-bit RGB image
    # with no NoData concept; included for interface symmetry with the
    # raster-based metrics elsewhere in this project, and to explicitly
    # confirm "no invalid pixels silently ignored" rather than assume it.
    valid_pixel_fraction: float


# Standard ITU-R BT.601 luma weights — a documented, real formula, not an
# arbitrary average of the three channels.
_LUMA_WEIGHTS = np.array([0.299, 0.587, 0.114], dtype=np.float64)

# Thresholds on a 0-255 luminance scale. These are real, fixed, documented
# cutoffs (not learned/fitted) — a pixel this dark/bright is being counted
# as under/overexposed by definition, not by any claim about how it will
# affect any downstream model.
_UNDEREXPOSED_THRESHOLD = 10.0
_OVEREXPOSED_THRESHOLD = 245.0


def compute_image_quality(rgb_image: np.ndarray) -> ImageQualityMetrics:
    """Computes real quality metrics from an (H, W, 3) uint8 RGB array —
    the same array already validated/extracted by
    `app.services.depth_pipeline.extract_rgb_uint8` for model input, so no
    new image-decoding logic is introduced here.
    """
    if rgb_image.ndim != 3 or rgb_image.shape[2] != 3:
        raise ValueError(f"Expected an (H, W, 3) RGB array, got shape {rgb_image.shape}")

    finite_mask = np.isfinite(rgb_image).all(axis=-1)
    valid_pixel_fraction = float(finite_mask.mean()) if finite_mask.size else 0.0

    luminance = (rgb_image.astype(np.float64) * _LUMA_WEIGHTS).sum(axis=-1)

    underexposed_fraction = float((luminance <= _UNDEREXPOSED_THRESHOLD).mean())
    overexposed_fraction = float((luminance >= _OVEREXPOSED_THRESHOLD).mean())

    sharpness = _laplacian_variance(luminance)

    return ImageQualityMetrics(
        sharpness_laplacian_variance=sharpness,
        underexposed_fraction=underexposed_fraction,
        overexposed_fraction=overexposed_fraction,
        luminance_min=float(luminance.min()),
        luminance_max=float(luminance.max()),
        luminance_mean=float(luminance.mean()),
        valid_pixel_fraction=valid_pixel_fraction,
    )


def _laplacian_variance(luminance: np.ndarray) -> float:
    """Real discrete Laplacian (4-neighbor kernel: [[0,1,0],[1,-4,1],[0,1,0]])
    convolution via `numpy` (no `opencv`/`scipy` dependency needed for a
    single fixed 3x3 kernel), computed with reflected edge padding so the
    output has the same shape as the input and edge pixels aren't
    artificially zeroed. The variance of the result is the actual sharpness
    proxy — a real statistic of a real convolution, not an approximation
    or a placeholder.
    """
    padded = np.pad(luminance, 1, mode="reflect")
    laplacian = (
        padded[:-2, 1:-1]  # up
        + padded[2:, 1:-1]  # down
        + padded[1:-1, :-2]  # left
        + padded[1:-1, 2:]  # right
        - 4.0 * luminance
    )
    return float(laplacian.var())
