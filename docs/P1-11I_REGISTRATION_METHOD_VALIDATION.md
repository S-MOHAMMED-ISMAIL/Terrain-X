# P1-11I — Registration-method validation (pre-registered)

**FINAL: FAIL.** The frozen registration criterion was **not** validated on the independent site. **No defensible registration criterion is established, so the experiment stops.** No TerraHeight-S validation follows.

**Scope:**
- **Not a TerraHeight-S experiment.** The model was not run; no AGL reference was built; no model output or lidar AGL was used anywhere.
- **P1-11H stays closed** as Gate D; nothing was applied retroactively to East Boulder.
- **No production change, integration, migration or commit.**

**Workspace:** scratchpad `p111i/`.

| File | Contents |
|---|---|
| `CRITERION_FROZEN.md` | The frozen criterion |
| `evaluate.py` | The frozen evaluation code |
| `FROZEN_HASHES.json` | Hashes and freeze time |
| `results.json` | Everything below |
| `w4/evaluation.json` | Raw evaluation output |
| `figures/` | Figures |

**Labels:** VERIFIED (measured), INFERENCE, UNRESOLVED, AUTHOR CLAIM.

## 1. Order of work (anti-tuning safeguards)

1. **Criterion and evaluation code written** before any validation site was chosen or any of its data read.
2. **Circularity found and fixed while still unfrozen.** The draft null test ("≤ 10% of null pairs valid") was circular, because validity uses the null 95th percentile. It was replaced before freezing by (a) derangement rejection and (b) a zero-offset-preference check. No validation data had been seen.
3. **Synthetic dry run** (code correctness only; no real site):
   - aligned synthetic → **PASS**, recovered (+1.171, +0.572) m vs designed (+1.2, +0.6) m
   - decorrelated synthetic → **FAIL**
   - **Observation:** C6 and C5b also passed on the null, so they're not diagnostic alone. The rule requires *all* criteria.
   - **Post-dry-run change:** `bool()` casts on the flags only (presentation).
4. **Validation site selected** by a pre-stated rule (§3). Its data were downloaded but **not read**.
5. **Frozen at 2026-09-27T11:41:25Z:**
   - `CRITERION_FROZEN.md` SHA-256 `bd539d3f…d39f`
   - `evaluate.py` SHA-256 `b2d13c0d…7b7`
   - Both hashes re-verified immediately before the single evaluation run.
6. **One evaluation run on the validation site.** Its result is reported unchanged.

**Disclosure:** the criterion was designed knowing the P1-11H outcome. That's why it was validated on a new site and included null and known-shift self-tests.

## 2. Exact pre-registered criterion

Reproduced from `CRITERION_FROZEN.md`.

### Inputs

Model-free and AGL-free.
- **NAIP** 4-band, 0.6 m.
- **Lidar rasters** on the NAIP grid, built as in P1-11H.
- **Ground-level mask:** highest first return is class 2, AND NDVI < 0.20, AND ≥ 20 m inside the border, AND ≥ 1 first return.

### Signals

- **Signal 1 (paint):** NAIP brightness vs lidar first-return intensity.
- **Signal 2 (curbs/edges, independent):** NAIP brightness gradient vs lidar ground-elevation gradient.

### Search

- Integer shifts ±10 px (±6 m) at 0.6 m; Pearson r over mask cells; parabolic sub-pixel peak.
- Sharpness S = r_peak − median(r at ≥ 5 px from the peak).

### Spatial units and null

- Whole site: 1.2 × 1.2 km. **Patches:** 16 of 300 × 300 m.
- **Null:** swapped patches (NAIP patch i vs lidar patch j, all i ≠ j). NULL95 = 95th percentile of null S.
- **VALID estimate:** peak not on the search edge AND S ≥ max(0.05, NULL95).

### Absolute r floor: not retained

Justified from first principles. r between 1064 nm lidar intensity and visible brightness is set by radiometric covariance and the paint fraction, not by geometry.

### PASS requires all of

| ID | Requirement |
|---|---|
| C1 | Whole-site signal-1 estimate VALID |
| C2 | ≥ 75% of patches VALID (signal 1), AND ≥ 90% of valid patches within 1.0 m of the whole-site offset (x and y) |
| C3 | Whole-site signal-2 estimate VALID, AND within 1.0 m of signal 1 |
| C4a | ≤ 1 of 20 random patch derangements passes C2 |
| C4b | ≤ 20% of swapped-null peaks within 1 px of (0, 0) |
| C5a | Patch bootstrap (200, seed 20260927) 95% half-width ≤ 0.30 m |
| C5b | Mask variants NDVI < 0.15 / < 0.25 within 0.30 m of the base offset |
| C5c | Ten 50% cell subsamples within 0.30 m of the base offset |
| C6 | Imposed lidar shifts (+4, 0), (0, −3), (−3, +2), (+6, +5) px recovered within 0.30 m |

**Magnitude (unchanged):** ≤ 0.6 m aligned; 0.6–3.0 m one global shift; > 3.0 m FAIL.

## 3. Candidate and site provenance

**Pre-stated selection rule:** eligible sites need:
- CO_DRCOG_2020 lidar
- no overlap with East Boulder
- median slope < 3°
- built/bare (NDVI < 0.1) > 50%
- the crop inside one NAIP tile
- ≥ 12 points/m² (all returns, probed tile)

Choose the highest density; break ties by the shortest NAIP–lidar gap.

VERIFIED from USGS TNM, NAIP Plus, 3DEP elevation and lidar header range reads:

| Candidate | Crop (EPSG:26913) | Single NAIP tile | Median slope | Built (NDVI < 0.1) | Points/m² (first) | Eligible |
|---|---|---|---|---|---|---|
| V1 Longmont downtown | 490700, 4445700, … | yes (2019-08-03) | 1.3° | 58.8% | 4.3 (2.8) | no: density |
| V2 Lafayette | 491700, 4426400, … | yes (2019-08-03) | 1.5° | 64.7% | 4.5 (3.3) | no: density |
| V3 Louisville / McCaslin | 486800, 4422500, … | yes (2019-09-19) | 2.4° | 42.9% | — | no: built < 50% |
| W1 Flatiron Park | 479800, 4429700, … | yes (2019-09-19) | 1.6° | 62.4% | 17.2 (15.2) | yes |
| W2 Gunbarrel | 481900, 4434400, … | **no** | — | — | — | no: NAIP seam |
| W3 North Boulder | 477400, 4431600, … | yes | 1.8° | 46.2% | — | no: built < 50% |
| **W4 downtown Boulder** | **475700, 4429200, 476900, 4430400** | yes, m_4010562_se (2019-09-19) | 1.9° | 51.8% | **30.3** (11.5) | **yes — selected** |

**Note (VERIFIED):** "points/m²" was all returns, as in P1-11G. By first returns, W1 (15.2) would lead W4 (11.5). The rule was applied as written. INFERENCE: all-return density favours canopy-rich sites.

### W4 data (VERIFIED)

- **Lidar:** USGS 3DEP LPC CO_DRCOG_2020_B20, tiles w0475n4429, w0475n4430, w0476n4429 and w0476n4430.
  - **Byte sizes equal the USGS-published sizes** (after resuming three truncated transfers).
  - Flown **2020-05-29**.
  - First returns per 0.6 m cell (interior): median 5; 85.2% have ≥ 3.
- **NAIP:** 4 × 2000 × 2000, EPSG:26913, origin (475700, 4430400), acquired 2019-09-19. **Gap: 253 days.**
- **Ground-level mask:** 246,938 cells (6.2%). East Boulder had 14.6%.

## 4. Results on W4 (single frozen run)

### Whole-site signal 1

| | |
|---|---|
| Best shift | (0, 0) px |
| **Offset** | **(−0.145, −0.026) m** |
| r_peak | 0.165 |
| Sharpness | **0.1198** vs NULL95 0.1161 → VALID |

### Whole-site signal 2

| | |
|---|---|
| Best shift | (0, −1) px |
| Offset | (+0.051, +0.363) m |
| r_peak | 0.093 |
| Sharpness | **0.0742 vs NULL95 0.0890 → NOT VALID** |

### Quadrants (signal 1)

All at (0, 0) px, offsets within 0.2 m, sharpness 0.10–0.15.

### Patches (signal 1)

| Patch | Best px | Offset (m) | Sharpness | Status |
|---|---|---|---|---|
| P00 | (0, 0) | +0.03, +0.07 | 0.145 | valid |
| P01 | (0, 0) | −0.20, −0.19 | 0.114 | weak |
| P02 | (0, 0) | −0.14, +0.19 | 0.294 | valid |
| P03 | (−1, 0) | −0.31, +0.04 | 0.191 | valid |
| P04 | (0, 0) | −0.11, −0.05 | 0.185 | valid |
| **P05** | (2, 10) | +1.2, −6.0 | 0.087 | **edge** |
| **P06** | (10, 9) | +6.0, −5.4 | 0.144 | **edge** |
| P07 | (0, 0) | +0.07, −0.07 | 0.123 | valid |
| **P08** | (10, 6) | +6.0, −3.6 | 0.130 | **edge** |
| **P09** | (10, 10) | +6.0, −6.0 | 0.111 | **edge** |
| P10 | (1, −1) | +0.65, +0.33 | 0.102 | weak |
| P11 | (0, 0) | −0.19, +0.05 | 0.133 | valid |
| P12 | (0, 0) | −0.08, −0.15 | 0.208 | valid |
| P13 | (0, 0) | −0.19, −0.01 | 0.198 | valid |
| P14 | (0, 0) | −0.11, −0.23 | 0.125 | valid |
| P15 | (0, 0) | −0.27, +0.13 | 0.116 | weak (0.1155 < 0.1161) |

### Null

| | Signal 1 | Signal 2 |
|---|---|---|
| Pairs | 240 | 240 |
| NULL95 | 0.1161 | 0.0890 |
| Median null sharpness | 0.047 | 0.049 |
| Null peaks within 1 px of zero | 0.0% | 1.25% |

### Criteria

| Criterion | Value | Result |
|---|---|---|
| C1 | whole-site signal 1 valid | **PASS** |
| **C2** | **9/16 = 56% valid** (needs ≥ 75%); 100% of valid within 1 m | **FAIL** |
| **C3** | signal 2 not valid (S 0.074 < 0.089); offset within 1 m | **FAIL** |
| C4a | 0 of 20 derangements pass C2 | PASS |
| C4b | 0% null peaks near zero | PASS |
| C5a | bootstrap half-width 0.076 / 0.067 m | PASS |
| C5b | NDVI 0.15 → (−0.149, −0.002); 0.25 → (−0.154, −0.050) | PASS |
| C5c | 10 subsamples all within 0.03 m | PASS |
| C6 | all 4 imposed shifts recovered exactly | PASS |
| Magnitude | 0.147 m (would be "aligned") | — |
| **FINAL** | | **FAIL** |

Figures: `figures/fig1_w4_surfaces_and_patches.png`, `figures/fig2_w4_patch_map.png`.

## 5. Interpretation

Post-hoc and descriptive; **nothing was changed**.

**VERIFIED:**
- The whole-site, quadrant and valid-patch estimates all sit at about zero offset. Valid patches are within 0.31 m; every non-edge patch, including the weak ones, is within 0.65 m.
- The method rejects mismatched data (C4) and is stable (C5).
- Seven of 16 patches failed. **Four put their peak on the search edge,** all in the **dense multi-storey commercial core** (P05, P06, P08, P09). **Three were just below the sharpness threshold** (P01, P10, P15).
- The failing patches have **more** ground-mask cells (median 20.8k) than valid ones (9.7k), so sample size isn't the explanation.
- The edge peaks share a direction (NAIP shifted about +4–6 m east, −4–6 m north).

**INFERENCE (UNRESOLVED):** building shadows cast onto ground-level pavement, and relief lean of multi-storey buildings in NAIP, create strong ground-level contrast that the lidar geometry doesn't share. In dense cores this pulls correlation peaks to large spurious offsets.

**What this shows about the method:**
- On a mixed site, it **correctly flags** the patches where registration is ambiguous, rather than passing them. That's the "genuinely ambiguous" case (B) the prompt asked to distinguish.
- It **does not establish** a site-level PASS here, because too many patches are ambiguous.
- The criterion is therefore **not yet validated as a general acceptance rule.**

## 6. Answer to the objective

**Question:** can we distinguish (A) weak-but-correctly-localised alignment from (B) genuinely ambiguous registration on real NAIP + lidar?

**Partially:**
- The frozen criterion **did** separate the synthetic aligned case from the null.
- On real data it **did** reject ambiguous patches, and it gives stable zero-offset estimates where ground features are clean.
- But on the pre-selected independent real site it **failed as a site-level acceptance rule** (C2, C3).

**No defensible, validated site-level registration criterion is established.** Per the stop condition, the TerraHeight-S real-data validation does not proceed.

## 7. Recommendations for any future attempt

Not executed; any use requires a **new** pre-registration.

- **Site eligibility:**
  - measure density in **first returns**, not all returns
  - exclude dense multi-storey cores (shadow and lean), e.g. by a building-height or shadow screen defined in advance from lidar surface − ground
  - note that shadow direction can be predicted from NAIP acquisition time and sun geometry
- **Stricter ground mask:** exclude cells within a fixed distance of elevated lidar objects, to remove shadow-contaminated ground.
- **Patch-level acceptance stated in advance:** e.g. require consistency among valid patches with a minimum count, rather than ≥ 75% of all patches.
- **Validation design:** validate on ≥ 2 independent sites before accepting any rule.

## 8. Final

**FAIL.** The frozen criterion was not validated on the independent site W4 (C2 and C3 failed).

**STOP:** no TerraHeight-S validation. P1-11H remains Gate D.

No production changes, no integration, no migration, no commit.
