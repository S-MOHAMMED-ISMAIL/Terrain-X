#!/usr/bin/env python
"""Development/validation-only script: runs TERRAIN-X's existing monocular
depth pipeline (`ai.registry.get_depth_estimator()` — the exact same
`DepthAnythingV2Estimator` used by `app/services/analysis_execution.py`) on a
GAMUS RGB tile, and scores the resulting relative-depth prediction against
GAMUS's own AGL ("above ground level") height ground truth using
`geospatial.gamus_validation` (which in turn reuses the real
`geospatial.calibration` robust-affine-fit/validation-metrics logic).

This is NOT part of the production pipeline, is not called by the backend or
worker, produces no database rows, and does not run inside Docker (GAMUS RGB
and AGL are plain HDF5 files with no CRS/geotransform — see
geospatial/gamus_validation.py's module docstring for why this is
deliberately kept separate from the real DEM/GCP calibration flow). Run it
directly from the repo root with a Python environment that has this
project's `ai`/`geospatial` runtime dependencies installed (numpy, h5py,
rasterio, torch, transformers — see docs/DEVELOPMENT.md; the backend's
Docker image does not currently include h5py, and does not have access to
GAMUS test data or this `scripts/` directory, so this script targets a
host-side Python environment, not `docker compose exec`).

Usage:
    python scripts/validate_gamus.py
    python scripts/validate_gamus.py --rgb path/to/RGB.h5 --agl path/to/AGL.h5
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

# scripts/ is a sibling of ai/, geospatial/, and backend/ at the repo root,
# but running this file directly (not via `python -m`) only puts scripts/
# itself on sys.path -- the same gotcha docs/DEVELOPMENT.md already documents
# for pytest/uvicorn. Fixed here explicitly rather than requiring every
# invocation to set PYTHONPATH by hand. `backend/` is added too, only to
# reuse `app.services.depth_pipeline.RELATIVE_DEPTH_VALUE_SEMANTICS` (a
# plain string constant, no FastAPI/SQLAlchemy import behind it) so this
# script's JSON report carries the exact same relative-depth disclaimer text
# as a real production artifact, never a re-worded copy of it.
REPO_ROOT = Path(__file__).resolve().parent.parent
for path in (REPO_ROOT, REPO_ROOT / "backend"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import numpy as np  # noqa: E402
from app.services.depth_pipeline import RELATIVE_DEPTH_VALUE_SEMANTICS  # noqa: E402

from ai.registry import get_depth_estimator  # noqa: E402
from geospatial.gamus_validation import (  # noqa: E402
    build_gamus_valid_mask,
    fit_and_validate_gamus,
    load_gamus_h5,
)

DEFAULT_RGB_PATH = (
    REPO_ROOT
    / "TERRAIN-X-TEST-DATA"
    / "03_gamus_validation"
    / "images"
    / "test"
    / "DC_03_26_RGB.h5"
)
DEFAULT_AGL_PATH = (
    REPO_ROOT
    / "TERRAIN-X-TEST-DATA"
    / "03_gamus_validation"
    / "heights"
    / "test"
    / "DC_03_26_AGL.h5"
)
DEFAULT_OUTPUT_PATH = REPO_ROOT / "storage" / "validation" / "gamus" / "DC_03_26_validation.json"

# Mirrors app/core/config.Settings.CALIBRATION_OUTLIER_SIGMA /
# CALIBRATION_MAX_ITERATIONS -- this script deliberately does not import
# backend settings (it must run outside the backend's environment/Docker
# image), so the same defaults are restated here rather than fabricated.
DEFAULT_OUTLIER_SIGMA = 2.5
DEFAULT_MAX_ITERATIONS = 5

RESULT_LABEL = "GAMUS pixel-aligned validation of monocular relative depth against AGL ground truth"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rgb", type=Path, default=DEFAULT_RGB_PATH, help="GAMUS RGB .h5 file")
    parser.add_argument("--agl", type=Path, default=DEFAULT_AGL_PATH, help="GAMUS AGL .h5 file")
    parser.add_argument(
        "--output", type=Path, default=DEFAULT_OUTPUT_PATH, help="JSON report output path"
    )
    parser.add_argument(
        "--outlier-sigma",
        type=float,
        default=DEFAULT_OUTLIER_SIGMA,
        help="Sigma-clipping threshold for the robust affine fit",
    )
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=DEFAULT_MAX_ITERATIONS,
        help="Max sigma-clipping iterations for the robust affine fit",
    )
    return parser.parse_args()


def _validate_and_prepare_rgb(rgb: np.ndarray) -> np.ndarray:
    """Real, explicit checks -- never a silent reshape/cast. Mirrors the
    intent of app/services/depth_pipeline.extract_rgb_uint8 (reject rather
    than guess), simplified because a GAMUS RGB sample is already a plain
    (H, W, 3) array with no band-order/alpha ambiguity to resolve."""
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


def main() -> None:
    args = _parse_args()

    print(f"Loading GAMUS RGB: {args.rgb}")
    rgb = load_gamus_h5(str(args.rgb))
    rgb = _validate_and_prepare_rgb(rgb)
    print(f"  shape={rgb.shape} dtype={rgb.dtype}")

    print("Loading depth estimator (ai.registry.get_depth_estimator)...")
    estimator = get_depth_estimator()
    estimator.load()
    model_info = estimator.info()
    print(f"  model={model_info.name} revision={model_info.revision} device={model_info.device}")

    print("Running inference...")
    start = time.monotonic()
    prediction = estimator.predict(rgb)
    inference_seconds = time.monotonic() - start
    depth = prediction.depth
    print(f"  done in {inference_seconds:.2f}s, output shape={depth.shape}")

    print(f"Loading GAMUS AGL: {args.agl}")
    agl = load_gamus_h5(str(args.agl))
    print(f"  shape={agl.shape} dtype={agl.dtype}")

    if not (rgb.shape[:2] == depth.shape == agl.shape):
        raise ValueError(
            f"Spatial dimension mismatch: RGB {rgb.shape[:2]}, predicted depth "
            f"{depth.shape}, AGL {agl.shape} must all match for pixel-aligned comparison."
        )

    valid_mask = build_gamus_valid_mask(agl, depth)
    valid_pixel_count = int(valid_mask.sum())
    print(f"Valid pixel count: {valid_pixel_count} / {agl.size}")

    fit, metrics = fit_and_validate_gamus(
        depth,
        agl,
        valid_mask,
        outlier_sigma=args.outlier_sigma,
        max_iterations=args.max_iterations,
    )
    base = metrics.base

    def _fmt(value: float) -> str:
        return "undefined" if math.isnan(value) else f"{value:.6f}"

    print()
    print("=" * 70)
    print(RESULT_LABEL)
    print("=" * 70)
    print(f"Valid pixels:      {valid_pixel_count}")
    print(f"Inlier / outlier:  {base.inlier_count} / {base.outlier_count}")
    print(f"Fitted scale (a):  {fit.scale:.6f}")
    print(f"Fitted offset (b): {fit.offset:.6f}")
    print(f"MAE:               {base.mae:.6f}")
    print(f"RMSE:              {base.rmse:.6f}")
    print(
        f"R2:                {_fmt(metrics.r2)}"
        + (f"  ({metrics.r2_explicit_reason})" if metrics.r2_explicit_reason else "")
    )
    print(
        f"Pearson r:         {_fmt(metrics.pearson_r)}"
        + (f"  ({metrics.pearson_r_explicit_reason})" if metrics.pearson_r_explicit_reason else "")
    )
    print(f"Bias:              {base.bias:.6f}")
    print(f"Residual min/max:  {base.min_residual:.6f} / {base.max_residual:.6f}")
    print("=" * 70)
    print(
        "NOTE: this is NOT a georeferenced DSM/DEM and carries no CRS or "
        "elevation semantics -- it is a pixel-space fit of relative depth "
        "against GAMUS AGL height for validation purposes only."
    )

    report = {
        "result_label": RESULT_LABEL,
        "sample_name": args.rgb.stem.replace("_RGB", ""),
        "rgb_path": str(args.rgb),
        "agl_path": str(args.agl),
        "rgb_shape": list(rgb.shape),
        "agl_shape": list(agl.shape),
        "depth_shape": list(depth.shape),
        "depth_value_semantics": RELATIVE_DEPTH_VALUE_SEMANTICS,
        "model_name": model_info.name,
        "model_revision": model_info.revision,
        "model_device": model_info.device,
        "inference_seconds": inference_seconds,
        "valid_pixel_count": valid_pixel_count,
        "total_pixel_count": int(agl.size),
        "fit": {
            "method": "affine_least_squares_with_iterative_sigma_clipping",
            "scale": fit.scale,
            "offset": fit.offset,
            "outlier_sigma": fit.outlier_sigma,
            "iterations_used": fit.iterations_used,
        },
        "validation_metrics": {
            "mae": base.mae,
            "rmse": base.rmse,
            "r2": metrics.r2,
            "r2_explicit_reason": metrics.r2_explicit_reason,
            "pearson_r": metrics.pearson_r,
            "pearson_r_explicit_reason": metrics.pearson_r_explicit_reason,
            "bias": base.bias,
            "residual_min": base.min_residual,
            "residual_max": base.max_residual,
            "sample_count": base.sample_count,
            "inlier_count": base.inlier_count,
            "outlier_count": base.outlier_count,
        },
        "limitations": (
            "This is a development/validation-only comparison of predicted relative "
            "depth against GAMUS AGL ground truth, computed entirely in raw pixel-index "
            "space. GAMUS's .h5 files carry no CRS/geotransform, so no georeferencing "
            "was used or fabricated; this is NOT a calibrated metric_elevation/DSM "
            "artifact and must never be presented as one. The affine fit cannot "
            "distinguish terrain from object tops any more than the production "
            "calibration pipeline can (see geospatial/calibration.py)."
        ),
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2))
    print(f"\nJSON report written to: {args.output}")


if __name__ == "__main__":
    main()
