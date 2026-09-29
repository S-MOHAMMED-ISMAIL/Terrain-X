"""Semantic-aware terrain mesh preparation — visualization-only regularization.

This module prepares a visualization mesh from raw monocular depth by applying
region-aware regularization. It uses MobileSAM's class-agnostic regions to
identify coherent surfaces and applies controlled smoothing to suppress
monocular depth noise.

CRITICAL SCIENTIFIC RULE:
This module does NOT perform semantic classification. It does NOT distinguish
between buildings, roads, trees, water, or terrain. It applies generic
region-aware smoothing to improve visualization coherence.

The original raw relative depth artifact remains untouched. This is purely a
visualization mesh preparation step.

Uses only numpy (no scipy dependency) for Docker compatibility.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class MeshPreparationConfig:
    """Configuration for semantic-aware mesh preparation."""

    region_smoothing: float = 0.5
    spike_threshold: float = 2.0
    min_region_area: int = 50
    building_min_area: int = 200
    road_aspect_ratio: float = 3.0
    water_flatness_threshold: float = 0.1


@dataclass(frozen=True)
class MeshPreparationResult:
    """Result of semantic-aware mesh preparation."""

    depth: np.ndarray
    region_map: np.ndarray
    region_stats: list[dict]
    processing_seconds: float
    config: MeshPreparationConfig


def _median_filter_numpy(arr: np.ndarray, size: int = 3) -> np.ndarray:
    """Simple median filter using numpy only."""
    pad = size // 2
    padded = np.pad(arr, pad, mode="edge")
    result = np.zeros_like(arr)
    for i in range(arr.shape[0]):
        for j in range(arr.shape[1]):
            window = padded[i:i+size, j:j+size]
            result[i, j] = np.median(window)
    return result


def _gaussian_filter_numpy(arr: np.ndarray, sigma: float = 1.0) -> np.ndarray:
    """Simple Gaussian filter using numpy only."""
    size = int(6 * sigma) | 1  # Ensure odd
    if size < 3:
        size = 3
    x = np.arange(size) - size // 2
    kernel_1d = np.exp(-x**2 / (2 * sigma**2))
    kernel_1d = kernel_1d / kernel_1d.sum()

    # Separable convolution
    result = np.apply_along_axis(
        lambda row: np.convolve(row, kernel_1d, mode="same"), 0, arr
    )
    result = np.apply_along_axis(
        lambda col: np.convolve(col, kernel_1d, mode="same"), 1, result
    )
    return result


def prepare_semantic_mesh(
    depth: np.ndarray,
    region_map: np.ndarray,
    config: MeshPreparationConfig | None = None,
) -> MeshPreparationResult:
    """Prepare a visualization mesh from raw depth and MobileSAM regions."""
    import time

    start = time.perf_counter()
    config = config or MeshPreparationConfig()

    regularized = depth.copy().astype(np.float32)
    region_stats = []

    unique_regions = np.unique(region_map)
    unique_regions = unique_regions[unique_regions > 0]

    for region_id in unique_regions:
        mask = region_map == region_id
        area = int(mask.sum())

        if area < config.min_region_area:
            continue

        region_depth = depth[mask]
        median_depth = float(np.median(region_depth))
        std_depth = float(np.std(region_depth))

        rows, cols = np.where(mask)
        if len(rows) == 0:
            continue
        height = rows.max() - rows.min() + 1
        width = cols.max() - cols.min() + 1
        aspect_ratio = max(height, width) / max(min(height, width), 1)

        region_type = _classify_region(area, aspect_ratio, std_depth, config)

        if region_type == "building":
            regularized = _regularize_building(regularized, mask, region_depth, config)
        elif region_type == "road":
            regularized = _regularize_road(regularized, mask, region_depth, config)
        elif region_type == "water":
            regularized = _regularize_water(regularized, mask, region_depth, config)
        else:
            regularized = _regularize_generic(regularized, mask, region_depth, config)

        region_stats.append({
            "region_id": int(region_id),
            "area": area,
            "aspect_ratio": aspect_ratio,
            "median_depth": median_depth,
            "std_depth": std_depth,
            "region_type": region_type,
        })

    regularized = _suppress_spikes(regularized, region_map, config)
    processing_seconds = time.perf_counter() - start

    return MeshPreparationResult(
        depth=regularized,
        region_map=region_map,
        region_stats=region_stats,
        processing_seconds=processing_seconds,
        config=config,
    )


def _classify_region(area: int, aspect_ratio: float, std_depth: float, config: MeshPreparationConfig) -> str:
    """Classify a region for visualization purposes only (NOT semantic)."""
    if area >= config.building_min_area and std_depth < config.water_flatness_threshold:
        return "building"
    if aspect_ratio >= config.road_aspect_ratio:
        return "road"
    if std_depth < config.water_flatness_threshold:
        return "water"
    return "generic"


def _regularize_building(depth: np.ndarray, mask: np.ndarray, region_depth: np.ndarray, config: MeshPreparationConfig) -> np.ndarray:
    """Regularize building-like regions with plane fitting."""
    rows, cols = np.where(mask)
    if len(rows) < 3:
        return depth
    A = np.column_stack([rows, cols, np.ones(len(rows))])
    coeffs, _, _, _ = np.linalg.lstsq(A, region_depth, rcond=None)
    plane = coeffs[0] * rows + coeffs[1] * cols + coeffs[2]
    alpha = config.region_smoothing
    depth[mask] = (1 - alpha) * region_depth + alpha * plane
    return depth


def _regularize_road(depth: np.ndarray, mask: np.ndarray, region_depth: np.ndarray, config: MeshPreparationConfig) -> np.ndarray:
    """Regularize road-like regions with median filtering."""
    masked_depth = depth.copy()
    masked_depth[~mask] = np.nan
    filtered = _nanmedian_filter(masked_depth, size=5)
    alpha = config.region_smoothing
    depth[mask] = (1 - alpha) * region_depth + alpha * filtered[mask]
    return depth


def _regularize_water(depth: np.ndarray, mask: np.ndarray, region_depth: np.ndarray, config: MeshPreparationConfig) -> np.ndarray:
    """Regularize water-like regions towards flat surface."""
    median_depth = float(np.median(region_depth))
    alpha = config.region_smoothing
    depth[mask] = (1 - alpha) * region_depth + alpha * median_depth
    return depth


def _regularize_generic(depth: np.ndarray, mask: np.ndarray, region_depth: np.ndarray, config: MeshPreparationConfig) -> np.ndarray:
    """Regularize generic regions with Gaussian smoothing."""
    masked_depth = depth.copy()
    masked_depth[~mask] = 0
    filtered = _gaussian_filter_numpy(masked_depth, sigma=1.0)
    weight = _gaussian_filter_numpy(mask.astype(np.float32), sigma=1.0)
    weight = np.maximum(weight, 1e-6)
    filtered = filtered / weight
    alpha = config.region_smoothing * 0.5
    depth[mask] = (1 - alpha) * region_depth + alpha * filtered[mask]
    return depth


def _suppress_spikes(depth: np.ndarray, region_map: np.ndarray, config: MeshPreparationConfig) -> np.ndarray:
    """Suppress isolated depth spikes outside regions."""
    background = region_map == 0
    if not background.any():
        return depth
    filtered = _median_filter_numpy(depth, size=3)
    depth[background] = filtered[background]
    return depth


def _nanmedian_filter(arr: np.ndarray, size: int = 5) -> np.ndarray:
    """Median filter that handles NaN values using numpy only."""
    pad = size // 2
    padded = np.pad(arr, pad, mode="edge")
    result = np.zeros_like(arr)
    for i in range(arr.shape[0]):
        for j in range(arr.shape[1]):
            window = padded[i:i+size, j:j+size]
            valid = window[~np.isnan(window)]
            result[i, j] = np.median(valid) if len(valid) > 0 else np.nan
    return result


def compute_mesh_statistics(depth: np.ndarray) -> dict:
    """Compute basic mesh statistics for validation."""
    finite = depth[np.isfinite(depth)]
    if len(finite) == 0:
        return {"min": None, "max": None, "mean": None, "std": None, "valid_pixels": 0, "total_pixels": int(depth.size)}
    return {
        "min": float(finite.min()),
        "max": float(finite.max()),
        "mean": float(finite.mean()),
        "std": float(finite.std()),
        "valid_pixels": int(len(finite)),
        "total_pixels": int(depth.size),
    }