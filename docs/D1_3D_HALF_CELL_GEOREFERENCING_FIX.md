# D1 — 3D half-cell georeferencing fix

**Status: CLOSED (§9).** No commit. No migration. No UI change. No TerraHeight-S. No change to terrain values, calibration, slope/aspect/hillshade, DTM/nDSM, raster preview, the map overlay, GLB coordinate semantics, or `coordinate_to_pixel`.

**Labels:**
- **VERIFIED:** measured or tested here.
- **INFERENCE:** reasoned, not directly tested.
- **UNRESOLVED:** open.

## 1. The defect

The browser 3D mesh put grid cell (row, col)'s value at the cell's **corner**. Every other part of TERRAIN-X puts it at the cell's **centre**.

A 3D click, HUD readout or 3D waypoint on the vertex carrying pixel P's value therefore reported a map coordinate half a cell north-west of P's centre (for north-up grids): −0.5·cell_x in x and −0.5·cell_y in y. Along a negative-y axis that shift points toward row −∞. This is the pixel-centre offset against:
- the 2D overlay,
- the GLB export,
- backend `coordinate_to_pixel`,
- inspection.

The surface itself was drawn shifted by the same half cell. **VERIFIED:** a fixture test on the old code (§6).

## 2. Pipeline trace (before the fix)

| # | Stage | Convention (old) | Source |
|---|---|---|---|
| 1 | Raster transform | Grid transform of the grid actually read. This is the decimated transform, or for geographic sources the UTM `dst_transform`. `origin_x/origin_y` = `transform.c/.f` = the **outer corner of cell (0,0)**. `cell_size_x/y` = `transform.a/.e`, signed (`e < 0` for north-up). Cell centre: `origin + (i + 0.5)·cell`. | `geospatial/terrain_grid.py` |
| 2 | Local 3D frame | `local = map − (origin_x, origin_y)` everywhere: HUD, 3D click, both waypoint sources, and the P1-8 local-coordinate endpoint. | flythrough.ts `flightReadout`, TerrainView3D `onClick`, TerrainWorkspace, `get_terrain_local_coordinate` |
| 3 | Mesh vertices | Vertex (r,c) at local `(c·cx, r·cy)`, the **corner**, carrying cell (r,c)'s block-average value. UV `(c/(w−1), 1 − r/(h−1))`. Mesh translated by `(−halfWidth, 0, −halfHeight)`. Flight: `col = (x + hw)/cx`. | `terrainMesh.ts`, `flythrough.ts` |
| 4 | GLB | Vertex (r,c) at `(c·cx, v, −r·cy)` relative to the **centre** of cell (0,0). Extras origin = corner + cell/2. `map_x = origin_x + x`, `map_y = origin_y − z`. UV `((c+.5)/w, (r+.5)/h)`. Not georeferenced: `pixel_col = 0.5·step + x`. | `geospatial/mesh_export.py`, `export_terrain_mesh` |
| 5 | Click / measurement | 2D: Leaflet lat/lng → backend `coordinate_to_pixel` = `floor(~transform·(x,y))`, the pixel containing the point. 3D georeferenced: hit → local → `origin + local` → same endpoint. 3D non-georeferenced: `round(local·src/w)`. | `geospatial/measurements.py`, `mapClick.ts`, TerrainView3D |
| 6 | **Discrepancy** | Only stage 3, plus its mirror in the flight grid mapping, places a value at the corner. Every inverse (`origin + local`) is correct for the corner-origin frame, so it faithfully reported the corner. | — |

Secondary effects of the same defect:
- the 3D texture UV stretched the image over the corner lattice;
- a non-georeferenced 2D waypoint used a pixel index as a continuous coordinate;
- the P1-8 `in_footprint` used the corner-vertex extent `[0, w−1]`.

**Why this was not ambiguous:**
- The authoritative convention is the grid's own affine transform (stage 1), which is centre-based under rasterio, `coordinate_to_pixel` and the GLB.
- For non-georeferenced rasters, the GLB contract already defines continuous source-pixel coordinates with pixel *i*'s centre at *i* + 0.5.
- **No second convention was introduced.** The local frame keeps its definition, `local = map − transform origin corner`. Only the vertex positions moved to the centres.

## 3. Convention after the fix

For grid cell (row, col), with signed cell sizes (no square-pixel or north-up assumption):

```
local_x = (col + 0.5) · cell_size_x          # mesh vertex, before the display centring
local_z = (row + 0.5) · cell_size_y
map_x   = origin_x + local_x                  # = transform · (col + 0.5, row + 0.5)
map_y   = origin_y + local_z
world   = local − (halfWidth, ·, halfHeight)  # the unchanged mesh.position centring
```

The inverse, used by the flight surface, clearance, paths and footprint:

```
col = (world_x + halfWidth)/cell_size_x − 0.5 ;  row = (world_z + halfHeight)/cell_size_y − 0.5
```

Integer (col, row) is a vertex, i.e. a cell centre.

- **GLB:** `glb_map = (origin_x + cx/2 + c·cx, origin_y + cy/2 − (−r·cy))`. This equals the rendered `origin + local` exactly: the same point, with the origin moved by half a cell.
- **Non-georeferenced:** the 3D local coordinate × (source/grid) = the continuous source-pixel coordinate (pixel *i* spans [*i*, *i*+1)). This is the GLB `pixel_col/pixel_row` convention.
- **Mesh surface extent:** the cell centres, grid coordinates [0.5, n − 0.5]. The outer half of each edge cell has no surface, just as in the GLB.

## 4. Code changes

| File | Change |
|---|---|
| `frontend/src/components/terrain/terrainMesh.ts` | Vertex `(col+0.5)·cellX, (row+0.5)·cellY`. UV `((col+0.5)/W, 1 − (row+0.5)/H)`, the same texels as the GLB. Docstring updated. |
| `frontend/src/components/terrain/flythrough.ts` | `worldToGrid`/`gridToWorld` offset by 0.5 cell, which keeps the flight surface on the rendered triangles. `gridToWorld` is exported. |
| `frontend/src/components/terrain/terrainCoords.ts` (new) | `pixelIndexAt` (floor + clamp, the same rule as `coordinate_to_pixel`) and `sourcePixelCentreToLocal` (non-georeferenced pixel centre → local). |
| `frontend/src/components/terrain/TerrainView3D.tsx` | Non-georeferenced 3D click: `pixelIndexAt(local·src/w)` instead of `round`. The georeferenced path is unchanged. |
| `frontend/src/components/terrain/TerrainWorkspace.tsx` | Non-georeferenced 2D waypoint: the clicked pixel's **centre** (`pixelCol = col + 0.5`, local accordingly). Georeferenced waypoints are unchanged. |
| `backend/app/services/visualization.py` | Local-coordinate `in_footprint`: `0.5 ≤ col ≤ w − 0.5` (the mesh surface). The GLB `representation` text no longer claims the browser uses cell corners; this is a string only, and the format and coordinates are unchanged. |
| `frontend/src/components/terrain/terrainTopDownCamera.ts`, `docs/ARCHITECTURE.md` | Comment and doc text only. |

**Unchanged** (VERIFIED: these files were not edited; the repo has no commits to diff against):
- `terrain_grid.py`
- `coordinate_to_pixel`
- `mesh_export.py`
- map overlay and raster preview
- calibration
- terrain derivatives
- the Three.js scene/mesh translation

## 5. Tests

**Fixture:**
- Georeferenced, 13 × 9 cells, non-square (3 m × −2 m), EPSG:32633.
- Unaligned origin (500123.37, 4649987.61).
- Background plane `100 + 0.25c − 0.5r`, with the distinctive value **777 at off-centre pixel (6, 9)**.
- Not decimated, so grid cell = source pixel.
- Geographic variant: EPSG:4326, 0.00031° × −0.00017°, reprojected by the backend to UTM.
- Non-georeferenced relative-depth variant: 16 × 12 source decimated to an 8 × 6 grid.

Expected values are computed independently, using rasterio `xy(offset="center")`, pyproj, and `origin + (i + 0.5)·cell` written in the test.

| Required test | Where |
|---|---|
| 1 pixel-centre world coordinate | `test_d1…::test_1_every_mesh_vertex_is_its_pixel_centre_world_coordinate` |
| 2 first pixel → its centre | `test_2_first_pixel_maps_to_its_centre_not_the_raster_corner`; Vitest `5:` |
| 3 interior pixel exact | `test_3_interior_off_centre_pixel_maps_exactly` |
| 4 last pixel centre inside footprint | `test_4_last_pixel_centre_is_inside_the_raster_footprint`; API `test_4_last_pixel_centre_is_on_the_mesh_and_its_outer_rim_is_not`; updated `test_terrain_local_coordinate` |
| 5 3D mesh coordinate = pixel centre | Vitest `terrainD1.test.ts` `5:` (real `buildTerrainGeometry` buffer) |
| 6 2D and 3D same pixel | Vitest `6:` (a real Three.js raycast on the placed mesh, resolved with the backend floor rule); API acceptance (`/measurements/pixel` from 3D map coordinates and from WGS84) |
| 7 `coordinate_to_pixel` round trip | `test_7_…[projected]`, `[geographic]`: every vertex; source pixel computed independently via pyproj |
| 8 GLB vertex | API acceptance (GLB vertex with value 777 → map = centre = rendered vertex); Vitest `8:` |
| 9 flythrough HUD | Vitest `9:` (`flightReadout` over the spike: map = centre, ground value = 777) |
| 10 waypoint | Vitest `10:` (2D-map and 3D-click waypoints coincide on the spike vertex, value 777); API acceptance (local-coordinate endpoint) |
| 11 geographic CRS | `test_7_…[geographic]`, `test_11_…`; Vitest `11:` |
| 12 projected CRS | the projected fixture throughout |
| 13 non-square pixels | fixture 3 × 2 m; `test_13_non_square_cells_use_each_axis_own_size` |
| 14 relative-depth fixture | Vitest `14:` ×2: HUD pixel = GLB `0.5·step + c·step`; source pixel → local → click resolution round-trips all 192 pixels |
| Acceptance | `test_acceptance_2d_backend_3d_glb_waypoint_agree_on_the_spike_pixel` (backend) + `terrainD1.test.ts` (frontend, the same constants) |

**Existing tests updated.** These asserted the old corner placement; the expectations moved to centres, and none was loosened:
- `terrainMesh.test.ts` (4)
- `flythrough.test.ts`: the `worldOf` helper plus 2 readouts
- `flythroughPath.test.ts`: the `wp` helper, the 2D/3D coincidence test, and one ridge probe
- `test_measurements.py`: the 3D-click helper; the tolerant ±ratio checks are now **exact** pixel equalities
- `test_terrain_local_coordinate.py`: the old "last pixel centre is outside" assertion is inverted, and outer-rim checks were added
- E2E `p17-flythrough.spec.ts`, `p18-flythrough-path.spec.ts`: entry pose, grid index −0.5, north limit

## 6. Before / after

Spike pixel (6, 9), independent centre **(500151.87, 4649974.61)**:

| | Old (corner) | New (centre) |
|---|---|---|
| Mesh vertex local | (27, −12) | (28.5, −13) |
| 3D click / HUD / waypoint map | (500150.37, 4649975.61) | **(500151.87, 4649974.61)** |
| Pixel of that map point (floor rule) | exactly on the corner: (6, 9) only by the floor tie-break. A hit a hair north-west gives (5, 8). The ±0.5-cell area around the vertex spans 4 pixels, and only ¼ of it is (6, 9). | (6, 9) for any hit within ±0.5 cell |
| GLB vertex of the 777 value | (500151.87, 4649974.61) | same, now equal to the rendered vertex |
| Last pixel centre (8, 12) `in_footprint` | False | True |

**Old-code check (VERIFIED):**
- **Method:** the four old formulas were restored temporarily, with a SHA-256 backup, then restored and verified byte-identical.
- **Frontend:** `terrainD1.test.ts` **8 of 9 fail**. The one that passes is the round-trip of the new `pixelIndexAt` helper, which did not exist before.
- **Backend:** the footprint test fails.
- **Limit of the backend acceptance test:** it passes on the old code, because the backend endpoints were already centre-consistent (it computes the mesh vertex from metadata). The frontend half is what detects the defect.

## 7. Regression results

All run on 2026-09-27 against the fixed code. All VERIFIED.

| Check | Result |
|---|---|
| D1 backend (`tests/test_d1_pixel_centre_georeferencing.py`) | **12 passed** |
| D1 frontend (`terrainD1.test.ts`) | **9 passed** |
| Backend targeted: D1, local-coordinate, mesh export, measurements, visualization, point-slope | **108 passed** |
| Backend full suite (`python -m pytest tests`, in the backend container) | **391 passed, 3 skipped**. The skips are environment-conditional (GAMUS data, MobileSAM checkpoint) and unrelated. |
| Vitest (whole frontend) | **18 files, 202 passed** |
| TypeScript (`tsc -b --noEmit`) | exit 0 |
| ESLint (`eslint .`) | 0 errors. 5 warnings, all pre-existing in `AuthContext.tsx` / `PageHeaderContext.tsx` (not touched). |
| Vite build (`npm run build`) | built. Only the existing >500 kB chunk-size warning. |
| Ruff (touched backend files) | check and format clean. The whole-tree `ruff check` reports 1 pre-existing I001 in `tests/test_disaster_screening.py` (not touched). |
| Playwright P1-7 `p17-flythrough` (2), P1-8 `p18-flythrough-path` (2), P1-9 `p19-mesh-export` (2) | **6 passed** |
| Playwright `phase10-terrain` (2), `phase13-golden-path`, `phase13b-measurement-interaction` (3, incl. 3D click inspection), `p14-slope-at-point`, `p16-georegistration` | **8 passed** |
| Playwright `p13-dtm-ndsm`, `p15-calibration-residuals` | **3 passed** |

**Notes:**
- Playwright ran against the running docker stack. The backend container was restarted once so it served the changed `in_footprint`, because uvicorn does not reliably hot-reload over the Windows bind mount (see DEVELOPMENT.md).
- **P1-7:** entry pose, `groundHeightAt`, HUD map coordinate, height-above-ground, terrain-follow, clearance and footprint clamping.
  - They all run through `worldToGrid`/`gridToWorld` on the mesh's own position buffer, so they move with the vertices.
  - Checked by the updated `flythrough.test.ts` (including the Three.js raycast agreement test) and `p17`.
  - Relative-depth semantics are unchanged: the labels and visual-unit clearance tests pass.
- **P1-8:** waypoint location, map coordinate, path, playback and clearance, checked by `flythroughPath.test.ts` (clearance over 10,000 random points against a real raycast) and `p18`.
- One transient: the first run of the new backend D1 file took 531 s (11 passed, plus one test bug I fixed). Every rerun took 5–6 s. The cause was not identified. It did not recur across 4 reruns and the full suite. **UNRESOLVED** as to cause.

## 8. Limitations

- **Existing Playwright E2E runs the real depth/calibration pipeline, so pixel values cannot be controlled.** The value-exact acceptance chain is therefore split across two test runners sharing one fixture definition:
  - backend: real endpoints with a stored GeoTIFF;
  - frontend: the real `buildTerrainGeometry` mesh, a Three.js raycast, the flight HUD and waypoint logic.

  There is no single browser test that clicks the spike. **UNRESOLVED:** a browser test hook for projecting a vertex to screen does not exist and was not added (no UI change).
- **Float32 precision.** Mesh positions are Float32 in the local frame. Rounding is about 1e-6 m for non-dyadic cell sizes such as the 16.37 m geographic example; it is not a convention issue. **VERIFIED.**
- **Mesh extent.** The mesh surface ends at the edge cells' centres, so the outer half-cell rim has no 3D surface. This is identical to the GLB. Before the fix, the rim was missing on the south and east edges only, while the north-west was drawn over the neighbouring half-cell. **INFERENCE** from the geometry.
- **Rotated transforms are not supported.** `extract_terrain_grid` carries only `a/e/c/f`, which was already the case. rasterio decimation and `calculate_default_transform` produce axis-aligned grids for axis-aligned sources. **INFERENCE:** a rotated source raster would need its own handling, and none is claimed here.
- **Non-georeferenced waypoint pixel values in exported paths are now continuous pixel coordinates** (centre = *i* + 0.5), matching the HUD and the GLB. Paths exported before D1 carried integer indices for 2D-picked waypoints. There is no path import, so nothing reads the old values. No migration was done.
- **Out of scope:** D2, the texture mismatch for geographic sources (`textureCompatible`).

## 9. Final acceptance

**D1: CLOSED.**

- **Single convention:** raster pixel centre = transform · (col + ½, row + ½) = mesh vertex = the inverse used by click, HUD, waypoints and the local-coordinate endpoint.
- **Agreement:** the GLB, backend `coordinate_to_pixel`, the 2D overlay and inspection all agree with it (§3, §5).
- **Scope:** the actual vertex positions changed, with no scene translation and no second convention.
- **Tests:** the fixture detects the old behaviour (§6), and every required test and regression passes (§7).
- **Remaining gap:** the value-exact acceptance chain spans the backend and Vitest rather than a single browser test (§8, UNRESOLVED). All the other required items hold.
- **Not done:** no commit, no migration, no UI change.
