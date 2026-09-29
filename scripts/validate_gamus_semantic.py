#!/usr/bin/env python
"""Development/validation-only script: runs TERRAIN-X's existing MobileSAM
region segmentation (`ai.registry.get_semantic_estimator()` — the exact
same `MobileSAMEstimator` used by `app/services/semantic_pipeline.py`) on a
GAMUS RGB tile, and reports how well its region boundaries align with
GAMUS's own CLS class labels using `geospatial.gamus_semantic_validation`.

MobileSAM produces class-agnostic region IDs with NO semantic meaning (see
`ai/semantic_estimator.py`'s interface contract). Nothing in this script
changes that, treats a region id as a predicted class, or persists a
"majority class" as if MobileSAM had predicted it. Every statistic here is
an evaluation-side, after-the-fact comparison of two independently-produced
categorical rasters — see geospatial/gamus_semantic_validation.py's module
docstring for the full scientific-honesty framing.

This is NOT part of the production pipeline, is not called by the backend
or worker, produces no database rows, and does not run inside Docker
(same reasoning as scripts/validate_gamus.py: GAMUS's .h5 files carry no
CRS, are not mounted into the backend/worker containers, and this script
needs a host-side Python environment with this project's `ai`/`geospatial`
runtime dependencies — including torch/torchvision/mobile_sam/h5py).

Usage:
    python scripts/validate_gamus_semantic.py
    python scripts/validate_gamus_semantic.py --rgb path/to/RGB.h5 --cls path/to/CLS.h5
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

# See scripts/validate_gamus.py for why this sys.path setup is needed when
# running this file directly rather than via `python -m`.
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402

from ai.registry import get_semantic_estimator  # noqa: E402
from geospatial.gamus_semantic_validation import (  # noqa: E402
    compute_gamus_semantic_validation,
    validate_gamus_cls_range,
)
from geospatial.gamus_validation import load_gamus_h5  # noqa: E402

DEFAULT_RGB_PATH = (
    REPO_ROOT
    / "TERRAIN-X-TEST-DATA"
    / "03_gamus_validation"
    / "images"
    / "test"
    / "DC_03_26_RGB.h5"
)
DEFAULT_CLS_PATH = (
    REPO_ROOT
    / "TERRAIN-X-TEST-DATA"
    / "03_gamus_validation"
    / "classes"
    / "test"
    / "DC_03_26_CLS.h5"
)
DEFAULT_OUTPUT_PATH = (
    REPO_ROOT / "storage" / "validation" / "gamus" / "DC_03_26_semantic_validation.json"
)

DEFAULT_BOUNDARY_TOLERANCE_PX = 2
# Mirrors app/core/config.Settings.MIN_SEMANTIC_REGION_AREA_PX's default use
# in app/services/semantic_pipeline.py -- restated here (this script
# deliberately does not import backend settings; see scripts/validate_gamus.py
# for the same reasoning) rather than fabricated.
DEFAULT_MIN_REGION_AREA_PX = 0

REPORT_HEADER = "MobileSAM region-boundary alignment against GAMUS CLS ground truth"
SCIENTIFIC_HONESTY_NOTES = (
    "NOT semantic classification",
    "NOT per-class accuracy",
    "MobileSAM region IDs are arbitrary and image-specific",
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rgb", type=Path, default=DEFAULT_RGB_PATH, help="GAMUS RGB .h5 file")
    parser.add_argument("--cls", type=Path, default=DEFAULT_CLS_PATH, help="GAMUS CLS .h5 file")
    parser.add_argument(
        "--output", type=Path, default=DEFAULT_OUTPUT_PATH, help="JSON report output path"
    )
    parser.add_argument(
        "--boundary-tolerance-px",
        type=int,
        default=DEFAULT_BOUNDARY_TOLERANCE_PX,
        help="Pixel tolerance for the boundary-alignment metric",
    )
    parser.add_argument(
        "--min-region-area-px",
        type=int,
        default=DEFAULT_MIN_REGION_AREA_PX,
        help="Drop MobileSAM regions smaller than this many pixels (see ai/mobile_sam.py)",
    )
    return parser.parse_args()


def _validate_and_prepare_rgb(rgb: np.ndarray) -> np.ndarray:
    """Same real, explicit checks as scripts/validate_gamus.py -- never a
    silent reshape/cast."""
    if rgb.ndim != 3 or rgb.shape[2] != 3:
        raise ValueError(f"Expected a (H, W, 3) RGB array; got shape {rgb.shape}")

    if rgb.dtype == np.uint8:
        return rgb

    if not np.issubdtype(rgb.dtype, np.integer) and not np.issubdtype(rgb.dtype, np.floating):
        raise ValueError(f"Cannot convert dtype {rgb.dtype} to uint8 RGB")

    if rgb.min() < 0 or rgb.max() > 255:
        raise ValueError(
            f"RGB values (min={rgb.min()}, max={rgb.max()}) are out of the 0-255 range "
            f"expected for uint8 conversion; refusing to silently clip/rescale."
        )
    return rgb.astype(np.uint8)


def _fmt(value: float | None) -> str:
    return "undefined" if value is None else f"{value:.6f}"


def main() -> None:
    args = _parse_args()

    print(f"Loading GAMUS RGB: {args.rgb}")
    rgb = load_gamus_h5(str(args.rgb))
    rgb = _validate_and_prepare_rgb(rgb)
    print(f"  shape={rgb.shape} dtype={rgb.dtype}")

    print("Loading semantic estimator (ai.registry.get_semantic_estimator)...")
    estimator = get_semantic_estimator()
    estimator.load()
    model_info = estimator.info()
    print(f"  model={model_info.name} task={model_info.task} device={model_info.device}")

    print("Running MobileSAM inference...")
    start = time.monotonic()
    prediction = estimator.predict(rgb, min_region_area_px=args.min_region_area_px)
    inference_seconds = time.monotonic() - start
    label_map = prediction.label_map
    print(f"  done in {inference_seconds:.2f}s, {len(prediction.regions)} region(s) detected")

    print(f"Loading GAMUS CLS: {args.cls}")
    cls = load_gamus_h5(str(args.cls))
    print(f"  shape={cls.shape} dtype={cls.dtype}")

    if rgb.shape[:2] != label_map.shape or label_map.shape != cls.shape:
        raise ValueError(
            f"Spatial dimension mismatch: RGB {rgb.shape[:2]}, MobileSAM label_map "
            f"{label_map.shape}, CLS {cls.shape} must all match for pixel-aligned comparison."
        )

    validate_gamus_cls_range(cls)

    result = compute_gamus_semantic_validation(
        label_map, cls, boundary_tolerance_px=args.boundary_tolerance_px
    )
    ba = result.boundary_alignment

    print()
    print("=" * 70)
    print(REPORT_HEADER)
    print("=" * 70)
    for note in SCIENTIFIC_HONESTY_NOTES:
        print(f"  * {note}")
    print("-" * 70)
    print(f"RGB dimensions:              {rgb.shape}")
    print(f"CLS dimensions:               {cls.shape}")
    print(f"MobileSAM inference seconds:  {inference_seconds:.2f}")
    print(f"Number of regions:            {result.region_count}")
    print(f"Total foreground pixels:      {result.total_foreground_pixels} / {result.total_pixels}")
    print(
        f"Area-weighted mean purity:    {_fmt(result.area_weighted_mean_purity)}"
        + (
            f"  ({result.area_weighted_mean_purity_explicit_reason})"
            if result.area_weighted_mean_purity_explicit_reason
            else ""
        )
    )
    print(
        f"Unweighted mean purity:       {_fmt(result.unweighted_mean_purity)}"
        + (
            f"  ({result.unweighted_mean_purity_explicit_reason})"
            if result.unweighted_mean_purity_explicit_reason
            else ""
        )
    )
    print("-" * 70)
    print(f"Boundary alignment ({ba.method}):")
    print(f"  MobileSAM boundary pixels:  {ba.mobilesam_boundary_pixel_count}")
    print(f"  GAMUS boundary pixels:      {ba.gamus_boundary_pixel_count}")
    print(f"  Precision:                  {_fmt(ba.precision)}")
    print(f"  Recall:                     {_fmt(ba.recall)}")
    print(
        f"  F1:                         {_fmt(ba.f1)}"
        + (f"  ({ba.explicit_reason})" if ba.explicit_reason else "")
    )
    print("-" * 70)
    print("Per-class pixel counts (GAMUS CLS):")
    for summary in result.class_overlap_summaries:
        print(
            f"  class {summary.gamus_class}: {summary.pixel_count} px, "
            f"{summary.touching_region_count} region(s) touch it, "
            f"{summary.background_pixel_count} px under no region"
        )
    print("=" * 70)

    report = {
        "result_label": REPORT_HEADER,
        "scientific_honesty_notes": list(SCIENTIFIC_HONESTY_NOTES),
        "sample_name": args.rgb.stem.replace("_RGB", ""),
        "rgb_path": str(args.rgb),
        "cls_path": str(args.cls),
        "rgb_shape": list(rgb.shape),
        "cls_shape": list(cls.shape),
        "label_map_shape": list(label_map.shape),
        "model_name": model_info.name,
        "model_task": model_info.task,
        "model_repository": model_info.repository,
        "model_revision": model_info.revision,
        "model_device": model_info.device,
        "inference_seconds": inference_seconds,
        "min_region_area_px": args.min_region_area_px,
        "region_count": result.region_count,
        "total_foreground_pixels": result.total_foreground_pixels,
        "total_pixels": result.total_pixels,
        "area_weighted_mean_purity": result.area_weighted_mean_purity,
        "area_weighted_mean_purity_explicit_reason": (
            result.area_weighted_mean_purity_explicit_reason
        ),
        "unweighted_mean_purity": result.unweighted_mean_purity,
        "unweighted_mean_purity_explicit_reason": result.unweighted_mean_purity_explicit_reason,
        "boundary_alignment": {
            "tolerance_px": ba.tolerance_px,
            "method": ba.method,
            "mobilesam_boundary_pixel_count": ba.mobilesam_boundary_pixel_count,
            "gamus_boundary_pixel_count": ba.gamus_boundary_pixel_count,
            "precision": ba.precision,
            "recall": ba.recall,
            "f1": ba.f1,
            "explicit_reason": ba.explicit_reason,
        },
        "region_purities": [
            {
                "region_id": r.region_id,
                "region_area": r.region_area,
                "majority_class": r.majority_class,
                "majority_count": r.majority_count,
                "purity": r.purity,
            }
            for r in result.region_purities
        ],
        "class_overlap_summaries": [
            {
                "gamus_class": s.gamus_class,
                "pixel_count": s.pixel_count,
                "touching_region_count": s.touching_region_count,
                "region_pixel_counts": s.region_pixel_counts,
                "background_pixel_count": s.background_pixel_count,
            }
            for s in result.class_overlap_summaries
        ],
        "overlap_matrix": {
            "label": result.overlap_matrix.label,
            "region_ids": result.overlap_matrix.region_ids,
            "gamus_classes": result.overlap_matrix.gamus_classes,
            "matrix": result.overlap_matrix.matrix,
        },
        "limitations": (
            "This is a development/validation-only, evaluation-side comparison of "
            "MobileSAM's class-agnostic region boundaries against GAMUS CLS ground "
            "truth. 'majority_class' per region is a post-hoc statistic computed by "
            "this script, NOT a MobileSAM prediction; MobileSAM's own output "
            "(label_map, region ids) is completely unchanged and carries no semantic "
            "meaning anywhere else in this project (see ai/semantic_estimator.py). "
            "The overlap matrix is a pixel crosstab of two independently-produced "
            "categorical rasters, not a classifier confusion matrix. The boundary "
            "alignment metric measures only whether the two rasters change value in "
            "similar places, not whether the values on either side agree."
        ),
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2))
    print(f"\nJSON report written to: {args.output}")


if __name__ == "__main__":
    main()
