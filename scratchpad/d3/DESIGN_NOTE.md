D3 Vertical Unit Correctness - Design Note

VERIFIED

- Physical terrain derivatives use metres internally for both horizontal and vertical distances.
- `geospatial/vertical_units.py` is the single resolver for physical elevation units. It reads documented metadata only: GeoTIFF band unit, compound vertical CRS unit, and GCP compound CRS unit.
- Supported vertical units are metre, international foot, and US survey foot. Feet are converted by exact factors for derivative calculations.
- Unknown, unsupported, or conflicting vertical units raise/return explicit diagnostics for metric terrain operations. They are not assumed to be metres.
- `prepare_elevation()` rejects physical derivative input when vertical units are unknown.
- Slope, aspect, and hillshade share Horn gradients where vertical deltas are multiplied by `vertical_to_metre` and horizontal cell size is converted to metres.
- Geographic horizontal CRS input is reprojected to local UTM before derivative calculation; D3 did not redesign that path.
- Ground filtering receives the resolved vertical unit. PMF thresholds/windows are metres, while DTM/nDSM outputs remain in the DSM's declared native vertical unit.
- Calibrated `metric_elevation`/`dsm` rasters write the calibration reference's declared band unit when known. Unknown references write no fabricated unit.
- Derivative and ground-filter artifact metadata carries unit provenance, including source vertical unit, calculation unit, conversion factor, CRS/horizontal unit, method, and `source_raster_modified=false`.
- Relative depth remains unitless and is not an accepted source for disaster screening.

INFERENCE

- The existing calibration contract is "same unit as the calibration reference", not always metres. D3 preserves the affine fit and quality gate math, then records the reference unit and propagates it to generated elevation rasters.
- Flood screening compares `water_level` in the source elevation raster's declared unit; it does not need metre conversion unless its caller wants a metre-only water-level contract in a future phase.
- DTM/nDSM stored in feet are acceptable because their metadata and band unit state feet; metric PMF thresholds are converted before comparison.

UNRESOLVED

- The frontend layer legend still omits dynamic elevation unit labels for metric elevation, DSM, DTM, and nDSM. This avoids false metres but does not yet display known feet/metres in the compact legend.
- Host-only Playwright specs that call local `python` + `rasterio` require rasterio in the host Python environment; the app portions ran, but those helper checks fail without the dependency.
