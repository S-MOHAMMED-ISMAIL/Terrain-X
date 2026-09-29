# P1-11F — Real NAIP (~0.6 m) + independent lidar AGL validation of TerraHeight-S

**Result: GATE D — DATA/REFERENCE INVALID for pixel-level scoring at this site.**

The experiment was **stopped before TerraHeight-S inference**, as the P1-11F stop conditions require:
- TerraHeight-S was **not run**.
- **No accuracy number exists.**

**Scope:** research only. No production code, registry, calibration, DSM/DTM/nDSM pipeline, database, migration, API or frontend was changed. No integration, no commit. P1-11B–E results are untouched.

**Workspace:** scratchpad folder `p111f/`. All P1-11F files are preserved, including the prepared but unrun `experiment.py` and `figures.py`.

**Labels:**
- **VERIFIED:** measured by TERRAIN-X from the released data.
- **INFERENCE:** our interpretation.
- **AUTHOR CLAIM:** dataset or model documentation.
- **UNRESOLVED:** not establishable from the available evidence.

---

## 1. Data provenance

### NAIP source image

`TERRAIN-X-TEST-DATA/02_calibrated/T1_NAIP_Boulder_RGB_2019.tif` (from T1)

| Item | Value |
|---|---|
| Size / type | 3 × 2000 × 2000, uint8 |
| CRS | EPSG:26913 (NAD83 / UTM 13N) |
| Pixel size | 0.6 m |
| Transform | (0.6, 0, 474720, 0, −0.6, 4424622), no rotation |
| NoData | none |
| Acquired | **2019-09-19** (USGS NAIP Plus catalog, recorded in T1) |
| Hashes | SHA-256 in `results.json` |

All VERIFIED.

### Bare-earth DEM

`T1_3DEP_Boulder_1m.tif`: USGS 3DEP 1 m, project CO_DRCOG_2020_B20, bare earth.

| Item | Value |
|---|---|
| Size / type | 1220 × 1220, float32 |
| CRS | EPSG:26913 |
| Pixel size | 1 m |
| NoData | −999999; 0 NoData cells |
| Vertical | NAVD88, metres (official USGS tile XML) |

VERIFIED as recorded in T1.

### Lidar surface source

**USGS 3DEP Lidar Point Cloud, project CO_DRCOG_2020_B20 (CO_DRCOG_1_2020)** — the same lidar project as the DEM.

**Tiles:** four official LAZ files (`w0474n4423`, `w0474n4424`, `w0475n4423`, `w0475n4424`) from `rockyweb.usgs.gov/vdelivery/Datasets/Staged/Elevation/LPC/Projects/CO_DRCOG_2020_B20/CO_DRCOG_1_2020/LAZ/`.

**Download and integrity (VERIFIED):**
- 1,058,774,736 bytes in total.
- **Every tile's byte size equals the size USGS publishes in the TNM Access API.**
- SHA-256 hashes are in `results.json`.

**Header contents (VERIFIED):**
- LAS 1.4, point format 6.
- 143,202,074 points in total; 57,515,566 inside the crop plus a 10 m buffer.
- Scale 0.001 m. System "Leica TerrainMapper".

**CRS (VERIFIED from the LAZ WKT VLR):** NAD83(2011) / UTM zone 13N + NAVD88 height – Geoid18, metres.

**Acquisition (VERIFIED from point GPS times):** **2020-05-29, 15:27–15:55 UTC.**

**Classes present in the crop (VERIFIED):**

| Class | Points |
|---|---|
| 1 unclassified | 49,227,590 |
| 2 ground | 8,094,648 |
| 7 low noise | 129,536 |
| 18 high noise | 63,792 |

There are **no vegetation or building classes**, so no independent semantic reference exists beyond ground vs non-ground. No labels were invented.

**Reader (tooling note):**
- Reading LAZ required `laspy` 2.7.0 and `lazrs` 0.8.2.
- They were installed only in an isolated venv, `scratchpad/p111f/.venv_lidar` (`venv_lidar_freeze.txt`: laspy, lazrs, numpy).
- No project or production environment was touched.

## 2. Reference construction

Prepared, and characterised model-free only.

**Planned reference:** LiDAR_AGL = LiDAR surface − bare-earth DEM.

**Surface** (VERIFIED as implemented in `rasterize_lidar.py`):
- The **highest first return per cell**: `return_number == 1`, classes 7 and 18 and withheld points excluded.
- Computed directly from points, with **no interpolation**. Empty cells are NoData.

**Grids:**
- **Prediction/scoring grid:** 0.330033 m (3636 × 3636, NAIP origin).
- **NAIP grid:** 0.6 m.
- **DEM grid:** 1 m.

**Point density (VERIFIED):**

| Grid | Cells with at least one first return | Median first returns per cell |
|---|---|---|
| 0.33 m | 84.4% | 2 |
| 0.6 m | 97.9% | 6 |
| 1 m | 99.5% | 16 |

**Bare ground:** the 3DEP DEM, sampled bilinearly at cell centres.

**Reference characterisation** (model-free, **not used for any scoring**):

| Grid | Valid | Mean / median / std (m) | Min / max (m) | < 0 m | > 2 m | > 5 m | > 10 m | > 20 m |
|---|---|---|---|---|---|---|---|---|
| 0.33 m | 84.4% | 6.62 / 6.05 / 5.97 | −37.9 / 63.0 | **6.2%** | 64.9% | 53.8% | 32.4% | 1.1% |
| 0.6 m | 97.9% | 7.04 / 6.67 / 6.03 | −19.7 / 63.0 | 1.4% | 67.6% | 56.1% | 34.9% | 1.4% |

Negative values were **not clamped**. The rule fixed before scoring would have kept them unmodified.

## 3. Alignment validation

Performed **before** any inference; model-free.

### (a) Lidar ground points vs the 3DEP DEM

Mean elevation of class-2 points per 1 m DEM cell, cells with at least 3 points; 77.8% of cells qualify.

- Median difference **+0.002 m**, mean +0.006 m, **MAE 0.083 m**.
- p95 |difference| 0.25 m, p99 0.65 m; 87.5% within 0.15 m.
- **VERIFIED:** the DEM and the lidar point cloud are vertically consistent.

### (b) Horizontal shift search for (a), ±3 m

- **Sharp minimum at (0, 0) m:** MAE 0.083 m.
- Every 1 m shift gives 0.42–0.64 m.
- **VERIFIED:** the DEM and the lidar are horizontally co-registered. The NAD83 vs NAD83(2011) labelling difference has no measurable effect.

### (c) Planned check: NAIP vs lidar

Normalised cross-correlation over a ±5 px (±3 m) shift search:

| Pair | Best shift (px) | r best | r at zero shift | r far from best |
|---|---|---|---|---|
| NAIP brightness vs lidar first-return intensity | (−5, −3), **on the search edge** | 0.176 | 0.156 | 0.139 |
| NAIP gradient vs intensity gradient | (−1, −1) | 0.014 | 0.011 | −0.005 |
| NAIP gradient vs lidar DSM gradient | (1, −2) | −0.019 | −0.025 | −0.030 |

**VERIFIED:** no defined correlation peak; the check is **inconclusive**.

**INFERENCE:** 1064 nm lidar intensity is only weakly related to visible brightness over forest and rock.

### (c′) Supplementary diagnostic

Added after (c) was inconclusive; model-free (`align_check_canopy.py`). NAIP canopy proxies vs lidar canopy height, ±10 px (±6 m) search, with sub-pixel peak:

| NAIP proxy (grid) | Best offset (x, y) | r best | r at zero shift | r ≥ 5 px from best |
|---|---|---|---|---|
| darkness (0.6 m) | (−2.55, −1.84) m | 0.372 | 0.341 | 0.295 |
| darkness (3 m blocks) | (−3.27, −2.20) m | 0.418 | 0.394 | 0.284 |
| excess green (0.6 m) | **(+3.36, +0.58) m** | 0.358 | 0.242 | 0.158 |
| excess green (3 m blocks) | **(+3.92, +0.74) m** | 0.461 | 0.333 | 0.170 |

**VERIFIED:** both proxies correlate moderately with lidar canopy height. Their best offsets are 2.5–4 m from zero and **point in opposite east–west directions**, a disagreement of about 7 m.

**INFERENCE:** consistent with:
- orthophoto relief displacement ("lean") of 10–25 m conifers on very steep terrain (median slope about 32°, from T1)
- the darkness proxy capturing cast shadows on one side of each crown, while the excess-green proxy captures sunlit crowns on the other

**Visual check** (`figB_canopy_overlay.png`; windows chosen by rule: centre and two diagonal quadrant centres):
- In dense forest, lidar canopy outlines can't be matched crown-to-crown with NAIP.
- **Lidar 5 m contours also trace rock-fin and cliff edges.** On near-vertical sandstone, "surface − smoothed bare earth" is positive, so this reference records rock relief as above-ground height as well as vegetation.

### Alignment decision

- **The reference is internally consistent:** lidar surface vs DEM (VERIFIED).
- **NAIP-to-reference co-registration at the scoring scale is NOT demonstrated:**
  - the planned check found no peak
  - the supplementary proxies disagree by about 7 m, against a 0.33 m scoring grid
- Per the P1-11F stop condition ("If reference alignment cannot be demonstrated: STOP. Do not produce a misleading accuracy number"), **the experiment stops here.**

## 4. TerraHeight-S inference configuration

Prepared, **not executed**. `experiment.py` would use:
- **the P1-11E configuration unchanged:** checkpoint SHA-256 `739ed4e9…22f8a`, strict load, RGB ÷ 255 + ImageNet normalisation, `relu × 8.492877943662961`, 630 px / 25% overlap / uniform-average tiling
- **the upsampled input:** NAIP 2000 px → PIL bilinear → 3636 px (0.330033 m), transform (0.330033, 0, 474720, 0, −0.330033, 4424622), same CRS
- **scoring:** against the prediction-independent lidar mask

## 5–7. Primary metrics, height-bin metrics, spatial error analysis

**Not produced.** TerraHeight-S was not run, and no accuracy, bias, correlation, height-bin or class number exists for this site.

The P1-11D/E failure-mode analyses (seams, zeros, tall-object bias, negatives) were **not** repeated on real data. **UNRESOLVED.**

**No DSM composition was attempted.** For the record: with the same bare-earth DEM on both sides, "DEM + predicted AGL vs lidar DSM" is algebraically identical to the AGL error, so it would add nothing without an independent ground source.

## 8. Temporal mismatch

- **Gap: 253 days.** NAIP 2019-09-19 vs lidar 2020-05-29 (VERIFIED).
- **Season:** both leaf-on for this site (September vs late May).
- **Vegetation:** predominantly conifer forest with exposed sandstone, so large seasonal change is not expected (INFERENCE).
- **Disturbance:** growth, fall or disturbance between the dates can't be checked from the data (UNRESOLVED).

This mismatch is **not** the reason for stopping; co-registration is.

## 9. Limitations and what is needed to proceed

**Why this site is unsuitable for pixel-level AGL validation (INFERENCE from the VERIFIED evidence):**
- Steep, forested terrain with tall trees maximises orthophoto relief displacement and shadowing.
- The only ground-level features are rock, scree and trails. The canopy itself can't serve as a registration target, because it is the object whose apparent position is displaced.
- At 0.33 m the lidar reference has few points per cell (median 2). On cliffs this gives 6.2% negative cells and rock-relief "AGL", which isn't object height.

**What a scientifically defensible P1-11F rerun needs** (recommendation; the rule should be fixed in advance):
1. **A flatter site with man-made ground-level features**, e.g. streets, parking lots, road paint and curbs, still inside the CO_DRCOG_2020_B20 lidar and the NAIP 2019 coverage (e.g. residential or commercial Boulder or Denver).
   - These allow a registration check on ground-level targets using lidar intensity (road paint and asphalt contrast) or lidar ground-edge features.
   - The check must be accepted before scoring.
2. **An acceptance criterion defined in advance.** For example: a consistent offset estimate across at least two independent ground-level proxies, with magnitude below one NAIP pixel. Or an estimated offset applied as a documented correction, with residual below one pixel.
3. **Pre-registered handling of relief displacement for elevated objects.**
   - It is inherent to NAIP (not a true orthophoto).
   - Options: score at coarser blocks, e.g. 5–10 m, with the block size fixed in advance, or apply an object-matching tolerance.
   - Report pixel-level results only as secondary.
4. **An AGL reference definition that excludes rock/cliff artefacts,** or a site without them.
   - Consider rasterising at 0.6 m or 1 m, where point counts are adequate, as the primary reference grid.
5. **Classification:** CO_DRCOG_2020_B20 classifies only ground and noise, so class-level analysis needs an independent classification source or must be omitted.

## 10. Final gate

**D. DATA/REFERENCE INVALID** (for pixel-level AGL scoring at this Boulder foothills crop).

**What holds (VERIFIED):**
- An official, independent lidar surface was obtained, and it is internally consistent with the bare-earth DEM (0.08 m MAE, zero horizontal shift).
- NAIP-to-lidar co-registration could not be demonstrated at the scoring scale: planned check inconclusive; supplementary proxies disagree by about 7 m.
- The reference also contains cliff/rock artefacts.

**What follows:**
- TerraHeight-S was not scored, **no accuracy claim is made**, and the P1-11E expectation (upsampling recovers scale on real 0.6 m imagery) remains **UNRESOLVED**.
- The model predicts AGL/nDSM only; nothing here concerns terrain elevation.
- No integration.

### Files

Scratchpad `p111f/`:

| File | Contents |
|---|---|
| `results.json` | Provenance, hashes, lidar metadata, reference characterisation, alignment evidence, stop decision |
| `lidar_rasters.npz`, `lidar_meta.json` | Point-cloud rasters and metadata |
| `alignment.json`, `alignment_canopy.json` | Alignment evidence |
| `figA_alignment_evidence.png`, `figB_canopy_overlay.png` | Alignment figures |
| `rasterize_lidar.py`, `align_check.py`, `align_check_canopy.py`, `finalize_gate_d.py` | Scripts that were run |
| `experiment.py`, `figures.py` | Prepared, **not run** |
| `lpc/` | The four official LAZ tiles |
| `.venv_lidar/` | Isolated reader environment |
