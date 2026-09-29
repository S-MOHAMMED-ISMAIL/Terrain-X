"""Model-agnostic class-agnostic region-segmentation interface.

Independent of the backend/FastAPI layer by design (see docs/ARCHITECTURE.md)
— the backend imports this module, never the reverse. Concrete model
implementations (e.g. `ai.mobile_sam.MobileSAMEstimator`) implement this
interface; callers depend only on `SemanticEstimator`, `SemanticPrediction`,
`RegionInfo`, and `SemanticModelInfo`, never on a specific model's internals.

Deliberately NOT a reuse/extension of `ai.depth_estimator.DepthEstimator` —
that interface's `DepthPrediction` is shaped for one continuous float array;
a region-segmentation result is fundamentally different (a categorical label
map plus a real list of per-region metadata), and forcing the two into one
shared interface would blur what each model actually produces. See
docs/ARCHITECTURE.md §3.6 for the full rationale.

CRITICAL SEMANTIC RULE: a `SemanticEstimator` implementation must never
report a specific object class (e.g. "building", "road", "vegetation").
Region IDs are arbitrary, class-agnostic identifiers assigned by the
rasterization policy — see `ai.mobile_sam` — not a supervised
classification. Nothing in this interface has a field for a class name,
and no caller may invent one.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class SemanticModelInfo:
    """Static facts about the loaded model — safe to persist verbatim as
    job/artifact metadata."""

    name: str
    task: str  # e.g. "class-agnostic region segmentation" — never a classification task name
    repository: str
    revision: str  # pinned commit SHA or release tag — never a moving branch reference
    checkpoint: str  # the specific checkpoint filename actually loaded
    license: str
    device: str


@dataclass(frozen=True)
class RegionInfo:
    """Real, per-region facts computed from the model's actual output for
    one detected region. `mask_score` is the model's own reported mask
    quality signal (e.g. SAM's `stability_score`) when the implementation
    provides one — `None`, never a fabricated number, when it doesn't.
    """

    # Matches the label_map's pixel value for this region; 0 is reserved for background.
    region_id: int
    # Real count of pixels assigned to this region in the final label map.
    pixel_area: int
    bbox_row_min: int
    bbox_col_min: int
    bbox_row_max: int
    bbox_col_max: int
    # Model-native mask quality (e.g. SAM's stability_score); never invented.
    mask_score: float | None
    # Model-native self-predicted IoU, if the implementation exposes one.
    predicted_iou: float | None


@dataclass(frozen=True)
class SemanticPrediction:
    """The real result of one inference call.

    `label_map` is a categorical raster: 0 = background/unassigned, and each
    distinct positive integer identifies one real detected region — NEVER a
    continuous/interpolatable value. `regions` describes every nonzero ID
    actually present in `label_map`, in the same order region IDs were
    assigned (see the concrete implementation's rasterization-policy
    docstring for exactly how overlaps and ordering are resolved).
    """

    label_map: np.ndarray  # uint32, shape (height, width); 0 = background
    regions: list[RegionInfo]
    inference_seconds: float
    input_width: int
    input_height: int


class SemanticEstimator(ABC):
    @abstractmethod
    def load(self) -> None:
        """Ensures the model/weights are loaded and ready. Must be
        idempotent/cheap on repeated calls once already loaded, so callers
        can call it unconditionally at the start of every job without
        reloading weights each time. Must raise a clear, real error (never
        fabricate a result) if weights cannot be obtained — e.g. no network
        access on first use and no cached copy present."""

    @abstractmethod
    def predict(self, rgb_image: np.ndarray, *, min_region_area_px: int = 0) -> SemanticPrediction:
        """rgb_image: (height, width, 3) uint8 array. `min_region_area_px`
        drops real detected regions smaller than this many pixels *before*
        region IDs are assigned (a real, documented filtering step — never a
        fabricated region). Raises an implementation-specific inference
        error on model failure. Must be called after `load()`."""

    @abstractmethod
    def info(self) -> SemanticModelInfo:
        """Static model metadata, valid whether or not `load()` has been
        called yet (device is decided at construction; name/repository/
        revision/checkpoint/license are compile-time constants of the
        implementation)."""
