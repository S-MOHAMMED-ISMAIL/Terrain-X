# SEMANTIC 3D RECONSTRUCTION

**Status:** Architecture implemented, validation-only (no semantic model integrated)
**Date:** 2026-09-29

---

## 1. Architecture

### Current State

| Component | File | Status |
|---|---|---|
| Semantic mesh preparation | geospatial/semantic_mesh.py | Implemented (numpy-only) |
| Semantic segmentation interface | i/semantic_segmentation_estimator.py | Interface only |
| Terrain grid integration | geospatial/semantic_terrain_grid.py | Implemented |
| Tests | ackend/tests/test_semantic_mesh.py | 12/12 pass |

### What Was Implemented

**Region-aware mesh preparation** (geospatial/semantic_mesh.py):
- Takes raw depth + MobileSAM region map as input
- Applies region-specific regularization:
  - Building-like regions: plane fitting for coherent roof surfaces
  - Road-like regions: median filtering for smooth draped surfaces
  - Water-like regions: flattening towards median depth
  - Generic regions: Gaussian smoothing
- Suppresses isolated depth spikes outside regions
- Preserves original raw depth (visualization-only transformation)

**Semantic segmentation interface** (i/semantic_segmentation_estimator.py):
- Defines SemanticSegmentationEstimator ABC
- Defines standard remote-sensing classes (GROUND, BUILDING, ROAD, TREE, WATER, LOW_VEGETATION)
- No concrete implementation (no suitable model available)

---

## 2. Critical Scientific Distinction

### What This Is
- **Region-aware visualization smoothing** using MobileSAM's class-agnostic regions
- Improves visualization coherence by suppressing monocular depth noise
- Does NOT perform semantic classification

### What This Is NOT
- NOT semantic segmentation (cannot distinguish building/road/tree/water)
- NOT metric elevation (relative depth remains unitless)
- NOT a replacement for the calibration pipeline

### Why No Semantic Model Is Integrated

| Requirement | Status |
|---|---|
| Remote-sensing semantic model | Not present in repository |
| Verified weights | Not available |
| License verification | Not possible without model |
| CPU compatibility | Not verified |
| GAMUS validation evidence | Not available |

**Decision:** Per the final decision rule, since a fully valid semantic model cannot be integrated quickly and correctly, the architecture is implemented but no semantic inference is claimed.

---

## 3. Region Classification (Visualization Only)

The mesh preparation uses simple geometric/statistical heuristics:

| Region Type | Detection Criteria | Regularization |
|---|---|---|
| Building-like | Area >= 200px, std < 0.1 | Plane fitting |
| Road-like | Aspect ratio >= 3.0 | Median filtering |
| Water-like | Std < 0.1 | Flattening |
| Generic | All other regions | Gaussian smoothing |

**These are NOT semantic classes.** They are geometric heuristics for visualization smoothing.

---

## 4. Building Reconstruction

### Current Capability
- Plane fitting for large coherent regions
- Creates planar roof surfaces from depth data
- Suppresses high-frequency monocular noise

### Limitations
- Cannot distinguish buildings from other large flat regions
- No true building detection
- No architectural detail generation

---

## 5. Road Handling

### Current Capability
- Median filtering for elongated regions
- Smooths depth noise while preserving road shape

### Limitations
- Cannot distinguish roads from other elongated features
- No true road detection

---

## 6. Tree Handling

### Current Capability
- Generic smoothing for non-building, non-road, non-water regions
- Reduces spike noise

### Limitations
- Cannot distinguish trees from other vegetation
- No canopy modeling

---

## 7. Water Handling

### Current Capability
- Flattening for flat regions
- Creates approximately planar surfaces

### Limitations
- Cannot distinguish water from other flat surfaces
- No true water detection

---

## 8. Validation

### Test Results
- 12/12 unit tests pass
- Tests cover: config, region detection, spike suppression, statistics

### GAMUS Validation
**Not performed** — no semantic model to validate against reference labels.

### Synthetic Dataset Validation
**Not performed** — no semantic model to validate against reference labels.

---

## 9. Performance

| Operation | Time | Notes |
|---|---|---|
| Mesh preparation | ~5ms | For 10x10 array |
| Region classification | ~1ms | Per region |
| Spike suppression | ~2ms | Median filter |

**Note:** Performance measured on small test arrays. Production performance depends on image size and region count.

---

## 10. Fallback Behavior

If semantic model unavailable:
- Existing relative terrain continues to work
- Standard terrain mesh is used
- No error or degradation

If semantic inference fails:
- Falls back to standard terrain
- Shows: "Semantic reconstruction unavailable — standard terrain remains available."

---

## 11. Files Changed

| File | Change |
|---|---|
| geospatial/semantic_mesh.py | New: region-aware mesh preparation |
| i/semantic_segmentation_estimator.py | New: semantic segmentation interface |
| geospatial/semantic_terrain_grid.py | New: terrain grid integration |
| ackend/tests/test_semantic_mesh.py | New: unit tests |

---

## 12. What Would Be Needed for True Semantic 3D

1. **A remote-sensing semantic segmentation model** with:
   - Verified weights and license
   - CPU compatibility
   - Classes mapping to: GROUND, BUILDING, ROAD, TREE, WATER, LOW_VEGETATION

2. **Integration**:
   - Implement SemanticSegmentationEstimator for the chosen model
   - Register in i/registry.py
   - Add semantic artifact type
   - Add semantic-aware mesh preparation using true class labels

3. **Validation**:
   - Run on GAMUS dataset
   - Compute IoU, per-class precision/recall, confusion matrix
   - Compare against reference labels

---

## 13. Honest Assessment

**What works now:**
- Region-aware smoothing improves visualization coherence
- Architecture is ready for a future semantic model
- All existing functionality remains intact

**What does NOT work:**
- True semantic classification (building/road/tree/water detection)
- Semantic-aware mesh using true class labels
- Validation against GAMUS reference labels

**The blocker is the absence of a suitable semantic model, not the architecture.**

---

**No production code modified. No calibration gates changed. No thresholds changed. No Git commit created.**