# P1-11E — TerraHeight-S upsample-recovery test

**Research only.** No production code, registry, calibration, thresholds, DSM/DTM/nDSM behaviour, schema, migration, frontend or API was changed. No integration, no commit. P1-11B/C/D results are untouched. Workspace: scratchpad folder `p111e/`.

| | |
|---|---|
| Generated (UTC) | 2026-09-27T08:50:32.552757+00:00 |
| **Gate** | **A. SCALE RECOVERY OBSERVED → proceed to a real ~0.6 m transfer test (NAIP + independent lidar height reference).** Substantial but incomplete recovery; limits in §8. |

TerraHeight-S remains an **AGL/nDSM** model. Nothing here establishes absolute terrain elevation.

**Labels:** VERIFIED = measured by TERRAIN-X; INFERENCE = our interpretation; AUTHOR CLAIM = TerraHeight-S uploader; UNRESOLVED = not establishable from the evidence.

## 1. Hypothesis

If ~0.6 m imagery is upsampled back to about the model's native ~0.33 m sampling before inference, does the height scale lost in P1-11D recover?

## 2. Tile selection (fixed before inference)

**Rule:** per city, rank P1-11D Part C tiles by native MAE; DC and PHL: ranks 6 and 7 of 12 (the two tiles bracketing the city median); NYC: rank 6 of 12 (lower median). Fixed before any P1-11E inference.

**Source:** P1-11D Part C (36 independent GAMUS test tiles, p111d/results.json). Recorded at 2026-09-27T08:40:37.461806+00:00.

| Tile | City | Rank by P1-11D native MAE in city | P1-11D native MAE / R² / zero% |
|---|---|---|---|
| DC_19_36 | DC | 6 of 12 | 2.730 / 0.687 / 50.9% |
| DC_26_08 | DC | 7 of 12 | 3.104 / 0.708 / 43.3% |
| NYC_17337 | NYC | 6 of 12 | 2.419 / 0.605 / 55.9% |
| PHL_4582 | PHL | 6 of 12 | 1.749 / 0.779 / 72.7% |
| PHL_4219 | PHL | 7 of 12 | 2.002 / 0.733 / 62.8% |

**Disclosure (VERIFIED):** the rule targets median **MAE**. The two PHL tiles it picks happen to have high native R² (0.78, 0.73), well above the PHL median R² in P1-11D (0.38). On R² they are better-than-typical PHL tiles.

## 3. Resampling and reference

All VERIFIED as performed.

| Item | Value |
|---|---|
| Native (A) | 1024 × 1024 px, 0.33 m (GAMUS paper (0.33 m); dataset documentation) |
| Downsample (B) | PIL Image.BOX (area average), uint8 RGB → 563 × 563 px, 0.6002 m, scale factor 0.5498. Identical to P1-11D. |
| Upsample (C) | PIL Image.BILINEAR, uint8 RGB, from the exact B image → 1024 × 1024 px, 0.33 m, scale factor 1.8188. **C is built from the exact B image.** The upsampled RGB differs from native by a mean 7.4–9.1 DN per channel. |
| Reference | original GAMUS AGL float32 -> PIL Image.BOX to 563 px (P1-11D); original unmodified. The same for A, B and C. |
| Evaluation grid | A and C (1024 px) -> PIL Image.BOX float32 to 563 px; B native 563 px |
| Classes | PIL NEAREST (analysis only) |

### Inference

TERRAIN-X reproduction inference, the same checkpoint (SHA-256 `739ed4e98168f1632192c666a3615d424188f2ce6597ebb435034d35fdc22f8a`, strict load: <All keys matched successfully>) and the same preprocessing and scaling for all conditions:
- **A:** tiled 630/25%/uniform. **Bit-identical** to the saved P1-11D predictions on all 5 tiles (VERIFIED).
- **B:** single 630x630 window, reflect-pad 563->630 (P1-11D Part B primary; tiled protocol degenerates because 563 < 630).
- **C:** tiled 630/25%/uniform (identical to A).

**Confound, handled by a control:** B necessarily uses one padded window while A and C use four tiles. §6 re-runs A and C as single-pass inference to separate input-scale effects from window-layout effects.

## 4. Primary per-tile metrics

All scored on the same 563 px reference grid. No affine calibration. Bias = prediction − reference.

| Tile | Condition | MAE | RMSE | R² | Pearson | Spearman | Bias | LS slope a | LS intercept b | LS R² | Zero% | Pred min / max / mean / std (m) | Time (s) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| DC_19_36 | A native | 2.710 | 4.412 | 0.693 | 0.865 | 0.871 | -0.673 | 0.797 | 1.956 | 0.749 | 50.1% | 0.00 / 30.81 / 6.31 / 8.65 | 11.3 |
|  | B ~0.6 m | 3.918 | 5.686 | 0.490 | 0.807 | 0.795 | -3.162 | 1.072 | 2.888 | 0.651 | 58.9% | 0.00 / 20.84 / 3.83 / 5.99 | 2.9 |
|  | C upsampled | 2.944 | 4.622 | 0.663 | 0.847 | 0.858 | -1.266 | 0.830 | 2.236 | 0.718 | 51.4% | 0.00 / 29.63 / 5.72 / 8.12 | 11.2 |
| DC_26_08 | A native | 3.079 | 4.469 | 0.713 | 0.884 | 0.873 | -2.098 | 0.927 | 2.570 | 0.781 | 42.2% | 0.00 / 30.66 / 6.52 / 7.94 | 11.2 |
|  | B ~0.6 m | 5.521 | 7.467 | 0.198 | 0.814 | 0.785 | -5.317 | 1.420 | 3.931 | 0.663 | 54.8% | 0.00 / 19.29 / 3.30 / 4.78 | 2.9 |
|  | C upsampled | 3.354 | 4.844 | 0.662 | 0.874 | 0.857 | -2.630 | 0.960 | 2.867 | 0.763 | 44.7% | 0.00 / 29.73 / 5.99 / 7.58 | 11.3 |
| NYC_17337 | A native | 2.342 | 3.372 | 0.631 | 0.882 | 0.849 | -2.126 | 1.008 | 2.100 | 0.778 | 54.5% | 0.00 / 21.89 / 3.16 / 4.86 | 11.3 |
|  | B ~0.6 m | 3.490 | 4.921 | 0.215 | 0.808 | 0.757 | -3.437 | 1.411 | 2.679 | 0.653 | 61.9% | 0.00 / 14.49 / 1.85 / 3.18 | 2.8 |
|  | C upsampled | 2.617 | 3.701 | 0.556 | 0.871 | 0.846 | -2.488 | 1.066 | 2.304 | 0.759 | 55.1% | 0.00 / 21.47 / 2.79 / 4.54 | 11.3 |
| PHL_4582 | A native | 1.738 | 3.152 | 0.785 | 0.919 | 0.753 | -1.638 | 1.036 | 1.548 | 0.844 | 72.2% | 0.00 / 27.06 / 2.51 / 6.02 | 11.4 |
|  | B ~0.6 m | 2.665 | 4.551 | 0.551 | 0.877 | 0.654 | -2.631 | 1.427 | 1.983 | 0.770 | 79.2% | 0.00 / 18.04 / 1.52 / 4.18 | 2.7 |
|  | C upsampled | 1.938 | 3.348 | 0.757 | 0.916 | 0.739 | -1.773 | 1.148 | 1.421 | 0.839 | 71.6% | 0.00 / 23.19 / 2.38 / 5.42 | 11.1 |
| PHL_4219 | A native | 1.991 | 3.628 | 0.736 | 0.890 | 0.822 | -1.571 | 1.098 | 1.224 | 0.792 | 62.2% | 0.00 / 32.94 / 3.55 / 5.72 | 11.2 |
|  | B ~0.6 m | 3.016 | 5.171 | 0.464 | 0.832 | 0.763 | -2.747 | 1.505 | 1.552 | 0.693 | 66.2% | 0.00 / 15.36 / 2.37 / 3.91 | 2.9 |
|  | C upsampled | 2.494 | 4.395 | 0.612 | 0.846 | 0.807 | -1.906 | 1.258 | 1.079 | 0.715 | 61.2% | 0.00 / 19.96 / 3.21 / 4.75 | 11.2 |

Peak process RAM: 803 MB. Inference time: A and C about 11 s, B about 3 s (CPU).

**Secondary check,** A and C on the native 1024 grid against the original AGL (B has no native-grid prediction):

| Tile | A MAE / R² (1024 grid) | C MAE / R² (1024 grid) |
|---|---|---|
| DC_19_36 | 2.730 / 0.687 | 2.965 / 0.658 |
| DC_26_08 | 3.104 / 0.708 | 3.376 / 0.658 |
| NYC_17337 | 2.419 / 0.605 | 2.688 / 0.533 |
| PHL_4582 | 1.749 / 0.779 | 1.956 / 0.752 |
| PHL_4219 | 2.002 / 0.733 | 2.505 / 0.610 |

## 5. Cross-tile summary and scale recovery

| Median across 5 tiles | A native | B ~0.6 m | C upsampled | B → C change | A → C difference |
|---|---|---|---|---|---|
| MAE (m) | 2.342 | 3.490 | 2.617 | -0.872 | +0.275 |
| RMSE (m) | 3.628 | 5.171 | 4.395 | -0.776 | +0.767 |
| R² | 0.713 | 0.464 | 0.662 | +0.199 | -0.050 |
| Pearson | 0.884 | 0.814 | 0.871 | +0.057 | -0.012 |
| Bias (m) | -1.638 | -3.162 | -1.906 | +1.256 | -0.269 |
| LS slope a | 1.008 | 1.420 | 1.066 | -0.354 | +0.057 |
| LS intercept b | 1.956 | 2.679 | 2.236 | -0.443 | +0.280 |
| Zero fraction | 0.545 | 0.619 | 0.551 | -0.067 | +0.006 |
| Prediction mean (m) | 3.545 | 2.368 | 3.209 | +0.841 | -0.336 |
| Prediction max (m) | 30.660 | 18.043 | 23.186 | +5.144 | -7.474 |

Least-squares fits (reference = a × prediction + b) are a **diagnostic only**; predictions were not modified.

**Share of the B-condition loss recovered by C**, as (B − C)/(B − A):

| Tile | City | MAE recovered | R² recovered | Bias recovered | LS slope recovered | A → C MAE (m) | A → C R² | LS slope A / B / C |
|---|---|---|---|---|---|---|---|---|
| DC_19_36 | DC | 80.7% | 85.3% | 76.2% | 87.7% | +0.234 | -0.030 | 0.797 / 1.072 / 0.830 |
| DC_26_08 | DC | 88.7% | 90.2% | 83.5% | 93.3% | +0.276 | -0.050 | 0.927 / 1.420 / 0.960 |
| NYC_17337 | NYC | 76.0% | 81.9% | 72.4% | 85.7% | +0.275 | -0.075 | 1.008 / 1.411 / 1.066 |
| PHL_4582 | PHL | 78.4% | 88.2% | 86.4% | 71.3% | +0.200 | -0.028 | 1.036 / 1.427 / 1.148 |
| PHL_4219 | PHL | 50.9% | 54.7% | 71.5% | 60.7% | +0.503 | -0.123 | 1.098 / 1.505 / 1.258 |

Median recovered: MAE 78.4%, RMSE 83.6%, R² 85.3%, bias 76.2%, LS slope 85.7%.

**Counts (VERIFIED):**

| | |
|---|---|
| C lower MAE than B | 5/5 |
| C higher R² than B | 5/5 |
| C's LS slope closer to A than B's | 5/5 |
| C within 0.5 m MAE of A | 4/5 |
| C within 0.1 R² of A | 4/5 |

### Tall objects and classes

Bias by reference height (m), conditions A / B / C:

| Tile | [1,5) m: A / B / C | [5,10) m: A / B / C | [10,20) m: A / B / C | [20,40) m: A / B / C |
|---|---|---|---|---|
| DC_19_36 | -0.41 / -1.14 / -0.59 | -2.15 / -4.97 / -2.88 | -1.07 / -6.66 / -2.44 | -0.93 / -8.23 / -2.47 |
| DC_26_08 | -1.73 / -2.40 / -1.91 | -3.49 / -6.04 / -4.07 | -2.65 / -8.72 / -3.69 | -4.34 / -13.08 / -5.33 |
| NYC_17337 | -2.32 / -2.43 / -2.37 | -4.15 / -5.60 / -4.82 | -2.68 / -7.70 / -3.67 | -4.75 / -10.47 / -5.03 |
| PHL_4582 | -2.61 / -2.84 / -2.22 | -4.97 / -6.18 / -5.05 | -5.92 / -9.80 / -5.71 | -2.13 / -8.58 / -4.55 |
| PHL_4219 | -1.53 / -2.27 / -0.77 | -2.02 / -3.10 / -1.45 | -4.81 / -8.23 / -6.30 | -5.15 / -12.49 / -10.08 |

| Tile | Buildings bias A / B / C (m) | Trees bias A / B / C (m) |
|---|---|---|
| DC_19_36 | -3.74 / -5.98 / -4.29 | +0.13 / -5.25 / -1.09 |
| DC_26_08 | -4.25 / -6.15 / -4.58 | -2.91 / -8.39 / -3.78 |
| NYC_17337 | -3.11 / -5.46 / -3.82 | -4.07 / -7.16 / -4.82 |
| PHL_4582 | -4.07 / -7.03 / -4.80 | -3.57 / -3.95 / -1.59 |
| PHL_4219 | -4.24 / -7.42 / -5.84 | -2.95 / -4.18 / -1.75 |

### Exact zeros

Median zero fraction: A 54.5%, B 61.9%, C 55.1%.

## 6. Control: single-pass geometry for all conditions

Sensitivity only.

| Tile | A single-pass MAE / R² / slope | B (single window) MAE / R² / slope | C single-pass MAE / R² / slope | C single-pass beats B on MAE |
|---|---|---|---|---|
| DC_19_36 | 3.028 / 0.676 / 0.956 | 3.918 / 0.490 / 1.072 | 3.344 / 0.619 / 1.002 | True |
| DC_26_08 | 3.538 / 0.640 / 1.018 | 5.521 / 0.198 / 1.420 | 3.933 / 0.560 / 1.082 | True |
| NYC_17337 | 2.652 / 0.523 / 1.147 | 3.490 / 0.215 / 1.411 | 2.915 / 0.448 / 1.246 | True |
| PHL_4582 | 2.057 / 0.726 / 1.184 | 2.665 / 0.551 / 1.427 | 2.165 / 0.701 / 1.257 | True |
| PHL_4219 | 2.826 / 0.463 / 1.392 | 3.016 / 0.464 / 1.505 | 3.039 / 0.403 / 1.587 | False |

Medians, single-pass:
- A: MAE 2.826, R² 0.640, slope 1.147
- C: MAE 3.039, R² 0.560, slope 1.246
- B: MAE 3.490, R² 0.464, slope 1.420

**VERIFIED:** with the **same single-pass geometry**, C still beats B on MAE on 4/5 tiles. **PHL_4219 is the exception:** C 3.039 vs B 3.016, and R² and slope also slightly worse. On that tile single-pass inference is poor even at native resolution (A single-pass MAE 2.83, R² 0.46).

**INFERENCE:** most of the B → C gain comes from input sampling scale, not window layout. On PHL_4219 the primary gain is partly due to tiling.

## 7. Visual analysis and seams

**Figures** (scratchpad `p111e/`):
- `fig_tile_<id>.png` for each of the 5 tiles. Each shows:
  - input RGB for A, B and C
  - the three predictions on the 563 px evaluation grid
  - the three signed error maps
  - the reference AGL, a seam profile and the scale diagnostic
- `fig_summary_ABC.png`: per-tile MAE, R², LS slope and 20–40 m bias across A → B → C.

**Scales:** heights −5 to 45 m and error ±20 m everywhere; nothing is adjusted per condition.

**Observations (VERIFIED visually):**
- C restores much of the building and canopy height contrast lost in B. The tallest structures stay somewhat lower than in A, most visibly on PHL_4219.
- Features overpredicted in A, such as PHL_4219's elevated roadway, are also overpredicted in C.

**Seams (VERIFIED):** B uses a single window, so it has no tiling seam. C uses the same tiling as A, and its window-edge steps are similar to A's:

| Tile | A step col 394 / 630 vs median | C step col 394 / 630 vs median |
|---|---|---|
| DC_19_36 | 0.539 / 0.878 vs 0.104 | 0.571 / 0.979 vs 0.088 |
| DC_26_08 | 1.238 / 0.938 vs 0.186 | 1.173 / 0.965 vs 0.169 |
| NYC_17337 | 0.657 / 1.368 vs 0.132 | 0.648 / 1.267 vs 0.115 |
| PHL_4582 | 0.326 / 0.504 vs 0.035 | 0.652 / 0.453 vs 0.037 |
| PHL_4219 | 1.727 / 0.733 vs 0.078 | 0.996 / 0.847 vs 0.062 |

## 8. Answers

**1. Does upsampling recover the prediction height scale?**
**Largely** (VERIFIED). Median LS slope: A 1.008, B 1.420, C 1.066; median 85.7% of the slope deviation recovered. Mean and max predictions move back toward A.

**2. Does C materially improve over B?**
**Yes.** Median MAE 3.49 → 2.62 m; R² 0.46 → 0.66; better than B on all 5 tiles.

**3. Does C approach native A?**
**Mostly, not fully.** Median MAE is +0.28 m worse than A and R² -0.05. C is within 0.5 m MAE and 0.1 R² of A on 4/5 tiles; PHL_4219 is the exception.

**4. Does C recover R²?**
**Mostly:** median 85.3% recovered (range 55–90%).

**5. Does C reduce MAE?**
**Yes:** median 78.4% of the loss recovered (51–89%).

**6. Does C reduce tall-object negative bias?**
**Yes, mostly.** 20–40 m bias improves on every tile, but stays worse than A. On PHL_4219 it is −10.1 m (B −12.5, A −5.2).

**7. Does C reduce the exact-zero problem?**
**It returns to native levels:** median zero fraction 55.1% vs A 54.5%. The native zero behaviour (P1-11D) is unchanged.

**8. Is C consistent across DC, NYC and PHL?**
**Largely.** DC 81–89%, NYC 76%, PHL 78% and 51% MAE recovery. The weakest case is PHL (PHL_4219), and under the single-pass control it shows no scale-driven gain. With one NYC tile and two PHL tiles, city-level consistency is only weakly supported.

**9. Does this justify a real ~0.6 m test?**
**Yes** (INFERENCE). The controlled failure in P1-11D is largely explained by input sampling scale and is largely reversible by deterministic upsampling. Only real 0.6 m imagery can tell whether that holds when the 0.6 m image comes from a real sensor rather than area-averaging.

### Interpretation (INFERENCE)

Upsampling **does not create information** that is absent from the 0.6 m image. The result only shows that TerraHeight-S is sensitive to **input sampling scale**, since apparent object size in pixels affects predicted height. Interpolating back to the training pixel density restores much of the input distribution the model expects, but not the fine detail lost in downsampling. That is consistent with C being close to A but slightly worse everywhere.

## 9. Gate decision

**A. SCALE RECOVERY OBSERVED → proceed to a real ~0.6 m transfer test (NAIP + independent lidar height reference).**

**Why A rather than B (partial):**
- Recovery is consistent in direction on every tile and every primary metric (5/5).
- It is large: a median 78–86% of the lost MAE, R² and slope.
- It brings C within 0.5 m MAE and 0.1 R² of native on 4/5 tiles.
- The control confirms it is mostly a sampling-scale effect.

**Limits that stay attached to this decision:**
- **Sample size:** 5 tiles, 1 of them NYC.
- **Synthetic degradation:** area-averaged GAMUS is not a real 0.6 m sensor with its own optics, MTF and processing (UNRESOLVED).
- **Incomplete recovery:** C remains slightly worse than A everywhere.
- **PHL_4219:** only about 51% recovered, and no scale-driven gain under the control.
- **Carried over from P1-11D:** native failure modes (tall-object underestimation, zeros on low objects, no negative outputs).
- **Carried over from P1-11B–D:** provenance gaps (training code, splits, the author's metrics, blending rule).

**No integration and no production design review follow from this result.**

### Conditions for the real-data test

Fix these in advance:
1. Keep this exact protocol: bilinear upsample of real ~0.6 m imagery to 0.33 m, then 630 px / 25% / uniform tiled inference.
2. Use a **lidar-derived nDSM** (DSM − DTM, e.g. from USGS 3DEP point clouds) as the reference. The 3DEP 1 m standard product is bare-earth only, so the reference must be built from point clouds.
3. Choose a region independent of GAMUS; the tile selection rule, metrics, and native vs upsampled comparison should be fixed in advance.
4. Keep the DEM + AGL = DSM composition question separate.

## 10. Files

Scratchpad `p111e/`:
- `results.json`: all per-tile metrics, stratified results, least-squares fits, seams, control and summary
- `selection.json`
- `run.py`, `control.py`, `figures.py`
- `pred*.npy` / `rgb*.npy` / `ref06_*.npy`: predictions, resampled inputs, references
- figures
