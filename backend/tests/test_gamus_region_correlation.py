"""Tests for the MobileSAM-region-vs-GAMUS-AGL correlation diagnostic
(development/validation-only — see geospatial/gamus_region_correlation.py's
module docstring). All pure, synthetic unit tests — no backend, no model,
no network, no real GAMUS files required, matching the conventions
established by test_gamus_validation.py / test_gamus_semantic_validation.py.

`geospatial.gamus_region_correlation` transitively imports
`geospatial.gamus_validation`, which imports `h5py` at module level (used
by that sibling module's own real GAMUS-file loading, not by anything in
this file) — so this module is skipped the same way its two siblings
already are wherever `h5py` isn't installed (e.g. the backend Docker
image, which deliberately doesn't include it; see
geospatial/gamus_validation.py's module docstring).
"""

import numpy as np
import pytest

h5py = pytest.importorskip("h5py")

from geospatial.gamus_region_correlation import (  # noqa: E402
    RegionCorrelation,
    compute_region_correlations,
    regions_meeting_threshold,
    summarize_by_class,
)

# A 4x8 synthetic tile with two regions:
#   region 1 (left half, 16 px): depth perfectly linear in AGL (r=1.0)
#   region 2 (right half, 16 px): depth uncorrelated with AGL (r ~ 0)
_ROWS, _COLS = 4, 8
LABEL_MAP = np.zeros((_ROWS, _COLS), dtype=np.uint32)
LABEL_MAP[:, :4] = 1
LABEL_MAP[:, 4:] = 2

_region1_agl = np.arange(16, dtype="float64").reshape(4, 4)
_region1_depth = 2.0 * _region1_agl + 1.0  # exact affine relationship -> r=1.0

_rng = np.random.default_rng(0)
_region2_agl = _rng.uniform(0, 10, size=(4, 4))
_region2_depth = np.array(
    [[0.1, 5.0, 0.1, 5.0], [5.0, 0.1, 5.0, 0.1], [0.1, 5.0, 0.1, 5.0], [5.0, 0.1, 5.0, 0.1]]
)  # deliberately uncorrelated with region2_agl's random values

RELATIVE_DEPTH = np.zeros((_ROWS, _COLS), dtype="float64")
RELATIVE_DEPTH[:, :4] = _region1_depth
RELATIVE_DEPTH[:, 4:] = _region2_depth

AGL = np.zeros((_ROWS, _COLS), dtype="float64")
AGL[:, :4] = _region1_agl
AGL[:, 4:] = _region2_agl

CLS = np.zeros((_ROWS, _COLS), dtype="int64")
CLS[:, :4] = 3  # region 1 -> majority class "building"
CLS[:, 4:] = 5  # region 2 -> majority class "road"


def test_region_correlations_recovers_perfect_correlation_in_one_region():
    run = compute_region_correlations(
        LABEL_MAP, RELATIVE_DEPTH, AGL, CLS, min_valid_pixels=4, outlier_sigma=2.5, max_iterations=5
    )

    by_id = {c.region_id: c for c in run.evaluated}
    assert set(by_id.keys()) == {1, 2}

    region1 = by_id[1]
    assert region1.valid_pixel_count == 16
    assert region1.majority_class == 3
    assert abs(region1.pearson_r - 1.0) < 1e-6
    assert abs(region1.r2 - 1.0) < 1e-6
    assert abs(region1.mae) < 1e-6
    # depth = 2*AGL + 1 => the fit (AGL as a function of depth) recovers the
    # INVERSE relationship: AGL = 0.5*depth - 0.5.
    assert abs(region1.scale - 0.5) < 1e-6
    assert abs(region1.offset - (-0.5)) < 1e-6


def test_region_correlations_reports_weak_correlation_honestly():
    run = compute_region_correlations(
        LABEL_MAP, RELATIVE_DEPTH, AGL, CLS, min_valid_pixels=4, outlier_sigma=2.5, max_iterations=5
    )
    region2 = next(c for c in run.evaluated if c.region_id == 2)

    assert region2.majority_class == 5
    # Not asserting an exact value (depends on the random AGL draw) -- only
    # that it's NOT fabricated to look like the perfect region.
    assert region2.pearson_r is None or abs(region2.pearson_r) < 0.9


def test_min_valid_pixels_threshold_skips_small_regions():
    run = compute_region_correlations(
        LABEL_MAP,
        RELATIVE_DEPTH,
        AGL,
        CLS,
        min_valid_pixels=100,
        outlier_sigma=2.5,
        max_iterations=5,
    )

    assert run.evaluated == []
    assert len(run.skipped) == 2
    for skipped in run.skipped:
        assert skipped.valid_pixel_count == 16
        assert "100" in skipped.reason


def test_background_region_zero_is_never_evaluated():
    label_map = np.array([[0, 0, 1, 1]] * 5, dtype=np.uint32)
    depth = np.tile(np.array([0.0, 0.0, 1.0, 2.0]), (5, 1)).astype("float64")
    agl = np.tile(np.array([9.0, 9.0, 3.0, 5.0]), (5, 1)).astype("float64")
    cls = np.zeros_like(label_map, dtype="int64")

    run = compute_region_correlations(label_map, depth, agl, cls, min_valid_pixels=2)

    assert run.total_regions == 1
    assert all(c.region_id != 0 for c in run.evaluated)
    assert all(s.region_id != 0 for s in run.skipped)


def test_shape_mismatch_raises_clear_error():
    label_map = np.zeros((4, 4), dtype=np.uint32)
    depth = np.zeros((4, 4), dtype="float64")
    agl = np.zeros((4, 4), dtype="float64")
    cls = np.zeros((3, 3), dtype="int64")

    with pytest.raises(ValueError, match="shape"):
        compute_region_correlations(label_map, depth, agl, cls)


def test_degenerate_region_is_skipped_not_crashed():
    """A region where relative depth is perfectly constant has no
    determinable scale -- must be recorded as skipped, never crash the
    whole diagnostic run."""
    label_map = np.ones((4, 4), dtype=np.uint32)
    depth = np.full((4, 4), 5.0)
    agl = np.linspace(0, 10, 16).reshape(4, 4)
    cls = np.full((4, 4), 1, dtype="int64")

    run = compute_region_correlations(label_map, depth, agl, cls, min_valid_pixels=4)

    assert run.evaluated == []
    assert len(run.skipped) == 1
    assert "Degenerate" in run.skipped[0].reason


# --------------------------------------------------------------------------
# summarize_by_class
# --------------------------------------------------------------------------


def test_summarize_by_class_groups_correctly():
    run = compute_region_correlations(
        LABEL_MAP, RELATIVE_DEPTH, AGL, CLS, min_valid_pixels=4, outlier_sigma=2.5, max_iterations=5
    )

    summaries = {s.gamus_class: s for s in summarize_by_class(run.evaluated)}
    assert len(summaries) == 7  # every class 0..6 reported, including empty ones

    building = summaries[3]
    assert building.region_count == 1
    assert building.max_abs_r == pytest.approx(1.0, abs=1e-6)
    assert building.total_valid_pixels == 16

    road = summaries[5]
    assert road.region_count == 1

    empty_class = summaries[0]
    assert empty_class.region_count == 0
    assert empty_class.median_abs_r is None
    assert empty_class.median_mae is None


def test_summarize_by_class_handles_no_evaluated_regions():
    summaries = summarize_by_class([])

    assert len(summaries) == 7
    for s in summaries:
        assert s.region_count == 0
        assert s.median_abs_r is None
        assert s.max_abs_r is None


# --------------------------------------------------------------------------
# regions_meeting_threshold
# --------------------------------------------------------------------------


def test_regions_meeting_threshold_finds_only_strong_correlation():
    run = compute_region_correlations(
        LABEL_MAP, RELATIVE_DEPTH, AGL, CLS, min_valid_pixels=4, outlier_sigma=2.5, max_iterations=5
    )

    strong = regions_meeting_threshold(run.evaluated, threshold=0.30)

    assert any(c.region_id == 1 for c in strong)


def test_regions_meeting_threshold_returns_empty_list_not_error_when_none_qualify():
    correlations = [
        RegionCorrelation(
            region_id=1,
            pixel_count=100,
            valid_pixel_count=100,
            majority_class=1,
            purity=0.9,
            pearson_r=0.05,
            pearson_r_explicit_reason=None,
            r2=0.0025,
            r2_explicit_reason=None,
            mae=1.0,
            rmse=1.2,
            scale=1.0,
            offset=0.0,
            inlier_count=95,
            outlier_count=5,
        )
    ]

    assert regions_meeting_threshold(correlations, threshold=0.30) == []


def test_regions_meeting_threshold_excludes_undefined_correlation():
    correlations = [
        RegionCorrelation(
            region_id=1,
            pixel_count=100,
            valid_pixel_count=100,
            majority_class=1,
            purity=0.9,
            pearson_r=None,
            pearson_r_explicit_reason="zero variance",
            r2=None,
            r2_explicit_reason="zero variance",
            mae=1.0,
            rmse=1.2,
            scale=0.0,
            offset=5.0,
            inlier_count=100,
            outlier_count=0,
        )
    ]

    assert regions_meeting_threshold(correlations, threshold=0.30) == []
