"""Geospatial processing package: raster metadata (`raster_metadata.py`) and
pixel I/O (`raster_io.py`) are implemented; CRS reprojection, DEM/GCP
calibration, DSM generation, and terrain derivatives are not yet implemented.

Must remain independent of the backend/FastAPI layer — backend imports from
here, never the reverse. No implementation yet for calibration/DSM; added
starting Phase 4 of docs/ROADMAP.md.
"""
