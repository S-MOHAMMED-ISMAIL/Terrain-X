"""Phase 8 backend-side orchestration for real terrain-derived hazard
screening. Model mechanics live in geospatial/terrain_derivatives.py (pure,
backend-independent); this module loads the real elevation artifact, calls
into it, and builds the real metadata persisted to the job's
`disaster_metadata` and to each hazard artifact's own `artifact_metadata`.

Unlike calibration_pipeline.py/semantic_pipeline.py — soft-failure add-ons
to an otherwise-independently-valid depth-estimation job — a disaster-
screening job is ALWAYS standalone (see AnalysisParametersV1's Phase 8
fields): its entire purpose IS the hazard screening, so a real failure here
is a real failure of the whole job. This module therefore DOES raise
(DisasterAnalysisError / RasterValidationError), unlike its soft-failure
siblings; `app/services/analysis_execution.py` catches these and turns them
into a real `AnalysisExecutionError`, exactly like a depth-inference
failure already does.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass

import numpy as np

from geospatial.terrain_derivatives import (
    DEFAULT_HILLSHADE_ALTITUDE_DEG,
    DEFAULT_HILLSHADE_AZIMUTH_DEG,
    FLOOD_CLASS_LABELS,
    LANDSLIDE_CLASS_LABELS,
    FloodScreeningResult,
    LandslideScreeningResult,
    LandslideThresholds,
    PreparedElevation,
    TerrainStatistics,
    compute_hillshade,
    compute_slope_aspect,
    compute_terrain_statistics,
    crs_to_string,
    prepare_elevation,
)
from geospatial.terrain_derivatives import run_flood_screening as _run_flood_screening
from geospatial.terrain_derivatives import run_landslide_screening as _run_landslide_screening
from geospatial.vertical_units import unit_provenance

# Phase 8: only a real, already-calibrated elevation artifact can back a
# disaster-screening job (see the NON-NEGOTIABLE rule in
# docs/ARCHITECTURE.md §3.8). Defined here (not in app/services/analysis_jobs.py
# or app/services/analysis_execution.py) specifically so BOTH of those
# modules can import the same real constants without a circular import
# (analysis_jobs -> app.jobs.tasks -> analysis_execution would otherwise
# cycle back if analysis_execution imported from analysis_jobs directly) —
# this module has no backend-side dependents of its own to cycle through.
ELEVATION_ARTIFACT_TYPES = {"metric_elevation", "dsm"}
NOT_ELEVATION_ERROR = (
    "Metric elevation/DSM is required for this hazard analysis. Relative depth is "
    "unitless and cannot be interpreted as elevation."
)

# Verbatim, persisted into every disaster job's own metadata (and surfaced
# in the frontend results panel) so a consumer reading only the metadata —
# without this module's source — still sees the required scope disclaimer.
DISASTER_SCOPE_DISCLAIMER = (
    "Disaster outputs are terrain-derived screening products and are not substitutes "
    "for validated hydrological, hydraulic, geotechnical, or operational disaster models."
)
FLOOD_METHOD_DISCLAIMER = (
    "This is a terrain-based inundation screening scenario. It does not model rainfall, "
    "drainage, rivers, flow routing, infiltration, tides, storm surge, hydraulic "
    "connectivity, or temporal flood dynamics."
)
LANDSLIDE_METHOD_DISCLAIMER = (
    "This is a terrain-derived landslide susceptibility screening index, not a "
    "calibrated or validated probability of landslide occurrence."
)


@dataclass
class DisasterOutcome:
    prepared: PreparedElevation
    slope: np.ndarray
    aspect: np.ndarray
    hillshade: np.ndarray
    terrain_stats: TerrainStatistics
    flood: FloodScreeningResult | None
    landslide: LandslideScreeningResult | None
    metadata: dict


def _terrain_stats_dict(stats: TerrainStatistics) -> dict:
    return {
        "min_elevation": stats.min_elevation,
        "max_elevation": stats.max_elevation,
        "mean_elevation": stats.mean_elevation,
        "median_elevation": stats.median_elevation,
        "elevation_range": stats.elevation_range,
        "min_slope_deg": stats.min_slope_deg,
        "max_slope_deg": stats.max_slope_deg,
        "mean_slope_deg": stats.mean_slope_deg,
        "valid_pixel_count": stats.valid_pixel_count,
        "total_pixel_count": stats.total_pixel_count,
    }


def _flood_dict(flood: FloodScreeningResult) -> dict:
    return {
        "water_level": flood.water_level,
        "min_elevation": flood.min_elevation,
        "max_elevation": flood.max_elevation,
        "potentially_inundated_pixel_count": flood.potentially_inundated_pixel_count,
        "valid_pixel_count": flood.valid_pixel_count,
        "pixel_area": flood.pixel_area,
        "area_unit": flood.area_unit,
        "potentially_inundated_area": flood.potentially_inundated_area,
        "valid_area": flood.valid_area,
        "potentially_inundated_percentage": flood.potentially_inundated_percentage,
        "method": "terrain_threshold",
        "class_labels": {str(k): v for k, v in FLOOD_CLASS_LABELS.items()},
        "disclaimer": FLOOD_METHOD_DISCLAIMER,
    }


def _landslide_dict(landslide: LandslideScreeningResult) -> dict:
    return {
        "method": "slope_based",
        "thresholds": landslide.thresholds.as_dict(),
        "class_pixel_counts": landslide.class_pixel_counts,
        "class_areas": landslide.class_areas,
        "class_percentages": landslide.class_percentages,
        "max_slope_deg": landslide.max_slope_deg,
        "mean_slope_deg": landslide.mean_slope_deg,
        "pixel_area": landslide.pixel_area,
        "area_unit": landslide.area_unit,
        "valid_pixel_count": landslide.valid_pixel_count,
        "class_labels": {str(k): v for k, v in LANDSLIDE_CLASS_LABELS.items()},
        "disclaimer": LANDSLIDE_METHOD_DISCLAIMER,
    }


def run_disaster_screening(
    elevation_path,
    *,
    source_artifact_id: uuid.UUID,
    source_artifact_type: str,
    run_flood: bool,
    water_level: float | None,
    run_landslide: bool,
    landslide_thresholds: LandslideThresholds | None,
) -> DisasterOutcome:
    """Runs the full real terrain-derivative + hazard-screening pipeline —
    ALWAYS raises (never returns a soft-failure sentinel) on any real
    problem, since a disaster-screening job has no other purpose to fall
    back on. Timing of each real stage is recorded in the returned
    metadata's `timings_seconds`.
    """
    timings: dict[str, float] = {}

    t0 = time.monotonic()
    prepared = prepare_elevation(elevation_path)
    timings["loading_terrain_data_seconds"] = time.monotonic() - t0

    t0 = time.monotonic()
    slope, aspect = compute_slope_aspect(prepared)
    timings["computing_slope_aspect_seconds"] = time.monotonic() - t0

    t0 = time.monotonic()
    # Always computed alongside slope/aspect (same real elevation, same
    # Horn gradients, same cheap pure-numpy cost) — never gated behind its
    # own opt-in flag, exactly like slope/aspect themselves aren't.
    hillshade = compute_hillshade(
        prepared,
        azimuth_deg=DEFAULT_HILLSHADE_AZIMUTH_DEG,
        altitude_deg=DEFAULT_HILLSHADE_ALTITUDE_DEG,
    )
    timings["computing_hillshade_seconds"] = time.monotonic() - t0

    t0 = time.monotonic()
    terrain_stats = compute_terrain_statistics(prepared, slope)
    timings["computing_terrain_statistics_seconds"] = time.monotonic() - t0

    flood: FloodScreeningResult | None = None
    if run_flood:
        t0 = time.monotonic()
        flood = _run_flood_screening(prepared, water_level=water_level)
        timings["flood_screening_seconds"] = time.monotonic() - t0

    landslide: LandslideScreeningResult | None = None
    if run_landslide:
        t0 = time.monotonic()
        landslide = _run_landslide_screening(prepared, slope, thresholds=landslide_thresholds)
        timings["landslide_screening_seconds"] = time.monotonic() - t0

    unit = prepared.vertical_unit.unit
    horizontal = (
        f"{prepared.crs.linear_units} (x {prepared.horizontal_to_metre} to metres)"
        if not prepared.reprojected
        else "metre (local UTM reprojection)"
    )
    metadata: dict = {
        # D3: slope/aspect/hillshade/landslide are computed with both axes in
        # metres; elevation statistics and the flood water level stay in the
        # elevation raster's own declared unit (`elevation_unit`).
        "vertical_unit": prepared.vertical_unit.as_dict(),
        "elevation_unit": unit.code,
        "unit_provenance": unit_provenance(
            prepared.vertical_unit,
            horizontal_unit=horizontal,
            crs=crs_to_string(prepared.crs),
            method="Horn (1981) 3x3 gradients with vertical and horizontal distances in metres",
        ),
        "source_artifact_id": str(source_artifact_id),
        "source_artifact_type": source_artifact_type,
        "source_crs": prepared.source_crs,
        "analysis_crs": crs_to_string(prepared.crs),
        "reprojected_for_analysis": prepared.reprojected,
        "pixel_width": prepared.pixel_width,
        "pixel_height": prepared.pixel_height,
        "width": prepared.width,
        "height": prepared.height,
        "terrain_statistics": _terrain_stats_dict(terrain_stats),
        "timings_seconds": timings,
        "disclaimer": DISASTER_SCOPE_DISCLAIMER,
    }
    if flood is not None:
        # D3: the water level is compared with the elevation in its own unit.
        metadata["flood"] = {**_flood_dict(flood), "elevation_unit": unit.code}
    if landslide is not None:
        metadata["landslide"] = _landslide_dict(landslide)

    return DisasterOutcome(
        prepared=prepared,
        slope=slope,
        aspect=aspect,
        hillshade=hillshade,
        terrain_stats=terrain_stats,
        flood=flood,
        landslide=landslide,
        metadata=metadata,
    )
