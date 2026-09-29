"""Process-local model singletons.

An RQ worker (see backend/worker.py) processes jobs one at a time within a
single long-lived process, so caching each constructed estimator here means
its (large) model weights are loaded at most once per worker process — not
once per job — while `load()` remains safe to call unconditionally at the
start of every job (it no-ops once already loaded). No locking is needed:
RQ's default worker executes jobs sequentially, never concurrently, within
one process.

Each model gets its own `@lru_cache(maxsize=1)` getter — this is
deliberately NOT a single dispatch-by-name registry (e.g. `get_estimator("depth")`),
since `DepthEstimator` and `SemanticEstimator` are different interfaces with
different prediction shapes (see ai/depth_estimator.py vs
ai/semantic_estimator.py); a name-keyed dispatcher would need to return a
common supertype that doesn't meaningfully exist. Once both getters have
been called in the same worker process, both models' weights are resident
in memory simultaneously — see docs/ARCHITECTURE.md §3.6 for measured
memory/runtime behavior.
"""

from functools import lru_cache

from ai.depth_anything import DepthAnythingV2Estimator
from ai.depth_estimator import DepthEstimator
from ai.mobile_sam import MobileSAMEstimator
from ai.rs_height_estimator import RemoteSensingHeightEstimator
from ai.semantic_estimator import SemanticEstimator


@lru_cache(maxsize=1)
def get_depth_estimator() -> DepthEstimator:
    return DepthAnythingV2Estimator()


@lru_cache(maxsize=1)
def get_semantic_estimator() -> SemanticEstimator:
    return MobileSAMEstimator()


def get_rs_height_estimator() -> RemoteSensingHeightEstimator:
    """Registration point for a future remote-sensing-specific height model
    (see `ai/rs_height_estimator.py` for the interface it must implement
    and why this extension point exists). Deliberately NOT `@lru_cache`d
    and deliberately does not construct, download, or fabricate anything —
    unlike the two getters above, there is currently no concrete
    `RemoteSensingHeightEstimator` implementation to return. Always raises
    `NotImplementedError` until a real, validated implementation is wired
    in here exactly the way `DepthAnythingV2Estimator`/`MobileSAMEstimator`
    are above."""
    raise NotImplementedError(
        "No RemoteSensingHeightEstimator implementation is registered yet. "
        "See ai/rs_height_estimator.py for the interface a future GAMUS-aware "
        "(or other remote-sensing-specific) height model must implement, and "
        "docs/ARCHITECTURE_NOTE_RS_HEIGHT.md for why this is currently only "
        "an extension point. Register a concrete estimator here exactly like "
        "get_depth_estimator()/get_semantic_estimator() above once one exists."
    )
