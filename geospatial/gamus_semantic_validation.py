"""Development/validation-only support for measuring how well MobileSAM's
class-agnostic region boundaries (see `ai/mobile_sam.py`,
`app/services/semantic_pipeline.py`) line up with GAMUS's real CLS
("classes") ground-truth labels. Never part of the production pipeline and
never imported by it.

## Scientific-honesty rule (read before changing anything here)

MobileSAM produces arbitrary, image-specific `region_id` integers with NO
semantic meaning — see `ai/semantic_estimator.py`'s own interface contract:
"a `SemanticEstimator` implementation must never report a specific object
class... no caller may invent one." This module does not, and must never,
change that. Everything below computes purely EVALUATION-SIDE statistics —
"if we look at what GAMUS class happens to dominate each MobileSAM region
after the fact, how consistent is that region with a single class" — never
a model prediction, never a persisted artifact, never a relabeling of
`label_map`. The word "majority class" below always means "the GAMUS class
that happens to cover the most of this region's pixels," not "the class
MobileSAM predicted" (MobileSAM predicted nothing of the sort).

## Why this is a separate module from geospatial/gamus_validation.py

`gamus_validation.py` scores a CONTINUOUS relative-depth prediction against
a continuous AGL reference (affine fit + residuals). This module compares
two CATEGORICAL rasters (MobileSAM region IDs vs. GAMUS class IDs) where
there is no shared value space to fit anything to — the only meaningful
questions are region-vs-class pixel overlap (purity, a crosstab) and,
optionally, whether the two rasters' boundaries fall in similar places
(a boundary-alignment metric, not a classification metric). Both modules
reuse `load_gamus_h5`/`GamusH5FormatError` from `gamus_validation` rather
than duplicating the h5 loading logic.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

GAMUS_CLS_VALID_CLASSES = tuple(range(7))  # GAMUS CLS is documented as classes 0..6


class GamusClassRangeError(Exception):
    """Raised when a GAMUS CLS array contains values outside the documented
    0..6 class range — a real, explicit failure rather than silently
    treating an unexpected value as a valid class or as background."""


def validate_gamus_cls_range(
    cls: np.ndarray, *, valid_classes: tuple[int, ...] = GAMUS_CLS_VALID_CLASSES
) -> None:
    """Raises `GamusClassRangeError` listing every out-of-range value found
    in `cls`, if any. Never silently clips/rounds/reinterprets an
    unexpected value."""
    present = np.unique(cls)
    invalid = [v for v in present.tolist() if v not in valid_classes]
    if invalid:
        raise GamusClassRangeError(
            f"GAMUS CLS array contains value(s) outside the documented "
            f"{valid_classes[0]}..{valid_classes[-1]} class range: {invalid}"
        )


@dataclass(frozen=True)
class RegionPurity:
    """Evaluation-side statistics for one MobileSAM region — NOT a
    prediction. `majority_class` is whichever GAMUS class happens to cover
    the most pixels of this region; ties are broken deterministically in
    favor of the numerically smallest class id (see
    `compute_region_purity`'s docstring)."""

    region_id: int  # MobileSAM's own arbitrary region id -- never assumed to be in 1..N
    region_area: int
    majority_class: int
    majority_count: int
    purity: float  # majority_count / region_area, in [0, 1]


def compute_region_purity(label_map: np.ndarray, cls: np.ndarray) -> list[RegionPurity]:
    """For every nonzero (i.e. real, MobileSAM-assigned) region id in
    `label_map`, finds the GAMUS class that covers the most of that
    region's pixels and reports the resulting purity. Region id 0
    (background/unassigned — MobileSAM's own convention, see
    `ai/semantic_estimator.py`) is never treated as a region.

    Region ids are read directly from whatever values are actually present
    in `label_map` (via `np.unique`) — never assumed to be a contiguous
    `1..N` range, since MobileSAM's rasterization policy
    (`ai/mobile_sam.py`) only guarantees "arbitrary positive integers,
    0 = background," not any particular numbering.

    Tie-breaking: if two or more GAMUS classes tie for the most pixels in a
    region, the smallest class id wins (a fixed, deterministic rule, not an
    arbitrary one — `np.unique` returns class ids in ascending order, and
    `argmax` keeps the first maximum it sees).

    Raises `ValueError` if `label_map.shape != cls.shape`.
    """
    if label_map.shape != cls.shape:
        raise ValueError(
            f"label_map and cls arrays must have matching shapes for pixel-aligned "
            f"comparison; got label_map {label_map.shape} vs cls {cls.shape}."
        )

    results: list[RegionPurity] = []
    for region_id in np.unique(label_map).tolist():
        if region_id == 0:
            continue
        mask = label_map == region_id
        region_area = int(mask.sum())
        classes, counts = np.unique(cls[mask], return_counts=True)
        majority_index = int(np.argmax(counts))
        majority_class = int(classes[majority_index])
        majority_count = int(counts[majority_index])
        results.append(
            RegionPurity(
                region_id=int(region_id),
                region_area=region_area,
                majority_class=majority_class,
                majority_count=majority_count,
                purity=majority_count / region_area,
            )
        )
    return results


@dataclass(frozen=True)
class ClassOverlapSummary:
    """Real, per-GAMUS-class facts: how many pixels of this class exist,
    how many distinct MobileSAM regions touch any of them, and exactly how
    those pixels split across regions (`region_pixel_counts`) plus how many
    sit under no MobileSAM region at all (`background_pixel_count`)."""

    gamus_class: int
    pixel_count: int
    touching_region_count: int
    region_pixel_counts: dict[int, int]  # region_id -> pixel count of this class in that region
    background_pixel_count: int  # pixels of this class where label_map == 0


@dataclass(frozen=True)
class OverlapMatrix:
    """MobileSAM region <-> GAMUS CLS pixel overlap matrix. NOT a
    classifier confusion matrix — there is no prediction/ground-truth pair
    here, only two independently-produced categorical rasters being
    cross-tabulated by pixel count."""

    label: str
    region_ids: list[int]  # sorted ascending, foreground (nonzero) regions only
    gamus_classes: list[int]
    # matrix[i][j] = pixel count where cls == gamus_classes[i] and label_map == region_ids[j]
    matrix: list[list[int]]


def compute_class_overlap_summaries(
    label_map: np.ndarray,
    cls: np.ndarray,
    *,
    gamus_classes: tuple[int, ...] = GAMUS_CLS_VALID_CLASSES,
) -> list[ClassOverlapSummary]:
    """Per-GAMUS-class overlap facts (see `ClassOverlapSummary`). Raises
    `ValueError` on a shape mismatch, same as `compute_region_purity`."""
    if label_map.shape != cls.shape:
        raise ValueError(
            f"label_map and cls arrays must have matching shapes for pixel-aligned "
            f"comparison; got label_map {label_map.shape} vs cls {cls.shape}."
        )

    summaries: list[ClassOverlapSummary] = []
    for gamus_class in gamus_classes:
        class_mask = cls == gamus_class
        pixel_count = int(class_mask.sum())
        region_values = label_map[class_mask]
        region_ids, counts = np.unique(region_values, return_counts=True)
        region_pixel_counts = {
            int(r): int(c)
            for r, c in zip(region_ids.tolist(), counts.tolist(), strict=True)
            if r != 0
        }
        background_pixel_count = int(counts[region_ids == 0].sum()) if 0 in region_ids else 0
        summaries.append(
            ClassOverlapSummary(
                gamus_class=int(gamus_class),
                pixel_count=pixel_count,
                touching_region_count=len(region_pixel_counts),
                region_pixel_counts=region_pixel_counts,
                background_pixel_count=background_pixel_count,
            )
        )
    return summaries


def build_overlap_matrix(
    label_map: np.ndarray,
    cls: np.ndarray,
    *,
    gamus_classes: tuple[int, ...] = GAMUS_CLS_VALID_CLASSES,
) -> OverlapMatrix:
    """Builds the full region x class pixel-count crosstab. Vectorized via
    `np.bincount` over a combined (region-index, class-index) key — fast
    even for a full 1024x1024 tile with a few hundred regions. Region ids
    are read from whatever is actually present in `label_map` (sorted
    ascending), never assumed contiguous."""
    if label_map.shape != cls.shape:
        raise ValueError(
            f"label_map and cls arrays must have matching shapes for pixel-aligned "
            f"comparison; got label_map {label_map.shape} vs cls {cls.shape}."
        )

    region_ids = sorted(int(r) for r in np.unique(label_map).tolist() if r != 0)
    class_index_of = {c: i for i, c in enumerate(gamus_classes)}

    foreground = label_map != 0
    flat_labels = label_map[foreground]
    flat_cls = cls[foreground]

    if not region_ids or flat_labels.size == 0:
        matrix = [[0] * len(region_ids) for _ in gamus_classes]
        return OverlapMatrix(
            label="MobileSAM region <-> GAMUS CLS pixel overlap matrix",
            region_ids=region_ids,
            gamus_classes=list(gamus_classes),
            matrix=matrix,
        )

    region_ids_arr = np.array(region_ids)
    region_index = np.searchsorted(region_ids_arr, flat_labels)
    class_index = np.array([class_index_of[int(c)] for c in flat_cls.tolist()])

    n_regions = len(region_ids)
    n_classes = len(gamus_classes)
    combined = class_index.astype(np.int64) * n_regions + region_index.astype(np.int64)
    counts = np.bincount(combined, minlength=n_classes * n_regions)
    matrix = counts.reshape(n_classes, n_regions)

    return OverlapMatrix(
        label="MobileSAM region <-> GAMUS CLS pixel overlap matrix",
        region_ids=region_ids,
        gamus_classes=list(gamus_classes),
        matrix=matrix.tolist(),
    )


@dataclass(frozen=True)
class BoundaryAlignment:
    """Boundary agreement between MobileSAM's region boundaries and
    GAMUS CLS's class boundaries — a real, documented boundary-detection
    metric (a tolerance-based boundary precision/recall/F1, the same family
    of metric used for edge/boundary benchmarks such as BSDS), reported as
    BOUNDARY ALIGNMENT ONLY. This is explicitly NOT a classification
    accuracy metric: it says nothing about whether the classes on either
    side of a boundary agree, only whether the two rasters tend to change
    value in the same places."""

    tolerance_px: int
    method: str
    mobilesam_boundary_pixel_count: int
    gamus_boundary_pixel_count: int
    precision: (
        float | None
    )  # fraction of MobileSAM boundary pixels within tolerance of a GAMUS boundary
    recall: (
        float | None
    )  # fraction of GAMUS boundary pixels within tolerance of a MobileSAM boundary
    f1: float | None
    explicit_reason: str | None  # set instead of fabricating 0.0/1.0 when undefined


def _boundary_mask(categorical: np.ndarray) -> np.ndarray:
    """A pixel is a boundary pixel if any of its 4-connected neighbors has a
    different categorical value. Image-edge pixels are compared only
    against the neighbors they actually have (no wraparound, no fabricated
    off-image neighbor)."""
    boundary = np.zeros(categorical.shape, dtype=bool)
    boundary[:, :-1] |= categorical[:, :-1] != categorical[:, 1:]
    boundary[:, 1:] |= categorical[:, :-1] != categorical[:, 1:]
    boundary[:-1, :] |= categorical[:-1, :] != categorical[1:, :]
    boundary[1:, :] |= categorical[:-1, :] != categorical[1:, :]
    return boundary


def _dilate_l1(mask: np.ndarray, radius: int) -> np.ndarray:
    """4-connected (L1/Manhattan) dilation by `radius` pixels, implemented
    with plain numpy shifts (no scipy dependency) — grows `mask` into an
    L1-ball of the given radius around every True pixel."""
    dilated = mask
    for _ in range(radius):
        grown = dilated.copy()
        grown[1:, :] |= dilated[:-1, :]
        grown[:-1, :] |= dilated[1:, :]
        grown[:, 1:] |= dilated[:, :-1]
        grown[:, :-1] |= dilated[:, 1:]
        dilated = grown
    return dilated


def compute_boundary_alignment(
    label_map: np.ndarray, cls: np.ndarray, *, tolerance_px: int = 2
) -> BoundaryAlignment:
    """Tolerance-based boundary precision/recall/F1 between MobileSAM's
    region boundaries and GAMUS CLS's class boundaries.

    Method: a boundary pixel in one raster "matches" if it falls within
    `tolerance_px` pixels (L1/4-connected dilation, see `_dilate_l1`) of a
    boundary pixel in the other raster. Precision = fraction of MobileSAM
    boundary pixels that have a nearby GAMUS boundary pixel; recall =
    fraction of GAMUS boundary pixels that have a nearby MobileSAM boundary
    pixel. This measures spatial agreement of WHERE the two rasters change
    value, never whether the values that change agree (that's purity/the
    overlap matrix, above) -- see `BoundaryAlignment`'s own docstring.

    Raises `ValueError` on a shape mismatch. If either raster has zero
    boundary pixels (fully constant), precision/recall/f1 are reported as
    `None` with an explicit reason rather than a fabricated/undefined 0 or 1.
    """
    if label_map.shape != cls.shape:
        raise ValueError(
            f"label_map and cls arrays must have matching shapes for pixel-aligned "
            f"comparison; got label_map {label_map.shape} vs cls {cls.shape}."
        )
    if tolerance_px < 0:
        raise ValueError(f"tolerance_px must be >= 0; got {tolerance_px}")

    mobilesam_boundary = _boundary_mask(label_map)
    gamus_boundary = _boundary_mask(cls)
    mobilesam_count = int(mobilesam_boundary.sum())
    gamus_count = int(gamus_boundary.sum())

    method = (
        f"tolerance-based boundary precision/recall/F1, {tolerance_px}px L1 "
        f"(4-connected) dilation tolerance"
    )

    if mobilesam_count == 0 or gamus_count == 0:
        return BoundaryAlignment(
            tolerance_px=tolerance_px,
            method=method,
            mobilesam_boundary_pixel_count=mobilesam_count,
            gamus_boundary_pixel_count=gamus_count,
            precision=None,
            recall=None,
            f1=None,
            explicit_reason=(
                "One of the two rasters has zero boundary pixels (a fully constant "
                "region map or class map); boundary precision/recall/F1 is undefined."
            ),
        )

    dilated_gamus = _dilate_l1(gamus_boundary, tolerance_px)
    dilated_mobilesam = _dilate_l1(mobilesam_boundary, tolerance_px)

    precision = float(np.sum(mobilesam_boundary & dilated_gamus)) / mobilesam_count
    recall = float(np.sum(gamus_boundary & dilated_mobilesam)) / gamus_count
    f1 = 0.0 if (precision + recall) == 0 else 2 * precision * recall / (precision + recall)

    return BoundaryAlignment(
        tolerance_px=tolerance_px,
        method=method,
        mobilesam_boundary_pixel_count=mobilesam_count,
        gamus_boundary_pixel_count=gamus_count,
        precision=precision,
        recall=recall,
        f1=f1,
        explicit_reason=None,
    )


@dataclass(frozen=True)
class GamusSemanticValidationResult:
    """The full result bundle for one MobileSAM-vs-GAMUS-CLS boundary
    alignment run. See the module docstring for the scientific-honesty
    framing every field here must be reported under."""

    region_purities: list[RegionPurity]
    class_overlap_summaries: list[ClassOverlapSummary]
    overlap_matrix: OverlapMatrix
    boundary_alignment: BoundaryAlignment
    area_weighted_mean_purity: float | None
    area_weighted_mean_purity_explicit_reason: str | None
    unweighted_mean_purity: float | None
    unweighted_mean_purity_explicit_reason: str | None
    region_count: int
    total_foreground_pixels: int
    total_pixels: int


def compute_gamus_semantic_validation(
    label_map: np.ndarray, cls: np.ndarray, *, boundary_tolerance_px: int = 2
) -> GamusSemanticValidationResult:
    """Top-level entry point: runs every evaluation-side statistic this
    module provides over one (label_map, cls) pair and bundles the result.
    Raises `ValueError` on a shape mismatch (propagated from the
    sub-functions above, checked once here too for a single clear failure
    point)."""
    if label_map.shape != cls.shape:
        raise ValueError(
            f"label_map and cls arrays must have matching shapes for pixel-aligned "
            f"comparison; got label_map {label_map.shape} vs cls {cls.shape}."
        )

    region_purities = compute_region_purity(label_map, cls)
    class_overlap_summaries = compute_class_overlap_summaries(label_map, cls)
    overlap_matrix = build_overlap_matrix(label_map, cls)
    boundary_alignment = compute_boundary_alignment(
        label_map, cls, tolerance_px=boundary_tolerance_px
    )

    total_foreground_pixels = sum(r.region_area for r in region_purities)

    if not region_purities:
        area_weighted_mean_purity = None
        area_weighted_reason = "No MobileSAM regions (label_map has no nonzero pixels)."
        unweighted_mean_purity = None
        unweighted_reason = area_weighted_reason
    else:
        area_weighted_mean_purity = (
            sum(r.purity * r.region_area for r in region_purities) / total_foreground_pixels
        )
        area_weighted_reason = None
        unweighted_mean_purity = sum(r.purity for r in region_purities) / len(region_purities)
        unweighted_reason = None

    return GamusSemanticValidationResult(
        region_purities=region_purities,
        class_overlap_summaries=class_overlap_summaries,
        overlap_matrix=overlap_matrix,
        boundary_alignment=boundary_alignment,
        area_weighted_mean_purity=area_weighted_mean_purity,
        area_weighted_mean_purity_explicit_reason=area_weighted_reason,
        unweighted_mean_purity=unweighted_mean_purity,
        unweighted_mean_purity_explicit_reason=unweighted_reason,
        region_count=len(region_purities),
        total_foreground_pixels=total_foreground_pixels,
        total_pixels=int(label_map.size),
    )
