# P1-11C — TerraHeight-S multi-tile GAMUS validation

**Research only.** Nothing here touches TERRAIN-X production code, the model registry, calibration, schemas, the API or the frontend. TerraHeight-S is **not** integrated.

| | |
|---|---|
| Benchmark run (UTC) | 2026-09-27T08:00:00.620906+00:00 |
| Inference label | TERRAIN-X REPRODUCTION INFERENCE (not official TerraHeight-S inference) |
| Decision | **B. PROMISING BUT INCONSISTENT — NEED MORE GAMUS VALIDATION** |

TerraHeight-S predicts **height above ground (AGL / nDSM)**. It does **not** produce terrain elevation. This experiment does not answer whether an AGL model combined with an independent bare-earth DEM gives a defensible DSM.

## 1. Objective

Test whether the strong P1-11B result on one tile (DC_03_26) holds across several independent GAMUS test tiles. In other words: is this a genuinely useful GAMUS-trained AGL model, or one that happened to do well on one tile?

## 2. Dataset selection rule

**Source.** The official GAMUS dataset on Hugging Face, `earthflow/GAMUS`, at revision `a3c0e2511f06d909612406f436cf8abb4da805f5` (licence CC-BY-4.0). This is the revision the local DC_03_26 copy came from.

**Test split.** 2,861 tiles: DC 361, NYC 1,000, PHL 1,500. Every test tile has RGB, AGL and CLS files. NYC RGB files are named `*_IMG.h5`; the others are `*_RGB.h5`.

**Rule** (content-blind and fixed before any inference was run):
- For each city, sort the test RGB IDs.
- Take the tiles at positions ⌊(k + 0.5)·n/3⌋ for k = 0, 1, 2. That gives 9 tiles, evenly spaced through each city's list.
- Add DC_03_26 to check that P1-11B reproduces. It is byte-identical to the P1-11B copy (all three files).

**Not used in selection:** building density, vegetation, height or any other image content. Scene diversity comes only from covering three cities and spreading picks through each list.

**Download size:** 93 MB (10 tiles × RGB + AGL + CLS). File SHA-256s are in `selection.json`.

**Acquisition metadata** (sensor, date): not provided in the dataset card, which gives only the licence. City is read from the tile ID prefix.

**Split relationship to TerraHeight-S** (partially verified, not documented by the uploader):
- The checkpoint's stored validation pixel count, 900,710,835, equals 859 tiles × 1,048,576 pixels, minus 15,949 non-finite pixels. 859 is exactly the size of this revision's validation split.
- Its training count, 5,247,066,018, is 5,004 tiles minus 8,286 pixels. 5,004 is exactly the training split.

This fits training on train and validating on val, leaving **test** unseen, but it isn't proof.

## 3. Tile inventory

| Tile | City (from ID prefix) | Index in sorted city test list | RGB | AGL | CLS dtype / classes present | AGL min / max / mean / std (m) | AGL<0 px | AGL = -5 px |
|---|---|---|---|---|---|---|---|---|
| DC_20_43 | DC | 60 of 361 | 1024x1024x3 uint8 | 1024x1024 float32 | float32 / 1,2,3,5,6 | 0.00 / 31.13 / 6.53 / 8.51 | 0 | 0 |
| DC_35_45 | DC | 180 of 361 | 1024x1024x3 uint8 | 1024x1024 float32 | float32 / 0,1,2,3,5,6 | 0.00 / 28.35 / 2.97 / 5.80 | 0 | 0 |
| DC_50_52 | DC | 300 of 361 | 1024x1024x3 uint8 | 1024x1024 float32 | float32 / 0,1,2,3,5,6 | 0.00 / 44.58 / 18.30 / 13.50 | 0 | 0 |
| NYC_03486 | NYC | 166 of 1000 | 1024x1024x3 uint8 | 1024x1024 float32 | uint8 / 0,2,3,4,5,6 | -2.87 / 27.15 / 5.64 / 6.68 | 74591 | 0 |
| NYC_11587 | NYC | 500 of 1000 | 1024x1024x3 uint8 | 1024x1024 float32 | uint8 / 4 | -1.28 / 21.34 / -0.53 / 1.00 | 1041129 | 0 |
| NYC_21651 | NYC | 833 of 1000 | 1024x1024x3 uint8 | 1024x1024 float32 | uint8 / 0,2,3,5,6 | -0.84 / 31.05 / 10.71 / 8.73 | 17102 | 0 |
| PHL_3725 | PHL | 250 of 1500 | 1024x1024x3 uint8 | 1024x1024 float32 | uint8 / 1,2,3,5,6 | 0.00 / 143.92 / 2.29 / 3.56 | 0 | 0 |
| PHL_4292 | PHL | 750 of 1500 | 1024x1024x3 uint8 | 1024x1024 float32 | uint8 / 1,2,3,5,6 | 0.00 / 35.29 / 2.57 / 4.52 | 0 | 0 |
| PHL_5091 | PHL | 1250 of 1500 | 1024x1024x3 uint8 | 1024x1024 float32 | uint8 / 1,2,3,4,5,6 | 0.00 / 22.25 / 2.30 / 3.29 | 0 | 0 |
| DC_03_26 | DC | 0 of 361 | 1024x1024x3 uint8 | 1024x1024 float32 | float32 / 0,1,2,3,4,5,6 | -5.00 / 42.57 / 11.09 / 10.89 | 5444 | 4963 |

## 4. Data integrity

**Checks:** every tile is 1024 × 1024. RGB is uint8 with 3 channels; AGL is float32 with **no non-finite values**.

**Valid-pixel definition:** identical to P1-11B, `geospatial.gamus_validation.build_gamus_valid_mask`, meaning a pixel is valid when both AGL and the prediction are finite. On every tile, all 1,048,576 pixels are valid.

**Ground truth:** unchanged; verified per tile with `np.array_equal` before and after. Nothing was clipped, and no floor values, signs or scales were altered.

**Reported, not excluded:**
- **NYC_11587 is an all-water tile.** Its class map is 100% class 4, AGL mean −0.53 m, std 1.00 m, and 99.3% of pixels are negative. R² there is fragile because there is little variance to explain.
- **PHL_3725 contains spike pixels:** 42 above 50 m and 22 above 100 m (max 143.92 m), though its 99.99th percentile is 30.0 m. These are probably reference artefacts, and they stay in every metric.
- **The −5 m floor occurs only in DC_03_26** (4,963 pixels). NYC tiles have small negative AGL values instead (down to −2.87 m).
- **Class-map data types differ by city:** float32 for DC, uint8 for NYC and PHL. NYC maps have no class 1 (ground).
- **DC RGB tiles contain a few all-black pixels** (8–54 per tile).

## 5. Exact inference procedure

### Checkpoint

| | |
|---|---|
| Repository / revision | `benfox6515/TerraHeight-S` @ `b61c569d17ea5ce30adc414152d52af7b39430df` |
| File | `best_model.pth` |
| SHA-256 | `739ed4e98168f1632192c666a3615d424188f2ce6597ebb435034d35fdc22f8a` (asserted at the start of every run) |
| Loading | `strict=True` → <All keys matched successfully> |
| Parameters | 24,785,089 |
| Architecture | official Depth Anything V2 {'encoder': 'vits', 'features': 64, 'out_channels': [48, 96, 192, 384]} |
| Model code | official Depth-Anything-V2 repository at commit `a561b849ebae10a6f5ef49e26c83cbbcd36c71bf`, the same as the checkpoint's recorded `source_commit` |

### Procedure

- **Label:** TERRAIN-X REPRODUCTION INFERENCE. This is not the official TerraHeight-S inference; the author's full-tile blending rule is undocumented.
- **Preprocessing:** RGB ÷ 255, then ImageNet mean/std [0.485, 0.456, 0.406] / [0.229, 0.224, 0.225].
- **Tiling:** 630 px crops, overlap 0.25 (stride 472), windows at [0, 394] on each axis. Overlaps use a uniform average. Identical to P1-11B.
- **Output:** `relu(net(x))[raw] * scale_m`, with `scale_m` = 8.492877943662961.
- **Whole-image sensitivity check:** reflect-pad to multiple of 14, single pass, crop back, run on **all 10 tiles**.
- **Runtime environment:** CPU only (6 threads), no fine-tuning. The official `dpt.py` imports OpenCV only for `infer_image()`, which is never called. A placeholder module stands in for the import and is removed from `sys.modules` immediately afterwards.

## 6. Native metrics

TerraHeight-S native output: no affine fit, all valid pixels, bias = prediction − reference.

| Tile | Valid / invalid px | Pred min / max / mean / std (m) | Ref mean (m) | MAE | RMSE | Bias | R² | Pearson | Spearman | Exact-zero px % | Tiled inference (s) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| DC_20_43 | 1,048,576 / 0 | 0.00 / 28.67 / 5.25 / 8.21 | 6.53 | 2.049 | 3.422 | -1.275 | 0.838 | 0.928 | 0.875 | 59.4 | 12.1 |
| DC_35_45 | 1,048,576 / 0 | 0.00 / 23.45 / 1.98 / 4.48 | 2.97 | 1.120 | 2.709 | -0.990 | 0.782 | 0.911 | 0.781 | 78.4 | 12.5 |
| DC_50_52 | 1,048,576 / 0 | 0.00 / 40.17 / 15.85 / 12.66 | 18.30 | 3.841 | 5.437 | -2.449 | 0.838 | 0.933 | 0.930 | 25.5 | 12.2 |
| NYC_03486 | 1,048,576 / 0 | 0.00 / 21.86 / 4.59 / 5.98 | 5.64 | 2.846 | 4.489 | -1.058 | 0.549 | 0.768 | 0.725 | 51.4 | 12.2 |
| NYC_11587 | 1,048,576 / 0 | 0.00 / 0.00 / 0.00 / 0.00 | -0.53 | 0.681 | 1.133 | +0.531 | -0.281 | n/a | n/a | 100.0 | 13.0 |
| NYC_21651 | 1,048,576 / 0 | 0.00 / 24.59 / 8.61 / 7.41 | 10.71 | 4.252 | 5.923 | -2.092 | 0.540 | 0.776 | 0.773 | 29.4 | 13.0 |
| PHL_3725 | 1,048,576 / 0 | 0.00 / 10.21 / 0.73 / 1.63 | 2.29 | 1.564 | 2.950 | -1.559 | 0.313 | 0.780 | 0.670 | 79.5 | 13.1 |
| PHL_4292 | 1,048,576 / 0 | 0.00 / 15.19 / 0.89 / 2.49 | 2.57 | 1.703 | 3.455 | -1.673 | 0.415 | 0.777 | 0.617 | 82.5 | 13.3 |
| PHL_5091 | 1,048,576 / 0 | 0.00 / 21.84 / 0.57 / 2.14 | 2.30 | 1.795 | 2.992 | -1.734 | 0.171 | 0.671 | 0.634 | 81.9 | 13.1 |
| DC_03_26 | 1,048,576 / 0 | 0.00 / 36.96 / 8.60 / 9.58 | 11.09 | 4.064 | 5.966 | -2.484 | 0.700 | 0.867 | 0.834 | 39.3 | 12.9 |

**P1-11B reproduction check:** DC_03_26 matches P1-11B exactly (MAE 4.064 m, R² 0.700).

## 7. DA-V2 comparison

The four views are **A.** DA-V2 raw output, **B.** DA-V2 affine diagnostic, **C.** TerraHeight-S native, **D.** TerraHeight-S affine (§8).

**Baseline procedure:** the production estimator, `DepthModelInfo(name='depth-anything/Depth-Anything-V2-Small-hf', revision='5426e4f0f36572d16453bbda7a8389317b1bef99', so…`, rerun on every tile, followed by `fit_and_validate_gamus` (σ 2.5, 5 iterations). The P1-11B baseline was not reused, so every tile follows the same procedure. On DC_03_26 the rerun reproduces the stored baseline exactly.

**Scope differences:**
- **A** is unitless, so MAE, RMSE and R² in metres are undefined for it.
- **B inlier scope** is the existing baseline convention: fitted in-sample to the same tile, scored only on inliers. It favours DA-V2.
- **B all-pixel scope** applies the same fitted line to every valid pixel.

| Tile | A. DA-V2 raw: Pearson / Spearman vs AGL | B. DA-V2 affine a / b | B. inlier scope MAE / RMSE / R² (inliers %) | B. all-pixel MAE / RMSE / R² | C. TH native MAE / RMSE / R² | DA-V2 time (s) |
|---|---|---|---|---|---|---|
| DC_20_43 | -0.011 / +0.031 | 0.544 / 5.209 | 6.119 / 7.399 / -0.000 (95.3%) | 6.824 / 8.550 / -0.009 | 2.049 / 3.422 / 0.838 | 1.6 |
| DC_35_45 | +0.239 / +0.192 | 4.192 / -5.821 | 1.151 / 1.459 / 0.449 (82.5%) | 3.112 / 6.004 / -0.071 | 1.120 / 2.709 / 0.782 | 1.6 |
| DC_50_52 | -0.307 / -0.361 | -13.585 / 31.916 | 11.319 / 12.845 / 0.094 (100.0%) | 11.319 / 12.845 / 0.094 | 3.841 / 5.437 / 0.838 | 1.7 |
| NYC_03486 | +0.252 / +0.312 | 6.345 / -5.038 | 5.046 / 5.929 / 0.081 (97.3%) | 5.369 / 6.485 / 0.059 | 2.846 / 4.489 / 0.549 | 1.8 |
| NYC_11587 | +0.166 / +0.053 | 0.004 / -0.611 | 0.063 / 0.078 / 0.001 (96.0%) | 0.149 / 1.004 / -0.005 | 0.681 / 1.133 / -0.281 | 1.7 |
| NYC_21651 | +0.118 / +0.137 | 2.626 / 6.408 | 7.735 / 8.673 / 0.014 (100.0%) | 7.735 / 8.673 / 0.014 | 4.252 / 5.923 / 0.540 | 1.9 |
| PHL_3725 | +0.465 / +0.285 | 5.217 / -6.001 | 1.395 / 1.699 / 0.584 (87.3%) | 2.160 / 3.277 / 0.152 | 1.564 / 2.950 / 0.313 | 1.7 |
| PHL_4292 | +0.334 / +0.355 | 1.530 / -0.873 | 1.025 / 1.345 / 0.052 (81.5%) | 2.590 / 4.643 / -0.056 | 1.703 / 3.455 / 0.415 | 1.6 |
| PHL_5091 | +0.623 / +0.476 | 4.797 / -3.082 | 1.507 / 1.905 / 0.506 (93.8%) | 1.854 / 2.605 / 0.372 | 1.795 / 2.992 / 0.171 | 1.7 |
| DC_03_26 | -0.071 / -0.007 | -3.669 / 15.215 | 9.315 / 10.823 / 0.005 (99.9%) | 9.339 / 10.862 / 0.005 | 4.064 / 5.966 / 0.700 | 1.8 |

**Counts** (descriptive, not a ranking):
- **TerraHeight-S native MAE below the DA-V2 affine inlier MAE** (the baseline convention, which favours DA-V2): **6/10**. The exceptions are the three low-relief PHL tiles and the all-water tile.
- **TerraHeight-S native R² above the DA-V2 affine all-pixel R²:** **8/10**.
- **Like for like** (TerraHeight-S affine vs DA-V2 affine, both all-pixel):
  - MAE lower on **8/10**
  - R² higher on **8/10**
  - undefined on 1 (NYC_11587)
- **DA-V2 raw correlation with AGL is inconsistent across tiles:** Spearman ranges from −0.361 to +0.476, and the sign varies.

## 8. Affine diagnostic

**SECONDARY AFFINE DIAGNOSTIC — NOT NATIVE MODEL ACCURACY.** This uses the same `fit_and_validate_gamus` as the baseline (σ 2.5, 5 iterations). The main columns apply the fitted line to all valid pixels; the inlier scope is shown for comparison with the existing convention.

| Tile | a | b | All-pixel MAE | All-pixel RMSE | All-pixel R² | Pearson | Spearman | Inlier-scope MAE / R² (inliers %) |
|---|---|---|---|---|---|---|---|---|
| DC_20_43 | 0.998 | 1.121 | 2.237 | 3.179 | 0.861 | 0.928 | 0.875 | 1.653 / 0.941 (90.4%) |
| DC_35_45 | 1.198 | 0.076 | 0.996 | 2.448 | 0.822 | 0.911 | 0.781 | 0.123 / 0.996 (75.3%) |
| DC_50_52 | 1.034 | 1.907 | 3.489 | 4.879 | 0.869 | 0.933 | 0.930 | 2.667 / 0.944 (91.4%) |
| NYC_03486 | 1.034 | 1.205 | 2.989 | 4.417 | 0.563 | 0.768 | 0.725 | 2.042 / 0.843 (88.7%) |
| NYC_11587 | undefined | — | — | — | — | — | — | prediction constant (1 unique value = 0.0 m): `Relative depth samples have (near-)zero variance; a scale cannot be determined.` |
| NYC_21651 | 1.021 | 2.257 | 4.113 | 5.571 | 0.593 | 0.776 | 0.773 | 3.300 / 0.779 (92.4%) |
| PHL_3725 | 1.855 | 0.250 | 1.174 | 2.343 | 0.567 | 0.780 | 0.670 | 0.356 / 0.969 (78.5%) |
| PHL_4292 | 1.516 | 0.431 | 1.548 | 2.960 | 0.571 | 0.777 | 0.617 | 0.543 / 0.955 (79.5%) |
| PHL_5091 | 1.942 | 1.250 | 1.981 | 3.129 | 0.093 | 0.671 | 0.634 | 1.575 / 0.542 (94.5%) |
| DC_03_26 | 1.063 | 1.997 | 3.789 | 5.473 | 0.747 | 0.867 | 0.834 | 2.778 / 0.900 (90.6%) |

The fitted slope `a` is a native-scale check:
- **About 1.0 on the dense DC and NYC scenes** (0.998–1.063, except DC_35_45 at 1.198). Native metres are correctly scaled there.
- **1.52–1.94 on the low-relief PHL tiles.** Native heights there are compressed to roughly half.
- **Undefined on NYC_11587,** where the prediction is the constant 0.

## 9. Class-stratified analysis

This uses GAMUS class maps **for analysis only**; classes never influence predictions. Cells are n / ref mean / pred mean / MAE / RMSE / bias (m). Classes: 0 others, 1 ground, 2 low vegetation, 3 buildings, 4 water, 5 road, 6 tree.

Cells: n / ref mean / pred mean / MAE / RMSE / bias (m). — = class absent.

| Tile | 0-others | 1-ground | 2-low vegetation | 3-buildings | 4-water | 5-road | 6-tree |
|---|---|---|---|---|---|---|---|
| DC_20_43 | — | 298,427 / 0.01 / 0.04 / 0.05 / 0.61 / +0.03 | 87,421 / 0.57 / 0.86 / 1.19 / 2.81 / +0.29 | 151,458 / 5.34 / 1.78 / 3.58 / 4.12 / -3.56 | — | 150,879 / 1.64 / 0.97 / 0.84 / 1.99 / -0.67 | 360,391 / 15.90 / 13.87 / 3.77 / 4.80 / -2.03 |
| DC_35_45 | 210 / 2.16 / 0.00 / 2.16 / 2.27 / -2.16 | 448,653 / 0.04 / 0.03 / 0.06 / 0.52 / -0.01 | 70,728 / 0.32 / 0.33 / 0.55 / 1.57 / +0.00 | 98,637 / 8.40 / 5.92 / 2.51 / 3.24 / -2.48 | — | 319,093 / 2.16 / 1.24 / 0.98 / 2.46 / -0.92 | 111,255 / 14.04 / 9.58 / 4.93 / 6.32 / -4.46 |
| DC_50_52 | 27 / 1.82 / 0.00 / 1.82 / 1.84 / -1.82 | 69,875 / 0.08 / 0.06 / 0.12 / 0.75 / -0.02 | 48,248 / 0.69 / 1.58 / 1.79 / 4.10 / +0.89 | 67,656 / 5.68 / 2.12 / 3.75 / 4.52 / -3.56 | — | 121,342 / 2.87 / 1.51 / 1.64 / 3.79 / -1.35 | 741,428 / 24.84 / 21.87 / 4.69 / 6.04 / -2.97 |
| NYC_03486 | 49,284 / 0.61 / 0.17 / 0.64 / 1.59 / -0.44 | — | 217,512 / 0.52 / 0.19 / 0.64 / 1.37 / -0.33 | 3,897 / 7.61 / 8.08 / 1.89 / 2.23 / +0.46 | 3,362 / 0.36 / 0.05 / 0.37 / 0.89 / -0.31 | 189,508 / 0.67 / 0.26 / 0.49 / 1.60 / -0.41 | 585,013 / 9.60 / 8.00 / 4.64 / 5.86 / -1.60 |
| NYC_11587 | — | — | — | — | 1,048,576 / -0.53 / 0.00 / 0.68 / 1.13 / +0.53 | — | — |
| NYC_21651 | 75,395 / 1.45 / 0.88 / 1.88 / 3.70 / -0.57 | — | 50,712 / 0.49 / 1.24 / 1.50 / 3.44 / +0.74 | 47,602 / 7.42 / 4.20 / 4.07 / 4.98 / -3.22 | — | 147,764 / 0.86 / 0.34 / 1.14 / 2.86 / -0.52 | 727,103 / 14.60 / 11.90 / 5.33 / 6.71 / -2.69 |
| PHL_3725 | — | 329,796 / 0.30 / 0.01 / 0.29 / 1.42 / -0.29 | 282,308 / 0.25 / 0.00 / 0.25 / 1.35 / -0.25 | 233,717 / 7.13 / 2.86 / 4.28 / 4.59 / -4.27 | — | 96,424 / 0.20 / 0.00 / 0.20 / 1.13 / -0.20 | 106,331 / 5.10 / 0.87 / 4.27 / 5.21 / -4.23 |
| PHL_4292 | — | 215,614 / 0.55 / 0.10 / 0.48 / 1.87 / -0.45 | 142,305 / 0.39 / 0.04 / 0.38 / 1.31 / -0.36 | 204,083 / 9.64 / 4.37 / 5.37 / 6.63 / -5.27 | — | 435,216 / 0.86 / 0.02 / 0.84 / 2.03 / -0.83 | 51,358 / 3.41 / 0.15 / 3.26 / 3.84 / -3.26 |
| PHL_5091 | — | 244,756 / 0.42 / 0.01 / 0.42 / 1.10 / -0.41 | 258,783 / 0.17 / 0.01 / 0.16 / 0.57 / -0.16 | 264,979 / 6.59 / 2.09 / 4.72 / 4.97 / -4.50 | 52,534 / 0.08 / 0.00 / 0.08 / 0.35 / -0.08 | 89,464 / 0.21 / 0.00 / 0.21 / 0.56 / -0.21 | 138,060 / 3.60 / 0.27 / 3.35 / 4.20 / -3.33 |
| DC_03_26 | 168 / 2.22 / 0.17 / 2.19 / 3.01 / -2.04 | 79,149 / 0.10 / 0.61 / 0.66 / 2.54 / +0.51 | 103,685 / 0.47 / 1.98 / 2.39 / 5.63 / +1.51 | 202,774 / 7.56 / 4.12 / 4.27 / 5.41 / -3.44 | 4,497 / 2.63 / 14.84 / 14.60 / 16.74 / +12.20 | 225,170 / 7.36 / 4.82 / 3.38 / 5.75 / -2.54 | 433,133 / 19.32 / 15.65 / 5.24 / 6.60 / -3.67 |

### Across tiles (classes with at least 500 pixels on a tile)

| Class / height bin | Tiles | Median bias (m) | Bias range (m) | Tiles with negative bias | Median MAE (m) |
|---|---|---|---|---|---|
| 0-others | 2 | -0.50 | -0.57 to -0.44 | 2 of 2 | 1.26 |
| 1-ground | 7 | -0.02 | -0.45 to +0.51 | 5 of 7 | 0.29 |
| 2-low vegetation | 9 | +0.00 | -0.36 to +1.51 | 4 of 9 | 0.64 |
| 3-buildings | 9 | -3.56 | -5.27 to +0.46 | 8 of 9 | 4.07 |
| 4-water | 4 | +0.22 | -0.31 to +12.20 | 2 of 4 | 0.53 |
| 5-road | 9 | -0.67 | -2.54 to -0.20 | 9 of 9 | 0.84 |
| 6-tree | 9 | -3.26 | -4.46 to -1.60 | 9 of 9 | 4.64 |

### GAMUS −5 m floor, class boundaries and interiors

Kept in the primary metrics.

| Tile | GAMUS -5 m floor | Class-boundary px | Class-interior px |
|---|---|---|---|
| DC_20_43 | — | 118,432 / 1.56 / 1.12 / 1.48 / 3.00 / -0.44 | 930,144 / 7.16 / 5.78 / 2.12 / 3.47 / -1.38 |
| DC_35_45 | — | 96,105 / 2.04 / 1.20 / 1.25 / 2.82 / -0.84 | 952,471 / 3.07 / 2.06 / 1.11 / 2.70 / -1.01 |
| DC_50_52 | — | 71,808 / 3.36 / 2.68 / 2.56 / 4.71 / -0.68 | 976,768 / 19.40 / 16.82 / 3.93 / 5.49 / -2.58 |
| NYC_03486 | — | 66,910 / 1.68 / 0.66 / 1.78 / 2.90 / -1.02 | 981,666 / 5.91 / 4.85 / 2.92 / 4.58 / -1.06 |
| NYC_11587 | — | — | 1,048,576 / -0.53 / 0.00 / 0.68 / 1.13 / +0.53 |
| NYC_21651 | — | 53,976 / 3.11 / 2.23 / 3.41 / 5.37 / -0.88 | 994,600 / 11.12 / 8.96 / 4.30 / 5.95 / -2.16 |
| PHL_3725 | — | 57,660 / 2.01 / 0.20 / 1.82 / 3.11 / -1.81 | 990,916 / 2.30 / 0.76 / 1.55 / 2.94 / -1.54 |
| PHL_4292 | — | 57,321 / 2.19 / 0.44 / 1.80 / 3.18 / -1.75 | 991,255 / 2.59 / 0.92 / 1.70 / 3.47 / -1.67 |
| PHL_5091 | — | 67,191 / 1.59 / 0.10 / 1.51 / 2.56 / -1.49 | 981,385 / 2.35 / 0.60 / 1.81 / 3.02 / -1.75 |
| DC_03_26 | 4,963 / -5.00 / 17.26 / 22.26 / 22.89 / +22.26 | 154,021 / 4.36 / 3.67 / 3.07 / 5.44 / -0.69 | 894,555 / 12.25 / 9.45 / 4.23 / 6.05 / -2.79 |

## 10. Height-stratified analysis

Cells are n / ref mean / pred mean / MAE / RMSE / bias (m).

| Tile | [-∞,0) | [0,1) | [1,5) | [5,10) | [10,20) | [20,40) | [40,∞) |
|---|---|---|---|---|---|---|---|
| DC_20_43 | — | 495,430 / 0.05 / 0.12 / 0.16 / 1.04 / +0.07 | 128,770 / 3.04 / 0.83 / 3.04 / 3.53 / -2.21 | 152,102 / 7.48 / 3.99 / 4.50 / 5.06 / -3.49 | 138,300 / 14.90 / 13.47 / 3.82 / 4.93 / -1.43 | 133,974 / 24.09 / 21.41 / 3.46 / 4.64 / -2.68 | — |
| DC_35_45 | — | 743,678 / 0.04 / 0.04 / 0.08 / 0.51 / -0.00 | 73,339 / 2.23 / 0.49 / 2.13 / 2.46 / -1.75 | 110,386 / 8.18 / 5.66 / 2.77 / 3.63 / -2.51 | 85,596 / 14.15 / 9.30 / 5.13 / 6.56 / -4.86 | 35,577 / 22.81 / 16.74 / 6.10 / 7.34 / -6.08 | — |
| DC_50_52 | — | 198,119 / 0.06 / 0.23 / 0.27 / 1.44 / +0.16 | 68,316 / 3.10 / 2.06 / 3.57 / 4.84 / -1.05 | 90,776 / 7.28 / 4.85 / 5.09 / 6.03 / -2.44 | 192,387 / 15.19 / 13.35 / 4.93 / 6.36 / -1.84 | 482,315 / 30.48 / 26.67 / 4.56 / 5.94 / -3.81 | 16,663 / 40.98 / 33.99 / 6.98 / 7.41 / -6.98 |
| NYC_03486 | 74,591 / -0.02 / 1.25 / 1.27 / 3.80 / +1.27 | 385,346 / 0.11 / 1.09 / 1.16 / 3.46 / +0.99 | 154,753 / 2.74 / 1.78 / 2.96 / 3.73 / -0.96 | 155,000 / 7.44 / 5.19 / 4.45 / 5.17 / -2.25 | 241,826 / 14.43 / 10.91 / 4.39 / 5.51 / -3.52 | 37,060 / 21.89 / 15.59 / 6.30 / 7.02 / -6.30 | — |
| NYC_11587 | 1,041,129 / -0.61 / 0.00 / 0.61 / 0.62 / +0.61 | 10 / 0.43 / 0.00 / 0.43 / 0.50 / -0.43 | 878 / 2.51 / 0.00 / 2.51 / 2.59 / -2.51 | 1,370 / 7.57 / 0.00 / 7.57 / 7.68 / -7.57 | 5,133 / 12.69 / 0.00 / 12.69 / 12.79 / -12.69 | 56 / 20.57 / 0.00 / 20.57 / 20.57 / -20.57 | — |
| NYC_21651 | 17,102 / -0.03 / 2.90 / 2.93 / 5.96 / +2.93 | 258,321 / 0.10 / 1.71 / 1.75 / 4.36 / +1.62 | 89,972 / 3.00 / 3.78 / 3.97 / 5.29 / +0.78 | 147,052 / 7.57 / 5.65 / 4.94 / 5.74 / -1.92 | 329,854 / 15.43 / 11.99 / 4.69 / 6.01 / -3.44 | 206,275 / 22.93 / 16.56 / 6.43 / 7.62 / -6.37 | — |
| PHL_3725 | — | 671,759 / 0.07 / 0.00 / 0.07 / 0.20 / -0.07 | 141,908 / 2.95 / 0.17 / 2.79 / 3.06 / -2.77 | 205,927 / 7.72 / 3.00 / 4.73 / 5.01 / -4.72 | 28,465 / 11.52 / 4.17 / 7.35 / 8.05 / -7.35 | 456 / 23.93 / 2.69 / 21.24 / 22.20 / -21.24 | 61 / 83.56 / 0.14 / 83.43 / 92.88 / -83.43 |
| PHL_4292 | — | 684,544 / 0.08 / 0.01 / 0.09 / 0.24 / -0.08 | 157,698 / 2.99 / 0.27 / 2.81 / 3.08 / -2.72 | 102,867 / 7.09 / 1.65 / 5.51 / 5.85 / -5.44 | 98,241 / 13.16 / 7.11 / 6.06 / 6.81 / -6.05 | 5,226 / 26.56 / 3.84 / 22.71 / 23.59 / -22.71 | — |
| PHL_5091 | — | 618,736 / 0.09 / 0.00 / 0.09 / 0.24 / -0.08 | 199,266 / 2.97 / 0.09 / 2.89 / 3.14 / -2.88 | 210,892 / 7.07 / 1.58 / 5.50 / 5.65 / -5.49 | 19,678 / 14.07 / 12.32 / 4.66 / 5.72 / -1.75 | 4 / 21.37 / 3.29 / 18.08 / 18.29 / -18.08 | — |
| DC_03_26 | 5,444 / -4.77 / 17.01 / 21.78 / 22.50 / +21.78 | 274,324 / 0.12 / 0.73 / 0.82 / 2.75 / +0.61 | 152,488 / 2.97 / 1.72 / 3.20 / 4.12 / -1.25 | 162,703 / 7.34 / 4.13 / 4.90 / 5.57 / -3.21 | 199,142 / 15.26 / 11.96 / 5.15 / 6.74 / -3.30 | 254,092 / 27.23 / 21.26 / 6.31 / 7.88 / -5.97 | 383 / 40.66 / 28.02 / 12.64 / 12.72 / -12.64 |

### Across tiles (bins with at least 500 pixels)

| Height bin (m) | Tiles | Median bias (m) | Bias range (m) | Tiles with negative bias | Median MAE (m) |
|---|---|---|---|---|---|
| [-inf,0) | 4 | +2.10 | +0.61 to +21.78 | 0 of 4 | 2.10 |
| [0,1) | 9 | +0.07 | -0.08 to +1.62 | 4 of 9 | 0.16 |
| [1,5) | 10 | -1.98 | -2.88 to +0.78 | 9 of 10 | 2.92 |
| [5,10) | 10 | -3.35 | -7.57 to -1.92 | 10 of 10 | 4.92 |
| [10,20) | 10 | -3.48 | -12.69 to -1.43 | 10 of 10 | 5.03 |
| [20,40) | 7 | -6.08 | -22.71 to -2.68 | 7 of 7 | 6.30 |
| [40,inf) | 1 | -6.98 | -6.98 to -6.98 | 1 of 1 | 6.98 |

## 11. Cross-tile statistics

TerraHeight-S native, 10 tiles. Correlations are undefined on NYC_11587.

| Metric | n | Mean | Median | Std | Min | Max |
|---|---|---|---|---|---|---|
| MAE (m) | 10 | 2.391 | 1.922 | 1.280 | 0.681 | 4.252 |
| RMSE (m) | 10 | 3.848 | 3.439 | 1.573 | 1.133 | 5.966 |
| R² | 10 | 0.487 | 0.545 | 0.351 | -0.281 | 0.838 |
| Pearson | 9 | 0.824 | 0.780 | 0.090 | 0.671 | 0.933 |
| Spearman | 9 | 0.760 | 0.773 | 0.108 | 0.617 | 0.930 |
| Bias (m) | 10 | -1.478 | -1.616 | 0.878 | -2.484 | 0.531 |
| Tiled inference (s) | 10 | 12.737 | 12.938 | 0.452 | 12.062 | 13.327 |

**Pooled diagnostic** (all 10,485,760 pixels from the 10 tiles combined):
- MAE 2.391 m, RMSE 4.127 m, bias -1.478 m, R² 0.803, Pearson 0.910.
- The pooled R² is higher than the per-tile median (0.545) because pooling mixes tall and flat scenes. **Use the tile-level statistics.**

**Descriptive counts:**
- R² positive: 9/10
- R² above 0.5: 6/10
- MAE under 5 m: 10/10
- MAE under 3 m: 7/10
- Improvement over DA-V2: see §7

## 12. Qualitative results

Figures are in the scratchpad folder `p111c/`. Every panel uses fixed scales: height −5 to 45 m, error ±20 m. Values beyond those limits saturate; data are never altered.

**Figures:**
- `p111c_overview_all_tiles.png`: **all 10 tiles** (RGB, reference, prediction, error), so no tile is selected by hand.
- Three detailed figures (RGB, reference, prediction, signed error, class map), chosen by the rule fixed beforehand: best, median (lower median, 5th of 10) and worst by native MAE, each labelled.
  - `p111c_best_NYC_11587.png`: **BEST, but degenerate:** the all-water tile, where the prediction is a constant 0.
  - `p111c_median_PHL_5091.png`: **MEDIAN.** Low-relief PHL rowhouses.
  - `p111c_worst_NYC_21651.png`: **WORST.** Dense tree canopy.

**Observations:**
- **Predictions are smooth.** Tree crowns merge into blobs.
- **Low buildings are faint or missing.** PHL rowhouses of about 5–10 m are mostly predicted near 0, the dominant blue in PHL error maps. Occasionally a building is overpredicted, e.g. one large PHL_5091 roof.
- **Canopy error is partly reference texture.** The lidar reference is speckled while the prediction is smooth, which produces red/blue speckle inside crowns (NYC_21651).
- **The DC imagery is leaf-off** while the reference shows full crowns. The model still predicts canopy.
- **NYC_11587:** one structure above the water, visible in the reference at the bottom-left, is not predicted.
- **The tiling seam is visible** (e.g. column 630 in NYC_21651).

## 13. Runtime and memory

- **TerraHeight-S tiled:** 12.7 s per tile on average (12.1–13.3 s).
- **TerraHeight-S whole-image:** 19.5 s on average (18.2–20.8 s).
- **DA-V2 Small:** 1.7 s (1.6–1.9 s).
- **Peak memory:** 2343 MB working set for the combined process (both models, 10 tiles, all arrays kept). The TerraHeight-S-only peak measured in P1-11B was 831.5 MB.
- **Hardware:** Ryzen 5 5625U, CPU only.
- **Output:** float32, 1024 × 1024, no non-finite or negative values.

## 14. Failure modes

These are observed relationships only; no causality is claimed.

**Relationships across tiles** (Spearman between tile metric and scene covariate, n = 9–10, descriptive):
- **MAE:** ref mean +0.85, ref p99 +0.75, tree fraction +0.89, building fraction -0.09, water fraction -0.15.
- **R²:** ref mean +0.79, ref p99 +0.88, tree fraction +0.60, water fraction -0.55, negative-ref fraction -0.32.
- **Bias:** ref mean -0.65, tree fraction -0.59, building fraction -0.38.

In short: absolute error grows with scene height and tree cover, while relative fit (R²) is better in taller scenes and worse in low-relief or water scenes.

| Tile | Ref mean (m) | Ref p99 (m) | Ref max (m) | Tree % | Building % | Road % | Water % | Negative-ref % | TH affine slope a |
|---|---|---|---|---|---|---|---|---|---|
| DC_20_43 | 6.53 | 27.84 | 31.13 | 34.4 | 14.4 | 14.4 | 0.0 | 0.0 | 0.998 |
| DC_35_45 | 2.97 | 23.86 | 28.35 | 10.6 | 9.4 | 30.4 | 0.0 | 0.0 | 1.198 |
| DC_50_52 | 18.30 | 40.55 | 44.58 | 70.7 | 6.5 | 11.6 | 0.0 | 0.0 | 1.034 |
| NYC_03486 | 5.64 | 22.59 | 27.15 | 55.8 | 0.4 | 18.1 | 0.3 | 7.1 | 1.034 |
| NYC_11587 | -0.53 | -0.39 | 21.34 | 0.0 | 0.0 | 0.0 | 100.0 | 99.3 | undefined |
| NYC_21651 | 10.71 | 26.55 | 31.05 | 69.3 | 4.5 | 14.1 | 0.0 | 1.6 | 1.021 |
| PHL_3725 | 2.29 | 11.12 | 143.92 | 10.1 | 22.3 | 9.2 | 0.0 | 0.0 | 1.855 |
| PHL_4292 | 2.57 | 17.83 | 35.29 | 4.9 | 19.5 | 41.5 | 0.0 | 0.0 | 1.516 |
| PHL_5091 | 2.30 | 14.86 | 22.25 | 13.2 | 25.3 | 8.5 | 5.0 | 0.0 | 1.942 |
| DC_03_26 | 11.09 | 35.25 | 42.57 | 41.3 | 19.3 | 21.5 | 0.4 | 0.5 | 1.063 |

### P1-11B findings, rechecked

| P1-11B finding | Recurs? |
|---|---|
| Tall structures underestimated | **Yes.** Buildings have negative bias on 8 of 9 tiles, trees on 9 of 9. Every height bin from 5 m upward has negative bias on every tile. |
| Underestimation grows with height | **Yes.** Median bias is −3.35 m at 5–10 m, −3.48 m at 10–20 m and −6.08 m at 20–40 m. |
| Water and floor pixels are problematic | **Mixed.** Median water bias is near 0, but DC_03_26 is +12.2 m (the stream strip). Negative-reference pixels are always overpredicted (4 of 4 tiles), because the ReLU output can't go below 0. The −5 m floor exists only in DC_03_26. |
| Reference/image disagreements | **Yes.** Leaf-off imagery against a leaf-on-looking reference in DC; speckled lidar canopy; PHL_3725 spike pixels; roads in DC_03_26 carrying canopy height. |
| Visible tiling seam | **Yes, on 9/10 tiles** (all non-trivial ones). The step at column 394 or 630 is more than 2× the median step, and the whole-image output has no such step. |

**Not previously seen: low-relief compression.**
- On all three PHL tiles, about 80% of output pixels are exactly 0, and the affine slope is 1.5–1.9.
- Low objects (1–5 m) are largely missed: that bin has negative bias on 9 of 10 tiles.

### Sensitivity to the inference protocol

Tiled inference gave a lower MAE than whole-image on 9/10 tiles. On the tenth (NYC_11587, constant 0) they are identical. Blending choices change metrics noticeably, which is why the undocumented blending rule matters.

| Tile | Tiled MAE / R² | Whole-image MAE / R² | Mean abs(tiled − whole) (m) | Tiled step at col 394 / 630 vs median step | Whole-image step at col 394 | Whole-image time (s) |
|---|---|---|---|---|---|---|
| DC_20_43 | 2.049 / 0.838 | 2.458 / 0.787 | 0.91 | 0.189 / 0.111 vs 0.089 | 0.043 | 18.2 |
| DC_35_45 | 1.120 / 0.782 | 1.559 / 0.665 | 0.53 | 0.368 / 0.427 vs 0.050 | 0.068 | 18.4 |
| DC_50_52 | 3.841 / 0.838 | 4.617 / 0.792 | 1.79 | 0.489 / 3.052 vs 0.210 | 0.076 | 18.4 |
| NYC_03486 | 2.846 / 0.549 | 3.338 / 0.444 | 1.44 | 0.830 / 0.859 vs 0.147 | 0.161 | 19.7 |
| NYC_11587 | 0.681 / -0.281 | 0.681 / -0.281 | 0.00 | 0.000 / 0.000 vs 0.000 | 0.000 | 19.8 |
| NYC_21651 | 4.252 / 0.540 | 4.761 / 0.473 | 1.69 | 1.343 / 2.051 vs 0.184 | 0.164 | 20.3 |
| PHL_3725 | 1.564 / 0.313 | 1.963 / -0.036 | 0.41 | 0.140 / 0.139 vs 0.028 | 0.020 | 19.4 |
| PHL_4292 | 1.703 / 0.415 | 1.826 / 0.312 | 0.33 | 0.000 / 0.249 vs 0.031 | 0.000 | 20.8 |
| PHL_5091 | 1.795 / 0.171 | 1.965 / 0.007 | 0.26 | 0.177 / 0.049 vs 0.024 | 0.031 | 19.5 |
| DC_03_26 | 4.064 / 0.700 | 4.193 / 0.695 | 1.41 | 2.090 / 0.835 vs 0.198 | 0.207 | 20.1 |

## 15. Reproducibility limitations

Carried over from P1-11B:
- **Training code** is unavailable. The checkpoint records `PROJECT_DIR ./DepthWizard`, another SIH team's run.
- **Exact training splits** are not documented. §2 shows they are consistent with train/val.
- **The author's reported GAMUS metrics** (val MAE 1.312 m; test MAE 1.580 m) are not independently established. This experiment's 10 test tiles are not the same evaluation and **must not be compared with them as if they were**.
- **The full-tile blending rule** is undocumented; uniform averaging is TERRAIN-X's choice.
- **The link between the released checkpoint and the published metrics** is not independently established.

Added in this experiment:
- Only 10 tiles from 3 East-Coast US cities at 0.33 m. Tile-level variation is large.
- Covariate relationships are based on 9–10 tiles.

## 16. Decision

**B. PROMISING BUT INCONSISTENT — NEED MORE GAMUS VALIDATION.**

**What supports it:**
- **Reproducibility holds:** exact checkpoint, strict load, DC_03_26 reproduced exactly.
- **Strong on dense DC and NYC scenes:** native R² 0.54–0.84, Pearson 0.77–0.93, native scale correct (affine slope about 1).
- **Better than DA-V2 on most tiles:** ahead on MAE and R² in 8 of 10 like-for-like comparisons. DA-V2 raw output has no consistent relationship with AGL.

**What holds it back:**
- **Inconsistent across scene types:** the low-relief PHL tiles have native R² of only 0.17–0.42, with outputs compressed about 2× and ~80% exact zeros.
- **Degenerate on water:** a constant 0 output.
- **Tall objects are systematically underestimated** on every tile.
- **Inference-protocol effects:** a visible seam, and sensitivity to how tiles are blended.

**Sample limits:** 10 tiles from 3 cities isn't enough to separate model behaviour from tile selection. Not option A, because performance isn't consistent. Not option C, because accuracy in dense scenes is useful. Not option D, because no reproducibility problem was found.

**No integration regardless;** that requires a separate design review.

## 17. Recommendation for P1-11D

1. **Larger stratified GAMUS test sample, rule fixed in advance.** Use the same rule with more tiles per city (for example 10–15 per city, about 35 tiles). The aim is to establish per-city and per-relief distributions, especially whether low-relief compression is systematic or specific to PHL. Report tile-level statistics as the primary result.
2. **Cheap 0.6 m transfer proxy on GAMUS.** Resample the same test tiles from 0.33 m to 0.6 m (the Cartosat-2S resolution) and re-evaluate against a correspondingly resampled reference. This checks how the model degrades with resolution before real 0.6 m imagery is used.
3. **Inference protocol.** Test distance-weighted blending and the whole-image variant on the larger sample, and report the spread. Ask the uploader for the official inference and blending code.
4. **Provenance.** Ask the uploader for the training code, split lists and the checkpoint-to-metrics link.
5. **After that:** a real 0.6 m transfer test (NAIP or Cartosat imagery with an independent lidar nDSM), and only then design-review the separate DEM-plus-AGL composition question. This experiment doesn't address it.

## Machine-readable outputs

Scratchpad folder `p111c/` (outside the repository):
- `results.json`: per-tile metrics A–D, stratified results, seams, covariates, cross-tile statistics
- `selection.json`: tile IDs, rule, file hashes
- `integrity.json`
- `bench_multi.py` and `figures.py`
- `pred_*.npy`: TerraHeight-S predictions
- figures

The P1-11B results in `p111b/` were not modified.
