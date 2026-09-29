# T1 — Boulder real-data independent calibration validation

**Result: the calibration quality gate REJECTED the calibration.** No metric elevation or DSM was produced. TERRAIN-X fell back to relative depth, as designed.

| | |
|---|---|
| Run date | 2026-09-27 (UTC) |
| Pipeline | the unmodified production TERRAIN-X pipeline, driven through its public REST API |
| Source image | USGS NAIP, 2019-09-19, 0.6 m, RGB |
| Reference | USGS 3DEP 1 m **bare-earth lidar DEM** (NAVD88, metres) |
| CRS | EPSG:26913 for both |
| Gate outcome | **FAILED** on G1 (wrong scale sign) and G2 (held-out skill below 0) |

This was the first TERRAIN-X calibration against a reference not derived from TERRAIN-X's own depth output.

---

## A. Dataset provenance

Both files come from official USGS services. Neither was generated, edited or derived from TERRAIN-X output.

**Source image: USGS/USDA NAIP orthoimagery**
- NAIP tile `m_3910506_ne_13_060_20190919`, Colorado, acquired **2019-09-19**, agency USDA.
- The tile is 0.6 m, 4-band (sensor type CNIR: colour plus near-infrared), UTM 13N, NAD83. These attributes come from the USGS NAIP Plus ImageServer catalog record, raster id 30800.
- Obtained through the official USGS NAIP Plus ImageServer `exportImage` operation:
  - locked to raster 30800 (`esriMosaicLockRaster`)
  - bands 1–3 only
  - nearest-neighbour resampling
  - output 2000 × 2000 px in EPSG:26913
- Service: `https://imagery.nationalmap.gov/arcgis/rest/services/USGSNAIPPlus/ImageServer`
- File: `TERRAIN-X-TEST-DATA/02_calibrated/T1_NAIP_Boulder_RGB_2019.tif`, SHA-256 `a938cc356181c65e6d9599cc178d12ff387bc126f615aefc6103dbd4d0b67743`

**Reference: USGS 3DEP 1 m DEM**
- Product `USGS 1 Meter 13 x47y443 CO_DRCOG_2020_B20`, published 2022-02-10.
- Source GeoTIFF: `https://prd-tnm.s3.amazonaws.com/StagedProducts/Elevation/1m/Projects/CO_DRCOG_2020_B20/TIFF/USGS_1M_13_x47y443_CO_DRCOG_2020_B20.tif`
- Metadata: `…/CO_DRCOG_2020_B20/metadata/USGS_1M_13_x47y443_CO_DRCOG_2020_B20.xml`; ScienceBase item `620de541d34e6c7e83baa08a`.
- The official metadata states:
  - the elevations represent "the topographic **bare-earth** surface"
  - one-meter DEMs "are produced exclusively from high resolution light detection and ranging (**lidar**) source data"
  - "All bare earth elevation values are in **meters** and are referenced to the **North American Vertical Datum of 1988 (NAVD88)**"
  - source data dates are 2020-05-26 to 2021-03-13
- **This reference is a bare-earth DTM, not a DSM.** It excludes trees and buildings.
- The crop is a windowed read over HTTP, 1220 × 1220 px, taken at columns 4716–5935 and rows 5374–6593 of the source tile.
  - No resampling. The saved pixels are bit-identical to the same window re-read from the USGS source.
  - The full tile was never downloaded.
  - One provenance tag was added (`TERRAINX_T1_PROVENANCE`). Pixels, CRS and transform are unchanged.
- File: `TERRAIN-X-TEST-DATA/02_calibrated/T1_3DEP_Boulder_1m.tif`, SHA-256 `ecdb080a6fd540d5ba039b8eaab976b7c26e8e674d2d4cd7ae667d46f462638a`

**Independence.** The reference is airborne lidar. The image is aerial photography from a different agency, mission and date, so they are genuinely independent.

**Location.** Boulder Mountain Parks / Flatirons foothills, Colorado. The image centre is at lon −105.288986, lat 39.965986.

## B. Source and DEM metadata

| Property | NAIP source | 3DEP reference |
|---|---|---|
| Size | 2000 × 2000 | 1220 × 1220 |
| Bands / type | 3 × uint8 | 1 × float32 |
| CRS | EPSG:26913 (NAD83 / UTM 13N) | EPSG:26913 (NAD83 / UTM 13N) |
| Pixel size | 0.6 m | 1.0 m |
| Geotransform | (474720.0, 0.6, 0, 4424622.0, 0, −0.6) | (474709.9999821812, 1.0, 0, 4424631.999955563, 0, −1.0) |
| Bounds (L, B, R, T) | 474720, 4423422, 475920, 4424622 | 474710.0, 4423412.0, 475930.0, 4424632.0 |
| NoData | none | −999999, with **0** NoData cells (100% valid) |
| Colour interpretation | undefined (as written by the ImageServer; content is RGB, checked visually) | gray |
| Vertical | — | metres, NAVD88, per the official XML. The GeoTIFF carries no vertical CRS. |

**Spatial relationship**, computed independently from the geotransforms:
- The NAIP footprint lies entirely inside the DEM, with a 10.000 m margin on every side.
- Their intersection is 100% of the image area (1,440,000 m²).
- The centres agree to within 0.00005 m.
- Every image pixel centre maps inside the DEM.

**DEM statistics:**

| Area | Min | Max | Relief | Mean | Std |
|---|---|---|---|---|---|
| Full crop | 1883.16 m | 2570.22 m | — | 2138.48 m | 145.23 m |
| Under the image footprint | 1884.53 m | 2565.79 m | **681.3 m** | — | 143.6 m |

Slope under the image footprint: median 32.0°, 90th percentile 48.7°.

**NAIP band means:** 48.6, 65.7 and 54.9. No zero or saturated pixels. It is natural-colour aerial imagery of dense conifer forest, sandstone slabs and clearings.

## C. Model and calibration configuration

These values were recorded before the run from the running worker container's effective settings. They match the defaults in the repository.

**Model**
- Depth Anything V2 Small: `depth-anything/Depth-Anything-V2-Small-hf`, revision `5426e4f0f36572d16453bbda7a8389317b1bef99`, Apache-2.0.
- Runs on CPU.
- The model input was 518 × 518: the 2000 px image is downsampled by the model's processor, so each model input pixel covers about 2.3 m. The output is resized back to 2000 × 2000.

**Runtime:** torch 2.5.1+cpu, transformers 4.47.1, rasterio 1.4.3, GDAL 3.9.3.

**Calibration**
- Affine fit: least squares with iterative sigma-clipping (`CALIBRATION_OUTLIER_SIGMA = 2.5`, `CALIBRATION_MAX_ITERATIONS = 5`).
- Sampling: `CALIBRATION_MAX_SAMPLES = 2000` on a uniform grid. Here that meant a stride of 44 px, which is 26.4 m, giving 46 × 46 = 2116 candidates. Also `MIN_CALIBRATION_SAMPLES = 3`.
- **Quality gate, policy v1:** `CALIBRATION_MIN_CV_SKILL = 0.0`, expected scale sign +1, `CALIBRATION_CV_BLOCKS_PER_SIDE = 4`.
  - DEM cross-validation leaves one spatial block out at a time, over 16 blocks.
  - These are engineering acceptance settings, not validated accuracy standards.
- `ANALYSIS_JOB_TIMEOUT_SECONDS = 900`.
- Job parameters: `{"version": "v1", "dem_reference_dataset_id": "4e6aa7c0-…"}`. No segmentation and no disaster screening.

## D. Execution details and timing

**Pre-flight:** all five containers were healthy (backend, worker, db, redis, frontend). `/api/v1/health` returned `database: ok, redis: ok`.

**Workflow:** the production REST API, the same endpoints the frontend uses:
1. register and log in
2. `POST /projects`
3. `POST /projects/{id}/datasets` with `role=source_image`
4. `POST /projects/{id}/datasets` with `role=dem_reference`
5. `POST /projects/{id}/datasets/{source}/analysis`
6. poll until the job finishes

| Item | Value |
|---|---|
| Project | `e3bf5fea-b5e5-481b-991e-570252537c4f`, "T1 — Boulder Independent DEM Validation" |
| Source dataset | `442b1db1-8075-4f8e-b285-9e7d0ace9927` (status `valid`) |
| DEM reference dataset | `4e6aa7c0-c9b0-490c-8c0c-d842eb7e000a` (status `valid`) |
| Analysis job | `c9caaf6b-f28a-4337-a0b6-0903234c63ab` |
| Created / started / completed (UTC) | 04:09:03.635 / 04:09:04.978 / 04:09:11.549 |
| Execution time | 6.57 s (the RQ worker reports 7.82 s including overhead) |
| Depth inference | 1.23 s |
| Calibration | 0.229 s |
| Job status | `completed` |
| Depth output | `relative_depth` artifact `79dac8b6-7547-4bce-afaa-39faaf25f814`: 2000 × 2000 float32, EPSG:26913, range 1.3032–2.6456 |
| Calibration status | **`failed`** (quality-gate rejection) |
| Errors / warnings | No job error. No warnings in the worker log. |

The stored uploads are byte-identical to the files in `TERRAIN-X-TEST-DATA/02_calibrated/` (same SHA-256).

**Runner-script note.** The first attempt of the local runner script failed at user registration: HTTP 422, because the reserved `.local` email domain was rejected. That failure happened before any project, dataset or job existed. The analysis ran exactly **once**.

## E. Calibration diagnostics (persisted)

All values are copied from `calibration_metadata` on the job record. Nothing was recomputed or adjusted.

**Sampling**

| | |
|---|---|
| Candidate samples | 2116 |
| Valid samples | 2116 (all inside the DEM, none NoData) |
| Inliers / outliers (2.5σ, 4 iterations) | 2100 / 16 |
| Reprojected | no (both EPSG:26913) |

**Fitted model:** Z = a·D + b

| | |
|---|---|
| Scale a | **−88.3816** |
| Offset b | 2315.6319 |

**In-sample.** These describe the fit on the same data it was fitted to. They aren't held-out evidence.

| Metric | Value |
|---|---|
| Pearson r (all 2116 valid samples) | **−0.1909** |
| Spearman ρ (all valid samples) | **−0.2505** |
| In-sample R² (all valid samples) | 0.0356 |
| All-sample MAE / RMSE / bias | 119.86 m / 141.91 m / −2.77 m |
| Inlier-only MAE / RMSE / bias | 117.98 m / 138.82 m / ≈0 (−1.5e-12) m |
| Inlier residual range | −345.60 m to +253.97 m |

**Held-out: leave one spatial block out, 4 × 4 = 16 non-empty blocks, 2116 held-out samples**

| Metric | Value |
|---|---|
| Held-out MAE | **136.997 m** |
| Held-out RMSE | **160.874 m** |
| Held-out bias (predicted − reference) | +2.534 m |
| SSE, cross-validation | 54,762,776 |
| SSE, baseline (training-fold mean) | 49,656,589 |
| **Held-out skill** = 1 − SSE_cv / SSE_baseline | **−0.1028** |

**Per-fold detail** (fold model fitted without the held-out block):
- The fitted scale is **negative in all 16 folds**, from −186.49 to −31.83.
- Depth beat the training-fold-mean baseline in 9 of 16 folds.
- Per-fold skill ranges from −3.70 to +0.34.
- The two worst folds (0 and 1) dominate the pooled result.

For scale: the reference's standard deviation under the image footprint is 143.6 m. A held-out RMSE of 160.9 m is therefore *worse* than predicting a constant.

## F. Quality-gate result

**`passed: false`** (policy v1). Two criteria failed; none were left unevaluated.

**G1_expected_scale_sign**
> Fitted scale a=−88.3816 does not have the expected sign (+1); the calibrated surface would contradict this pipeline's depth convention.

**G2_heldout_skill**
> Held-out skill −0.10283 is not above the policy minimum 0: depth did not predict held-out reference elevations better than their training-fold mean.

G0 (validation feasibility) **passed**: cross-validation was feasible with 16 non-empty blocks.

**Persisted outcome:** "No metric elevation or DSM was written."

**This is an honest scientific rejection, not a software failure.** The job completed normally and the gate did exactly what it is for.

## G. Generated and unavailable artifacts

Checked through the production API, and against the job's storage directory, which contains only `depth.tif`.

| Artifact / capability | Status | Evidence |
|---|---|---|
| `relative_depth` | **Generated** | Labelled "Relative Depth (Uncalibrated)", with value semantics "Relative, unitless inverse depth … NOT metric elevation, NOT a DSM" |
| `metric_elevation` | **Not produced** | Layer unavailable, citing the full gate rejection reason |
| `dsm` | **Not produced** | Same |
| `dtm` / `ndsm` | **Unavailable** | "ground filtering needs a calibrated DSM that passed the calibration quality gate". Ground filter status: `not_requested`. |
| `calibration_residuals` | **Unavailable** | "Calibration failed or was rejected by the calibration quality gate…" |
| Slope / aspect / hillshade / flood / landslide | **Unavailable** | A disaster-screening job citing the relative-depth artifact returns **HTTP 422**: "Metric elevation/DSM is required for this hazard analysis. Relative depth is unitless and cannot be interpreted as elevation." No job was created (the project's job count stayed at 1). |
| Slope-at-point on relative depth | **Refused** | HTTP 422 |
| Point value on relative depth | Available, **correctly labelled** | `value_kind: relative_depth`, units "relative units (unitless inverse depth — NOT elevation)", `calibration_state: failed` |
| 2D map overlays | Available | EPSG:3857 overlay PNGs for the source and the relative-depth artifact, both HTTP 200 |
| 3D terrain | Available, relative | Terrain context `available: true`, `height_kind: relative_depth`. Grid 256 × 256, 4.6875 m cells, no NoData. Checked through the API, not visually in a browser. |
| GLB export, 256 px | Available, **labelled unitless** | 7.58 MB, texture embedded. `height_kind: relative_depth`, `vertical_units: unitless`, `physical_height: false`, `horizontal_vertical_units_comparable: false`, `calibration_status: failed`, `calibration_quality_gate_passed: false`. 65,536 vertices; 130,050 triangles. |
| GLB export, 512 px | Available | 12.56 MB. Texture not requested, reported as `omitted`. |

**Observation.** The GLB metadata reports `sky_mask_applied: true` for this nadir aerial image. The mask removed **no** cells (vertex and triangle counts equal a full 256 × 256 grid). The sky mask is a heuristic for ground-level photos, and it is evaluated on nadir imagery as well.

## H. Scientific limitations

1. **Single site, single image, single run.** One tile of forested, steep foothill terrain. It is not a benchmark.
2. **Bare-earth reference against a canopy-seeing model.** The DEM excludes the dense conifer canopy that the image and the depth model see. Canopy is roughly tens of metres; the residuals are about 140 m RMSE, so canopy cannot account for the result on its own.
3. **Nadir imagery.** Depth Anything V2 is trained mainly on ground-level and oblique photographs. In a nadir orthophoto, terrain elevation mostly doesn't show up as camera distance. This result is consistent with the earlier GAMUS single-tile diagnostic (depth vs. above-ground height: R² 0.005, Pearson r −0.07; `docs/ARCHITECTURE_NOTE_RS_HEIGHT.md`).
4. **Downsampled model input.** 518 × 518, about 2.3 m per input pixel.
5. **Time gap.** The image is from 2019-09; the lidar was flown 2020-05 to 2021-03.
6. **Imaging conditions.** Steep slopes cause terrain shadows. NAIP acquisition time of day is not recorded in the catalog record used.
7. **Possible sub-pixel shift.** The ImageServer re-served NAIP on the requested grid (nearest neighbour), which may shift it by up to 0.3 m. That is negligible against a 26.4 m sample spacing.
8. **Unreliable in-sample correlations.** Pearson and Spearman are in-sample and reported only. Samples 26.4 m apart are spatially autocorrelated, so the effective number of independent samples is below 2116.
9. **Engineering gate thresholds.** They aren't validated accuracy standards. The negative sign in every fold, together with skill below 0, does not depend on threshold tuning.

## I. What this result supports

- On this real, independent pair, **monocular relative depth from Depth Anything V2 Small did not carry usable information about bare-earth terrain elevation.**
  - The relationship was weak and negative: Pearson −0.19, Spearman −0.25.
  - Held-out prediction was worse than a constant (skill −0.10; RMSE 160.9 m against 143.6 m reference std).
- **The quality gate works as intended on real data.** It correctly refused to produce a metric surface that would have been wrong by ~140–160 m RMSE with an inverted relationship. It reported the exact failure reasons.
- **The relative-depth fallback is correct end to end:**
  - no metric, DSM, DTM, nDSM or residual artifacts
  - metric-only analyses refused with explicit messages
  - relative 2D, 3D and GLB outputs available and consistently labelled unitless / not elevation
- **The pipeline runs cleanly on a real 2000 × 2000 georeferenced image:** 6.6 s job, 1.2 s inference, on CPU.

## J. What this result does NOT support

- **Any claim that TERRAIN-X can estimate metric terrain elevation or a DSM from a single nadir aerial image.** The single-view metric-height claim is not supported by this experiment.
- Any claim about accuracy against canopy, buildings or a DSM. The reference is a **bare-earth lidar DEM (DTM)**. This run measures agreement with bare-earth elevation only.
- Generalisation in either direction. The result does not show that calibration fails on every scene (flat terrain, urban scenes, oblique imagery or other sensors were not tested). It does not show that a different model, resolution or reference type would fail.
- Any accuracy statement for the relative-depth surface itself. It is unitless and was never claimed to be elevation.
- Any change to thresholds, models or code. None was made, and none is implied by this report.

## K. Reproduction instructions

These steps rebuild the same inputs from official USGS sources and rerun the same production workflow.

**1. NAIP source** (official USGS ImageServer, locked to raster 30800):
```
curl -G "https://imagery.nationalmap.gov/arcgis/rest/services/USGSNAIPPlus/ImageServer/exportImage" \
  --data-urlencode "bbox=474720,4423422,475920,4424622" --data-urlencode "bboxSR=26913" \
  --data-urlencode "imageSR=26913" --data-urlencode "size=2000,2000" \
  --data-urlencode "bandIds=0,1,2" --data-urlencode "format=tiff" --data-urlencode "pixelType=U8" \
  --data-urlencode "interpolation=RSP_NearestNeighbor" \
  --data-urlencode 'mosaicRule={"mosaicMethod":"esriMosaicLockRaster","lockRasterIds":[30800]}' \
  --data-urlencode "f=image" -o T1_NAIP_Boulder_RGB_2019.tif
```
Expected: 2000 × 2000 × 3 uint8, EPSG:26913, geotransform (474720, 0.6, 0, 4424622, 0, −0.6). The file hash depends on the live service, so compare pixel statistics (band means 48.63 / 65.66 / 54.87) instead.

**2. DEM reference** (windowed read, no resampling). Uses GDAL's `/vsicurl/`, so only the needed blocks are fetched:
```python
import rasterio
from rasterio.windows import Window
U = "/vsicurl/https://prd-tnm.s3.amazonaws.com/StagedProducts/Elevation/1m/Projects/CO_DRCOG_2020_B20/TIFF/USGS_1M_13_x47y443_CO_DRCOG_2020_B20.tif"
with rasterio.open(U) as src:
    win = Window(4716, 5374, 1220, 1220)   # = bounds 474710, 4423412, 475930, 4424632
    data = src.read(1, window=win)
    prof = src.profile | dict(width=1220, height=1220, transform=src.window_transform(win),
                              compress="lzw", tiled=True, blockxsize=256, blockysize=256)
    with rasterio.open("T1_3DEP_Boulder_1m.tif", "w", **prof) as dst:
        dst.write(data, 1)
        dst.units = src.units
```
`gdal_translate -projwin 474710 4424632 475930 4423412 /vsicurl/<URL> out.tif` gives the same window. Expected: 1220 × 1220 float32, 0 NoData cells, min 1883.159, max 2570.219, mean 2138.484.

**3. Run TERRAIN-X**, with production defaults and the services healthy:
1. Register and log in.
2. `POST /api/v1/projects`.
3. Upload the NAIP file with `role=source_image` and the DEM with `role=dem_reference` (`POST /api/v1/projects/{pid}/datasets`, multipart).
4. `POST /api/v1/projects/{pid}/datasets/{source_id}/analysis` with body `{"parameters": {"version": "v1", "dem_reference_dataset_id": "<dem_id>"}}`.
5. Poll `GET /api/v1/projects/{pid}/analysis/{job_id}` until it finishes, then read `calibration_metadata`.

**Expected outcome:** `calibration_status: failed`, gate rejection on G1 and G2, with scale a ≈ −88.38 and held-out skill ≈ −0.103. Identical results are expected for identical input pixels and the same model revision: CPU inference plus deterministic grid sampling. This was not verified by a second run, which T1 ruled out.
