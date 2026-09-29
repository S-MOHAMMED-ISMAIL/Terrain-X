# D2 — geographic-source RGB texture consistency

**Status: CLOSED (§9).** No commit. No migration. No UI redesign. No texture reprojection. No TerraHeight-S. Terrain values, grid formulas, calibration, DTM/nDSM, map overlays, GLB coordinate/format semantics and D1 geometry are unchanged.

**Labels:**
- **VERIFIED:** measured or tested here.
- **INFERENCE:** reasoned from code, not directly tested.
- **UNRESOLVED:** open.

## 1. Original defect

The browser decided whether to drape the source RGB on the 3D terrain with a dimension check in `TerrainWorkspace.tsx`:

```
textureCompatible = mode === "3d" && terrain.available && rgbLayer.available
                    && rgbLayer.width === terrain.width && rgbLayer.height === terrain.height
```

`terrain.width/height` are the terrain **artifact raster's** dimensions. Analysis artifacts are written with the source raster's own dimensions, CRS and transform (`analysis_execution.py`: `write_single_band_float32(..., crs=raster.crs, transform=raster.transform)`). So for a geographic source (EPSG:4326) the check always passed.

But `extract_terrain_grid` **reprojects** a geographic artifact into a UTM grid. That grid has its own transform, its own extent (with NaN fill) and its own cell count, and it is rotated and skewed relative to the lat/lon pixel grid.

The texture draped is the dataset preview: the source's own pixel grid, decimated, never warped. It was stretched over the UTM mesh with normalised UVs, so the texture was spatially misregistered, and nothing reported it.

- **VERIFIED:** under the restored old rule, the new browser test finds the texture toggle enabled for the geographic fixture (§7).
- **Separate definitions:** the P1-9 GLB export (`_mesh_texture_png`) already omitted the texture for this case. That was a second, independent definition.

Design note (written before coding): scratchpad `d2/DESIGN_NOTE.md`.

## 2. Compatibility contract (the one definition)

`geospatial.terrain_grid.rgb_texture_compatibility(source_path, terrain_path)` reads both rasters' **headers** (provenance, never dimensions alone). It returns `TextureCompatibility(compatible, code, reason)`.

The checks run in this order; the first failure decides:

| # | Condition | `code` if violated |
|---|---|---|
| 0 | A source raster image exists (not absent, not the GCP CSV) | `no_source_image` |
| 0 | Both rasters are readable | `terrain_unreadable` / `source_unreadable` |
| 5 | The terrain grid is **not reprojected**: `grid_is_reprojected(terrain CRS)`. This is the same predicate `extract_terrain_grid` now uses to decide reprojection. | `reprojected_terrain_grid` |
| 2 | Source dimensions equal the terrain raster's dimensions | `dimension_mismatch` |
| 1 | Source has ≥ 3 bands, the first three uint8 | `not_rgb_uint8` |
| 4 | Both or neither are georeferenced | `georeferencing_mismatch` |
| 4 | Same CRS | `crs_mismatch` |
| 3/4 | Same affine transform (`Affine.almost_equals`) | `transform_mismatch` |

**Why passing all checks means correspondence:**
- Terrain pixel (r, c) *is* source pixel (r, c).
- The grid's decimation (terrain) and the preview's decimation (texture) are both uniform rescales of that same pixel grid.
- The D1 UVs `((c+0.5)/W, 1−(r+0.5)/H)` address each grid cell's own texel centre.

**Non-georeferenced:** both rasters without CRS, same dimensions and transform, are compatible. That is the same result as before D2.

## 3. Implementation

| File | Change |
|---|---|
| `geospatial/terrain_grid.py` | New `grid_is_reprojected(crs)`, now also used by `extract_terrain_grid` (same condition as before, `crs.is_geographic`). New `TextureCompatibility` and `rgb_texture_compatibility`. The grid formulas are untouched. |
| `backend/app/services/visualization.py` | `texture_compatibility(dataset, terrain_artifact)` wraps the rule. `_mesh_texture_png` (GLB) now calls it instead of its own checks. The visualization context fills the new terrain fields for both the DSM and the relative-depth terrain. |
| `backend/app/schemas/visualization.py` | `TerrainContextOut` gains `texture_compatible`, `texture_unavailable_code` (machine-readable) and `texture_unavailable_reason`. |
| `frontend/src/api/types.ts` | The same three fields on `TerrainContext`. |
| `frontend/src/components/terrain/terrainAvailability.ts` | `textureAvailable(terrain)` = `terrain.available && terrain.texture_compatible === true`. It is the backend's decision only; no frontend heuristic remains. |
| `frontend/src/components/terrain/TerrainWorkspace.tsx` | The dimension heuristic is removed and `textureAvailable` is used. The toggle gets `data-testid="texture-toggle"`, `data-texture-compatible` and `data-texture-code`. The existing "(not spatially compatible)" text now carries the reason as a `title`. No other visible change. |
| `frontend/src/components/terrain/TerrainView3D.tsx` | `data-texture-applied` on the 3D container. It is `"true"` only after a texture is actually set on the mesh material, and it resets whenever the map is cleared. This exposes the real state for the browser test. |
| `scripts/generate_e2e_geographic_fixture.py`, `frontend/e2e/fixtures/geographic-source.tif`, fixtures README | New deterministic fixture: the structured scene, 32×32, EPSG:4326, 0.0005°/px at 17.87 E, 60.045 N. |
| `docs/ARCHITECTURE.md` | The texture rule sentence is updated. |

**Not changed** (VERIFIED: not edited in D2):
- `terrainMesh.ts`, `flythrough.ts`, `terrainCoords.ts` (D1 geometry and UVs)
- `mesh_export.py`
- the map overlay and raster preview
- calibration
- `coordinate_to_pixel`

## 4. Projected-source behaviour

Example: `calibrated-source.tif`, EPSG:32633, 32×32. The terrain artifact has the same CRS, transform and dimensions, and is not reprojected. The result is `texture_compatible: true`, the toggle is enabled and checked by default, and the texture is applied to the material (`data-texture-applied="true"`). **VERIFIED** (API and browser tests).

## 5. Geographic-source behaviour

Example: `geographic-source.tif`, EPSG:4326, 32×32. The RGB layer and the terrain artifact are both 32×32 (**VERIFIED**: the pre-D2 rule would enable the texture).

The context reports:
- `texture_compatible: false`
- `texture_unavailable_code: "reprojected_terrain_grid"`
- a reason mentioning "reprojected"

In the browser:
- The toggle is disabled with "(not spatially compatible)".
- A forced click applies nothing.
- The dataset preview is **never requested**, and `data-texture-applied` stays `"false"`.

The GLB for the same case omits the texture with the **identical** reason string. **VERIFIED.**

## 6. Tests

**Backend** — `tests/test_d2_texture_compatibility.py`, 14 tests, pure plus API:

| Required | Test |
|---|---|
| 1 projected, exact correspondence → enabled | `test_1_projected_source_with_exact_correspondence_is_compatible`; API `test_1_api_projected_source_texture_enabled_in_context_and_glb` (context true, GLB embedded with `TEXCOORD_0`) |
| 2 geographic, reprojected grid → disabled | `test_2_geographic_source_reprojected_grid_is_incompatible_despite_equal_dims`; API `test_2_api_geographic_source_texture_disabled_although_dims_match` |
| 3 same dimensions, different transform → disabled | `test_3_same_dimensions_different_transform_is_incompatible` (geographic, and projected with a shifted grid → `transform_mismatch`) |
| 4 CRS mismatch → disabled | `test_4_crs_mismatch_is_incompatible` (plus `georeferencing_mismatch`) |
| 5 dimension mismatch → disabled | `test_5_dimension_mismatch_is_incompatible` |
| 6 RGB uint8 requirement | `test_6_rgb_must_be_uint8[uint16, float32]` |
| 7 non-RGB → disabled | `test_7_non_rgb_source_is_incompatible[1, 2 bands]` |
| 8 P1-9 GLB unchanged | Existing `test_mesh_export.py` passes unchanged (textured off-meridian DSM, untextured geographic DSM with "reprojected" reason). The API test 2 checks the GLB reason equals the context reason. |
| 9 terrain mesh geometry unchanged | `terrainMesh.test.ts`, `flythrough*.test.ts` unchanged and passing |
| 10 D1 pixel-centre mapping unchanged | `terrainD1.test.ts` (9), `test_d1_pixel_centre_georeferencing.py` (12) unchanged and passing |

Also: no-source and unreadable-source codes; the non-georeferenced pair is still compatible; `grid_is_reprojected` agrees with what `extract_terrain_grid` actually does (geographic, projected, none).

**Frontend:** `terrainAvailability.test.ts`, 2 new tests. `textureAvailable` follows only the backend flag: a grid with the same dimensions but flagged reprojected gives no texture, and unavailable or unknown gives no texture.

**Browser:** `frontend/e2e/d2-texture-compatibility.spec.ts`, 2 tests on the same uncalibrated pipeline with sources differing only in CRS. It reads state from the backend context and the app's own data attributes, not screenshots.

**Detects the old behaviour (VERIFIED):**
- **Method:** `TerrainWorkspace.tsx` was backed up with a SHA-256, the old dimension rule restored, the spec run, and the file restored and verified identical.
- **Result:** the geographic test **failed** ("Expected: disabled, Received: enabled"), and the projected test still passed.
- **Backend:** the API tests assert the new context fields, which the old backend did not have.

## 7. Validation results

All VERIFIED, run 2026-09-28.

| Check | Result |
|---|---|
| D2 backend tests | **14 passed** |
| D2 Playwright (`d2-texture-compatibility.spec.ts`) | **2 passed** |
| Backend full suite (`python -m pytest tests`, backend container) | **405 passed, 3 skipped** (391 before D2, plus 14). The skips are the environment-conditional GAMUS/MobileSAM ones. |
| Vitest | **18 files, 204 passed** (202 before D2, plus 2) |
| TypeScript `tsc -b --noEmit` | exit 0 |
| ESLint `eslint .` | 0 errors. 5 pre-existing warnings (`AuthContext.tsx`, `PageHeaderContext.tsx`; not touched). |
| Vite build | built. Existing >500 kB chunk warning only. |
| Ruff (`app`, `geospatial`, D2 test) | check clean. `ruff format --check` flags only the pre-existing `geospatial/calibration.py` (not touched). |
| D1 regression | `test_d1_pixel_centre_georeferencing.py` and `terrainD1.test.ts` pass (in the suites above) |
| Playwright, all 19 specs | **19 passed**, including P1-7 `p17` (2), P1-8 `p18` (2), P1-9 `p19` (2), phase10/13/13b, p13–p16 and D2 (2). The backend container was restarted first so it served the changed code. |

## 8. Limitations

- **The GLB rule is now slightly stricter.** Through the shared function it also requires matching georeferenced-ness, CRS and transform. Pipeline artifacts always carry the source's own CRS, transform and dimensions (**INFERENCE** from `analysis_execution.py`), so this cannot change a GLB produced from pipeline outputs. All existing GLB tests pass unchanged (**VERIFIED**). The reason strings for the pre-existing conditions are identical. The GLB format and metadata keys are unchanged.
- **Dimension source for the GLB.** Its dimension check now reads the source raster header instead of the `Dataset.width/height` database columns. These are recorded from that header at upload (**INFERENCE**).
- **Extra reads per request.** The visualization context now opens two raster headers per request for the terrain, a small read with no pixel data.
- **No texture for geographic sources in 3D.** The texture is not reprojected by design; that would be a separate feature. **UNRESOLVED** by design.
- **Rotated or skewed source transforms are not handled.** They are not handled by the terrain grid either (D1 §8).
- **Relative-depth terrains follow the same rule.** A projected uncalibrated source is textured, as before; a geographic uncalibrated source now is not, which is the corrected behaviour.

## 9. Final acceptance

**D2: CLOSED.** Every acceptance condition holds (VERIFIED):

- **Geographic sources:** a reprojected geographic terrain never receives the RGB texture (API, browser, GLB).
- **Projected sources:** a compatible projected terrain still does.
- **Dimensions alone are not enough:** the geographic fixture has matching dimensions, and the browser test fails under the old dimension-only rule.
- **One definition:** the browser and the GLB share a single provenance-based rule with a machine-readable code.
- **D1 intact:** the D1 pixel-centre geometry files are untouched and its tests pass.
- **GLB still correct:** the GLB export tests pass unchanged.
- **Regressions:** all relevant ones pass.

No commit, no migration, no texture reprojection.
