"""GAMUS semantic (MobileSAM region <-> GAMUS CLS) boundary-alignment
validation tests (development/validation-only — see
geospatial/gamus_semantic_validation.py's module docstring).

All tests below are pure, fast unit tests over synthetic arrays — no
backend, no model, no worker, no network, matching the conventions
established in test_gamus_validation.py and test_calibration.py. None of
these tests require MobileSAM inference or GAMUS files to be present; the
one exception (an opt-in real-sample smoke test for the CLS loader) skips
itself if the real file isn't on disk, for the same reason
test_gamus_validation.py's real-sample test does.
"""

from pathlib import Path

import numpy as np
import pytest

h5py = pytest.importorskip("h5py")

from geospatial.gamus_semantic_validation import (  # noqa: E402
    GamusClassRangeError,
    build_overlap_matrix,
    compute_boundary_alignment,
    compute_class_overlap_summaries,
    compute_gamus_semantic_validation,
    compute_region_purity,
    validate_gamus_cls_range,
)
from geospatial.gamus_validation import load_gamus_h5  # noqa: E402

REAL_GAMUS_CLS_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "TERRAIN-X-TEST-DATA"
    / "03_gamus_validation"
    / "classes"
    / "test"
    / "DC_03_26_CLS.h5"
)

# The example given in the task: two regions (1, 2), CLS values 2..5.
EXAMPLE_LABEL_MAP = np.array([[1, 1, 1, 2, 2], [1, 1, 1, 2, 2]], dtype=np.uint32)
EXAMPLE_CLS = np.array([[3, 3, 2, 4, 4], [3, 2, 2, 4, 5]], dtype=np.int64)


# --------------------------------------------------------------------------
# 1. Region purity calculation
# --------------------------------------------------------------------------


def test_region_purity_matches_hand_computed_example():
    """Region 1 covers 6 pixels: cls values [3,3,2,3,2,2] -> 3 appears 3x,
    2 appears 3x -> tie, smallest class (2) wins deterministically.
    Region 2 covers 4 pixels: cls values [4,4,4,5] -> majority class 4,
    count 3, purity 3/4."""
    purities = {r.region_id: r for r in compute_region_purity(EXAMPLE_LABEL_MAP, EXAMPLE_CLS)}

    assert set(purities.keys()) == {1, 2}

    region1 = purities[1]
    assert region1.region_area == 6
    assert region1.majority_class == 2  # tie between 2 and 3 (3 each) -> smallest wins
    assert region1.majority_count == 3
    assert abs(region1.purity - 0.5) < 1e-9

    region2 = purities[2]
    assert region2.region_area == 4
    assert region2.majority_class == 4
    assert region2.majority_count == 3
    assert abs(region2.purity - 0.75) < 1e-9


def test_region_purity_excludes_background_region_zero():
    label_map = np.array([[0, 0, 1], [0, 1, 1]], dtype=np.uint32)
    cls = np.array([[1, 1, 2], [1, 2, 2]], dtype=np.int64)

    purities = compute_region_purity(label_map, cls)

    assert len(purities) == 1
    assert purities[0].region_id == 1


def test_region_purity_raises_clear_error_on_shape_mismatch():
    label_map = np.zeros((4, 4), dtype=np.uint32)
    cls = np.zeros((3, 3), dtype=np.int64)

    with pytest.raises(ValueError, match="shape"):
        compute_region_purity(label_map, cls)


# --------------------------------------------------------------------------
# 2. Region/class overlap matrix
# --------------------------------------------------------------------------


def test_build_overlap_matrix_matches_hand_computed_example():
    overlap = build_overlap_matrix(EXAMPLE_LABEL_MAP, EXAMPLE_CLS)

    assert overlap.region_ids == [1, 2]
    assert overlap.gamus_classes == [0, 1, 2, 3, 4, 5, 6]
    assert overlap.label == "MobileSAM region <-> GAMUS CLS pixel overlap matrix"

    matrix = {c: row for c, row in zip(overlap.gamus_classes, overlap.matrix, strict=True)}
    # class 2: 3 px in region 1, 0 in region 2
    assert matrix[2] == [3, 0]
    # class 3: 3 px in region 1, 0 in region 2
    assert matrix[3] == [3, 0]
    # class 4: 0 px in region 1, 3 px in region 2
    assert matrix[4] == [0, 3]
    # class 5: 0 px in region 1, 1 px in region 2
    assert matrix[5] == [0, 1]
    # classes not present at all
    assert matrix[0] == [0, 0]
    assert matrix[1] == [0, 0]
    assert matrix[6] == [0, 0]

    # Every foreground pixel accounted for exactly once.
    total = sum(sum(row) for row in overlap.matrix)
    assert total == EXAMPLE_LABEL_MAP.size


def test_class_overlap_summaries_report_background_and_touching_regions():
    label_map = np.array([[0, 1, 1], [2, 2, 0]], dtype=np.uint32)
    cls = np.array([[5, 5, 3], [3, 3, 5]], dtype=np.int64)

    summaries = {s.gamus_class: s for s in compute_class_overlap_summaries(label_map, cls)}

    class5 = summaries[5]
    assert class5.pixel_count == 3
    # class 5 pixels: (0,0)->region0(bg), (0,1)->region1, (1,2)->region0(bg)
    assert class5.touching_region_count == 1
    assert class5.region_pixel_counts == {1: 1}
    assert class5.background_pixel_count == 2

    class3 = summaries[3]
    assert class3.pixel_count == 3
    # class 3 pixels: (0,2)->region1, (1,0)->region2, (1,1)->region2
    assert class3.touching_region_count == 2
    assert class3.region_pixel_counts == {1: 1, 2: 2}
    assert class3.background_pixel_count == 0

    # A GAMUS class present in the valid 0..6 range but absent from this
    # synthetic array must still be reported, with all-zero stats.
    assert summaries[0].pixel_count == 0
    assert summaries[0].touching_region_count == 0
    assert summaries[0].region_pixel_counts == {}


# --------------------------------------------------------------------------
# 3. Empty/background handling
# --------------------------------------------------------------------------


def test_compute_gamus_semantic_validation_handles_all_background_label_map():
    """No MobileSAM regions at all (label_map is entirely 0) must report
    explicit None + reason, never a fabricated purity."""
    label_map = np.zeros((5, 5), dtype=np.uint32)
    cls = np.random.default_rng(0).integers(0, 7, size=(5, 5))

    result = compute_gamus_semantic_validation(label_map, cls)

    assert result.region_count == 0
    assert result.total_foreground_pixels == 0
    assert result.area_weighted_mean_purity is None
    assert result.area_weighted_mean_purity_explicit_reason is not None
    assert result.unweighted_mean_purity is None
    assert result.unweighted_mean_purity_explicit_reason is not None
    assert result.region_purities == []


# --------------------------------------------------------------------------
# 4. Regions with a single pixel
# --------------------------------------------------------------------------


def test_region_purity_handles_single_pixel_region():
    label_map = np.array([[1, 0], [0, 0]], dtype=np.uint32)
    cls = np.array([[6, 0], [0, 0]], dtype=np.int64)

    purities = compute_region_purity(label_map, cls)

    assert len(purities) == 1
    single = purities[0]
    assert single.region_id == 1
    assert single.region_area == 1
    assert single.majority_class == 6
    assert single.majority_count == 1
    assert single.purity == 1.0


# --------------------------------------------------------------------------
# 5. Multiple regions
# --------------------------------------------------------------------------


def test_region_purity_handles_many_regions_independently():
    rng = np.random.default_rng(42)
    label_map = rng.integers(0, 6, size=(20, 20)).astype(np.uint32)  # regions 0..5 (0 = background)
    cls = rng.integers(0, 7, size=(20, 20))

    purities = compute_region_purity(label_map, cls)

    expected_region_ids = {r for r in np.unique(label_map).tolist() if r != 0}
    assert {p.region_id for p in purities} == expected_region_ids
    for p in purities:
        assert p.region_area == int((label_map == p.region_id).sum())
        assert 0.0 < p.purity <= 1.0
        assert p.majority_count <= p.region_area


# --------------------------------------------------------------------------
# 6. Shape mismatch raises a clear error
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "func",
    [
        compute_region_purity,
        compute_class_overlap_summaries,
        build_overlap_matrix,
        compute_gamus_semantic_validation,
    ],
)
def test_shape_mismatch_raises_clear_error(func):
    label_map = np.zeros((4, 4), dtype=np.uint32)
    cls = np.zeros((5, 5), dtype=np.int64)

    with pytest.raises(ValueError, match="shape"):
        func(label_map, cls)


def test_boundary_alignment_shape_mismatch_raises_clear_error():
    label_map = np.zeros((4, 4), dtype=np.uint32)
    cls = np.zeros((5, 5), dtype=np.int64)

    with pytest.raises(ValueError, match="shape"):
        compute_boundary_alignment(label_map, cls)


# --------------------------------------------------------------------------
# 7. GAMUS CLS values outside 0..6 are detected/rejected
# --------------------------------------------------------------------------


def test_validate_gamus_cls_range_accepts_valid_classes():
    cls = np.array([[0, 1, 2], [3, 4, 5], [6, 0, 6]], dtype=np.int64)
    validate_gamus_cls_range(cls)  # must not raise


def test_validate_gamus_cls_range_rejects_out_of_range_values():
    cls = np.array([[0, 1, 2], [3, 4, 99]], dtype=np.int64)

    with pytest.raises(GamusClassRangeError, match="99"):
        validate_gamus_cls_range(cls)


def test_validate_gamus_cls_range_rejects_negative_values():
    cls = np.array([[0, 1], [-1, 2]], dtype=np.int64)

    with pytest.raises(GamusClassRangeError, match="-1"):
        validate_gamus_cls_range(cls)


# --------------------------------------------------------------------------
# 8. Arbitrary region IDs (not assumed to be 1..N)
# --------------------------------------------------------------------------


def test_region_purity_handles_arbitrary_nonconsecutive_region_ids():
    label_map = np.array([[7, 7, 42], [7, 103, 103]], dtype=np.uint32)
    cls = np.array([[1, 1, 2], [1, 3, 3]], dtype=np.int64)

    purities = {r.region_id: r for r in compute_region_purity(label_map, cls)}

    assert set(purities.keys()) == {7, 42, 103}
    assert purities[7].region_area == 3
    assert purities[7].majority_class == 1
    assert purities[42].region_area == 1
    assert purities[42].majority_class == 2
    assert purities[103].region_area == 2
    assert purities[103].majority_class == 3


def test_overlap_matrix_handles_arbitrary_nonconsecutive_region_ids():
    label_map = np.array([[7, 7, 42], [7, 103, 103]], dtype=np.uint32)
    cls = np.array([[1, 1, 2], [1, 3, 3]], dtype=np.int64)

    overlap = build_overlap_matrix(label_map, cls)

    assert overlap.region_ids == [7, 42, 103]
    total = sum(sum(row) for row in overlap.matrix)
    assert total == label_map.size


# --------------------------------------------------------------------------
# 9. GAMUS class IDs (0..6) are never interpreted as MobileSAM region IDs
# --------------------------------------------------------------------------


def test_gamus_class_ids_and_region_ids_are_never_conflated():
    """A pathological case where GAMUS class values happen to numerically
    overlap with MobileSAM region ids (e.g. both use small integers) must
    not cause the two label spaces to be confused -- region purity/overlap
    must be keyed strictly by which array (label_map vs cls) a value came
    from, never by its numeric value alone."""
    # Region ids 1 and 2 (from label_map) coincide numerically with class
    # ids 1 and 2 (from cls) -- if the two were ever conflated, region 1's
    # "majority class" could wrongly default to matching its own id.
    label_map = np.array([[1, 1], [2, 2]], dtype=np.uint32)
    cls = np.array([[5, 6], [5, 6]], dtype=np.int64)  # deliberately NOT 1/2

    purities = {r.region_id: r for r in compute_region_purity(label_map, cls)}

    assert purities[1].majority_class == 5
    assert purities[2].majority_class == 5  # tie broken toward smallest class id (5 < 6)

    overlap = build_overlap_matrix(label_map, cls)
    assert overlap.gamus_classes == [0, 1, 2, 3, 4, 5, 6]  # always the fixed 0..6 range
    assert overlap.region_ids == [1, 2]  # region ids taken only from label_map


# --------------------------------------------------------------------------
# Boundary alignment
# --------------------------------------------------------------------------


def test_boundary_alignment_is_perfect_when_rasters_share_boundaries():
    """label_map and cls change value at exactly the same place -> boundary
    precision, recall, and F1 must all be 1.0 (tolerance=0)."""
    label_map = np.array([[1, 1, 2, 2], [1, 1, 2, 2]], dtype=np.uint32)
    cls = np.array([[5, 5, 6, 6], [5, 5, 6, 6]], dtype=np.int64)

    alignment = compute_boundary_alignment(label_map, cls, tolerance_px=0)

    assert alignment.precision == pytest.approx(1.0)
    assert alignment.recall == pytest.approx(1.0)
    assert alignment.f1 == pytest.approx(1.0)
    assert alignment.explicit_reason is None


def test_boundary_alignment_handles_zero_boundary_pixels_explicitly():
    """A fully constant label_map (single region, no boundaries at all)
    must report None + an explicit reason, never a fabricated score."""
    label_map = np.ones((5, 5), dtype=np.uint32)
    cls = np.random.default_rng(1).integers(0, 7, size=(5, 5))

    alignment = compute_boundary_alignment(label_map, cls)

    assert alignment.precision is None
    assert alignment.recall is None
    assert alignment.f1 is None
    assert alignment.explicit_reason is not None


def test_boundary_alignment_tolerance_improves_score_for_shifted_boundary():
    """A region boundary shifted by 1 pixel from the class boundary scores
    worse at tolerance=0 than at tolerance>=1 -- proves the tolerance
    parameter actually does something, not just a documented no-op."""
    label_map = np.array([[1, 1, 1, 2, 2]] * 4, dtype=np.uint32)  # boundary at col 2/3
    cls = np.array([[5, 5, 6, 6, 6]] * 4, dtype=np.int64)  # boundary at col 1/2

    strict = compute_boundary_alignment(label_map, cls, tolerance_px=0)
    tolerant = compute_boundary_alignment(label_map, cls, tolerance_px=2)

    assert strict.f1 < tolerant.f1
    assert tolerant.f1 == pytest.approx(1.0)


def test_boundary_alignment_rejects_negative_tolerance():
    label_map = np.array([[1, 2]], dtype=np.uint32)
    cls = np.array([[1, 2]], dtype=np.int64)

    with pytest.raises(ValueError, match="tolerance_px"):
        compute_boundary_alignment(label_map, cls, tolerance_px=-1)


# --------------------------------------------------------------------------
# Full pipeline sanity check (synthetic, no model)
# --------------------------------------------------------------------------


def test_compute_gamus_semantic_validation_bundles_everything_consistently():
    result = compute_gamus_semantic_validation(EXAMPLE_LABEL_MAP, EXAMPLE_CLS)

    assert result.region_count == 2
    assert result.total_foreground_pixels == EXAMPLE_LABEL_MAP.size
    assert result.total_pixels == EXAMPLE_LABEL_MAP.size
    assert result.area_weighted_mean_purity is not None
    assert result.unweighted_mean_purity is not None
    assert len(result.class_overlap_summaries) == 7
    assert result.overlap_matrix.region_ids == [1, 2]


# --------------------------------------------------------------------------
# Real GAMUS CLS sample (opt-in: skips if the file isn't present)
# --------------------------------------------------------------------------


@pytest.mark.skipif(
    not REAL_GAMUS_CLS_PATH.exists(),
    reason=f"real GAMUS sample not present at {REAL_GAMUS_CLS_PATH}",
)
def test_real_gamus_cls_sample_is_in_the_documented_0_to_6_range():
    cls = load_gamus_h5(str(REAL_GAMUS_CLS_PATH))

    assert cls.shape == (1024, 1024)
    validate_gamus_cls_range(cls)  # must not raise for the real sample
