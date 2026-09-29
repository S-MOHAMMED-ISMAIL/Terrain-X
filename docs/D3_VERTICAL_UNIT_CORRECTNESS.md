# D3 Vertical Unit Correctness

## VERIFIED

Original defect: terrain derivative code can silently mix horizontal metres with vertical values whose unit is feet or unknown. D3 makes that unit contract explicit.

Internal convention: physical derivatives calculate with horizontal distance in metres and vertical distance in metres. Known feet are converted internally; the source raster is not modified.

Supported vertical units:

- `metre` / `meter` / `m`: factor `1.0`
- international `foot` / `feet` / `ft`: factor `0.3048`
- `US survey foot`: factor `1200/3937`

Unit sources are documented metadata only:

- GeoTIFF band unit (`dataset.units[0]`)
- compound CRS vertical unit
- GCP compound CRS vertical unit

Unknown units fail safely for metric derivative operations. Relative depth remains unitless and non-metric.

Derivative behavior:

- slope/aspect/hillshade require verified vertical units
- geographic CRS rasters are reprojected to a local metric CRS before derivative calculation
- PMF ground filtering applies metre thresholds/windows and converts those thresholds to the DSM's declared vertical unit
- DTM/nDSM are written in the DSM's declared vertical unit with band-unit metadata

Artifact provenance now records source vertical unit, calculation unit, conversion factor, horizontal unit/CRS, method, and whether the source raster was modified.

## INFERENCE

Calibration output has the same unit as the calibration reference. D3 preserves existing affine calibration semantics and records that unit rather than forcing calibration values to metres.

Flood screening still compares water level to elevation in the raster's own declared unit; slope-based landslide screening uses metre-normalized slope.

## UNRESOLVED

The frontend avoids claiming metres for elevation layers but does not yet render a dynamic unit label in every compact legend/inspection label.

Host-only Playwright specs that call local `python` + `rasterio` require rasterio in the host Python environment. Backend Docker validation passed; two browser helper checks were blocked by that host dependency.
