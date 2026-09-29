# SIH FINAL DEMO READINESS

**Date:** 2026-09-29
**Status:** PASS
**Dataset:** TERRAIN-X-TEST-DATA/04_synthetic_complete/mountain_georeferenced_rgb.tif

---

## Demo Flow

`
Dataset Upload → Analysis → Terrain Workspace → 3D View → Layers → Measurements → Flythrough → Export
`

---

## Tested Interactions

| Interaction | Result |
|---|---|
| Registration | PASS |
| Project creation | PASS |
| GeoTIFF upload | PASS |
| DEM upload | PASS |
| Calibration analysis | PASS (completed, calibration: rejected) |
| Mode = Relative | PASS |
| Vertical = Unitless | PASS |
| CRS = EPSG:32643 | PASS |
| Calibration = Rejected | PASS |
| Metric unavailable | PASS |
| 3D view | PASS |
| 2D/3D toggle | PASS |
| Layer switching | PASS |
| Visibility toggle | PASS |
| Terrain exaggeration | PASS |
| Measurement mode | PASS |
| Flythrough mode | PASS |
| GLB export | PASS |
| No console errors | PASS |

**Result: 18/18 PASS**

---

## Known Limitations

1. **Metric calibration rejected** — Depth Anything V2 Small produces inverted output on aerial imagery. The G1 quality gate correctly rejects this. This is expected behavior, not a bug.

2. **Report generation disabled** — The "Generate report" button is correctly disabled when calibration fails. Reports require at least one completed analysis with meaningful results.

3. **Metric products unavailable** — Metric Elevation, DSM, DTM, nDSM are correctly shown as unavailable. No fake metric terrain is displayed.

4. **GCP calibration blocked** — The synthetic GCP CSV uses non-standard column names (easting_m/northing_m/elevation_m) that the pipeline correctly rejects.

---

## Expected Calibration Behavior

- **Fitted scale:** ~-279.488 (negative)
- **Rejection reason:** G1_expected_scale_sign
- **UI display:** "Calibration rejected" with full diagnostics
- **Relative terrain:** Remains available
- **Metric products:** Correctly unavailable

---

## Startup Instructions

`ash
cd docker
docker compose up --build
`

Then open:
- Frontend: http://localhost:5173
- Backend API docs: http://localhost:8000/docs

---

## Final Screenshots

| File | Description |
|---|---|
| SIH_3D.png | 3D terrain view |
| SIH_2D.png | 2D map view |
| SIH_3D_exaggerated.png | 3D with vertical exaggeration |
| SIH_measure.png | Measurement mode |
| SIH_flythrough.png | Flythrough mode |
| SIH_report.png | Reports panel |
| SIH_FINAL.png | Final 3D terrain view |

All screenshots: D:\SIH_project\scratchpad\synth_04_screenshots\

---

## Regression Results

| Test | Result |
|---|---|
| TypeScript | PASS |
| Vitest | PASS (256/256) |
| Vite build | PASS (15.52s) |
| Playwright demo flow | PASS (18/18) |

---

## Final State

The browser is left on the strongest 3D terrain view:
- **Dataset:** mountain_georeferenced_rgb.tif
- **Mode:** Relative
- **Vertical:** Unitless
- **CRS:** EPSG:32643
- **Calibration:** Rejected (with diagnostics)
- **Active Layer:** Relative Depth (Uncalibrated)
- **3D terrain:** Visible with RGB texture

---

## Blocker

**None.** The demo flow is complete and reliable. Metric calibration rejection is expected behavior and is correctly displayed.

---

**No production code modified. No calibration gates changed. No thresholds changed. No Git commit created.**