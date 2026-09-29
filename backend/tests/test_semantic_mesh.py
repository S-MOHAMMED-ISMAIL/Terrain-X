"""Tests for semantic-aware mesh preparation."""

import numpy as np
import pytest

from geospatial.semantic_mesh import (
    MeshPreparationConfig,
    compute_mesh_statistics,
    prepare_semantic_mesh,
)


class TestMeshPreparationConfig:
    def test_default_config(self):
        config = MeshPreparationConfig()
        assert config.region_smoothing == 0.5
        assert config.spike_threshold == 2.0
        assert config.min_region_area == 50
        assert config.building_min_area == 200
        assert config.road_aspect_ratio == 3.0
        assert config.water_flatness_threshold == 0.1


class TestPrepareSemanticMesh:
    def test_empty_regions(self):
        depth = np.ones((10, 10), dtype=np.float32)
        region_map = np.zeros((10, 10), dtype=np.uint32)
        result = prepare_semantic_mesh(depth, region_map)
        assert result.depth.shape == (10, 10)
        assert result.region_stats == []

    def test_single_region(self):
        depth = np.ones((10, 10), dtype=np.float32) * 5.0
        region_map = np.ones((10, 10), dtype=np.uint32)
        result = prepare_semantic_mesh(depth, region_map)
        assert result.depth.shape == (10, 10)
        assert len(result.region_stats) == 1
        assert result.region_stats[0]["region_id"] == 1
        assert result.region_stats[0]["area"] == 100

    def test_small_region_filtered(self):
        depth = np.ones((10, 10), dtype=np.float32)
        region_map = np.zeros((10, 10), dtype=np.uint32)
        region_map[0, 0] = 1
        result = prepare_semantic_mesh(depth, region_map)
        assert result.region_stats == []

    def test_building_region_detection(self):
        depth = np.ones((20, 20), dtype=np.float32) * 5.0
        region_map = np.zeros((20, 20), dtype=np.uint32)
        region_map[5:15, 5:15] = 1
        config = MeshPreparationConfig(building_min_area=50)
        result = prepare_semantic_mesh(depth, region_map, config)
        assert len(result.region_stats) == 1
        assert result.region_stats[0]["region_type"] == "building"

    def test_road_region_detection(self):
        depth = np.ones((30, 10), dtype=np.float32) * 5.0
        region_map = np.zeros((30, 10), dtype=np.uint32)
        region_map[5:25, 2:8] = 1
        config = MeshPreparationConfig(road_aspect_ratio=2.0, min_region_area=10)
        result = prepare_semantic_mesh(depth, region_map, config)
        assert len(result.region_stats) == 1
        assert result.region_stats[0]["region_type"] == "road"

    def test_water_region_detection(self):
        depth = np.ones((20, 20), dtype=np.float32) * 5.0
        region_map = np.zeros((20, 20), dtype=np.uint32)
        region_map[5:15, 5:15] = 1
        config = MeshPreparationConfig(water_flatness_threshold=0.5, min_region_area=50)
        result = prepare_semantic_mesh(depth, region_map, config)
        assert len(result.region_stats) == 1
        assert result.region_stats[0]["region_type"] == "water"

    def test_spike_suppression(self):
        depth = np.ones((10, 10), dtype=np.float32) * 5.0
        depth[5, 5] = 100.0
        region_map = np.zeros((10, 10), dtype=np.uint32)
        result = prepare_semantic_mesh(depth, region_map)
        assert result.depth[5, 5] < 100.0

    def test_preserves_original_depth(self):
        depth = np.ones((10, 10), dtype=np.float32) * 5.0
        region_map = np.ones((10, 10), dtype=np.uint32)
        result = prepare_semantic_mesh(depth, region_map)
        np.testing.assert_array_equal(depth, np.ones((10, 10), dtype=np.float32) * 5.0)


class TestComputeMeshStatistics:
    def test_empty_array(self):
        stats = compute_mesh_statistics(np.array([]))
        assert stats["min"] is None
        assert stats["max"] is None

    def test_normal_array(self):
        depth = np.array([1.0, 2.0, 3.0, 4.0, 5.0], dtype=np.float32)
        stats = compute_mesh_statistics(depth)
        assert stats["min"] == 1.0
        assert stats["max"] == 5.0
        assert stats["mean"] == 3.0
        assert stats["valid_pixels"] == 5

    def test_with_nodata(self):
        depth = np.array([1.0, 2.0, np.nan, 4.0], dtype=np.float32)
        stats = compute_mesh_statistics(depth)
        assert stats["valid_pixels"] == 3
        assert stats["total_pixels"] == 4