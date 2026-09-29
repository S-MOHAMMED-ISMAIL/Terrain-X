"""Real, deterministic unit tests for geospatial/sky_mask.py's conservative
depth-driven sky heuristic — never applied to calibrated DSM/metric
elevation (see app/services/visualization.py's own integration, tested
separately in test_visualization.py)."""

import numpy as np

from geospatial.sky_mask import detect_sky_mask


def test_clear_sky_band_at_top_is_masked():
    # Rows 0-2: uniform low depth (sky-like). Rows 3-5: high depth (terrain).
    depth = np.array(
        [
            [0.0, 0.0, 0.0],
            [0.0, 0.0, 0.0],
            [0.1, 0.1, 0.1],
            [9.0, 9.0, 9.0],
            [9.5, 9.5, 9.5],
            [10.0, 10.0, 10.0],
        ],
        dtype=np.float32,
    )
    mask = detect_sky_mask(depth)
    assert mask[:3].all(), "the low, top-contiguous rows must be masked as sky"
    assert not mask[3:].any(), "the real high-depth terrain rows must not be masked"


def test_uniform_depth_with_no_real_variation_masks_nothing():
    depth = np.full((5, 4), 3.0, dtype=np.float32)
    mask = detect_sky_mask(depth)
    assert not mask.any(), "a flat depth field has no real evidence of sky and must not be masked"


def test_low_depth_region_not_touching_top_is_never_masked():
    # A "valley floor" with the image's own lowest depth values sits in the
    # MIDDLE of the image, never touching row 0 — must not be treated as sky
    # regardless of how low its values are, since sky detection requires
    # contiguity from the top.
    depth = np.array(
        [
            [8.0, 8.0, 8.0],
            [7.5, 7.5, 7.5],
            [0.0, 0.0, 0.0],  # lowest values in the whole image, but mid-frame
            [0.0, 0.0, 0.0],
            [7.0, 7.0, 7.0],
        ],
        dtype=np.float32,
    )
    mask = detect_sky_mask(depth)
    assert not mask.any(), "a low-depth region that never touches row 0 must never be masked as sky"


def test_a_peak_poking_into_the_sky_band_survives():
    # Rows 0-2 are otherwise sky-like (a wide, mostly-uniform low band, as a
    # real image's sky row would be), but row 1 has one real, much higher
    # value (a distant peak silhouette poking into the sky region) — that
    # single cell must NOT be masked, even though it sits within the
    # candidate sky row band; the rest of row 1 must still be masked.
    width = 100
    depth = np.zeros((4, width), dtype=np.float32)
    depth[1, 50] = 8.0  # one real terrain pixel within an otherwise-sky row
    depth[2, :] = 0.1
    depth[3, :] = 9.0
    mask = detect_sky_mask(depth)
    assert not mask[1, 50], "a real terrain feature poking into the sky band must not be stripped"
    assert (
        mask[1, 0] and mask[1, -1]
    ), "the genuinely sky-like cells in the same row must stay masked"


def test_all_nan_input_is_fully_masked_as_invalid():
    depth = np.full((3, 3), np.nan, dtype=np.float32)
    mask = detect_sky_mask(depth)
    assert mask.all()


def test_preexisting_nan_cells_always_stay_masked():
    depth = np.array(
        [
            [np.nan, 5.0, 5.0],
            [5.0, 5.0, 5.0],
        ],
        dtype=np.float32,
    )
    mask = detect_sky_mask(depth)
    assert mask[
        0, 0
    ], "a pre-existing invalid/NaN cell must stay masked regardless of sky detection"


def test_mask_shape_matches_input():
    depth = np.random.rand(17, 23).astype(np.float32)
    mask = detect_sky_mask(depth)
    assert mask.shape == depth.shape
    assert mask.dtype == np.bool_


def test_sky_fraction_is_capped_and_never_consumes_the_whole_image():
    # A depth field that is low almost everywhere (only the very last row is
    # real terrain) must still respect the safety cap rather than masking
    # ~100% of the grid.
    depth = np.zeros((20, 4), dtype=np.float32)
    depth[-1, :] = 10.0
    mask = detect_sky_mask(depth, max_sky_fraction=0.9)
    masked_rows = mask.all(axis=1).sum()
    assert masked_rows <= 18, "the sky band must never exceed the configured safety cap"


def test_does_not_mutate_input_array():
    depth = np.array([[0.0, 0.0], [9.0, 9.0]], dtype=np.float32)
    original = depth.copy()
    detect_sky_mask(depth)
    np.testing.assert_array_equal(depth, original)
