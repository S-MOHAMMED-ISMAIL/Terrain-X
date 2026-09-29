# P1-11D — TerraHeight-S failure analysis, 0.6 m transfer and larger GAMUS validation

**Research only.** No production code, registry, calibration, thresholds, DSM/DTM/nDSM behaviour, schema, migration, frontend or API was changed. TerraHeight-S is **not** integrated, and nothing was committed. P1-11B/C results and figures are untouched. The P1-11D workspace is the scratchpad folder `p111d/`.

| | |
|---|---|
| Generated (UTC) | 2026-09-27T08:38:02.710538+00:00 |
| **Decision** | **D. TRANSFER TO ~0.6 m FAILS.** The GAMUS-native results are separately **B-like** (promising but scene-dependent); see §7. |

TerraHeight-S predicts **height above ground (AGL / nDSM)**. **No experiment here establishes absolute terrain elevation.**

### Evidence labels used in this report

- **VERIFIED:** measured by TERRAIN-X from released files and data.
- **AUTHOR CLAIM:** stated by the TerraHeight-S uploader.
- **INFERENCE:** our interpretation of measured results.
- **UNRESOLVED:** cannot be established from the available evidence.

## 1. Provenance and configuration

| Item | Value | Status |
|---|---|---|
| Checkpoint | `benfox6515/TerraHeight-S` @ `b61c569d17ea5ce30adc414152d52af7b39430df`, `best_model.pth`, SHA-256 `739ed4e98168f1632192c666a3615d424188f2ce6597ebb435034d35fdc22f8a`, asserted at every run | VERIFIED |
| Loading / parameters | `strict=True` → <All keys matched successfully>; 24,785,089 parameters | VERIFIED |
| Architecture / model code | official Depth Anything V2 vits, features 64, out_channels [48, 96, 192, 384]; code at commit `a561b849…`, equal to the checkpoint's recorded `source_commit` | VERIFIED |
| Output scaling | `relu(net(x)) × 8.492877943662961`; `scale_m` = √(mean² + std²) of the stored training statistics | VERIFIED |
| Inference | **TERRAIN-X reproduction inference:** 630 px windows, 25% overlap (stride 472), uniform average; RGB ÷ 255, ImageNet normalisation | VERIFIED as our procedure; **not** the author's |
| Author's full-tile blending rule | not documented | UNRESOLVED |
| Training code / exact split lists | not public | UNRESOLVED |
| Train/val usage | checkpoint pixel counts equal GAMUS train (5,004) and val (859) tile counts minus a few pixels; test likely unseen | PARTIALLY VERIFIED |
| Val MAE 1.312 m, test MAE 1.580 m, Depth2Elevation comparison | model card | AUTHOR CLAIM, **not** reproduced here (different samples and protocol) |
| Checkpoint → published metrics link | not established | UNRESOLVED |
| Model scope | AGL/nDSM only (card; consistent with ReLU output ≥ 0 and GAMUS nDSM training) | AUTHOR CLAIM + consistent with measurement |

## 2. Part A — failure characterisation (the 10 P1-11C tiles)

**Setup:**
- **Data:** the P1-11C tiled predictions (`p111c/pred_*.npy`) and the P1-11C GAMUS files, unaltered. No new data.
- **Reproducibility check (A6):** fresh inference on 4 tiles was **bit-identical** to the saved P1-11C predictions (VERIFIED).
- **Valid pixels:** `build_gamus_valid_mask`, all 1,048,576 pixels on every tile.

### A1. Exact-zero output

| Tile | City | Pred == 0 | Pred < 0.01 m | Pred min / max / mean / std (m) | Ref min / max / mean / std (m) | Ref mean in zero region (m) | Zero-region px with ref > 2 m / > 5 m |
|---|---|---|---|---|---|---|---|
| DC_20_43 | DC | 59.4% | 59.5% | 0.00 / 28.67 / 5.25 / 8.21 | 0.00 / 31.13 / 6.53 / 8.51 | 0.92 | 17.4% / 5.7% |
| DC_35_45 | DC | 78.4% | 78.4% | 0.00 / 23.45 / 1.98 / 4.48 | 0.00 / 28.35 / 2.97 / 5.80 | 0.46 | 5.7% / 2.6% |
| DC_50_52 | DC | 25.5% | 25.5% | 0.00 / 40.17 / 15.85 / 12.66 | 0.00 / 44.58 / 18.30 / 13.50 | 1.52 | 24.3% / 11.6% |
| NYC_03486 | NYC | 51.4% | 51.4% | 0.00 / 21.86 / 4.59 / 5.98 | -2.87 / 27.15 / 5.64 / 6.68 | 1.18 | 20.2% / 8.2% |
| NYC_11587 | NYC | 100.0% | 100.0% | 0.00 / 0.00 / 0.00 / 0.00 | -1.28 / 21.34 / -0.53 / 1.00 | -0.53 | 0.7% / 0.6% |
| NYC_21651 | NYC | 29.4% | 29.4% | 0.00 / 24.59 / 8.61 / 7.41 | -0.84 / 31.05 / 10.71 / 8.73 | 2.06 | 26.9% / 17.2% |
| PHL_3725 | PHL | 79.5% | 79.6% | 0.00 / 10.21 / 0.73 / 1.63 | 0.00 / 143.92 / 2.29 / 3.56 | 0.83 | 14.3% / 4.7% |
| PHL_4292 | PHL | 82.5% | 82.6% | 0.00 / 15.19 / 0.89 / 2.49 | 0.00 / 35.29 / 2.57 / 4.52 | 0.94 | 15.9% / 6.2% |
| PHL_5091 | PHL | 81.9% | 81.9% | 0.00 / 21.84 / 0.57 / 2.14 | 0.00 / 22.25 / 2.30 / 3.29 | 1.11 | 21.5% / 6.8% |
| DC_03_26 | DC | 39.3% | 39.3% | 0.00 / 36.96 / 8.60 / 9.58 | -5.00 / 42.57 / 11.09 / 10.89 | 2.22 | 33.2% / 15.8% |

**Exact-zero share within each GAMUS class** (CLS used for analysis only):

| Tile | others | ground | low vegetation | buildings | water | road | tree |
|---|---|---|---|---|---|---|---|
| DC_20_43 | — | 99.2% | 86.2% | 49.6% | — | 89.4% | 11.6% |
| DC_35_45 | 100.0% | 99.3% | 92.6% | 6.4% | — | 89.6% | 16.2% |
| DC_50_52 | 100.0% | 98.4% | 76.6% | 55.0% | — | 81.8% | 3.4% |
| NYC_03486 | 92.9% | — | 93.4% | 0.1% | 95.8% | 93.2% | 18.8% |
| NYC_11587 | — | — | — | — | 100.0% | — | — |
| NYC_21651 | 81.3% | — | 78.8% | 12.9% | — | 92.7% | 8.7% |
| PHL_3725 | — | 99.4% | 99.8% | 21.6% | — | 100.0% | 73.0% |
| PHL_4292 | — | 97.1% | 98.0% | 22.8% | — | 97.3% | 90.6% |
| PHL_5091 | — | 99.7% | 99.7% | 38.4% | 100.0% | 100.0% | 81.6% |
| DC_03_26 | 81.0% | 90.3% | 76.7% | 39.3% | 1.3% | 58.3% | 11.4% |

**Across the 10 tiles** (Spearman, descriptive, n = 10): zero fraction vs ref_mean -0.93, ref_max -0.42, frac_negative_ref -0.05, frac_building +0.26, frac_tree -0.95, frac_low_veg +0.24, frac_ground +0.26.

**What the measurements show (VERIFIED):**
- **Most zeros are correct AGL.** Exact-zero pixels sit mainly on ground, low vegetation and road. The share of each class predicted zero is ground 90–100%, low vegetation 77–100% and road 58–100% (road's lowest is DC_03_26).
- **Some are missed objects.** 14–33% of pixels inside zero regions have reference heights above 2 m.
- **Buildings are zeroed at 0–55%** of building pixels, depending on the tile.
- **PHL trees are mostly zeroed** (73–91% of tree pixels), whereas trees on DC and NYC tiles are 3–19% zero.

**INFERENCE:** the zero behaviour is systematic. The model outputs zero for low and small objects, and in PHL for most trees; the PHL imagery appears leaf-off. The histograms in Fig 1 also show an effective height ceiling on PHL tiles, at about 10 m (PHL_3725) and about 15 m (PHL_4292), while the reference continues beyond 30 m.

### A2. Height compression

Diagnostic only: ordinary least squares of reference = a × prediction + b over all valid pixels. Native predictions are not altered.

| Tile | City | OLS a | OLS b | R² | Pearson | Spearman | P1-11C robust a |
|---|---|---|---|---|---|---|---|
| DC_20_43 | DC | 0.963 | 1.471 | 0.862 | 0.928 | 0.875 | 0.998 |
| DC_35_45 | DC | 1.179 | 0.635 | 0.830 | 0.911 | 0.781 | 1.198 |
| DC_50_52 | DC | 0.995 | 2.536 | 0.871 | 0.933 | 0.930 | 1.034 |
| NYC_03486 | NYC | 0.859 | 1.705 | 0.590 | 0.768 | 0.725 | 1.034 |
| NYC_11587 | NYC | undefined (zero-variance prediction) | — | — | — | — | n/a |
| NYC_21651 | NYC | 0.915 | 2.827 | 0.603 | 0.776 | 0.773 | 1.021 |
| PHL_3725 | PHL | 1.704 | 1.046 | 0.609 | 0.780 | 0.670 | 1.855 |
| PHL_4292 | PHL | 1.413 | 1.304 | 0.604 | 0.777 | 0.617 | 1.516 |
| PHL_5091 | PHL | 1.027 | 1.719 | 0.450 | 0.671 | 0.634 | 1.942 |
| DC_03_26 | DC | 0.985 | 2.609 | 0.752 | 0.867 | 0.834 | 1.063 |

**Corrects P1-11C:**
- **VERIFIED:** under plain OLS the PHL slopes are 1.70 / 1.41 / 1.03. The 1.5–1.9 values reported in P1-11C came from the **sigma-clipped robust** fit.
- **INFERENCE:** PHL scale compression is **not consistent** across PHL tiles. It's clear on PHL_3725 and PHL_4292 and absent on PHL_5091. DC slopes are 0.96–1.18; NYC slopes are 0.86–0.92, i.e. slightly *over*-scaled.

### A3. Height-bin error

Cells are n / MAE / RMSE / bias (m).

| Tile | [-∞,0) | [0,1) | [1,5) | [5,10) | [10,20) | [20,40) | [40,∞) |
|---|---|---|---|---|---|---|---|
| DC_20_43 | — | 495,430 / 0.16 / 1.04 / +0.07 | 128,770 / 3.04 / 3.53 / -2.21 | 152,102 / 4.50 / 5.06 / -3.49 | 138,300 / 3.82 / 4.93 / -1.43 | 133,974 / 3.46 / 4.64 / -2.68 | — |
| DC_35_45 | — | 743,678 / 0.08 / 0.51 / -0.00 | 73,339 / 2.13 / 2.46 / -1.75 | 110,386 / 2.77 / 3.63 / -2.51 | 85,596 / 5.13 / 6.56 / -4.86 | 35,577 / 6.10 / 7.34 / -6.08 | — |
| DC_50_52 | — | 198,119 / 0.27 / 1.44 / +0.16 | 68,316 / 3.57 / 4.84 / -1.05 | 90,776 / 5.09 / 6.03 / -2.44 | 192,387 / 4.93 / 6.36 / -1.84 | 482,315 / 4.56 / 5.94 / -3.81 | 16,663 / 6.98 / 7.41 / -6.98 |
| NYC_03486 | 74,591 / 1.27 / 3.80 / +1.27 | 385,346 / 1.16 / 3.46 / +0.99 | 154,753 / 2.96 / 3.73 / -0.96 | 155,000 / 4.45 / 5.17 / -2.25 | 241,826 / 4.39 / 5.51 / -3.52 | 37,060 / 6.30 / 7.02 / -6.30 | — |
| NYC_11587 | 1,041,129 / 0.61 / 0.62 / +0.61 | 10 / 0.43 / 0.50 / -0.43 | 878 / 2.51 / 2.59 / -2.51 | 1,370 / 7.57 / 7.68 / -7.57 | 5,133 / 12.69 / 12.79 / -12.69 | 56 / 20.57 / 20.57 / -20.57 | — |
| NYC_21651 | 17,102 / 2.93 / 5.96 / +2.93 | 258,321 / 1.75 / 4.36 / +1.62 | 89,972 / 3.97 / 5.29 / +0.78 | 147,052 / 4.94 / 5.74 / -1.92 | 329,854 / 4.69 / 6.01 / -3.44 | 206,275 / 6.43 / 7.62 / -6.37 | — |
| PHL_3725 | — | 671,759 / 0.07 / 0.20 / -0.07 | 141,908 / 2.79 / 3.06 / -2.77 | 205,927 / 4.73 / 5.01 / -4.72 | 28,465 / 7.35 / 8.05 / -7.35 | 456 / 21.24 / 22.20 / -21.24 | 61 / 83.43 / 92.88 / -83.43 |
| PHL_4292 | — | 684,544 / 0.09 / 0.24 / -0.08 | 157,698 / 2.81 / 3.08 / -2.72 | 102,867 / 5.51 / 5.85 / -5.44 | 98,241 / 6.06 / 6.81 / -6.05 | 5,226 / 22.71 / 23.59 / -22.71 | — |
| PHL_5091 | — | 618,736 / 0.09 / 0.24 / -0.08 | 199,266 / 2.89 / 3.14 / -2.88 | 210,892 / 5.50 / 5.65 / -5.49 | 19,678 / 4.66 / 5.72 / -1.75 | 4 / 18.08 / 18.29 / -18.08 | — |
| DC_03_26 | 5,444 / 21.78 / 22.50 / +21.78 | 274,324 / 0.82 / 2.75 / +0.61 | 152,488 / 3.20 / 4.12 / -1.25 | 162,703 / 4.90 / 5.57 / -3.21 | 199,142 / 5.15 / 6.74 / -3.30 | 254,092 / 6.31 / 7.88 / -5.97 | 383 / 12.64 / 12.72 / -12.64 |

**Median across tiles** (bins with at least 500 pixels):

| Height bin (m) | Tiles | Median bias | Bias range | Negative-bias tiles | Median MAE | Median RMSE |
|---|---|---|---|---|---|---|
| [-∞,0) | 4 | 2.10 | 0.61 to 21.78 | 0 of 4 | 2.10 | 4.88 |
| [0,1) | 9 | 0.07 | -0.08 to 1.62 | 4 of 9 | 0.16 | 1.04 |
| [1,5) | 10 | -1.98 | -2.88 to 0.78 | 9 of 10 | 2.92 | 3.34 |
| [5,10) | 10 | -3.35 | -7.57 to -1.92 | 10 of 10 | 4.92 | 5.61 |
| [10,20) | 10 | -3.48 | -12.69 to -1.43 | 10 of 10 | 5.03 | 6.46 |
| [20,40) | 7 | -6.08 | -22.71 to -2.68 | 7 of 7 | 6.30 | 7.34 |
| [40,∞) | 1 | -6.98 | -6.98 to -6.98 | 1 of 1 | 6.98 | 7.41 |

**VERIFIED:** tall-object underestimation persists. Every bin from 5 m up has negative bias on every tile, and the median bias grows with height.

### A4. Class error

Cells are n / MAE / RMSE / bias (m).

| Tile | others | ground | low vegetation | buildings | water | road | tree |
|---|---|---|---|---|---|---|---|
| DC_20_43 | — | 298,427 / 0.05 / 0.61 / +0.03 | 87,421 / 1.19 / 2.81 / +0.29 | 151,458 / 3.58 / 4.12 / -3.56 | — | 150,879 / 0.84 / 1.99 / -0.67 | 360,391 / 3.77 / 4.80 / -2.03 |
| DC_35_45 | 210 / 2.16 / 2.27 / -2.16 | 448,653 / 0.06 / 0.52 / -0.01 | 70,728 / 0.55 / 1.57 / +0.00 | 98,637 / 2.51 / 3.24 / -2.48 | — | 319,093 / 0.98 / 2.46 / -0.92 | 111,255 / 4.93 / 6.32 / -4.46 |
| DC_50_52 | 27 / 1.82 / 1.84 / -1.82 | 69,875 / 0.12 / 0.75 / -0.02 | 48,248 / 1.79 / 4.10 / +0.89 | 67,656 / 3.75 / 4.52 / -3.56 | — | 121,342 / 1.64 / 3.79 / -1.35 | 741,428 / 4.69 / 6.04 / -2.97 |
| NYC_03486 | 49,284 / 0.64 / 1.59 / -0.44 | — | 217,512 / 0.64 / 1.37 / -0.33 | 3,897 / 1.89 / 2.23 / +0.46 | 3,362 / 0.37 / 0.89 / -0.31 | 189,508 / 0.49 / 1.60 / -0.41 | 585,013 / 4.64 / 5.86 / -1.60 |
| NYC_11587 | — | — | — | — | 1,048,576 / 0.68 / 1.13 / +0.53 | — | — |
| NYC_21651 | 75,395 / 1.88 / 3.70 / -0.57 | — | 50,712 / 1.50 / 3.44 / +0.74 | 47,602 / 4.07 / 4.98 / -3.22 | — | 147,764 / 1.14 / 2.86 / -0.52 | 727,103 / 5.33 / 6.71 / -2.69 |
| PHL_3725 | — | 329,796 / 0.29 / 1.42 / -0.29 | 282,308 / 0.25 / 1.35 / -0.25 | 233,717 / 4.28 / 4.59 / -4.27 | — | 96,424 / 0.20 / 1.13 / -0.20 | 106,331 / 4.27 / 5.21 / -4.23 |
| PHL_4292 | — | 215,614 / 0.48 / 1.87 / -0.45 | 142,305 / 0.38 / 1.31 / -0.36 | 204,083 / 5.37 / 6.63 / -5.27 | — | 435,216 / 0.84 / 2.03 / -0.83 | 51,358 / 3.26 / 3.84 / -3.26 |
| PHL_5091 | — | 244,756 / 0.42 / 1.10 / -0.41 | 258,783 / 0.16 / 0.57 / -0.16 | 264,979 / 4.72 / 4.97 / -4.50 | 52,534 / 0.08 / 0.35 / -0.08 | 89,464 / 0.21 / 0.56 / -0.21 | 138,060 / 3.35 / 4.20 / -3.33 |
| DC_03_26 | 168 / 2.19 / 3.01 / -2.04 | 79,149 / 0.66 / 2.54 / +0.51 | 103,685 / 2.39 / 5.63 / +1.51 | 202,774 / 4.27 / 5.41 / -3.44 | 4,497 / 14.60 / 16.74 / +12.20 | 225,170 / 3.38 / 5.75 / -2.54 | 433,133 / 5.24 / 6.60 / -3.67 |

| Class | Tiles | Median bias | Bias range | Negative-bias tiles | Median MAE | Median RMSE |
|---|---|---|---|---|---|---|
| 0-others | 2 | -0.50 | -0.57 to -0.44 | 2 of 2 | 1.26 | 2.64 |
| 1-ground | 7 | -0.02 | -0.45 to 0.51 | 5 of 7 | 0.29 | 1.10 |
| 2-low vegetation | 9 | 0.00 | -0.36 to 1.51 | 4 of 9 | 0.64 | 1.57 |
| 3-buildings | 9 | -3.56 | -5.27 to 0.46 | 8 of 9 | 4.07 | 4.59 |
| 4-water | 4 | 0.22 | -0.31 to 12.20 | 2 of 4 | 0.53 | 1.01 |
| 5-road | 9 | -0.67 | -2.54 to -0.20 | 9 of 9 | 0.84 | 2.03 |
| 6-tree | 9 | -3.26 | -4.46 to -1.60 | 9 of 9 | 4.64 | 5.86 |

**VERIFIED:**
- **Buildings** are underestimated on 8 of 9 tiles.
- **Trees** are underestimated on 9 of 9.
- **Ground and low vegetation** are close to unbiased.
- **Water** is mixed; its largest error is DC_03_26's stream strip, where the reference and image disagree.

### A5. Water failure (NYC_11587)

**Measurements (VERIFIED):**
- **The class map is 100% water;** RGB channel means 71.0, 88.0, 85.9 with low variance, visually open water with a wake.
- **The prediction is exactly 0 everywhere:** True (range 0.0–0.0, variance 0.0).
- **The reference ranges -1.28–21.34 m** (variance 1.003, mean -0.53). It includes 7,300 pixels above 2 m in rows/cols [933, 197, 1023, 372], a structure labelled "water" in the class map.

**Classification:** a **domain/failure observation**, not an accuracy score. The undefined correlation and negative R² (the reference has almost no variance) are **not** counted as model evidence. The model outputs a constant over open water and misses the structure.

### A6. Tiling seam

Sensitivity check only; the primary method is unchanged. Tiles chosen by rule: first non-degenerate P1-11C tile per city in selection order, plus DC_03_26.

| Tile | Fresh tiled = saved P1-11C | Tiled MAE / RMSE / R² | Whole MAE / RMSE / R² | Mean (tiled − whole) / mean abs / p99 abs (m) | Tiled step at col 394 / 630 vs median | Whole step at col 394 / 630 | Tiled MAE: overlap band / elsewhere |
|---|---|---|---|---|---|---|---|
| DC_20_43 | True | 2.049 / 3.422 / 0.838 | 2.458 / 3.925 / 0.787 | +0.679 / 0.914 / 5.50 | 0.189 / 0.111 vs 0.089 | 0.043 / 0.043 | 2.30 / 1.87 |
| NYC_03486 | True | 2.846 / 4.489 / 0.549 | 3.338 / 4.984 / 0.444 | +1.295 / 1.443 / 6.84 | 0.830 / 0.859 vs 0.147 | 0.161 / 0.116 | 2.67 / 2.96 |
| PHL_3725 | True | 1.564 / 2.950 / 0.313 | 1.963 / 3.622 / -0.036 | +0.402 / 0.409 / 3.89 | 0.140 / 0.139 vs 0.028 | 0.020 / 0.020 | 1.27 / 1.77 |
| DC_03_26 | True | 4.064 / 5.966 / 0.700 | 4.193 / 6.014 / 0.695 | -0.692 / 1.406 / 8.71 | 2.090 / 0.835 vs 0.198 | 0.207 / 0.164 | 4.43 / 3.81 |

**VERIFIED:**
- **The seam is numerically real.** Height steps at window edges are about 2–11× the typical step and absent from whole-image output.
- **It is local.** MAE inside the overlap band is not consistently higher.
- **Tiled inference still has lower MAE** than whole-image on all 4 tiles (by 0.13–0.49 m).

**INFERENCE:** the seam is **both** visual and numerically real, but not a driver of aggregate accuracy. Whole-image inference (outside the 630 px training crop size) is worse overall.

## 3. Part B — controlled ~0.6 m transfer (10 P1-11C tiles)

**Construction** (VERIFIED as performed):

| | |
|---|---|
| Native pixel size | 0.33 m (GAMUS paper (arXiv 2305.14914): 0.33 m; AUTHOR/DATASET documentation) |
| Target | 0.6 m → **563 × 563 px**, effective 0.6002 m, scale factor 0.5498 |
| RGB | PIL Image.BOX area average on uint8 RGB (deterministic); no colour/contrast change |
| Reference | original float32 AGL -> PIL Image.BOX area average to 563x563 (mean height per 0.6 m cell); original unmodified |
| Same-grid comparison | P1-11C saved native prediction area-averaged with the same BOX rule |
| 0.6 m inference | primary: single 630x630 window: reflect-pad 563->630 (training crop size), crop back; sensitivity: single window reflect-padded 563->574 (next multiple of 14) |

This is **not** a claim that the model was trained for 0.6 m. It's a controlled transfer test.

| Tile | Native input (0.6 m grid) MAE / RMSE / bias / R² / Pearson / zero% | 0.6 m input MAE / RMSE / bias / R² / Pearson / zero% | Pad-574 MAE | OLS a native → 0.6 m | Output range 0.6 m (m) | Time (s) |
|---|---|---|---|---|---|---|
| DC_20_43 | 2.032 / 3.374 / -1.27 / 0.842 / 0.931 / 58.7% | 3.975 / 6.119 / -3.85 / 0.481 / 0.882 / 69.5% | 3.781 | 0.963 → 1.522 | 0.00–18.83 | 2.2 |
| DC_35_45 | 1.113 / 2.677 / -0.99 / 0.786 / 0.914 / 78.0% | 2.221 / 4.630 / -2.20 / 0.360 / 0.843 / 82.3% | 2.105 | 1.180 → 2.169 | 0.00–13.18 | 2.1 |
| DC_50_52 | 3.789 / 5.345 / -2.45 / 0.842 / 0.936 / 24.9% | 7.724 / 9.592 / -7.21 / 0.492 / 0.907 / 29.7% | 7.379 | 0.995 → 1.300 | 0.00–28.04 | 2.1 |
| NYC_03486 | 2.442 / 3.764 / -1.06 / 0.634 / 0.825 / 50.5% | 4.160 / 6.093 / -4.04 / 0.041 / 0.743 / 66.2% | 4.162 | 0.859 → 1.674 | 0.00–13.95 | 2.1 |
| NYC_11587 | 0.680 / 1.079 / +0.53 / -0.319 / n/a / 100.0% | 0.680 / 1.079 / +0.53 / -0.319 / n/a / 100.0% | 0.680 | n/a → n/a | 0.00–0.00 | 2.0 |
| NYC_21651 | 3.754 / 5.247 / -2.10 / 0.599 / 0.818 / 28.6% | 6.529 / 8.463 / -5.93 / -0.043 / 0.709 / 33.2% | 6.383 | 0.915 → 1.350 | 0.00–15.33 | 2.1 |
| PHL_3725 | 1.563 / 2.872 / -1.56 / 0.324 / 0.795 / 78.9% | 2.133 / 3.873 / -2.13 / -0.229 / 0.555 / 86.8% | 2.076 | 1.706 → 3.704 | 0.00–6.63 | 2.0 |
| PHL_4292 | 1.701 / 3.437 / -1.67 / 0.418 / 0.780 / 82.0% | 2.254 / 4.522 / -2.24 / -0.008 / 0.603 / 85.1% | 2.221 | 1.414 → 2.416 | 0.00–7.96 | 2.2 |
| PHL_5091 | 1.793 / 2.972 / -1.73 / 0.173 / 0.674 / 81.2% | 2.142 / 3.622 / -2.14 / -0.229 / 0.583 / 90.0% | 2.130 | 1.028 → 2.795 | 0.00–7.16 | 2.0 |
| DC_03_26 | 4.024 / 5.891 / -2.49 / 0.705 / 0.871 / 38.2% | 6.151 / 8.272 / -5.32 / 0.419 / 0.841 / 45.2% | 6.340 | 0.986 → 1.351 | 0.00–23.70 | 2.1 |

**Change from native to 0.6 m input**, 9 non-degenerate tiles, same evaluation grid:
- MAE +1.72 m (median; range +0.35 to +3.93)
- R² -0.43 (median; range -0.64 to -0.29)
- Pearson -0.08
- Bias -2.57 m
- Zero fraction +7.0 points

| Height bin | Median bias, native input (m) | Median bias, 0.6 m input (m) | Tiles |
|---|---|---|---|
| [1,5) | -1.98 | -2.55 | 10 |
| [5,10) | -3.32 | -6.54 | 10 |
| [10,20) | -3.00 | -9.95 | 10 |
| [20,40) | -5.87 | -13.31 | 7 |

**Memory:** peak process RAM was 802 MB; each 0.6 m inference took about 2 s.

**VERIFIED:** at ~0.6 m input the prediction keeps much of its spatial ranking (Pearson falls only modestly), but **the height scale collapses**:
- The OLS slope rises to 1.3–3.7.
- Predicted means roughly halve.
- Underestimation of tall objects roughly doubles; median bias at 20–40 m reaches about −13 m.
- MAE worsens on every non-degenerate tile, and R² falls to ≤ 0.49.

**INFERENCE:** consistent with a model that infers height partly from object size in pixels. Halving the resolution roughly halves predicted heights.

**UNRESOLVED:** whether upsampling 0.6 m imagery back to 0.33 m before inference would recover the scale. Not tested.

**Scope:** GAMUS downsampling is **not** real Cartosat-2S imagery (different sensor, optics and processing). So this result shows failure under controlled downsampling. It doesn't prove behaviour on real 0.6 m data.

## 4. Part C — larger GAMUS validation (36 tiles)

**Selection** (fixed and written to `selection_c.json` before inference): per city (DC,NYC,PHL): sorted test RGB ids; positions floor((k+0.5)*n/12), k=0..11; fixed before any Part C inference.
- 12 tiles per city: DC 12, NYC 12, PHL 12.
- **0 tiles overlap P1-11C**, so this is an independent sample.
- 324 MB downloaded (RGB + AGL + CLS only), with SHA-256 recorded.
- Whole-image sensitivity: k=0 tile of each city.

### C2. Data integrity (VERIFIED)

- Every tile: 1024 × 1024 RGB uint8, AGL float32, no non-finite AGL.
- **0 malformed tiles.** No tile was discarded.
- The ground truth was unchanged on every tile.

| Tile | City | k | RGB | AGL min / max / mean / std (m) | AGL < 0 px | AGL = −5 px | AGL > 100 m px | CLS dtype / classes | Malformed |
|---|---|---|---|---|---|---|---|---|---|
| DC_12_15 | DC | 0 | 1024x1024x3 uint8 | 0.00 / 35.60 / 7.64 / 7.03 | 0 | 0 | 0 | float32 / 0,1,2,3,5,6 | False |
| DC_19_36 | DC | 1 | 1024x1024x3 uint8 | 0.00 / 29.88 / 6.99 / 8.00 | 0 | 0 | 0 | float32 / 0,1,2,3,5,6 | False |
| DC_22_21 | DC | 2 | 1024x1024x3 uint8 | -5.00 / 43.90 / 14.42 / 11.82 | 711 | 676 | 0 | float32 / 0,1,2,3,4,5,6 | False |
| DC_26_08 | DC | 3 | 1024x1024x3 uint8 | 0.00 / 35.50 / 8.61 / 8.36 | 0 | 0 | 0 | float32 / 0,1,2,3,5,6 | False |
| DC_30_13 | DC | 4 | 1024x1024x3 uint8 | 0.00 / 38.58 / 8.64 / 8.47 | 0 | 0 | 0 | float32 / 0,1,2,3,5,6 | False |
| DC_33_53 | DC | 5 | 1024x1024x3 uint8 | 0.00 / 29.48 / 3.37 / 4.62 | 0 | 0 | 0 | float32 / 0,1,2,3,5,6 | False |
| DC_36_57 | DC | 6 | 1024x1024x3 uint8 | 0.00 / 31.50 / 8.75 / 8.24 | 0 | 0 | 0 | float32 / 0,1,2,5,6 | False |
| DC_40_43 | DC | 7 | 1024x1024x3 uint8 | 0.00 / 24.50 / 4.05 / 4.90 | 0 | 0 | 0 | float32 / 0,1,2,3,5,6 | False |
| DC_44_21 | DC | 8 | 1024x1024x3 uint8 | -5.00 / 22.93 / 1.66 / 5.61 | 186746 | 185920 | 0 | float32 / 0,1,2,4,5,6 | False |
| DC_47_47 | DC | 9 | 1024x1024x3 uint8 | 0.00 / 34.21 / 4.60 / 7.13 | 0 | 0 | 0 | float32 / 0,1,2,3,5,6 | False |
| DC_54_40 | DC | 10 | 1024x1024x3 uint8 | -5.00 / 21.41 / 1.29 / 4.80 | 199795 | 198929 | 0 | float32 / 0,1,2,3,4,5,6 | False |
| DC_64_40 | DC | 11 | 1024x1024x3 uint8 | 0.00 / 25.00 / 4.92 / 5.80 | 0 | 0 | 0 | float32 / 0,1,2,3,5,6 | False |
| NYC_01826 | NYC | 0 | 1024x1024x3 uint8 | -0.57 / 26.62 / 8.78 / 6.97 | 32072 | 0 | 0 | uint8 / 0,1,2,3,5,6 | False |
| NYC_02942 | NYC | 1 | 1024x1024x3 uint8 | -0.88 / 36.07 / 14.70 / 7.73 | 16397 | 0 | 0 | uint8 / 0,2,5,6 | False |
| NYC_04059 | NYC | 2 | 1024x1024x3 uint8 | -1.10 / 25.47 / 3.28 / 4.77 | 56638 | 0 | 0 | uint8 / 0,1,2,3,4,5,6 | False |
| NYC_04939 | NYC | 3 | 1024x1024x3 uint8 | -0.76 / 15.95 / 1.15 / 2.22 | 96950 | 0 | 0 | uint8 / 0,2,4,5,6 | False |
| NYC_05703 | NYC | 4 | 1024x1024x3 uint8 | -1.01 / 17.82 / 0.17 / 1.08 | 188064 | 0 | 0 | uint8 / 0,2,4,5,6 | False |
| NYC_08668 | NYC | 5 | 1024x1024x3 uint8 | -0.42 / 19.83 / 1.56 / 2.61 | 75067 | 0 | 0 | uint8 / 0,2,3,4,5,6 | False |
| NYC_15287 | NYC | 6 | 1024x1024x3 uint8 | -1.62 / 27.93 / 6.69 / 5.48 | 47934 | 0 | 0 | uint8 / 0,2,3,5,6 | False |
| NYC_17337 | NYC | 7 | 1024x1024x3 uint8 | -0.69 / 25.91 / 5.28 / 5.68 | 53322 | 0 | 0 | uint8 / 0,2,3,5,6 | False |
| NYC_19359 | NYC | 8 | 1024x1024x3 uint8 | -0.80 / 23.97 / 4.20 / 4.65 | 55088 | 0 | 0 | uint8 / 0,2,3,5,6 | False |
| NYC_21098 | NYC | 9 | 1024x1024x3 uint8 | -1.04 / 36.47 / 11.50 / 9.21 | 37876 | 0 | 0 | uint8 / 0,2,3,5,6 | False |
| NYC_22037 | NYC | 10 | 1024x1024x3 uint8 | -1.84 / 29.43 / 7.24 / 8.62 | 111851 | 0 | 0 | uint8 / 0,2,3,5,6 | False |
| NYC_22623 | NYC | 11 | 1024x1024x3 uint8 | -1.16 / 16.52 / 3.13 / 3.73 | 51702 | 0 | 0 | uint8 / 0,2,3,5,6 | False |
| PHL_3504 | PHL | 0 | 1024x1024x3 uint8 | 0.00 / 15.13 / 3.52 / 4.15 | 0 | 0 | 0 | uint8 / 1,2,3,5,6 | False |
| PHL_3651 | PHL | 1 | 1024x1024x3 uint8 | 0.00 / 33.48 / 3.61 / 4.00 | 0 | 0 | 0 | uint8 / 1,2,3,5,6 | False |
| PHL_3787 | PHL | 2 | 1024x1024x3 uint8 | 0.00 / 26.89 / 0.58 / 1.20 | 0 | 0 | 0 | uint8 / 1,2,3,6 | False |
| PHL_3934 | PHL | 3 | 1024x1024x3 uint8 | 0.00 / 132.76 / 1.48 / 2.68 | 0 | 0 | 4 | uint8 / 1,2,3,5,6 | False |
| PHL_4078 | PHL | 4 | 1024x1024x3 uint8 | 0.00 / 23.64 / 3.43 / 4.38 | 0 | 0 | 0 | uint8 / 1,2,3,5,6 | False |
| PHL_4219 | PHL | 5 | 1024x1024x3 uint8 | 0.00 / 56.80 / 5.12 / 7.08 | 0 | 0 | 0 | uint8 / 1,2,3,5,6 | False |
| PHL_4367 | PHL | 6 | 1024x1024x3 uint8 | 0.00 / 20.20 / 1.80 / 3.61 | 0 | 0 | 0 | uint8 / 1,2,3,4,5 | False |
| PHL_4582 | PHL | 7 | 1024x1024x3 uint8 | 0.00 / 35.69 / 4.15 / 6.82 | 0 | 0 | 0 | uint8 / 1,2,3,5,6 | False |
| PHL_4852 | PHL | 8 | 1024x1024x3 uint8 | 0.00 / 31.23 / 3.09 / 4.15 | 0 | 0 | 0 | uint8 / 1,2,3,4,5,6 | False |
| PHL_5012 | PHL | 9 | 1024x1024x3 uint8 | 0.00 / 26.73 / 2.72 / 5.15 | 0 | 0 | 0 | uint8 / 1,2,3,5,6 | False |
| PHL_5175 | PHL | 10 | 1024x1024x3 uint8 | 0.00 / 33.36 / 1.45 / 2.47 | 0 | 0 | 0 | uint8 / 1,2,3,5,6 | False |
| PHL_5344 | PHL | 11 | 1024x1024x3 uint8 | 0.00 / 41.76 / 3.11 / 6.30 | 0 | 0 | 0 | uint8 / 1,2,4,5,6 | False |

### C4. TerraHeight-S native metrics

No fit. Label: TERRAIN-X reproduction inference (not official TerraHeight-S inference).

| Tile | City | MAE | RMSE | R² | Pearson | Spearman | Bias | Zero% | Pred min / max / mean / std | Ref mean / std | Valid / invalid | Time (s) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| DC_12_15 | DC | 3.431 | 4.799 | 0.533 | 0.844 | 0.819 | -2.895 | 46.7% | 0.00 / 28.94 / 4.74 / 6.60 | 7.64 / 7.03 | 1,048,576 / 0 | 8.5 |
| DC_19_36 | DC | 2.730 | 4.470 | 0.687 | 0.862 | 0.866 | -0.674 | 50.9% | 0.00 / 30.91 / 6.31 / 8.65 | 6.99 / 8.00 | 1,048,576 / 0 | 8.8 |
| DC_22_21 | DC | 4.644 | 6.602 | 0.688 | 0.846 | 0.854 | -1.847 | 25.0% | 0.00 / 35.55 / 12.58 / 10.77 | 14.42 / 11.82 | 1,048,576 / 0 | 8.5 |
| DC_26_08 | DC | 3.104 | 4.522 | 0.708 | 0.881 | 0.867 | -2.096 | 43.3% | 0.00 / 30.67 / 6.52 / 7.95 | 8.61 / 8.36 | 1,048,576 / 0 | 8.8 |
| DC_30_13 | DC | 3.118 | 4.754 | 0.685 | 0.869 | 0.859 | -2.242 | 43.6% | 0.00 / 28.37 / 6.40 / 7.40 | 8.64 / 8.47 | 1,048,576 / 0 | 9.0 |
| DC_33_53 | DC | 1.854 | 3.116 | 0.545 | 0.828 | 0.786 | -1.708 | 69.9% | 0.00 / 24.14 / 1.67 / 3.54 | 3.37 / 4.62 | 1,048,576 / 0 | 8.8 |
| DC_36_57 | DC | 3.179 | 4.631 | 0.684 | 0.858 | 0.841 | -1.533 | 39.8% | 0.00 / 30.15 / 7.22 / 8.18 | 8.75 / 8.24 | 1,048,576 / 0 | 8.4 |
| DC_40_43 | DC | 2.300 | 3.567 | 0.470 | 0.826 | 0.788 | -2.187 | 65.1% | 0.00 / 20.28 / 1.86 / 3.50 | 4.05 / 4.90 | 1,048,576 / 0 | 8.4 |
| DC_44_21 | DC | 2.218 | 3.570 | 0.595 | 0.801 | 0.700 | -0.350 | 80.5% | 0.00 / 22.32 / 1.31 / 3.33 | 1.66 / 5.61 | 1,048,576 / 0 | 8.4 |
| DC_47_47 | DC | 3.292 | 7.007 | 0.035 | 0.485 | 0.554 | -3.116 | 80.4% | 0.00 / 28.87 / 1.48 / 4.13 | 4.60 / 7.13 | 1,048,576 / 0 | 8.5 |
| DC_54_40 | DC | 2.204 | 3.314 | 0.522 | 0.750 | 0.656 | -0.245 | 82.1% | 0.00 / 15.75 / 1.05 / 2.67 | 1.29 / 4.80 | 1,048,576 / 0 | 8.5 |
| DC_64_40 | DC | 2.430 | 3.771 | 0.577 | 0.848 | 0.802 | -2.188 | 62.4% | 0.00 / 22.09 / 2.73 / 4.79 | 4.92 / 5.80 | 1,048,576 / 0 | 8.6 |
| NYC_01826 | NYC | 3.990 | 5.504 | 0.377 | 0.670 | 0.667 | -0.787 | 25.0% | 0.00 / 21.86 / 7.99 / 6.38 | 8.78 / 6.97 | 1,048,576 / 0 | 8.2 |
| NYC_02942 | NYC | 5.551 | 7.627 | 0.027 | 0.409 | 0.394 | +2.077 | 3.2% | 0.00 / 29.01 / 16.78 / 5.18 | 14.70 / 7.73 | 1,048,576 / 0 | 8.9 |
| NYC_04059 | NYC | 2.129 | 3.608 | 0.429 | 0.726 | 0.642 | -1.297 | 70.1% | 0.00 / 22.77 / 1.98 / 4.20 | 3.28 / 4.77 | 1,048,576 / 0 | 8.4 |
| NYC_04939 | NYC | 1.103 | 2.329 | -0.097 | 0.411 | 0.234 | -1.084 | 97.2% | 0.00 / 9.62 / 0.07 / 0.54 | 1.15 / 2.22 | 1,048,576 / 0 | 8.5 |
| NYC_05703 | NYC | 0.130 | 0.694 | 0.585 | 0.776 | 0.171 | -0.075 | 98.8% | 0.00 / 15.90 / 0.09 / 0.96 | 0.17 / 1.08 | 1,048,576 / 0 | 8.4 |
| NYC_08668 | NYC | 0.838 | 1.732 | 0.560 | 0.795 | 0.621 | -0.700 | 81.3% | 0.00 / 16.61 / 0.86 / 2.04 | 1.56 / 2.61 | 1,048,576 / 0 | 8.6 |
| NYC_15287 | NYC | 3.877 | 4.998 | 0.168 | 0.805 | 0.816 | -3.637 | 43.4% | 0.00 / 16.95 / 3.05 / 3.32 | 6.69 / 5.48 | 1,048,576 / 0 | 8.6 |
| NYC_17337 | NYC | 2.419 | 3.568 | 0.605 | 0.863 | 0.817 | -2.126 | 55.9% | 0.00 / 21.96 / 3.16 / 4.86 | 5.28 / 5.68 | 1,048,576 / 0 | 8.8 |
| NYC_19359 | NYC | 2.915 | 4.136 | 0.209 | 0.756 | 0.721 | -2.757 | 66.4% | 0.00 / 18.58 / 1.44 / 3.02 | 4.20 / 4.65 | 1,048,576 / 0 | 8.6 |
| NYC_21098 | NYC | 5.151 | 7.023 | 0.419 | 0.787 | 0.791 | -4.111 | 33.0% | 0.00 / 26.35 / 7.39 / 6.98 | 11.50 / 9.21 | 1,048,576 / 0 | 8.3 |
| NYC_22037 | NYC | 2.974 | 5.290 | 0.623 | 0.803 | 0.722 | -0.840 | 50.9% | 0.00 / 25.78 / 6.40 / 7.85 | 7.24 / 8.62 | 1,048,576 / 0 | 8.7 |
| NYC_22623 | NYC | 2.407 | 3.682 | 0.027 | 0.679 | 0.672 | -2.384 | 78.6% | 0.00 / 17.05 / 0.74 / 1.93 | 3.13 / 3.73 | 1,048,576 / 0 | 8.3 |
| PHL_3504 | PHL | 2.238 | 3.328 | 0.356 | 0.829 | 0.754 | -2.183 | 70.2% | 0.00 / 11.72 / 1.33 / 2.47 | 3.52 / 4.15 | 1,048,576 / 0 | 8.4 |
| PHL_3651 | PHL | 2.467 | 3.688 | 0.148 | 0.766 | 0.745 | -2.429 | 66.9% | 0.00 / 11.28 / 1.19 / 2.01 | 3.61 / 4.00 | 1,048,576 / 0 | 8.6 |
| PHL_3787 | PHL | 0.580 | 1.316 | -0.202 | 0.224 | 0.094 | -0.580 | 99.7% | 0.00 / 4.10 / 0.00 / 0.10 | 0.58 / 1.20 | 1,048,576 / 0 | 8.2 |
| PHL_3934 | PHL | 0.925 | 1.971 | 0.459 | 0.788 | 0.604 | -0.914 | 84.5% | 0.00 / 11.12 / 0.57 / 1.54 | 1.48 / 2.68 | 1,048,576 / 0 | 8.4 |
| PHL_4078 | PHL | 2.090 | 3.369 | 0.409 | 0.778 | 0.719 | -1.902 | 69.1% | 0.00 / 17.11 / 1.53 / 3.03 | 3.43 / 4.38 | 1,048,576 / 0 | 8.4 |
| PHL_4219 | PHL | 2.002 | 3.659 | 0.733 | 0.888 | 0.816 | -1.570 | 62.8% | 0.00 / 32.97 / 3.55 / 5.73 | 5.12 / 7.08 | 1,048,576 / 0 | 8.3 |
| PHL_4367 | PHL | 1.242 | 2.727 | 0.429 | 0.839 | 0.584 | -1.219 | 85.9% | 0.00 / 9.31 / 0.58 / 1.58 | 1.80 / 3.61 | 1,048,576 / 0 | 8.2 |
| PHL_4582 | PHL | 1.749 | 3.204 | 0.779 | 0.915 | 0.745 | -1.638 | 72.7% | 0.00 / 27.09 / 2.51 / 6.03 | 4.15 / 6.82 | 1,048,576 / 0 | 8.3 |
| PHL_4852 | PHL | 2.665 | 4.491 | -0.169 | 0.406 | 0.442 | -2.400 | 76.9% | 0.00 / 12.03 / 0.69 / 1.71 | 3.09 / 4.15 | 1,048,576 / 0 | 8.6 |
| PHL_5012 | PHL | 1.529 | 2.932 | 0.676 | 0.875 | 0.593 | -1.465 | 82.7% | 0.00 / 24.87 / 1.25 / 4.00 | 2.72 / 5.15 | 1,048,576 / 0 | 9.1 |
| PHL_5175 | PHL | 1.156 | 2.267 | 0.155 | 0.647 | 0.501 | -1.154 | 89.9% | 0.00 / 7.26 / 0.30 / 1.07 | 1.45 / 2.47 | 1,048,576 / 0 | 9.7 |
| PHL_5344 | PHL | 2.935 | 6.542 | -0.079 | 0.541 | 0.347 | -2.931 | 94.2% | 0.00 / 7.50 / 0.18 / 0.93 | 3.11 / 6.30 | 1,048,576 / 0 | 8.7 |

### C5. Baseline comparison

The four views are kept separate:
1. **DA-V2 native:** unitless, so correlation only.
2. **DA-V2 affine:** in-sample; "inlier" is the existing baseline convention and favours DA-V2.
3. **TerraHeight-S native.**
4. **TerraHeight-S affine.**

The TerraHeight-S OLS slope is a separate diagnostic.

| Tile | 1. DA-V2 native: Pearson / Spearman | 2. DA-V2 affine a / b | 2. inlier MAE / R² | 2. all-pixel MAE / R² | 3. TH native MAE / R² | 4. TH affine a / b | 4. all-pixel MAE / R² | TH OLS a |
|---|---|---|---|---|---|---|---|---|
| DC_12_15 | +0.091 / +0.187 | 2.872 / 4.455 | 5.014 / 0.018 | 5.551 / -0.002 | 3.431 / 0.533 | 0.936 / 2.914 | 2.872 / 0.709 | 0.899 |
| DC_19_36 | -0.085 / -0.158 | -1.760 / 8.109 | 6.527 / 0.003 | 6.702 / 0.006 | 2.730 / 0.687 | 0.928 / 1.541 | 2.908 / 0.720 | 0.797 |
| DC_22_21 | -0.339 / -0.311 | -10.525 / 28.113 | 9.525 / 0.115 | 9.525 / 0.115 | 4.644 / 0.688 | 0.984 / 1.981 | 4.563 / 0.714 | 0.928 |
| DC_26_08 | +0.059 / +0.196 | 3.022 / 4.151 | 6.160 / 0.010 | 6.797 / -0.007 | 3.104 / 0.708 | 0.957 / 2.243 | 3.066 / 0.774 | 0.927 |
| DC_30_13 | +0.279 / +0.296 | 7.300 / 0.978 | 6.273 / 0.113 | 6.730 / 0.072 | 3.118 / 0.685 | 1.054 / 1.616 | 2.990 / 0.751 | 0.994 |
| DC_33_53 | +0.565 / +0.352 | 4.140 / -4.194 | 2.374 / 0.427 | 2.850 / 0.302 | 1.854 / 0.545 | 1.134 / 1.114 | 1.924 / 0.677 | 1.081 |
| DC_36_57 | +0.130 / +0.175 | 1.497 / 5.071 | 6.957 / 0.019 | 7.016 / 0.017 | 3.179 / 0.684 | 0.926 / 1.884 | 3.042 / 0.732 | 0.864 |
| DC_40_43 | +0.511 / +0.556 | 6.830 / -3.065 | 2.032 / 0.481 | 2.937 / 0.221 | 2.300 / 0.470 | 1.228 / 1.431 | 2.110 / 0.674 | 1.155 |
| DC_44_21 | +0.289 / +0.313 | 3.872 / -5.902 | 2.026 / 0.249 | 3.689 / -0.016 | 2.218 / 0.595 | 1.433 / -0.578 | 2.339 / 0.635 | 1.348 |
| DC_47_47 | +0.246 / +0.390 | 3.830 / -1.938 | 1.893 / 0.405 | 4.013 / -0.043 | 3.292 / 0.035 | 1.010 / 1.312 | 3.361 / 0.162 | 0.837 |
| DC_54_40 | +0.584 / +0.604 | 7.155 / -10.216 | 1.740 / 0.583 | 2.765 / 0.286 | 2.204 / 0.522 | 1.376 / -0.366 | 2.231 / 0.559 | 1.345 |
| DC_64_40 | +0.121 / +0.150 | 3.110 / 0.597 | 3.406 / 0.089 | 4.489 / -0.038 | 2.430 / 0.577 | 1.072 / 1.667 | 2.364 / 0.715 | 1.026 |
| NYC_01826 | -0.108 / -0.147 | -1.834 / 10.729 | 6.110 / 0.012 | 6.110 / 0.012 | 3.990 / 0.377 | 0.900 / 2.531 | 3.936 / 0.408 | 0.733 |
| NYC_02942 | +0.192 / +0.188 | 6.986 / 1.864 | 6.253 / 0.037 | 6.253 / 0.037 | 5.551 / 0.027 | 0.633 / 4.178 | 5.566 / 0.167 | 0.610 |
| NYC_04059 | +0.204 / +0.212 | 2.405 / -1.127 | 2.225 / 0.100 | 3.274 / -0.026 | 2.129 / 0.429 | 1.106 / 1.157 | 2.267 / 0.465 | 0.824 |
| NYC_04939 | +0.447 / +0.363 | 0.596 / -0.728 | 0.442 / 0.052 | 1.153 / -0.029 | 1.103 / -0.097 | 2.019 / 0.321 | 1.123 / 0.066 | 1.697 |
| NYC_05703 | +0.123 / +0.048 | 0.006 / 0.006 | 0.018 / 0.000 | 0.171 / -0.019 | 0.130 / 0.585 | 0.877 / 0.016 | 0.127 / 0.598 | 0.872 |
| NYC_08668 | +0.402 / +0.373 | 2.420 / -2.969 | 1.135 / 0.319 | 1.522 / 0.131 | 0.838 / 0.560 | 1.108 / 0.184 | 0.832 / 0.601 | 1.017 |
| NYC_15287 | +0.454 / +0.449 | 5.476 / -2.105 | 4.109 / 0.212 | 4.136 / 0.206 | 3.877 / 0.168 | 1.538 / 1.927 | 2.417 / 0.633 | 1.330 |
| NYC_17337 | +0.452 / +0.463 | 7.306 / -5.893 | 2.786 / 0.388 | 3.753 / 0.165 | 2.419 / 0.605 | 1.068 / 1.737 | 2.200 / 0.741 | 1.007 |
| NYC_19359 | +0.377 / +0.388 | 5.564 / -3.573 | 2.680 / 0.183 | 3.312 / 0.110 | 2.915 / 0.209 | 1.292 / 2.301 | 2.462 / 0.565 | 1.166 |
| NYC_21098 | -0.067 / -0.071 | -2.346 / 14.495 | 7.967 / 0.004 | 7.983 / 0.005 | 5.151 / 0.419 | 1.148 / 2.884 | 4.293 / 0.611 | 1.037 |
| NYC_22037 | +0.477 / +0.388 | 10.872 / -7.128 | 5.458 / 0.329 | 6.041 / 0.217 | 2.974 / 0.623 | 0.983 / 0.701 | 3.162 / 0.635 | 0.881 |
| NYC_22623 | +0.716 / +0.700 | 4.251 / -2.999 | 1.717 / 0.590 | 1.923 / 0.507 | 2.407 / 0.027 | 1.339 / 1.924 | 2.358 / 0.458 | 1.312 |
| PHL_3504 | +0.486 / +0.490 | 6.130 / -3.500 | 2.861 / 0.256 | 2.933 / 0.235 | 2.238 / 0.356 | 1.515 / 1.217 | 1.743 / 0.678 | 1.393 |
| PHL_3651 | +0.429 / +0.400 | 5.411 / -2.997 | 2.598 / 0.272 | 2.843 / 0.175 | 2.467 / 0.148 | 1.691 / 1.097 | 1.844 / 0.562 | 1.521 |
| PHL_3787 | +0.156 / +0.006 | 0.027 / 0.081 | 0.130 / -0.050 | 0.578 / -0.137 | 0.580 / -0.202 | 2.726 / 0.134 | 0.569 / -0.083 | 2.608 |
| PHL_3934 | +0.549 / +0.224 | 2.716 / -2.340 | 0.685 / 0.728 | 1.418 / 0.211 | 0.925 / 0.459 | 1.509 / 0.088 | 0.809 / 0.575 | 1.370 |
| PHL_4078 | +0.344 / +0.338 | 3.628 / -1.228 | 3.164 / 0.114 | 3.337 / 0.114 | 2.090 / 0.409 | 1.329 / 1.116 | 1.981 / 0.581 | 1.126 |
| PHL_4219 | +0.719 / +0.619 | 13.642 / -9.829 | 3.040 / 0.659 | 3.664 / 0.512 | 2.002 / 0.733 | 1.116 / 0.484 | 1.986 / 0.779 | 1.098 |
| PHL_4367 | +0.808 / +0.474 | 9.320 / -9.039 | 1.256 / 0.767 | 1.585 / 0.651 | 1.242 / 0.429 | 2.089 / 0.047 | 0.847 / 0.676 | 1.916 |
| PHL_4582 | +0.853 / +0.608 | 7.437 / -5.106 | 1.979 / 0.810 | 2.504 / 0.727 | 1.749 / 0.779 | 1.064 / 0.869 | 1.881 / 0.829 | 1.036 |
| PHL_4852 | +0.242 / +0.335 | 1.419 / -0.272 | 2.060 / 0.123 | 2.916 / 0.005 | 2.665 / -0.169 | 1.106 / 1.493 | 2.738 / 0.122 | 0.989 |
| PHL_5012 | +0.703 / +0.276 | 2.861 / -2.494 | 0.748 / 0.549 | 2.312 / 0.231 | 1.529 / 0.676 | 1.130 / 0.552 | 1.554 / 0.745 | 1.127 |
| PHL_5175 | +0.629 / +0.381 | 3.260 / -3.392 | 0.928 / 0.589 | 1.302 / 0.368 | 1.156 / 0.155 | 1.620 / 0.270 | 1.090 / 0.335 | 1.485 |
| PHL_5344 | +0.749 / +0.548 | 14.023 / -13.095 | 3.186 / 0.579 | 3.279 / 0.560 | 2.935 / -0.079 | 4.038 / 0.273 | 2.600 / 0.178 | 3.659 |

### C6. Cross-tile summary

| Metric (all 36 tiles) | n | Mean | Median | Std | Min | Max |
|---|---|---|---|---|---|---|
| mae | 36 | 2.488 | 2.413 | 1.208 | 0.130 | 5.551 |
| rmse | 36 | 3.995 | 3.671 | 1.618 | 0.694 | 7.627 |
| r2 | 36 | 0.399 | 0.465 | 0.284 | -0.202 | 0.779 |
| pearson | 36 | 0.741 | 0.798 | 0.166 | 0.224 | 0.915 |
| spearman | 36 | 0.656 | 0.720 | 0.201 | 0.094 | 0.867 |
| bias | 36 | -1.644 | -1.673 | 1.146 | -4.111 | 2.077 |
| zero_fraction | 36 | 0.652 | 0.695 | 0.229 | 0.032 | 0.997 |

**DC**

| Metric | n | Mean | Median | Std | Min | Max |
|---|---|---|---|---|---|---|
| mae | 12 | 2.875 | 2.917 | 0.756 | 1.854 | 4.644 |
| rmse | 12 | 4.510 | 4.496 | 1.223 | 3.116 | 7.007 |
| r2 | 12 | 0.561 | 0.586 | 0.184 | 0.035 | 0.708 |
| pearson | 12 | 0.808 | 0.845 | 0.108 | 0.485 | 0.881 |
| spearman | 12 | 0.783 | 0.811 | 0.098 | 0.554 | 0.867 |
| bias | 12 | -1.757 | -1.971 | 0.922 | -3.116 | -0.245 |
| zero_fraction | 12 | 0.575 | 0.566 | 0.186 | 0.250 | 0.821 |

**NYC**

| Metric | n | Mean | Median | Std | Min | Max |
|---|---|---|---|---|---|---|
| mae | 12 | 2.790 | 2.667 | 1.660 | 0.130 | 5.551 |
| rmse | 12 | 4.183 | 3.909 | 2.048 | 0.694 | 7.627 |
| r2 | 12 | 0.328 | 0.398 | 0.253 | -0.097 | 0.623 |
| pearson | 12 | 0.707 | 0.766 | 0.149 | 0.409 | 0.863 |
| spearman | 12 | 0.606 | 0.669 | 0.220 | 0.171 | 0.817 |
| bias | 12 | -1.477 | -1.191 | 1.672 | -4.111 | 2.077 |
| zero_fraction | 12 | 0.586 | 0.611 | 0.292 | 0.032 | 0.988 |

**PHL**

| Metric | n | Mean | Median | Std | Min | Max |
|---|---|---|---|---|---|---|
| mae | 12 | 1.798 | 1.876 | 0.730 | 0.580 | 2.935 |
| rmse | 12 | 3.291 | 3.266 | 1.330 | 1.316 | 6.542 |
| r2 | 12 | 0.308 | 0.382 | 0.340 | -0.202 | 0.779 |
| pearson | 12 | 0.708 | 0.783 | 0.215 | 0.224 | 0.915 |
| spearman | 12 | 0.579 | 0.599 | 0.208 | 0.094 | 0.816 |
| bias | 12 | -1.699 | -1.604 | 0.694 | -2.931 | -0.580 |
| zero_fraction | 12 | 0.796 | 0.798 | 0.117 | 0.628 | 0.997 |

**Descriptive counts:**

| Count | Value |
|---|---|
| Tiles with positive R² | 32/36 |
| R² above 0.5 | 17/36 |
| MAE under 3 m | 26/36 |
| MAE under 5 m | 34/36 |
| TerraHeight-S native MAE below DA-V2 affine **inlier** MAE (favours DA-V2) | 23/36 |
| TerraHeight-S native R² above DA-V2 affine **all-pixel** R² | 26/36 |
| **Like for like** (both affine, all pixels): TerraHeight-S lower MAE | 35/36 |
| **Like for like** (both affine, all pixels): TerraHeight-S higher R² | 33/36 |
| TerraHeight-S affine undefined / DA-V2 affine undefined | 0 / 0 |

**Per city:** DC: R² above 0.5 on 10/12, like-for-like MAE better on 12/12, NYC: R² above 0.5 on 4/12, like-for-like MAE better on 11/12, PHL: R² above 0.5 on 3/12, like-for-like MAE better on 12/12.

**OLS slope by city** (diagnostic):

| City | n | Median OLS a | Min | Max |
|---|---|---|---|---|
| DC | 12 | 0.961 | 0.797 | 1.348 |
| NYC | 12 | 1.012 | 0.610 | 1.697 |
| PHL | 12 | 1.382 | 0.989 | 3.659 |

**Whole-image sensitivity subset:**

| Tile (k = 0 per city) | Tiled MAE / R² | Whole MAE / R² | Mean abs(tiled − whole) (m) |
|---|---|---|---|
| DC_12_15 | 3.431 / 0.533 | 3.614 / 0.498 | 0.89 |
| NYC_01826 | 3.990 / 0.377 | 4.864 / 0.230 | 2.39 |
| PHL_3504 | 2.238 / 0.356 | 2.556 / 0.152 | 0.45 |

**Seam:** a boundary step more than 2× the median step on 34/36 tiles.

**Degenerate** (constant prediction) tiles: none. **Majority-water** tiles: none.

**Tile metric vs scene covariate** (Spearman, n = 36, descriptive, no causal claim):

| Tile metric | ref_mean | ref_max | ref_p99 | frac_tree | frac_building | frac_low_veg | frac_ground | frac_water | frac_road | frac_negative_ref |
|---|---|---|---|---|---|---|---|---|---|---|
| mae | 0.86 | 0.43 | 0.76 | 0.71 | 0.09 | -0.44 | -0.37 | -0.29 | 0.12 | -0.01 |
| r2 | 0.34 | 0.21 | 0.46 | 0.09 | 0.09 | -0.12 | 0.09 | -0.11 | 0.33 | -0.04 |
| bias | -0.35 | -0.26 | -0.31 | -0.03 | -0.54 | 0.14 | 0.06 | 0.32 | -0.32 | 0.34 |
| zero_fraction | -0.96 | -0.39 | -0.73 | -0.65 | -0.19 | 0.50 | 0.43 | 0.49 | -0.20 | 0.04 |

### C7. Stratified analysis (36 tiles)

**By class** (tiles with at least 500 pixels of the class):

| Class | Tiles | Median bias | Bias range | Negative-bias tiles | Median MAE | Median RMSE |
|---|---|---|---|---|---|---|
| 0-others | 18 | -0.82 | -5.24 to 4.04 | 15 of 18 | 1.32 | 2.27 |
| 1-ground | 26 | -0.08 | -1.75 to 0.16 | 22 of 26 | 0.24 | 1.08 |
| 2-low vegetation | 35 | -0.26 | -1.06 to 1.72 | 27 of 35 | 0.63 | 1.54 |
| 3-buildings | 29 | -3.96 | -6.64 to -0.97 | 29 of 29 | 4.14 | 4.82 |
| 4-water | 10 | -0.02 | -0.14 to 4.90 | 5 of 10 | 0.10 | 0.40 |
| 5-road | 35 | -0.27 | -14.61 to 0.09 | 33 of 35 | 0.35 | 1.05 |
| 6-tree | 35 | -3.22 | -5.55 to 2.12 | 33 of 35 | 4.31 | 5.30 |

**By reference height:**

| Height bin (m) | Tiles | Median bias | Bias range | Negative-bias tiles | Median MAE | Median RMSE |
|---|---|---|---|---|---|---|
| [-∞,0) | 15 | 0.48 | 0.03 to 13.71 | 0 of 15 | 0.48 | 2.18 |
| [0,1) | 36 | -0.02 | -0.14 to 13.27 | 19 of 36 | 0.15 | 0.67 |
| [1,5) | 36 | -2.29 | -3.60 to 10.08 | 33 of 36 | 2.76 | 3.04 |
| [5,10) | 36 | -4.45 | -6.75 to 6.80 | 35 of 36 | 4.90 | 5.40 |
| [10,20) | 35 | -4.59 | -15.92 to 1.26 | 34 of 35 | 5.00 | 6.19 |
| [20,40) | 25 | -5.14 | -21.69 to -1.00 | 25 of 25 | 5.20 | 7.16 |
| [40,∞) | 1 | -11.76 | -11.76 to -11.76 | 1 of 1 | 11.76 | 12.30 |

**Special groups:** gamus_floor_minus5: 3 tiles, 385,525 px, median MAE 5.00, median bias 5.00, class_boundary_pixels: 36 tiles, 2,913,532 px, median MAE 1.95, median bias -1.30, class_interior_pixels: 36 tiles, 34,835,204 px, median MAE 2.41, median bias -1.73.

Per-tile class and height tables are in `results.json` → `part_C.tiles[*].stratified_terraheight_native`.

## 5. Figures

Folder: scratchpad `p111d/`.

| Figure | File |
|---|---|
| 1. PHL zero-output analysis (RGB with zero overlay, reference vs prediction histograms, zero share within each class) | `fig1_phl_zero_output.png` |
| 2. Height-compression comparison (OLS vs P1-11C robust slope, by city) | `fig2_height_compression.png` |
| 3. Height-bin error (per-tile bias, city colour, median) | `fig3_height_bin_error.png` |
| 4. Class error | `fig4_class_error.png` |
| 5. Tiling vs whole-image (maps, difference, seam profile) | `fig5_tiling_vs_whole.png` |
| 6. Native vs 0.6 m (paired MAE and R², reference / native-input / 0.6 m-input maps) | `fig6_native_vs_0p6m.png` |
| 7. Multi-tile overview, all 36 Part C tiles | `fig7_multitile_overview.png` |
| 8. Representative maps (best / median / worst native MAE by fixed rule) | `fig8_representative_maps.png` |
| 9. Per-city distributions | `fig9_city_distributions.png` |

## 6. Part D — interpretation

Each answer is labelled with its evidence basis.

**1. Is the P1-11C scene dependence reproducible?**

**Yes** (VERIFIED on 36 new tiles).
- Median native R² by city: DC 0.59, NYC 0.40, PHL 0.38.
- R² above 0.5: DC 10/12, NYC 4/12, PHL 3/12.
- MAE rises with mean scene height (ρ = 0.86; P1-11C found 0.85).

**2. Is the PHL low-relief failure systematic?**

**Partly** (VERIFIED + INFERENCE).
- PHL has the highest exact-zero fraction (median 79.8%).
- It has the highest OLS slope (median 1.38, range 0.99–3.66) and a low median R².
- **But** PHL also has the lowest median MAE (1.88 m), and 3 PHL tiles exceed R² 0.5.
- Under OLS, compression is **frequent but not universal**; P1-11C's robust-fit slopes overstated it (Part A2).

**3. Is the exact-zero behaviour systematic?**

**Yes** (VERIFIED).
- Zero fraction vs mean reference height: ρ = -0.96 over 36 tiles, and −0.93 in Part A.
- Median zero share within class: ground 98.7%, road 98.8%, low vegetation 94.5%. These zeros are mostly **correct** AGL.
- The same shares are buildings 25.2% and trees 33.3%. These zeros are **missed objects**.

**4. Does tall-object underestimation persist?**

**Yes** (VERIFIED).
- Buildings: negative bias on 29/29 tiles (median -3.96 m).
- Trees: 33/35 tiles (median -3.22 m).
- 5–10 m bin: 35/36 tiles. 20–40 m bin: 25/25 tiles (median -5.14 m).

**5. Does the water failure persist?**

**Not as a general water error** (VERIFIED).
- Water-class pixels on 10 tiles have median MAE 0.10 m and are predicted 0.
- NYC_11587 (Part A5) was a constant-output domain failure plus a structure the class map labels as water; no majority-water tile occurred in Part C.
- **Related, systematic effect:** negative reference pixels, including the GAMUS −5 m floor (3 tiles, 385,525 px, median error 5.00 m), are always overpredicted. The ReLU output can't go below 0.

**6. Does ~0.6 m input materially change performance?**

**Yes, materially worse** (VERIFIED, controlled downsampling only). Median change on the same grid:
- MAE +1.72 m
- R² -0.43
- bias -2.57 m
- OLS slope 1.3–3.7

The ranking is largely kept (Pearson -0.08); the height scale is not.

**7. Does the tiling seam materially affect numerical accuracy?**

**It's numerically real but not material in aggregate** (VERIFIED).
- A boundary step appears on 34/36 Part C tiles.
- Tiled still beat whole-image on all 7 tested tiles (4 in A6, 3 in C).

**8. Does TerraHeight-S consistently outperform generic DA-V2?**

**For AGL, yes, in like-for-like terms** (VERIFIED).
- Affine vs affine, all pixels: MAE lower on 35/36, R² higher on 33/36.
- TerraHeight-S native against DA-V2's in-sample inlier fit is less one-sided: 23/36. That fit favours DA-V2 on low-relief tiles.
- DA-V2's raw output has no consistent relationship with AGL.

**9. Is TerraHeight-S stable enough to justify a separate production-design review?**

**Not yet** (INFERENCE).
- GAMUS-native quality depends on the scene (R² above 0.5 on 17/36).
- Tall objects are systematically too low.
- **Native transfer to ~0.6 m, the resolution relevant to the SIH evaluation (Cartosat-2S), fails** in the controlled test.
- Provenance gaps remain UNRESOLVED.

**10. Does any experiment here establish absolute terrain elevation?**

**NO.** TerraHeight-S predicts AGL/nDSM (≈ 0 on ground by design). Nothing here combines it with a DEM, and nothing validates a DSM.

## 7. Part E — integration gate

**Decision: D. TRANSFER TO ~0.6 m FAILS.**

The parts point different ways, so the decision is explained here rather than forced:

| Part | Conclusion |
|---|---|
| **A** (failure characterisation) | The failures are **systematic model behaviour**, not noise: zeros on low and small objects, tall-object underestimation, no negative outputs. They do not reveal a reproducibility problem. The PHL-compression claim is partly corrected. |
| **C** (36 tiles at native 0.33 m) | Taken alone, this would be **B, promising but scene-dependent**. TerraHeight-S clearly beats generic DA-V2 for AGL like for like (35/36 MAE), but it's reliable (R² above 0.5) on only about half the tiles and strongly city-dependent. |
| **B** (0.6 m transfer) | **Failure of native transfer.** Height scale collapses and MAE worsens on every non-degenerate tile. Median R² falls from 0.63 to 0.04, with 4 of 9 tiles negative. |

For TERRAIN-X, whose target imagery is 0.6 m, **B governs the gate**.

**No integration. No design review yet.** There is no reproducibility problem (not E). Accuracy at 0.33 m is not uniformly insufficient (not C). The evidence is not strong enough across scenes for A.

### Recommended P1-11E (research only, rule fixed before running)

1. **Resolution-matching test.** On the same controlled GAMUS setup, upsample the ~0.6 m RGB back to 0.33 m (deterministic bicubic or area-inverse, documented) before inference, and measure whether the native height scale recovers. This is the cheapest test of whether the 0.6 m failure is simply a pixel-scale mismatch.
2. **Real 0.6 m test, only if step 1 recovers scale.** Use real ~0.6 m imagery with an independent lidar nDSM, e.g. NAIP 0.6 m with USGS 3DEP-derived DSM − DTM over a US city, a region independent of GAMUS.
3. **Keep the DEM + AGL = DSM composition question separate.** It needs its own design: a bare-earth reference, co-registration, datums, and avoiding double-counting with surface-model DEMs such as Copernicus or SRTM. Nothing here validates it.
4. **Provenance.** Continue seeking the uploader's training and inference code and split lists.

## 8. Reproducibility and limitations

- **Scripts** (scratchpad `p111d/`): `common.py`, `part_a.py`, `part_b.py`, `part_c.py`, `part_c_analysis.py`, `fig_ab.py`. `results.json` holds every number in this report.
- **Environment:** CPU only (Ryzen 5 5625U, 6 threads). Peak process RAM: Part A 1968 MB, Part B 802 MB, Part C 2091 MB (two models loaded). Tiled inference about 8.5 s per tile.
- **Coverage:** 3 East-Coast US cities at 0.33 m. Findings don't generalise to other regions, sensors, seasons or real 0.6 m sensors without new evidence.
- **Unresolved items carried from P1-11B/C:** training code, exact splits, the author's metrics, the blending rule, and the checkpoint-to-metrics link.
