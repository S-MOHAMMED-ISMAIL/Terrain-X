"""Depth Anything V2 (Small) monocular depth estimator.

Model: depth-anything/Depth-Anything-V2-Small-hf
  - Source: https://huggingface.co/depth-anything/Depth-Anything-V2-Small-hf
    (the official `transformers`-compatible port of Depth Anything V2's
    Small checkpoint, published by the model's authors)
  - Paper/project: https://depth-anything-v2.github.io/
  - License: Apache-2.0 (the Small checkpoint specifically — Depth Anything
    V2's Base/Large checkpoints are CC-BY-NC-4.0 and are deliberately NOT
    used here to keep this project's dependencies unambiguously permissive)
  - Architecture: DPT (dense prediction transformer) head over a small
    DINOv2 backbone.
  - Expected input: a single RGB image, arbitrary resolution (the processor
    resizes internally for the model; the prediction is resized back to the
    source resolution before being returned — see DepthPrediction).
  - Output semantics: relative, unitless, *inverse* depth ("disparity-like")
    — LARGER values mean CLOSER to the camera, SMALLER values mean FARTHER.
    This is the model's native convention (inherited from MiDaS-style
    monocular depth training) and is NOT inverted here. It is emphatically
    NOT metric elevation, NOT a DSM, and has undergone no calibration.
  - Known limitations: monocular depth is scale-ambiguous (no absolute unit
    without external calibration — see Phase 4+), trained predominantly on
    ground-level/indoor-outdoor photographic scenes rather than nadir
    remote-sensing imagery, and can produce artifacts on textureless
    regions, reflective surfaces, or scenes far outside its training
    distribution (which aerial/satellite imagery often is).

Weights are downloaded from the Hugging Face Hub on first use and cached
under the `HF_HOME` directory (see docker-compose.yml / docs/ARCHITECTURE.md)
so subsequent loads — including after a container restart — are local and
fast. This requires outbound network access the first time only; if that
access is unavailable, `load()` raises ModelLoadError with a clear message
rather than leaving a job stuck.
"""

import logging
import time

import numpy as np

from ai.depth_estimator import DepthEstimator, DepthModelInfo, DepthPrediction
from ai.device import select_device
from ai.exceptions import InferenceError, ModelLoadError, UnsupportedInputError

logger = logging.getLogger("terrainx.ai.depth_anything")

MODEL_NAME = "depth-anything/Depth-Anything-V2-Small-hf"
# Pinned to an exact, immutable commit SHA (never a moving branch reference
# like "main") for reproducibility — Phase 11 hardening. This is the real
# commit that "main" resolved to at the time of pinning, confirmed two ways:
# (1) it is the exact commit already present in this project's local
# Hugging Face cache (storage/model_cache/hub/models--depth-anything--
# Depth-Anything-V2-Small-hf/refs/main), i.e. the commit every prior phase's
# testing — including Phase 10's real browser acceptance — actually ran
# against; (2) independently confirmed live against the Hugging Face Hub API
# (`GET /api/models/depth-anything/Depth-Anything-V2-Small-hf/revision/main`)
# at pin time, which returned this same SHA. Operators changing this should
# re-verify the model card's license terms still apply.
MODEL_REVISION = "5426e4f0f36572d16453bbda7a8389317b1bef99"
MODEL_SOURCE = f"https://huggingface.co/{MODEL_NAME}"
MODEL_LICENSE = "Apache-2.0"


class DepthAnythingV2Estimator(DepthEstimator):
    def __init__(self) -> None:
        self._model = None
        self._processor = None
        self._device = select_device()

    def load(self) -> None:
        if self._model is not None and self._processor is not None:
            return  # already loaded in this worker process — cheap no-op

        try:
            from transformers import AutoImageProcessor, AutoModelForDepthEstimation

            logger.info("Loading depth model %s (revision=%s)", MODEL_NAME, MODEL_REVISION)
            processor = AutoImageProcessor.from_pretrained(MODEL_NAME, revision=MODEL_REVISION)
            model = AutoModelForDepthEstimation.from_pretrained(MODEL_NAME, revision=MODEL_REVISION)
            model.to(self._device)
            model.eval()
        except Exception as exc:
            raise ModelLoadError(f"Failed to load depth model '{MODEL_NAME}': {exc}") from exc

        self._processor = processor
        self._model = model

    def predict(self, rgb_image: np.ndarray) -> DepthPrediction:
        if self._model is None or self._processor is None:
            raise InferenceError("predict() called before load()")
        if rgb_image.ndim != 3 or rgb_image.shape[2] != 3:
            raise UnsupportedInputError(
                f"Expected an (H, W, 3) RGB array, got shape {rgb_image.shape}"
            )

        import torch
        from PIL import Image

        height, width = rgb_image.shape[0], rgb_image.shape[1]

        try:
            pil_image = Image.fromarray(rgb_image, mode="RGB")
            inputs = self._processor(images=pil_image, return_tensors="pt")
            model_input_height = int(inputs["pixel_values"].shape[-2])
            model_input_width = int(inputs["pixel_values"].shape[-1])
            inputs = {key: value.to(self._device) for key, value in inputs.items()}

            start = time.monotonic()
            with torch.inference_mode():
                outputs = self._model(**inputs)
                predicted = outputs.predicted_depth  # (1, h', w')
                resized = torch.nn.functional.interpolate(
                    predicted.unsqueeze(1),
                    size=(height, width),
                    mode="bicubic",
                    align_corners=False,
                ).squeeze(1)
            inference_seconds = time.monotonic() - start

            depth_array = resized.squeeze(0).cpu().numpy().astype(np.float32)
        except (UnsupportedInputError, InferenceError):
            raise
        except Exception as exc:
            raise InferenceError(f"Depth inference failed: {exc}") from exc

        return DepthPrediction(
            depth=depth_array,
            inference_seconds=inference_seconds,
            input_width=width,
            input_height=height,
            model_input_width=model_input_width,
            model_input_height=model_input_height,
        )

    def info(self) -> DepthModelInfo:
        return DepthModelInfo(
            name=MODEL_NAME,
            revision=MODEL_REVISION,
            source=MODEL_SOURCE,
            license=MODEL_LICENSE,
            device=self._device,
        )
