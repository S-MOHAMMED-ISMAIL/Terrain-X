# E2E fixtures

**These fixtures validate pipeline behavior and are NOT accuracy evidence.**

- `calibrated-source.tif`: a 32×32 synthetic RGB scene georeferenced in EPSG:32633 at 2 m/pixel. It matches `make_structured_scene_geotiff_bytes(32, 32)` in the backend test fixtures.
- `calibrated-dem.tif`: a DEM on the same grid, built from the pinned Depth Anything V2 model's own relative-depth prediction for `calibrated-source.tif`:
  - `DEM = 10 * depth + 100`, giving a range of about 107.5 to 140.1.
  - The top-left 5×5 cells are NoData (`-9999`).
  - D3: band 1 declares vertical unit `metre`.
  - It has a deliberately positive, depth-consistent relationship, so the calibration quality gate (`geospatial/calibration.py`) accepts it. The golden-path flood water level of 125 falls inside its range.
  - Regenerate it with `scripts/generate_e2e_calibration_fixture.py`.
  - The previous version of this file was an unrelated planar ramp. It had no relationship to the scene's depth.
- `uncalibrated-source.jpg`: a small non-georeferenced photo for the relative-terrain path.
- `offmeridian-source.tif` / `offmeridian-dem.tif` (P1-6): the structured test scene at 128×128 px and 40 m/px in EPSG:32633, with its origin at (660000 E, 6660000 N). That is about 60°N and 2.9° east of the zone's central meridian.
  - The DEM is built like `calibrated-dem.tif`: 10 × the pinned model's own depth + 100, giving about 101.2 to 141.9, with a 5×5 NoData corner and vertical unit `metre`. 98.7% of its valid values are unique.
  - Every other georeferenced fixture sits on the central meridian, where the pre-P1-6 2D map error was exactly zero. At this placement the old overlay was up to about 220 m off, and a click resolved the wrong source pixel for about 98% of locations.
  - Regenerate it with `scripts/generate_e2e_offmeridian_fixture.py`.
- `geographic-source.tif` (D2): the structured test scene at 32×32 px, the same size as `calibrated-source.tif`, but in geographic EPSG:4326 (0.0005°/px at 17.87 E, 60.045 N).
  - Its analysis artifacts keep the 32×32 dimensions, but the 3D terrain grid is reprojected to UTM. The RGB texture must therefore be unavailable: equal dimensions are not pixel correspondence.
  - Regenerate it with `scripts/generate_e2e_geographic_fixture.py`.
