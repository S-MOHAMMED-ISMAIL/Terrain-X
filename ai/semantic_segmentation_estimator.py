"""Remote-sensing semantic segmentation estimator interface.

This module defines the interface for a semantic segmentation model that can
produce remote-sensing classes (building, road, tree, water, vegetation, ground).

CRITICAL: No concrete implementation exists yet. The interface is defined so
that a future model can be integrated without changing the architecture.

For now, the system uses MobileSAM's class-agnostic regions with region-based
regularization (see geospatial/semantic_mesh.py) to improve visualization
coherence. This is NOT semantic classification.

See docs/SEMANTIC_3D_RECONSTRUCTION.md for the full architecture and limitations.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class SemanticClassInfo:
    """Information about a semantic class."""

    class_id: int
    name: str
    color: tuple[int, int, int]  # RGB color for visualization
    description: str


@dataclass(frozen=True)
class SemanticSegmentationModelInfo:
    """Static facts about the loaded semantic segmentation model."""

    name: str
    task: str
    repository: str
    revision: str
    checkpoint: str
    license: str
    device: str
    classes: tuple[SemanticClassInfo, ...]


@dataclass(frozen=True)
class SemanticSegmentationPrediction:
    """The result of one semantic segmentation inference.

    class_map is a categorical raster where each pixel is assigned a class ID.
    confidence is the model's confidence score for each pixel (if available).
    """

    class_map: np.ndarray  # uint8, shape (height, width)
    confidence: np.ndarray | None  # float32, shape (height, width), None if not available
    inference_seconds: float
    input_width: int
    input_height: int
    model_input_width: int
    model_input_height: int


class SemanticSegmentationEstimator(ABC):
    """Interface for remote-sensing semantic segmentation models.

    A concrete implementation must:
    1. Load a model that can produce remote-sensing classes
    2. Accept (H, W, 3) uint8 RGB input
    3. Return a class map with the model's native class IDs
    4. Provide model metadata including class definitions

    The model must be trained for remote-sensing imagery (aerial/nadir),
    not ground-level photography.
    """

    @abstractmethod
    def load(self) -> None:
        """Load the model/weights. Must be idempotent/cheap once loaded."""
        ...

    @abstractmethod
    def predict(self, rgb_image: np.ndarray) -> SemanticSegmentationPrediction:
        """Run semantic segmentation on an RGB image.

        Args:
            rgb_image: (height, width, 3) uint8 array

        Returns:
            SemanticSegmentationPrediction with class map and metadata
        """
        ...

    @abstractmethod
    def info(self) -> SemanticSegmentationModelInfo:
        """Return static model metadata."""
        ...


# Standard remote-sensing semantic classes (based on GAMUS dataset)
STANDARD_SEMANTIC_CLASSES: tuple[SemanticClassInfo, ...] = (
    SemanticClassInfo(0, "BACKGROUND", (0, 0, 0), "Background / unclassified"),
    SemanticClassInfo(1, "GROUND", (139, 119, 101), "Ground / terrain"),
    SemanticClassInfo(2, "LOW_VEGETATION", (107, 142, 35), "Low vegetation / grass"),
    SemanticClassInfo(3, "BUILDING", (220, 20, 60), "Building / structure"),
    SemanticClassInfo(4, "WATER", (0, 105, 148), "Water body"),
    SemanticClassInfo(5, "ROAD", (128, 128, 128), "Road / path"),
    SemanticClassInfo(6, "TREE", (34, 139, 34), "Tree / canopy"),
)


def get_semantic_segmentation_estimator() -> SemanticSegmentationEstimator:
    """Get the registered semantic segmentation estimator.

    Raises:
        NotImplementedError: Always, until a concrete implementation is registered.
    """
    raise NotImplementedError(
        "No SemanticSegmentationEstimator implementation is registered yet. "
        "See docs/SEMANTIC_3D_RECONSTRUCTION.md for the interface a future "
        "remote-sensing semantic segmentation model must implement."
    )