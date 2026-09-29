"""Model-agnostic remote-sensing height estimation interface — an
ARCHITECTURAL EXTENSION POINT ONLY. There is no concrete implementation of
this interface anywhere in the codebase yet, and `ai/registry.py`'s
`get_rs_height_estimator()` deliberately raises `NotImplementedError` rather
than constructing anything.

Independent of the backend/FastAPI layer by design (see docs/ARCHITECTURE.md)
— the backend would import this module, never the reverse, exactly like
`ai.depth_estimator` and `ai.semantic_estimator`.

## Why this exists now, with nothing behind it

TERRAIN-X's generic depth pipeline (`ai/depth_anything.py`, Depth Anything V2
Small) was benchmarked against the GAMUS remote-sensing height dataset and
found to carry essentially no whole-image linear relationship to real AGL
height on the one tile tested (Pearson r = -0.072, R² = 0.0052 — see
`storage/validation/gamus/DC_03_26_validation.json`). A follow-up,
MobileSAM-region-conditioned diagnostic found much stronger LOCAL
correlation specifically within building-majority regions (median |r| =
0.661 across 35 regions / 56,861 pixels on that same single tile — see
`storage/validation/gamus/DC_03_26_region_correlation.json`/`_report.md`).
**That finding is single-tile, unreplicated evidence only** (a second GAMUS
tile was not available locally to attempt replication) — it motivates
keeping this extension point ready, not any conclusion that a specific
model or calibration strategy is validated. See
`docs/ARCHITECTURE_NOTE_RS_HEIGHT.md` for the full research trail and why
no production calibration currently depends on MobileSAM regions.

## Why this is a SIBLING interface, not a `DepthEstimator` subclass

Same reasoning `ai/semantic_estimator.py` already documents for MobileSAM:
forcing two genuinely different output semantics into one shared interface
would blur what each model actually produces. Concretely:

- `DepthEstimator.predict()` returns relative, unitless, scale-ambiguous
  inverse depth — explicitly NOT metric, by construction (see
  `ai/depth_estimator.py`'s own docstring and
  `RELATIVE_DEPTH_VALUE_SEMANTICS` in `app/services/depth_pipeline.py`).
- A future `RemoteSensingHeightEstimator` implementation is expected to
  target AGL/nDSM-style height, trained/adapted specifically for
  nadir/aerial remote-sensing imagery rather than generic ground-level
  photography. Its output may or may not be metric depending on the
  specific model chosen later — this interface does NOT presuppose an
  answer.

Because that answer isn't known yet (no model has been selected, let alone
implemented), `RemoteSensingHeightPrediction` deliberately does NOT claim a
fixed unit or calibration status. A concrete implementation's own `info()`
and documentation MUST state its own exact value semantics (relative vs.
metric AGL vs. nDSM, valid range, nodata convention, ...) — callers must
read that from the concrete class, never assume "height" implies metric or
validated ground truth. This mirrors how `ai/depth_estimator.py` documents
Depth Anything's specific "larger = closer, inverse, relative" convention
in ITS docstring rather than the interface pretending to define it.

## Explicit non-goals of this module

- Does not download, train, or run any model.
- Does not decide whether a future implementation's output becomes a new
  `analysis_artifacts.artifact_type` in production, or how calibration
  would consume it — those are real design decisions for when a concrete,
  validated model actually exists (see docs/ARCHITECTURE_NOTE_RS_HEIGHT.md
  §"Remaining work").
- Does not use GAMUS CLS or MobileSAM regions as an inference-time input —
  a real implementation's own architecture decides its inputs; the GAMUS
  region-correlation diagnostic that motivated this interface used CLS and
  MobileSAM strictly as an EVALUATION-side tool, never as model input (see
  `geospatial/gamus_semantic_validation.py`'s scientific-honesty rule).
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class RemoteSensingHeightModelInfo:
    """Static facts about a loaded remote-sensing height model — safe to
    persist verbatim as job/artifact metadata, exactly like
    `DepthModelInfo`/`SemanticModelInfo`. A concrete implementation's `task`
    string MUST state its own real output semantics (e.g. "monocular AGL
    height estimation, metric meters" or "relative nDSM-like height,
    uncalibrated") — never left generic enough to imply a guarantee this
    interface itself does not make."""

    name: str
    task: str
    source: str
    revision: str
    license: str
    device: str


@dataclass(frozen=True)
class RemoteSensingHeightPrediction:
    """The real result of one inference call. Shaped like `DepthPrediction`
    (a single continuous `(H, W)` array plus timing/dimension metadata) —
    NOT like `SemanticPrediction`'s categorical label map, since height
    estimation is a continuous-value task exactly like depth estimation.

    `height`'s value semantics (units, relative vs. metric, valid range,
    nodata convention) are defined by the concrete implementation's own
    `info().task` and documentation — NOT by this dataclass, which
    deliberately makes no claim about them. A caller must never assume
    `height` is metric, calibrated, or validated ground truth merely
    because the field is named `height` rather than `depth`.
    """

    height: np.ndarray  # float32, shape (input_height, input_width)
    inference_seconds: float
    input_width: int
    input_height: int
    model_input_width: int
    model_input_height: int


class RemoteSensingHeightEstimator(ABC):
    """Sibling interface to `ai.depth_estimator.DepthEstimator` and
    `ai.semantic_estimator.SemanticEstimator` — see this module's docstring
    for why it is a sibling, not a subclass. No concrete implementation
    exists yet; see `ai/registry.py::get_rs_height_estimator()`.

    A future implementation should keep the same conventions the other two
    estimators already establish: `load()` idempotent/cheap once already
    loaded, real pinned model revision/checksum verification (see
    `ai/depth_anything.py`'s HF revision pin and `ai/mobile_sam.py`'s git
    blob SHA verification for the two existing patterns to follow), and
    `ai.exceptions.ModelLoadError`/`InferenceError`/`UnsupportedInputError`
    for its real, anticipated failure modes rather than inventing new
    exception types.
    """

    @abstractmethod
    def load(self) -> None:
        """Ensures the model/weights are loaded and ready. Must be
        idempotent/cheap on repeated calls once already loaded (see
        `DepthEstimator.load`/`SemanticEstimator.load`). Must raise
        `ai.exceptions.ModelLoadError` with a clear message — never
        fabricate a result — if weights cannot be obtained or loaded."""

    @abstractmethod
    def predict(self, rgb_image: np.ndarray) -> RemoteSensingHeightPrediction:
        """rgb_image: (height, width, 3) uint8 array — the same input
        contract `DepthEstimator.predict`/`SemanticEstimator.predict`
        already use, so a future implementation can reuse
        `app/services/depth_pipeline.py::extract_rgb_uint8`'s existing
        band/dtype policy unchanged rather than inventing a second one.
        Raises `ai.exceptions.UnsupportedInputError`/`InferenceError` on
        failure. Must be called after `load()`."""

    @abstractmethod
    def info(self) -> RemoteSensingHeightModelInfo:
        """Static model metadata, valid whether or not `load()` has been
        called yet — see `RemoteSensingHeightModelInfo`'s docstring for the
        requirement that `task` state this implementation's own real
        output semantics."""
