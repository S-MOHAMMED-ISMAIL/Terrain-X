"""Conservative sky/background detection for UNCALIBRATED relative-depth
terrain only (never applied to a calibrated DSM/metric-elevation grid — see
callers in app/services/visualization.py).

Root cause this addresses: Depth Anything (like any monocular depth model)
still produces a real numeric prediction for sky pixels — physically, sky is
effectively at infinite distance, so the model's own inverse-depth
convention ("larger = closer, smaller = farther") pushes sky to the LOWEST
values in the image. Before this module existed, TERRAIN-X's 3D mesh
(frontend/src/components/terrain/terrainMesh.ts) treated every finite depth
value as a real terrain sample, so the sky rendered as an ordinary — and
visually confusing — flat low surface textured with the sky's own RGB
colors.

REVISION NOTE (real regression found and fixed via a real photo, not
theory): an earlier version of this heuristic gated on each row's raw
MEAN depth plus a row-to-row mean-change cap. That worked for a clear-sky
photo with a sharp ridge silhouette, but badly over-masked a hazy aerial
photo: the distant mountain chain, valley, fields, lake, and town all sit
under atmospheric haze, so their depth rises only gradually toward the
camera — a real, structured, monotonic gradient that the old row-mean
check could not distinguish from sky's own gradual-looking low band,
because it never actually measured whether a row was FLAT. The result was
~49% of a real photo (the entire mid-ground, not just the sky) being
excluded from the terrain mesh, leaving only the foreground peak as an
island. This version instead measures each row's own internal SPREAD
(P90-P10, a single-pixel-outlier-resistant substitute for a raw std) —
true sky is close to perfectly uniform both left-to-right and top-to-
bottom, while even heavily hazy real terrain still has real, if subtle,
structure (field boundaries, faint ridgelines) that shows up as a real,
non-zero spread. This is the actual physical distinction between "sky"
and "distant terrain", and is far more robust than a mean-based threshold.

This is NOT a semantic sky classifier (no image-understanding model is
involved) and NOT an RGB-color threshold (which would misclassify bright
terrain, snow, or clouds over mountains — explicitly avoided). It is a
depth-driven, spatially-conservative heuristic with two real physical
assumptions: in a normal forward-facing landscape/drone photo, sky (if
present) (a) forms one contiguous region starting at the image's top edge
(row 0), and (b) is essentially FLAT/uniform, unlike ANY real terrain —
however distant, hazy, or gently sloped — which always retains some real
internal structure. Concretely:

1. For each row, compute a robust spread (the 90th minus the 10th
   percentile depth, over valid cells) — resistant to a single outlier
   pixel (e.g. a bird, a lens artifact, or a distant real peak silhouette;
   see the per-pixel refinement in step 3).
2. Scan down from row 0: a row extends the candidate "sky run" only while
   its spread stays below a small fraction of the image's own [min, max]
   depth range. The scan stops at the first row whose spread indicates
   real structure. Combined with requiring the run to start at row 0, this
   is what makes the heuristic safe against a low-depth region anywhere
   else in the image (a shadowed valley floor, a synthetic gradient test
   texture) and — the real bug this revision fixes — against hazy but
   genuinely structured distant terrain.
3. Within an accepted run, individual pixels are masked only if their OWN
   depth is below a second, more generous threshold — this lets a real,
   isolated terrain feature (e.g. a distant peak silhouette) that happens
   to sit within an otherwise-flat sky row band stay classified as real
   terrain, since a single such feature does not meaningfully change a
   wide row's P90-P10 spread but IS far enough from the row's own low
   values to fail this per-pixel check.
4. Each row's own low value (its P10) must also stay close to the level
   the run started at — this rejects a flat-but-unrelated-level region
   elsewhere in the frame (e.g. a uniform rooftop or calm water body) that
   happens to also be individually flat, while still tolerating real
   sky's own slight gradient near the horizon.
5. The candidate run only counts as sky at all if it spans at least
   `min_sky_rows` — a single low row in isolation is not enough evidence.

Disclosed limitations (real, not swept under the rug): this assumes a
roughly horizon-forward composition with sky at the top; it will not help
(and will correctly do nothing — see the "contiguous from the top" and
"flat" rules) for a nadir/top-down drone shot with no sky at all, and
could in principle mis-classify a genuinely featureless, uniformly hazy
region (e.g. fog with zero visible structure) as sky if it happens to sit
at the very top of the frame. This is an intentionally conservative
heuristic, not a certified semantic classifier — verified against two real
aerial photos (a clear-sky mountain ridge and a hazy valley panorama), not
just synthetic data.
"""

from __future__ import annotations

import numpy as np

# A row's P90-P10 depth spread, as a fraction of the image's own [min, max]
# depth range, below which the row counts as "flat" (sky-like). Tuned
# against two real aerial photos (see docs/ARCHITECTURE.md's sky-mask
# section): true sky measured spread ~0 in both; the first row of real
# (if hazy) terrain jumped to a spread an order of magnitude higher than
# this threshold.
SKY_ROW_SPREAD_FRACTION = 0.0015
# Maximum drift, as a fraction of the depth range, allowed between a
# candidate sky run's own starting level and any later row's level within
# that same run — allows real sky's own slight atmospheric gradient near
# the horizon while rejecting a jump into an unrelated flat region (e.g. a
# uniform rooftop or calm water body) elsewhere in the frame.
SKY_LEVEL_DRIFT_FRACTION = 0.05
# A more generous per-pixel fraction used only to refine which individual
# cells within an accepted sky row band are actually masked — intentionally
# looser than SKY_ROW_SPREAD_FRACTION so a real, isolated terrain feature
# (e.g. a distant peak silhouette) within the band is not stripped out
# along with genuine sky.
SKY_PIXEL_FRACTION = 0.15
# Safety cap: never let the sky band consume more than this fraction of the
# image height, regardless of what the row scan finds — protects against a
# degenerate/near-flat depth map being almost entirely masked.
MAX_SKY_FRACTION_OF_HEIGHT = 0.9
# A candidate sky run shorter than this many rows is not real evidence of
# sky (could just be one noisy dark row) — reject it entirely rather than
# masking a sliver.
MIN_SKY_ROWS = 3


def detect_sky_mask(
    depth: np.ndarray,
    *,
    row_spread_fraction: float = SKY_ROW_SPREAD_FRACTION,
    pixel_fraction: float = SKY_PIXEL_FRACTION,
    max_sky_fraction: float = MAX_SKY_FRACTION_OF_HEIGHT,
    min_sky_rows: int = MIN_SKY_ROWS,
) -> np.ndarray:
    """Returns a boolean array, same shape as `depth`, True where a cell
    should be excluded from the terrain mesh as sky/background. `depth` may
    already contain NaN (existing nodata semantics) — those cells are
    always True (invalid) in the returned mask, never reinterpreted as
    "not sky". Returns an all-False mask (nothing excluded) for a
    degenerate input (no valid pixels, a near-zero depth range, or no
    sufficiently long flat run from the top) — this heuristic only ever
    removes cells it has real evidence for, never invents removals.
    """
    height, _width = depth.shape
    valid = np.isfinite(depth)
    mask = ~valid  # NaN/invalid cells are always excluded, independent of sky detection

    if not valid.any():
        return mask

    finite_depth = depth[valid]
    depth_min = float(finite_depth.min())
    depth_max = float(finite_depth.max())
    depth_range = depth_max - depth_min
    if depth_range <= 1e-9:
        # No real spatial variation to distinguish sky from terrain — do
        # not guess.
        return mask

    spread_threshold = row_spread_fraction * depth_range
    pixel_threshold = depth_min + pixel_fraction * depth_range

    # Robust (outlier-resistant) per-row spread: P90-P10 over valid cells
    # only. A row with no valid cells can't be judged either way, so it
    # yields NaN here and stops the scan below rather than being silently
    # skipped.
    depth_for_percentile = np.where(valid, depth, np.nan)
    with np.errstate(all="ignore"):
        row_p10 = np.nanpercentile(depth_for_percentile, 10, axis=1)
        row_p90 = np.nanpercentile(depth_for_percentile, 90, axis=1)
    row_spread = row_p90 - row_p10

    # A row's own low value (its P10) must also stay close to the level the
    # run started at — this is what stops a flat-but-DIFFERENT-level region
    # elsewhere in the image (e.g. a uniform rooftop or calm water body)
    # from being swept into the same "sky" run just because it is also
    # individually flat. Real sky's own level drifts only slightly across
    # a run (subtle atmospheric gradient near the horizon); a jump to an
    # unrelated flat level is a real discontinuity, not sky continuing.
    level_tolerance = SKY_LEVEL_DRIFT_FRACTION * depth_range

    horizon_row = 0
    max_horizon = int(height * max_sky_fraction)
    reference_level: float | None = None
    for row in range(min(height, max_horizon)):
        spread = row_spread[row]
        if not np.isfinite(spread) or spread > spread_threshold:
            break
        level = float(row_p10[row])
        if reference_level is None:
            reference_level = level
        elif abs(level - reference_level) > level_tolerance:
            break
        horizon_row = row + 1

    if horizon_row < min_sky_rows:
        # Not enough contiguous, flat, low evidence to call this sky.
        return mask

    band = depth[:horizon_row, :]
    band_valid = valid[:horizon_row, :]
    mask[:horizon_row, :] |= band_valid & (band < pixel_threshold)

    return mask
