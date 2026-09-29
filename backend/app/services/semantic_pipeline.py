"""Phase 6 backend-side orchestration for real class-agnostic region
segmentation (MobileSAM). Model mechanics live in ai/mobile_sam.py (pure,
backend-independent); this module loads the estimator, calls into it, and
turns the result into the job's semantic_status/semantic_metadata — the
same separation of concerns, and the same never-raises soft-failure
contract, as app/services/calibration_pipeline.py.
"""

import logging
import time
from dataclasses import dataclass

import numpy as np

from ai.exceptions import InferenceError, ModelLoadError, UnsupportedInputError
from ai.mobile_sam import POINTS_PER_SIDE
from ai.registry import get_semantic_estimator
from app.core.config import Settings
from app.models.analysis_job import SemanticStatus

logger = logging.getLogger("terrainx.backend.semantic")

# Verbatim, persisted into every successful semantic artifact's metadata so
# a consumer reading only the artifact — without this module's source —
# still sees the critical scientific caveat.
REGION_VALUE_SEMANTICS = (
    "Each nonzero label_map value is an arbitrary region ID assigned by real "
    "class-agnostic segmentation (MobileSAM) — it does NOT correspond to a "
    "semantic land-cover or object class such as 'building', 'road', or "
    "'vegetation'. Region boundaries reflect the model's own visual grouping "
    "of the source image only; they are not validated against any real-world "
    "ground truth in this project. See docs/ARCHITECTURE.md §3.6."
)


@dataclass
class SemanticOutcome:
    status: SemanticStatus
    metadata: dict
    label_map: np.ndarray | None = None


def run_semantic_segmentation(rgb_image: np.ndarray, *, settings: Settings) -> SemanticOutcome:
    """Runs the full segmentation attempt and always returns a
    SemanticOutcome (never raises) — every anticipated failure mode becomes
    a real, informative `SemanticOutcome(status=FAILED, metadata={"error": ...})`,
    never an exception the caller must also handle.
    """
    start = time.monotonic()
    estimator = get_semantic_estimator()

    try:
        estimator.load()
        model_info = estimator.info()
        prediction = estimator.predict(
            rgb_image, min_region_area_px=settings.MIN_SEMANTIC_REGION_AREA_PX
        )
    except (ModelLoadError, UnsupportedInputError, InferenceError) as exc:
        return SemanticOutcome(status=SemanticStatus.FAILED, metadata={"error": str(exc)})
    except Exception:
        logger.exception("Unexpected error during semantic segmentation")
        return SemanticOutcome(
            status=SemanticStatus.FAILED,
            metadata={"error": "An internal error occurred during semantic segmentation."},
        )

    total_seconds = time.monotonic() - start

    region_scores = [r.mask_score for r in prediction.regions if r.mask_score is not None]
    region_ious = [r.predicted_iou for r in prediction.regions if r.predicted_iou is not None]

    metadata = {
        "model_name": model_info.name,
        "model_task": model_info.task,
        "model_repository": model_info.repository,
        "model_revision": model_info.revision,
        "model_checkpoint": model_info.checkpoint,
        "model_license": model_info.license,
        "device": model_info.device,
        "points_per_side": POINTS_PER_SIDE,
        "min_region_area_px": settings.MIN_SEMANTIC_REGION_AREA_PX,
        "inference_seconds": prediction.inference_seconds,
        "total_seconds": total_seconds,
        "output_width": prediction.input_width,
        "output_height": prediction.input_height,
        "region_count": len(prediction.regions),
        "regions": [
            {
                "region_id": r.region_id,
                "pixel_area": r.pixel_area,
                "bbox_row_min": r.bbox_row_min,
                "bbox_col_min": r.bbox_col_min,
                "bbox_row_max": r.bbox_row_max,
                "bbox_col_max": r.bbox_col_max,
                "mask_score": r.mask_score,
                "predicted_iou": r.predicted_iou,
            }
            for r in prediction.regions
        ],
        # Real, model-native mask-quality summary — labeled explicitly as
        # what it is (SAM's own stability_score), never "confidence" or
        # "accuracy". None when the model provided no such value for any
        # region, rather than a fabricated placeholder.
        "mask_stability_mean": float(np.mean(region_scores)) if region_scores else None,
        "mask_stability_min": float(np.min(region_scores)) if region_scores else None,
        "mask_stability_max": float(np.max(region_scores)) if region_scores else None,
        "predicted_iou_mean": float(np.mean(region_ious)) if region_ious else None,
        "value_semantics": REGION_VALUE_SEMANTICS,
    }

    return SemanticOutcome(
        status=SemanticStatus.COMPLETED, metadata=metadata, label_map=prediction.label_map
    )
