# METRIC PATH FEASIBILITY — RESEARCH ONLY

**Date:** 2026-09-29
**Scope:** Research only. No code changes, no installations, no model downloads.

---

## 1. What depth estimators/models are currently implemented?

| Model | Role | Status |
|---|---|---|
| **Depth Anything V2 Small** | Monocular relative depth | **Production** — i/depth_anything.py |
| **MobileSAM** | Class-agnostic region segmentation | **Production** — i/mobile_sam.py |
| **RemoteSensingHeightEstimator** | Future RS height model | **Extension point only** — i/rs_height_estimator.py (no implementation) |

The production depth pipeline uses **Depth Anything V2 Small** exclusively. The RemoteSensingHeightEstimator interface exists but has no concrete implementation.

---

## 2. Which estimator generated the current negative-scale result (~ -279.488)?

**Depth Anything V2 Small** (depth-anything/Depth-Anything-V2-Small-hf, revision 5426e4f0f36572d16453bbda7a8389317b1bef99).

The model was applied to mountain_georeferenced_rgb.tif. Its output has a **negative correlation** with the reference DEM (Pearson r = -0.9857), producing a fitted scale of -279.488. The calibration quality gate (G1: expected_scale_sign) correctly rejected this.

**Root cause**: Depth Anything V2 was trained on ground-level photography, not aerial/nadir remote-sensing imagery. Its depth convention is inverted relative to the synthetic dataset's elevation values.

---

## 3. What exact interface does the production depth pipeline expect?

The production pipeline expects a DepthEstimator implementation with this interface:

`python
class DepthEstimator(ABC):
    def load(self) -> None: ...           # Idempotent model loading
    def predict(self, rgb_image: np.ndarray) -> DepthPrediction: ...  # (H, W, 3) uint8 → depth
    def info(self) -> DepthModelInfo: ...  # Static model metadata
`

**Input**: (height, width, 3) uint8 RGB array (extracted by pp/services/depth_pipeline.py::extract_rgb_uint8)

**Output**: DepthPrediction with:
- depth: float32 array, shape (input_height, input_width) — raw relative depth, resized to source resolution
- inference_seconds: float
- input_width, input_height: int
- model_input_width, model_input_height: int

**Registration**: i/registry.py::get_depth_estimator() returns a singleton estimator. To plug in a new model, implement DepthEstimator and update this function.

---

## 4. Can another depth estimator be plugged into the existing architecture without changing calibration logic?

**YES.** The architecture is explicitly designed for this:

1. **Model-agnostic interface**: DepthEstimator ABC in i/depth_estimator.py
2. **Registry pattern**: i/registry.py::get_depth_estimator() is the single registration point
3. **Calibration is model-agnostic**: pp/services/calibration_pipeline.py fits Z = a * D + b regardless of which estimator produced D
4. **Extension point ready**: i/rs_height_estimator.py defines a sibling interface for future RS height models

A new estimator would:
1. Implement load(), predict(), info()
2. Return a DepthPrediction with the correct value convention (larger = closer = higher elevation)
3. Be registered in i/registry.py

**No calibration code changes needed.**

---

## 5. Which existing model/checkpoint files are already present locally?

| Model | Location | Size |
|---|---|---|
| Depth Anything V2 Small | storage/model_cache/hub/models--depth-anything--Depth-Anything-V2-Small-hf/ | ~99 MB |
| MobileSAM | storage/model_cache/mobile_sam/mobile_sam.pt | 40.7 MB |

No other depth model checkpoints are present locally.

---

## 6. Which models previously investigated in TERRAIN-X could potentially produce metric-compatible aerial depth?

**No other depth models have been investigated.** The repository contains:

- **Depth Anything V2 Small** — the only depth estimator implemented
- **MobileSAM** — segmentation only, not depth
- **GAMUS dataset** — used for validation, not as a model
- **RemoteSensingHeightEstimator** — extension point with no implementation

The docs/ARCHITECTURE_NOTE_RS_HEIGHT.md documents that Depth Anything V2 was benchmarked against GAMUS and found to have no whole-image linear relationship to AGL height (Pearson r = -0.072, R² = 0.0052). A MobileSAM-region-conditioned diagnostic found stronger local correlation (median |r| = 0.661), but this is single-tile, unreplicated evidence.

---

## 7. For each candidate, state:

### Depth Anything V2 Small (current)

| Attribute | Value |
|---|---|
| Code availability | **Available** — i/depth_anything.py |
| Checkpoint availability | **Available** — storage/model_cache/hub/models--depth-anything--Depth-Anything-V2-Small-hf/ |
| License | Apache-2.0 |
| Input requirements | 8-bit RGB imagery, max 4096px |
| Output semantics | Relative, unitless, inverse depth (larger = closer) |
| GAMUS/RS evidence | **Negative** — r = -0.072, R² = 0.0052 on GAMUS tile |
| Sufficient for production? | **No** — fails calibration on aerial imagery |

### RemoteSensingHeightEstimator (future)

| Attribute | Value |
|---|---|
| Code availability | **Interface only** — i/rs_height_estimator.py |
| Checkpoint availability | **None** — no model selected |
| License | **Unknown** — no model chosen |
| Input requirements | Same as DepthEstimator (H, W, 3) uint8 |
| Output semantics | **Unknown** — defined by future implementation |
| GAMUS/RS evidence | **None** — no model exists |
| Sufficient for production? | **No** — no implementation |

### MobileSAM (segmentation, not depth)

| Attribute | Value |
|---|---|
| Code availability | **Available** — i/mobile_sam.py |
| Checkpoint availability | **Available** — storage/model_cache/mobile_sam/mobile_sam.pt |
| License | Apache-2.0 |
| Input requirements | 8-bit RGB imagery |
| Output semantics | Categorical region IDs (not depth/height) |
| GAMUS/RS evidence | **Weak** — region-conditioned correlation only |
| Sufficient for production? | **No** — not a depth estimator |

---

## 8. What is the minimum technically honest path to demonstrate:

RGB → depth → positive calibration → metric elevation → DSM → DTM → nDSM?

**The minimum path requires a new depth estimator that produces output with the correct sign convention.**

Steps:
1. **Select a depth model** trained/adapted for aerial/nadir remote-sensing imagery
2. **Implement DepthEstimator** interface for the new model
3. **Register in i/registry.py**
4. **Verify output convention**: larger depth = closer = higher elevation
5. **Run calibration**: the existing pipeline will fit Z = a * D + b and check G1 (scale sign)
6. **If calibration passes**: metric elevation, DSM, DTM, nDSM all follow automatically

**No calibration code changes needed** — only a new estimator implementation.

---

## 9. Identify anything that is impossible with the current repository/data without adding a new validated model.

**Impossible without a new validated model:**

1. **Positive calibration on the current synthetic dataset** — Depth Anything V2 produces inverted output
2. **Metric elevation** — requires successful calibration
3. **DSM** — requires successful calibration
4. **DTM** — requires calibrated DSM
5. **nDSM** — requires calibrated DSM
6. **Terrain derivatives** (slope, aspect, hillshade) — require metric elevation
7. **Metric measurements** — require metric elevation
8. **Disaster screening** — requires metric elevation

**Possible with current repository:**
- Relative depth visualization
- Relative 3D terrain
- Relative measurements (point, distance, profile)
- Flythrough on relative terrain
- GLB export of relative terrain
- Reports preserving relative-only semantics

---

## Engineering Recommendation

**The architecture is ready for a new depth estimator.** The DepthEstimator interface, registry pattern, and calibration pipeline are all model-agnostic. Integrating a new model requires:

1. Implementing load(), predict(), info() for the new model
2. Registering it in i/registry.py
3. Verifying the output convention matches the calibration gate's expectation

**The blocker is not architectural — it's the absence of a suitable model.** Depth Anything V2 was trained on ground-level photography and produces inverted output on aerial imagery. A model trained or adapted for nadir/aerial remote-sensing imagery is needed.

**Recommended next step**: Investigate depth models specifically trained on aerial/nadir imagery (e.g., from the remote-sensing community) and evaluate their output convention against the GAMUS dataset before integration.

---

**No code was modified. No dependencies were installed. No models were downloaded. No Git commit was created.**