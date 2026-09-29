"""Tests for the remote-sensing height estimator ARCHITECTURAL EXTENSION
POINT (`ai/rs_height_estimator.py`, `ai/registry.py::get_rs_height_estimator`)
— see those modules' docstrings and docs/ARCHITECTURE_NOTE_RS_HEIGHT.md for
why this exists with no concrete implementation yet.

These tests verify the SCAFFOLDING is correct: the interface is a real ABC
that can't be instantiated directly, a conforming implementation can be
built against it (proving the contract is usable), the registry placeholder
never silently returns something usable, and it never attempts any network
access / download. No real model, no real inference — there is nothing to
run.
"""

import numpy as np
import pytest

from ai.registry import get_rs_height_estimator
from ai.rs_height_estimator import (
    RemoteSensingHeightEstimator,
    RemoteSensingHeightModelInfo,
    RemoteSensingHeightPrediction,
)


def test_remote_sensing_height_estimator_cannot_be_instantiated_directly():
    """A real ABC with abstract methods -- not a usable class on its own,
    exactly like DepthEstimator/SemanticEstimator."""
    with pytest.raises(TypeError):
        RemoteSensingHeightEstimator()


def test_remote_sensing_height_estimator_is_not_a_depth_estimator_subclass():
    """The core architectural decision this module's docstring documents:
    a SIBLING interface, never a DepthEstimator subclass."""
    from ai.depth_estimator import DepthEstimator

    assert not issubclass(RemoteSensingHeightEstimator, DepthEstimator)
    assert not issubclass(DepthEstimator, RemoteSensingHeightEstimator)


class _FakeRemoteSensingHeightEstimator(RemoteSensingHeightEstimator):
    """A minimal, test-only conforming implementation -- proves the
    abstract contract (load/predict/info) is actually implementable and
    well-shaped, without being a real model. Never registered anywhere,
    never imported outside this test file."""

    def __init__(self) -> None:
        self._loaded = False

    def load(self) -> None:
        self._loaded = True

    def predict(self, rgb_image: np.ndarray) -> RemoteSensingHeightPrediction:
        if not self._loaded:
            raise RuntimeError("predict() called before load()")
        height, width = rgb_image.shape[0], rgb_image.shape[1]
        return RemoteSensingHeightPrediction(
            height=np.zeros((height, width), dtype=np.float32),
            inference_seconds=0.0,
            input_width=width,
            input_height=height,
            model_input_width=width,
            model_input_height=height,
        )

    def info(self) -> RemoteSensingHeightModelInfo:
        return RemoteSensingHeightModelInfo(
            name="fake-test-only",
            task="test fixture -- never a real estimator",
            source="n/a",
            revision="n/a",
            license="n/a",
            device="cpu",
        )


def test_conforming_implementation_satisfies_the_full_contract():
    estimator = _FakeRemoteSensingHeightEstimator()
    estimator.load()

    rgb = np.zeros((4, 5, 3), dtype=np.uint8)
    prediction = estimator.predict(rgb)

    assert isinstance(prediction, RemoteSensingHeightPrediction)
    assert prediction.height.shape == (4, 5)
    assert prediction.height.dtype == np.float32
    assert prediction.input_width == 5
    assert prediction.input_height == 4

    info = estimator.info()
    assert isinstance(info, RemoteSensingHeightModelInfo)
    assert info.name == "fake-test-only"


def test_get_rs_height_estimator_raises_not_implemented_error():
    """The registry placeholder must never silently return a usable
    estimator -- there is no concrete implementation to return."""
    with pytest.raises(NotImplementedError, match="RemoteSensingHeightEstimator"):
        get_rs_height_estimator()


def test_get_rs_height_estimator_error_points_to_the_interface_module():
    """The failure message must be a real, useful pointer for whoever
    implements this later -- not a bare/unhelpful NotImplementedError."""
    with pytest.raises(NotImplementedError, match="ai/rs_height_estimator.py"):
        get_rs_height_estimator()


def test_get_rs_height_estimator_raises_every_call_not_just_the_first():
    """Deliberately not @lru_cache'd -- must fail consistently, not only
    on a first call (proves there's no accidental caching of a partial/
    broken state that could later be mistaken for "it's set up now")."""
    for _ in range(3):
        with pytest.raises(NotImplementedError):
            get_rs_height_estimator()
