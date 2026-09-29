"""Integration of semantic-aware mesh preparation with the existing terrain grid.

This module provides a drop-in replacement for the standard terrain grid
extraction that applies semantic-aware regularization to the depth data.

The original raw relative depth artifact remains untouched. This is purely a
visualization mesh preparation step.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import rasterio

from geospatial.semantic_mesh import (
    MeshPreparationConfig,
    MeshPreparationResult,
    compute_mesh_statistics,
    prepare_semantic_mesh,
)


@dataclass(frozen=True)
class SemanticTerrainGrid:
    """Terrain grid with semantic-aware regularization applied."""

    width: int
    height: int
    source_width: int
    source_height: int
    elevations: np.ndarray  # Regularized depth (float32)
    raw_elevations: np.ndarray  # Original raw depth (float32)
    region_map: np.ndarray  # MobileSAM region map (uint32)
    min_elevation: float | None
    max_elevation: float | None
    nodata_present: bool
    crs: str | None
    is_georeferenced: bool
    local_crs: str | None
    origin_x: float | None
    origin_y: float | None
    cell_size_x: float | None
    cell_size_y: float | None
    bounds: tuple[float, float, float, float] | None
    preparation_result: MeshPreparationResult


def extract_semantic_terrain_grid(
    depth_path: Path,
    region_map: np.ndarray,
    config: MeshPreparationConfig | None = None,
) -> SemanticTerrainGrid:
    """Extract a terrain grid with semantic-aware regularization.

    Args:
        depth_path: Path to the raw relative depth raster
        region_map: MobileSAM region map (H, W), uint32
        config: Mesh preparation configuration

    Returns:
        SemanticTerrainGrid with regularized depth and metadata
    """
    from geospatial.terrain_grid import extract_terrain_grid

    # Extract the standard terrain grid
    grid = extract_terrain_grid(depth_path)

    # Apply semantic-aware regularization
    preparation = prepare_semantic_mesh(
        depth=grid.elevations,
        region_map=region_map,
        config=config,
    )

    # Compute statistics
    stats = compute_mesh_statistics(preparation.depth)

    return SemanticTerrainGrid(
        width=grid.width,
        height=grid.height,
        source_width=grid.source_width,
        source_height=grid.source_height,
        elevations=preparation.depth,
        raw_elevations=grid.elevations,
        region_map=preparation.region_map,
        min_elevation=stats["min"],
        max_elevation=stats["max"],
        nodata_present=grid.nodata_present,
        crs=grid.crs,
        is_georeferenced=grid.is_georeferenced,
        local_crs=grid.local_crs,
        origin_x=grid.origin_x,
        origin_y=grid.origin_y,
        cell_size_x=grid.cell_size_x,
        cell_size_y=grid.cell_size_y,
        bounds=grid.bounds,
        preparation_result=preparation,
    )