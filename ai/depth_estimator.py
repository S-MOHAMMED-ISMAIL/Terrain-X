"""Model-agnostic monocular depth estimation interface.

Independent of the backend/FastAPI layer by design (see docs/ARCHITECTURE.md)
— the backend imports this module, never the reverse. Concrete model
implementations (e.g. `ai.depth_anything.DepthAnythingV2Estimator`) implement
this interface; callers depend only on `DepthEstimator`, `DepthPrediction`,
and `DepthModelInfo`, never on a specific model's internals.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class DepthModelInfo:
    """Static facts about the loaded model — safe to persist verbatim as
    job/artifact metadata."""

    name: str
    revision: str
    source: str
    license: str
    device: str


@dataclass(frozen=True)
class DepthPrediction:
    """The real result of one inference call.

    `depth` is the model's raw relative-depth output, resized back to the
    source image's resolution (so every pixel corresponds 1:1 with the
    source image), and is NEVER normalized/clipped for storage — only a
    separate visualization would do that. See docs/ARCHITECTURE.md for the
    exact value semantics (which direction is "closer").
    """

    depth: np.ndarray  # float32, shape (input_height, input_width)
    inference_seconds: float
    input_width: int
    input_height: int
    model_input_width: int
    model_input_height: int


class DepthEstimator(ABC):
    @abstractmethod
    def load(self) -> None:
        """Ensures the model/processor are loaded and ready. Must be
        idempotent/cheap on repeated calls once already loaded, so callers
        can call it unconditionally at the start of every job without
        reloading multi-hundred-MB weights each time."""

    @abstractmethod
    def predict(self, rgb_image: np.ndarray) -> DepthPrediction:
        """rgb_image: (height, width, 3) uint8 array. Raises InferenceError
        on failure. Must be called after `load()`."""

    @abstractmethod
    def info(self) -> DepthModelInfo:
        """Static model metadata, valid whether or not `load()` has been
        called yet (device is decided at construction; name/revision/source/
        license are compile-time constants of the implementation)."""
