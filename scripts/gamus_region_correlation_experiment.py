#!/usr/bin/env python
"""Development/validation-only diagnostic script (Option C diagnostic from
the "Height-Pipeline Architecture Options" report, §D/E): determines whether
TERRAIN-X's existing Depth Anything V2 relative-depth output carries useful
LOCAL correlation with GAMUS AGL when conditioned on MobileSAM regions.

This is the direct successor to scripts/validate_gamus.py (global affine
fit, r=-0.072) and scripts/validate_gamus_semantic.py (MobileSAM region
boundary alignment) — it re-runs both existing estimators (nothing cached
from those runs was persisted as raw arrays, only derived JSON statistics),
then feeds their outputs into geospatial/gamus_region_correlation.py's
per-region diagnostic.

NOT a new model, NOT a new calibration strategy, NOT a change to the
production pipeline (app/services/*, geospatial/calibration.py) — purely a
research question: "restricted to one MobileSAM region's pixels, is
relative depth linearly related to GAMUS AGL?" GAMUS CLS is used only to
label which class a region's pixels mostly belong to, never as a model
input. See geospatial/gamus_region_correlation.py's module docstring for
the full scientific-honesty framing, and its own docstring for why a
region's correlation being strong does NOT prove generalization.

Usage:
    python scripts/gamus_region_correlation_experiment.py
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import matplotlib  # noqa: E402

matplotlib.use("Agg")  # headless -- this script never opens a display
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from ai.registry import get_depth_estimator, get_semantic_estimator  # noqa: E402
from geospatial.gamus_region_correlation import (  # noqa: E402
    DEFAULT_MAX_ITERATIONS,
    DEFAULT_MIN_VALID_PIXELS,
    DEFAULT_OUTLIER_SIGMA,
    MEANINGFUL_CORRELATION_THRESHOLD,
    compute_region_correlations,
    regions_meeting_threshold,
    summarize_by_class,
)
from geospatial.gamus_semantic_validation import validate_gamus_cls_range  # noqa: E402
from geospatial.gamus_validation import build_gamus_valid_mask, load_gamus_h5  # noqa: E402

DATA_ROOT = REPO_ROOT / "TERRAIN-X-TEST-DATA" / "03_gamus_validation"
RGB_PATH = DATA_ROOT / "images" / "test" / "DC_03_26_RGB.h5"
AGL_PATH = DATA_ROOT / "heights" / "test" / "DC_03_26_AGL.h5"
CLS_PATH = DATA_ROOT / "classes" / "test" / "DC_03_26_CLS.h5"

OUT_DIR = REPO_ROOT / "storage" / "validation" / "gamus"
JSON_OUT_PATH = OUT_DIR / "DC_03_26_region_correlation.json"
MARKDOWN_OUT_PATH = OUT_DIR / "DC_03_26_region_correlation_report.md"
SCATTER_PNG_PATH = OUT_DIR / "DC_03_26_region_scatter.png"
BAR_PNG_PATH = OUT_DIR / "DC_03_26_region_pearson_bar.png"

# Prior, already-computed whole-image results (see storage/validation/gamus/
# DC_03_26_validation.json / DC_03_26_semantic_validation.json) -- restated
# here as literal constants for the report/JSON's "baseline" section rather
# than re-parsed from those files, since this script's only job is the NEW
# per-region diagnostic; the prior JSONs remain the source of record for the
# global numbers.
GLOBAL_BASELINE = {
    "mae": 9.315280311996643,
    "rmse": 10.822713532469217,
    "r2": 0.0051694951283883794,
    "pearson_r": -0.07189920116655343,
    "scale": -3.668697100743908,
    "offset": 15.214557426754096,
    "mobilesam_region_count": 61,
    "mobilesam_foreground_coverage_fraction": 97196 / 1048576,
}

CLASS_NAMES = {
    0: "background/other",
    1: "ground",
    2: "low vegetation",
    3: "building",
    4: "water",
    5: "road",
    6: "tree",
}

CLASS_COLORS = {
    0: "#999999",
    1: "#8c564b",
    2: "#2ca02c",
    3: "#d62728",
    4: "#1f77b4",
    5: "#7f7f7f",
    6: "#17becf",
}


def _validate_rgb(rgb: np.ndarray) -> np.ndarray:
    if rgb.ndim != 3 or rgb.shape[2] != 3:
        raise ValueError(f"Expected a (H, W, 3) RGB array; got shape {rgb.shape}")
    if rgb.dtype != np.uint8:
        raise ValueError(f"Expected uint8 RGB; got dtype {rgb.dtype}")
    return rgb


def _write_scatter_plot(
    label_map: np.ndarray, depth: np.ndarray, agl: np.ndarray, cls: np.ndarray, path: Path
) -> None:
    """Scatter of relative depth vs GAMUS AGL for every valid FOREGROUND
    (nonzero MobileSAM region) pixel, colored by that pixel's own GAMUS CLS
    class. A random subsample caps the point count for a readable, honest
    (not overplotted-to-a-blob) figure -- the subsample is drawn AFTER
    masking, so it never biases which classes/regions appear."""
    foreground = label_map != 0
    valid = build_gamus_valid_mask(agl, depth) & foreground
    depth_v = depth[valid]
    agl_v = agl[valid]
    cls_v = cls[valid]

    max_points = 20_000
    rng = np.random.default_rng(0)
    if depth_v.size > max_points:
        idx = rng.choice(depth_v.size, size=max_points, replace=False)
        depth_v, agl_v, cls_v = depth_v[idx], agl_v[idx], cls_v[idx]

    fig, ax = plt.subplots(figsize=(8, 6))
    for class_id in sorted(CLASS_NAMES):
        mask = cls_v == class_id
        if not mask.any():
            continue
        ax.scatter(
            depth_v[mask],
            agl_v[mask],
            s=4,
            alpha=0.4,
            color=CLASS_COLORS[class_id],
            label=f"{class_id}: {CLASS_NAMES[class_id]} (n={int(mask.sum())})",
        )
    ax.set_xlabel("Relative depth (Depth Anything V2, unitless)")
    ax.set_ylabel("GAMUS AGL (m)")
    ax.set_title(
        "DC_03_26: relative depth vs GAMUS AGL\n"
        "(MobileSAM foreground pixels only, colored by GAMUS majority CLS class)"
    )
    ax.legend(markerscale=3, fontsize=8, loc="upper right")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def _write_pearson_bar_plot(evaluated, path: Path) -> None:
    """Region id (x) vs |Pearson r| (y), bar color by GAMUS majority class,
    bar alpha by region purity -- so purity is visually available exactly as
    required, without a second plot."""
    if not evaluated:
        fig, ax = plt.subplots(figsize=(8, 3))
        ax.text(0.5, 0.5, "No regions met the minimum valid-pixel threshold.", ha="center")
        ax.axis("off")
        fig.savefig(path, dpi=150)
        plt.close(fig)
        return

    ordered = sorted(evaluated, key=lambda c: c.region_id)
    region_ids = [c.region_id for c in ordered]
    abs_r = [abs(c.pearson_r) if c.pearson_r is not None else 0.0 for c in ordered]
    colors = [CLASS_COLORS[c.majority_class] for c in ordered]
    alphas = [max(0.25, c.purity) for c in ordered]

    fig, ax = plt.subplots(figsize=(max(8, len(ordered) * 0.18), 5))
    for i, (r, color, alpha) in enumerate(zip(abs_r, colors, alphas, strict=True)):
        ax.bar(i, r, color=color, alpha=alpha)
    ax.axhline(
        MEANINGFUL_CORRELATION_THRESHOLD,
        color="black",
        linestyle="--",
        linewidth=1,
        label=f"diagnostic threshold |r|={MEANINGFUL_CORRELATION_THRESHOLD}",
    )
    ax.set_xticks(range(len(region_ids)))
    ax.set_xticklabels(region_ids, rotation=90, fontsize=6)
    ax.set_xlabel("MobileSAM region id")
    ax.set_ylabel("|Pearson r| (relative depth vs GAMUS AGL, within-region fit)")
    ax.set_title(
        "DC_03_26: per-region |Pearson r|\n"
        "(bar color = GAMUS majority class, bar opacity = region purity)"
    )
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def main() -> None:
    print(f"Loading GAMUS RGB: {RGB_PATH}")
    rgb = _validate_rgb(load_gamus_h5(str(RGB_PATH)))

    print("Loading depth estimator and running inference...")
    depth_estimator = get_depth_estimator()
    depth_estimator.load()
    depth_start = time.monotonic()
    depth_prediction = depth_estimator.predict(rgb)
    depth_seconds = time.monotonic() - depth_start
    depth = depth_prediction.depth
    print(f"  depth inference done in {depth_seconds:.2f}s, shape={depth.shape}")

    print("Loading semantic estimator and running MobileSAM inference...")
    semantic_estimator = get_semantic_estimator()
    semantic_estimator.load()
    semantic_start = time.monotonic()
    semantic_prediction = semantic_estimator.predict(rgb, min_region_area_px=0)
    semantic_seconds = time.monotonic() - semantic_start
    label_map = semantic_prediction.label_map
    print(
        f"  MobileSAM inference done in {semantic_seconds:.2f}s, "
        f"{len(semantic_prediction.regions)} region(s)"
    )

    print(f"Loading GAMUS AGL: {AGL_PATH}")
    agl = load_gamus_h5(str(AGL_PATH))
    print(f"Loading GAMUS CLS: {CLS_PATH}")
    cls = load_gamus_h5(str(CLS_PATH))

    shapes = {
        "rgb[:2]": rgb.shape[:2],
        "depth": depth.shape,
        "label_map": label_map.shape,
        "agl": agl.shape,
        "cls": cls.shape,
    }
    if len(set(shapes.values())) > 1:
        raise ValueError(f"Pixel-alignment check failed across arrays: {shapes}")
    print(f"Pixel-alignment confirmed: all arrays share shape {depth.shape}")

    validate_gamus_cls_range(cls)

    print(
        f"Computing per-region correlations "
        f"(min_valid_pixels={DEFAULT_MIN_VALID_PIXELS}, "
        f"outlier_sigma={DEFAULT_OUTLIER_SIGMA}, max_iterations={DEFAULT_MAX_ITERATIONS})..."
    )
    run = compute_region_correlations(
        label_map,
        depth,
        agl,
        cls,
        min_valid_pixels=DEFAULT_MIN_VALID_PIXELS,
        outlier_sigma=DEFAULT_OUTLIER_SIGMA,
        max_iterations=DEFAULT_MAX_ITERATIONS,
    )
    class_summaries = summarize_by_class(run.evaluated)
    strong_regions = regions_meeting_threshold(
        run.evaluated, threshold=MEANINGFUL_CORRELATION_THRESHOLD
    )

    best_region = max(
        (c for c in run.evaluated if c.pearson_r is not None),
        key=lambda c: abs(c.pearson_r),
        default=None,
    )

    print(
        f"Evaluated {len(run.evaluated)}/{run.total_regions} regions "
        f"({len(run.skipped)} skipped below the size threshold or degenerate)."
    )
    if best_region is not None:
        print(
            f"Best region by |r|: region {best_region.region_id}, "
            f"|r|={abs(best_region.pearson_r):.4f}, n={best_region.valid_pixel_count}, "
            f"class={best_region.majority_class} ({CLASS_NAMES[best_region.majority_class]})"
        )
    print(f"Regions meeting |r| >= {MEANINGFUL_CORRELATION_THRESHOLD}: {len(strong_regions)}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    print("Writing scatter plot...")
    _write_scatter_plot(label_map, depth, agl, cls, SCATTER_PNG_PATH)
    print("Writing per-region |r| bar plot...")
    _write_pearson_bar_plot(run.evaluated, BAR_PNG_PATH)

    report = {
        "result_label": (
            "MobileSAM-region-conditioned local correlation of relative depth vs GAMUS AGL "
            "(Option C diagnostic, single-tile evaluation-only)"
        ),
        "sample_name": "DC_03_26",
        "rgb_shape": list(rgb.shape),
        "agl_shape": list(agl.shape),
        "cls_shape": list(cls.shape),
        "depth_inference_seconds": depth_seconds,
        "mobilesam_inference_seconds": semantic_seconds,
        "min_valid_pixels": run.min_valid_pixels,
        "outlier_sigma": run.outlier_sigma,
        "max_iterations": run.max_iterations,
        "meaningful_correlation_threshold": MEANINGFUL_CORRELATION_THRESHOLD,
        "total_regions": run.total_regions,
        "evaluated_region_count": len(run.evaluated),
        "skipped_region_count": len(run.skipped),
        "regions_meeting_threshold_count": len(strong_regions),
        "global_baseline": GLOBAL_BASELINE,
        "best_region": (
            None
            if best_region is None
            else {
                "region_id": best_region.region_id,
                "pixel_count": best_region.pixel_count,
                "valid_pixel_count": best_region.valid_pixel_count,
                "majority_class": best_region.majority_class,
                "majority_class_name": CLASS_NAMES[best_region.majority_class],
                "purity": best_region.purity,
                "pearson_r": best_region.pearson_r,
                "r2": best_region.r2,
                "mae": best_region.mae,
                "rmse": best_region.rmse,
                "scale": best_region.scale,
                "offset": best_region.offset,
            }
        ),
        "evaluated_regions": [
            {
                "region_id": c.region_id,
                "pixel_count": c.pixel_count,
                "valid_pixel_count": c.valid_pixel_count,
                "majority_class": c.majority_class,
                "majority_class_name": CLASS_NAMES[c.majority_class],
                "purity": c.purity,
                "pearson_r": c.pearson_r,
                "pearson_r_explicit_reason": c.pearson_r_explicit_reason,
                "r2": c.r2,
                "r2_explicit_reason": c.r2_explicit_reason,
                "mae": c.mae,
                "rmse": c.rmse,
                "scale": c.scale,
                "offset": c.offset,
                "inlier_count": c.inlier_count,
                "outlier_count": c.outlier_count,
            }
            for c in run.evaluated
        ],
        "skipped_regions": [
            {
                "region_id": s.region_id,
                "pixel_count": s.pixel_count,
                "valid_pixel_count": s.valid_pixel_count,
                "reason": s.reason,
            }
            for s in run.skipped
        ],
        "class_summaries": [
            {
                "gamus_class": s.gamus_class,
                "class_name": CLASS_NAMES[s.gamus_class],
                "region_count": s.region_count,
                "regions_with_defined_r": s.regions_with_defined_r,
                "total_valid_pixels": s.total_valid_pixels,
                "median_abs_r": s.median_abs_r,
                "mean_abs_r": s.mean_abs_r,
                "max_abs_r": s.max_abs_r,
                "median_mae": s.median_mae,
                "median_rmse": s.median_rmse,
            }
            for s in class_summaries
        ],
        "limitations": (
            "Single GAMUS tile (DC_03_26) only -- not a claim about GAMUS or MobileSAM in "
            "general. Per-region fits use small sample sizes (see valid_pixel_count on every "
            "region and class); a high |r| in one small region is reported honestly but does "
            "NOT prove the relationship generalizes to unseen regions/tiles. GAMUS CLS is used "
            "strictly as an evaluation-side partition label, never as a model input. The "
            f"threshold |r| >= {MEANINGFUL_CORRELATION_THRESHOLD} is the predefined diagnostic "
            "threshold from the architecture report for deciding whether local calibration is "
            "worth further investigation -- it is NOT an acceptance claim about any final model."
        ),
    }

    JSON_OUT_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"JSON report written to: {JSON_OUT_PATH}")

    _write_markdown_report(report, class_summaries, strong_regions, best_region)
    print(f"Markdown report written to: {MARKDOWN_OUT_PATH}")


def _write_markdown_report(report: dict, class_summaries, strong_regions, best_region) -> None:
    baseline = report["global_baseline"]
    lines: list[str] = []
    lines.append("# DC_03_26 — MobileSAM-region-conditioned depth/AGL correlation diagnostic")
    lines.append("")
    lines.append(
        "Evaluation-only research diagnostic (Option C from the Height-Pipeline "
        "Architecture Options report, §D/E). Determines whether Depth Anything V2's "
        "relative-depth output has useful LOCAL correlation with GAMUS AGL when "
        "restricted to individual MobileSAM regions. **Not an acceptance claim about "
        "any final model.**"
    )
    lines.append("")
    lines.append("## Data / artifacts used")
    lines.append("")
    lines.append("- `TERRAIN-X-TEST-DATA/03_gamus_validation/images/test/DC_03_26_RGB.h5`")
    lines.append("- `TERRAIN-X-TEST-DATA/03_gamus_validation/heights/test/DC_03_26_AGL.h5`")
    lines.append("- `TERRAIN-X-TEST-DATA/03_gamus_validation/classes/test/DC_03_26_CLS.h5`")
    lines.append(
        "- Prior artifacts referenced for the global baseline: "
        "`storage/validation/gamus/DC_03_26_validation.json`, "
        "`storage/validation/gamus/DC_03_26_semantic_validation.json` "
        "(raw depth/label_map arrays were not persisted by those runs, so this "
        "experiment re-ran both estimators; both are deterministic in eval mode)."
    )
    lines.append("")
    lines.append("## Region-size threshold")
    lines.append("")
    lines.append(
        f"`min_valid_pixels = {report['min_valid_pixels']}` (documented in "
        "`geospatial/gamus_region_correlation.py`: well below the tile's mean region "
        "size of ~1,593 px while ruling out the smallest, statistically unstable regions)."
    )
    lines.append("")
    lines.append(
        f"**{report['evaluated_region_count']} of {report['total_regions']} regions evaluated** "
        f"({report['skipped_region_count']} skipped — see `skipped_regions` in the JSON for "
        "the exact reason per region)."
    )
    lines.append("")
    lines.append("## Global baseline (for comparison, from the prior whole-image run)")
    lines.append("")
    lines.append(
        f"- MAE {baseline['mae']:.3f} m, RMSE {baseline['rmse']:.3f} m, "
        f"R² {baseline['r2']:.4f}, Pearson r {baseline['pearson_r']:.4f}"
    )
    lines.append(
        f"- MobileSAM: {baseline['mobilesam_region_count']} regions, "
        f"{baseline['mobilesam_foreground_coverage_fraction']:.1%} foreground coverage"
    )
    lines.append("")
    lines.append("## Best region by |Pearson r|")
    lines.append("")
    if best_region is None:
        lines.append("No region produced a defined Pearson r.")
    else:
        lines.append(
            f"Region **{best_region.region_id}** — |r| = **{abs(best_region.pearson_r):.4f}** "
            f"(r = {best_region.pearson_r:.4f}, R² = {best_region.r2:.4f}), "
            f"n = {best_region.valid_pixel_count} valid pixels, "
            f"majority class = {best_region.majority_class} "
            f"({report['best_region']['majority_class_name']}), "
            f"purity = {best_region.purity:.3f}, "
            f"MAE = {best_region.mae:.3f} m, RMSE = {best_region.rmse:.3f} m."
        )
    lines.append("")
    threshold_value = report["meaningful_correlation_threshold"]
    lines.append(f"## Regions meeting the diagnostic threshold (|r| >= {threshold_value})")
    lines.append("")
    if not strong_regions:
        lines.append(
            f"**None.** Zero of {report['evaluated_region_count']} evaluated regions reached "
            f"|r| >= {report['meaningful_correlation_threshold']}."
        )
    else:
        lines.append(f"{len(strong_regions)} region(s) reached the threshold:")
        for c in sorted(strong_regions, key=lambda c: -abs(c.pearson_r)):
            lines.append(
                f"- region {c.region_id}: |r|={abs(c.pearson_r):.4f}, n={c.valid_pixel_count}, "
                f"class={c.majority_class}, purity={c.purity:.3f}"
            )
    lines.append("")
    lines.append("## Class-level statistics")
    lines.append("")
    lines.append(
        "| Class | Name | Regions | Valid px | Median|r| | Mean|r| | Max|r| "
        "| Median MAE | Median RMSE |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|")

    def _fmt(v):
        return "—" if v is None else f"{v:.4f}" if v < 10 else f"{v:.2f}"

    for s in class_summaries:
        lines.append(
            f"| {s.gamus_class} | {CLASS_NAMES[s.gamus_class]} | {s.region_count} | "
            f"{s.total_valid_pixels} | {_fmt(s.median_abs_r)} | {_fmt(s.mean_abs_r)} | "
            f"{_fmt(s.max_abs_r)} | {_fmt(s.median_mae)} | {_fmt(s.median_rmse)} |"
        )
    lines.append("")
    lines.append("## Visualization artifacts")
    lines.append("")
    lines.append(
        f"- `{SCATTER_PNG_PATH.name}` — relative depth vs GAMUS AGL, colored by majority CLS class"
    )
    lines.append(
        f"- `{BAR_PNG_PATH.name}` — per-region |Pearson r|, colored by class, opacity by purity"
    )
    lines.append("")
    lines.append("## Does the evidence support continuing with Option C?")
    lines.append("")
    if strong_regions:
        max_r = max(abs(c.pearson_r) for c in strong_regions)
        smallest_n = min(c.valid_pixel_count for c in strong_regions)
        lines.append(
            f"{len(strong_regions)} region(s) individually exceed the diagnostic threshold "
            f"(max |r|={max_r:.4f}). **This is reported honestly and is NOT, on its own, "
            "evidence that Option C generalizes** — every qualifying region's sample size is "
            f"reported above (smallest qualifying n={smallest_n}); a single tile's regions "
            "meeting a threshold does not establish a class- or tile-general relationship. "
            "See the class-level table for whether this concentrates in one class or is "
            "scattered, and see Limitations below."
        )
    else:
        lines.append(
            "No region reached the predefined threshold. Combined with the global result "
            "(r=-0.072) already showing no linear relationship, this is evidence AGAINST "
            "Option C on this tile: conditioning on MobileSAM regions did not surface a "
            "meaningfully correlated subset of the image."
        )
    lines.append("")
    lines.append("## Limitations")
    lines.append("")
    lines.append(report["limitations"])
    lines.append("")

    MARKDOWN_OUT_PATH.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
