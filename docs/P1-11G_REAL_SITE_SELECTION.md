# P1-11G — Real ~0.6 m validation-site selection

**Decision: B. SITE POSSIBLE BUT NEEDS DATA VERIFICATION.** The selected candidate for P1-11H is **C1 East Boulder**.

**Scope:** metadata and small image chips only.
- **No** TerraHeight-S inference, **no** scoring, no production change, no integration, no commit.
- Nothing larger than a few MB was downloaded: lidar header and first-chunk range reads of about 9 MB per tile, and NAIP/3DEP chips under 1 MB each.

**Evidence:** scratchpad `p111g/` — `site_candidates.json`, `candidates_raw.json`, `crop_characterisation.json`, `lpc_probe*.json`, `fig_candidate_chips.png`.

**Labels:** VERIFIED (measured here from official services or files), AUTHOR CLAIM (dataset documentation), INFERENCE, UNRESOLVED.

**Selection basis:** the site was chosen **only on data quality and registration suitability**, never on expected model performance. No model output exists for any candidate.

## 1. Candidate sites

All candidates use the **same lidar project**, USGS 3DEP LPC **CO_DRCOG_2020_B20**. Its format, CRS, datum and ground/DEM consistency were already verified in P1-11F, so the comparison is controlled. Each crop is 1.2 × 1.2 km in EPSG:26913 and was placed to sit inside a single NAIP tile.

| ID | Place | Crop (EPSG:26913 L, B, R, T) | Centre (lon, lat) |
|---|---|---|---|
| C1 | East Boulder (28th St commercial/residential) | 477400, 4429400, 478600, 4430600 | −105.25781, 40.01992 |
| C2 | Denver Central Park (former Stapleton) | 509000, 4400500, 510200, 4401700 | −104.88793, 39.75976 |
| C3 | Aurora / Havana St corridor | 511000, 4389000, 512200, 4390200 | −104.86478, 39.65612 |
| Reference | P1-11F Boulder foothills (rejected, Gate D) | 474720, 4423422, 475920, 4424622 | −105.28899, 39.96599 |

## 2. NAIP provenance

VERIFIED from the USGS NAIP Plus ImageServer catalog.

| | C1 | C2 | C3 |
|---|---|---|---|
| NAIP tile | m_4010562_se_13_060_20190919 | m_3910409_se_13_060_20190803 | m_3910418_sw_13_060_20190803 |
| Acquired | 2019-09-19 | 2019-08-03 | 2019-08-03 |
| Resolution / bands | 0.6 m / 4 (CNIR) | 0.6 m / 4 | 0.6 m / 4 |
| Projection | UTM 13N, NAD83 | UTM 13N, NAD83 | UTM 13N, NAD83 |
| Crop inside one NAIP tile | **yes** (277 m from the tile's east edge) | yes | yes |

**Note:** C1's original centre straddled two NAIP tiles (overlap strip x 478457–478877). The crop was placed inside `m_4010562_se` to avoid a mosaic seam.

## 3. Lidar provenance

**Common to all candidates:** USGS 3DEP LPC, project CO_DRCOG_2020_B20 (CO_DRCOG_1_2020), official LAZ tiles from `rockyweb.usgs.gov/.../LPC/Projects/CO_DRCOG_2020_B20/...`. LAS 1.4, point format 6.

**Per candidate, probed with range reads:** header, the first 100k points, and samples at 25%, 50% and 75% through each file.

| | C1 (all 4 tiles) | C2 (1 tile probed) | C3 (1 tile probed) |
|---|---|---|---|
| Tiles covering the crop | w0477n4429, w0477n4430, w0478n4429, w0478n4430 | w0508n4399, w0508n4401, w0510n4399, w0510n4401 | w0510n4389, w0510n4390, w0511n4389, w0511n4390 |
| Classes present | 1, 2, 7, 18 (unclassified, ground, noise) | same | same |

## 4. Acquisition dates

From point GPS times (VERIFIED on the probed chunks):
- **C1:** all four tiles **2020-05-29**.
- **C2:** 2020-05-27.
- **C3:** 2020-05-27 and 2020-05-29.
- **NAIP:** as in §2.

## 5. CRS

VERIFIED; carried over from P1-11F.
- **Lidar:** NAD83(2011) / UTM 13N + NAVD88 (Geoid18), metres.
- **NAIP and 3DEP DEM:** EPSG:26913 (NAD83 / UTM 13N); DEM heights in NAVD88 metres.

In P1-11F the labelling difference had **no measurable effect**: lidar ground vs DEM had 0.083 m MAE and zero horizontal shift.

## 6. Resolution

- **NAIP:** 0.6 m.
- **Lidar reference grid:** 0.6 m or 1 m, not 0.33 m (§11).

## 7. Point density

VERIFIED from headers. First-return share estimated from the first 100k points of each tile.

| | Points/m² (all returns) | Est. first returns/m² | First returns per 0.6 m cell | Per 1 m cell |
|---|---|---|---|---|
| **C1 w0477n4429** | 21.7 | 15.3 | about 5.5 | about 15 |
| **C1 w0477n4430** | 22.6 | 12.3 | about 4.4 | about 12 |
| **C1 w0478n4429** | 17.7 | 16.3 | about 5.9 | about 16 |
| **C1 w0478n4430** | 16.0 | 13.8 | about 5.0 | about 14 |
| C2 w0508n4399 | 4.7 | 3.6 | about 1.3 | about 3.6 |
| C3 w0510n4389 | 5.1 | 3.2 | about 1.2 | about 3.2 |
| P1-11F reference | 33.9 | 13.8 | about 5.0 | about 14 |

**INFERENCE:** only C1 supports a 0.6 m reference with a minimum-count rule. C2 and C3 would need a 1 m or coarser reference.

## 8. Terrain characteristics

VERIFIED from USGS 3DEP elevation service exports at 5 m. Used for characterisation only.

| | Relief | Median slope | p90 slope | Area > 10° |
|---|---|---|---|---|
| **C1** | 20 m | 1.3° | 3.5° | 1% |
| C2 | 16 m | 1.5° | 4.0° | 2% |
| C3 | 27 m | 1.8° | 6.3° | 3% |
| P1-11F reference | 680 m | 32.3° | 46.8° | 96% |

## 9. Vegetation characteristics

NAIP 4-band NDVI at 3 m (VERIFIED).

| | Vegetated (NDVI > 0.3) | Built or bare (NDVI < 0.1) |
|---|---|---|
| **C1** | 14% | 79% |
| C2 | 17% | 68% |
| C3 | 37% | 51% |
| P1-11F reference | 71% | 12% |

**Seasonal state:** the lidar (late May) and NAIP (August/September) are **both leaf-on**. No leaf-off pair exists for this project and NAIP year (INFERENCE from the dates). The preference for leaf-off data is therefore **not met**. Leaf-on on both sides at least keeps vegetation state comparable.

## 10. Likely registration features

VERIFIED visually on 0.6 m NAIP chips, `fig_candidate_chips.png`.

- **C1:** a major arterial intersection with lane lines, crosswalks and stop bars. Several large parking lots with stall paint. Continuous curbs and sidewalks. 1–3 storey commercial buildings (little lean). Residential trees are mostly in the north-west part of the crop.
- **C2:** dense new street grid, curbs, park paths; homes and rowhouses of about 2 storeys.
- **C3:** golf course, creek and open space dominate; fewer hard ground features.

## 11. Reference feasibility — plan for P1-11H

Fixed now, before download.

| Item | Rule |
|---|---|
| Surface | **Highest first return** (`return_number == 1`, classes 7/18 and withheld excluded) per **0.6 m cell** on the NAIP grid, computed directly from points, no interpolation. Secondary 1 m grid on the DEM grid. |
| Ground | 3DEP 1 m bare-earth DEM (CO_DRCOG_2020_B20, same lidar project), bilinear at cell centres. Re-verified against class-2 points as in P1-11F (a)/(b); must again show a median within ±0.05 m and zero best horizontal shift. |
| AGL reference | surface − ground. **Not clamped.** Negatives kept and reported. |
| Minimum points per cell | **≥ 3 first returns** per 0.6 m cell; ≥ 5 per 1 m cell. Fewer → NoData. |
| Valid-cell rule | minimum count met, DEM valid, and ≥ 20 m inside the crop border. The mask **never depends on predictions**. |
| Vertical datum / CRS | NAVD88 (Geoid18) m; UTM 13N. AGL is a difference, so the datum cancels. |
| Temporal change screening | Before scoring, flag 50 m blocks where lidar shows a structure > 3 m with no corresponding NAIP roof, or the reverse (construction or demolition between 2019-09 and 2020-05). Review visually with a documented rule; exclude flagged blocks from primary metrics and report them separately. |

## 12. Temporal mismatch

| | C1 | C2 | C3 |
|---|---|---|---|
| NAIP → lidar | 2019-09-19 → 2020-05-29 (**253 days**) | 2019-08-03 → 2020-05-27 (298 days) | about 298 days |

Urban change within about 8 months is possible (construction, tree removal), hence the screening step in §11.

## 13. Expected download size (C1)

- **Lidar:** 535.3 MB (w0477n4429 149.6 MB, w0477n4430 158.5 MB, w0478n4429 120.4 MB, w0478n4430 106.8 MB).
- **DEM:** a few MB, windowed `/vsicurl` reads from x47y443 and x47y444.
- **NAIP:** about 16 MB (4-band 2000 × 2000 export locked to OID 32834).

## 14. Objective site-selection rationale

C1 is the only candidate that meets every **data-quality** requirement:
- flat (median slope 1.3°) and mostly built (79%)
- abundant ground-level paint, curb and sidewalk features
- enough density for a **0.6 m** reference (about 4–6 first returns per cell) in all 4 tiles
- a single NAIP tile
- the shortest temporal gap (253 days)
- the same verified lidar project and DEM as P1-11F

C2 and C3 fail the density requirement for 0.6 m, and C3 also has fewer hard features.

## Registration plan for P1-11H

The test design and acceptance rule are **fixed before any C1 data is seen**.

**Features:** ground-level only.
- The mask keeps cells with a lidar class-2 top return **or** reference AGL < 0.3 m, **and** NAIP NDVI < 0.2.
- This excludes trees, roofs and building lean.

**Signal pair 1 — paint:** NAIP panchromatic brightness vs lidar first-return **intensity** (mean per 0.6 m cell), on the ground-level mask.

**Signal pair 2 — curbs and edges:** NAIP gradient magnitude vs gradient magnitude of the lidar ground elevation (curb steps, about 0.15 m) and of the intensity edges, on the same mask.

**Method:**
- Normalised cross-correlation, shift search ±10 px (±6 m), parabolic sub-pixel peak.
- Computed on the whole crop and **independently on the 4 quadrants** (600 × 600 m).

**Acceptance rule (all must hold):**
1. **Defined peak:** whole-crop pair-1 peak strictly inside the search window, r_peak ≥ 0.30, and r_peak − median(r at ≥ 5 px from the peak) ≥ 0.10.
2. **Spatial consistency:** each quadrant's pair-1 offset within **1.0 m** of the whole-crop offset in x and in y.
3. **Signal consistency:** the pair-2 offset within **1.0 m** of the pair-1 offset.
4. **Magnitude:**
   - |offset| ≤ 0.6 m (1 NAIP px): **aligned as is**.
   - 0.6 m < |offset| ≤ 3.0 m: accepted **only as a single global translation** of the reference, estimated from pair 1 and fixed before any model inference.
   - Otherwise: **Gate D, stop.**

**Expected positional tolerance:**
- **Ground features:** after acceptance, about 0.6 m (one NAIP pixel).
- **Elevated objects:** additionally displaced by orthophoto relief (lean ≈ height × tan(off-nadir angle)). For example, about 0.9 m for a 10 m building at 5° off-nadir and about 2.7 m at 15°.
- **So 0.33 m or 0.6 m pixel-level scoring is not defensible for elevated objects.** P1-11H primary scoring should use **5 m and 10 m blocks**, fixed in advance, with 0.6 m pixel metrics reported only as secondary.
- **Formal accuracy specifications:** the NAIP and lidar horizontal-accuracy specifications were not retrieved here (**UNRESOLVED**). The empirical test above decides.

## What remains UNRESOLVED (why this is B, not A)

- **Lidar intensity usefulness:** whether intensity shows road paint clearly enough for pair 1 is unknown until points over paved areas are read. The range probe samples flight-line chunks, not locations.
- **Density:** full-tile first-return density and per-cell counts over the crop (probes sampled 100k points per tile).
- **The registration test itself:** it has not been run, so NAIP-to-lidar alignment at C1 is **not yet demonstrated**.
- **Temporal change:** extent of construction or change between 2019-09 and 2020-05 within the crop.
- **Accuracy specifications:** formal NAIP 2019 and CO_DRCOG_2020 horizontal accuracy.

## Decision

**B. SITE POSSIBLE BUT NEEDS DATA VERIFICATION.** C1 East Boulder is selected for P1-11H.

**P1-11H must proceed in this order:**
1. Download the C1 lidar (535 MB), the NAIP crop and the DEM windows.
2. Re-verify the ground/DEM consistency.
3. Run the pre-registered registration test above.
4. **Only if it passes** (acceptance rules 1–4), run TerraHeight-S with the P1-11E protocol and score on 5 m and 10 m blocks as primary.

If registration fails, stop at Gate D again. No TerraHeight-S claim is made here.
