"""DEVELOPMENT-ONLY diagnostic tool for geospatial/sky_mask.py — never
imported by the running application (not referenced from app/ or the
frontend). Run manually against a real depth artifact + its source image
to visually verify the sky mask on real data before trusting it:

    python -m geospatial.sky_mask_diagnostic \\
        --original /path/to/original.jpg \\
        --depth /path/to/depth.tif \\
        --out /tmp/sky_mask_diagnostic.png

Produces a 2x2 composite:
    top-left     = original RGB (resized to the depth grid's resolution)
    top-right    = depth, grayscale (black=far/low, white=near/high)
    bottom-left  = mask (green=valid terrain, red=excluded sky/nodata)
    bottom-right = original RGB with excluded regions dimmed, previewing
                   what the terrain mesh will actually render

Also prints the real sky/valid percentages and a coarse per-row summary,
so a reviewer can confirm the boundary sits at the actual sky/terrain
horizon in the source photo rather than eating into real (if hazy or
distant) terrain — see sky_mask.py's own docstring for why a purely
depth-driven heuristic needs this kind of real-photo sanity check.
"""

from __future__ import annotations

import argparse

import numpy as np
import rasterio
from PIL import Image

from geospatial.sky_mask import detect_sky_mask


def build_diagnostic_composite(original_rgb: np.ndarray, depth: np.ndarray) -> Image.Image:
    height, width = depth.shape
    if original_rgb.shape[:2] != (height, width):
        original_rgb = np.array(
            Image.fromarray(original_rgb).resize((width, height), Image.BILINEAR)
        )

    mask = detect_sky_mask(depth)

    valid = np.isfinite(depth)
    normalized = np.zeros_like(depth, dtype=np.float64)
    if valid.any():
        dmin, dmax = float(depth[valid].min()), float(depth[valid].max())
        normalized[valid] = (depth[valid] - dmin) / max(dmax - dmin, 1e-9)
    depth_gray = (np.clip(normalized, 0, 1) * 255).astype(np.uint8)
    depth_rgb = np.stack([depth_gray] * 3, axis=-1)

    mask_rgb = np.zeros((height, width, 3), dtype=np.uint8)
    mask_rgb[~mask] = (40, 200, 80)  # valid terrain
    mask_rgb[mask] = (220, 40, 40)  # excluded (sky/nodata)

    dimmed = (original_rgb.astype(np.float64) * 0.15).astype(np.uint8)
    final_preview = np.where(mask[..., None], dimmed, original_rgb)

    gap = 8
    composite = Image.new("RGB", (width * 2 + gap, height * 2 + gap), (20, 20, 20))
    composite.paste(Image.fromarray(original_rgb), (0, 0))
    composite.paste(Image.fromarray(depth_rgb), (width + gap, 0))
    composite.paste(Image.fromarray(mask_rgb), (0, height + gap))
    composite.paste(Image.fromarray(final_preview), (width + gap, height + gap))
    return composite


def _print_summary(depth: np.ndarray) -> None:
    mask = detect_sky_mask(depth)
    height = depth.shape[0]
    print(f"depth shape: {depth.shape}")
    print(f"sky/excluded fraction: {100 * mask.mean():.2f}%")
    print(f"valid terrain fraction: {100 * (~mask).mean():.2f}%")
    first_terrain_row = next((r for r in range(height) if not mask[r].all()), height)
    print(
        f"first fully-terrain row: {first_terrain_row} "
        f"({100 * first_terrain_row / height:.1f}% down the image)"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--original", required=True, help="Path to the source RGB image")
    parser.add_argument("--depth", required=True, help="Path to the real relative_depth GeoTIFF")
    parser.add_argument("--out", default="/tmp/sky_mask_diagnostic.png")
    args = parser.parse_args()

    with rasterio.open(args.depth) as dataset:
        depth = dataset.read(1).astype(np.float32)
    original_rgb = np.array(Image.open(args.original).convert("RGB"))

    _print_summary(depth)
    composite = build_diagnostic_composite(original_rgb, depth)
    composite.save(args.out)
    print(f"saved {args.out}")


if __name__ == "__main__":
    main()
