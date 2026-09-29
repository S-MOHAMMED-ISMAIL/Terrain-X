# Architecture note: remote-sensing height — an extension point, not a feature

This note explains a small set of scaffolding-only additions (an unused
sibling model interface, a registry placeholder that always raises, and a
reserved-but-unused third `height_kind` value) added to prepare TERRAIN-X
for a *future* remote-sensing-specific height model, **without
implementing one**. It exists so the reasoning behind those additions is
recorded in one place rather than scattered across commit messages.

## 1. Generic relative depth is not metric elevation — and still isn't

Nothing here changes this. `ai/depth_anything.py` (Depth Anything V2
Small) still produces relative, unitless, scale-ambiguous inverse depth —
exactly as documented in `ai/depth_estimator.py` and
`RELATIVE_DEPTH_VALUE_SEMANTICS` (`app/services/depth_pipeline.py`). The
only way TERRAIN-X currently produces real metric elevation is the
existing DEM/GCP calibration path (`app/services/calibration_pipeline.py`
→ `geospatial/calibration.py`), which fits a real affine relationship
against a real reference and reports honest residual statistics. That path
is completely unmodified by this note's additions.

## 2. Remote-sensing height is a distinct *future* model output

A generic, ground-level-photo-trained depth model and a model built or
adapted specifically for nadir/aerial remote-sensing imagery are not the
same thing and should not be pretended to be. `ai/rs_height_estimator.py`
defines `RemoteSensingHeightEstimator` as a **sibling** interface to
`DepthEstimator` — not a subclass — for the same reason
`ai/semantic_estimator.py` already gives for keeping `SemanticEstimator`
separate: forcing different output semantics into one shared interface
blurs what each model actually produces.

**There is no concrete implementation.** `ai/registry.py::get_rs_height_estimator()`
always raises `NotImplementedError`. No model has been downloaded,
selected, trained, or wired into any pipeline.

## 3. GAMUS building-region correlation is experimental evidence only

The motivation for keeping this extension point ready — not for building
anything yet — comes from two real, already-run diagnostics on GAMUS tile
`DC_03_26`:

- **Whole-image baseline** (`storage/validation/gamus/DC_03_26_validation.json`):
  Depth Anything V2 vs. GAMUS AGL, MAE 9.315 m, RMSE 10.823 m, R² 0.0052,
  Pearson r = -0.072 — essentially no linear relationship globally.
- **MobileSAM-region-conditioned diagnostic** (`storage/validation/gamus/DC_03_26_region_correlation.json`/`_report.md`):
  restricted to individual MobileSAM regions, building-majority regions
  showed much stronger local correlation — median |r| = 0.661 across 35
  regions / 56,861 valid pixels, best single region |r| = 0.922 (n=562).

**This is single-tile, unreplicated evidence.** A second GAMUS tile was
not available locally to attempt replication (see the stopped replication
request in this project's history) — per that instruction, the
building-region finding **must not** be treated as validated, and **no
production calibration strategy currently depends on it.**

## 4. Replication is pending additional GAMUS tiles

Before any per-region or GAMUS-aware calibration strategy is implemented
in production, the building-region correlation finding needs to be
checked against at least 2 more GAMUS tiles — ideally from a different
city than Washington DC (`DC_03_26`'s prefix) — using the exact same
`geospatial/gamus_region_correlation.py` methodology already built and
tested. That replication has not happened; it is blocked on additional
GAMUS tile data not currently present in `TERRAIN-X-TEST-DATA/`.

## 5. No production calibration depends on MobileSAM regions

`geospatial/calibration.py` (the real, production DEM/GCP calibration
math) is unmodified. `app/services/calibration_pipeline.py`'s orchestration
is unmodified. MobileSAM's real production role remains exactly what it
was before this note: class-agnostic region segmentation
(`app/services/semantic_pipeline.py`), never a calibration input. The
GAMUS region-correlation diagnostic that uses MobileSAM regions
(`geospatial/gamus_region_correlation.py`) is evaluation-only tooling, not
imported by any production code path.

## 6. What exists now (scaffolding only)

| Addition | File | Status |
|---|---|---|
| `RemoteSensingHeightEstimator` ABC, `RemoteSensingHeightPrediction`, `RemoteSensingHeightModelInfo` | `ai/rs_height_estimator.py` | Interface only, no implementation |
| `get_rs_height_estimator()` | `ai/registry.py` | Always raises `NotImplementedError` |
| `height_kind: Literal[..., "remote_sensing_height"]` | `backend/app/schemas/visualization.py` | Type accepts the value; nothing produces it |
| `height_kind_for_artifact_type("remote_sensing_height")` → `"remote_sensing_height"` | `backend/app/services/visualization.py` | Correct classification for a value that never occurs today |
| `HeightKind` TS union widened | `frontend/src/api/types.ts` | Type only; no rendering branch added |

**Deliberately NOT done**: no `app/services/rs_height_pipeline.py` (no
orchestration exists to wire), no new `AnalysisStage`/job status fields, no
change to `TERRAIN_SOURCE_ARTIFACT_TYPES` (still `{"dsm", "relative_depth"}`
— widening it now would accept a terrain source that can never exist), and
no change to `TerrainView3D.tsx`'s rendering branches (its existing
`=== "relative_depth"` checks already fall through safely to
elevation-like behavior for any other value, including the new one, so no
renderer change is required for this scaffolding to be safe).

## 7. Remaining work before a real RS height model can be integrated

In roughly the order it would need to happen:

1. **Replicate** the building-region correlation finding on 2+ additional
   GAMUS tiles (see §4) — or determine it doesn't hold and drop this
   direction.
2. **Choose or build a real model.** Prior research this project has
   already done found no verified, licensed, checkpoint-available
   RGB-only GAMUS height model (Depth2Elevation: real paper, no released
   code/checkpoint from the actual authors; TerraHeight-S: unverified
   third-party provenance, not used). A real implementation likely means
   either training/fine-tuning something in-house, or a legitimate release
   appearing later.
3. **Implement `RemoteSensingHeightEstimator`** against the interface in
   `ai/rs_height_estimator.py`, following the same conventions
   `ai/depth_anything.py`/`ai/mobile_sam.py` already establish (pinned
   revision, checksum verification, documented real value semantics in
   `info().task`).
4. **Register it** in `ai/registry.py::get_rs_height_estimator()`.
5. **Decide the production artifact/calibration story**: does
   `"remote_sensing_height"` become a new `analysis_artifacts.artifact_type`
   (no migration needed — it's a plain `String(50)` column, see
   `docs/ARCHITECTURE.md` §4.5)? Does it need its own calibration/validation
   gate before being trusted as a terrain source (the same way DSM
   requires a successful calibration today), or is it presented as-is with
   its own disclaimer? This is a real design decision for when a real,
   evaluated model exists — not decided by this note.
6. **Widen `TERRAIN_SOURCE_ARTIFACT_TYPES`** and add the corresponding
   branch to `get_visualization_context`'s terrain selection, only once
   step 5 is decided.
7. **Add the real 3D-renderer treatment** (camera framing, exaggeration,
   disclaimer copy) for `height_kind === "remote_sensing_height"` in
   `TerrainView3D.tsx`/`TerrainWorkspace.tsx` — deliberately not done now
   (no UI/UX work for a state that can't occur).
