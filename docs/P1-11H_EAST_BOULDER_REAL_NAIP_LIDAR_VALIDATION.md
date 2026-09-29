# P1-11H — East Boulder real NAIP + lidar validation

**Result: GATE D — DATA/REFERENCE INVALID under the fixed, pre-registered registration rule.**

The experiment stopped after Phase 2, as P1-11H requires ("If registration fails: STOP immediately. Do not run TerraHeight-S."):
- **TerraHeight-S was not run.**
- **The AGL reference was not constructed.**
- **No accuracy number exists.**

The single failed criterion is the **absolute correlation floor** (r_peak 0.211 < 0.30). All other registration evidence indicates alignment within about 0.2 m (§4). Deciding whether to amend a pre-registered rule is left to you (§8).

**Scope:** research only. No production code, registry, calibration, DSM/DTM/nDSM pipeline, database, migration, API or frontend change. No integration, no commit. P1-11B–G results untouched.

**Workspace:** scratchpad `p111h/` — `results.json`, `phase1.json`, `registration.json`, `lidar_meta.json`, `figures/`, and the scripts. `phase3_6.py` is prepared but **not run**.

**Labels:** VERIFIED (measured here), AUTHOR CLAIM (dataset documentation), INFERENCE, UNRESOLVED.

## 1. Fixed site

C1 East Boulder, exactly as selected in P1-11G. Crop in EPSG:26913: **477400, 4429400, 478600, 4430600** (1.2 × 1.2 km). The site was not changed.

## 2. Phase 1 — data verification

| Check | Result |
|---|---|
| Lidar files | 4 official USGS 3DEP LPC tiles, CO_DRCOG_2020_B20 (w0477n4429, w0477n4430, w0478n4429, w0478n4430). **Byte sizes equal the USGS-published sizes** (149,569,223 / 158,537,992 / 120,409,458 / 106,804,408). SHA-256 in `results.json`. VERIFIED |
| Lidar headers | LAS 1.4, point format 6; 21,690,262 / 22,563,503 / 17,736,118 / 16,002,114 points; 24,140,796 inside the crop plus a 15 m buffer. VERIFIED |
| Lidar CRS / units / datum | NAD83(2011) / UTM 13N + NAVD88 (Geoid18), metres (LAZ WKT). VERIFIED |
| Lidar acquisition | **2020-05-29, 16:09–16:48 UTC**, all tiles (point GPS times). VERIFIED |
| Lidar classes in crop | 1 unclassified 16.92M, 2 ground 7.16M, 7 noise 38k, 18 noise 2k, 17 bridge deck 15k, 20 ignored ground 66. **No vegetation or building classes.** VERIFIED |
| NAIP | USGS NAIP Plus export locked to `m_4010562_se_13_060_20190919` (OID 32834): 4 bands (R, G, B, NIR) × 2000 × 2000 uint8, EPSG:26913 (metres), 0.6 m, transform (0.6, 0, 477400, 0, −0.6, 4430600), no NoData, 0 all-black pixels. Acquired **2019-09-19** (catalog). VERIFIED |
| DEM | 3DEP 1 m CO_DRCOG_2020_B20, windowed merge of x47y443 + x47y444 with no resampling (grid within 0.00002 px of the source): 1220 × 1220 (10 m buffer), EPSG:26913, metres, **0 NoData cells**, elevation 1599.3–1621.1 m. Vertical datum NAVD88 m (AUTHOR CLAIM: USGS tile XML). VERIFIED except datum |
| Overlap | NAIP fully inside the lidar tile union (477000–479000 × 4429000–4431000) and the DEM. VERIFIED |
| Temporal difference | **253 days**, 2019-09-19 → 2020-05-29. Both leaf-on. VERIFIED |
| CRS / datum mismatch | NAD83 vs NAD83(2011) labelling; empirically none (next row). Lidar and DEM are both NAVD88. VERIFIED |
| **DEM vs lidar ground points** | 1,017,661 cells at 1 m with ≥ 3 ground points (68.4%): median +0.0005 m, mean +0.0002 m, **MAE 0.0085 m**, p95 0.029 m, 99.75% within 0.15 m. **Best horizontal shift (0, 0)** in a ±3 m search. P1-11G requirement met. VERIFIED |

### Lidar density per 0.6 m cell

Interior only (≥ 20 m from the border). VERIFIED.

| First returns per cell | Value |
|---|---|
| Minimum | 0 |
| Maximum | 54 |
| Median / mean | 4 / 4.42 |
| Cells ≥ 3 | **77.6%** |
| Cells < 3 | 22.4% |
| Empty cells | 0.87% |

All returns per cell: minimum 0, median 5, maximum 65.

**Density structure (VERIFIED, `fig2`): flight-line striping.** Single-coverage strips have about 2 first returns per 0.6 m cell. Per-column, the share of cells with ≥ 3 returns ranges from 0.54 to 1.00.

**Phase 1 gate status:**
- Every data-integrity check passed.
- The operational density rule in `phase1.py` (**at least 80%** of interior 0.6 m cells with ≥ 3 first returns) was **not met** (77.6%).
  - That 80% rule was written by TERRAIN-X **before the data were read**.
  - It is **not** one of your criteria or of P1-11G's: P1-11G only made < 3-return cells NoData.
  - It was **not relaxed**.

**Model-free feasibility facts** (for the decision, not a rule change):
- At 1 m, 96.9% of cells have ≥ 5 first returns (median 11).
- Under the ≥ 3 rule, 92.9% of 5 m blocks and 96.6% of 10 m blocks would have ≥ 50% valid coverage.

**INFERENCE:** density constrains a 0.6 m **pixel** reference but not the pre-registered 5 m / 10 m **block** scoring.

## 3. Phase 2 — registration test

Pre-registered in P1-11G and fixed in `registration.py` before any lidar data were read.

**Ground-level mask:** cells whose highest first return is class 2 (ground), NAIP NDVI < 0.2, and ≥ 20 m inside the border. That's 582,956 cells (14.6%).
- **Deviation from P1-11G (stricter, stated before running):** P1-11G also allowed "reference AGL < 0.3 m". That clause was **dropped** to obey the P1-11H anti-leakage rule, which forbids any use of the lidar AGL reference in registration.

**Signals:**
- **Signal 1 (paint):** NAIP brightness vs lidar first-return intensity.
- **Signal 2 (curbs/edges, independent):** NAIP brightness gradient vs the gradient of lidar ground-point elevation.

**Search:** ±10 px = ±6 m at 0.6 m, with a parabolic sub-pixel peak.

**No model, AGL reference or model output was used** (anti-leakage satisfied).

| Region | Signal | Best px | Offset east / north (m) | r_peak | r at zero shift | Sharpness (r_peak − median r ≥ 5 px away) | Peak on search edge |
|---|---|---|---|---|---|---|---|
| Whole crop | 1 | (0, 0) | **+0.12 / −0.12** | **0.211** | 0.211 | **0.141** | no |
| Whole crop | 2 | (0, 0) | −0.18 / −0.06 | 0.061 | 0.061 | 0.064 | no |
| Q1 NW | 1 | (0, 0) | +0.04 / +0.02 | 0.280 | 0.280 | 0.213 | no |
| Q2 NE | 1 | (0, 0) | +0.16 / −0.18 | 0.116 | 0.116 | 0.088 | no |
| Q3 SW | 1 | (0, 0) | +0.19 / −0.15 | 0.246 | 0.246 | 0.171 | no |
| Q4 SE | 1 | (0, 0) | +0.12 / −0.20 | 0.302 | 0.302 | 0.153 | no |

**Signal 2 by quadrant:** all peaks inside the window. Offsets: Q1 (−0.10, −0.02), Q2 (−0.53, +0.02), Q3 (−0.30, −0.19), Q4 (+0.01, −0.25) m.

**Correlation surfaces:** `figures/fig1_registration_surfaces.png`. **Offsets, mask and density:** `figures/fig2_quadrant_offsets_mask_density.png`.

## 4. Fixed acceptance rule applied without change

| Criterion (P1-11G) | Result |
|---|---|
| 1. Defined peak: inside window ✓; **r_peak ≥ 0.30 ✗ (0.211)**; sharpness ≥ 0.10 ✓ (0.141) | **FAIL** |
| 2. Quadrant signal-1 offsets within 1.0 m of whole-crop (x and y) | PASS (max 0.14 m) |
| 3. Signal-2 offset within 1.0 m of signal-1 | PASS (0.30 m, 0.06 m) |
| 4. Magnitude | 0.17 m, which would be "aligned" |
| **Decision** | **FAIL → Gate D. Stop.** |

**INFERENCE, clearly separated from the gate:**
- Every one of the 10 independent estimates (5 regions × 2 signals) peaks at the **same zero offset**, with sub-pixel offsets of 0.3 m or less and inter-estimate agreement within about 0.5 m.
- That pattern is what correct co-registration would look like. It is the opposite of P1-11F, where estimates disagreed by about 7 m.
- The failed criterion measures **how strongly** lidar 1064 nm intensity and NAIP visible brightness co-vary over pavement. That is a radiometric property, not a geometric one.
- **But the rule was fixed in advance, and the user instruction forbids changing it after seeing results.** So the gate outcome is D.

## 5. Phases 3–6 — reference, change detection, TerraHeight-S, scoring

**Not executed.**
- `phase3_6.py` asserts Phase 1 and registration PASS and would refuse to run.
- No AGL reference statistics, change mask, prediction, block metrics, height bins or least-squares diagnostic exist.
- The P1-11E real-data comparison remains **UNRESOLVED**.

## 6. Temporal mismatch

- **Gap:** 253 days. Both leaf-on (September vs late May). VERIFIED.
- **Urban change** between the dates: not assessed, because Phase 4 was not reached. UNRESOLVED.

## 7. Limitations

- **The r floor was never calibrated.** The 0.30 threshold was chosen in P1-11G without data on typical intensity–brightness correlation over pavement. Here the peak location is consistent, but r is moderate.
- **Density striping** from flight-line overlap limits a 0.6 m pixel reference.
- **No semantic lidar classes** (vegetation, building) exist in this survey.
- **Semantics:** TerraHeight-S predicts AGL only; nothing here concerns terrain elevation or a production DSM.

## 8. Decision for the user (not taken here)

The experiment stops. Moving forward needs an explicit decision by you, recorded as a **protocol amendment made after seeing the Phase 1–2 results**:

1. **Accept Gate D and close the real-0.6 m validation line for now.**
2. **Amend criterion 1** by documented reasoning, e.g. replace the fixed r ≥ 0.30 floor with a peak-significance / consistency criterion:
   - This site would pass on sharpness 0.141 ≥ 0.10, zero-offset agreement of all 10 estimates, and quadrant/signal consistency ≤ 1 m.
   - Optionally also accept block-level density: the pre-registered 5 m / 10 m blocks keep 92.9% / 96.6% coverage.
   - Then run `phase3_6.py` exactly as written.
   - **Every result would have to be labelled as obtained under an amended, post-hoc registration criterion.**
   - No part of the amendment would use TerraHeight-S, the AGL reference or any model output, so anti-leakage would still hold.
3. **Pre-register the amended criterion and test it first on a different site** before scoring here.
   - Note that C2 and C3 have lower density (P1-11G).

## 9. Final gate

**D. DATA/REFERENCE INVALID** under the fixed rule.

**Why:**
- Data integrity passed.
- Ground/DEM consistency is excellent (MAE 0.0085 m, zero shift).
- Registration **failed pre-registered criterion 1** (r_peak 0.211 < 0.30), even though its location and consistency evidence indicate about 0.2 m alignment.
- The operational 0.6 m density rule was also not met (77.6% < 80%).

TerraHeight-S was not executed, and **no accuracy claim is made**.
