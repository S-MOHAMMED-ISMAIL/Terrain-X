"""GAMUS validation capability tests (development/validation-only — see
geospatial/gamus_validation.py's module docstring).

All tests below are pure, fast unit tests of `geospatial.gamus_validation`
(h5 loading, the valid-pixel mask policy, and the R²/Pearson additions to
the reused `geospatial.calibration` fitting/metrics) — no backend, no model,
no worker, no network, matching the "unit tests: geospatial.calibration
(pure, no backend/model/worker)" section of test_calibration.py.

`h5py` is a dependency of the GAMUS validation capability only (never a
dependency of the production pipeline — see docs/ARCHITECTURE.md §4.4); it
is not currently installed in the backend/worker Docker image
(`backend/requirements.txt`), so the whole module is skipped cleanly via
`pytest.importorskip` wherever it isn't available, rather than failing
collection.

The one test that reads the real downloaded GAMUS sample
(`TERRAIN-X-TEST-DATA/03_gamus_validation/...`) is opt-in: it skips itself
if that file isn't present on disk, so the suite never depends on
downloaded test data being available in every environment (that directory
is untracked local test data, not committed fixtures).
"""

from pathlib import Path

import numpy as np
import pytest

h5py = pytest.importorskip("h5py")

from geospatial.calibration import DegenerateCalibrationError  # noqa: E402
from geospatial.gamus_validation import (  # noqa: E402
    GamusH5FormatError,
    build_gamus_valid_mask,
    compute_extended_validation_metrics,
    fit_and_validate_gamus,
    load_gamus_h5,
)

# tests/ -> backend/ -> repo root -> TERRAIN-X-TEST-DATA/...
REAL_GAMUS_AGL_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "TERRAIN-X-TEST-DATA"
    / "03_gamus_validation"
    / "heights"
    / "test"
    / "DC_03_26_AGL.h5"
)


def _write_h5(path: Path, key: str, data: np.ndarray) -> None:
    with h5py.File(path, "w") as f:
        f.create_dataset(key, data=data)


# --------------------------------------------------------------------------
# load_gamus_h5
# --------------------------------------------------------------------------


def test_load_gamus_h5_reads_the_image_dataset(tmp_path):
    path = tmp_path / "sample.h5"
    expected = np.arange(12, dtype="float32").reshape(3, 4)
    _write_h5(path, "image", expected)

    loaded = load_gamus_h5(str(path))

    np.testing.assert_array_equal(loaded, expected)


def test_load_gamus_h5_raises_clear_error_on_missing_image_key(tmp_path):
    path = tmp_path / "not_gamus.h5"
    _write_h5(path, "not_image", np.zeros((2, 2), dtype="float32"))

    with pytest.raises(GamusH5FormatError, match="image"):
        load_gamus_h5(str(path))


# --------------------------------------------------------------------------
# build_gamus_valid_mask
# --------------------------------------------------------------------------


def test_build_gamus_valid_mask_keeps_finite_positive_values():
    agl = np.array([1.0, 2.5, 10.0])
    depth = np.array([0.1, 0.2, 0.3])

    mask = build_gamus_valid_mask(agl, depth)

    assert mask.tolist() == [True, True, True]


def test_build_gamus_valid_mask_keeps_negative_values():
    """The core scientific-honesty requirement: negative AGL (including the
    real sample's -5.0 clamp floor, see geospatial/gamus_validation.py) must
    NOT be rejected just for being negative — it is real, bounded ground
    truth, not a nodata sentinel."""
    agl = np.array([-5.0, -1.0, -0.001, 0.0])
    depth = np.array([0.1, 0.2, 0.3, 0.4])

    mask = build_gamus_valid_mask(agl, depth)

    assert mask.all()


def test_build_gamus_valid_mask_rejects_nan():
    agl = np.array([1.0, np.nan, 3.0])
    depth = np.array([0.1, 0.2, 0.3])

    mask = build_gamus_valid_mask(agl, depth)

    assert mask.tolist() == [True, False, True]


def test_build_gamus_valid_mask_rejects_positive_infinity():
    agl = np.array([1.0, np.inf, 3.0])
    depth = np.array([0.1, 0.2, 0.3])

    mask = build_gamus_valid_mask(agl, depth)

    assert mask.tolist() == [True, False, True]


def test_build_gamus_valid_mask_rejects_negative_infinity():
    agl = np.array([1.0, -np.inf, 3.0])
    depth = np.array([0.1, 0.2, 0.3])

    mask = build_gamus_valid_mask(agl, depth)

    assert mask.tolist() == [True, False, True]


def test_build_gamus_valid_mask_rejects_nan_or_inf_in_depth_too():
    agl = np.array([1.0, 2.0, 3.0])
    depth = np.array([0.1, np.nan, np.inf])

    mask = build_gamus_valid_mask(agl, depth)

    assert mask.tolist() == [True, False, False]


def test_build_gamus_valid_mask_respects_explicit_nodata_when_given():
    agl = np.array([1.0, -9999.0, 3.0])
    depth = np.array([0.1, 0.2, 0.3])

    mask = build_gamus_valid_mask(agl, depth, agl_nodata=-9999.0)

    assert mask.tolist() == [True, False, True]


def test_build_gamus_valid_mask_raises_clear_error_on_shape_mismatch():
    agl = np.zeros((4, 4))
    depth = np.zeros((3, 3))

    with pytest.raises(ValueError, match="shape"):
        build_gamus_valid_mask(agl, depth)


# --------------------------------------------------------------------------
# R² and Pearson r (via compute_extended_validation_metrics / fit_and_validate_gamus)
# --------------------------------------------------------------------------


def test_extended_metrics_r2_is_one_for_a_perfect_fit():
    depth = np.linspace(0.1, 9.9, 50)
    agl = 2.0 * depth + 1.0  # exact, no noise

    fit, metrics = fit_and_validate_gamus(
        depth, agl, np.ones_like(depth, dtype=bool), outlier_sigma=2.5, max_iterations=5
    )

    assert abs(metrics.r2 - 1.0) < 1e-9
    assert metrics.r2_explicit_reason is None


def test_extended_metrics_r2_matches_hand_computed_value_for_a_known_imperfect_fit():
    """predicted depth [1,2,3,4,5] with reference [3,5,7,9,11] is an EXACT
    scale=2/offset=1 relationship (see the dedicated affine-recovery test
    below); this test instead perturbs the reference to get a known,
    hand-computable R² < 1."""
    depth = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    # Exact fit would be [3, 5, 7, 9, 11]; perturb one point.
    agl = np.array([3.0, 5.0, 7.0, 9.0, 15.0])

    fit, metrics = fit_and_validate_gamus(
        depth,
        agl,
        np.ones_like(depth, dtype=bool),
        # A large outlier_sigma keeps every point as an inlier, so R² is
        # computed (and hand-verifiable) against the OLS fit on all 5 points,
        # not a sigma-clipped subset.
        outlier_sigma=100.0,
        max_iterations=1,
    )

    a, b = np.polyfit(depth, agl, 1)
    predicted = a * depth + b
    ss_res = float(np.sum((agl - predicted) ** 2))
    ss_tot = float(np.sum((agl - np.mean(agl)) ** 2))
    expected_r2 = 1.0 - ss_res / ss_tot

    assert abs(metrics.r2 - expected_r2) < 1e-9
    assert 0.0 < metrics.r2 < 1.0


def test_extended_metrics_r2_is_explicit_not_nan_for_zero_variance_reference():
    depth = np.linspace(0.1, 9.9, 20)
    agl = np.full(20, 7.0)  # zero variance -- SS_tot == 0

    fit, metrics = fit_and_validate_gamus(
        depth, agl, np.ones_like(depth, dtype=bool), outlier_sigma=2.5, max_iterations=5
    )

    assert np.isnan(metrics.r2)
    assert metrics.r2_explicit_reason is not None
    assert "variance" in metrics.r2_explicit_reason


def test_extended_metrics_pearson_r_is_one_for_perfect_positive_correlation():
    depth = np.linspace(1.0, 10.0, 30)
    agl = 3.0 * depth + 2.0

    fit, metrics = fit_and_validate_gamus(
        depth, agl, np.ones_like(depth, dtype=bool), outlier_sigma=2.5, max_iterations=5
    )

    assert abs(metrics.pearson_r - 1.0) < 1e-9
    assert metrics.pearson_r_explicit_reason is None


def test_extended_metrics_pearson_r_is_negative_one_for_perfect_negative_correlation():
    depth = np.linspace(1.0, 10.0, 30)
    agl = -4.0 * depth + 50.0

    fit, metrics = fit_and_validate_gamus(
        depth, agl, np.ones_like(depth, dtype=bool), outlier_sigma=2.5, max_iterations=5
    )

    assert abs(metrics.pearson_r - (-1.0)) < 1e-9
    assert metrics.pearson_r_explicit_reason is None


def test_extended_metrics_pearson_r_is_explicit_not_nan_for_zero_variance_depth():
    """Zero-variance relative depth is already rejected by fit_robust_affine
    itself (DegenerateCalibrationError) before Pearson r would ever be
    computed on it -- verified directly here for the underlying
    compute_extended_validation_metrics building block, using a fit object
    built the same way test_calibration.py's own metrics test does."""
    from geospatial.calibration import RobustFitResult

    depth = np.full(10, 5.0)
    agl = np.linspace(0.0, 100.0, 10)
    fit = RobustFitResult(
        scale=0.0,
        offset=5.0,
        inlier_mask=np.ones(10, dtype=bool),
        iterations_used=1,
        outlier_sigma=2.5,
    )

    metrics = compute_extended_validation_metrics(depth, agl, fit)

    assert np.isnan(metrics.pearson_r)
    assert metrics.pearson_r_explicit_reason is not None
    assert "variance" in metrics.pearson_r_explicit_reason


def test_fit_and_validate_gamus_raises_degenerate_error_on_zero_variance_depth():
    depth = np.full(20, 5.0)
    agl = np.linspace(0.0, 100.0, 20)

    with pytest.raises(DegenerateCalibrationError):
        fit_and_validate_gamus(
            depth, agl, np.ones_like(depth, dtype=bool), outlier_sigma=2.5, max_iterations=5
        )


# --------------------------------------------------------------------------
# Known synthetic affine recovery (scale=2, offset=1)
# --------------------------------------------------------------------------


def test_fit_and_validate_gamus_recovers_known_scale_and_offset():
    depth = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    agl = np.array([3.0, 5.0, 7.0, 9.0, 11.0])  # exactly 2*depth + 1

    fit, metrics = fit_and_validate_gamus(
        depth, agl, np.ones_like(depth, dtype=bool), outlier_sigma=2.5, max_iterations=5
    )

    assert abs(fit.scale - 2.0) < 1e-6
    assert abs(fit.offset - 1.0) < 1e-6
    assert abs(metrics.base.mae - 0.0) < 1e-9
    assert abs(metrics.r2 - 1.0) < 1e-9
    assert abs(metrics.pearson_r - 1.0) < 1e-9


# --------------------------------------------------------------------------
# Real GAMUS sample regression test (opt-in: skips if the file isn't present)
# --------------------------------------------------------------------------


@pytest.mark.skipif(
    not REAL_GAMUS_AGL_PATH.exists(),
    reason=f"real GAMUS sample not present at {REAL_GAMUS_AGL_PATH}",
)
def test_real_gamus_agl_sample_matches_inspected_statistics():
    """Regression test locking in the empirical AGL statistics that
    justified build_gamus_valid_mask's "don't reject negative values"
    policy (see geospatial/gamus_validation.py) — found by direct
    inspection of TERRAIN-X-TEST-DATA/03_gamus_validation/heights/test/
    DC_03_26_AGL.h5: shape (1024, 1024), every pixel finite, and exactly
    4963 pixels at the -5.0 clamp floor. If this ever fails, the sample
    file changed and the mask policy's rationale should be re-verified
    against the new data, not silently assumed to still hold."""
    agl = load_gamus_h5(str(REAL_GAMUS_AGL_PATH))

    assert agl.shape == (1024, 1024)
    assert np.isfinite(agl).all()
    assert int((agl == -5.0).sum()) == 4963
